"""Atomic Action Runtime & Plan Orchestrator (Phase 3.6A)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.spatial.affordance_service import get_spatial_affordances
from app.main import append_world_event


ATOMIC_ACTION_TYPES = {
    "move": "移动至目标节点",
    "enter": "进入空间门禁与检查",
    "wait": "排队等待空间余量",
    "observe": "感知周围环境与事件",
    "rest": "生理与精力恢复",
    "consume": "商品与餐饮消费",
    "hydrate": "饮水补给",
    "use_facility": "使用空间设施或资源",
    "socialize": "与其他 Agent 社交互动",
}


def recovery_service_status(conn, target_node_id, affordance_key):
    """Check that a physical recovery action has a real, usable provider.

    Imported OSM buildings often have only an affordance record, whereas the
    seeded campus also has spatial resources and supply inventory.  Absence of
    optional supply tables must not make a real imported canteen unavailable;
    but an explicitly closed service, depleted resource, or known empty food
    stock is a genuine blocking condition.
    """
    result = {"available": True, "reason": "", "quality": 78}
    try:
        aff = conn.execute(
            "SELECT status FROM spatial_affordances WHERE node_id = ? AND affordance_key = ? LIMIT 1",
            (target_node_id, affordance_key),
        ).fetchone()
        if aff and str(aff["status"]) != "open":
            return {"available": False, "reason": "该补给服务当前关闭", "quality": 0}
    except Exception:
        return result

    try:
        resources = conn.execute(
            "SELECT resource_key, available_units, status, properties FROM spatial_resources WHERE node_id = ?",
            (target_node_id,),
        ).fetchall()
        relevant = []
        for row in resources:
            props = row["properties"]
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (TypeError, ValueError):
                    props = {}
            actions = (props or {}).get("actions") or []
            if affordance_key in actions:
                relevant.append(row)
        if relevant and all(str(row["status"]) != "available" or int(row["available_units"] or 0) <= 0 for row in relevant):
            return {"available": False, "reason": "补给设施当前无可用服务能力", "quality": 0}
        if relevant:
            capacity_ratio = max(
                min(1.0, float(row["available_units"] or 0) / max(1.0, float(row["available_units"] or 1)))
                for row in relevant
            )
            result["quality"] = round(68 + 20 * capacity_ratio)
    except Exception:
        pass

    if affordance_key != "consume":
        return result
    try:
        node = conn.execute("SELECT name FROM spatial_nodes WHERE id = ?", (target_node_id,)).fetchone()
        location = str(node["name"] if node else "食堂")
        stock = conn.execute(
            """
            SELECT COUNT(*) AS account_count, COALESCE(SUM(account.quantity_on_hand), 0) AS quantity
            FROM inventory_accounts account
            JOIN catalog_items item ON item.id = account.item_id
            WHERE item.name IN ('套餐饭', '早餐券', '食材包')
              AND account.location IN (?, '食堂')
            """,
            (location,),
        ).fetchone()
        if stock and int(stock["account_count"] or 0) and int(stock["quantity"] or 0) <= 0:
            return {"available": False, "reason": "餐饮库存已耗尽，正在等待补货", "quality": 0}
        if stock and int(stock["account_count"] or 0):
            result["quality"] = min(95, 70 + min(25, int(stock["quantity"] or 0)))
    except Exception:
        # Supply runtime is optional during phased rollout.
        pass
    return result


def create_agent_action_plan(
    conn,
    resident_id: int,
    target_affordance_key: str,
    target_node_id: int,
    goal_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate a sequence of atomic steps for an agent to fulfill a target affordance."""
    # An Agent may have many historical plans, but never more than one plan
    # actively driving movement.  Leaving old plans in ``executing`` made
    # later ticks pick a different target and caused food/dorm oscillation.
    conn.execute(
        """
        UPDATE agent_action_plans
        SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ? AND status = 'executing'
        """,
        (resident_id,),
    )
    spatial_state = conn.execute(
        "SELECT current_node_id FROM agent_spatial_states WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    current_node_id = int(spatial_state["current_node_id"]) if spatial_state else 1

    target_node = conn.execute(
        "SELECT id, name, capacity, status FROM spatial_nodes WHERE id = ?",
        (target_node_id,),
    ).fetchone()
    target_node_name = target_node["name"] if target_node else "目标地点"

    steps = []

    if current_node_id != target_node_id:
        steps.append({
            "action": "move",
            "target_node_id": target_node_id,
            "target_node_name": target_node_name,
            "goal": f"前往{target_node_name}",
            "expected_cost": {"energy": 5, "time_budget": 10},
            "preconditions": ["path_exists"],
            "fallback": "observe",
        })

    node_capacity = target_node["capacity"] if target_node else 50
    node_status = target_node["status"] if target_node else "open"

    if node_status != "open":
        steps.append({
            "action": "wait",
            "target_node_id": target_node_id,
            "target_node_name": target_node_name,
            "goal": f"等待{target_node_name}开放",
            "expected_cost": {"time_budget": 15},
            "preconditions": [],
            "fallback": "observe",
        })

    steps.append({
        "action": "enter",
        "target_node_id": target_node_id,
        "target_node_name": target_node_name,
        "goal": f"进入{target_node_name}",
        "expected_cost": {"energy": 1},
        "preconditions": ["at_location", "location_open"],
        "fallback": "wait",
    })

    if target_affordance_key in ("consume", "hydrate", "rest", "use_facility", "socialize", "observe"):
        action_name = ATOMIC_ACTION_TYPES.get(target_affordance_key, target_affordance_key)
        steps.append({
            "action": target_affordance_key,
            "target_node_id": target_node_id,
            "target_node_name": target_node_name,
            "goal": f"在{target_node_name}执行{action_name}",
            "expected_cost": {"energy": 5, "time_budget": 20, "money": 15 if target_affordance_key == "consume" else 0},
            "preconditions": ["inside_location"],
            "fallback": "rest",
        })
    else:
        steps.append({
            "action": "use_facility",
            "target_node_id": target_node_id,
            "target_node_name": target_node_name,
            "goal": f"在{target_node_name}完成{target_affordance_key}",
            "expected_cost": {"energy": 10, "time_budget": 30},
            "preconditions": ["inside_location"],
            "fallback": "observe",
        })

    steps.append({
        "action": "rest",
        "target_node_id": target_node_id,
        "target_node_name": target_node_name,
        "goal": "短暂休整",
        "expected_cost": {"energy": -10},
        "preconditions": [],
        "fallback": None,
    })

    win_start = f"atomic_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    cursor = conn.execute(
        """
        INSERT INTO agent_action_plans
        (resident_id, goal_id, status, target_affordance_key, target_node_id, current_step_index, steps_json,
         window_start, window_end, plan_json, model_name, prompt_version)
        VALUES (?, ?, 'executing', ?, ?, 0, ?, ?, '24:00', '{}', 'atomic-plan-v1', 'atomic-v1')
        """,
        (
            resident_id,
            goal_id,
            target_affordance_key,
            target_node_id,
            json.dumps(steps, ensure_ascii=False),
            win_start,
        ),
    )
    plan_id = cursor.lastrowid



    return {
        "id": plan_id,
        "resident_id": resident_id,
        "goal_id": goal_id,
        "status": "executing",
        "target_affordance_key": target_affordance_key,
        "target_node_id": target_node_id,
        "current_step_index": 0,
        "steps": steps,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }


