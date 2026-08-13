"""Read response shaping for lifecycle endpoints."""

from fastapi import HTTPException


def life_course_action_label(action):
    return {
        "move": "移动", "chat": "交流", "buy_sell": "交易", "submit_policy": "政策提案",
        "observe": "观察", "create_group": "创建群体", "join_group": "加入群体",
        "leave_group": "离开群体", "attend_class": "参加课程", "club_activity": "参加活动",
        "collaborate": "协作", "conflict": "冲突", "reflect": "反思", "rest": "休息",
    }.get(str(action or "").strip(), "行动")


def life_course_evidence(source, row_id):
    return {"source": source, "id": row_id}


def score_life_course_event(event):
    score = int(event.get("importance") or 1)
    reasons = []
    event_type = str(event.get("event_type") or "")
    action = str(event.get("action") or "")
    content = str(event.get("content") or "")
    is_memory = event.get("source") == "memories"
    if action in {"chat", "conflict", "collaborate", "create_group", "join_group", "leave_group", "submit_policy"}:
        score += 2
        reasons.append("社会互动或群体行为")
    if action in {"conflict", "submit_policy", "create_group", "leave_group"} or "conflict" in event_type:
        score += 2
        reasons.append("可能改变关系或群体状态")
    if "failed" in event_type or event.get("success") is False:
        score += 2
        reasons.append("行动失败或运行异常")
    if event.get("goal_completed"):
        score += 3
        reasons.append("长期目标完成")
    if event.get("memory_importance", 0) >= 3:
        score += 1 if is_memory else 2
        reasons.append("留下重要记忆" if is_memory else "行动被记忆强化")
    if event.get("spread_count", 0) > 1:
        score += 1
        reasons.append("影响多个对象或地点")
    if any(word in content for word in ("关系", "小组", "冲突", "目标", "政策", "信息")):
        score += 1
    if not reasons:
        reasons.append("构成日常行动轨迹")
    event["turning_point_score"] = min(score, 12)
    event["significance"] = "turning_point" if score >= 7 else ("important" if score >= 4 else "ordinary")
    event["significance_reasons"] = reasons
    return event

def life_course_kind(item):
    return "memory" if item.get("source") == "memories" else "action"

def life_course_display_title(item):
    source = item.get("source")
    action = item.get("action")
    if source == "memories":
        return "日记与记忆" if item.get("memory_source") == "diary" else "记忆沉淀"
    if source == "simulation_action_logs":
        return f"{life_course_action_label(action)}路线"
    if source == "world_event_stream":
        return item.get("title") or f"{life_course_action_label(action)}事件"
    return item.get("title") or "校园经历"

def life_course_turning_summary(item):
    reasons = item.get("significance_reasons") or []
    if item.get("source") == "memories":
        prefix = "重要记忆" if item.get("memory_source") != "diary" else "重要日记"
    elif item.get("significance") == "turning_point":
        prefix = "关键转折"
    else:
        prefix = "重要行动"
    return f"{prefix} · {'、'.join(reasons[:2]) if reasons else '值得回看'}"


def life_course_temporal_coverage(current_day, latest_recorded_day, from_day=None, to_day=None):
    current = max(1, int(current_day or 1))
    latest = int(latest_recorded_day) if latest_recorded_day is not None else None
    requested_to = max(1, int(to_day)) if to_day is not None else current
    requested_from = max(1, int(from_day)) if from_day is not None else None
    return {
        "current_day": current,
        "latest_recorded_day": latest,
        "has_current_day_record": latest == current,
        "days_without_records_after_latest": max(0, current - latest) if latest is not None else None,
        "requested_from_day": requested_from,
        "requested_to_day": requested_to,
        "window_includes_current_day": requested_to >= current,
    }


