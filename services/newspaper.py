"""Campus newspaper ranking and rendering rules."""

from contextlib import contextmanager
import os
import threading

from app.db import db_savepoint

_MODULE_NAME = __name__


def classify_candidate(event_type, action="", content="", payload=None, *, json_dumps):
    payload = payload if isinstance(payload, dict) else {}
    factual_payload = {key: value for key, value in payload.items() if key not in {"runtime_decision", "preconditions", "causal_settlement"}}
    text = f"{event_type} {action} {content} {json_dumps(factual_payload, ensure_ascii=False)}"
    if action in {"conflict", "late", "request_leave"} or any(word in text for word in ("冲突", "紧张", "请假", "迟到")): return "反常行为", 90
    if event_type in {"agent_tick_failed", "world_tick_failed", "real_weather_auto_sync_failed"} or payload.get("action_success") is False or payload.get("failure_code") or any(word in str(content or "") for word in ("异常", "故障", "中断", "失败")): return "突发异常", 100
    if event_type in {"social_interaction", "relationship_change"} or payload.get("social_effect") or any(word in text for word in ("关系", "信任", "合作", "好感", "竞争")): return "关系风向", 86
    if action in {"collaborate", "create_group", "join_group", "club_activity"} or any(word in text for word in ("小组", "社团", "协作", "动员", "扩散")): return "群体现象", 82
    if event_type in {"crowd_transmission", "organization_mobilization", "group_diffusion"}: return "群体现象", 80
    if action in {"observe", "reflect"} or any(word in text for word in ("发现", "观察", "反思", "想法")): return "内心发现", 70
    if any(word in text for word in ("天气", "人流", "拥挤", "食堂", "图书馆", "空间", "资源", "服务")): return "校园环境", 60
    return "校园环境", 50


def headline(category, location, name):
    location = location or "校园"
    return {"突发异常": f"{location}出现需要关注的异常信号", "反常行为": f"{name}的反常行动引发关注", "关系风向": "校园关系网络出现新动向", "群体现象": f"{location}涌现出新的集体动态", "内心发现": f"{name}记录到一条内心发现", "校园环境": f"{location}出现新的环境变化"}.get(category, f"{location}发布最新校园动态")


def fallback_content(candidate):
    category, name, role, location = candidate["category"], candidate["name"], candidate["role"] or "校园居民", candidate["location"] or "校园"
    content = str(candidate["content"] or "校园出现一条新的运行记录。")
    templates = {"关系风向": f"{role}{name}在{location}经历了一次不同于往常的互动。{content[:110]} 这次接触是否会改变双方后续的信任与合作，成为编辑部继续追踪的线索。", "突发异常": f"{location}出现异常动向，{role}{name}正处在事件中心。{content[:110]} 当前影响仍在发展，相关行动与环境变化需要继续观察。", "群体现象": f"{role}{name}在{location}参与的行动开始吸引更多人响应。{content[:110]} 原本的个人选择正在形成集体趋势，可能改变接下来的空间热度和校园注意力。", "反常行为": f"{role}{name}今天在{location}偏离了惯常行动轨迹。{content[:110]} 这究竟是临时选择还是持续变化，仍需结合后续行为判断。", "内心发现": f"{role}{name}在{location}停下来重新审视自己的选择。{content[:110]} 这份想法尚未转化为行动，却可能影响其下一步决定。", "校园环境": f"{location}的运行状态发生变化。{content[:120]} 身处其中的{role}{name}首先受到影响，其他居民的行动和空间选择也可能随之调整。"}
    return templates.get(category, f"{role}{name}在{location}完成了一次不同寻常的行动。{content[:120]} 这件事为观察后续变化留下了新的线索。")


