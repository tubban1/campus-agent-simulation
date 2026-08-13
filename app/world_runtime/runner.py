"""Background execution loop for the world runtime."""

import os
import time


_FALSE_VALUES = {"0", "false", "no", "off"}


def environment_flag_enabled(name, default="true"):
    return os.getenv(name, default).strip().lower() not in _FALSE_VALUES


def run_world_runner_loop(
    *,
    get_connection,
    reconcile_stale_ticks,
    read_runtime,
    ensure_runtime_running,
    tick_due,
    advance_tick,
    http_exception_type,
    logger,
    sleep_seconds=5,
    stop_event=None,
):
    while not (stop_event and stop_event.is_set()):
        try:
            with get_connection() as conn:
                stale_tick_ids = reconcile_stale_ticks(conn)
                if stale_tick_ids:
                    logger.warning("Recovered stale world ticks: %s", stale_tick_ids)
                    conn.commit()
                runtime = ensure_runtime_running(conn, read_runtime(conn))
            if tick_due(runtime):
                advance_tick(reason="background")
        except http_exception_type as exc:
            if exc.status_code != 409:
                logger.exception("World runner loop skipped one cycle")
        except Exception:
            logger.exception("World runner loop skipped one cycle")
        if stop_event:
            stop_event.wait(sleep_seconds)
        else:
            time.sleep(sleep_seconds)