def life_course_episodes(timeline):
    by_day = {}
    for event in timeline:
        day = int(event.get("day") or 0)
        if day <= 0:
            continue
        episode = by_day.setdefault(day, {"id": f"day-{day}", "day": day, "event_ids": [], "actions": [], "locations": [], "evidence": [], "event_count": 0, "repeat_count": 0, "planned_actions": [], "actual_actions": [], "deviations": [], "feedback": [], "memories": [], "state_before": None, "state_after": None, "reasons": []})
        episode["event_ids"].append(event.get("id"))
        episode["event_count"] += 1
        episode["repeat_count"] += max(1, int(event.get("repeat_count") or 1))
        if event.get("action") and event["action"] != "memory" and event["action"] not in episode["actions"]: episode["actions"].append(event["action"])
        decision = event.get("decision") if isinstance(event.get("decision"), dict) else {}
        execution = event.get("execution") if isinstance(event.get("execution"), dict) else {}
        planned = decision.get("planned_action") or decision.get("action")
        actual = execution.get("action") or (event.get("action") if event.get("action") != "memory" else None)
        if planned and planned not in episode["planned_actions"]: episode["planned_actions"].append(planned)
        if actual and actual not in episode["actual_actions"]: episode["actual_actions"].append(actual)
        if planned and actual and planned != actual: episode["deviations"].append({"planned": planned, "actual": actual, "reason": decision.get("reason", "")})
        reason = str(decision.get("reason") or event.get("content") or "").strip()
        if reason and reason not in episode["reasons"] and event.get("source") != "memories": episode["reasons"].append(reason)
        if event.get("location") and event["location"] not in episode["locations"]: episode["locations"].append(event["location"])
        if event.get("environment_feedback"): episode["feedback"].append(event["environment_feedback"])
        if event.get("source") == "memories": episode["memories"].append(event.get("content", ""))
        before = event.get("state_before") if isinstance(event.get("state_before"), dict) else None
        after = event.get("state_after") if isinstance(event.get("state_after"), dict) else None
        if before and episode["state_before"] is None: episode["state_before"] = before
        if after: episode["state_after"] = after
        episode["evidence"].extend(event.get("evidence") or [])
    episodes = []
    for episode in sorted(by_day.values(), key=lambda item: item["day"], reverse=True):
        labels = [life_course_action_label(action) for action in episode["actions"][:4]]
        episode["title"] = f"第{episode['day']}天经历片段"
        episode["summary"] = "、".join(labels) if labels else "校园日常观察"
        episode["evidence"] = episode["evidence"][:20]
        before, after = episode.get("state_before") or {}, episode.get("state_after") or {}
        changes = {key: {"before": before.get(key), "after": after.get(key)} for key in ("location", "energy", "time_budget", "mood", "current_task") if before.get(key) != after.get(key) and (before.get(key) is not None or after.get(key) is not None)}
        feedback_keys = [f"{key}={value}" for item in episode["feedback"] if isinstance(item, dict) for key, value in item.items()]
        impact_parts = []
        if changes: impact_parts.append("状态变化：" + "、".join(f"{key} {value['before']}→{value['after']}" for key, value in changes.items()))
        if episode["memories"]: impact_parts.append(f"形成 {len(episode['memories'])} 条后续记忆")
        if feedback_keys: impact_parts.append("环境反馈：" + "、".join(feedback_keys[:4]))
        episode["narrative"] = {"intention": "、".join(life_course_action_label(item) for item in episode["planned_actions"][:4]) or "未记录计划", "actual": "、".join(life_course_action_label(item) for item in episode["actual_actions"][:4]) or "未记录行动", "deviation_count": len(episode["deviations"]), "memory_count": len(episode["memories"]), "feedback_count": len(episode["feedback"])}
        episode["narrative"]["reasons"] = episode["reasons"][:3]
        episode["impact"] = {"state_changes": changes, "interpretation": "；".join(impact_parts) + "。这些是时序上观察到的结果，不代表已证明因果关系。" if impact_parts else "当前片段暂无可观测的后续状态变化。"}
        episodes.append(episode)
    return episodes


def life_course_groups(conn, resident_id, timeline, *, load_json, rows_to_dicts):
    groups = []
    rows = conn.execute("SELECT * FROM group_goals ORDER BY status, id DESC").fetchall()
    for row in rows:
        members = load_json(row["member_ids"], [])
        member_ids = [int(member) for member in members if str(member).isdigit()]
        if resident_id not in member_ids and int(row["leader_id"]) != resident_id:
            continue
        evidence = [event["id"] for event in timeline if any(word in str(event.get("event_type") or "") for word in ("group", "collabor")) or any(word in str(event.get("content") or "") for word in (str(row["name"]), str(row["shared_goal"]))) ]
        groups.append({"id": row["id"], "name": row["name"], "group_type": row["group_type"], "leader_id": row["leader_id"], "member_ids": member_ids, "roles": load_json(row["roles"], {}), "shared_goal": row["shared_goal"], "progress": row["progress"], "target_progress": row["target_progress"], "status": row["status"], "evidence_event_ids": evidence[:12], "membership_history_available": False})
        history = conn.execute("SELECT id, day, resident_id, action, reason, member_ids, created_at FROM group_membership_events WHERE group_id = ? ORDER BY day ASC, id ASC LIMIT 100", (row["id"],)).fetchall()
        groups[-1]["membership_history"] = rows_to_dicts(history)
        groups[-1]["membership_history_available"] = bool(history)
    return groups