def publish_agent_news(conn, day, results, *, ensure_system, choice, summarize_action, ask_llm):
    ensure_system(conn); published = []
    for item in choice(results, min(4, len(results))):
        resident = conn.execute("SELECT id, name, role, location, goal FROM residents WHERE id = ?", (item.get("resident_id"),)).fetchone()
        if not resident: continue
        summary = summarize_action(item.get("execution", {}))
        prompt = f"""你是《World2 世界时报》的校园记者。根据以下事实写一则 90 到 150 字、具有现场感的中文校园快讯：
消息来源：{resident['name']}（{resident['role']}）
地点：{resident['location']}
事实：{summary}

使用第三人称和客观新闻口吻，先写具体行动，再写变化或影响。不要输出标题、JSON、Markdown 或解释，只输出新闻正文。"""
        fallback = f"{resident['role']}{resident['name']}当天来到{resident['location']}，完成了与自身目标相关的一项行动。现场留下的变化已经进入后续观察，编辑部将继续追踪它是否影响其他居民的选择。"
        try: content = ask_llm(prompt).strip()
        except Exception: content = fallback
        if not content or content.startswith(("{", "[")): content = fallback
        if any(word in content for word in ("维修", "检修", "施工")): headline = f"{resident['location']}启动设施维护"
        elif any(word in content for word in ("食堂", "套餐", "供餐", "补货")): headline = "校园餐饮服务推出新安排"
        elif any(word in content for word in ("实验", "项目", "科研", "代码")): headline = "校园教学科研项目取得新进展"
        elif any(word in content for word in ("考试", "复习", "压力")): headline = "考试周校园保障措施持续推进"
        else: headline = f"{resident['location']}发布最新校园动态"
        cursor = conn.execute("INSERT OR IGNORE INTO agent_news_posts (day, resident_id, source_slot, news_value, headline, content) VALUES (?, ?, ?, ?, ?, ?)", (day, resident["id"], "日终补充", 55, headline, content[:500]))
        if cursor.rowcount: published.append({"resident_id": resident["id"], "headline": headline})
    return published


def write_daily_diaries(conn, day, results=None, replace_existing=False, *, ask_llm, add_memory):
    agents = conn.execute("SELECT id, name, role, personality, location, goal FROM residents ORDER BY id").fetchall(); by_agent = {item.get("resident_id"): item for item in (results or [])}; created = []
    action_text = {"chat": "和校园里的其他人交流", "move": "前往新的校园空间", "buy_sell": "完成了一次交易", "observe": "观察校园环境", "submit_policy": "参与校园事务讨论"}
    for agent in agents:
        exists = conn.execute("SELECT 1 FROM memories WHERE resident_id = ? AND day = ? AND content LIKE ?", (agent["id"], day, f"日记·第{day}天：%")).fetchone()
        if exists and not replace_existing: continue
        if exists: conn.execute("DELETE FROM memories WHERE resident_id = ? AND day = ? AND content LIKE ?", (agent["id"], day, f"日记·第{day}天：%"))
        item = by_agent.get(agent["id"], {}); execution = item.get("execution", {}) if isinstance(item, dict) else {}; decision = item.get("decision", {}).get("decision", {}) if isinstance(item, dict) else {}
        action = execution.get("action") or decision.get("action"); activity = action_text.get(action, "完成自己的校园安排"); reason = str(decision.get("reason") or "")[:90].strip()
        memories = conn.execute("SELECT content FROM memories WHERE resident_id = ? AND day = ? AND content NOT LIKE ? ORDER BY id DESC LIMIT 4", (agent["id"], day, f"日记·第{day}天：%")).fetchall(); memory_text = "；".join(row["content"][:160] for row in memories)
        prompt = f"""你是校园封闭世界中的 Agent“{agent['name']}”。你的身份：{agent['role']}；性格：{agent['personality']}；长期目标：{agent['goal']}；当前位置：{agent['location']}。今天你实际完成的行动：{activity}。行动理由：{reason or '根据自己的状态和环境自主判断'}。个人经历：{memory_text or '暂无额外记录'}。请以第一人称写 70 到 130 字中文个人日记，只输出日记正文。"""
        fallback = f"今天我在{agent['location']}{activity}。这次经历让我更清楚地看到校园的变化，也提醒我继续朝“{agent['goal']}”努力。"
        try: diary_text = ask_llm(prompt).strip()
        except Exception: diary_text = fallback
        if not diary_text or diary_text.startswith(("{", "[")): diary_text = fallback
        add_memory(conn, agent["id"], day, f"日记·第{day}天：{diary_text[:500]}", importance=5, memory_type="episodic", tags=["日记", agent["location"], activity], source="diary"); created.append(agent["id"])
    return created


