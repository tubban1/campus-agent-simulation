"""Composable orchestration helpers for a world tick."""


def start_world_tick(
    conn,
    reason,
    *,
    runtime_id,
    read_runtime,
    get_world_now,
    sync_current_day,
    world_slot_from_hour,
):
    runtime = read_runtime(conn)
    world_time = get_world_now()
    day_sync = sync_current_day(conn, world_time)
    day = day_sync["day"]
    slot = world_slot_from_hour(world_time.hour)
    tick_index_row = conn.execute(
        "SELECT COALESCE(MAX(tick_index), 0) AS value FROM world_ticks"
    ).fetchone()
    tick_index = int(tick_index_row["value"]) + 1
    tick_cursor = conn.execute(
        """
        INSERT INTO world_ticks (tick_index, world_time, day, slot, reason, status)
        VALUES (?, ?, ?, ?, ?, 'running')
        """,
        (tick_index, world_time.isoformat(), day, slot, reason),
    )
    tick_id = tick_cursor.lastrowid
    conn.execute(
        """
        UPDATE world_runtime
        SET last_tick_started_at = ?, world_time = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (world_time.isoformat(), world_time.isoformat(), runtime_id),
    )
    conn.commit()
    return {
        "runtime": runtime,
        "world_time": world_time,
        "day_sync": day_sync,
        "day": day,
        "slot": slot,
        "tick_id": tick_id,
        "tick_index": tick_index,
    }


def record_movement_events(
    movement_results,
    *,
    append_world_event,
    tick_id,
    day,
    slot,
    parent_event_id,
):
    movement_events = []
    for movement in movement_results:
        arrived = movement["movement_status"] == "arrived"
        movement_events.append(
            append_world_event(
                movement["event_type"],
                (
                    f"{movement.get('resident_name', 'Agent')}抵达目的地"
                    if arrived
                    else f"{movement.get('resident_name', 'Agent')}正在移动"
                ),
                (
                    "已抵达目标空间，完成本段路线。"
                    if arrived
                    else (
                        f"本 tick 前进 {movement.get('distance_traveled_meters', 0):.1f} 米，"
                        f"路线进度 {movement.get('progress', 0) * 100:.1f}%。"
                    )
                ),
                tick_id=tick_id,
                resident_id=movement["resident_id"],
                payload=movement,
                day=day,
                slot=slot,
                source_type="spatial_movement",
                source_id=movement["resident_id"],
                parent_event_id=parent_event_id,
                rule_version="spatial-movement-v1",
            )
        )
    return movement_events


def record_organization_events(
    conn,
    organization_updates,
    *,
    append_world_event,
    tick_id,
    day,
    slot,
    parent_event_id,
):
    organization_events = []
    for proposal_id in organization_updates["executed"]:
        proposal = conn.execute(
            """
            SELECT proposal.*, organization.name AS organization_name
            FROM organization_proposals proposal
            JOIN campus_organizations organization
              ON organization.id = proposal.organization_id
            WHERE proposal.id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if not proposal:
            continue
        organization_events.append(
            append_world_event(
                "organization_collective_action",
                f"{proposal['organization_name']}执行集体行动",
                proposal["title"],
                tick_id=tick_id,
                payload={
                    "organization_id": proposal["organization_id"],
                    "proposal_id": proposal_id,
                    "proposal_type": proposal["proposal_type"],
                    "requested_budget_minor": proposal["requested_budget_minor"],
                    "ledger_transaction_id": proposal["ledger_transaction_id"],
                },
                day=day,
                slot=slot,
                source_type="organization_proposal",
                source_id=proposal_id,
                parent_event_id=parent_event_id,
                rule_version="organization-runtime-v1",
            )
        )
    return organization_events


def run_pre_agent_subsystems(
    conn,
    reason,
    *,
    world_time,
    tick_id,
    tick_index,
    day_sync,
    day,
    slot,
    active_branch_key,
    append_world_event,
    compact_external_sync_result,
    process_population_runtime,
    ensure_current_action_plans,
    sync_world_time_environment,
    process_due_world_delayed_effects,
    external_world_available,
    process_external_world_runtime,
    maybe_auto_sync_real_weather,
    get_campus_environment,
    maybe_auto_sync_external_information,
    process_resilience_runtime,
    capture_tick_observations,
    advance_body_states,
    advance_active_movements,
    run_due_world_updates,
    process_supply_runtime,
    process_market_runtime,
    process_labor_runtime,
    process_credit_runtime,
    process_budget_runtime,
    process_public_policy_runtime,
    process_organization_runtime,
    process_social_institution_runtime,
    process_macro_runtime,
):
    population_updates = process_population_runtime(conn, world_time)
    ensure_result = ensure_current_action_plans(conn, world_time)
    environment = sync_world_time_environment(conn, world_time)
    delayed_effects = process_due_world_delayed_effects(
        conn,
        world_time,
        tick_id=tick_id,
        day=day,
        slot=slot,
    )
    if external_world_available(conn):
        external_world_updates = process_external_world_runtime(
            conn,
            world_time,
            branch_key=active_branch_key(),
        )
        weather_sync = {"skipped": True, "reason": "delegated_to_external_ingestion"}
        external_sync = {"skipped": True, "reason": "delegated_to_external_ingestion"}
    else:
        external_world_updates = {"available": False}
        weather_sync = maybe_auto_sync_real_weather(
            conn, world_time, tick_id=tick_id, day=day, slot=slot
        )
        if not weather_sync.get("skipped") and not weather_sync.get("failed"):
            environment = get_campus_environment(conn, day)
        external_sync = maybe_auto_sync_external_information(
            conn, world_time, tick_id=tick_id, day=day, slot=slot
        )
    resilience_updates = process_resilience_runtime(conn, world_time)
    start_event = append_world_event(
        "world_tick_started",
        "世界 tick 开始",
        f"{slot} tick 开始，世界正在按真实时间推进。",
        tick_id=tick_id,
        payload={
            "reason": reason,
            "plans_created": ensure_result["created"],
            "llm_plans": ensure_result["llm_plans"],
            "rule_based_plans": ensure_result["rule_based_plans"],
            "weather": environment.get("weather"),
            "weather_sync": weather_sync,
            "external_sync": compact_external_sync_result(external_sync),
            "delayed_effects": {
                "due_count": delayed_effects["due_count"],
                "applied_count": len(delayed_effects["applied"]),
                "failed_count": len(delayed_effects["failed"]),
            },
            "resilience_updates": resilience_updates,
            "population_updates": population_updates,
            "external_world_updates": external_world_updates,
            "day_sync": day_sync,
        },
        day=day,
        slot=slot,
        source_type="runtime_tick",
        source_id=tick_id,
    )
    local_observations = capture_tick_observations(
        conn,
        world_time,
        tick_id,
        day,
        branch_key=active_branch_key(),
    )
    body_states = advance_body_states(conn, world_time, tick_index, environment)
    movement_results = advance_active_movements(conn, world_time, tick_index)
    movement_events = record_movement_events(
        movement_results,
        append_world_event=append_world_event,
        tick_id=tick_id,
        day=day,
        slot=slot,
        parent_event_id=start_event["id"],
    )
    multiscale_updates = run_due_world_updates(
        conn,
        world_time,
        tick_id,
        day,
        slot,
        parent_event_id=start_event["id"],
    )
    supply_updates = process_supply_runtime(conn, world_time)
    market_updates = process_market_runtime(conn, world_time)
    labor_updates = process_labor_runtime(conn, world_time)
    credit_updates = process_credit_runtime(conn, world_time)
    budget_updates = process_budget_runtime(conn, world_time)
    public_policy_updates = process_public_policy_runtime(conn, world_time)
    organization_updates = process_organization_runtime(conn, world_time)
    organization_events = record_organization_events(
        conn,
        organization_updates,
        append_world_event=append_world_event,
        tick_id=tick_id,
        day=day,
        slot=slot,
        parent_event_id=start_event["id"],
    )
    social_institution_updates = process_social_institution_runtime(conn, world_time)
    macro_updates = process_macro_runtime(conn, world_time)
    conn.commit()
    return {
        "environment": environment,
        "start_event": start_event,
        "local_observations": local_observations,
        "body_states": body_states,
        "movement_results": movement_results,
        "movement_events": movement_events,
        "multiscale_updates": multiscale_updates,
        "organization_updates": organization_updates,
        "organization_events": organization_events,
        "supply_updates": supply_updates,
        "market_updates": market_updates,
        "labor_updates": labor_updates,
        "credit_updates": credit_updates,
        "budget_updates": budget_updates,
        "public_policy_updates": public_policy_updates,
        "social_institution_updates": social_institution_updates,
        "macro_updates": macro_updates,
        "resilience_updates": resilience_updates,
        "population_updates": population_updates,
        "external_world_updates": external_world_updates,
    }


def run_agent_and_learning_stage(
    conn,
    runtime,
    *,
    world_time,
    tick_id,
    tick_index,
    day,
    slot,
    parent_event_id,
    active_branch_key,
    select_world_tick_agents,
    process_world_agent_tick,
    process_adaptive_learning,
    process_norm_emergence,
    process_institution_evolution,
    process_longitudinal_runtime,
):
    selected_agents, next_cursor, focused_set = select_world_tick_agents(conn, runtime)
    results = []
    failed = 0
    for agent in selected_agents:
        item = process_world_agent_tick(
            conn,
            agent,
            world_time,
            tick_id,
            day,
            slot,
            observed=agent["id"] in focused_set,
            parent_event_id=parent_event_id,
        )
        results.append(item)
        if not item["success"]:
            failed += 1
    branch_key = active_branch_key()
    adaptive_learning = process_adaptive_learning(
        conn,
        world_time=world_time,
        tick_id=tick_id,
        tick_number=tick_index,
        branch_key=branch_key,
        resident_ids=[int(agent["id"]) for agent in selected_agents],
    )
    norm_emergence = process_norm_emergence(
        conn,
        branch_key=branch_key,
        tick_number=tick_index,
        world_time=world_time,
    )
    return {
        "selected_agents": selected_agents,
        "next_cursor": next_cursor,
        "results": results,
        "failed": failed,
        "adaptive_learning": adaptive_learning,
        "norm_emergence": norm_emergence,
        "institution_evolution": process_institution_evolution(conn, world_time),
        "longitudinal_updates": process_longitudinal_runtime(conn, world_time),
    }


def settle_tick_completion(
    conn,
    *,
    runtime_id,
    tick_id,
    next_cursor,
    results,
    failed,
    completed_at,
):
    action_limited = sum(
        1 for item in results if item.get("action_success") is False
    )
    conn.execute(
        """
        UPDATE world_ticks
        SET status = ?, processed_agents = ?, failed_agents = ?, completed_at = ?
        WHERE id = ?
        """,
        ("failed" if failed else "complete", len(results), failed, completed_at, tick_id),
    )
    conn.execute(
        """
        UPDATE world_runtime
        SET current_agent_cursor = ?, last_tick_completed_at = ?, world_time = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (next_cursor, completed_at, completed_at, runtime_id),
    )
    return action_limited


def build_tick_completion_payload(
    *,
    start_event,
    results,
    failed,
    action_limited,
    multiscale_updates,
    organization_updates,
    supply_updates,
    market_updates,
    labor_updates,
    credit_updates,
    budget_updates,
    public_policy_updates,
    social_institution_updates,
    macro_updates,
    adaptive_learning,
    norm_emergence,
    institution_evolution,
    resilience_updates,
    population_updates,
    external_world_updates,
    longitudinal_updates,
    organization_events,
    movement_results,
    body_states,
    local_observations,
):
    return {
        "started_event_id": start_event["id"],
        "processed_agents": len(results),
        "failed_agents": failed,
        "action_limited_agents": action_limited,
        "multiscale_updates": {
            "due_count": multiscale_updates["due_count"],
            "completed_count": len(multiscale_updates["completed"]),
            "failed_count": len(multiscale_updates["failed"]),
        },
        "organization_updates": organization_updates,
        "supply_updates": supply_updates,
        "market_updates": market_updates,
        "labor_updates": labor_updates,
        "credit_updates": credit_updates,
        "budget_updates": budget_updates,
        "public_policy_updates": public_policy_updates,
        "social_institution_updates": social_institution_updates,
        "macro_updates": macro_updates,
        "adaptive_learning": adaptive_learning,
        "norm_emergence": norm_emergence,
        "institution_evolution": institution_evolution,
        "resilience_updates": resilience_updates,
        "population_updates": population_updates,
        "external_world_updates": external_world_updates,
        "longitudinal_updates": longitudinal_updates,
        "organization_event_count": len(organization_events),
        "spatial_movements": {
            "advanced_count": len(movement_results),
            "arrived_count": sum(
                1 for item in movement_results if item["movement_status"] == "arrived"
            ),
        },
        "body_states": {
            "advanced_count": len(body_states),
            "sleeping_count": sum(1 for item in body_states if item["sleeping"]),
            "moving_count": sum(1 for item in body_states if item["moving"]),
        },
        "local_perception": {
            "observation_count": len(local_observations),
            "observer_count": len(
                {item["observer_resident_id"] for item in local_observations}
            ),
        },
    }


def run_post_tick_handlers(
    conn,
    *,
    world_time,
    tick_id,
    day,
    slot,
    maybe_generate_group_behavior_event,
    maybe_publish_campus_news_from_world_window,
):
    conn.commit()
    group_behavior = maybe_generate_group_behavior_event(
        conn, world_time, tick_id, day, slot
    )
    campus_news = maybe_publish_campus_news_from_world_window(
        conn, world_time, tick_id=tick_id, day=day
    )
    conn.commit()
    return group_behavior, campus_news
