"""World-clock and tick-scheduling primitives."""

from datetime import datetime, timedelta, timezone


WORLD_TIMEZONE = "Asia/Shanghai"
WORLD_TZ = timezone(timedelta(hours=8))


def get_world_now():
    return datetime.now(WORLD_TZ)


def parse_world_datetime(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    candidates = [text, text.replace(" ", "T")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc if " " in text else WORLD_TZ)
            return parsed.astimezone(WORLD_TZ)
        except ValueError:
            continue
    return None


def world_slot_from_hour(hour):
    if 0 <= hour < 8:
        return "00:00-08:00"
    if 8 <= hour < 16:
        return "08:00-16:00"
    return "16:00-24:00"


def get_world_plan_window(world_time):
    start_hour = (world_time.hour // 8) * 8
    window_start = world_time.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    return window_start, window_start + timedelta(hours=8)


def previous_completed_world_window(world_time, window_seconds):
    current_window_start, _ = get_world_plan_window(world_time)
    window_start = current_window_start - timedelta(seconds=window_seconds)
    return window_start, current_window_start, world_slot_from_hour(window_start.hour)


def parse_runtime_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=WORLD_TZ)
    return parsed.astimezone(WORLD_TZ)


def world_tick_due(runtime, now=None):
    if runtime.get("status") != "running":
        return False
    interval = max(10, int(runtime.get("tick_interval_seconds", 60) or 60))
    last = parse_runtime_time(runtime.get("last_tick_started_at") or runtime.get("last_tick_completed_at"))
    if not last:
        return True
    return ((now or get_world_now()) - last).total_seconds() >= interval
