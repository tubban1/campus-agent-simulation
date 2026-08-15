"""Causal action preconditions, settlement, and delayed effects."""

_MODULE_NAME = __name__


def is_residential_rest_location(location):
    """Accept OSM apartment and dorm names for the legacy ``rest`` rule."""
    text = str(location or "").lower()
    return text == "宿舍区" or any(
        token in text for token in ("宿舍", "公寓", "住宅", "residence")
    )

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


def evaluate_world_action_preconditions(conn, resident_id, action_type, location, rule, world_time):
    preconditions = rule.get("preconditions", {})
    resources = rule.get("required_resources", {})
    state = action_resource_state(conn, resident_id)
    checks = []
    budget_choice = None
    market_choice = None
    body_state = get_body_state(conn, resident_id) or {}
    critical_recovery = (
        action_type == "consume"
        and float(body_state.get("hunger") or 0) >= 90
    ) or (
        action_type == "rest"
        and (
            float(body_state.get("fatigue") or 0) >= 88
            or float(body_state.get("sleep_debt") or 0) >= 85
            or float(body_state.get("health", 100) if body_state.get("health") is not None else 100) < 35
            or float(body_state.get("attention", 100) if body_state.get("attention") is not None else 100) < 15
        )
    )

    def add_check(key, passed, actual, required, failure_code, reason):
        checks.append(
            {
                "key": key,
                "passed": bool(passed),
                "actual": actual,
                "required": required,
                "failure_code": "" if passed else failure_code,
                "reason": "" if passed else reason,
            }
        )

    allowed_locations = preconditions.get("allowed_locations")
    if allowed_locations:
        allowed_at_location = location in allowed_locations or (
            action_type == "rest" and is_residential_rest_location(location)
        )
        add_check(
            "allowed_location",
            allowed_at_location,
            location,
            allowed_locations,
            "location_mismatch",
            f"{action_type} 不能在{location}完成",
        )
    if preconditions.get("location_open"):
        from app.spatial.location_catalog import is_real_world_location
        location_is_open = (
            action_type == "rest" and is_residential_rest_location(location)
        ) or (
            location in VALID_LOCATIONS and is_location_open_at_hour(location, world_time.hour)
        ) or (
            is_real_world_location(conn, location)
        )
        add_check(
            "location_open",
            location_is_open,
            location,
            "open",
            "location_closed",
            f"{location}当前未开放",
        )
    if preconditions.get("capacity_available"):
        snapshot = get_space_snapshot(conn)
        space = next((item for item in snapshot.get("spaces", []) if item.get("location") == location), None)
        if not space:
            matching_spaces = [
                item for item in snapshot.get("spaces", [])
                if location in item.get("location", "") or item.get("location", "") in location
            ]
            if matching_spaces:
                total_slots = sum(int(s.get("available_slots", 0)) for s in matching_spaces)
                any_open = any(s.get("effective_status") not in ("关闭", "已关闭") for s in matching_spaces)
                space = {
                    "location": location,
                    "available_slots": total_slots if any_open else 0,
                    "effective_status": "开放" if any_open else "关闭"
                }

        if not space and (location in VALID_LOCATIONS or is_residential_rest_location(location)):
            space = {"location": location, "available_slots": 100, "effective_status": "开放"}

        available = int(space.get("available_slots", 0)) if space else 0
        add_check(
            "capacity_available",
            bool(space) and available > 0 and space.get("effective_status") not in ("关闭", "已关闭"),
            available,
            "> 0",
            "space_full",
            f"{location}当前没有可用容量",
        )
    if action_type != "move" and location in VALID_LOCATIONS:
        resource = check_action_resource(conn, location, action_type)
        if resource["required"]:
            add_check(
                "spatial_resource_available",
                resource["available"],
                resource,
                "> 0 available units",
                "resource_unavailable",
                (
                    f"{location}的{resource['resource_name']}当前不可用，"
                    f"预计等待 {resource['estimated_wait_minutes']} 分钟"
                ),
            )
    if action_type == "consume" and rule.get("rule_key") != "passive-runtime-poll":
        supply = consumption_availability(conn, location)
        if supply.get("managed"):
            emergency_nutrition = (
                not supply["available"]
                and float(body_state.get("hunger") or 0) >= 75
            )
            if emergency_nutrition:
                state["emergency_nutrition"] = {
                    "reason": "food_stockout",
                    "location": location,
                    "hunger": float(body_state.get("hunger") or 0),
                    "supply": supply,
                }
            add_check(
                "supply_available",
                supply["available"] or emergency_nutrition,
                supply,
                "> 0 saleable units",
                "goods_out_of_stock",
                (
                    "库存补给中，已启用校园保障餐"
                    if emergency_nutrition
                    else f"{location}当前可消费商品缺货"
                ),
            )
            if (
                supply["available"]
                and market_runtime_available(conn)
            ):
                mechanism = find_market_mechanism(
                    conn,
                    item_name=supply["item_name"],
                    provider_actor_key=supply["provider_actor_key"],
                    location=location,
                )
                if mechanism:
                    market_choice = evaluate_market_choice(
                        conn,
                        resident_id=resident_id,
                        mechanism_id=int(mechanism["id"]),
                        action_type=action_type,
                        world_time=world_time,
                    )
                    market_allowed = market_choice["status"] == "accepted"
                    if not market_allowed and market_choice.get("substitute"):
                        sub = market_choice["substitute"]
                        sub_choice = evaluate_market_choice(
                            conn,
                            resident_id=resident_id,
                            mechanism_id=int(sub["mechanism_id"]),
                            action_type=action_type,
                            world_time=world_time,
                        )
                        if sub_choice["status"] == "accepted":
                            sub_choice["fallback_from_item"] = market_choice["item_name"]
                            sub_choice["reason"] = (
                                f"原报价 {market_choice['item_name']} 超过意愿/受限，"
                                f"同 tick 无缝降级选择替代品 {sub_choice['item_name']}"
                            )
                            market_choice = sub_choice
                            market_allowed = True

                    add_check(
                        "market_offer",
                        market_allowed,
                        market_choice,
                        "accepted market offer",
                        f"market_{market_choice['status']}",
                        (
                            market_choice["reason"]
                            + (
                                f"，可替代为{market_choice['substitute']['item_name']}"
                                if not market_allowed and market_choice.get("substitute")
                                else ""
                            )
                        ),
                    )
                    resources["money"] = int(
                        math.ceil(
                            market_choice["total_unit_cost_minor"]
                            * market_choice["quantity"]
                            / 100
                        )
                    )
    if rule.get("rule_key") != "passive-runtime-poll":
        checks.extend(body_action_checks(conn, resident_id, action_type))
        checks.extend(capability_action_checks(conn, resident_id, action_type))
        if budget_runtime_available(conn):
            profile = conn.execute(
                "SELECT resident_id FROM household_budget_profiles WHERE resident_id = ?",
                (resident_id,),
            ).fetchone()
            if profile:
                budget_choice = evaluate_action_choice(
                    conn,
                    resident_id=resident_id,
                    action_type=action_type,
                    location=location,
                    required_money_minor=int(resources.get("money", 0) or 0) * 100,
                    required_time_minutes=int(rule.get("duration_minutes", 0) or 0),
                    world_time=world_time,
                )
                add_check(
                    "budget_disposable",
                    budget_choice["decision"] != "rejected",
                    budget_choice["disposable_minor"],
                    budget_choice["required_money_minor"],
                    "insufficient_disposable_budget",
                    budget_choice["rationale"],
                )
                add_check(
                    "budget_free_time",
                    budget_choice["decision"] != "deferred" or critical_recovery,
                    budget_choice["free_time_minutes"],
                    budget_choice["required_time_minutes"],
                    "insufficient_free_time",
                    (
                        "关键生理恢复行动允许越过当日自由时间门槛"
                        if budget_choice["decision"] == "deferred" and critical_recovery
                        else budget_choice["rationale"]
                    ),
                )
    for resource_key in ("energy", "time_budget", "money"):
        required = int(resources.get(resource_key, 0) or 0)
        available = state[resource_key] >= required
        if state.get("emergency_nutrition") and resource_key in {"energy", "money"}:
            available = True
        if (
            resource_key == "money"
            and budget_choice
            and budget_choice["emergency_override"]
        ):
            available = True
        if resource_key == "time_budget" and critical_recovery:
            available = True
        add_check(
            f"resource_{resource_key}",
            available,
            state[resource_key],
            required,
            f"insufficient_{resource_key}",
            f"{resource_key}不足，需要 {required}，当前 {state[resource_key]}",
        )
    if budget_choice:
        state = {**state, "budget_choice": budget_choice}
    if market_choice:
        state = {**state, "market_choice": market_choice}
    return checks, state