def audit_candidate_evidence(conn, candidate):
    """Audit evidence for an news candidate before publication to prevent hallucination."""
    source_event_id = candidate.get("source_event_id")
    if source_event_id is not None:
        ev = conn.execute(
            "SELECT id FROM world_event_stream WHERE id = ?", (source_event_id,)
        ).fetchone()
        if not ev:
            return {"decision": "reject", "reason": "missing_source_event"}

    if candidate.get("category") == "群体现象" and candidate.get("score", 0) < 50:
        return {"decision": "hold", "reason": "low_confidence_pattern"}

    return {"decision": "publish", "confidence": candidate.get("score", 50)}


def collect_candidates(conn, day, source_slot, limit=60, *, active_branch, load_json, classify):
    branch_key = active_branch(conn); existing = {int(row["resident_id"]) for row in conn.execute("SELECT resident_id FROM agent_news_posts WHERE day = ?", (day,)).fetchall()}; candidates = []
    rows = conn.execute("""SELECT e.id, e.event_type, e.resident_id, e.location, e.title, e.content, e.payload, r.name, r.role FROM world_event_stream e LEFT JOIN residents r ON r.id = e.resident_id WHERE e.day = ? AND e.branch_key = ? AND e.resident_id IS NOT NULL AND e.event_type NOT IN ('world_tick_started', 'world_tick_complete', 'campus_news_published', 'campus_news_skipped', 'observer_session', 'observer_model_detail') ORDER BY e.id DESC LIMIT ?""", (day, branch_key, limit)).fetchall()
    for row in rows:
        resident_id = int(row["resident_id"])
        if resident_id in existing: continue
        payload = load_json(row["payload"], {}); action = payload.get("action") or payload.get("runtime_decision", {}).get("action") or ""; category, score = classify(row["event_type"], action, row["content"], payload)
        if row["event_type"] == "agent_tick" and source_slot and row["content"] and row["location"]: score += 5
        cand = {"resident_id": resident_id, "name": row["name"] or f"Agent {resident_id}", "role": row["role"] or "校园居民", "location": row["location"] or "校园", "event_type": row["event_type"], "title": row["title"], "content": row["content"], "payload": payload, "action": action, "category": category, "score": score, "source_event_id": row["id"]}
        if audit_candidate_evidence(conn, cand)["decision"] == "publish":
            candidates.append(cand)
    relationship_rows = conn.execute("""SELECT c.id, c.from_resident_id, c.to_resident_id, c.interaction, c.reason, c.affinity_before, c.affinity_after, c.trust_before, c.trust_after, c.cooperation_before, c.cooperation_after, c.conflict_before, c.conflict_after, r.name, r.role, r.location, target.name AS target_name FROM relationship_change_events c JOIN residents r ON r.id = c.from_resident_id JOIN residents target ON target.id = c.to_resident_id WHERE c.day = ? ORDER BY c.id DESC LIMIT 40""", (day,)).fetchall()
    for row in relationship_rows:
        resident_id = int(row["from_resident_id"])
        if resident_id in existing: continue
        delta = abs(int(row["trust_after"] or 0) - int(row["trust_before"] or 0)) + abs(int(row["cooperation_after"] or 0) - int(row["cooperation_before"] or 0)) + abs(int(row["conflict_after"] or 0) - int(row["conflict_before"] or 0))
        content = f"{row['name']}与{row['target_name']}的关系发生变化：{row['reason'] or row['interaction']}。"
        cand = {"resident_id": resident_id, "name": row["name"], "role": row["role"], "location": row["location"] or "校园", "event_type": "relationship_change", "title": "关系变化被记录", "content": content, "payload": {"relationship_change_event_id": row["id"], "target_name": row["target_name"]}, "action": row["interaction"], "category": "关系风向", "score": 86 + min(delta, 20), "source_event_id": None}
        if audit_candidate_evidence(conn, cand)["decision"] == "publish":
            candidates.append(cand)

    # Collect R2.2 group pattern candidates if present
    if conn.execute("PRAGMA table_info(group_pattern_candidates)").fetchall():
        pattern_rows = conn.execute(
            """
            SELECT * FROM group_pattern_candidates
            WHERE status IN ('candidate', 'confirmed') AND branch_key = ?
            ORDER BY candidate_score DESC LIMIT 10
            """,
            (branch_key,),
        ).fetchall()
        for prow in pattern_rows:
            # Pick a resident from the location if possible
            r_match = conn.execute(
                "SELECT id, name, role FROM residents WHERE location = ? LIMIT 1",
                (prow["location"],),
            ).fetchone() or conn.execute("SELECT id, name, role FROM residents LIMIT 1").fetchone()
            if r_match and int(r_match["id"]) not in existing:
                cand = {
                    "resident_id": int(r_match["id"]),
                    "name": r_match["name"],
                    "role": r_match["role"],
                    "location": prow["location"],
                    "event_type": "group_pattern",
                    "title": prow["title"],
                    "content": f"{prow['title']}：参与人数 {prow['participant_count']}，拥挤偏离度 {prow['baseline_deviation']}。",
                    "payload": {"pattern_id": prow["id"], "candidate_score": prow["candidate_score"]},
                    "action": "aggregate",
                    "category": "群体现象",
                    "score": round(80 + prow["candidate_score"] * 15),
                    "source_event_id": None,
                }
                if audit_candidate_evidence(conn, cand)["decision"] == "publish":
                    candidates.append(cand)

    candidates.sort(key=lambda item: (-item["score"], item["resident_id"]))
    return candidates


