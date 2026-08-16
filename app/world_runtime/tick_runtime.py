"""World tick transaction orchestration.

The application composition root supplies runtime dependencies.
"""

from dataclasses import dataclass
from typing import Any, Mapping

_MODULE_NAME = __name__


@dataclass(frozen=True)
class TickRuntimeDependencies:
    """Explicit composition-root bindings for the world tick transaction."""

    values: Mapping[str, Any]

    def apply(self):
        configure(**dict(self.values))

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


def reconcile_stale_world_ticks(conn, now=None):
    """Mark abandoned running rows failed after the configured safety window."""
    now = now or get_world_now()
    threshold = stale_world_tick_seconds()
    runtime_started = None
    runtime_last = conn.execute(
        "SELECT last_tick_started_at FROM world_runtime WHERE id = ?",
        (WORLD_RUNTIME_ID,),
    ).fetchone()
    if runtime_last:
        runtime_started = parse_world_datetime(runtime_last["last_tick_started_at"])
    stale_ids = []
    rows = conn.execute(
        """
        SELECT id, started_at FROM world_ticks
        WHERE status = 'running'
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        # A running tick with an unparseable/missing timestamp must not wedge
        # the world forever: fall back to the runtime's wall-clock marker and,
        # if that is also unavailable, treat it as stale so it self-heals.
        started_at = parse_world_datetime(row["started_at"])
        if started_at is None:
            started_at = runtime_started
        if started_at is None or (now - started_at).total_seconds() >= threshold:
            stale_ids.append(int(row["id"]))
    if not stale_ids:
        return []
    completed_at = now.isoformat()
    message = "runner recovered a stale running tick"
    for tick_id in stale_ids:
        conn.execute(
            """
            UPDATE world_ticks
            SET status = 'failed', error_message = ?, completed_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (message, completed_at, tick_id),
        )
    remaining = conn.execute(
        "SELECT 1 FROM world_ticks WHERE status = 'running' LIMIT 1"
    ).fetchone()
    if remaining is None:
        conn.execute(
            """
            UPDATE world_runtime
            SET last_tick_started_at = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (WORLD_RUNTIME_ID,),
        )
    return stale_ids


def record_world_tick_failure(tick_id, reason, exc):
    failed_at = get_world_now().isoformat()
    with get_connection() as failure_conn:
        if tick_id is not None:
            failure_conn.execute(
                """
                UPDATE world_ticks
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (f"{type(exc).__name__}: {str(exc)[:500]}", failed_at, tick_id),
            )
            failure_conn.execute(
                """
                UPDATE world_runtime
                SET last_tick_started_at = '', last_tick_completed_at = ?,
                    world_time = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (failed_at, failed_at, WORLD_RUNTIME_ID),
            )
        append_world_event(
            failure_conn,
            "world_tick_failed",
            "世界 tick 失败",
            f"后台世界推进失败：{type(exc).__name__}: {str(exc)[:180]}",
            tick_id=tick_id,
            payload={"error": str(exc), "reason": reason},
        )
        failure_conn.commit()


def advance_world_tick(reason="background"):
    if not WORLD_TICK_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="世界 tick 正在执行中")
    try:
        with world_tick_database_lease() as acquired:
            if not acquired:
                raise HTTPException(status_code=409, detail="另一个服务实例正在执行世界 tick")
            return _advance_world_tick_locked(reason)
    finally:
        WORLD_TICK_LOCK.release()


def _advance_world_tick_locked(reason="background"):
    tick_id = None
    try:
        with get_connection() as conn:
            if using_postgres() and hasattr(conn, "_connection"):
                try:
                    conn.execute(
                        "SELECT id FROM world_runtime WHERE id = ? FOR UPDATE NOWAIT",
                        (WORLD_RUNTIME_ID,),
                    ).fetchone()
                except Exception as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="另一个服务实例正在执行世界 tick",
                    ) from exc
            # Self-heal any abandoned running tick before refusing to start a
            # new one, so a stale leftover cannot wedge the world across
            # restarts regardless of which caller drives the tick.
            reconcile_stale_world_ticks(conn)
            conn.commit()
            existing_tick = conn.execute(
                "SELECT id FROM world_ticks WHERE status = 'running' ORDER BY id LIMIT 1"
            ).fetchone()
            if existing_tick:
                raise HTTPException(
                    status_code=409,
                    detail=f"世界 tick {existing_tick['id']} 正在执行中",
                )
            tick = start_world_tick(
                conn,
                reason,
                runtime_id=WORLD_RUNTIME_ID,
                read_runtime=read_world_runtime,
                get_world_now=get_world_now,
                sync_current_day=sync_current_day_with_world_date,
                world_slot_from_hour=world_slot_from_hour,
            )
            runtime = tick["runtime"]
            world_time = tick["world_time"]
            day_sync = tick["day_sync"]
            day = tick["day"]
            slot = tick["slot"]
            tick_id = tick["tick_id"]
            tick_index = tick["tick_index"]
            pre_agent = run_pre_agent_subsystems(
                conn,
                reason,
                world_time=world_time,
                tick_id=tick_id,
                tick_index=tick_index,
                day_sync=day_sync,
                day=day,
                slot=slot,
                active_branch_key=lambda: active_world_branch_key(conn),
                append_world_event=lambda *args, **kwargs: append_world_event(conn, *args, **kwargs),
                compact_external_sync_result=compact_external_sync_result,
                process_population_runtime=process_population_runtime,
                ensure_current_action_plans=ensure_current_action_plans,
                sync_world_time_environment=sync_world_time_environment,
                process_due_world_delayed_effects=process_due_world_delayed_effects,
                external_world_available=external_world_available,
                process_external_world_runtime=process_external_world_runtime,
                maybe_auto_sync_real_weather=maybe_auto_sync_real_weather,
                get_campus_environment=get_campus_environment,
                maybe_auto_sync_external_information=maybe_auto_sync_external_information,
                process_resilience_runtime=process_resilience_runtime,
                capture_tick_observations=capture_tick_observations,
                advance_body_states=advance_body_states,
                advance_active_movements=advance_active_movements,
                run_due_world_updates=run_due_world_updates,
                process_supply_runtime=process_supply_runtime,
                process_market_runtime=process_market_runtime,
                process_labor_runtime=process_labor_runtime,
                process_credit_runtime=process_credit_runtime,
                process_budget_runtime=process_budget_runtime,
                process_public_policy_runtime=process_public_policy_runtime,
                process_organization_runtime=process_organization_runtime,
                process_social_institution_runtime=process_social_institution_runtime,
                process_macro_runtime=process_macro_runtime,
                process_night_dreams=process_night_dreams,
            )
            start_event = pre_agent["start_event"]
            local_observations = pre_agent["local_observations"]
            body_states = pre_agent["body_states"]
            facility_updates = pre_agent["facility_updates"]
            movement_results = pre_agent["movement_results"]
            movement_events = pre_agent["movement_events"]
            multiscale_updates = pre_agent["multiscale_updates"]
            organization_updates = pre_agent["organization_updates"]
            organization_events = pre_agent["organization_events"]
            supply_updates = pre_agent["supply_updates"]
            market_updates = pre_agent["market_updates"]
            labor_updates = pre_agent["labor_updates"]
            credit_updates = pre_agent["credit_updates"]
            budget_updates = pre_agent["budget_updates"]
            public_policy_updates = pre_agent["public_policy_updates"]
            social_institution_updates = pre_agent["social_institution_updates"]
            macro_updates = pre_agent["macro_updates"]
            resilience_updates = pre_agent["resilience_updates"]
            population_updates = pre_agent["population_updates"]
            external_world_updates = pre_agent["external_world_updates"]
            agent_stage = run_agent_and_learning_stage(
                conn,
                runtime,
                world_time=world_time,
                tick_id=tick_id,
                tick_index=tick_index,
                day=day,
                slot=slot,
                parent_event_id=start_event["id"],
                active_branch_key=lambda: active_world_branch_key(conn),
                select_world_tick_agents=select_world_tick_agents,
                process_world_agent_tick=process_world_agent_tick,
                process_adaptive_learning=process_adaptive_learning,
                process_norm_emergence=process_norm_emergence,
                process_institution_evolution=process_institution_evolution,
                process_longitudinal_runtime=process_longitudinal_runtime,
            )
            selected_agents = agent_stage["selected_agents"]
            next_cursor = agent_stage["next_cursor"]
            results = agent_stage["results"]
            failed = agent_stage["failed"]
            adaptive_learning = agent_stage["adaptive_learning"]
            norm_emergence = agent_stage["norm_emergence"]
            institution_evolution = agent_stage["institution_evolution"]
            longitudinal_updates = agent_stage["longitudinal_updates"]
            completed_at = get_world_now().isoformat()
            action_limited = settle_tick_completion(
                conn,
                runtime_id=WORLD_RUNTIME_ID,
                tick_id=tick_id,
                next_cursor=next_cursor,
                results=results,
                failed=failed,
                completed_at=completed_at,
            )

            # 自动刷新环境 real_time 维度时间与状态
            sync_fn = globals().get("sync_world_time_environment") or pre_agent.get("sync_world_time_environment")
            if sync_fn:
                try:
                    sync_fn(conn, world_time)
                except Exception as sync_err:
                    logger.warning("Failed to sync world_time to environment: %s", sync_err)

            # 唤醒新闻编辑 Agent，自动搜集精彩动态并发布最新新闻
            news_fn = globals().get("publish_agent_news")
            if not news_fn:
                import app.main as main_mod
                news_fn = getattr(main_mod, "publish_agent_news", None)
            if news_fn and results:
                try:
                    published_news = news_fn(conn, day, results)
                    if published_news:
                        logger.info("Campus news editor published %d news items for day %s", len(published_news), day)
                except Exception as news_err:
                    logger.warning("Auto news publishing failed: %s", news_err)

            finish_event = append_world_event(
                conn,
                "world_tick_complete",
                "世界 tick 完成",
                (
                    f"本次 tick 处理 {len(results)} 位 Agent，运行失败 {failed} 位"
                    f"，行动受限 {action_limited} 位。"
                ),
                tick_id=tick_id,
                payload={
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
                            1
                            for item in movement_results
                            if item["movement_status"] == "arrived"
                        ),
                    },
                    "body_states": {
                        "advanced_count": len(body_states),
                        "sleeping_count": sum(
                            1 for item in body_states if item["sleeping"]
                        ),
                        "moving_count": sum(
                            1 for item in body_states if item["moving"]
                        ),
                    },
                    # This is deliberately part of the completed tick as well
                    # as its start event: clients polling only completion
                    # events can update a POI's stock/maintenance badge
                    # without reloading the complete spatial scene.
                    "facility_lifecycle": facility_updates,
                    "local_perception": {
                        "observation_count": len(local_observations),
                        "observer_count": len(
                            {
                                item["observer_resident_id"]
                                for item in local_observations
                            }
                        ),
                    },
                },
                day=day,
                slot=slot,
                source_type="runtime_tick",
                source_id=tick_id,
                parent_event_id=start_event["id"],
            )
            group_behavior, campus_news = run_post_tick_handlers(
                conn,
                world_time=world_time,
                tick_id=tick_id,
                day=day,
                slot=slot,
                maybe_generate_group_behavior_event=maybe_generate_group_behavior_event,
                maybe_publish_campus_news_from_world_window=maybe_publish_campus_news_from_world_window,
            )
            return {
                "tick_id": tick_id,
                "tick_index": tick_index,
                "world_time": completed_at,
                "day": day,
                "slot": slot,
                "reason": reason,
                "processed_agents": len(results),
                "failed_agents": failed,
                "events": [start_event, finish_event],
                "multiscale_updates": multiscale_updates,
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
                "organization_events": organization_events,
                "spatial_movements": movement_results,
                "facility_updates": facility_updates,
                "body_states": body_states,
                "local_observations": local_observations,
                "movement_events": movement_events,
                "group_behavior": group_behavior,
                "campus_news": campus_news,
                "results": results,
            }
    except HTTPException as exc:
        if tick_id is not None:
            record_world_tick_failure(tick_id, reason, exc)
        raise
    except Exception as exc:
        logger.exception("World tick failed")
        try:
            record_world_tick_failure(tick_id, reason, exc)
        except Exception:
            logger.exception("Failed to persist world tick failure state")
        raise
