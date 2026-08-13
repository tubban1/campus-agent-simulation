from datetime import datetime, timedelta, timezone


_MODULE_NAME = __name__


def clamp(value, low=0, high=100):
    """Keep environment derivation usable outside the composition root."""
    return max(low, min(high, value))


def configure(**bindings):
    module_globals = globals()
    for name, value in bindings.items():
        if name.startswith("__"):
            continue
        current = module_globals.get(name)
        if callable(current) and getattr(current, "__module__", None) == _MODULE_NAME:
            continue
        module_globals[name] = value
    module_globals["__name__"] = _MODULE_NAME


def get_real_campus_time(now=None):
    tz = timezone(timedelta(hours=8))
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    hour = current.hour
    if 5 <= hour <= 10:
        time_slot = "上午"
    elif 11 <= hour <= 13:
        time_slot = "中午"
    elif 14 <= hour <= 17:
        time_slot = "下午"
    elif 18 <= hour <= 23:
        time_slot = "晚上"
    else:
        time_slot = "深夜"

    month = current.month
    day = current.day
    if month in {2, 8}:
        semester_stage = "假期"
    elif month in {1, 7}:
        semester_stage = "考试周"
    elif month in {4, 11} and 10 <= day <= 25:
        semester_stage = "期中周"
    elif month in {3, 9} and day <= 20:
        semester_stage = "开学适应期"
    elif month in {5, 10}:
        semester_stage = "活动周"
    else:
        semester_stage = "平时周"

    return {
        "real_date": current.strftime("%Y-%m-%d"),
        "real_time": current.strftime("%H:%M:%S"),
        "weekday": weekdays[current.weekday()],
        "time_slot": time_slot,
        "semester_stage": semester_stage,
        "time_source": "system_clock",
        "hour": hour,
        "is_weekend": current.weekday() >= 5,
    }


