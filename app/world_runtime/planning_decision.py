"""Goal, planning, decision and perception runtime services."""

_MODULE_NAME = __name__
_DEPENDENCY_NAMES = {
    "HTTPException", "WORLD_AUTONOMOUS_ACTIONS", "action_noise_for_agent", "action_score",
    "active_schedule_rules", "active_world_branch_key", "add_event", "add_memory",
    "advance_multiscale_goals_from_outcome", "advance_personal_goal",
    "apply_wellbeing_priority_to_decision", "ask_llm", "assert_destination_available",
    "attach_goal_context_to_plan", "attach_schedule_guidance", "build_rule_based_plan",
    "buy_sell", "calculate_action_cost", "chat_between", "consume_auto_model_budget",
    "create_agent_goal", "create_collaboration", "ensure_action_affordable",
    "ensure_goal_trajectory_episode", "ensure_social_system_tables", "ensure_world_runtime_tables",
    "evolve_relationship", "extract_json", "get_agent_cognitive_context", "get_agent_module_state",
    "get_body_state", "get_campus_environment", "get_current_day", "get_recent_context",
    "get_resident", "get_space_snapshot", "get_world_plan_window", "hunger_recovery_instruction",
    "infer_goal_category", "is_location_open_at_hour", "json_dumps", "load_json_text",
    "location_options_for_context", "log_model_call", "move_resident", "multiscale_goal_templates",
    "normalize_plan_step", "parse_goal_deadline", "plan_step_key", "population_runtime_available",
    "update_agent_profile_after_action", "update_trajectory_from_outcome", "random", "os", "VALID_LOCATIONS",
}

# Default runtime dependency placeholders (overridden via configure())
get_campus_environment = None
active_world_branch_key = None
get_agent_cognitive_context = None
is_location_open_at_hour = None
location_options_for_context = None
active_schedule_rules = None
VALID_LOCATIONS = ["宿舍区", "图书馆", "清芬园", "学堂路", "主楼", "近春园", "紫荆公寓"]
get_body_state = None
rows_to_dicts = (lambda rows: [dict(r) for r in rows])
action_noise_for_agent = None
WORLD_AUTONOMOUS_ACTIONS = [
    "move", "observe", "chat", "reflect", "attend_class", "queue", "consume", "rest", "club_activity", "conflict", "collaborate", "late", "request_leave"
]


def configure(**bindings):
    module_globals = globals()
    for name, value in bindings.items():
        if name not in _DEPENDENCY_NAMES:
            continue
        current = module_globals.get(name)
        if callable(current) and getattr(current, "__module__", None) == _MODULE_NAME:
            continue
        module_globals[name] = value
    module_globals["__name__"] = _MODULE_NAME


def review_multiscale_goals(conn, resident_id, world_time, tick_id=None):
    day = get_current_day(conn)
    rows = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND status = 'active'
        ORDER BY id
        """,
        (resident_id,),
    ).fetchall()
    reviewed = 0
    revised = 0
    for raw in rows:
        goal = dict(raw)
        interval = 7 if goal["horizon"] == "long" else 1
        if day - int(goal.get("last_reviewed_day") or 0) < interval:
            continue
        before = dict(goal)
        status = goal["status"]
        deadline_at = goal["deadline_at"]
        progress = int(goal["progress"] or 0)
        revision_type = "reviewed"
        reason = "按时间尺度完成周期复盘"
        if progress >= 100:
            status = "completed"
            revision_type = "completed"
            reason = "目标进度达到完成阈值"
        else:
            deadline = parse_goal_deadline(deadline_at)
            if deadline and deadline <= world_time:
                commitment = int(goal["commitment"] or 0)
                feasibility = int(goal["feasibility"] or 0)
                if goal["horizon"] == "short":
                    status = "completed" if progress >= 75 else ("paused" if commitment >= 65 else "abandoned")
                    revision_type = status
                    reason = "短期目标到期，根据完成度和承诺强度结算"
                elif goal["horizon"] == "medium" and feasibility < 35 and progress < 40:
                    status = "paused"
                    revision_type = "paused"
                    reason = "中期项目到期且可行性持续偏低"
                else:
                    extension_days = 30 if goal["horizon"] == "long" else 7
                    deadline_at = (world_time + timedelta(days=extension_days)).isoformat()
                    revision_type = "extended"
                    reason = "目标仍有价值，调整期限继续推进"
        completed_at = world_time.isoformat() if status == "completed" else goal.get("completed_at")
        conn.execute(
            """
            UPDATE agent_goals
            SET status = ?, deadline_at = ?, last_reviewed_day = ?,
                completed_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, deadline_at, day, completed_at, goal["id"]),
        )
        after = dict(conn.execute("SELECT * FROM agent_goals WHERE id = ?", (goal["id"],)).fetchone())
        if revision_type != "reviewed":
            record_goal_revision(
                conn,
                goal["id"],
                resident_id,
                revision_type,
                before=before,
                after=after,
                reason=reason,
                trigger_type="periodic_review",
                tick_id=tick_id,
            )
        reviewed += 1
        revised += int(revision_type != "reviewed")
    return {"reviewed": reviewed, "revised": revised}