def begin_world_action_execution(
    conn,
    resident_id,
    action_type,
    location,
    world_time,
    tick_id=None,
    parent_event_id=None,
    settlement_mode="active",
):
    rule = get_world_action_rule(conn, action_type)
    if not rule:
        raise ValueError(f"未找到行动规则：{action_type}")
    if settlement_mode == "passive":
        rule = {
            **rule,
            "rule_key": "passive-runtime-poll",
            "rule_version": "passive-tick-v1",
            "preconditions": {},
            "required_resources": {"energy": 0, "time_budget": 0, "money": 0},
            "duration_minutes": 0,
            "success_probability": 1.0,
            "direct_effects": [],
            "delayed_effects": [],
        }
    else:
        rule = individualize_action_rule(conn, resident_id, rule, action_type)
    checks, resources_before = evaluate_world_action_preconditions(
        conn, resident_id, action_type, location, rule, world_time
    )
    if rule.get("individualization"):
        resources_before = {
            **resources_before,
            "capability_adjustment": rule["individualization"],
        }
    failed_check = next((check for check in checks if not check["passed"]), None)
    roll = deterministic_action_roll(conn, tick_id, resident_id, action_type, location)
    probability = float(rule.get("success_probability", 1.0))
    status = "rejected" if failed_check else ("pending" if roll <= probability else "failed")
    failure_code = failed_check["failure_code"] if failed_check else ("probability_failure" if status == "failed" else "")
    failure_reason = failed_check["reason"] if failed_check else ("行动未通过成功概率结算" if status == "failed" else "")
    cursor = conn.execute(
        """
        INSERT INTO world_action_executions
        (tick_id, resident_id, action_type, target_type, target_id, location, status, settlement_mode,
         rule_key, rule_version, precondition_results_json, resources_before_json,
         resource_costs_json, duration_minutes, success_probability, random_roll,
         direct_effects_json, failure_code, failure_reason, parent_event_id, occurred_at)
        VALUES (?, ?, ?, 'location', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tick_id,
            resident_id,
            action_type,
            location,
            location,
            status,
            settlement_mode,
            rule["rule_key"],
            rule["rule_version"],
            canonical_json(checks),
            canonical_json(resources_before),
            canonical_json(rule.get("required_resources", {})),
            int(rule.get("duration_minutes", 0)),
            probability,
            roll,
            canonical_json(rule.get("direct_effects", [])),
            failure_code,
            failure_reason,
            parent_event_id,
            world_time.isoformat(),
        ),
    )
    if resources_before.get("budget_choice"):
        record_action_choice(
            conn,
            action_execution_id=cursor.lastrowid,
            resident_id=resident_id,
            action_type=action_type,
            location=location,
            evaluation=resources_before["budget_choice"],
            world_time=world_time,
        )
    if resources_before.get("market_choice"):
        record_market_demand(
            conn,
            action_execution_id=cursor.lastrowid,
            resident_id=resident_id,
            evaluation=resources_before["market_choice"],
            world_time=world_time,
        )
    return {
        "id": cursor.lastrowid,
        "status": status,
        "failure_code": failure_code,
        "failure_reason": failure_reason,
        "preconditions": checks,
        "resources_before": resources_before,
        "rule": rule,
        "random_roll": roll,
        "settlement_mode": settlement_mode,
    }


def apply_structured_world_effect(conn, resident_id, effect):
    target_type = effect.get("target_type")
    state_key = effect.get("state_key")
    operation = effect.get("operation", "add")
    value = effect.get("value")
    if target_type == "agent_profile":
        if state_key == "energy" and operation == "add":
            row = conn.execute(
                "SELECT energy FROM agent_profiles WHERE resident_id = ?",
                (resident_id,),
            ).fetchone()
            before = int(row["energy"])
            after = clamp(before + int(value))
            conn.execute(
                "UPDATE agent_profiles SET energy = ? WHERE resident_id = ?",
                (after, resident_id),
            )
        elif state_key == "mood" and operation == "set":
            row = conn.execute(
                "SELECT mood FROM agent_profiles WHERE resident_id = ?",
                (resident_id,),
            ).fetchone()
            before = row["mood"]
            after = str(value)[:40]
            conn.execute(
                "UPDATE agent_profiles SET mood = ? WHERE resident_id = ?",
                (after, resident_id),
            )
        else:
            raise ValueError(f"不支持的 Agent 效果：{state_key}/{operation}")
    elif target_type == "campus_state":
        allowed_numeric = set(ENV_COLUMN_TYPES) & {
            "exam_pressure", "assignment_pressure", "study_atmosphere", "activity_heat",
            "event_intensity", "campus_flow", "classroom_crowd", "canteen_crowd",
            "library_crowd", "dorm_crowd", "playground_crowd", "commercial_crowd",
            "safety_level", "resource_pressure", "consumption_index",
        }
        if state_key not in allowed_numeric or operation not in {"add", "set"}:
            raise ValueError(f"不支持的校园效果：{state_key}/{operation}")
        day = get_current_day(conn)
        state = conn.execute(
            f"SELECT {state_key} AS value FROM campus_state WHERE day = ?",
            (day,),
        ).fetchone()
        if not state:
            raise ValueError(f"第 {day} 天校园状态不存在")
        before = state["value"]
        raw_after = float(before) + float(value) if operation == "add" else float(value)
        if state_key == "consumption_index":
            after = round(max(0.1, min(3.0, raw_after)), 2)
        else:
            after = clamp(round(raw_after))
        conn.execute(
            f"UPDATE campus_state SET {state_key} = ? WHERE day = ?",
            (after, day),
        )
    else:
        raise ValueError(f"不支持的效果目标：{target_type}")
    return {
        "target_type": target_type,
        "state_key": state_key,
        "operation": operation,
        "before": before,
        "after": after,
    }


def settle_world_action_resources(conn, action_execution, success):
    resident_id = action_execution["resources_before"].get("resident_id")
    if not resident_id:
        row = conn.execute(
            "SELECT resident_id FROM world_action_executions WHERE id = ?",
            (action_execution["id"],),
        ).fetchone()
        resident_id = int(row["resident_id"])
    rule = action_execution["rule"]
    requested_costs = {
        key: int(rule.get("required_resources", {}).get(key, 0) or 0)
        for key in ("energy", "time_budget", "money")
    }
    ratio = 1.0 if success else float(rule.get("failure_policy", {}).get("probability_failure_cost_ratio", 0.5))
    costs = {
        key: min(value, max(0, round(value * ratio)))
        for key, value in requested_costs.items()
    }
    if success and action_execution["resources_before"].get("emergency_nutrition"):
        costs["energy"] = 0
        costs["money"] = 0
    exact_money_minor = costs["money"] * 100
    if action_execution["resources_before"].get("market_choice"):
        market_choice = action_execution["resources_before"]["market_choice"]
        exact_money_minor = (
            int(market_choice["total_unit_cost_minor"])
            * int(market_choice["quantity"])
        )
    if costs["money"] and success and action_execution["resources_before"].get("budget_choice"):
        fund_emergency_action(
            conn,
            resident_id=resident_id,
            amount_minor=exact_money_minor,
            action_execution_id=action_execution["id"],
            evaluation=action_execution["resources_before"]["budget_choice"],
        )
    before = action_resource_state(conn, resident_id)
    conn.execute(
        """
        UPDATE agent_profiles
        SET energy = ?, time_budget = ?
        WHERE resident_id = ?
        """,
        (
            clamp(before["energy"] - costs["energy"]),
            clamp(before["time_budget"] - costs["time_budget"]),
            resident_id,
        ),
    )
    supply_settlement = None
    if (
        costs["money"]
        and success
        and rule["action_type"] == "consume"
        and supply_runtime_available(conn)
        and action_execution["resources_before"].get("market_choice")
    ):
        supply_settlement = fulfill_market_goods_trade(
            conn,
            resident_id=resident_id,
            evaluation=action_execution["resources_before"]["market_choice"],
            action_execution_id=action_execution["id"],
        )
        ledger_transaction = {"id": supply_settlement["ledger_transaction_id"]}
        transfer_target = supply_settlement["provider_actor_key"]
    elif (
        costs["money"]
        and success
        and rule["action_type"] == "consume"
        and supply_runtime_available(conn)
    ):
        execution_row = conn.execute(
            "SELECT location FROM world_action_executions WHERE id = ?",
            (action_execution["id"],),
        ).fetchone()
        supply_settlement = fulfill_runtime_consumption(
            conn,
            resident_id,
            execution_row["location"],
            costs["money"] * 100,
            action_execution["id"],
        )
        ledger_transaction = {"id": supply_settlement["ledger_transaction_id"]}
        transfer_target = supply_settlement["provider_actor_key"]
    elif costs["money"]:
        ledger_transaction = post_money_transfer(
            conn,
            transaction_key=f"action-cost:{action_execution['id']}:money",
            from_account_key=f"resident:{resident_id}:cash",
            to_account_key="system:campus-services:cash",
            amount_coins=costs["money"],
            transaction_type="action_resource_cost",
            source_type="world_action_execution",
            source_id=str(action_execution["id"]),
            action_execution_id=action_execution["id"],
            description=f"{rule['action_type']} 行动资源成本",
            metadata={"success": bool(success)},
        )
        transfer_target = "campus-services"
    else:
        ledger_transaction = None
        transfer_target = ""
    if costs["money"]:
        conn.execute(
            """
            INSERT INTO world_resource_transfers
            (action_execution_id, from_type, from_id, to_account_key,
             resource_type, amount, reason)
            VALUES (?, 'resident', ?, ?, 'money', ?, ?)
            """,
            (
                action_execution["id"],
                str(resident_id),
                transfer_target,
                costs["money"],
                f"{rule['action_type']} 行动资源成本",
            ),
        )
    applied_effects = []
    if success:
        for effect in rule.get("direct_effects", []):
            applied_effects.append(apply_structured_world_effect(conn, resident_id, effect))
    body_effects = None
    if action_execution.get("settlement_mode") != "passive":
        body_effects = apply_action_body_effects(
            conn,
            resident_id,
            rule["action_type"],
            success=success,
        )
        if body_effects:
            applied_effects.append(
                {
                    "target_type": "agent_body_state",
                    "state_key": "body",
                    "operation": "transition",
                    "before": body_effects["before"],
                    "after": body_effects["after"],
                }
            )
    after = action_resource_state(conn, resident_id)
    conn.execute(
        """
        UPDATE world_action_executions
        SET status = ?, resources_after_json = ?, resource_costs_json = ?,
            direct_effects_json = ?, completed_at = ?
        WHERE id = ?
        """,
        (
            "completed" if success else "failed",
            canonical_json(after),
            canonical_json(costs),
            canonical_json(applied_effects),
            get_world_now().isoformat(),
            action_execution["id"],
        ),
    )
    return {
        "before": before,
        "after": after,
        "costs": costs,
        "direct_effects": applied_effects,
        "body_effects": body_effects,
        "supply_settlement": supply_settlement,
        "ledger_transaction_id": (
            ledger_transaction["id"] if ledger_transaction else None
        ),
    }


def finalize_rejected_action_execution(conn, action_execution):
    conn.execute(
        """
        UPDATE world_action_executions
        SET resources_after_json = ?, resource_costs_json = '{}',
            direct_effects_json = '[]', completed_at = ?
        WHERE id = ?
        """,
        (
            canonical_json(action_execution["resources_before"]),
            get_world_now().isoformat(),
            action_execution["id"],
        ),
    )
    return {
        "before": action_execution["resources_before"],
        "after": action_execution["resources_before"],
        "costs": {"energy": 0, "time_budget": 0, "money": 0},
        "direct_effects": [],
    }


def enqueue_world_delayed_effects(conn, action_execution, source_event_id, world_time):
    effect_ids = []
    resident_id = conn.execute(
        "SELECT resident_id FROM world_action_executions WHERE id = ?",
        (action_execution["id"],),
    ).fetchone()["resident_id"]
    for effect in action_execution["rule"].get("delayed_effects", []):
        due_at = world_time + timedelta(minutes=max(0, int(effect.get("delay_minutes", 0))))
        target_type = effect.get("target_type") or "campus_state"
        target_id = effect.get("target_id")
        if target_id is None and target_type == "agent_profile":
            target_id = resident_id
        cursor = conn.execute(
            """
            INSERT INTO world_delayed_effects
            (source_action_execution_id, source_event_id, due_at, effect_type,
             target_type, target_id, state_key, operation, value_json, rule_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_execution["id"],
                source_event_id,
                due_at.isoformat(),
                f"{target_type}.{effect.get('state_key')}",
                target_type,
                str(target_id or ""),
                effect.get("state_key") or "",
                effect.get("operation") or "add",
                canonical_json(effect.get("value")),
                action_execution["rule"]["rule_version"],
            ),
        )
        effect_ids.append(cursor.lastrowid)
    conn.execute(
        "UPDATE world_action_executions SET delayed_effect_ids_json = ? WHERE id = ?",
        (canonical_json(effect_ids), action_execution["id"]),
    )
    return effect_ids


