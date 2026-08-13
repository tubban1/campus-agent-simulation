"""Bring the pre-Alembic schema to the complete migration baseline."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_connection, using_postgres  # noqa: E402
from app.main import (  # noqa: E402
    ensure_agent_news_system,
    ensure_campus_state_table,
    ensure_external_information_system,
    ensure_space_system,
    ensure_world_runtime_tables,
)
from app.models import SCHEMA_SQL  # noqa: E402


def _postgres_table_exists(conn, table_name: str) -> bool:
    row = conn.execute("SELECT to_regclass(?) AS table_name", (table_name,)).fetchone()
    return bool(row and row["table_name"])


def _postgres_world_runtime_ready(conn) -> bool:
    """Avoid replaying expensive legacy DDL on an already prepared production DB."""
    required_tables = (
        "world_runtime",
        "world_event_stream",
        "world_ticks",
        "relationship_dynamics",
        "agent_goals",
    )
    return all(_postgres_table_exists(conn, table_name) for table_name in required_tables)


def prepare_legacy_schema() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
        ensure_campus_state_table(conn, allow_ddl=True)
        ensure_space_system(conn, allow_ddl=True)
        ensure_agent_news_system(conn, allow_ddl=True)
        ensure_external_information_system(conn, allow_ddl=True)
        if using_postgres() and _postgres_world_runtime_ready(conn):
            print("Legacy world runtime schema already exists; skipping heavy DDL.")
        else:
            ensure_world_runtime_tables(conn, allow_ddl=True)
        conn.commit()


def main() -> None:
    prepare_legacy_schema()
    print("Legacy campus schema is ready for the Alembic baseline.")


if __name__ == "__main__":
    main()
