"""Read model for the campus world observer."""


def build_world_observer_state(
    conn,
    *,
    get_current_day,
    read_world_runtime,
    get_campus_environment,
    get_space_snapshot,
    rows_to_dicts,
):
    day = get_current_day(conn)
    runtime = read_world_runtime(conn)
    branch_key = runtime.get("active_branch_key") or "main"
    residents = conn.execute(
        "SELECT id, name, role, location FROM residents ORDER BY id"
    ).fetchall()
    events = conn.execute(
        """
        SELECT * FROM world_event_stream
        WHERE branch_key = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (branch_key,),
    ).fetchall()
    latest_tick = conn.execute(
        "SELECT * FROM world_ticks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    budget = {
        "date": runtime["budget_date"],
        "auto_model_calls_used": runtime["auto_model_calls_used"],
        "daily_auto_model_budget": runtime["daily_auto_model_budget"],
        "remaining_auto_model_calls": max(
            0,
            int(runtime["daily_auto_model_budget"])
            - int(runtime["auto_model_calls_used"]),
        ),
    }
    return {
        "current_day": day,
        "environment": get_campus_environment(conn, day),
        "spaces": get_space_snapshot(conn, day),
        "agents": rows_to_dicts(residents),
        "events": list(reversed(rows_to_dicts(events))),
        "runtime": {
            "status": runtime["status"],
            "world_time": runtime["world_time"],
            "world_timezone": runtime["world_timezone"],
            "tick_interval_seconds": runtime["tick_interval_seconds"],
            "active_branch_key": branch_key,
            "latest_tick": dict(latest_tick) if latest_tick else None,
            "budget": budget,
        },
    }