def derive_environment_from_real_time(values, now=None):
    real_time = get_real_campus_time(now)
    hour = real_time["hour"]
    is_weekend = real_time["is_weekend"]
    time_slot = real_time["time_slot"]
    semester_stage = real_time["semester_stage"]
    values.update({key: real_time[key] for key in ["real_date", "real_time", "weekday", "time_slot", "semester_stage", "time_source"]})

    class_peak = 0 if is_weekend else (75 if 8 <= hour <= 11 or 14 <= hour <= 17 else 30)
    canteen_peak = 90 if 11 <= hour <= 13 or 17 <= hour <= 19 else (45 if 7 <= hour <= 9 else 25)
    library_base = 75 if semester_stage in {"期中周", "考试周"} else 45
    library_peak = library_base + (20 if 18 <= hour <= 22 else 0) - (15 if is_weekend and hour < 12 else 0)
    dorm_peak = 85 if hour >= 22 or hour <= 7 else (55 if 12 <= hour <= 14 else 35)
    playground_peak = 70 if 16 <= hour <= 20 else 25
    commercial_peak = 80 if 12 <= hour <= 14 or 18 <= hour <= 21 else 40
    rainfall = int(values.get("rainfall", 0) or 0)
    temperature = int(values.get("temperature", 24) or 24)
    wind_speed = float(values.get("wind_speed_10m", 0) or 0)
    weather = str(values.get("weather", "晴"))

    outdoor_penalty = min(65, rainfall // 2 + (20 if wind_speed > 25 else 0))
    heat_penalty = 15 if temperature >= 32 else 0

    playground_crowd = clamp(playground_peak - outdoor_penalty - heat_penalty, 0, 100)
    library_crowd = clamp(library_peak + rainfall // 4 + (10 if outdoor_penalty > 20 else 0), 0, 100)
    canteen_crowd = clamp(canteen_peak + (15 if rainfall > 20 else 0), 0, 100)
    dorm_crowd = clamp(dorm_peak + (15 if rainfall > 20 or wind_speed > 30 else 0), 0, 100)
    commercial_crowd = clamp(commercial_peak - rainfall // 4 + (8 if temperature >= 30 else 0), 0, 100)
    classroom_crowd = clamp(class_peak, 0, 100)

    exam_pressure = 82 if semester_stage == "考试周" else (65 if semester_stage == "期中周" else int(values.get("exam_pressure", 35)))
    activity_heat = 75 if semester_stage == "活动周" else int(values.get("activity_heat", 50))
    if is_weekend:
        activity_heat = min(100, activity_heat + 10)

    values.update({
        "exam_pressure": clamp(exam_pressure, 0, 100),
        "assignment_pressure": clamp(70 if semester_stage in {"期中周", "考试周"} else int(values.get("assignment_pressure", 40)), 0, 100),
        "study_atmosphere": clamp(55 + exam_pressure // 3 + (10 if time_slot == "晚上" else 0), 0, 100),
        "activity_heat": clamp(activity_heat, 0, 100),
        "event_name": "真实时间与天气驱动校园状态",
        "event_intensity": clamp(activity_heat + (10 if time_slot in {"中午", "晚上"} else 0), 0, 100),
        "classroom_crowd": classroom_crowd,
        "canteen_crowd": canteen_crowd,
        "library_crowd": library_crowd,
        "dorm_crowd": dorm_crowd,
        "playground_crowd": playground_crowd,
        "commercial_crowd": commercial_crowd,
    })
    campus_flow = (classroom_crowd + canteen_crowd + commercial_crowd + playground_crowd) // 4
    values["campus_flow"] = clamp(campus_flow + (10 if time_slot in {"中午", "下午"} else 0) - rainfall // 4, 0, 100)
    values["traffic_status"] = "拥堵" if (values["campus_flow"] >= 75 or rainfall >= 40 or wind_speed >= 35) else "正常"
    values["network_status"] = "拥堵" if (values["dorm_crowd"] >= 75 and (time_slot in {"晚上", "深夜"} or rainfall > 30)) else "稳定"
    values["resource_pressure"] = clamp((canteen_crowd + library_crowd + classroom_crowd) // 3, 0, 100)
    values["safety_level"] = clamp(95 - rainfall // 6 - int(wind_speed) // 4 - values["campus_flow"] // 10, 30, 100)

    if weather in {"大雨", "暴雨", "大雪", "雷雨"} or wind_speed >= 40:
        values["campus_mood"] = "严酷"
    elif weather in {"小雨", "中雨", "小雪", "雾", "闷热"}:
        values["campus_mood"] = "低落"
    elif values["exam_pressure"] >= 75:
        values["campus_mood"] = "紧张"
    elif values["activity_heat"] >= 70:
        values["campus_mood"] = "活跃"
    else:
        values["campus_mood"] = "平稳"

    values["consumption_index"] = round(max(0.5, min(1.8, 0.75 + values["commercial_crowd"] / 180 + values["canteen_crowd"] / 260)), 2)
    return values


def default_environment_config():
    spaces = [
        {
            "code": code,
            "name": name,
            "location": location,
            "capacity": capacity,
            "open_hour": open_hour,
            "close_hour": close_hour,
            "status": status,
            "crowd_field": crowd_field,
            "purpose": purpose,
        }
        for code, name, location, capacity, open_hour, close_hour, status, crowd_field, purpose in DEFAULT_SPACES
    ]
    return {
        "schema_version": "environment-config-v1",
        "campus": {
            "key": "campus-default",
            "name": "默认校园平行世界",
            "school_type": "综合校园",
            "timezone": WORLD_TIMEZONE,
            "semester_system": "term",
        },
        "spaces": spaces,
        "population": {
            "initial_size": 20,
            "role_mix": {"student": 0.70, "teacher": 0.10, "business": 0.10, "service": 0.10},
            "generation_mode": "seeded_profiles",
        },
        "institutions": {
            "access_policy": "campus-default-v1",
            "schedule_rule_version": "campus-schedule-v1",
            "organizations": ["教学系统", "校务系统", "学生社团", "校园商业"],
        },
        "economy": {
            "currency": "campus_credit",
            "price_baseline": 1.0,
            "resource_abundance": 0.65,
            "ledger_mode": "legacy-transactions",
        },
        "external_context": {
            "city": "北京",
            "culture": "campus-local",
            "policy_context": "baseline",
            "external_data_mode": "live",
        },
        "environment_baseline": {
            key: value
            for key, value in DEFAULT_ENV.items()
            if key not in {"real_date", "real_time", "weather_observed_at"}
        },
        "rules": {
            "world_rule_version": "world-runtime-v1",
            "causal_weight_version": "causal-weights-v1",
            "action_taxonomy": "world-runtime-v3",
        },
    }


def validate_environment_config(config):
    if not isinstance(config, dict):
        raise ValueError("环境配置必须是 JSON 对象")
    required = {"campus", "spaces", "population", "institutions", "economy", "external_context"}
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"环境配置缺少字段：{', '.join(missing)}")
    for section in required - {"spaces"}:
        if not isinstance(config[section], dict):
            raise ValueError(f"环境配置字段 {section} 必须是对象")
    if not isinstance(config["spaces"], list) or not config["spaces"]:
        raise ValueError("环境配置至少需要一个空间")
    codes = set()
    locations = set()
    for space in config["spaces"]:
        if not isinstance(space, dict):
            raise ValueError("空间配置必须是对象")
        code = str(space.get("code") or "").strip()
        location = str(space.get("location") or "").strip()
        if not code or not location:
            raise ValueError("每个空间必须包含 code 和 location")
        if code in codes or location in locations:
            raise ValueError("空间 code 和 location 必须唯一")
        codes.add(code)
        locations.add(location)
        try:
            capacity = int(space.get("capacity"))
            open_hour = int(space.get("open_hour"))
            close_hour = int(space.get("close_hour"))
        except (TypeError, ValueError) as exc:
            raise ValueError("空间容量和开放时间必须是整数") from exc
        if capacity <= 0 or not 0 <= open_hour <= 24 or not 0 <= close_hour <= 24:
            raise ValueError("空间容量必须大于 0，开放时间必须在 0-24 之间")
    expected_locations = set(VALID_LOCATIONS)
    if locations != expected_locations:
        missing_locations = sorted(expected_locations - locations)
        unsupported_locations = sorted(locations - expected_locations)
        details = []
        if missing_locations:
            details.append(f"缺少地点：{', '.join(missing_locations)}")
        if unsupported_locations:
            details.append(f"当前 runtime 尚不支持：{', '.join(unsupported_locations)}")
        raise ValueError("当前环境配置必须覆盖七个 runtime 地点；" + "；".join(details))
    baseline = config.get("environment_baseline", {})
    if baseline is not None and not isinstance(baseline, dict):
        raise ValueError("environment_baseline 必须是对象")
    unknown_baseline = sorted(set(baseline or {}) - set(ENV_COLUMN_TYPES))
    if unknown_baseline:
        raise ValueError(f"environment_baseline 包含未知字段：{', '.join(unknown_baseline)}")
    return config


def apply_environment_config(conn, config_row):
    config = load_json_text(config_row["config_json"], {})
    validate_environment_config(config)
    ensure_campus_state_table(conn)
    ensure_space_system(conn)
    for space in config["spaces"]:
        conn.execute(
            """
            INSERT INTO campus_spaces
            (code, name, location, capacity, open_hour, close_hour, status, crowd_field, purpose)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                location = excluded.location,
                capacity = excluded.capacity,
                open_hour = excluded.open_hour,
                close_hour = excluded.close_hour,
                status = excluded.status,
                crowd_field = excluded.crowd_field,
                purpose = excluded.purpose,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                space["code"],
                space.get("name") or space["location"],
                space["location"],
                int(space["capacity"]),
                int(space["open_hour"]),
                int(space["close_hour"]),
                space.get("status") or "开放",
                space.get("crowd_field") or "campus_flow",
                space.get("purpose") or "",
            ),
        )
    baseline = config.get("environment_baseline")
    applied_baseline = []
    if isinstance(baseline, dict):
        allowed = sorted(set(baseline) & set(ENV_COLUMN_TYPES) - {"real_date", "real_time", "time_source"})
        if allowed:
            day = get_current_day(conn)
            get_campus_environment(conn, day)
            set_clause = ", ".join(f"{key} = ?" for key in allowed)
            conn.execute(
                f"UPDATE campus_state SET {set_clause} WHERE day = ?",
                [baseline[key] for key in allowed] + [day],
            )
            applied_baseline = allowed
    conn.execute("UPDATE environment_configs SET status = 'archived' WHERE status = 'active'")
    conn.execute("UPDATE environment_configs SET status = 'active' WHERE id = ?", (config_row["id"],))
    version_label = environment_version_label(config_row)
    conn.execute(
        """
        UPDATE world_runtime
        SET environment_config_id = ?, environment_version = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (config_row["id"], version_label, WORLD_RUNTIME_ID),
    )
    return {"spaces": len(config["spaces"]), "baseline_fields": applied_baseline}


def build_environment_modules(env):
    return {
        "TimeWeather": {
            "description": "时间、天气和学期阶段",
            "weather": env["weather"],
            "temperature": env["temperature"],
            "rainfall": env["rainfall"],
            "weather_source": env["weather_source"],
            "weather_observed_at": env["weather_observed_at"],
            "real_date": env.get("real_date", ""),
            "real_time": env.get("real_time", ""),
            "time_source": env.get("time_source", "simulation"),
            "weekday": env["weekday"],
            "time_slot": env["time_slot"],
            "semester_stage": env["semester_stage"],
        },
        "Academic": {
            "description": "学习氛围、考试压力和作业压力",
            "exam_pressure": env["exam_pressure"],
            "assignment_pressure": env["assignment_pressure"],
            "study_atmosphere": env["study_atmosphere"],
        },
        "Activity": {
            "description": "校园活动与事件热度",
            "activity_heat": env["activity_heat"],
            "event_name": env["event_name"],
            "event_intensity": env["event_intensity"],
        },
        "Crowd": {
            "description": "校园各空间人流和拥挤度",
            "campus_flow": env["campus_flow"],
            "classroom_crowd": env["classroom_crowd"],
            "canteen_crowd": env["canteen_crowd"],
            "library_crowd": env["library_crowd"],
            "dorm_crowd": env["dorm_crowd"],
            "playground_crowd": env["playground_crowd"],
            "commercial_crowd": env["commercial_crowd"],
        },
        "Infrastructure": {
            "description": "交通、网络、资源和安全秩序",
            "traffic_status": env["traffic_status"],
            "network_status": env["network_status"],
            "safety_level": env["safety_level"],
            "resource_pressure": env["resource_pressure"],
        },
        "Business": {
            "description": "商业消费和校园整体情绪",
            "consumption_index": env["consumption_index"],
            "campus_mood": env["campus_mood"],
        },
    }