# Runtime dependencies are supplied by the application composition root.
def configure_runtime(**bindings):
    module_globals = globals()
    for name, value in bindings.items():
        if name.startswith("__"):
            continue
        current = module_globals.get(name)
        if callable(current) and getattr(current, "__module__", None) == _MODULE_NAME:
            continue
        module_globals[name] = value
    module_globals["__name__"] = _MODULE_NAME


def maybe_publish_campus_news_from_world_window(conn, world_time, tick_id=None, day=None):
    """Publish campus news from the autonomous runtime, prioritizing unusual emergent material."""
    ensure_agent_news_system(conn)
    ensure_world_runtime_tables(conn)
    day = day or get_current_day(conn)
    branch_key = active_world_branch_key(conn)
    window_start, window_end, source_slot = previous_completed_world_window(world_time)
    source_window_key = f"{window_start.date().isoformat()} {source_slot}"
    existing = conn.execute(
        """
        SELECT id FROM world_event_stream
        WHERE event_type = 'campus_news_published'
          AND branch_key = ?
          AND payload LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (branch_key, f'%"source_window_key": "{source_window_key}"%'),
    ).fetchone()
    if existing:
        return {"skipped": True, "reason": "already_published", "source_window_key": source_window_key}

    candidates = []
    seen_residents = set()
    for candidate in collect_campus_news_candidates(conn, day, source_slot):
        resident_id = int(candidate["resident_id"])
        if resident_id in seen_residents:
            continue
        seen_residents.add(resident_id)
        candidates.append(candidate)
        if len(candidates) >= 3:
            break

    if not candidates:
        event = append_world_event(
            conn,
            "campus_news_skipped",
            "校园新闻本窗口未发布",
            f"{source_slot} 暂无新的可发布发现，校园日报继续等待 runtime 事件。",
            tick_id=tick_id,
            payload={
                "source_window_key": source_window_key,
                "source_window_start": window_start.isoformat(),
                "source_window_end": window_end.isoformat(),
                "source_slot": source_slot,
                "reason": "no_new_agent_material",
                "retryable": True,
            },
            day=day,
            slot=source_slot,
        )
        return {"skipped": True, "reason": "no_new_agent_material", "event_id": event["id"], "source_window_key": source_window_key}

    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    published = []
    failed = []
    for candidate in candidates:
        action = candidate.get("action") or "observe"
        payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
        goal = payload.get("goal") or payload.get("runtime_decision", {}).get("goal") or "推进校园生活"
        source_text = f"{candidate['title']}：{candidate['content']}"
        headline = campus_news_headline(candidate["category"], candidate["location"], candidate["name"])
        content = None
        prompt = f"""
你是《World2 世界时报》的运行时观察记者。请根据校园平行世界刚出现的事实材料，写一则 90 到 150 字、具有现场感和可读性的中文校园快讯。

时间窗口：{source_slot}
新闻类型：{candidate['category']}
人物：{candidate['name']}（{candidate['role']}）
地点：{candidate['location'] or '校园'}
动作类型：{action}
行动目标：{goal}
事实材料：{source_text}

要求：
- 使用第三人称、客观新闻口吻。
- 第一两句直接写清人物在什么地点做了什么，不要用“系统捕捉到”“值得记录”“发布最新进展”等套话起笔。
- 随后写出行动造成的具体变化、反应或悬念；结尾说明为什么值得继续关注。
- 句式自然，有长短变化，避免连续重复人物全称、地点和“校园”。
- 优先呈现突发异常、关系风向、反常行为、群体现象、内心发现或校园环境变化。
- 只能基于事实材料写，不要编造材料中没有的人物关系或因果。
- 不要写标题、JSON、Markdown、口号或解释，只输出新闻正文。
"""
        model_configured = is_llm_configured()
        if consume_auto_model_budget(conn, "campus_news", resident_id=candidate["resident_id"]):
            try:
                raw = ask_llm(prompt)
                content = re.sub(r"\s+", " ", raw).strip().strip('"“”')
                if not content or content.startswith(("{", "[")):
                    raise ValueError("invalid campus news content")
                log_model_call(
                    conn,
                    "campus_news",
                    status="success",
                    resident_id=candidate["resident_id"],
                    related_event_id=candidate.get("source_event_id"),
                    model_name=model_name,
                    prompt_version="campus-news-runtime-v2",
                    input_tokens=max(1, len(prompt) // 4),
                    output_tokens=max(1, len(raw) // 4),
                )
            except Exception as exc:
                logger.warning("Campus news generation failed for resident %s", candidate["resident_id"], exc_info=True)
                failed.append({"resident_id": candidate["resident_id"], "reason": type(exc).__name__})
                log_model_call(
                    conn,
                    "campus_news",
                    status=f"failed:{type(exc).__name__}",
                    resident_id=candidate["resident_id"],
                    related_event_id=candidate.get("source_event_id"),
                    model_name=model_name,
                    prompt_version="campus-news-runtime-v2",
                )
        elif model_configured:
            failed.append({"resident_id": candidate["resident_id"], "reason": "budget_exhausted"})
        if not content:
            content = fallback_campus_news_content(candidate)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO agent_news_posts
            (day, resident_id, source_slot, source_event_id, news_value, headline, content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                candidate["resident_id"],
                source_slot,
                candidate.get("source_event_id"),
                candidate["score"],
                headline,
                content[:500],
            ),
        )
        if cursor.rowcount:
            published.append(
                {
                    "resident_id": candidate["resident_id"],
                    "headline": headline,
                    "category": candidate["category"],
                    "source_event_id": candidate.get("source_event_id"),
                    "score": candidate["score"],
                }
            )

    if published:
        title = "校园新闻已自动发布"
        labels = "、".join(sorted({item["category"] for item in published}))
        content = f"{source_slot} 窗口从 runtime 事件中发布 {len(published)} 条校园快讯，类型包括：{labels}。"
        event_type = "campus_news_published"
    else:
        title = "校园新闻生成未完成"
        content = f"{source_slot} 窗口没有成功生成校园快讯，世界运行继续。"
        event_type = "campus_news_skipped"
    event = append_world_event(
        conn,
        event_type,
        title,
        content,
        tick_id=tick_id,
        payload={
            "source_window_key": source_window_key,
            "source_window_start": window_start.isoformat(),
            "source_window_end": window_end.isoformat(),
            "source_slot": source_slot,
            "published": published,
            "failed": failed,
        },
        day=day,
        slot=source_slot,
    )
    return {
        "skipped": not bool(published),
        "published_count": len(published),
        "failed_count": len(failed),
        "event_id": event["id"],
        "source_window_key": source_window_key,
    }


def select_world_tick_agents(conn, runtime):
    movement_join = ""
    movement_column = "'idle' AS movement_status"
    body_join = ""
    hunger_column = "0 AS hunger"
    if conn.execute("PRAGMA table_info(agent_body_states)").fetchall():
        body_join = "LEFT JOIN agent_body_states body ON body.resident_id = r.id"
        hunger_column = "COALESCE(body.hunger, 0) AS hunger"
    if spatial_runtime_available(conn):
        movement_join = (
            "LEFT JOIN agent_spatial_states spatial "
            "ON spatial.resident_id = r.id"
        )
        movement_column = "COALESCE(spatial.movement_status, 'idle') AS movement_status"
    lifecycle_join = ""
    lifecycle_filter = ""
    if population_runtime_available(conn):
        lifecycle_join = (
            "LEFT JOIN population_profiles lifecycle "
            "ON lifecycle.resident_id = r.id"
        )
        lifecycle_filter = (
            "WHERE lifecycle.resident_id IS NULL "
            "OR lifecycle.lifecycle_status = 'active'"
        )
    agents = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT r.id, r.name, r.role, r.personality, r.goal, r.money,
                   r.location, p.strategy, COALESCE(p.energy, 0) AS energy,
                   {movement_column}, {hunger_column}
            FROM residents r
            LEFT JOIN agent_profiles p ON p.resident_id = r.id
            {movement_join}
            {body_join}
            {lifecycle_join}
            {lifecycle_filter}
            ORDER BY r.id
            """
        ).fetchall()
    ]
    if not agents:
        return [], int(runtime.get("current_agent_cursor", 0) or 0), set()
    eligible_agents = [
        agent
        for agent in agents
        if agent["movement_status"] not in ACTIVE_MOVEMENT_STATUSES
    ]
    if not eligible_agents:
        return [], int(runtime.get("current_agent_cursor", 0) or 0), set()
    focused_agent_ids, _ = get_recent_observer_focus(conn)
    focused_set = set(focused_agent_ids)
    agent_by_id = {agent["id"]: agent for agent in eligible_agents}
    cursor = int(runtime.get("current_agent_cursor", 0) or 0) % len(eligible_agents)
    per_tick = bounded_agent_batch_size(
        runtime.get("agents_per_tick", 3),
        len(eligible_agents),
        seed=(
            runtime.get("last_tick_completed_at")
            or runtime.get("world_time")
            or runtime.get("current_agent_cursor", 0)
        ),
    )
    # A meal is planned from the prevention band (60), not only after an
    # Agent has reached the emergency band (90).  This keeps the bounded
    # scheduler fair while ensuring a whole cohort cannot slowly become
    # hungry simply because their round-robin turn comes too late.
    recovery_agents = sorted(
        (agent for agent in eligible_agents if float(agent.get("hunger") or 0) >= 60),
        key=lambda agent: (-float(agent.get("hunger") or 0), int(agent["id"])),
    )
    target_count = max(per_tick, min(len(recovery_agents), 8))
    # Recovery needs outrank normal round-robin selection, but must not pin
    # the same lowest-ID residents on every tick.  Rotate the priority list
    # with the persistent cursor so every hungry Agent gets a recovery turn.
    critical_cursor = cursor % len(recovery_agents) if recovery_agents else 0
    selected = [
        recovery_agents[(critical_cursor + offset) % len(recovery_agents)]
        for offset in range(min(target_count, len(recovery_agents)))
    ]
    selected.extend(
        agent_by_id[agent_id]
        for agent_id in focused_agent_ids
        if agent_id in agent_by_id and agent_id not in {item["id"] for item in selected}
    )
    # Advance even when critical Agents already fill the complete batch.  The
    # former implementation advanced only in the ordinary selection loop,
    # so a large hungry cohort could select Agent 1 (and the same first 8) forever.
    next_cursor = cursor + len(selected)
    while len(selected) < target_count and len(selected) < len(eligible_agents):
        candidate = eligible_agents[next_cursor % len(eligible_agents)]
        if candidate["id"] not in {item["id"] for item in selected}:
            selected.append(candidate)
        next_cursor += 1
    return selected[:target_count], next_cursor % len(eligible_agents), focused_set