def link_action_execution_event(conn, action_execution_id, event_id):
    conn.execute(
        "UPDATE world_action_executions SET world_event_id = ? WHERE id = ?",
        (event_id, action_execution_id),
    )


def process_due_world_delayed_effects(conn, world_time, tick_id=None, day=None, slot=None, limit=100):
    rows = conn.execute(
        """
        SELECT d.*, a.resident_id
        FROM world_delayed_effects d
        LEFT JOIN world_action_executions a ON a.id = d.source_action_execution_id
        WHERE d.status = 'pending' AND d.due_at <= ?
        ORDER BY d.due_at, d.id
        LIMIT ?
        """,
        (world_time.isoformat(), limit),
    ).fetchall()
    if any(row["target_type"] == "campus_state" for row in rows):
        get_campus_environment(conn, day)
    applied = []
    failed = []
    for raw in rows:
        effect = dict(raw)
        conn.execute("SAVEPOINT delayed_effect_apply")
        try:
            value = load_json_text(effect["value_json"], None)
            result = apply_structured_world_effect(
                conn,
                int(effect.get("target_id") or effect.get("resident_id") or 0),
                {
                    "target_type": effect["target_type"],
                    "state_key": effect["state_key"],
                    "operation": effect["operation"],
                    "value": value,
                },
            )
            event = append_world_event(
                conn,
                "delayed_effect_applied",
                "延迟效果已结算",
                f"{effect['effect_type']} 已按计划生效。",
                tick_id=tick_id,
                resident_id=effect.get("resident_id"),
                payload={"delayed_effect_id": effect["id"], "result": result},
                day=day,
                slot=slot,
                source_type="delayed_effect",
                source_id=effect["id"],
                parent_event_id=effect.get("source_event_id"),
                rule_version=effect["rule_version"],
                occurred_at=world_time.isoformat(),
            )
            conn.execute(
                """
                UPDATE world_delayed_effects
                SET status = 'applied', attempts = attempts + 1,
                    applied_event_id = ?, applied_at = ?, last_error = ''
                WHERE id = ?
                """,
                (event["id"], world_time.isoformat(), effect["id"]),
            )
            conn.execute("RELEASE SAVEPOINT delayed_effect_apply")
            applied.append({"id": effect["id"], "event_id": event["id"], "result": result})
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT delayed_effect_apply")
            conn.execute("RELEASE SAVEPOINT delayed_effect_apply")
            attempts = int(effect.get("attempts") or 0) + 1
            conn.execute(
                """
                UPDATE world_delayed_effects
                SET status = ?, attempts = ?, last_error = ?
                WHERE id = ?
                """,
                ("failed" if attempts >= 3 else "pending", attempts, str(exc)[:240], effect["id"]),
            )
            failed.append({"id": effect["id"], "error": str(exc)})
    return {"due_count": len(rows), "applied": applied, "failed": failed}