def execute_next_atomic_step(conn, resident_id: int, world_time=None) -> Dict[str, Any]:
    """Execute the current step in the active action plan for resident_id."""
    plan_row = conn.execute(
        "SELECT * FROM agent_action_plans WHERE resident_id = ? AND status = 'executing' ORDER BY id DESC LIMIT 1",
        (resident_id,),
    ).fetchone()

    if not plan_row:
        return {
            "status": "no_active_plan",
            "resident_id": resident_id,
            "action": "observe",
            "message": "当前没有在执行中的原子行动计划，执行默认观察",
        }

    plan_id = int(plan_row["id"])
    step_idx = int(plan_row["current_step_index"])
    steps = json.loads(plan_row["steps_json"]) if isinstance(plan_row["steps_json"], str) else plan_row["steps_json"]

    if step_idx >= len(steps):
        conn.execute("UPDATE agent_action_plans SET status = 'completed' WHERE id = ?", (plan_id,))
        return {
            "status": "completed",
            "plan_id": plan_id,
            "resident_id": resident_id,
            "message": "所有原子步骤已顺利完成",
        }

    current_step = steps[step_idx]
    action_type = current_step["action"]
    target_node_id = current_step.get("target_node_id")

    node = conn.execute("SELECT name FROM spatial_nodes WHERE id = ?", (target_node_id,)).fetchone() if target_node_id else None
    location_name = node["name"] if node else "当前节点"

    success = True
    failure_reason = ""

    from app.spatial.runtime import spatial_runtime_available, start_spatial_movement, _check_destination_admission
    from app.world_runtime.clock import get_world_now

    now = world_time or get_world_now()

    service_status = None
    if action_type in {"consume", "hydrate"} and target_node_id:
        service_status = recovery_service_status(conn, target_node_id, action_type)
        if not service_status["available"]:
            success = False
            failure_reason = service_status["reason"]

    # Handle 'move' step via SpatialRuntime without direct teleportation
    if action_type == "move" and target_node_id and spatial_runtime_available(conn):
        spatial_state = conn.execute(
            "SELECT current_node_id, target_node_id, movement_status, progress FROM agent_spatial_states WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()

        if spatial_state:
            curr_id = int(spatial_state["current_node_id"])
            move_status = str(spatial_state["movement_status"])

            # Check if agent has already arrived at target node
            if curr_id == int(target_node_id) and move_status in ("idle", "arrived"):
                # Move step complete! Advance plan step
                next_step_idx = step_idx + 1
                new_status = "completed" if next_step_idx >= len(steps) else "executing"
                conn.execute(
                    "UPDATE agent_action_plans SET current_step_index = ?, status = ? WHERE id = ?",
                    (next_step_idx, new_status, plan_id),
                )
                return {
                    "status": "step_executed",
                    "plan_id": plan_id,
                    "step_index": step_idx,
                    "action": "move",
                    "location": location_name,
                    "success": True,
                    "message": f"Agent 已抵达 {location_name}",
                }

            # If agent is actively moving, wait for movement progress via world tick
            if move_status in ("moving", "replanning"):
                prog = float(spatial_state["progress"] or 0)
                return {
                    "status": "moving",
                    "plan_id": plan_id,
                    "step_index": step_idx,
                    "action": "move",
                    "location": location_name,
                    "success": True,
                    "message": f"Agent 正在移动前往 {location_name}（进度 {prog * 100:.1f}%）",
                }

        # Start movement using spatial route planner
        try:
            target_node_row = conn.execute("SELECT * FROM spatial_nodes WHERE id = ?", (target_node_id,)).fetchone()
            if not target_node_row:
                raise ValueError(f"目标空间节点 {target_node_id} 不存在")
            # Plans already carry an exact spatial node. Passing its ID avoids
            # resolving a duplicate imported building name to another node.
            target_dest = str(target_node_id)
            start_res = start_spatial_movement(conn, resident_id, target_dest, world_time=now)
            if start_res and start_res.get("movement_status") == "idle":
                # Already at destination
                next_step_idx = step_idx + 1
                new_status = "completed" if next_step_idx >= len(steps) else "executing"
                conn.execute(
                    "UPDATE agent_action_plans SET current_step_index = ?, status = ? WHERE id = ?",
                    (next_step_idx, new_status, plan_id),
                )
                return {
                    "status": "step_executed",
                    "plan_id": plan_id,
                    "step_index": step_idx,
                    "action": "move",
                    "location": location_name,
                    "success": True,
                }
            return {
                "status": "movement_started",
                "plan_id": plan_id,
                "step_index": step_idx,
                "action": "move",
                "location": location_name,
                "success": True,
                "message": f"Agent 已启动前往 {location_name} 的 A* 移动路线",
            }
        except Exception as err:
            fail_reason = f"无法规划前往 {location_name} 的路径：{str(err)}"
            conn.execute(
                "UPDATE agent_action_plans SET status = 'failed' WHERE id = ?",
                (plan_id,),
            )
            append_world_event(
                conn,
                event_type="atomic_action_failed",
                title="原子行动失败:路径无法规划",
                content=fail_reason,
                resident_id=resident_id,
                location=location_name,
                payload={"plan_id": plan_id, "action": "move", "target_node_id": target_node_id, "error": str(err)},
                source_type="atomic_action",
            )
            return {
                "status": "plan_failed",
                "plan_id": plan_id,
                "step_index": step_idx,
                "action": "move",
                "location": location_name,
                "success": False,
                "failure_reason": fail_reason,
            }

    # Handle 'enter' step admission checks
    if action_type == "enter" and target_node_id and spatial_runtime_available(conn):
        target_node_row = conn.execute("SELECT * FROM spatial_nodes WHERE id = ?", (target_node_id,)).fetchone()
        if target_node_row:
            node_dict = dict(target_node_row)
            if isinstance(node_dict.get("properties"), str):
                try:
                    node_dict["properties"] = json.loads(node_dict["properties"])
                except Exception:
                    node_dict["properties"] = {}
            admission = _check_destination_admission(conn, node_dict, now, resident_id)
            if not admission.get("allowed", True):
                success = False
                failure_reason = admission.get("reason", f"{location_name} 限制进入")

    res_row = conn.execute("SELECT money FROM residents WHERE id = ?", (resident_id,)).fetchone()
    money = int(res_row["money"]) if res_row and res_row["money"] is not None else 0

    prof_row = conn.execute("SELECT energy FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    energy = int(prof_row["energy"]) if prof_row and prof_row["energy"] is not None else 50

    body = conn.execute("SELECT hunger, fatigue, sleep_debt FROM agent_body_states WHERE resident_id = ?", (resident_id,)).fetchone()

    cost_energy = current_step.get("expected_cost", {}).get("energy", 0)
    cost_money = current_step.get("expected_cost", {}).get("money", 0)

    if money < cost_money:
        success = False
        failure_reason = f"资金不足：需 {cost_money} 元，持有 {money} 元"

    if success:
        new_money = max(0, money - cost_money)
        conn.execute("UPDATE residents SET money = ? WHERE id = ?", (new_money, resident_id))
        # Body state is the only energy truth.  Updating agent_profiles first
        # and body state separately caused a later tick/profile read to mask
        # a completed meal with a stale value.  Let the common body runtime
        # apply effects and synchronise the derived profile energy once.
        if body and action_type in ("consume", "hydrate", "rest", "use_facility"):
            from app.body_runtime import apply_action_body_effects
            apply_action_body_effects(
                conn,
                resident_id,
                action_type,
                success=True,
                recovery_quality=(service_status or {}).get("quality"),
            )
        elif prof_row:
            # Non-body atomic steps (enter/observe/etc.) retain their explicit
            # lightweight cost; recovery actions never write a competing value.
            new_energy = min(100, max(0, energy - cost_energy))
            conn.execute("UPDATE agent_profiles SET energy = ? WHERE resident_id = ?", (new_energy, resident_id))

        next_step_idx = step_idx + 1
        new_status = "completed" if next_step_idx >= len(steps) else "executing"
        conn.execute(
            "UPDATE agent_action_plans SET current_step_index = ?, status = ? WHERE id = ?",
            (next_step_idx, new_status, plan_id),
        )

    event_content = f"原子行动【{action_type}】于 {location_name} " + ("成功完成" if success else f"失败: {failure_reason}")
    append_world_event(
        conn,
        event_type="atomic_action_execution",
        title=f"原子行动:{action_type}",
        content=event_content,
        resident_id=resident_id,
        location=location_name,
        payload={
            "plan_id": plan_id,
            "action": action_type,
            "target_node_id": target_node_id,
            "step_index": step_idx,
            "success": success,
            "failure_reason": failure_reason,
            "cost": current_step.get("expected_cost", {}),
        },
        source_type="atomic_action",
    )

    return {
        "status": "step_executed",
        "plan_id": plan_id,
        "step_index": step_idx,
        "action": action_type,
        "location": location_name,
        "success": success,
        "failure_reason": failure_reason,
    }


def get_agent_active_plan(conn, resident_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM agent_action_plans WHERE resident_id = ? ORDER BY id DESC LIMIT 1",
        (resident_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    if isinstance(item.get("steps_json"), str):
        item["steps"] = json.loads(item["steps_json"])
    else:
        item["steps"] = item.get("steps_json", [])
    return item


def process_atomic_action_plan_for_agent_tick(conn, agent: Dict[str, Any], world_time=None) -> Dict[str, Any]:
    """Autonomous affordance discovery and atomic plan execution for an agent during world tick."""
    resident_id = int(agent["id"])
    active_plan = get_agent_active_plan(conn, resident_id)

    body_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(agent_body_states)").fetchall()
    }
    hydration_column = "hydration" if "hydration" in body_columns else "0 AS hydration"
    body_row = conn.execute(
        """
        SELECT hunger, fatigue, sleep_debt, health, %s
        FROM agent_body_states WHERE resident_id = ?
        """ % hydration_column,
        (resident_id,),
    ).fetchone()
    body = dict(body_row) if body_row else {}
    hunger = float(body.get("hunger") or 0.0)
    fatigue = float(body.get("fatigue") or 0.0)
    sleep_debt = float(body.get("sleep_debt") or 0.0)
    health = float(body.get("health") if body.get("health") is not None else 100.0)
    hydration = float(body.get("hydration") if body.get("hydration") is not None else 0.0)
    # Begin a normal meal before the alert threshold (80) is reached.  At
    # night, defer ordinary food-seeking until hunger is more urgent: real
    # dining facilities may be closed and sleep is the safer recovery action.
    hour = getattr(world_time, "hour", None)
    meal_window = hour is None or 6 <= int(hour) < 22
    recovery_affordance = (
        "hydrate" if hydration >= 70
        else "consume" if hunger >= (60 if meal_window else 78)
        else "rest" if fatigue >= 88 or sleep_debt >= 85 or health < 35
        else None
    )

    # A non-food plan must not keep an acutely hungry Agent trapped in a
    # low-priority observe/rest loop. Cancel it before it produces further
    # steps, then let the essential-food search create a reachable meal plan.
    if (
        active_plan
        and active_plan.get("status") == "executing"
        and recovery_affordance
        and active_plan.get("target_affordance_key") != recovery_affordance
    ):
        conn.execute(
            "UPDATE agent_action_plans SET status = 'failed' WHERE id = ?",
            (active_plan["id"],),
        )
        append_world_event(
            conn,
            event_type="atomic_action_reprioritized",
            title="原子行动改为优先补充食物",
            content=f"身体状态达到风险阈值，原计划已让位给可达{recovery_affordance}设施搜索。",
            resident_id=resident_id,
            payload={
                "replaced_plan_id": active_plan["id"],
                "recovery_affordance": recovery_affordance,
                "hunger": hunger,
                "fatigue": fatigue,
                "sleep_debt": sleep_debt,
                "health": health,
                "hydration": hydration,
            },
            source_type="atomic_action",
        )
        active_plan = None

    # Atomic affordances are a physical execution mechanism for recovery,
    # rather than the top-level daily planner.  At normal body values, defer
    # to role schedules and multiscale goals (class, work, library, sport,
    # social plans) so the nearest canteen/dorm cannot dominate all behavior.
    if not recovery_affordance:
        if active_plan and active_plan.get("status") == "executing":
            conn.execute(
                """
                UPDATE agent_action_plans
                SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
                WHERE resident_id = ? AND status = 'executing'
                """,
                (resident_id,),
            )
        return {
            "status": "no_recovery_required",
            "resident_id": resident_id,
            "message": "身体状态稳定，交由日程、角色与长期目标决定下一处地点。",
        }

    if not active_plan or active_plan.get("status") in ("completed", "failed"):
        from app.spatial.affordance_service import discover_agent_affordance_opportunities
        opps_res = discover_agent_affordance_opportunities(conn, resident_id)
        available_opps = [o for o in opps_res.get("opportunities", []) if o.get("is_available")]
        if available_opps:
            # Recovery only considers the matching affordance.  A hungry
            # Agent seeks food; an exhausted/ill Agent seeks rest.
            available_opps = [
                opportunity
                for opportunity in available_opps
                if opportunity.get("affordance_key") == recovery_affordance
            ]
            available_opps.sort(
                key=lambda o: (
                    not o.get("is_current_node"),
                    o.get("distance_meters", 0),
                )
            )
            selected = available_opps[0]
            active_plan = create_agent_action_plan(
                conn,
                resident_id=resident_id,
                target_affordance_key=selected["affordance_key"],
                target_node_id=selected["node_id"],
            )

    if active_plan and active_plan.get("status") == "executing":
        outcome = execute_next_atomic_step(conn, resident_id, world_time=world_time)
        # Entering a canteen is an administrative/physical precondition, not
        # a separate meal.  When the hungry Agent is already there, settle
        # that precondition and the meal in the same selected tick.  Movement
        # is never skipped, and this never chains non-recovery actions.
        if recovery_affordance == "consume" and outcome.get("action") == "enter" and outcome.get("success"):
            next_plan = get_agent_active_plan(conn, resident_id)
            steps = (next_plan or {}).get("steps") or []
            step_index = int((next_plan or {}).get("current_step_index") or 0)
            if step_index < len(steps) and steps[step_index].get("action") == "consume":
                return execute_next_atomic_step(conn, resident_id, world_time=world_time)
        return outcome

    return {"status": "idle", "resident_id": resident_id}
