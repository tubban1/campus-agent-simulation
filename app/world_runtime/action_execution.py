"""Autonomous agent action execution for world ticks."""


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


def apply_runtime_social_effect(conn, agent, action, location, day):
    target = nearby_interaction_target(conn, agent["id"], location)
    if not target:
        return None
    if action in {"chat", "club_activity", "collaborate"}:
        change = evolve_relationship(conn, agent["id"], target["id"], action, f"{location}发生协作或交流", 3, 4, -1)
        commitment = maybe_create_social_commitment(conn, agent["id"], target, location) if action == "collaborate" else None
        return {
            "target_id": target["id"],
            "target_name": target["name"],
            "effect": "positive",
            "relationship": change,
            "commitment": commitment,
        }
    if action == "conflict":
        change = evolve_relationship(conn, agent["id"], target["id"], "conflict", f"{location}发生轻微摩擦", -3, -2, 4)
        add_event(conn, day, "world_agent_conflict", f"{agent['name']} 与 {target['name']} 在{location}出现轻微摩擦。")
        return {"target_id": target["id"], "target_name": target["name"], "effect": "conflict", "relationship": change}
    return None


def get_current_spatial_action_context(conn, resident_id, action):
    """Return the real node when an agent can already perform ``action`` there.

    Legacy world decisions still use broad destination labels such as ``食堂``.
    Real-world imports, however, place residents at concrete nodes such as
    ``清晏楼(食堂/报告厅)``.  Comparing those two strings caused an agent who had
    already arrived to be sent to the generic destination again on every tick.
    """
    if action != "consume":
        return None
    try:
        row = conn.execute(
            """
            SELECT n.id AS node_id, n.name AS node_name
            FROM agent_spatial_states s
            JOIN spatial_nodes n ON n.id = s.current_node_id
            JOIN spatial_affordances a ON a.node_id = n.id
            WHERE s.resident_id = ?
              AND s.movement_status IN ('idle', 'arrived')
              AND a.affordance_key = ?
              AND a.status = 'open'
            LIMIT 1
            """,
            (resident_id, action),
        ).fetchone()
    except Exception:
        # Spatial tables may not exist during legacy-schema startup.  Preserve
        # the existing non-spatial execution path in that case.
        return None
    return dict(row) if row else None


def describe_runtime_action(conn, agent, action, destination, goal, day, observed=False):
    social_effect = None
    importance = 1
    if action == "attend_class":
        content = f"{agent['name']} 在{destination}参与课程活动，围绕「{goal}」记录课堂进展。"
        event_type = "world_agent_attend_class"
    elif action == "queue":
        content = f"{agent['name']} 在{destination}排队等待服务，资源压力让当前节奏变慢，目标：{goal}。"
        event_type = "world_agent_queue"
    elif action == "consume":
        content = f"{agent['name']} 在{destination}完成一次校园消费或服务使用，目标：{goal}。"
        event_type = "world_agent_consume"
    elif action == "rest":
        content = f"{agent['name']} 在{destination}休息恢复精力，暂时放慢行动节奏，目标：{goal}。"
        event_type = "world_agent_rest"
    elif action == "club_activity":
        content = f"{agent['name']} 在{destination}参加社团或课余活动，校园互动热度被轻微带动，目标：{goal}。"
        event_type = "world_agent_club_activity"
        social_effect = apply_runtime_social_effect(conn, agent, action, destination, day)
    elif action == "conflict":
        content = f"{agent['name']} 在{destination}因拥挤、资源或意见差异出现轻微冲突，目标：{goal}。"
        event_type = "world_agent_conflict"
        social_effect = apply_runtime_social_effect(conn, agent, action, destination, day)
        importance = max(importance, 3)
    elif action == "collaborate":
        content = f"{agent['name']} 在{destination}与他人协作推进任务，目标：{goal}。"
        event_type = "world_agent_collaborate"
        social_effect = apply_runtime_social_effect(conn, agent, action, destination, day)
        importance = max(importance, 2)
    elif action == "late":
        content = f"{agent['name']} 到达{destination}的节奏偏慢，可能错过部分安排，目标：{goal}。"
        event_type = "world_agent_late"
        importance = max(importance, 2)
    elif action == "request_leave":
        content = f"{agent['name']} 在{destination}整理或提交请假/事务申请，目标：{goal}。"
        event_type = "world_agent_request_leave"
    elif action == "chat":
        content = f"{agent['name']} 在{agent['location']}围绕{destination}附近的校园状态进行轻量交流，目标：{goal}。"
        event_type = "world_agent_chat"
        social_effect = apply_runtime_social_effect(conn, agent, action, agent["location"], day)
    elif action == "reflect":
        content = f"{agent['name']} 在{agent['location']}整理当前节奏和个人状态，目标：{goal}。"
        event_type = "world_agent_reflect"
    else:
        focus = destination if destination in VALID_LOCATIONS else "校园状态"
        content = f"{agent['name']} 在{agent['location']}观察{focus}，目标：{goal}。"
        event_type = "world_agent_observe"
    add_event(conn, day, event_type, content)
    add_memory_once(conn, agent["id"], day, content, importance=importance, source="world_tick")
    return content, event_type, social_effect


