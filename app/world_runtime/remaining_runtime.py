from typing import Optional
from dataclasses import dataclass
from typing import Any, Mapping
from app.world_runtime.clock import parse_world_datetime

_MODULE_NAME = __name__


@dataclass(frozen=True)
class RemainingRuntimeDependencies:
    """Explicit composition-root bindings for remaining runtime helpers."""

    values: Mapping[str, Any]

    def apply(self):
        configure(**dict(self.values))

def configure(**bindings):
    module_globals = globals()
    protected = {
        "build_autonomous_tick_decision",
        "build_rule_based_plan",
        "get_space_snapshot",
    }
    for name, value in bindings.items():
        if name.startswith("__") or name in protected:
            continue
        current = module_globals.get(name)
        if callable(current) and getattr(current, "__module__", None) == _MODULE_NAME:
            continue
        module_globals[name] = value
    module_globals["__name__"] = _MODULE_NAME


def create_agent_goal(conn, resident_id, horizon, title, category="general", parent_goal_id=None,
                      source="runtime", priority=50, commitment=50, expected_utility=50,
                      feasibility=50, uncertainty=30, deadline_at="", visibility="private"):
    day = get_current_day(conn)
    cursor = conn.execute(
        """
        INSERT INTO agent_goals
        (resident_id, parent_goal_id, horizon, title, category, source, priority,
         commitment, expected_utility, feasibility, uncertainty, deadline_at,
         status, progress, visibility, created_day, last_reviewed_day)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)
        """,
        (
            resident_id,
            parent_goal_id,
            horizon,
            title[:180],
            category,
            source,
            clamp(priority),
            clamp(commitment),
            clamp(expected_utility),
            clamp(feasibility),
            clamp(uncertainty),
            deadline_at,
            visibility,
            day,
            day,
        ),
    )
    goal = dict(conn.execute("SELECT * FROM agent_goals WHERE id = ?", (cursor.lastrowid,)).fetchone())
    record_goal_revision(
        conn,
        goal["id"],
        resident_id,
        "created",
        after=goal,
        reason=f"运行时生成{horizon}层目标",
        trigger_type="goal_generation",
    )
    return goal


def append_social_interaction_event(
    conn,
    actor_resident_id,
    target_resident_id=None,
    interaction_type="interaction",
    summary="",
    tick_id=None,
    world_event_id=None,
    relationship_change_event_id=None,
    location="",
    channel="in_person",
    intensity=50,
    valence=0,
    visibility="local",
    disclosure_state="ordinary",
    resource_context="",
    institution_context="",
    evidence=None,
):
    ensure_social_system_tables(conn)
    participants = [actor_resident_id]
    if target_resident_id is not None:
        participants.append(target_resident_id)
    conn.execute(
        """
        INSERT INTO social_interaction_events
        (day, tick_id, world_event_id, relationship_change_event_id, actor_resident_id,
         target_resident_id, participants_json, location, interaction_type, interaction_channel,
         intensity, valence, visibility, disclosure_state, resource_context, institution_context,
         observer_summary, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            get_current_day(conn), tick_id, world_event_id, relationship_change_event_id,
            actor_resident_id, target_resident_id, json_dumps(participants, ensure_ascii=False),
            location or "", interaction_type or "interaction", channel or "in_person",
            clamp(intensity), max(-100, min(100, int(valence))), visibility or "local",
            disclosure_state or "ordinary", resource_context or "", institution_context or "",
            summary or "", json_dumps(evidence or {}, ensure_ascii=False),
        ),
    )


def sync_current_day_with_world_date(conn, world_time):
    """Advance the simulation day when the real-world date crosses midnight."""
    current_real_date = world_time.date().isoformat()
    current_day = get_current_day(conn)
    last_real_date = get_simulation_state_value(conn, "world_runtime_current_day_date", "")
    if not last_real_date:
        last_real_date = infer_runtime_day_anchor_date(conn, current_day, current_real_date)
        set_simulation_state_value(conn, "world_runtime_current_day_date", last_real_date)
    parsed_last = parse_world_datetime(last_real_date)
    last_date = parsed_last.date() if parsed_last else world_time.date()

    elapsed_days = (world_time.date() - last_date).days
    if elapsed_days <= 0:
        if last_real_date != current_real_date:
            set_simulation_state_value(conn, "world_runtime_current_day_date", current_real_date)
        return {"advanced": False, "day": current_day, "elapsed_days": 0}

    elapsed_days = min(elapsed_days, 7)
    new_day = current_day + elapsed_days
    set_simulation_state_value(conn, "current_day", new_day)
    set_simulation_state_value(conn, "world_runtime_current_day_date", current_real_date)
    for day in range(current_day + 1, new_day + 1):
        recover_agents_for_new_day(conn, day)
        values = dict(DEFAULT_ENV)
        values.update({"semester_stage": "平时周", "event_name": "真实时间推进"})
        values = derive_environment_from_real_time(values, world_time)
        save_environment_values(conn, day, values)

    append_world_event(
        conn,
        "world_day_rollover",
        "世界日期已自动推进",
        f"真实日期从 {last_date.isoformat()} 推进到 {current_real_date}，仿真日从第 {current_day} 天推进到第 {new_day} 天。",
        day=new_day,
        slot=world_slot_from_hour(world_time.hour),
        payload={
            "previous_day": current_day,
            "new_day": new_day,
            "elapsed_days": elapsed_days,
            "last_real_date": last_date.isoformat(),
            "current_real_date": current_real_date,
        },
        ensure_schema=False,
    )
    return {"advanced": True, "day": new_day, "previous_day": current_day, "elapsed_days": elapsed_days}


def record_learning(conn, resident_id, action, outcome, score_delta, lesson):
    profile = ensure_profile_meta(conn, resident_id)
    if not profile:
        return None
    day = get_current_day(conn)
    skills = load_json_text(profile["skills"], {})
    strategy = load_json_text(profile["strategy"], {})
    action_key = str(action)
    lesson = format_learning_diary(action_key, outcome, lesson)
    skill = skills.get(action_key, {"uses": 0, "score": 0})
    if not isinstance(skill, dict):
        skill = {"uses": int(skill), "score": 0}
    skill["uses"] = int(skill.get("uses", 0)) + 1
    skill["score"] = int(skill.get("score", 0)) + int(score_delta)
    skills[action_key] = skill
    strategy[action_key] = {
        "last_outcome": outcome,
        "last_score_delta": int(score_delta),
        "lesson": lesson,
    }
    conn.execute(
        """
        UPDATE agent_profiles
        SET skills = ?, strategy = ?
        WHERE resident_id = ?
        """,
        (json_dumps(skills, ensure_ascii=False), json_dumps(strategy, ensure_ascii=False), resident_id),
    )
    conn.execute(
        """
        INSERT INTO agent_learning (resident_id, day, action, outcome, score_delta, lesson)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (resident_id, day, action_key, outcome, int(score_delta), lesson),
    )
    add_memory(
        conn,
        resident_id,
        day,
        lesson,
        importance=4,
        memory_type="semantic",
        tags=[action_key, "学习", "经验"],
        source="learning",
    )
    return {
        "resident_id": resident_id,
        "action": action_key,
        "outcome": outcome,
        "score_delta": int(score_delta),
        "lesson": lesson,
        "skills": skills,
        "strategy": strategy,
    }