def life_course_relationships(conn, resident_id, timeline, *, rows_to_dicts, infer_relationship):
    rows = conn.execute("""SELECT relationships.to_resident_id, residents.name, residents.role, relationships.score, relationship_dynamics.affinity, relationship_dynamics.trust, relationship_dynamics.cooperation, relationship_dynamics.competition, relationship_dynamics.conflict, relationship_dynamics.interaction_count FROM relationships JOIN residents ON residents.id = relationships.to_resident_id LEFT JOIN relationship_dynamics ON relationship_dynamics.from_resident_id = relationships.from_resident_id AND relationship_dynamics.to_resident_id = relationships.to_resident_id WHERE relationships.from_resident_id = ? ORDER BY relationships.score DESC LIMIT 12""", (resident_id,)).fetchall()
    result = []
    for row in rows:
        target_id = row["to_resident_id"]
        history_rows = conn.execute("""SELECT id, day, tick_id, event_id, interaction, reason, affinity_before, affinity_after, trust_before, trust_after, cooperation_before, cooperation_after, competition_before, competition_after, conflict_before, conflict_after, created_at FROM relationship_change_events WHERE from_resident_id = ? AND to_resident_id = ? ORDER BY day ASC, id ASC LIMIT 30""", (resident_id, target_id)).fetchall()
        related = []
        for event in timeline:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            social = payload.get("social_effect") if isinstance(payload.get("social_effect"), dict) else {}
            if social.get("target_id") == target_id or payload.get("target_id") == target_id:
                related.append(event["id"])
        result.append({"resident_id": target_id, "name": row["name"], "role": row["role"], "score": row["score"], "affinity": row["affinity"] if row["affinity"] is not None else 50, "trust": row["trust"] if row["trust"] is not None else 50, "cooperation": row["cooperation"] if row["cooperation"] is not None else 50, "competition": row["competition"] if row["competition"] is not None else 0, "conflict": row["conflict"] if row["conflict"] is not None else 0, "interaction_count": row["interaction_count"] if row["interaction_count"] is not None else 0, "evidence_event_ids": related[:12], "history_available": bool(history_rows), "history": rows_to_dicts(history_rows), "emergent_interpretation": infer_relationship(conn, resident_id, target_id, dict(row), row["score"])})
    return result