def process_world_agent_tick(conn, agent, world_time, tick_id, day, slot, observed=False, parent_event_id=None):
    state_before = get_agent_module_state(conn, agent["id"])

    # Autonomous affordance discovery & atomic action plan progression during world tick
    try:
        from app.world_runtime.atomic_action_runtime import process_atomic_action_plan_for_agent_tick
        atomic_res = process_atomic_action_plan_for_agent_tick(conn, agent, world_time=world_time)
        if atomic_res and atomic_res.get("status") in ("step_executed", "movement_started", "moving", "plan_failed", "completed"):
            conn.commit()
            return {
                "resident_id": agent["id"],
                "success": True,
                "action_success": atomic_res.get("success", True),
                "event": None,
                "action_execution_id": None,
                "plan_outcome": atomic_res,
                "source": "atomic_action_plan",
            }
    except Exception as err:
        append_world_event(
            conn,
            event_type="atomic_action_exception",
            title=f"{agent.get('name', 'Agent')}原子行动推进故障",
            content=f"执行原子行动计划出现异常：{str(err)}",
            resident_id=agent["id"],
            payload={"error": str(err)},
            source_type="atomic_action",
        )
        conn.commit()

    plan = get_current_agent_plan(conn, agent["id"], world_time) or {}

    step = choose_plan_step(plan, world_time, agent["location"])
    perception = build_runtime_perception(conn, agent, world_time, day, slot, plan, step, observed)
    decision = build_autonomous_tick_decision(conn, agent, perception, step)
    decision = apply_realism_constraints_to_decision(conn, agent, decision, perception, world_time)
    decision = apply_wellbeing_priority_to_decision(conn, agent, decision, world_time)
    action = str(decision.get("action") or "observe")
    destination = str(decision.get("location") or agent["location"])
    goal = str(decision.get("goal") or plan.get("intent") or "观察校园环境")
    destination_actions = {
        "attend_class",
        "queue",
        "consume",
        "rest",
        "club_activity",
        "request_leave",
        "collaborate",
        "conflict",
        "late",
    }
    spatial_context = get_current_spatial_action_context(conn, agent["id"], action)
    event_location = spatial_context["node_name"] if spatial_context else destination
    if (
        action in destination_actions
        and destination in VALID_LOCATIONS
        and destination != agent["location"]
        and not spatial_context
        and spatial_runtime_available(conn)
    ):
        decision["deferred_action"] = action
        decision["reason"] = (
            f"先前往{destination}，到达后再执行 {action}。"
        )
        action = "move"
        decision["action"] = action
        event_location = destination
    title = f"{agent['name']}正在{event_location}行动"

    try:
        action_execution = begin_world_action_execution(
            conn,
            agent["id"],
            action,
            destination,
            world_time,
            tick_id=tick_id,
            parent_event_id=parent_event_id,
            # A meal at a confirmed real-world canteen is a real recovery
            # action, even when it was initiated by an otherwise passive
            # runtime poll.  Passive settlement intentionally has no body
            # effects, which previously left residents at hunger=100 forever.
            settlement_mode=(
                "active"
                if step.get("plan_state") == "due" or (spatial_context and action == "consume")
                else "passive"
            ),
        )
        if action_execution["status"] in {"rejected", "failed"}:
            if action_execution["status"] == "failed":
                settlement = settle_world_action_resources(conn, action_execution, success=False)
                event_type = "agent_action_failed"
                title = f"{agent['name']}的行动未成功"
            else:
                settlement = finalize_rejected_action_execution(conn, action_execution)
                event_type = "agent_action_rejected"
                title = f"{agent['name']}的行动条件不足"
            content = (
                f"{agent['name']}未能执行 {action}：{action_execution['failure_reason']}。"
                "本次结算保留了结构化失败原因，Agent 可在后续 tick 选择替代行动。"
            )
            execution = {
                "action": action,
                "result": {"description": content},
                "success": False,
                "failure_code": action_execution["failure_code"],
                "causal_settlement": settlement,
                "plan_step": step,
                "runtime_decision": decision,
            }
            state_after = get_agent_module_state(conn, agent["id"])
            event = append_world_event(
                conn,
                event_type,
                title,
                content,
                tick_id=tick_id,
                resident_id=agent["id"],
                location=(
                    event_location
                    if spatial_context or destination in VALID_LOCATIONS
                    else agent["location"]
                ),
                payload={
                    "action": action,
                    "goal": goal,
                    "failure_code": action_execution["failure_code"],
                    "failure_reason": action_execution["failure_reason"],
                    "preconditions": action_execution["preconditions"],
                    "action_execution_id": action_execution["id"],
                    "causal_settlement": settlement,
                },
                day=day,
                slot=slot,
                source_type="world_action_execution",
                source_id=action_execution["id"],
                parent_event_id=parent_event_id,
                rule_version=action_execution["rule"]["rule_version"],
            )
            link_action_execution_event(conn, action_execution["id"], event["id"])
            record_simulation_log(
                conn,
                agent["id"],
                perception,
                {
                    "decision": {
                        "action": action,
                        "reason": decision.get("reason") or goal,
                        "tool_input": {"destination": destination},
                    },
                    "memory_context": {"memories": []},
                },
                execution,
                {},
                state_before,
                state_after,
                tick_id=tick_id,
            )
            add_memory_once(
                conn,
                agent["id"],
                day,
                content,
                importance=2,
                source="world_action_settlement",
            )
            conn.commit()
            return {
                "resident_id": agent["id"],
                "success": True,
                "action_success": False,
                "event": event,
                "action_execution_id": action_execution["id"],
                "plan_outcome": None,
            }

        if action == "move" and destination in VALID_LOCATIONS and destination != agent["location"]:
            result = move_resident(conn, agent["id"], destination, commit=False)
            content = result["description"]
        elif action in destination_actions and destination in VALID_LOCATIONS and destination != agent["location"]:
            move_resident(conn, agent["id"], destination, commit=False)
            agent = dict(agent)
            agent["location"] = destination
            content, _, social_effect = describe_runtime_action(conn, agent, action, event_location, goal, day, observed=observed)
        else:
            content, _, social_effect = describe_runtime_action(conn, agent, action, destination, goal, day, observed=observed)
        execution = {"action": action, "result": {"description": content}, "success": True, "plan_step": step, "runtime_decision": decision}
        if "social_effect" in locals() and social_effect:
            execution["social_effect"] = social_effect
        settlement = settle_world_action_resources(conn, action_execution, success=True)
        execution["causal_settlement"] = settlement
        state_after = get_agent_module_state(conn, agent["id"])
        conn.execute(
            """
            UPDATE agent_profiles
            SET current_task = ?, perception = ?
            WHERE resident_id = ?
            """,
            (goal[:120], json_dumps(perception, ensure_ascii=False), agent["id"]),
        )
        event = append_world_event(
            conn,
            "agent_tick",
            title,
            content,
            tick_id=tick_id,
            resident_id=agent["id"],
            location=(
                event_location
                if spatial_context or destination in VALID_LOCATIONS
                else agent["location"]
            ),
            payload={
                "action": action,
                "goal": goal,
                "observed": observed,
                "plan_step": step,
                "goal_chain": plan.get("goal_chain", {}),
                "runtime_decision": decision,
                "social_effect": execution.get("social_effect"),
                "action_taxonomy": "world-runtime-v3",
                "action_execution_id": action_execution["id"],
                "preconditions": action_execution["preconditions"],
                "causal_settlement": settlement,
            },
            day=day,
            slot=slot,
            source_type="world_action_execution",
            source_id=action_execution["id"],
            parent_event_id=parent_event_id,
            rule_version=action_execution["rule"]["rule_version"],
        )
        link_action_execution_event(conn, action_execution["id"], event["id"])
        delayed_effect_ids = enqueue_world_delayed_effects(
            conn,
            action_execution,
            event["id"],
            world_time,
        )
        execution["delayed_effect_ids"] = delayed_effect_ids
        record_simulation_log(
            conn,
            agent["id"],
            perception,
            {
                "decision": {
                    "action": action,
                    "reason": decision.get("reason") or goal,
                    "tool_input": {"destination": destination},
                },
                "memory_context": {"memories": []},
            },
            execution,
            {},
            state_before,
            state_after,
            tick_id=tick_id,
        )
        plan_outcome = record_plan_outcome(
            conn,
            agent,
            plan,
            step,
            decision,
            action,
            destination,
            content,
            world_time,
            tick_id,
            day,
            event["id"],
        )
        if step.get("plan_state") == "due":
            mark_plan_step_executed(conn, plan, step, world_time, {"action": action, "location": destination, "goal": goal, "mode": decision.get("mode")})
        if observed:
            generate_observed_agent_detail(conn, agent, step, world_time, tick_id, event, day, slot)
        conn.commit()
        return {"resident_id": agent["id"], "success": True, "event": event, "plan_outcome": plan_outcome}
    except Exception as exc:
        conn.rollback()
        error_content = f"{agent['name']} 的 world tick 行动失败，已保留状态：{type(exc).__name__}。"
        event = append_world_event(
            conn,
            "agent_tick_failed",
            "Agent tick 失败",
            error_content,
            tick_id=tick_id,
            resident_id=agent["id"],
            location=agent["location"],
            payload={"error": str(exc), "action": action, "goal": goal},
            day=day,
            slot=slot,
            source_type="agent_action",
            source_id=agent["id"],
            parent_event_id=parent_event_id,
            rule_version="world-runtime-v3",
        )
        conn.commit()
        return {"resident_id": agent["id"], "success": False, "event": event, "error": str(exc)}
