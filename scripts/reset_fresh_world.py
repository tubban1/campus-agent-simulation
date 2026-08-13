"""Destructively rebuild a local or Supabase schema into a fresh world.

This command intentionally has no data migration path.  It refuses to run
without an exact schema confirmation, then removes that schema and delegates
creation to ``deploy_database.py``.  Do not use it against a database that
contains a world you intend to keep.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.engine import create_database_engine, get_database_schema  # noqa: E402
from app.db.migration_runtime import describe_database_target  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-schema", required=True)
    parser.add_argument("--yes-rebuild-fresh-world", action="store_true")
    args = parser.parse_args()

    schema = get_database_schema()
    if not args.yes_rebuild_fresh_world or args.confirm_schema != schema:
        raise SystemExit(
            "Refusing destructive reset. Pass --confirm-schema " + schema
            + " and --yes-rebuild-fresh-world after verifying the target."
        )

    engine = create_database_engine()
    try:
        target = describe_database_target(engine)
        print(
            "Rebuilding fresh world on "
            f"{target['dialect']}:{target['database']}/{target['schema'] or 'default'}"
        )
        with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                quoted = connection.dialect.identifier_preparer.quote(schema)
                connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))
                connection.execute(text(f"CREATE SCHEMA {quoted}"))
            elif connection.dialect.name == "sqlite":
                tables = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).scalars().all()
                for table in tables:
                    if table != "sqlite_sequence":
                        connection.exec_driver_sql(f'DROP TABLE "{table}"')
            else:
                raise RuntimeError(f"Unsupported reset dialect: {connection.dialect.name}")
    finally:
        engine.dispose()

    subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "deploy_database.py")], check=True)


if __name__ == "__main__":
    main()
