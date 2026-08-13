"""Read-only acceptance checks for world-tick concurrency and health."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import create_database_engine  # noqa: E402
from app.db.migration_runtime import create_migration_engine, get_alembic_config, get_current_revision, get_head_revision  # noqa: E402
from sqlalchemy import text  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-recent-failure-rate", type=float, default=0.05)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--after-tick-id", type=int, default=0,
                        help="Inspect only ticks created after a verified deployment tick.")
    args = parser.parse_args()
    migration_engine = create_migration_engine()
    try:
        current = get_current_revision(migration_engine)
        head = get_head_revision(get_alembic_config())
    finally:
        migration_engine.dispose()
    if current != head:
        raise SystemExit(f"migration revision {current!r} does not match head {head!r}")

    engine = create_database_engine()
    try:
        with engine.connect() as conn:
            running = conn.exec_driver_sql("SELECT COUNT(*) FROM world_ticks WHERE status = 'running'").scalar_one()
            if running > 1:
                raise SystemExit(f"concurrency failure: {running} ticks are marked running")
            rows = conn.execute(
                text("SELECT status FROM world_ticks WHERE id > :after_id ORDER BY id DESC LIMIT :window"),
                {"after_id": args.after_tick_id, "window": args.window},
            ).all()
            failures = sum(1 for row in rows if row[0] == "failed")
            rate = failures / len(rows) if rows else 0.0
            if rate > args.max_recent_failure_rate:
                raise SystemExit(f"recent tick failure rate {rate:.1%} exceeds {args.max_recent_failure_rate:.1%}")
            runtime = conn.exec_driver_sql(
                "SELECT last_tick_started_at, last_tick_completed_at FROM world_runtime WHERE id = 1"
            ).first()
            if not runtime:
                raise SystemExit("world_runtime row is missing")
    finally:
        engine.dispose()
    print(f"Tick runtime health passed: running={running}, after_tick_id={args.after_tick_id}, sampled={len(rows)}, failures={failures}, rate={rate:.1%}.")


if __name__ == "__main__":
    main()
