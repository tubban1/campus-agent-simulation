"""Campus environment derivation and persistence helpers."""

from app.environment import repository


def derive_environment_from_weather(base_values, *, clamp):
    values = dict(base_values)
    rainfall = int(values.get("rainfall", 0) or 0)
    temperature = int(values.get("temperature", 24) or 24)
    weather = values.get("weather", "晴")
    activity_heat = int(values.get("activity_heat", 50) or 50)
    exam_pressure = int(values.get("exam_pressure", 35) or 35)
    assignment_pressure = int(values.get("assignment_pressure", 40) or 40)
    outdoor_penalty = min(35, rainfall // 2)
    heat_penalty = 10 if temperature >= 32 else 0
    values["playground_crowd"] = clamp(int(values.get("playground_crowd", 40)) - outdoor_penalty - heat_penalty, 10, 100)
    values["library_crowd"] = clamp(35 + exam_pressure // 2 + rainfall // 4, 10, 100)
    values["canteen_crowd"] = clamp(int(values.get("canteen_crowd", 50)) + (10 if rainfall > 20 else 0), 10, 100)
    values["commercial_crowd"] = clamp(35 + activity_heat // 2 - rainfall // 5 + (8 if temperature >= 30 else 0), 10, 100)
    values["campus_flow"] = clamp(55 + activity_heat // 3 - rainfall // 4, 10, 100)
    values["classroom_crowd"] = clamp(40 + assignment_pressure // 2, 10, 100)
    values["dorm_crowd"] = clamp(int(values.get("dorm_crowd", 45)) + (12 if rainfall > 20 else 0), 10, 100)
    values["study_atmosphere"] = clamp(35 + exam_pressure // 2 + assignment_pressure // 3, 10, 100)
    values["traffic_status"] = "拥堵" if values["campus_flow"] > 75 or rainfall > 40 else "正常"
    values["resource_pressure"] = clamp((values["canteen_crowd"] + values["library_crowd"] + values["classroom_crowd"]) // 3, 10, 100)
    values["network_status"] = "拥堵" if values["dorm_crowd"] > 75 else "稳定"
    values["safety_level"] = clamp(92 - rainfall // 8 - values["campus_flow"] // 12, 50, 100)
    values["consumption_index"] = round(max(0.5, min(1.8, 0.7 + activity_heat / 120 + values["commercial_crowd"] / 240)), 2)
    values["campus_mood"] = "紧张" if exam_pressure > 75 else ("低落" if weather in {"小雨", "中雨", "大雨", "雷雨"} else ("活跃" if activity_heat > 70 else "平稳"))
    return values


def save_environment_values(conn, day, values, *, default_environment):
    full_values = {key: values.get(key, default) for key, default in default_environment.items()}
    repository.upsert_campus_state(conn, day, full_values)


def environment_version_label(config_row):
    return f"{config_row['config_key']}@{config_row['version']}:{config_row['checksum'][:12]}"


def decode_environment_config(row, *, load_json):
    item = dict(row)
    item["config"] = load_json(item.pop("config_json", "{}"), {})
    item["version_label"] = environment_version_label(item)
    return item


def seed_default_environment_config(conn, *, default_config, content_checksum, canonical_json):
    config = default_config()
    checksum = content_checksum(config)
    row = repository.insert_default_config(conn, canonical_json(config), checksum)
    return dict(row)


def get_active_environment_config(conn, *, runtime_id, load_json):
    row = repository.active_config(conn, runtime_id)
    return decode_environment_config(row, load_json=load_json) if row else None


def create_environment_config_record(conn, config_key, name, config, parent_config_id=None,
                                     created_by="admin", *, validate_config, content_checksum,
                                     canonical_json, load_json):
    config_key = str(config_key or "").strip()
    name = str(name or "").strip()
    if not config_key or not name:
        raise ValueError("环境配置 key 和名称不能为空")
    validate_config(config)
    if parent_config_id:
        parent = repository.config_parent_exists(conn, parent_config_id)
        if not parent:
            raise ValueError("父环境配置不存在")
    version = repository.next_config_version(conn, config_key)
    checksum = content_checksum(config)
    row = repository.insert_config(conn, (config_key, name, version, parent_config_id, canonical_json(config), checksum, created_by))
    return decode_environment_config(row, load_json=load_json)


def get_environment_hour(environment):
    real_time = str(environment.get("real_time") or "")
    try:
        return int(real_time.split(":", 1)[0])
    except (TypeError, ValueError):
        return {"上午": 9, "中午": 12, "下午": 15, "晚上": 20, "深夜": 2}.get(
            environment.get("time_slot"), 9
        )


def get_active_campus_events(conn, day=None, *, current_day, rows_to_dicts):
    day = day or current_day(conn)
    rows = repository.active_campus_events(conn, day)
    return rows_to_dicts(rows)


def assert_destination_available(conn, destination, *, valid_locations, space_snapshot):
    from app.spatial.location_catalog import is_real_world_location
    dest_str = str(destination or "").strip()
    if dest_str not in valid_locations and not is_real_world_location(conn, dest_str):
        raise ValueError(f"地点 {destination} 不存在")
    snapshot = space_snapshot(conn)
    space = next((item for item in snapshot.get("spaces", []) if item.get("location") == dest_str), None)
    if not space:
        space = next((item for item in snapshot.get("spaces", []) if dest_str in item.get("location", "") or item.get("location", "") in dest_str), None)
    if space and space.get("effective_status") in ("关闭", "已关闭", "维护中", "暂停开放"):
        raise ValueError(f"{destination}当前{space['effective_status']}，Agent 需要调整计划")


def apply_environment_feedback(conn, resident_id, action, result, *, current_day,
                               campus_environment, clamp, add_event):
    day = current_day(conn)
    environment = campus_environment(conn, day)
    updates = {}
    description = result.get("description", "") if isinstance(result, dict) else ""
    if action == "move":
        updates["campus_flow"] = clamp(int(environment.get("campus_flow", 55)) + 1, 0, 100)
        crowd_fields = {"图书馆": ("library_crowd", 45), "食堂": ("canteen_crowd", 50), "操场": ("playground_crowd", 40), "商业街": ("commercial_crowd", 50)}
        for location, (field, default) in crowd_fields.items():
            if location in description:
                updates[field] = clamp(int(environment.get(field, default)) + 2, 0, 100)
                break
    elif action == "chat":
        updates = {"campus_mood": "活跃", "activity_heat": clamp(int(environment.get("activity_heat", 50)) + 1, 0, 100)}
    elif action == "buy_sell":
        updates = {"consumption_index": round(min(1.8, float(environment.get("consumption_index", 1.0)) + 0.03), 2), "commercial_crowd": clamp(int(environment.get("commercial_crowd", 50)) + 1, 0, 100)}
    elif action == "submit_policy":
        updates = {"resource_pressure": clamp(int(environment.get("resource_pressure", 45)) - 1, 0, 100), "campus_mood": "有序"}
    elif action in {"create_group", "join_group"}:
        updates = {"activity_heat": clamp(int(environment.get("activity_heat", 50)) + 3, 0, 100), "campus_mood": "活跃"}
    elif action == "leave_group":
        updates = {"activity_heat": clamp(int(environment.get("activity_heat", 50)) - 1, 0, 100)}
    elif action == "observe":
        updates = {"study_atmosphere": clamp(int(environment.get("study_atmosphere", 60)) + 1, 0, 100)}
    if updates:
        repository.update_campus_state(conn, day, updates)
        add_event(conn, day, "environment_feedback", f"Agent 行动 {action} 反馈到环境：{updates}")
        conn.commit()
    return updates


def get_campus_environment(conn, day=None, *, ensure_state_table, current_day,
                           default_environment, build_modules):
    ensure_state_table(conn)
    if day is None or not isinstance(day, int):
        try:
            day = int(day)
        except (TypeError, ValueError):
            day = current_day(conn)
    row = repository.campus_state(conn, day)
    if not row:
        previous = repository.latest_campus_state_before(conn, day)
        values = dict(previous) if previous else dict(default_environment)
        values.pop("day", None)
        values.pop("created_at", None)
        repository.ensure_campus_state(conn, day, {key: values.get(key, default) for key, default in default_environment.items()})
        conn.commit()
        row = repository.campus_state(conn, day)
    environment = dict(row)
    environment["modules"] = build_modules(environment)
    return environment


def apply_campus_event_effects(conn, day, effects, *, default_environment, campus_environment):
    updates = effects.get("environment_updates", {}) if isinstance(effects, dict) else {}
    updates = {key: value for key, value in updates.items() if key in default_environment}
    if not updates:
        return {}
    current = campus_environment(conn, day)
    smoothed_updates = {}
    alpha = 0.65  # EWMA smoothing factor for environmental continuity
    for key, target_val in updates.items():
        if key in current and isinstance(current[key], (int, float)) and isinstance(target_val, (int, float)):
            smoothed = alpha * float(target_val) + (1.0 - alpha) * float(current[key])
            if isinstance(target_val, float) or isinstance(current[key], float):
                smoothed_updates[key] = round(smoothed, 2)
            else:
                smoothed_updates[key] = max(0, min(100, round(smoothed)))
        else:
            smoothed_updates[key] = target_val
    repository.update_campus_state(conn, day, smoothed_updates)
    return smoothed_updates


def create_campus_event(conn, day, title, event_type, intensity, target_spaces=None, effects=None, *,
                        ensure_space_system, campus_environment, default_event_configuration,
                        json_dumps, apply_effects, add_event):
    ensure_space_system(conn)
    environment = campus_environment(conn, day)
    targets, default_effects = default_event_configuration(
        environment, event_type, intensity, target_spaces or []
    )
    final_effects = effects or default_effects
    cursor = repository.insert_campus_event(conn, (day, title, event_type, intensity, json_dumps(targets, ensure_ascii=False), json_dumps(final_effects, ensure_ascii=False)))
    updates = apply_effects(conn, day, final_effects)
    add_event(conn, day, "campus_event", f"校园事件《{title}》已触发，类型：{event_type}，影响空间：{targets or '全校'}。")
    conn.commit()
    return {"id": cursor.lastrowid, "title": title, "event_type": event_type, "target_spaces": targets, "effects": final_effects, "environment_updates": updates}


def maybe_generate_environment_event(conn, day, *, active_events, campus_environment,
                                     create_event, random_value):
    if active_events(conn, day):
        return None
    environment = campus_environment(conn, day)
    if int(environment.get("rainfall", 0)) >= 60:
        return create_event(conn, day, "降雨天气预警", "天气预警", 65, ["操场"])
    if int(environment.get("resource_pressure", 0)) >= 80:
        return create_event(conn, day, "设施资源紧张", "设施故障", 55, ["图书馆"])
    if int(environment.get("activity_heat", 0)) >= 75 and random_value() < 0.45:
        return create_event(conn, day, "校园主题活动", "大型活动", 60, ["操场", "教学楼"])
    return None