def life_course_timeline(conn, resident_id, from_day=None, to_day=None, limit=240, *, active_branch, load_json):
    clauses = ["resident_id = ?"]
    params = [resident_id]
    if from_day is not None: clauses.append("day >= ?"); params.append(max(1, int(from_day)))
    if to_day is not None: clauses.append("day <= ?"); params.append(max(1, int(to_day)))
    where = " AND ".join(clauses)
    branch_key = active_branch(conn)
    events, seen_action_keys, world_event_items, memory_items = [], set(), {}, {}
    row_limit = min(max(int(limit), 20), 500)
    for row in conn.execute(f"SELECT id, tick_id, day, slot, event_type, resident_id, location, title, content, payload, created_at FROM world_event_stream WHERE {where} AND branch_key = ? AND event_type NOT IN ('observer_session', 'observer_model_detail') ORDER BY day ASC, id ASC LIMIT ?", params + [branch_key, row_limit]).fetchall():
        payload = load_json(row["payload"], {})
        action = payload.get("action") or payload.get("runtime_decision", {}).get("action")
        key = (row["day"], str(action or row["event_type"]), row["location"] or "", row["content"] or "")
        if key in world_event_items:
            world_event_items[key]["repeat_count"] += 1
            world_event_items[key]["evidence"].append(life_course_evidence("world_event_stream", row["id"]))
            continue
        item = {"id": row["id"], "day": row["day"], "slot": row["slot"], "event_type": row["event_type"], "action": action, "title": row["title"], "content": row["content"], "location": row["location"], "created_at": row["created_at"], "source": "world_event_stream", "evidence": [life_course_evidence("world_event_stream", row["id"])], "payload": payload, "success": row["event_type"] not in {"agent_tick_failed", "world_tick_failed"}, "memory_importance": 0, "spread_count": len(payload.get("recipients", [])) if isinstance(payload.get("recipients"), list) else 0, "repeat_count": 1}
        world_event_items[key] = item; seen_action_keys.add(key); events.append(score_life_course_event(item))
    for row in conn.execute(f"SELECT id, day, tick_id, perception, retrieved_memories, decision, execution, environment_feedback, state_before, state_after, created_at FROM simulation_action_logs WHERE {where} ORDER BY day ASC, id ASC LIMIT ?", params + [row_limit]).fetchall():
        decision, execution = load_json(row["decision"], {}), load_json(row["execution"], {})
        feedback = load_json(row["environment_feedback"], {})
        action = decision.get("action") or execution.get("action")
        result = execution.get("result") if isinstance(execution, dict) else {}
        result = result if isinstance(result, dict) else {}
        location = result.get("location") or decision.get("tool_input", {}).get("destination", "")
        key = (row["day"], str(action or "action"), str(location or ""), str(result.get("description") or result.get("message") or decision.get("reason") or ""))
        if key in seen_action_keys: continue
        seen_action_keys.add(key)
        goal_update = execution.get("long_term_goal") if isinstance(execution, dict) else {}
        item = {"id": row["id"], "day": row["day"], "slot": "", "event_type": "simulation_action", "action": action, "title": f"{life_course_action_label(action)}行动", "content": result.get("description") or result.get("message") or str(decision.get("reason") or "完成一次行动"), "location": location, "created_at": row["created_at"], "tick_id": row["tick_id"], "source": "simulation_action_logs", "evidence": [life_course_evidence("simulation_action_logs", row["id"])], "decision": decision, "execution": execution, "environment_feedback": feedback, "retrieved_memories": load_json(row["retrieved_memories"], []), "state_before": load_json(row["state_before"], {}), "state_after": load_json(row["state_after"], {}), "success": execution.get("success", "error" not in result), "goal_completed": isinstance(goal_update, dict) and goal_update.get("status") == "completed", "memory_importance": 0, "spread_count": 0, "repeat_count": 1}
        events.append(score_life_course_event(item))
    for row in conn.execute(f"SELECT id, day, content, importance, memory_type, source, created_at FROM memories WHERE {where} ORDER BY day ASC, id ASC LIMIT ?", params + [row_limit]).fetchall():
        importance, source = int(row["importance"] or 1), str(row["source"] or "action")
        if importance < 2 and source not in {"diary", "relationship", "fallback", "world_tick"}: continue
        item = {"id": row["id"], "day": row["day"], "slot": "", "event_type": "memory", "action": "memory", "title": "梦境片段（非事实）" if source == "dream" else ("个人经历记录" if source != "diary" else "个人日记"), "content": row["content"], "location": "", "created_at": row["created_at"], "source": "memories", "evidence": [life_course_evidence("memories", row["id"])], "importance": importance, "memory_type": row["memory_type"], "memory_source": source, "memory_importance": importance, "success": True, "spread_count": 0, "repeat_count": 1}
        key = (row["day"], source, row["content"] or "")
        if key in memory_items:
            memory_items[key]["repeat_count"] += 1; memory_items[key]["evidence"].append(life_course_evidence("memories", row["id"]))
            continue
        memory_items[key] = item; events.append(score_life_course_event(item))
    events.sort(key=lambda item: (int(item.get("day") or 0), str(item.get("created_at") or ""), int(item.get("id") or 0)))
    return events[-row_limit:]