def ensure_daily_commitments(conn, resident, short_goal, world_time):
    day_key = world_time.strftime("%Y-%m-%d")
    conn.execute(
        """
        UPDATE agent_commitments
        SET status = 'released', updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ? AND status = 'active' AND goal_id IS NOT NULL
          AND goal_id IN (
              SELECT id FROM agent_goals
              WHERE resident_id = ? AND status != 'active'
          )
        """,
        (resident["id"], resident["id"]),
    )
    expired = conn.execute(
        """
        SELECT * FROM agent_commitments
        WHERE resident_id = ? AND status = 'active' AND due_at != '' AND due_at <= ?
        """,
        (resident["id"], world_time.isoformat()),
    ).fetchall()
    for row in expired:
        linked_goal = conn.execute(
            "SELECT progress FROM agent_goals WHERE id = ?",
            (row["goal_id"],),
        ).fetchone() if row["goal_id"] else None
        status = "fulfilled" if linked_goal and int(linked_goal["progress"] or 0) >= 60 else "missed"
        conn.execute(
            "UPDATE agent_commitments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, row["id"]),
        )
    existing = conn.execute(
        """
        SELECT * FROM agent_commitments
        WHERE resident_id = ? AND goal_id = ? AND status = 'active' AND start_at LIKE ?
        ORDER BY importance DESC, id
        LIMIT 1
        """,
        (resident["id"], short_goal["id"], f"{day_key}%"),
    ).fetchone()
    if existing:
        return dict(existing)
    group = role_group(resident.get("role"))
    weekday = world_time.weekday() < 5
    if group == "teacher":
        title, commitment_type, importance = "履行当天教学与指导职责", "institutional", 82
    elif group == "business":
        title, commitment_type, importance = "维持当天校园服务并回应需求", "service", 76
    elif group == "service":
        title, commitment_type, importance = "完成当天校园运行巡查与协调", "institutional", 84
    elif weekday:
        title, commitment_type, importance = "完成当天课程与学习安排", "institutional", 78
    else:
        title, commitment_type, importance = "平衡休息、自主学习与社会联系", "personal", 62
    start_at = world_time.replace(hour=0, minute=0, second=0, microsecond=0)
    due_at = start_at + timedelta(days=1)
    cursor = conn.execute(
        """
        INSERT INTO agent_commitments
        (resident_id, goal_id, commitment_type, title, start_at, due_at,
         status, importance, flexibility, visibility)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, 'private')
        """,
        (
            resident["id"],
            short_goal["id"],
            commitment_type,
            title,
            start_at.isoformat(),
            due_at.isoformat(),
            importance,
            35 if commitment_type == "institutional" else 65,
        ),
    )
    return dict(conn.execute("SELECT * FROM agent_commitments WHERE id = ?", (cursor.lastrowid,)).fetchone())