def maybe_generate_group_behavior_event(conn, world_time, tick_id, day, slot):
    ensure_world_runtime_tables(conn)
    branch_key = active_world_branch_key(conn)
    latest = conn.execute(
        """
        SELECT created_at FROM world_event_stream
        WHERE event_type IN ('group_diffusion', 'crowd_transmission', 'organization_mobilization')
          AND branch_key = ?
        ORDER BY id DESC LIMIT 1
        """,
        (branch_key,),
    ).fetchone()
    latest_at = parse_world_datetime(latest["created_at"]) if latest else None
    if latest_at and (world_time - latest_at).total_seconds() < 1800:
        return {"skipped": True, "reason": "interval_not_elapsed"}

    env = dict(get_campus_environment(conn, day))
    counts = {
        row["location"]: int(row["count"])
        for row in conn.execute("SELECT location, COUNT(*) AS count FROM residents GROUP BY location").fetchall()
    }
    hot_location, hot_count = max(counts.items(), key=lambda item: item[1]) if counts else ("校园", 0)
    campus_flow = int(env.get("campus_flow") or 0)
    activity_heat = int(env.get("activity_heat") or 0)
    event = None
    if hot_count >= 4 and campus_flow >= 65:
        event = append_world_event(
            conn,
            "crowd_transmission",
            "空间拥堵正在传导",
            f"{hot_location} 聚集了 {hot_count} 位 Agent，拥挤感开始影响周边行动选择。",
            tick_id=tick_id,
            location=hot_location,
            payload={"location": hot_location, "agent_count": hot_count, "campus_flow": campus_flow},
            day=day,
            slot=slot,
        )
    elif activity_heat >= 70 and random.random() < 0.35:
        event = append_world_event(
            conn,
            "organization_mobilization",
            "组织活动正在动员",
            "校园活动热度较高，部分社团和组织开始吸引周边 Agent 关注。",
            tick_id=tick_id,
            payload={"activity_heat": activity_heat, "mechanism": "activity_heat_threshold"},
            day=day,
            slot=slot,
        )
    else:
        recent_info = conn.execute(
            """
            SELECT title, category FROM external_information
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if recent_info and random.random() < 0.25:
            event = append_world_event(
                conn,
                "group_diffusion",
                "外部信息沿关系扩散",
                f"关于「{recent_info['title']}」的讨论开始在少数相关 Agent 之间扩散。",
                tick_id=tick_id,
                payload={"information_title": recent_info["title"], "category": recent_info["category"], "mechanism": "relationship_and_place_diffusion"},
                day=day,
                slot=slot,
            )
    if not event:
        return {"skipped": True, "reason": "no_group_trigger"}
    return {"skipped": False, "event_id": event["id"], "event_type": event["event_type"]}


def sync_world_time_environment(conn, world_time):
    day = get_current_day(conn)
    values = dict(get_campus_environment(conn, day))
    values = derive_environment_from_real_time(values, world_time)
    save_environment_values(conn, day, values)
    return get_campus_environment(conn, day)


def sync_real_weather_into_world(conn, event_type="real_weather_manual_sync", tick_id=None, day=None, slot=None, world_time=None):
    from app.spatial.service import update_spatial_weather_factors
    day = day or get_current_day(conn)
    current_env = get_campus_environment(conn, day)
    weather_data = fetch_real_weather()
    values = dict(current_env)
    values.update({key: weather_data[key] for key in ["weather", "temperature", "rainfall", "weather_source", "weather_observed_at"]})
    if "raw" in weather_data and isinstance(weather_data["raw"], dict):
        raw = weather_data["raw"]
        if "wind_speed_10m" in raw and raw["wind_speed_10m"] is not None:
            values["wind_speed_10m"] = float(raw["wind_speed_10m"])
        if "relative_humidity_2m" in raw and raw["relative_humidity_2m"] is not None:
            values["relative_humidity_2m"] = float(raw["relative_humidity_2m"])
    values = derive_environment_from_real_time(values, world_time)
    save_environment_values(conn, day, values)
    update_spatial_weather_factors(conn, values, day=day, add_event_func=add_event)
    content = f"接入真实天气：{values['weather']}，{values['temperature']}℃，降雨指数 {values['rainfall']}。"
    add_event(conn, day, "real_weather_sync", content)
    event = append_world_event(
        conn,
        event_type,
        "真实天气自动同步" if event_type == "real_weather_auto_sync" else "真实天气同步",
        content,
        tick_id=tick_id,
        payload={
            "weather": values["weather"],
            "temperature": values["temperature"],
            "rainfall": values["rainfall"],
            "weather_source": values.get("weather_source", ""),
            "weather_observed_at": values.get("weather_observed_at", ""),
        },
        day=day,
        slot=slot,
    )
    return {"environment": get_campus_environment(conn, day), "raw": weather_data.get("raw", {}), "event": event}


def maybe_auto_sync_real_weather(conn, world_time, tick_id=None, day=None, slot=None):
    ensure_world_runtime_tables(conn)
    latest = conn.execute(
        """
        SELECT created_at FROM world_event_stream
        WHERE event_type IN ('real_weather_auto_sync', 'real_weather_auto_sync_failed')
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    latest_at = parse_world_datetime(latest["created_at"]) if latest else None
    if latest_at and (world_time - latest_at).total_seconds() < WORLD_WEATHER_SYNC_INTERVAL_SECONDS:
        return {"skipped": True, "reason": "interval_not_elapsed", "last_synced_at": latest_at.isoformat()}
    try:
        with db_savepoint(conn, "weather_auto_sync"):
            result = sync_real_weather_into_world(conn, event_type="real_weather_auto_sync", tick_id=tick_id, day=day, slot=slot, world_time=world_time)
        env = result["environment"]
        return {
            "skipped": False,
            "weather": env.get("weather"),
            "temperature": env.get("temperature"),
            "rainfall": env.get("rainfall"),
            "weather_source": env.get("weather_source"),
            "weather_observed_at": env.get("weather_observed_at"),
            "event_id": result["event"].get("id"),
        }
    except Exception as exc:
        logger.warning("Auto real weather sync failed", exc_info=True)
        event = append_world_event(
            conn,
            "real_weather_auto_sync_failed",
            "真实天气自动同步失败",
            f"真实天气源暂时不可用：{type(exc).__name__}",
            tick_id=tick_id,
            payload={"error": str(exc)[:240]},
            day=day,
            slot=slot,
        )
        return {"skipped": False, "failed": True, "error": str(exc), "event_id": event["id"]}


@contextmanager
def world_tick_database_lease():
    """Hold a cross-process tick lease.

    Session-level advisory locks are incompatible with PgBouncer transaction
    pooling (Supabase port 6543).  Fall back to the in-process WORLD_TICK_LOCK
    which is sufficient for single-instance deployments.
    """
    # Cross-process lease skipped: PgBouncer transaction pooling does not
    # preserve session state across transactions, so pg_advisory_lock cannot
    # work reliably.
    yield True
