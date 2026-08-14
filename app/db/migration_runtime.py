from __future__ import annotations

from typing import Optional

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.engine import (
    PROJECT_ROOT,
    create_database_engine,
    get_database_schema,
    get_database_url,
)


BASELINE_REVISION = "20260729_0001"
BASELINE_REQUIRED_TABLES = {
    "residents",
    "simulation_state",
    "campus_state",
    "campus_spaces",
    "world_runtime",
    "world_event_stream",
    "world_snapshots",
    "experiment_runs",
    "world_branches",
}

# Tables that must exist at the current Alembic head.  Keep this separate
# from the pre-migration baseline: a blank fresh database is stamped at the
# baseline before later migrations create these runtime tables.
READINESS_REQUIRED_TABLES = BASELINE_REQUIRED_TABLES | {
    "spatial_nodes",
    "spatial_edges",
    "spatial_import_batches",
    "spatial_physical_states",
    "spatial_edge_physical_states",
    "spatial_facility_states",
    "spatial_facility_work_orders",
    "social_interaction_sessions",
    "social_session_participants",
    "social_session_turns",
}


def get_alembic_config(database_url: Optional[str] = None) -> Config:
    config_path = PROJECT_ROOT / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option(
        "script_location", str(PROJECT_ROOT / "app" / "db" / "migrations")
    )
    resolved_url = database_url or get_database_url()
    config.set_main_option("sqlalchemy.url", resolved_url.replace("%", "%%"))
    config.attributes["database_url"] = resolved_url
    config.attributes["database_schema"] = get_database_schema()
    return config


def get_current_revision(engine: Engine) -> Optional[str]:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def get_head_revision(config: Optional[Config] = None) -> str:
    heads = ScriptDirectory.from_config(config or get_alembic_config()).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected one migration head, found: {heads}")
    return heads[0]


def list_business_tables(engine: Engine) -> list[str]:
    schema = get_database_schema() if engine.dialect.name == "postgresql" else None
    return sorted(
        name
        for name in inspect(engine).get_table_names(schema=schema)
        if name != "alembic_version"
    )


def create_migration_engine(database_url: Optional[str] = None) -> Engine:
    return create_database_engine(database_url)


def migrate_pending_to_head() -> dict:
    """Apply any pending Alembic migrations so runtime tables exist.

    Safe to call at application startup: it is a no-op when the database is
    already at head, and it refuses to touch an unversioned schema (which is
    bootstrapped by scripts/deploy_database.py).
    """
    engine = create_migration_engine()
    config = get_alembic_config()
    try:
        current = get_current_revision(engine)
        head = get_head_revision(config)
        if current is None:
            return {"applied": False, "reason": "unversioned_schema"}
        if current == head:
            return {"applied": False, "reason": "already_at_head"}
        command.upgrade(config, "head")
        return {
            "applied": True,
            "from_revision": current,
            "to_revision": get_current_revision(engine),
        }
    finally:
        engine.dispose()


def describe_database_target(engine: Engine) -> dict:
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            row = connection.execute(
                text(
                    "SELECT current_database() AS database_name, "
                    "current_schema() AS schema_name"
                )
            ).mappings().one()
            return {
                "dialect": "postgresql",
                "database": row["database_name"],
                "schema": row["schema_name"],
            }
        return {
            "dialect": engine.dialect.name,
            "database": str(engine.url.database or ""),
            "schema": "",
        }