def ensure_multiscale_goal_structure(conn, resident, world_time, tick_id=None):
    review = review_multiscale_goals(conn, resident["id"], world_time, tick_id=tick_id)
    active = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM agent_goals
            WHERE resident_id = ? AND status = 'active'
            ORDER BY priority DESC, commitment DESC, id
            """,
            (resident["id"],),
        ).fetchall()
    ]
    long_goals = [goal for goal in active if goal["horizon"] == "long"]
    if not long_goals:
        long_goal = create_agent_goal(
            conn,
            resident["id"],
            "long",
            resident.get("goal") or "形成稳定而有意义的校园生活",
            category=infer_goal_category(resident.get("goal")),
            source="self",
            priority=70,
            commitment=65,
            expected_utility=70,
            feasibility=55,
            uncertainty=35,
            deadline_at=(world_time + timedelta(days=90)).isoformat(),
        )
    else:
        long_goal = max(
            long_goals,
            key=lambda goal: (
    int(goal.get("priority") or 0)
    + int(goal.get("commitment") or 0)
    + int(goal.get("expected_utility") or 0)
    + random.uniform(-8, 8)
),
        )
        for competing_goal in long_goals:
            if competing_goal["id"] == long_goal["id"]:
                continue
            conn.execute(
                """
                INSERT INTO goal_dependencies
                (goal_id, related_goal_id, relationship_type, strength, explanation)
                VALUES (?, ?, 'competes', 45, '多个长期方向竞争有限的时间、精力和资源')
                ON CONFLICT(goal_id, related_goal_id, relationship_type) DO NOTHING
                """,
                (long_goal["id"], competing_goal["id"]),
            )
    category, medium_title, short_title = multiscale_goal_templates(resident, long_goal)
    medium_row = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND parent_goal_id = ? AND horizon = 'medium' AND status = 'active'
        ORDER BY priority DESC, id LIMIT 1
        """,
        (resident["id"], long_goal["id"]),
    ).fetchone()
    medium_goal = dict(medium_row) if medium_row else create_agent_goal(
        conn,
        resident["id"],
        "medium",
        medium_title,
        category=category,
        parent_goal_id=long_goal["id"],
        source="goal_decomposition",
        priority=68,
        commitment=62,
        expected_utility=68,
        feasibility=62,
        uncertainty=28,
        deadline_at=(world_time + timedelta(days=21)).isoformat(),
    )
    short_row = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND parent_goal_id = ? AND horizon = 'short' AND status = 'active'
        ORDER BY priority DESC, id LIMIT 1
        """,
        (resident["id"], medium_goal["id"]),
    ).fetchone()
    short_goal = dict(short_row) if short_row else create_agent_goal(
        conn,
        resident["id"],
        "short",
        short_title,
        category=category,
        parent_goal_id=medium_goal["id"],
        source="goal_decomposition",
        priority=72,
        commitment=68,
        expected_utility=66,
        feasibility=72,
        uncertainty=20,
        deadline_at=(world_time + timedelta(days=3)).isoformat(),
    )
    for goal_id, related_goal_id in (
        (medium_goal["id"], long_goal["id"]),
        (short_goal["id"], medium_goal["id"]),
    ):
        conn.execute(
            """
            INSERT INTO goal_dependencies
            (goal_id, related_goal_id, relationship_type, strength, explanation)
            VALUES (?, ?, 'supports', 80, '下层目标为上层目标提供可验证进展')
            ON CONFLICT(goal_id, related_goal_id, relationship_type) DO NOTHING
            """,
            (goal_id, related_goal_id),
        )
    commitment = ensure_daily_commitments(conn, resident, short_goal, world_time)
    episodes = {
        goal["horizon"]: ensure_goal_trajectory_episode(conn, goal, world_time)
        for goal in (long_goal, medium_goal, short_goal)
    }
    conn.execute(
        "UPDATE trajectory_episodes SET parent_episode_id = ? WHERE id = ?",
        (episodes["long"]["id"], episodes["medium"]["id"]),
    )
    conn.execute(
        "UPDATE trajectory_episodes SET parent_episode_id = ? WHERE id = ?",
        (episodes["medium"]["id"], episodes["short"]["id"]),
    )
    return {
        "long": long_goal,
        "medium": medium_goal,
        "short": short_goal,
        "commitment": commitment,
        "episodes": episodes,
        "review": review,
    }


def build_llm_action_plan(conn, resident, window_start, window_end, world_time, goal_context=None):
    if not consume_auto_model_budget(conn, "planner", resident_id=resident["id"]):
        return None
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    prompt = f"""
你是一个校园平行世界的运行时 planner。请为 Agent 制定接下来 8 小时内的简短行动计划。

世界时间：{world_time.strftime('%Y-%m-%d %H:%M')}
计划窗口：{window_start.strftime('%H:%M')} 到 {window_end.strftime('%H:%M')}
可选地点：{", ".join(VALID_LOCATIONS)}
可选动作：move, observe, chat, reflect, attend_class, queue, consume, rest, club_activity, conflict, collaborate, late, request_leave
现实约束：
- 00:00-06:00 大多数学生应在宿舍区休息或反思，只有少量异常情况会在其他开放空间观察。
- 食堂开放 06:00-21:00，商业街 09:00-22:00，教学楼和图书馆夜间关闭，校务处 08:00-18:00。
- 下雨、雷雨、大风时应显著减少操场计划。
- 计划需要有少量随机性和个体差异，但不能让所有 Agent 同时去同一地点。

