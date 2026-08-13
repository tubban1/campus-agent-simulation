"""Apply the current migrations to the sole supported fresh-world schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.migration_runtime import (  # noqa: E402
    BASELINE_REQUIRED_TABLES,
    BASELINE_REVISION,
    create_migration_engine,
    describe_database_target,
    get_alembic_config,
    get_current_revision,
    get_head_revision,
    list_business_tables,
)


def migrate_database(check_only: bool = False) -> dict:
    engine = create_migration_engine()
    config = get_alembic_config()
    try:
        tables = list_business_tables(engine)
        target = describe_database_target(engine)
        current = get_current_revision(engine)
        head = get_head_revision(config)
        if check_only:
            return {
                "current": current,
                "head": head,
                "business_tables": len(tables),
                "ready": current == head,
                "target": target,
            }
        if not tables:
            raise RuntimeError(
            "Database has no fresh-world schema "
                f"(dialect={target['dialect']}, database={target['database']}, "
                f"schema={target['schema'] or 'default'}). "
                "Run scripts/bootstrap_fresh_world.py before migrations."
            )
        if current is None:
            missing = sorted(BASELINE_REQUIRED_TABLES - set(tables))
            if missing:
                raise RuntimeError(
                    "Database is missing required baseline tables: "
                    + ", ".join(missing)
                    + ". Run scripts/bootstrap_fresh_world.py first."
                )
            with engine.connect() as connection:
                marker = connection.execute(
                    text(
                        "SELECT value FROM simulation_state "
                        "WHERE key = 'fresh_world_bootstrap'"
                    )
                ).scalar()
            if marker != "1":
                raise RuntimeError(
                    "Database is not a freshly bootstrapped world. Refusing "
                    "to stamp or upgrade an existing schema; rebuild it with "
                    "scripts/reset_fresh_world.py."
                )
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        current = get_current_revision(engine)
        tables = list_business_tables(engine)
        if current != head:
            raise RuntimeError(
                "Database migration did not persist the target revision: "
                f"current={current or 'unversioned'}, head={head}."
            )
        return {
            "current": current,
            "head": head,
            "business_tables": len(tables),
            "ready": current == head,
            "target": target,
        }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report revision state without changing the database.",
    )
    args = parser.parse_args()
    result = migrate_database(check_only=args.check)
    print(
        f"Database target {result['target']['dialect']}:"
        f"{result['target']['database']}"
        f"{('/' + result['target']['schema']) if result['target']['schema'] else ''}; "
        f"revision {result['current'] or 'unversioned'} / "
        f"{result['head']}; tables={result['business_tables']}; "
        f"ready={str(result['ready']).lower()}"
    )
    if args.check and not result["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
