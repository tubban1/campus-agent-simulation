from app.world_runtime.causal_actions import (
    WORLD_UPDATE_HANDLERS,
    decode_world_update_run,
    world_events_for_update,
)
from datetime import timedelta

_MODULE_NAME = __name__
_DEPENDENCY_NAMES = {
    "append_world_event", "canonical_json", "ensure_world_runtime_tables",
    "parse_world_datetime",
}

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


def run_due_world_updates(conn, world_time, tick_id, day, slot, parent_event_id=None):
    ensure_world_runtime_tables(conn)
    schedule_rows = conn.execute(
        """
        SELECT * FROM world_update_schedules
        WHERE status = 'active'
        ORDER BY interval_seconds, id
        """
    ).fetchall()
    schedules = []
    seen_update_keys = set()
    for schedule_row in schedule_rows:
        schedule = dict(schedule_row)
        update_key = schedule["update_key"]
        if update_key in seen_update_keys:
            continue
        seen_update_keys.add(update_key)
        schedules.append(schedule)
    event_cursor_row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS value FROM world_event_stream"
    ).fetchone()
    input_event_cursor = int(event_cursor_row["value"] or 0)
    completed = []
    failed = []
    for schedule in schedules:
        next_run_at = parse_world_datetime(schedule["next_run_at"])
        if next_run_at and next_run_at > world_time:
            continue
        handler = WORLD_UPDATE_HANDLERS.get(schedule["update_key"])
        if not handler:
            continue
        run_cursor = conn.execute(
            """
            INSERT INTO world_update_runs
            (schedule_id, tick_id, update_key, scheduled_for, input_event_cursor)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                schedule["id"],
                tick_id,
                schedule["update_key"],
                world_time.isoformat(),
                input_event_cursor,
            ),
        )
        run_id = run_cursor.lastrowid
        conn.execute("SAVEPOINT world_update_run")
        try:
            events = world_events_for_update(
                conn,
                int(schedule.get("last_event_cursor") or 0),
                input_event_cursor,
            )
            metrics = handler(conn, events)
            metrics["event_window"] = {
                "after_id": int(schedule.get("last_event_cursor") or 0),
                "through_id": input_event_cursor,
            }
            update_event = append_world_event(
                conn,
                "world_multiscale_update",
                f"{schedule['scope']} 多尺度更新完成",
                f"{schedule['cadence']} 更新《{schedule['update_key']}》已从底层状态与事件完成聚合。",
                tick_id=tick_id,
                payload={
                    "run_id": run_id,
                    "update_key": schedule["update_key"],
                    "scope": schedule["scope"],
                    "cadence": schedule["cadence"],
                    "metrics": metrics,
                },
                day=day,
                slot=slot,
                source_type="world_update_run",
                source_id=run_id,
                parent_event_id=parent_event_id,
                rule_version=schedule["rule_version"],
            )
            completed_at = world_time.isoformat()
            next_due_at = (
                world_time + timedelta(seconds=int(schedule["interval_seconds"]))
            ).isoformat()
            conn.execute(
                """
                UPDATE world_update_runs
                SET output_event_id = ?, status = 'completed', metrics_json = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (update_event["id"], canonical_json(metrics), completed_at, run_id),
            )
            conn.execute(
                """
                UPDATE world_update_schedules
                SET last_run_at = ?, next_run_at = ?, last_event_cursor = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (completed_at, next_due_at, input_event_cursor, schedule["id"]),
            )
            conn.execute("RELEASE SAVEPOINT world_update_run")
            completed.append(
                decode_world_update_run(
                    conn.execute(
                        "SELECT * FROM world_update_runs WHERE id = ?", (run_id,)
                    ).fetchone()
                )
            )
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT world_update_run")
            conn.execute("RELEASE SAVEPOINT world_update_run")
            retry_at = (
                world_time
                + timedelta(seconds=min(int(schedule["interval_seconds"]), 300))
            ).isoformat()
            conn.execute(
                """
                UPDATE world_update_runs
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(exc)[:500], world_time.isoformat(), run_id),
            )
            conn.execute(
                """
                UPDATE world_update_schedules
                SET next_run_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (retry_at, schedule["id"]),
            )
            failed.append({"run_id": run_id, "update_key": schedule["update_key"], "error": str(exc)})
    return {"due_count": len(completed) + len(failed), "completed": completed, "failed": failed}