def decode_world_update_schedule(row):
    item = dict(row)
    item["metadata"] = load_json_text(item.pop("metadata_json", "{}"), {})
    return item


def decode_world_update_run(row):
    item = dict(row)
    item["metrics"] = load_json_text(item.pop("metrics_json", "{}"), {})
    return item


def world_events_for_update(conn, after_id, through_id):
    branch_key = active_world_branch_key(conn)
    events = []
    for row in conn.execute(
            """
            SELECT * FROM world_event_stream
            WHERE id > ? AND id <= ? AND branch_key = ?
            ORDER BY id
            """,
            (after_id, through_id, branch_key),
        ).fetchall():
        event = dict(row)
        event["payload"] = load_json_text(event.get("payload"), {})
        events.append(event)
    return events


def aggregate_campus_space_activity(conn, events):
    residents_by_location = {
        row["location"]: int(row["count"])
        for row in conn.execute(
            "SELECT location, COUNT(*) AS count FROM residents GROUP BY location ORDER BY location"
        ).fetchall()
    }
    actions_by_type = {}
    actions_by_location = {}
    rejected_actions = 0
    for event in events:
        if event["event_type"] != "agent_tick":
            continue
        payload = event["payload"]
        action = str(payload.get("action") or "unknown")
        location = event.get("location") or "校园"
        actions_by_type[action] = actions_by_type.get(action, 0) + 1
        actions_by_location[location] = actions_by_location.get(location, 0) + 1
        if payload.get("action_success") is False or payload.get("failure_code"):
            rejected_actions += 1
    return {
        "resident_count": sum(residents_by_location.values()),
        "residents_by_location": residents_by_location,
        "action_count": sum(actions_by_type.values()),
        "actions_by_type": actions_by_type,
        "actions_by_location": actions_by_location,
        "rejected_action_count": rejected_actions,
    }