def action_for_context(role, location, hour, conn=None, env=None, agent=None):
    options = []
    from app.spatial.location_catalog import _categories
    cats = _categories(location, set())
    if 0 <= hour < 6:
        options = [("rest", 7), ("reflect", 3)] if "rest" in cats else [("observe", 4), ("late", 1)]
    elif "rest" in cats:
        options = [("rest", 4), ("reflect", 3), ("observe", 1)] if hour >= 21 or hour < 7 else [("reflect", 3), ("observe", 2)]
    elif "study" in cats:
        options = [("attend_class", 5), ("collaborate", 2), ("late", 0.5), ("observe", 1)]
    elif "consume" in cats:
        options = [("queue", 3), ("consume", 4), ("chat", 2), ("conflict", 0.2)]
    elif "business" in cats:
        options = [("consume", 4), ("queue", 1), ("chat", 2), ("conflict", 0.25)]
    elif "activity" in cats:
        options = [("club_activity", 4), ("chat", 2), ("collaborate", 1), ("observe", 1)]
    elif "service" in cats:
        options = [("request_leave", 1.5), ("collaborate", 3), ("observe", 2)]
    else:
        options = [("observe", 2), ("move", 1)]
    for rule in active_schedule_rules(conn, role, hour, env):
        if not rule.get("location") or rule.get("location") == location:
            noise = float(rule.get("noise") or 0)
            weight = float(rule.get("base_weight") or 1.0) * (1 + random.uniform(-noise, noise))
            options.append((rule["action_type"], weight))
    if conn and env:
        options = [
            (action, weight * causal_multiplier_for_target(conn, env, "action", action))
            for action, weight in options
        ]
    if agent:
        bias = action_noise_for_agent(agent)
        adjusted = []
        for action, weight in options:
            if action in {"chat", "club_activity", "collaborate"}:
                weight *= bias["social"]
            if action in {"attend_class", "observe", "reflect"}:
                weight *= bias["study"]
            if action in {"conflict", "late"}:
                weight *= bias["risk"]
            if action in {"request_leave", "collaborate"}:
                weight *= bias["service"]
            adjusted.append((action, weight))
        options = adjusted
    return weighted_choice(options)