Agent:
- id: {resident['id']}
- name: {resident['name']}
- role: {resident['role']}
- current_location: {resident['location']}
- long_goal: {resident['goal']}
- active_long_goal: {goal_context['long']['title'] if goal_context else resident['goal']}
- medium_project: {goal_context['medium']['title'] if goal_context else '尚未建立'}
- short_goal: {goal_context['short']['title'] if goal_context else '尚未建立'}
- current_commitment: {goal_context['commitment']['title'] if goal_context and goal_context.get('commitment') else '无'}

只返回 JSON，不要解释。格式：
{{
  "intent": "一句话说明这个 8 小时窗口的意图",
  "steps": [
    {{"time": "HH:MM", "action": "attend_class", "location": "教学楼", "goal": "具体目标"}}
  ],
  "flexibility": 0.35
}}
steps 保持 3 条以内，时间必须落在计划窗口内。
"""
    try:
        raw = ask_llm(prompt)
        payload = extract_json(raw)
        steps = [
            normalize_plan_step(step, window_start, index, resident["location"], resident["goal"])
            for index, step in enumerate((payload.get("steps") or [])[:3])
        ]
        if not steps:
            raise ValueError("LLM plan has no steps")
        plan = {
            "resident_id": resident["id"],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "intent": str(payload.get("intent") or f"{resident['name']}按个人目标推进校园生活")[:180],
            "steps": steps,
            "flexibility": float(payload.get("flexibility") or 0.35),
            "source": "llm-planner-v1",
        }
        log_model_call(
            conn,
            "planner",
            status="success",
            resident_id=resident["id"],
            model_name=model_name,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return plan
    except Exception as exc:
        logger.warning("LLM planner failed for resident %s", resident["id"], exc_info=True)
        log_model_call(conn, "planner", status=f"failed:{type(exc).__name__}", resident_id=resident["id"], model_name=model_name)
        return None


def ensure_current_action_plans(conn, world_time):
    ensure_world_runtime_tables(conn)
    window_start, window_end = get_world_plan_window(world_time)
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
    residents = conn.execute(
        f"""
        SELECT r.id, r.name, r.role, r.personality, r.goal, r.money, r.location, p.strategy
        FROM residents r
        LEFT JOIN agent_profiles p ON p.resident_id = r.id
        {lifecycle_join}
        {lifecycle_filter}
        ORDER BY r.id
        """
    ).fetchall()
    created = 0
    llm_plans = 0
    rule_based_plans = 0
    backfilled_plans = 0
    goals_revised = 0
    for resident in residents:
        existing = conn.execute(
            """
            SELECT id, plan_json FROM agent_action_plans
            WHERE resident_id = ? AND window_start = ? AND status = 'active'
            """,
            (resident["id"], window_start.isoformat()),
        ).fetchone()
        if existing:
            existing_plan = load_json_text(existing["plan_json"], {})
            if not existing_plan.get("goal_chain"):
                goal_context = ensure_multiscale_goal_structure(conn, dict(resident), world_time)
                existing_plan = attach_goal_context_to_plan(existing_plan, goal_context)
                conn.execute(
                    "UPDATE agent_action_plans SET plan_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json_dumps(existing_plan, ensure_ascii=False), existing["id"]),
                )
                backfilled_plans += 1
            continue
        goal_context = ensure_multiscale_goal_structure(conn, dict(resident), world_time)
        runtime_llm_enabled = os.getenv("WORLD_RUNTIME_USE_LLM", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        plan = (
            build_llm_action_plan(conn, resident, window_start, window_end, world_time, goal_context)
            if runtime_llm_enabled
            else None
        )
        if plan:
            model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
            llm_plans += 1
        else:
            plan = build_rule_based_plan(conn, resident, window_start, window_end, world_time, goal_context)
            model_name = "rule-based-v1"
            rule_based_plans += 1
        plan = attach_goal_context_to_plan(plan, goal_context)
        conn.execute(
            """
            INSERT INTO agent_action_plans
            (resident_id, window_start, window_end, plan_json, model_name, prompt_version, status)
            VALUES (?, ?, ?, ?, 'rule-based-v1', 'world-runtime-v4', 'active')
            ON CONFLICT(resident_id, window_start)
            DO UPDATE SET plan_json = excluded.plan_json, window_end = excluded.window_end,
                          prompt_version = 'world-runtime-v4', status = 'active',
                          updated_at = CURRENT_TIMESTAMP
            """,
            (resident["id"], window_start.isoformat(), window_end.isoformat(), json_dumps(plan, ensure_ascii=False)),
        )
        conn.execute(
            """
            UPDATE agent_action_plans
            SET model_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ? AND window_start = ?
            """,
            (model_name, resident["id"], window_start.isoformat()),
        )
        created += 1
    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "created": created,
        "llm_plans": llm_plans,
        "rule_based_plans": rule_based_plans,
        "backfilled_plans": backfilled_plans,
        "goals_revised": goals_revised,
    }


def decide_agent_action(conn, resident_id):
    ensure_social_system_tables(conn)
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    day = get_current_day(conn)
    env = get_campus_environment(conn, day)
    module_state = get_agent_module_state(conn, resident_id)
    schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
    memory_terms = [
        resident["location"],
        resident["goal"],
        env.get("weather", ""),
        env.get("event_name", ""),
        schedule_context.get("current_task", ""),
        schedule_context.get("location", ""),
    ]
    context = get_recent_context(conn, resident_id, query_terms=memory_terms)
    other_agents = conn.execute(
        "SELECT id, name, role, location FROM residents WHERE id != ? ORDER BY id",
        (resident_id,),
    ).fetchall()
    active_groups = conn.execute(
        "SELECT id, name, shared_goal, member_ids, deadline_day FROM group_goals WHERE status = 'active' ORDER BY id DESC LIMIT 8"
    ).fetchall()

    prompt = f"""
