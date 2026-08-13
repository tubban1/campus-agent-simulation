"""Run the complete deployment database preparation under a shared lock."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator
from contextlib import contextmanager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import using_postgres  # noqa: E402
from app.db.engine import get_database_schema  # noqa: E402


DEPLOYMENT_SCRIPTS = (
    "bootstrap_fresh_world.py",
    "migrate_db.py",
    "import_tsinghua_world.py",
    "seed_spatial_foundation.py",
    "seed_economy_foundation.py",
    "seed_organization_runtime.py",
    "seed_supply_foundation.py",
    "seed_labor_runtime.py",
    "seed_budget_runtime.py",
    "seed_market_runtime.py",
    "seed_credit_runtime.py",
    "seed_public_policy_runtime.py",
    "seed_social_institution_runtime.py",
    "seed_macro_runtime.py",
    "seed_adaptation_runtime.py",
    "seed_resilience_runtime.py",
    "seed_population_runtime.py",
    "seed_external_world.py",
    "seed_longitudinal_runtime.py",
    "audit_economy_ledger.py",
)
POSTGRES_DEPLOYMENT_LOCK_ID = 4_341_097_383_745_713


@contextmanager
def deployment_lock() -> Iterator[None]:
    if not using_postgres():
        yield
        return

    import psycopg

    connection = psycopg.connect(
        os.environ["DATABASE_URL"].strip(),
        autocommit=True,
    )
    schema = get_database_schema()
    try:
        connection.execute(f'SET search_path TO "{schema}"')
        print(f"Waiting for PostgreSQL deployment lock (schema={schema})...")
        connection.execute(
            "SELECT pg_advisory_lock(%s)",
            (POSTGRES_DEPLOYMENT_LOCK_ID,),
        )
        print("PostgreSQL deployment lock acquired.")
        yield
    finally:
        try:
            connection.execute(
                "SELECT pg_advisory_unlock(%s)",
                (POSTGRES_DEPLOYMENT_LOCK_ID,),
            )
        finally:
            connection.close()


def validate_target(*, require_postgres: bool) -> None:
    if require_postgres and not using_postgres():
        raise RuntimeError(
            "DATABASE_URL must point to PostgreSQL for deployment. Refusing to "
            "initialize a local SQLite database."
        )
    target = "PostgreSQL" if using_postgres() else "SQLite"
    print(f"Deployment database target validated: {target}.")


def run_deployment(*, require_postgres: bool = False) -> None:
    validate_target(require_postgres=require_postgres)
    os.environ.setdefault("INITIAL_WORLD_KEY", "tsinghua_main")
    with deployment_lock():
        for script_name in DEPLOYMENT_SCRIPTS:
            script_path = PROJECT_ROOT / "scripts" / script_name
            print(f"Running {script_name}...")
            subprocess.run([sys.executable, str(script_path)], check=True)
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "migrate_db.py"),
                "--check",
            ],
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-postgres",
        action="store_true",
        help="Fail instead of falling back to SQLite when DATABASE_URL is absent.",
    )
    args = parser.parse_args()
    run_deployment(require_postgres=args.require_postgres)
    print("Deployment database preparation completed.")


if __name__ == "__main__":
    main()
