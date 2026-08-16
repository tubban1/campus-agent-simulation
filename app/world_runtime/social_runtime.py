_MODULE_NAME = __name__


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


def _initialize_social_system_tables(conn, *, allow_ddl=False):
    ensure_agent_profile_table(conn, allow_ddl=allow_ddl)
    if allow_ddl:
        from app.models import SCHEMA_SQL
        conn.executescript(SCHEMA_SQL)
        conn.executescript(SOCIAL_SYSTEM_SQL)
        conn.executescript(BEHAVIOR_SYSTEM_SQL)
    ensure_table_columns(
        conn,
        "relationship_dynamics",
        RELATIONSHIP_DYNAMIC_COLUMNS,
        allow_ddl=allow_ddl,
    )
    ensure_table_columns(
        conn,
        "long_term_goals",
        LONG_TERM_GOAL_COLUMNS,
        allow_ddl=allow_ddl,
    )
    ensure_table_columns(
        conn,
        "simulation_action_logs",
        {
            "tick_id": "INTEGER",
            "state_before": "TEXT NOT NULL DEFAULT '{}'",
            "state_after": "TEXT NOT NULL DEFAULT '{}'",
        },
        allow_ddl=allow_ddl,
    )
    if allow_ddl:
        relationship_id_type = "SERIAL PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS relationship_change_events (
                id {relationship_id_type},
                day INTEGER NOT NULL DEFAULT 1,
                tick_id INTEGER,
                event_id INTEGER,
                from_resident_id INTEGER NOT NULL,
                to_resident_id INTEGER NOT NULL,
                interaction TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                affinity_before INTEGER NOT NULL DEFAULT 50,
                affinity_after INTEGER NOT NULL DEFAULT 50,
                trust_before INTEGER NOT NULL DEFAULT 50,
                trust_after INTEGER NOT NULL DEFAULT 50,
                cooperation_before INTEGER NOT NULL DEFAULT 50,
                cooperation_after INTEGER NOT NULL DEFAULT 50,
                competition_before INTEGER NOT NULL DEFAULT 0,
                competition_after INTEGER NOT NULL DEFAULT 0,
                conflict_before INTEGER NOT NULL DEFAULT 0,
                conflict_after INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        membership_id_type = "SERIAL PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS group_membership_events (
                id {membership_id_type}, day INTEGER NOT NULL DEFAULT 1, group_id INTEGER NOT NULL,
                resident_id INTEGER NOT NULL, action TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
                member_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    ensure_table_columns(conn, "relationship_change_events", {})
    ensure_table_columns(conn, "group_membership_events", {})
    normalize_agent_hierarchy(conn)
    seed_long_term_goals(conn)
    seed_multiscale_goals(conn)
    seed_campus_organizations(conn)