def build_rule_based_plan(conn, resident, window_start, window_end, world_time=None, goal_context=None):
    role = str(resident["role"])
    role_kind = role_group(role)
    if role_group(role) == "teacher":
        intent = "平衡教学、指导学生和校园服务"
    elif role_group(role) == "business":
        intent = "维持校园服务供给并寻找需求变化"
    elif role_group(role) == "service":
        intent = "维护空间秩序和资源稳定"
    elif window_start.hour == 0:
        intent = "在夜间恢复精力，并为白天学习生活做准备"
    elif window_start.hour == 8:
        intent = "推进课程学习并保持必要社交"
    else:
        intent = "整理一天收获并进行轻量社交"
    steps = []
    offsets = [45, 255, 435]
    from app.spatial.location_catalog import best_real_location, choose_weighted_real_location
    for index, offset in enumerate(offsets):
        step_time = window_start + timedelta(minutes=offset + random.randint(-20, 20))
        if step_time >= window_end:
            step_time = window_end - timedelta(minutes=30)
        hour = step_time.hour
        res_loc = dict(resident).get("location", "")
        def _resolve_loc(act):
            return choose_weighted_real_location(conn, resident["id"], act, hour=hour, current_location=res_loc) or best_real_location(conn, act, current_location=res_loc) or res_loc or "校园"

        if 0 <= hour < 6:
            action = "rest"
            location = _resolve_loc(action)
        elif role_kind == "teacher":
            action = "attend_class"
            location = _resolve_loc(action)
        elif role_kind == "business":
            action = "consume"
            location = _resolve_loc(action)
        elif role_kind == "service":
            action = "observe"
            location = _resolve_loc(action)
        elif 11 <= hour < 14:
            action = "consume"
            location = _resolve_loc(action)
        elif 8 <= hour < 18:
            action = "attend_class"
            location = _resolve_loc(action)
        else:
            action = "reflect"
            location = res_loc or _resolve_loc(action)
        steps.append(
            {
                "time": step_time.strftime("%H:%M"),
                "action": action,
                "location": location,
                "goal": (
                    goal_context["short"]["title"]
                    if goal_context
                    else f"{resident['name']}围绕「{resident['goal']}」调整当前节奏"
                ),
            }
        )
    return {
        "resident_id": resident["id"],
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "intent": intent,
        "steps": steps,
        "flexibility": 0.35,
        "source": "rule-based-v1",
    }


def get_space_snapshot(conn, day=None):
    ensure_space_system(conn)
    env = get_campus_environment(conn, day)
    hour = get_environment_hour(env)
    active_events = get_active_campus_events(conn, day)
    actual_counts = {
        row["location"]: row["count"]
        for row in conn.execute("SELECT location, COUNT(*) AS count FROM residents GROUP BY location").fetchall()
    }
    total_agents = sum(actual_counts.values())

    from app.spatial.location_catalog import real_world_locations
    real_locations = real_world_locations(conn)
    if real_locations:
        spaces = []
        cat_aggregates = {
            "食堂": {"capacity": 0, "actual_agents": 0},
            "教学楼": {"capacity": 0, "actual_agents": 0},
            "图书馆": {"capacity": 0, "actual_agents": 0},
            "宿舍区": {"capacity": 0, "actual_agents": 0},
            "商业街": {"capacity": 0, "actual_agents": 0},
            "操场": {"capacity": 0, "actual_agents": 0},
            "校务处": {"capacity": 0, "actual_agents": 0},
        }
        cat_terms = {
            "食堂": ("食堂", "餐厅", "餐饮", "咖啡", "清晏", "清芬", "观畴", "桃李", "紫荆园"),
            "教学楼": ("教学", "教室", "学堂", "实验", "科研", "逸夫"),
            "图书馆": ("图书馆", "阅览"),
            "宿舍区": ("宿舍", "公寓", "寝室", "住宅", "紫荆"),
            "商业街": ("商业", "超市", "商店", "便利"),
            "操场": ("操场", "体育", "球场", "运动"),
            "校务处": ("主楼", "行政", "办公", "服务", "校务"),
        }
        for loc in real_locations:
            location_name = loc["name"]
            capacity = int(loc.get("capacity") or 100)
            actual_agents = int(actual_counts.get(location_name, 0))
            crowd_percent = round(actual_agents * 100 / max(1, total_agents)) if total_agents > 0 else 0
            demand_percent = clamp(env.get("campus_flow", 50))
            raw_status = str(loc.get("status") or "open")
            effective_status = "开放" if raw_status in ("open", "开放") else ("已关闭" if raw_status in ("closed", "已关闭") else raw_status)

            relevant_events = []
            for event in active_events:
                targets = json.loads(event.get("target_spaces") or "[]") if isinstance(event.get("target_spaces"), str) else (event.get("target_spaces") or [])
                if location_name in targets:
                    relevant_events.append(event["title"])

            spaces.append(
                {
                    "code": str(loc["id"]),
                    "name": location_name,
                    "location": location_name,
                    "capacity": capacity,
                    "open_hour": 0,
                    "close_hour": 24,
                    "status": "开放",
                    "crowd_field": "campus_flow",
                    "purpose": f"{loc.get('node_type', 'building')} 空间",
                    "crowd_percent": crowd_percent,
                    "demand_percent": demand_percent,
                    "actual_agents": actual_agents,
                    "estimated_occupancy": round(capacity * demand_percent / 100),
                    "occupancy": actual_agents,
                    "available_slots": max(0, capacity - actual_agents),
                    "effective_status": effective_status,
                    "active_events": relevant_events,
                }
            )

            for cat_name, terms in cat_terms.items():
                if any(term in location_name for term in terms):
                    cat_aggregates[cat_name]["capacity"] += capacity
                    cat_aggregates[cat_name]["actual_agents"] += actual_agents

        for cat_name, agg in cat_aggregates.items():
            cap = max(200, agg["capacity"])
            occ = agg["actual_agents"]
            spaces.append({
                "code": f"cat_{cat_name}",
                "name": cat_name,
                "location": cat_name,
                "capacity": cap,
                "open_hour": 0,
                "close_hour": 24,
                "status": "开放",
                "crowd_field": "campus_flow",
                "purpose": f"{cat_name} 汇总",
                "crowd_percent": round(occ * 100 / max(1, total_agents)) if total_agents > 0 else 0,
                "demand_percent": clamp(env.get("campus_flow", 50)),
                "actual_agents": occ,
                "estimated_occupancy": occ,
                "occupancy": occ,
                "available_slots": max(0, cap - occ),
                "effective_status": "开放",
                "active_events": [],
            })

        return {"hour": hour, "spaces": spaces, "active_events": active_events}

    spaces = []
    for row in conn.execute("SELECT * FROM campus_spaces ORDER BY code").fetchall():
        space = dict(row)
        capacity = int(space["capacity"])
        demand_percent = clamp(env.get(space["crowd_field"], env.get("campus_flow", 50)))
        actual_agents = int(actual_counts.get(space["location"], 0))
        crowd_percent = round(actual_agents * 100 / max(1, total_agents))
        occupancy = actual_agents
        event_status = None
        relevant_events = []
        for event in active_events:
            targets = json.loads(event["target_spaces"])
            effects = json.loads(event["effects"])
            if space["location"] in targets:
                relevant_events.append(event["title"])
                event_status = effects.get("space_status", event_status)
        within_hours = space["open_hour"] <= hour < space["close_hour"] if space["close_hour"] != 24 else hour >= space["open_hour"]
        base_status = space["status"]
        if base_status != "开放":
            effective_status = base_status
        elif event_status:
            effective_status = event_status
        elif not within_hours:
            effective_status = "已关闭"
        elif occupancy >= capacity:
            effective_status = "满员"
        else:
            effective_status = "开放"
        space.update(
            {
                "crowd_percent": crowd_percent,
                "demand_percent": demand_percent,
                "actual_agents": actual_agents,
                "estimated_occupancy": round(capacity * demand_percent / 100),
                "occupancy": occupancy,
                "available_slots": max(0, capacity - occupancy),
                "effective_status": effective_status,
                "active_events": relevant_events,
            }
        )
        spaces.append(space)
    return {"hour": hour, "spaces": spaces, "active_events": active_events}



