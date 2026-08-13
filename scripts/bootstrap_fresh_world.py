"""Create the only supported blank-world schema before Alembic migrations.

This command is deliberately for an empty database.  It contains no upgrade,
column-probing, or data-preservation behaviour: a release is bootstrapped from
one schema contract and then migrated to the current head.
"""

from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_connection  # noqa: E402
from app.main import (  # noqa: E402
    ensure_agent_news_system,
    ensure_campus_state_table,
    ensure_external_information_system,
    ensure_space_system,
    ensure_world_runtime_tables,
)
from app.models import SCHEMA_SQL  # noqa: E402


def main() -> None:
    with get_connection() as conn:
        # SCHEMA_SQL is the foundational (pre-Alembic) contract.  It is safe
        # to apply only to create an empty schema; existing domain rows mean
        # this is not a fresh-world operation and must stop here.
        conn.executescript(SCHEMA_SQL)
        existing = conn.execute("SELECT 1 FROM residents LIMIT 1").fetchone()
        if existing is not None:
            raise RuntimeError(
                "Fresh bootstrap requires an empty database. Use the explicit "
                "reset command before creating a new world."
            )
        # The bootstrap is the only place allowed to materialize the complete
        # baseline schema before Alembic takes ownership of later revisions.
        # These calls are deliberately absent from web startup and requests.
        ensure_campus_state_table(conn, allow_ddl=True)
        ensure_space_system(conn, allow_ddl=True)
        ensure_agent_news_system(conn, allow_ddl=True)
        ensure_external_information_system(conn, allow_ddl=True)
        ensure_world_runtime_tables(conn, allow_ddl=True)
        conn.execute(
            "INSERT OR REPLACE INTO simulation_state (key, value) VALUES (?, ?)",
            ("fresh_world_bootstrap", "1"),
        )
        conn.commit()
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "seed_fresh_residents.py")],
        check=True,
    )
    print("Fresh world baseline schema created.")


if __name__ == "__main__":
    main()