def evolve_relationship(
    conn,
    from_id,
    to_id,
    interaction,
    note,
    trust_delta=0,
    cooperation_delta=0,
    tension_delta=0,
    affinity_delta=None,
    competition_delta=0,
    conflict_delta=None,
    tick_id=None,
    event_id=None,
):
    current = get_relationship_dynamics(conn, from_id, to_id)
    if affinity_delta is None:
        affinity_delta = round((trust_delta + cooperation_delta - tension_delta) / 3)
    if conflict_delta is None:
        conflict_delta = tension_delta
    affinity = clamp(int(current["affinity"]) + affinity_delta)
    trust = clamp(int(current["trust"]) + trust_delta)
    cooperation = clamp(int(current["cooperation"]) + cooperation_delta)
    competition = clamp(int(current["competition"]) + competition_delta)
    conflict = clamp(int(current["conflict"]) + conflict_delta)
    tension = clamp(int(current["tension"]) + tension_delta)
    relationship_delta = round((affinity_delta + trust_delta + cooperation_delta - conflict_delta) / 4)
    relationship_score = change_relationship(conn, from_id, to_id, relationship_delta, note)
    conn.execute(
        """
        UPDATE relationship_dynamics
        SET affinity = ?, trust = ?, cooperation = ?, competition = ?, conflict = ?, tension = ?,
            interaction_count = interaction_count + 1, last_day = ?
        WHERE from_resident_id = ? AND to_resident_id = ?
        """,
        (affinity, trust, cooperation, competition, conflict, tension, get_current_day(conn), from_id, to_id),
    )
    change_cursor = conn.execute(
        """
        INSERT INTO relationship_change_events
        (day, tick_id, event_id, from_resident_id, to_resident_id, interaction, reason,
         affinity_before, affinity_after, trust_before, trust_after,
         cooperation_before, cooperation_after, competition_before, competition_after,
         conflict_before, conflict_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            get_current_day(conn), tick_id, event_id, from_id, to_id, interaction, note or "",
            int(current["affinity"]), affinity, int(current["trust"]), trust,
            int(current["cooperation"]), cooperation, int(current["competition"]), competition,
            int(current["conflict"]), conflict,
        ),
    )
    relationship_change_event_id = getattr(change_cursor, "lastrowid", None)
    append_social_interaction_event(
        conn,
        actor_resident_id=from_id,
        target_resident_id=to_id,
        interaction_type=interaction,
        summary=note or "",
        tick_id=tick_id,
        world_event_id=event_id,
        relationship_change_event_id=relationship_change_event_id,
        intensity=max(abs(affinity_delta), abs(trust_delta), abs(cooperation_delta), abs(competition_delta), abs(conflict_delta), 1) * 10,
        valence=clamp(affinity_delta + trust_delta + cooperation_delta - conflict_delta, -100, 100),
        evidence={
            "relationship_delta": relationship_delta,
            "affinity_before": int(current["affinity"]),
            "affinity_after": affinity,
            "trust_before": int(current["trust"]),
            "trust_after": trust,
            "cooperation_before": int(current["cooperation"]),
            "cooperation_after": cooperation,
            "competition_before": int(current["competition"]),
            "competition_after": competition,
            "conflict_before": int(current["conflict"]),
            "conflict_after": conflict,
        },
    )
    record_social_relation_interpretation(conn, from_id, to_id, tick_id=tick_id)
    return {
        "interaction": interaction,
        "affinity": affinity,
        "trust": trust,
        "cooperation": cooperation,
        "competition": competition,
        "conflict": conflict,
        "tension": tension,
        "relationship_score": relationship_score,
    }


def infer_emergent_relationship(conn, from_id, to_id, dynamics=None, score=None, history_rows=None):
    """Interpret an edge from accumulated evidence without declaring a fixed relationship type."""
    dynamics = dynamics or get_relationship_dynamics(conn, from_id, to_id)
    affinity = int(dynamics.get("affinity") or 50)
    trust = int(dynamics.get("trust") or 50)
    cooperation = int(dynamics.get("cooperation") or 50)
    competition = int(dynamics.get("competition") or 0)
    conflict = int(dynamics.get("conflict") or 0)
    tension = int(dynamics.get("tension") or 0)
    interaction_count = int(dynamics.get("interaction_count") or 0)
    score = int(score if score is not None else get_relationship_score(conn, from_id, to_id))
    if history_rows is None:
        history_rows = conn.execute(
            """
            SELECT interaction, reason, affinity_before, affinity_after, trust_before, trust_after,
                   cooperation_before, cooperation_after, competition_before, competition_after,
                   conflict_before, conflict_after, day, created_at
            FROM relationship_change_events
            WHERE from_resident_id = ? AND to_resident_id = ?
            ORDER BY id DESC
            LIMIT 12
            """,
            (from_id, to_id),
        ).fetchall()
    interaction_counts = {}
    evidence = []
    for row in history_rows:
        interaction = row["interaction"] or "interaction"
        interaction_counts[interaction] = interaction_counts.get(interaction, 0) + 1
        if len(evidence) < 4:
            reason = row["reason"] or interaction
            evidence.append(f"第{row['day']}天：{reason}")

    candidates = []

    def add_candidate(label, weight, rationale):
        weight = max(0, min(100, int(round(weight))))
        if weight > 0:
            candidates.append({"label": label, "confidence": weight, "rationale": rationale})

    add_candidate("弱联系/待观察", 65 - min(interaction_count * 9, 45), "互动证据还少，关系解释应保持开放")
    add_candidate("熟人关系", 34 + interaction_count * 4 + max(0, score - 45) * 0.5, "多次接触形成基本熟悉度")
    add_candidate("可信关系", trust * 0.75 + interaction_count * 2 - conflict * 0.25, "信任值和稳定互动共同支撑")
    add_candidate("合作伙伴", cooperation * 0.8 + interaction_counts.get("collaborate", 0) * 8 + interaction_counts.get("collaboration", 0) * 8, "协作行为和合作维度较强")
    add_candidate("紧张关系", conflict * 0.9 + tension * 0.55 + interaction_counts.get("conflict", 0) * 10, "冲突、紧张或摩擦事件较多")
    add_candidate("竞争关系", competition * 0.85 + interaction_counts.get("competition", 0) * 9, "竞争维度或竞争事件突出")
    add_candidate("潜在亲近关系", affinity * 0.55 + trust * 0.35 + interaction_count * 2 - conflict * 0.45, "高好感、高信任与重复接触可能形成更亲近解释")
    add_candidate("疏远但可信", trust * 0.7 - affinity * 0.2 - interaction_count * 1.5, "信任存在，但亲近和互动证据不足")

    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    top = candidates[0] if candidates else {"label": "未形成稳定解释", "confidence": 20, "rationale": "缺少关系证据"}
    if not evidence:
        evidence.append("暂无明确关系变化事件，主要依据当前关系指标推断")
    return {
        "label": top["label"],
        "confidence": top["confidence"],
        "candidates": candidates[:4],
        "evidence": evidence,
        "metrics": {
            "score": score,
            "affinity": affinity,
            "trust": trust,
            "cooperation": cooperation,
            "competition": competition,
            "conflict": conflict,
            "tension": tension,
            "interaction_count": interaction_count,
        },
        "perspective": "from_agent",
        "interpretation_boundary": "这是从互动证据和关系指标生成的当前解释，不是预设身份，也不是确定事实。",
    }


def update_agent_profile_after_action(conn, resident_id, action, reason, success=True, cost=None, schedule_context=None, tool_input=None):
    ensure_agent_profile_table(conn)
    profile = conn.execute(
        "SELECT energy, time_budget FROM agent_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    if not profile:
        return

    cost = cost or calculate_action_cost(conn, resident_id, action, success=success)
    energy_delta = -int(cost["energy"])

    new_energy = clamp(int(profile["energy"]) + energy_delta)
    new_time_budget = clamp(int(profile["time_budget"]) - int(cost["time"]))
    new_mood = choose_mood(new_energy, action, success)
    task_label = {
        "move": "前往新地点并观察周围变化",
        "chat": "完成一次校园交流",
        "buy_sell": "完成一次校园消费或交易",
        "submit_policy": "提出校园治理建议",
        "create_group": "发起一项协作计划",
        "join_group": "加入一项协作计划",
        "leave_group": "调整自己的协作关系",
        "observe": "观察校园环境并记录线索",
    }.get(action, "根据当前状态继续行动")
    schedule_aligned = is_schedule_aligned(get_resident(conn, resident_id), action, tool_input or {}, schedule_context)
    if schedule_aligned is True:
        task_label = f"按日程执行：{schedule_context['current_task']}"
    elif schedule_aligned is False:
        task_label = f"自主选择暂缓日程：{schedule_context['current_task']}"
    perception = {
        "last_action": action,
        "last_reason": reason,
        "status": "成功" if success else "失败后转为观察",
        "action_cost": cost,
        "time_budget_remaining": new_time_budget,
        "schedule_adherence": schedule_aligned,
    }
    conn.execute(
        """
        UPDATE agent_profiles
        SET energy = ?, time_budget = ?, mood = ?, current_task = ?, perception = ?
        WHERE resident_id = ?
        """,
        (new_energy, new_time_budget, new_mood, task_label, json_dumps(perception, ensure_ascii=False), resident_id),
    )
    body_effects = apply_action_body_effects(
        conn,
        resident_id,
        action,
        success=success,
    )
    if body_effects:
        new_energy = body_effects["energy"]
    return {
        "energy_cost": int(cost["energy"]),
        "time_cost": int(cost["time"]),
        "energy_remaining": new_energy,
        "time_budget_remaining": new_time_budget,
        "body_effects": body_effects,
    }


def advance_multiscale_goals_from_outcome(conn, resident_id, goal_ids, action, adherence, world_time, tick_id, outcome_id):
    updates = []
    for horizon, goal_id in goal_ids.items():
        if not goal_id:
            continue
        raw = conn.execute(
            "SELECT * FROM agent_goals WHERE id = ? AND resident_id = ?",
            (goal_id, resident_id),
        ).fetchone()
        if not raw or raw["status"] != "active":
            continue
        goal = dict(raw)
        delta = goal_progress_delta(goal, action, adherence)
        if delta <= 0:
            continue
        before = dict(goal)
        progress = clamp(int(goal["progress"] or 0) + delta)
        status = "completed" if progress >= 100 else "active"
        conn.execute(
            """
            UPDATE agent_goals
            SET progress = ?, status = ?,
                completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (progress, status, status, world_time.isoformat(), goal_id),
        )
        if goal.get("legacy_long_term_goal_id"):
            conn.execute(
                """
                UPDATE long_term_goals
                SET progress = ?, status = ?, last_update_day = ?,
                    completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END
                WHERE id = ?
                """,
                (
                    progress,
                    status,
                    get_current_day(conn),
                    status,
                    world_time.isoformat(),
                    goal["legacy_long_term_goal_id"],
                ),
            )
        after = dict(conn.execute("SELECT * FROM agent_goals WHERE id = ?", (goal_id,)).fetchone())
        if status == "completed":
            record_goal_revision(
                conn,
                goal_id,
                resident_id,
                "completed",
                before=before,
                after=after,
                reason=f"{action} 行动使目标达到完成阈值",
                trigger_type="plan_outcome",
                tick_id=tick_id,
            )
            conn.execute(
                """
                UPDATE trajectory_episodes
                SET status = 'completed', end_at = ?, outcome_summary = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE resident_id = ? AND goal_id = ?
                """,
                (world_time.isoformat(), f"目标《{goal['title']}》完成", resident_id, goal_id),
            )
            if horizon == "short":
                conn.execute(
                    """
                    UPDATE agent_commitments
                    SET status = 'fulfilled', updated_at = CURRENT_TIMESTAMP
                    WHERE resident_id = ? AND goal_id = ? AND status = 'active'
                    """,
                    (resident_id, goal_id),
                )
        updates.append({"goal_id": goal_id, "horizon": horizon, "delta": delta, "progress": progress, "status": status})
    return updates