你正在驱动一个真实地理校园世界中的 Agent。

当前日期：第 {day} 天
校园环境：{json_dumps(env, ensure_ascii=False)}
空间状态（容量、开放状态和事件）：{json_dumps(get_space_snapshot(conn, day), ensure_ascii=False)}
当前 Agent：{json_dumps(dict(resident), ensure_ascii=False)}
其他 Agent：{json_dumps(rows_to_dicts(other_agents), ensure_ascii=False)}
可加入或协作的活跃小组：{json_dumps(rows_to_dicts(active_groups), ensure_ascii=False)}
近期记忆和事件：{json_dumps(context, ensure_ascii=False)}
Agent 六模块状态：{json_dumps(module_state, ensure_ascii=False)}
当前日程提示：{json_dumps(schedule_context, ensure_ascii=False)}。日程、天气、关系和资源都是你需要权衡的信息，不是强制命令。你必须自主选择行动，也要在 reason 中说明是否愿意承担暂缓日程、绕开拥挤或消耗资源的后果。

请只返回严格 JSON，不要解释，不要 Markdown。
可选 action 只能是：move、chat、buy_sell、submit_policy、observe、create_group、join_group、leave_group。
地点只能从这些里面选：{list(VALID_LOCATIONS)}。

返回格式：
{{
  "action": "move/chat/buy_sell/submit_policy/observe",
  "reason": "为什么这样做",
  "tool_input": {{}}
}}