def aggregate_social_dynamics(conn, events):
    interactions = 0
    positive_effects = 0
    commitments_created = 0
    residents_involved = set()
    for event in events:
        payload = event["payload"]
        social_effect = payload.get("social_effect")
        if not isinstance(social_effect, dict):
            continue
        interactions += 1
        if social_effect.get("effect") == "positive":
            positive_effects += 1
        if social_effect.get("commitment"):
            commitments_created += 1
        if event.get("resident_id"):
            residents_involved.add(int(event["resident_id"]))
        if social_effect.get("target_id"):
            residents_involved.add(int(social_effect["target_id"]))
    relationship_summary = conn.execute(
        """
        SELECT COUNT(*) AS relationship_count,
               COALESCE(AVG(trust), 0) AS average_trust,
               COALESCE(AVG(cooperation), 0) AS average_cooperation,
               COALESCE(AVG(conflict), 0) AS average_conflict
        FROM relationship_dynamics
        """
    ).fetchone()
    return {
        "interaction_count": interactions,
        "positive_effect_count": positive_effects,
        "commitment_count": commitments_created,
        "residents_involved": sorted(residents_involved),
        "relationship_count": int(relationship_summary["relationship_count"] or 0),
        "average_trust": round(float(relationship_summary["average_trust"] or 0), 2),
        "average_cooperation": round(float(relationship_summary["average_cooperation"] or 0), 2),
        "average_conflict": round(float(relationship_summary["average_conflict"] or 0), 2),
    }


def aggregate_institutional_resources(conn, events):
    policy_counts = {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM policies GROUP BY status ORDER BY status"
        ).fetchall()
    }
    resource_accounts = {
        row["account_key"]: {
            "resource_type": row["resource_type"],
            "balance": float(row["balance"]),
        }
        for row in conn.execute(
            "SELECT account_key, resource_type, balance FROM world_resource_accounts ORDER BY account_key"
        ).fetchall()
    }
    pending_effects = conn.execute(
        "SELECT COUNT(*) AS count FROM world_delayed_effects WHERE status = 'pending'"
    ).fetchone()
    active_events = conn.execute(
        "SELECT COUNT(*) AS count FROM campus_events WHERE status = 'active'"
    ).fetchone()
    return {
        "policy_counts": policy_counts,
        "resource_accounts": resource_accounts,
        "pending_delayed_effects": int(pending_effects["count"] or 0),
        "active_campus_events": int(active_events["count"] or 0),
        "source_event_count": len(events),
    }


WORLD_UPDATE_HANDLERS = {
    "campus_space_activity": aggregate_campus_space_activity,
    "social_dynamics": aggregate_social_dynamics,
    "institutional_resource_review": aggregate_institutional_resources,
}
