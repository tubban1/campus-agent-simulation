from datetime import datetime, timedelta

from app.world_runtime.clock import (
    parse_world_datetime,
    parse_runtime_time,
    WORLD_TZ,
    get_world_plan_window,
    previous_completed_world_window,
    world_slot_from_hour,
    world_tick_due,
)


def test_parse_postgres_timestamp_with_short_fraction_and_hour_offset():
    parsed = parse_world_datetime("2026-08-09 12:07:54.96507+00")

    assert parsed is not None
    assert parsed.isoformat() == "2026-08-09T20:07:54.965070+08:00"


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


def test_world_clock_accepts_postgres_short_utc_offset():
    parsed = parse_world_datetime("2026-08-08 21:07:36.313557+00")

    assert parsed == datetime(2026, 8, 9, 5, 7, 36, 313557, tzinfo=WORLD_TZ)


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


def test_world_tick_due_uses_quiet_interval_during_deep_night():
    now = datetime(2026, 8, 7, 4, 0, tzinfo=WORLD_TZ)
    runtime = {
        "status": "running",
        "tick_interval_seconds": 60,
        "last_tick_completed_at": (now - timedelta(seconds=60)).isoformat(),
    }
    assert not world_tick_due(runtime, now=now)
    runtime["last_tick_completed_at"] = (now - timedelta(minutes=15)).isoformat()
    assert world_tick_due(runtime, now=now)


def test_parse_runtime_time_handles_z_suffix():
    parsed = parse_runtime_time("2026-08-09T14:00:00Z")
    assert parsed is not None
    assert parsed.isoformat() == "2026-08-09T22:00:00+08:00"

    now = datetime(2026, 8, 9, 22, 0, 30, tzinfo=WORLD_TZ)
    runtime = {
        "status": "running",
        "tick_interval_seconds": 60,
        "last_tick_completed_at": "2026-08-09T14:00:00Z",
    }
    assert not world_tick_due(runtime, now=now)
