"""Validate non-secret deployment prerequisites before a staging or production deploy."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import using_postgres  # noqa: E402
from app.db.migration_runtime import (  # noqa: E402
    create_migration_engine,
    get_alembic_config,
    get_current_revision,
    get_head_revision,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    parser.add_argument("--check-database", action="store_true")
    args = parser.parse_args()

    expected_environment = args.environment
    configured_environment = os.getenv("APP_ENV", "").strip().lower()
    if configured_environment != expected_environment:
        raise SystemExit(f"APP_ENV must be {expected_environment!r} before deployment")
    if not os.getenv("ADMIN_TOKEN", "").strip():
        raise SystemExit("ADMIN_TOKEN must be configured for non-local deployment")
    if not using_postgres():
        raise SystemExit("DATABASE_URL must point to PostgreSQL for non-local deployment")
    if expected_environment == "staging":
        for key in ("WORLD_RUNNER_ENABLED", "WORLD_RUNTIME_AUTO_START"):
            if os.getenv(key, "").strip().lower() not in {"false", "0", "no", "off"}:
                raise SystemExit(f"{key}=false is required for staging")

    if args.check_database:
        engine = create_migration_engine()
        try:
            current = get_current_revision(engine)
            head = get_head_revision(get_alembic_config())
        finally:
            engine.dispose()
        if current != head:
            raise SystemExit(f"database revision is {current or 'unversioned'}, expected {head}")
    print(f"{expected_environment} deployment preflight passed")


if __name__ == "__main__":
    main()