def build_life_course_overview(conn, resident_id, from_day=None, to_day=None, limit=240, *, get_resident, ensure_tables, current_day, load_json, rows_to_dicts, active_branch, infer_relationship):
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    ensure_tables(conn)
    profile = conn.execute("SELECT * FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    goals = conn.execute("SELECT * FROM long_term_goals WHERE resident_id = ? ORDER BY status, deadline_day, id", (resident_id,)).fetchall()
    timeline = life_course_timeline(conn, resident_id, from_day, to_day, limit, active_branch=active_branch, load_json=load_json)
    episodes = life_course_episodes(timeline)
    for item in timeline:
        item["timeline_kind"] = life_course_kind(item); item["display_title"] = life_course_display_title(item); item["turning_summary"] = life_course_turning_summary(item)
    action_timeline = [item for item in timeline if item.get("timeline_kind") == "action"]
    memory_timeline = [item for item in timeline if item.get("timeline_kind") == "memory"]
    relationships = life_course_relationships(conn, resident_id, timeline, rows_to_dicts=rows_to_dicts, infer_relationship=infer_relationship)
    groups = life_course_groups(conn, resident_id, timeline, load_json=load_json, rows_to_dicts=rows_to_dicts)
    action_counts, locations = {}, set()
    for item in timeline:
        action = item.get("action")
        if action and action != "memory": action_counts[action] = action_counts.get(action, 0) + 1
        if item.get("location"): locations.add(item["location"])
    profile = dict(profile) if profile else {}
    important = [item for item in timeline if item.get("significance") == "turning_point" or (item.get("significance") == "important" and item.get("timeline_kind") == "action")]
    coverage = life_course_temporal_coverage(current_day(conn), life_course_latest_recorded_day(conn, resident_id, timeline=timeline, active_branch=active_branch), from_day, to_day)
    return {"analysis_version": "life-course-v2", "temporal_coverage": coverage, "resident": dict(resident), "current_state": {"location": resident["location"], "energy": profile.get("energy"), "time_budget": profile.get("time_budget"), "mood": profile.get("mood"), "current_task": profile.get("current_task")}, "initial_goal": resident["goal"], "goals": [dict(goal) for goal in goals], "timeline": timeline, "episodes": episodes, "action_timeline": action_timeline, "memory_timeline": memory_timeline, "turning_points": sorted(important, key=lambda item: (-int(item.get("turning_point_score") or 0), int(item.get("day") or 0)))[:12], "relationships": relationships, "groups": groups, "behavior_summary": {"event_count": len(timeline), "action_event_count": len(action_timeline), "memory_event_count": len(memory_timeline), "action_counts": action_counts, "unique_spaces": sorted(locations), "relationship_count": len(relationships), "active_group_count": sum(1 for group in groups if group.get("status") == "active")}, "research_boundaries": {"state_history_available": any(item.get("state_before") or item.get("state_after") for item in timeline if item.get("source") == "simulation_action_logs"), "relationship_history_available": any(item.get("history_available") for item in relationships), "group_membership_history_available": any(group.get("membership_history_available") for group in groups), "causal_links_available": False, "message": "当前版本展示事件证据和时序关联，不将时序关联表述为因果关系。"}, "evidence": [life_course_evidence("residents", resident_id), *[life_course_evidence("world_event_stream", item["id"]) for item in timeline if item.get("source") == "world_event_stream"]][:40]}


def life_course_latest_recorded_day(conn, resident_id, timeline=None, *, active_branch):
    if timeline is not None:
        days = [int(item["day"]) for item in timeline if item.get("day") is not None]
        return max(days) if days else None
    latest_days = []
    branch_key = active_branch(conn)
    table_filters = {"world_event_stream": ("resident_id = ? AND branch_key = ?", (resident_id, branch_key)), "simulation_action_logs": ("resident_id = ?", (resident_id,)), "memories": ("resident_id = ?", (resident_id,))}
    for table, (where, params) in table_filters.items():
        row = conn.execute(f"SELECT MAX(day) AS latest_day FROM {table} WHERE {where}", params).fetchone()
        if row and row["latest_day"] is not None:
            latest_days.append(int(row["latest_day"]))
    return max(latest_days) if latest_days else None


def lifecycle_overview(conn, resident_id, *, build_overview, **filters):
    return build_overview(conn, resident_id, **filters)


def lifecycle_events(conn, resident_id, *, build_overview, **filters):
    overview = build_overview(conn, resident_id, **filters)
    return {"analysis_version": overview["analysis_version"], "events": overview["timeline"], "research_boundaries": overview["research_boundaries"]}


def lifecycle_turning_points(conn, resident_id, *, build_overview, limit=12):
    overview = build_overview(conn, resident_id, limit=500)
    return {"analysis_version": overview["analysis_version"], "turning_points": overview["turning_points"][:min(max(limit, 1), 30)]}


def lifecycle_relationships(conn, resident_id, *, build_overview):
    overview = build_overview(conn, resident_id, limit=240)
    return {"analysis_version": overview["analysis_version"], "relationships": overview["relationships"], "history_available": overview["research_boundaries"]["relationship_history_available"]}


def lifecycle_groups(conn, resident_id, *, build_overview):
    overview = build_overview(conn, resident_id, limit=240)
    return {"analysis_version": overview["analysis_version"], "groups": overview["groups"], "membership_history_available": overview["research_boundaries"]["group_membership_history_available"]}
