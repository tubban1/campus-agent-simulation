from datetime import datetime, timedelta

from app.world_runtime.clock import (
    WORLD_TZ,
    get_world_plan_window,
    parse_world_datetime,
    previous_completed_world_window,
    world_slot_from_hour,
    world_tick_due,
)


def test_world_clock_normalizes_and_windows_are_stable():
    parsed = parse_world_datetime("2026-08-07T00:30:00+00:00")

    assert parsed == datetime(2026, 8, 7, 8, 30, tzinfo=WORLD_TZ)
    assert world_slot_from_hour(8) == "08:00-16:00"
    assert get_world_plan_window(parsed) == (
        datetime(2026, 8, 7, 8, 0, tzinfo=WORLD_TZ),
        datetime(2026, 8, 7, 16, 0, tzinfo=WORLD_TZ),
    )
    assert previous_completed_world_window(parsed, 8 * 3600) == (
        datetime(2026, 8, 7, 0, 0, tzinfo=WORLD_TZ),
        datetime(2026, 8, 7, 8, 0, tzinfo=WORLD_TZ),
        "00:00-08:00",
    )


def test_world_tick_due_uses_configured_interval():
    now = datetime(2026, 8, 7, 10, 0, tzinfo=WORLD_TZ)
    runtime = {
        "status": "running",
        "tick_interval_seconds": 60,
        "last_tick_completed_at": (now - timedelta(seconds=60)).isoformat(),
    }

    assert world_tick_due(runtime, now=now)
    runtime["status"] = "paused"
    assert not world_tick_due(runtime, now=now)