tool_input 规则：
move: {{"destination": "图书馆"}}
chat: {{"target_id": 2, "message": "一句校园对话"}}
buy_sell: {{"seller_id": 5, "item_name": "套餐饭", "quantity": 1, "unit_price": 12}}
submit_policy: {{"title": "政策标题", "description": "政策内容"}}
observe: {{"focus": "观察什么"}}
create_group: {{"title": "小组名称", "goal": "共同目标", "member_ids": [2, 3]}}
join_group: {{"group_id": 1}}
leave_group: {{"group_id": 1}}
"""

    try:
        raw = ask_llm(prompt)
        decision = extract_json(raw)
    except Exception as exc:
        decision = {
            "action": "observe",
            "reason": f"AI 决策解析失败，改为观察校园：{exc}",
            "tool_input": {"focus": "校园整体状态"},
        }

    decision = attach_schedule_guidance(schedule_context, decision)

    return {
        "resident": dict(resident),
        "decision": decision,
        "schedule_context": schedule_context,
        "memory_context": context,
    }


def execute_decision(conn, resident_id, decision):
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    action = str(decision.get("action", "observe")).strip()
    reason = str(decision.get("reason", "自主决策"))
    tool_input = decision.get("tool_input") or {}
    day = get_current_day(conn)
    module_state = get_agent_module_state(conn, resident_id)
    schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
    planned_cost = calculate_action_cost(conn, resident_id, action, tool_input, success=True)

    try:
        ensure_action_affordable(conn, resident_id, planned_cost, action)
        if action == "move":
            destination = tool_input.get("destination", resident["location"])
            assert_destination_available(conn, destination)
            result = move_resident(conn, resident_id, destination)
        elif action == "chat":
            target_id = int(tool_input.get("target_id"))
            message = tool_input.get("message") or "今天校园情况怎么样？"
            result = chat_between(conn, resident_id, target_id, message)
        elif action == "buy_sell":
            seller_id = int(tool_input.get("seller_id", 5))
            item_name = tool_input.get("item_name", "套餐饭")
            quantity = int(tool_input.get("quantity", 1))
            unit_price = int(tool_input.get("unit_price", 10))
            result = buy_sell(conn, resident_id, seller_id, item_name, quantity, unit_price)
        elif action == "submit_policy":
            title = tool_input.get("title", "校园微调建议")
            description = tool_input.get("description", reason)
            conn.execute(
                """
                INSERT INTO policies (title, description, proposer_id)
                VALUES (?, ?, ?)
                """,
                (title, description, resident_id),
            )
            text = f"{resident['name']} 提交校园政策《{title}》：{description}"
            add_event(conn, day, "policy_submit", text)
            add_memory(conn, resident_id, day, text, importance=3)
            conn.commit()
            result = {"message": "政策提交成功", "description": text}
        elif action == "create_group":
            title = str(tool_input.get("title") or f"{resident['name']}的协作小组")[:40]
            goal = str(tool_input.get("goal") or resident["goal"])[:180]
            member_ids = [int(member_id) for member_id in tool_input.get("member_ids", []) if str(member_id).isdigit()]
            member_ids = [member_id for member_id in member_ids if member_id != resident_id][:5]
            if not member_ids:
                raise ValueError("发起协作至少需要邀请一位其他 Agent")
            group = create_collaboration(conn, resident_id, member_ids, title, goal)
            result = {"message": "协作小组已发起", "description": f"{resident['name']} 发起小组「{title}」。", "group": group}
        elif action == "join_group":
            group_id = int(tool_input.get("group_id"))
            group = join_group_goal(conn, resident_id, group_id)
            result = {"message": group["message"], "description": f"{resident['name']} 加入小组「{group['group_name']}」。", "group": group}
        elif action == "leave_group":
            group_id = int(tool_input.get("group_id"))
            group = leave_group_goal(conn, resident_id, group_id)
            result = {"message": group["message"], "description": f"{resident['name']} 退出小组「{group['group_name']}」。", "group": group}
        elif action == "observe":
            focus = tool_input.get("focus", "校园状态")
            text = f"{resident['name']} 观察 {focus}。原因：{reason}"
            add_event(conn, day, "agent_observe", text)
            add_memory(conn, resident_id, day, text, importance=1)
            conn.commit()
            result = {"message": "观察完成", "description": text}
        else:
            raise ValueError(f"不支持的自主行动：{action}")
    except Exception as exc:
        # PostgreSQL marks the current transaction as unusable after a failed
        # statement. Roll it back before recording this Agent's failed action.
        conn.rollback()
        text = f"{resident['name']} 自主选择执行 {action}，但未能完成：{exc}。本轮不替 Agent 改选其他行为。"
        add_event(conn, day, "agent_action_failed", text)
        add_memory(conn, resident_id, day, text, importance=1)
        failed_cost = calculate_action_cost(conn, resident_id, action, tool_input, success=False)
        action_cost = update_agent_profile_after_action(conn, resident_id, action, reason, success=False, cost=failed_cost, schedule_context=schedule_context, tool_input=tool_input)
        conn.commit()
        result = {"message": "行动失败，保留自主选择结果", "description": text, "error": str(exc)}

    success = "error" not in result
    learned_action = action
    if success:
        action_cost = update_agent_profile_after_action(conn, resident_id, action, reason, success=True, cost=planned_cost, schedule_context=schedule_context, tool_input=tool_input)
    social_update = None
    if success and action == "chat":
        try:
            target_id = int(tool_input.get("target_id"))
            social_update = {
                "speaker": evolve_relationship(conn, resident_id, target_id, "chat", "日常交流", 3, 2, -1),
                "listener": evolve_relationship(conn, target_id, resident_id, "chat", "回应交流", 2, 2, -1),
            }
        except Exception as exc:
            conn.rollback()
            social_update = {"error": str(exc)}
    goal_update = advance_personal_goal(conn, resident_id, learned_action, success)
    learning = record_learning(
        conn,
        resident_id,
        learned_action,
        "成功" if success else "失败",
        action_score(learned_action, success),
        f"执行 {learned_action} 后得到反馈：{result}",
    )
    conn.commit()

    return {
        "resident_id": resident_id,
        "action": action,
        "reason": reason,
        "result": result,
        "success": success,
        "learning": learning,
        "social_update": social_update,
        "long_term_goal": goal_update,
        "action_cost": action_cost,
        "schedule_context": schedule_context,
    }


def record_plan_outcome(conn, agent, plan, step, decision, action, destination, content, world_time, tick_id, day, event_id):
    plan_id = plan.get("_plan_row_id")
    step_key = step.get("step_key") or plan_step_key(step)
    if not plan_id or step.get("plan_state") != "due" or not step_key:
        return None
    planned_action = str(step.get("action") or "")
    planned_location = str(step.get("location") or "")
    relation = str(decision.get("plan_relation") or "continue")
    if action == planned_action and destination == planned_location and relation == "continue":
        adherence = "followed"
        deviation_type = ""
    elif relation in {"adjust", "respond", "rest"}:
        adherence = "adjusted"
        deviation_type = relation
    else:
        adherence = "deviated"
        deviation_type = "action_or_location_changed"
    deviation_reason = ""
    if adherence != "followed":
        notes = decision.get("constraint_notes") or []
        deviation_reason = "；".join(str(note) for note in notes) or str(decision.get("reason") or "")
    goal_ids = {
        "long": step.get("long_goal_id") or plan.get("goal_chain", {}).get("long_goal_id"),
        "medium": step.get("medium_goal_id") or plan.get("goal_chain", {}).get("medium_goal_id"),
        "short": step.get("short_goal_id") or plan.get("goal_chain", {}).get("short_goal_id"),
    }
    cursor = conn.execute(
        """
        INSERT INTO plan_outcomes
        (resident_id, plan_id, long_goal_id, medium_goal_id, short_goal_id,
         tick_id, day, step_key, planned_json, actual_json, adherence,
         deviation_type, deviation_reason, outcome_summary, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plan_id, step_key) DO NOTHING
        """,
        (
            agent["id"],
            plan_id,
            goal_ids["long"],
            goal_ids["medium"],
            goal_ids["short"],
            tick_id,
            day,
            step_key,
            json_dumps(step, ensure_ascii=False),
            json_dumps({"action": action, "location": destination, "decision": decision}, ensure_ascii=False),
            adherence,
            deviation_type,
            deviation_reason[:240],
            content[:300],
            json_dumps({"world_event_id": event_id}, ensure_ascii=False),
        ),
    )
    outcome = conn.execute(
        "SELECT * FROM plan_outcomes WHERE plan_id = ? AND step_key = ?",
        (plan_id, step_key),
    ).fetchone()
    if not outcome:
        return None
    outcome_id = outcome["id"]
    progress_updates = advance_multiscale_goals_from_outcome(
        conn,
        agent["id"],
        goal_ids,
        action,
        adherence,
        world_time,
        tick_id,
        outcome_id,
    ) if cursor.rowcount else []
    if cursor.rowcount:
        conn.execute(
            """
            UPDATE plan_outcomes
            SET progress_delta = ?, evidence_json = ?
            WHERE id = ?
            """,
            (
                sum(int(item["delta"]) for item in progress_updates),
                json_dumps(
                    {"world_event_id": event_id, "goal_progress": progress_updates},
                    ensure_ascii=False,
                ),
                outcome_id,
            ),
        )
        update_trajectory_from_outcome(
            conn,
            agent["id"],
            goal_ids,
            action,
            destination,
            adherence,
            world_time,
            outcome_id,
        )
    return {
        "id": outcome_id,
        "adherence": adherence,
        "deviation_type": deviation_type,
        "goal_progress": progress_updates,
    }


def build_runtime_perception(conn, agent, world_time, day, slot, plan, step, observed):
    env_fn = get_campus_environment
    env = dict(env_fn(conn, day)) if callable(env_fn) else {}

    branch_fn = active_world_branch_key
    branch_key = branch_fn(conn) if callable(branch_fn) else "main"

    cog_fn = get_agent_cognitive_context
    cognitive_context = None
    if callable(cog_fn):
        try:
            cognitive_context = cog_fn(conn, agent["id"], branch_key=branch_key, limit=8)
        except Exception:
            cognitive_context = None
    if not cognitive_context:
        cognitive_context = {
            "observations": [], "beliefs": [], "spatial_memories": [],
            "adaptive_memories": [], "strategy_states": [], "norm_beliefs": [],
            "received_information": []
        }

    location_counts = {
        row["location"]: row["count"]
        for row in conn.execute("SELECT location, COUNT(*) AS count FROM residents GROUP BY location").fetchall()
    }
    hour = world_time.hour

    open_fn = is_location_open_at_hour or (lambda loc, h: True)
    loc_opts_fn = location_options_for_context or (lambda r, h, w, loc, **kw: [])
    sched_rules_fn = active_schedule_rules or (lambda c, r, h, e: [])

    valid_locs = VALID_LOCATIONS or []
    open_locations = [location for location in valid_locs if open_fn(location, hour)]
    realistic_options = [
        location
        for location, _ in loc_opts_fn(
            agent["role"],
            hour,
            env.get("weather"),
            agent["location"],
            conn=None,
            env=None,
            agent=agent,
        )
    ]
    schedule_rules = sched_rules_fn(conn, agent["role"], hour, env)
    relationships = conn.execute(
        """
        SELECT r.to_resident_id, residents.name, r.affinity, r.trust, r.cooperation, r.conflict, r.tension
        FROM relationship_dynamics r
        JOIN residents ON residents.id = r.to_resident_id
        WHERE r.from_resident_id = ?
        ORDER BY r.interaction_count DESC, r.trust DESC
        LIMIT 5
        """,
        (agent["id"],),
    ).fetchall()
    profile = conn.execute("SELECT energy, time_budget, mood, skills, strategy FROM agent_profiles WHERE resident_id = ?", (agent["id"],)).fetchone()
    
    body_state_fn = get_body_state
    if not callable(body_state_fn):
        from app.body_runtime import get_body_state as body_state_fn
    body_state = body_state_fn(conn, agent["id"])

    noise_fn = action_noise_for_agent or (lambda a: 0.0)
    avail_actions = WORLD_AUTONOMOUS_ACTIONS or [
        "move", "observe", "chat", "reflect", "attend_class", "queue", "consume", "rest", "club_activity", "conflict", "collaborate", "late", "request_leave"
    ]

    from app.body_runtime import get_agent_inventory_summary
    inventory_summary = get_agent_inventory_summary(conn, agent["id"])
    return {
        "world_time": world_time.isoformat(),
        "slot": slot,
        "agent_location": agent["location"],
        "agent_profile": {
            "personality": agent.get("personality", ""),
            "money": agent.get("money", 0),
            "energy": profile["energy"] if profile else None,
            "time_budget": profile["time_budget"] if profile else None,
            "mood": profile["mood"] if profile else "",
            "trait_bias": noise_fn(agent),
        },
        "body_state": body_state or {},
        "inventory_summary": inventory_summary,
        "local_crowd": int(location_counts.get(agent["location"], 0)),
        "open_locations": open_locations,
        "realistic_location_options": realistic_options,
        "available_actions": sorted(avail_actions),
        "active_schedule_rules": [
            {
                "action_type": row.get("action_type"),
                "location": row.get("location"),
                "base_weight": row.get("base_weight"),
                "description": row.get("description"),
            }
            for row in schedule_rules[:6]
        ],
        "relationship_context": rows_to_dicts(relationships),
        "realism_constraints": {
            "hour": hour,
            "deep_night": 0 <= hour < 6,
            "bad_weather": any(token in str(env.get("weather") or "") for token in ("雨", "雷", "雪", "大风")),
            "note": "行动可以随机偏离计划，但需符合时间、天气、空间开放、角色身份、个体差异和关系网络。",
        },
        "environment": {
            "weather": env.get("weather"),
            "temperature": env.get("temperature"),
            "time_slot": env.get("time_slot"),
            "rainfall": env.get("rainfall"),
        },
        "plan_intent": plan.get("intent", ""),
        "goal_chain": plan.get("goal_chain", {}),
        "plan_step": step,
        "local_observations": cognitive_context["observations"],
        "beliefs": cognitive_context["beliefs"],
        "spatial_memories": cognitive_context["spatial_memories"],
        "adaptive_memories": cognitive_context["adaptive_memories"],
        "learned_strategies": cognitive_context["strategy_states"],
        "norm_beliefs": cognitive_context["norm_beliefs"],
        "received_information": cognitive_context["received_information"],
        "information_boundary": (
            "仅包含亲历、自身状态、局部观察、已接收消息和由这些证据形成的信念；"
            "不包含校园全局事件或系统聚合真相。"
        ),
    }


def apply_wellbeing_priority_to_decision(conn, agent, decision, world_time):
    """Pass-through decision maker. Emergency safety stop ONLY when health <= 0."""
    body_state = get_body_state(conn, agent["id"])
    if not body_state:
        return decision
    decision = dict(decision or {})
    health = float(body_state.get("health", 100) if body_state.get("health") is not None else 100)
    if health <= 0:
        return {
            **decision,
            "action": "rest",
            "location": agent["location"],
            "goal": "身体虚脱，原地修养恢复",
            "reason": "极度身体虚脱，触发底线安全休养。",
            "plan_relation": "rest",
        }
    return decision