def default_event_configuration(env, event_type, intensity, target_spaces):
    intensity = clamp(intensity, 1, 100)
    targets = target_spaces or {
        "设施故障": ["图书馆"],
        "天气预警": ["操场"],
        "大型活动": ["操场", "教学楼"],
        "考试通知": ["图书馆", "教学楼"],
    }.get(event_type, [])
    updates = {
        "event_name": event_type,
        "event_intensity": intensity,
    }
    space_status = "开放"
    if event_type == "设施故障":
        space_status = "维护中"
        updates.update(
            {
                "resource_pressure": clamp(int(env["resource_pressure"]) + intensity // 2),
                "campus_mood": "关注中",
            }
        )
    elif event_type == "天气预警":
        space_status = "暂停开放"
        updates.update(
            {
                "playground_crowd": clamp(int(env["playground_crowd"]) - intensity // 2),
                "campus_flow": clamp(int(env["campus_flow"]) - intensity // 4),
                "campus_mood": "谨慎",
            }
        )
    elif event_type == "大型活动":
        updates.update(
            {
                "activity_heat": clamp(int(env["activity_heat"]) + intensity // 3),
                "campus_flow": clamp(int(env["campus_flow"]) + intensity // 4),
                "campus_mood": "活跃",
            }
        )
    elif event_type == "考试通知":
        updates.update(
            {
                "exam_pressure": clamp(int(env["exam_pressure"]) + intensity // 3),
                "study_atmosphere": clamp(int(env["study_atmosphere"]) + intensity // 4),
                "library_crowd": clamp(int(env["library_crowd"]) + intensity // 3),
                "campus_mood": "紧张",
            }
        )
    return targets, {"space_status": space_status, "environment_updates": updates}


def retrieve_relevant_memories(conn, resident_id, query_terms=None, limit=6):
    """Rank personal memories by relevance, importance, recency, and prior reuse."""
    ensure_memory_columns(conn)
    current_day = get_current_day(conn)
    terms = [str(term).strip() for term in (query_terms or []) if str(term).strip()]
    rows = conn.execute(
        """
        SELECT id, day, content, importance, memory_type, tags, source,
               access_count, last_accessed_at, created_at
        FROM memories
        WHERE resident_id = ? AND day <= ?
        ORDER BY id DESC
        LIMIT 120
        """,
        (resident_id, current_day),
    ).fetchall()
    type_bonus = {"relationship": 18, "semantic": 15, "episodic": 9, "working": 5}
    ranked = []
    for row in rows:
        memory = dict(row)
        text = f"{memory.get('tags', '')} {memory['content']}"
        matches = sum(1 for term in terms if term in text)
        age = max(0, current_day - int(memory["day"]))
        score = (
            int(memory["importance"]) * 10
            + type_bonus.get(memory.get("memory_type"), 6)
            + min(int(memory.get("access_count") or 0), 5) * 2
            + matches * 18
            + max(0, 18 - age * 3)
        )
        memory["relevance_score"] = score
        ranked.append(memory)
    selected = sorted(ranked, key=lambda item: item["relevance_score"], reverse=True)[:limit]
    if selected:
        placeholders = ", ".join("?" for _ in selected)
        conn.execute(
            f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            [item["id"] for item in selected],
        )
        for memory in selected:
            memory["access_count"] = int(memory.get("access_count") or 0) + 1
            memory["last_accessed_at"] = "本次决策检索"
    return selected


def perceive_environment(conn, resident_id):
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    day = get_current_day(conn)
    env = get_campus_environment(conn, day)
    module_state = get_agent_module_state(conn, resident_id)
    schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
    location = resident["location"]
    space_snapshot = get_space_snapshot(conn, day)
    current_space = next((space for space in space_snapshot["spaces"] if space["location"] == location), None)
    if not current_space:
        current_space = next(
            (
                space
                for space in space_snapshot["spaces"]
                if location and (location in space["location"] or space["location"] in location)
            ),
            None,
        )

    if current_space and "crowd_percent" in current_space:
        local_crowd = current_space["crowd_percent"]
    elif current_space and "demand_percent" in current_space:
        local_crowd = current_space["demand_percent"]
    else:
        from app.spatial.location_catalog import _categories
        cats = _categories(location, set())
        category_crowd_map = {
            "consume": env.get("canteen_crowd", 50),
            "study": env.get("classroom_crowd", env.get("library_crowd", 50)),
            "rest": env.get("dorm_crowd", 50),
            "activity": env.get("playground_crowd", 50),
            "business": env.get("commercial_crowd", 50),
            "service": env.get("campus_flow", 50),
        }
        local_crowd = env.get("campus_flow", 50)
        for cat in cats:
            if cat in category_crowd_map:
                local_crowd = category_crowd_map[cat]
                break
    from app.spatial.location_catalog import rank_real_location_options
    candidate_options = rank_real_location_options(conn, resident_id, "consume", hour=get_environment_hour(env), weather=env.get("weather", ""))
    nearby_candidate_pois = [
        {
            "name": item["location"],
            "score": item["score"],
            "available": item["available"],
            "estimated_minutes": item["reasons"].get("travel_minutes_estimate", 0),
        }
        for item in candidate_options[:5]
    ]
    perception = {
        "day": day,
        "location": location,
        "weather": env.get("weather"),
        "temperature": env.get("temperature"),
        "rainfall": env.get("rainfall"),
        "local_crowd": local_crowd,
        "campus_mood": env.get("campus_mood"),
        "exam_pressure": env.get("exam_pressure"),
        "activity_heat": env.get("activity_heat"),
        "event_name": env.get("event_name"),
        "network_status": env.get("network_status"),
        "safety_level": env.get("safety_level"),
        "current_space": current_space,
        "nearby_candidate_pois": nearby_candidate_pois,
        "active_events": space_snapshot["active_events"],
        "agent_energy": module_state["modules"]["Physical"]["energy"],
        "agent_mood": module_state["modules"]["Physical"]["mood"],
        "current_task": module_state["modules"]["Mental"]["task"],
    }
    conn.execute(
        "UPDATE agent_profiles SET perception = ? WHERE resident_id = ?",
        (json_dumps(perception, ensure_ascii=False), resident_id),
    )
    add_memory(
        conn,
        resident_id,
        day,
        f"感知环境：当前位置 {location}，天气 {perception['weather']}，局部拥挤度 {local_crowd}，校园情绪 {perception['campus_mood']}。",
        importance=1,
    )
    conn.commit()
    return perception


def plan_step_key(step):
    return "|".join(
        str(step.get(key, "")).strip()
        for key in ("time", "action", "location", "goal")
    )


def choose_plan_step(plan, world_time, current_location="校园"):
    steps = plan.get("steps") or []
    if not steps:
        return {"action": "observe", "location": current_location, "goal": plan.get("intent", "观察校园环境"), "plan_state": "unplanned"}
    current_hm = world_time.strftime("%H:%M")
    normalized = [step if isinstance(step, dict) else {} for step in steps]
    due_steps = [
        (index, step)
        for index, step in enumerate(normalized)
        if str(step.get("time", "00:00")) <= current_hm
    ]
    pending_due = [
        (index, step)
        for index, step in due_steps
        if not step.get("executed_at")
    ]
    if pending_due:
        index, step = pending_due[0]
        selected = dict(step)
        selected["step_index"] = index
        selected["step_key"] = plan_step_key(step)
        selected["plan_state"] = "due"
        return selected
    future_steps = [
        (index, step)
        for index, step in enumerate(normalized)
        if str(step.get("time", "00:00")) > current_hm and not step.get("executed_at")
    ]
    if future_steps:
        index, step = future_steps[0]
        return {
            "action": "observe",
            "location": current_location,
            "goal": f"等待 {step.get('time', '--:--')} 的计划：{step.get('goal') or plan.get('intent') or '继续观察校园环境'}",
            "step_index": index,
            "step_key": plan_step_key(step),
            "plan_state": "waiting",
            "next_step": step,
        }
    return {
        "action": "reflect",
        "location": current_location,
        "goal": plan.get("intent") or "本窗口计划已完成，整理状态等待下一窗口。",
        "plan_state": "completed",
    }


def generate_observed_agent_detail(conn, agent, step, world_time, tick_id, base_event, day, slot):
    if os.getenv("WORLD_RUNTIME_USE_LLM", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    if not should_generate_observed_agent_detail(conn, agent["id"], world_time):
        return None
    if not consume_auto_model_budget(conn, "observer", resident_id=agent["id"]):
        return None
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    prompt = f"""
你是World2的局部观察镜头。用户正在观察这个 Agent，请生成一条短小、具体、可被记录的观察细节。

世界时间：{world_time.strftime('%Y-%m-%d %H:%M')}
Agent：{agent['name']}，{agent['role']}
当前位置：{agent['location']}
长期目标：{agent['goal']}
当前计划步骤：{json_dumps(step, ensure_ascii=False)}

要求：
- 只写 1 句中文，80 字以内。
- 用第三人称描述可观察行为或一瞬间的想法外显，不要写系统解释。
- 不要编造超自然或大规模事件。
"""
    try:
        raw = ask_llm(prompt)
        detail = re.sub(r"\s+", " ", raw).strip().strip('"“”')[:160]
        if not detail:
            raise ValueError("empty observer detail")
        detail_event = append_world_event(
            conn,
            "observer_model_detail",
            f"{agent['name']}的被观察细节",
            detail,
            tick_id=tick_id,
            resident_id=agent["id"],
            location=agent["location"],
            payload={"base_event_id": base_event["id"], "plan_step": step, "trigger": "observer_focus"},
            day=day,
            slot=slot,
        )
        log_model_call(
            conn,
            "observer",
            status="success",
            resident_id=agent["id"],
            related_event_id=detail_event["id"],
            model_name=model_name,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return detail_event
    except Exception as exc:
        logger.warning("Observer LLM detail failed for resident %s", agent["id"], exc_info=True)
        log_model_call(conn, "observer", status=f"failed:{type(exc).__name__}", resident_id=agent["id"], related_event_id=base_event["id"], model_name=model_name)
        return None


def build_autonomous_tick_decision(conn, agent, perception, step):
    import json
    runtime_llm_enabled = os.getenv("WORLD_RUNTIME_USE_LLM", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not runtime_llm_enabled:
        if not is_llm_configured():
            return fallback_runtime_decision(agent, step, "当前世界使用规则决策，按个人计划继续行动。", "rule-unconfigured-v1")
        return fallback_runtime_decision(agent, step, "后台世界循环使用规则决策，按个人计划继续行动。", "rule-runtime-v1")
    budget_fn = consume_auto_model_budget if callable(globals().get("consume_auto_model_budget")) else None
    if budget_fn and not budget_fn(conn, "autonomous_decision", resident_id=agent["id"]):
        if not is_llm_configured():
            return fallback_runtime_decision(agent, step, "当前世界使用规则决策，按个人计划继续行动。", "rule-unconfigured-v1")
        return fallback_runtime_decision(agent, step, "自动模型预算不足，按原计划执行。", "rule-budget-fallback-v1")

    # 1. 通用生理矢量状态 (Generic Physical Vector)
    body_row = conn.execute("SELECT * FROM agent_body_states WHERE resident_id = ?", (agent["id"],)).fetchone()
    body_state = dict(body_row) if body_row else {}

    # 2. 通用 Agent 心智循环 Prompt (Generic Agent Mind Prompt)
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    valid_loc_list = list(VALID_LOCATIONS) if "VALID_LOCATIONS" in globals() else []
    prompt = f"""
你是World2平行世界中一个具备完全独立心智的 Agent 循环决策器。
请根据你的【个人身份与目标】、【当下生理状态】、【环境与社交感知】以及【参考计划】，自主推理做出本 tick 的理性决策。

Agent 核心档案:
- ID: {agent['id']}
- 姓名: {agent['name']}
- 身份角色: {agent['role']}
- 当前位置: {agent['location']}
- 长期使命与目标: {agent['goal']}

当下生理与精神矢量:
{json_dumps(body_state, ensure_ascii=False) if 'json_dumps' in globals() else json.dumps(body_state, ensure_ascii=False)}

环境与社交感知上下文:
{json_dumps(perception, ensure_ascii=False) if 'json_dumps' in globals() else json.dumps(perception, ensure_ascii=False)}

参考计划步骤 (仅供参考，可以自由调整或忽略):
{json_dumps(step, ensure_ascii=False) if 'json_dumps' in globals() else json.dumps(step, ensure_ascii=False)}

请综合考量个人目标、身体状况、环境与社交情境，自主做出符合逻辑的决策。只返回纯 JSON：
{{
  "action": "move|observe|chat|reflect|attend_class|queue|consume|rest|club_activity|conflict|collaborate|late|request_leave",
  "location": "只能从 {valid_loc_list} 中选择",
  "goal": "本 tick 的自主目标，80 字以内",
  "reason": "你做出该决定的完整自主思考逻辑与动机（内心独白），120 字以内",
  "plan_relation": "continue|adjust|respond|rest"
}}
"""
    try:
        raw = ask_llm(prompt)
        payload = extract_json(raw)
        decision = normalize_runtime_decision(payload, step, agent["location"], step.get("goal"))
        log_model_call(
            conn,
            "autonomous_decision",
            status="success",
            resident_id=agent["id"],
            model_name=model_name,
            prompt_version="autonomous-loop-v3",
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return decision
    except Exception as exc:
        logger.warning("Autonomous tick decision failed for resident %s", agent["id"], exc_info=True)
        log_model_call(conn, "autonomous_decision", status=f"failed:{type(exc).__name__}", resident_id=agent["id"], model_name=model_name, prompt_version="autonomous-loop-v3")
        return fallback_runtime_decision(agent, step, "自主决策失败，按原计划执行。", "rule-error-fallback-v1")


def maybe_create_social_commitment(conn, agent_id, target, location):
    existing = conn.execute(
        """
        SELECT * FROM agent_commitments
        WHERE resident_id = ? AND counterparty_resident_id = ?
          AND commitment_type = 'social_collaboration' AND status = 'active'
        ORDER BY id DESC LIMIT 1
        """,
        (agent_id, target["id"]),
    ).fetchone()
    if existing:
        return dict(existing)
    short_goal = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND horizon = 'short' AND status = 'active'
        ORDER BY priority DESC, id LIMIT 1
        """,
        (agent_id,),
    ).fetchone()
    if not short_goal:
        return None
    now = get_world_now()
    cursor = conn.execute(
        """
        INSERT INTO agent_commitments
        (resident_id, goal_id, counterparty_resident_id, commitment_type, title,
         start_at, due_at, status, importance, flexibility, visibility)
        VALUES (?, ?, ?, 'social_collaboration', ?, ?, ?, 'active', 68, 55, 'shared')
        """,
        (
            agent_id,
            short_goal["id"],
            target["id"],
            f"继续与{target['name']}推进在{location}形成的协作",
            now.isoformat(),
            (now + timedelta(days=3)).isoformat(),
        ),
    )
    return dict(conn.execute("SELECT * FROM agent_commitments WHERE id = ?", (cursor.lastrowid,)).fetchone())


def runtime_response(conn):
    current_day = get_current_day(conn)
    runtime = read_world_runtime(conn)
    latest_tick = conn.execute("SELECT * FROM world_ticks ORDER BY id DESC LIMIT 1").fetchone()
    latest_event = conn.execute(
        "SELECT id FROM world_event_stream WHERE branch_key = ? ORDER BY id DESC LIMIT 1",
        (runtime.get("active_branch_key") or "main",),
    ).fetchone()
    runtime["latest_tick"] = dict(latest_tick) if latest_tick else None
    runtime["latest_event_id"] = latest_event["id"] if latest_event else 0
    runtime["budget"] = {
        "date": runtime["budget_date"],
        "auto_model_calls_used": runtime["auto_model_calls_used"],
        "daily_auto_model_budget": runtime["daily_auto_model_budget"],
        "remaining_auto_model_calls": max(0, int(runtime["daily_auto_model_budget"]) - int(runtime["auto_model_calls_used"])),
    }
    runtime["day_sync"] = {
        "advanced": False,
        "day": current_day,
        "elapsed_days": 0,
    }
    runtime["environment_config"] = get_active_environment_config(conn)
    active_branch = conn.execute(
        "SELECT * FROM world_branches WHERE branch_key = ?",
        (runtime.get("active_branch_key") or "main",),
    ).fetchone()
    runtime["active_branch"] = decode_world_branch(active_branch) if active_branch else None
    runtime["multiscale_updates"] = {
        "schedules": [
            decode_world_update_schedule(row)
            for row in conn.execute(
                "SELECT * FROM world_update_schedules WHERE status = 'active' ORDER BY interval_seconds, id"
            ).fetchall()
        ],
        "latest_runs": [
            decode_world_update_run(row)
            for row in conn.execute(
                "SELECT * FROM world_update_runs ORDER BY id DESC LIMIT 3"
            ).fetchall()
        ],
    }
    return runtime


def generate_admin_event_impact(conn, payload, base_event, day):
    if not consume_auto_model_budget(conn, "admin", resident_id=payload.resident_id):
        return None
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    target_text = "、".join(payload.target_spaces) or payload.location or "校园全局"
    prompt = f"""
你是校园平行世界的事件导演。admin 刚刚向世界注入一个事件，请生成一条短的运行反馈。

事件标题：{payload.title}
事件内容：{payload.content or '无补充内容'}
事件类型：{payload.event_type}
目标空间：{target_text}
目标 Agent：{payload.resident_id or '无'}

要求：
- 只写 1 句中文，100 字以内。
- 说明这个事件会如何被校园空间或 Agent 感知到。
- 不要写技术字段，不要承诺尚未执行的长期结果。
"""
    try:
        raw = ask_llm(prompt)
        content = re.sub(r"\s+", " ", raw).strip().strip('"“”')[:180]
        if not content:
            raise ValueError("empty admin impact")
        event = append_world_event(
            conn,
            "admin_model_impact",
            "admin 事件影响已生成",
            content,
            resident_id=payload.resident_id,
            location=payload.location,
            payload={"base_event_id": base_event["id"], "target_spaces": payload.target_spaces},
            day=day,
        )
        log_model_call(
            conn,
            "admin",
            status="success",
            resident_id=payload.resident_id,
            related_event_id=event["id"],
            model_name=model_name,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return event
    except Exception as exc:
        logger.warning("Admin event impact LLM failed", exc_info=True)
        log_model_call(conn, "admin", status=f"failed:{type(exc).__name__}", resident_id=payload.resident_id, related_event_id=base_event["id"], model_name=model_name)
        return None


def build_agent_social_graph(conn, resident_id, limit=10):
    rows = conn.execute(
        """
        SELECT relationships.to_resident_id, residents.name, residents.role,
               relationships.score, relationship_dynamics.affinity, relationship_dynamics.trust,
               relationship_dynamics.cooperation, relationship_dynamics.competition,
               relationship_dynamics.conflict, relationship_dynamics.tension,
               relationship_dynamics.interaction_count
        FROM relationships
        JOIN residents ON residents.id = relationships.to_resident_id
        LEFT JOIN relationship_dynamics
          ON relationship_dynamics.from_resident_id = relationships.from_resident_id
         AND relationship_dynamics.to_resident_id = relationships.to_resident_id
        WHERE relationships.from_resident_id = ?
        ORDER BY relationships.score DESC
        LIMIT ?
        """,
        (resident_id, min(max(limit, 1), 20)),
    ).fetchall()
    histories = relationship_histories_by_target(
        conn,
        resident_id,
        [row["to_resident_id"] for row in rows],
    )
    owner = get_resident(conn, resident_id)
    return {
        "nodes": [{"id": resident_id, "name": owner["name"], "role": owner["role"], "owner": True}]
        + [{"id": row["to_resident_id"], "name": row["name"], "role": row["role"], "owner": False} for row in rows],
        "links": [
            {
                "from": resident_id,
                "to": row["to_resident_id"],
                "score": row["score"],
                "affinity": row["affinity"] if row["affinity"] is not None else 50,
                "trust": row["trust"] if row["trust"] is not None else 50,
                "cooperation": row["cooperation"] if row["cooperation"] is not None else 50,
                "competition": row["competition"] if row["competition"] is not None else 0,
                "conflict": row["conflict"] if row["conflict"] is not None else 0,
                "tension": row["tension"] if row["tension"] is not None else 0,
                "interaction_count": row["interaction_count"] if row["interaction_count"] is not None else 0,
                "emergent_interpretation": infer_emergent_relationship(
                    conn,
                    resident_id,
                    row["to_resident_id"],
                    dict(row),
                    row["score"],
                    history_rows=histories.get(int(row["to_resident_id"]), []),
                ),
            }
            for row in rows
        ],
    }


def agent_newspaper_posts(day: Optional[int] = None):
    """Return campus newspaper posts for the requested day, defaulting to today or latest issue."""
    with get_connection() as conn:
        ensure_agent_news_system(conn)
        current_day = get_current_day(conn)
        days = [
            int(row["day"])
            for row in conn.execute(
                "SELECT DISTINCT day FROM agent_news_posts ORDER BY day DESC LIMIT 60"
            ).fetchall()
        ]
        all_days = sorted(list(set(days + [current_day])))
        latest_day = max(all_days) if all_days else current_day

        if day is not None:
            target_day = max(1, int(day))
        else:
            target_day = latest_day

        posts = conn.execute(
            """
            SELECT p.id, p.day, p.resident_id, r.name, r.role, p.source_slot,
                   p.source_event_id, p.news_value, p.headline, p.content, p.created_at
            FROM agent_news_posts p
            JOIN residents r ON r.id = p.resident_id
            WHERE p.day = ?
            ORDER BY p.id DESC
            LIMIT 12
            """,
            (target_day,),
        ).fetchall()

        previous_day = next((item for item in reversed(all_days) if item < target_day), None)
        next_day = next((item for item in all_days if item > target_day), None)

        is_today = (target_day == latest_day)
        return {
            "day": target_day,
            "current_day": latest_day,
            "edition": {
                "kind": "rolling" if is_today else "archive",
                "label": "今日滚动版" if is_today else f"第 {target_day} 天归档日报",
                "brief_count": len(posts),
                "issue_key": f"day-{target_day}",
            },
            "available_days": days,
            "previous_day": previous_day,
            "next_day": next_day,
            "posts": rows_to_dicts(posts),
        }
