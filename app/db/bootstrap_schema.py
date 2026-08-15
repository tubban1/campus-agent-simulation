"""Small, explicit schema guards used by local test/bootstrap fixtures.

Production services must arrive with Alembic migrations already applied.  These
helpers deliberately refuse to mutate a schema unless ``allow_ddl`` is set by
a test or an explicit bootstrap command; keeping them here prevents the web
composition root from quietly becoming a second migration system.
"""

from __future__ import annotations

from app.schema import (
    AGENT_NEWS_COLUMN_TYPES,
    AGENT_NEWS_SQL,
    AGENT_PROFILE_SQL,
    AGENT_INFORMATION_COLUMNS,
    CAMPUS_STATE_SQL,
    DEFAULT_SPACES,
    ENV_COLUMN_TYPES,
    EXTERNAL_INFORMATION_SQL,
    PROFILE_COLUMN_TYPES,
    ROADMAP2_OBSERVER_SQL,
    SPACE_SYSTEM_SQL,
)


class SchemaMigrationRequired(RuntimeError):
    """The database is behind the application's Alembic schema."""


def table_columns(conn, table_name: str) -> set[str]:
    """Return columns through the DB compatibility connection."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    result = set()
    for row in rows:
        if hasattr(row, "keys"):
            result.add(row["name"])
        elif hasattr(row, "_mapping"):
            result.add(row._mapping["name"])
        elif isinstance(row, (list, tuple)):
            result.add(str(row[1]))
        else:
            try:
                result.add(row["name"])
            except Exception:
                result.add(str(row[1]))
    return result


def ensure_table_columns(conn, table_name, column_types, *, allow_ddl=False):
    columns = table_columns(conn, table_name)
    if not columns:
        raise SchemaMigrationRequired(
            f"Database table '{table_name}' is missing. Run the deployment schema "
            "initialization before starting the web service."
        )
    missing_columns = [
        (column, column_type)
        for column, column_type in column_types.items()
        if column not in columns
    ]
    if missing_columns and not allow_ddl:
        names = ", ".join(column for column, _ in missing_columns)
        raise SchemaMigrationRequired(
            f"Database table '{table_name}' is missing columns: {names}. Run the "
            "deployment migrations before starting the web service."
        )
    for column, column_type in missing_columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {column_type}")


def ensure_agent_profile_table(conn, *, allow_ddl=False):
    if not table_columns(conn, "agent_profiles") and allow_ddl:
        conn.executescript(AGENT_PROFILE_SQL)
    ensure_table_columns(conn, "agent_profiles", PROFILE_COLUMN_TYPES, allow_ddl=allow_ddl)


def ensure_campus_state_table(conn, *, allow_ddl=False):
    if not table_columns(conn, "campus_state") and allow_ddl:
        conn.executescript(CAMPUS_STATE_SQL)
    ensure_table_columns(conn, "campus_state", ENV_COLUMN_TYPES, allow_ddl=allow_ddl)


def ensure_space_system(conn, *, allow_ddl=False, seed_demo_spaces=False):
    space_columns = table_columns(conn, "campus_spaces")
    event_columns = table_columns(conn, "campus_events")
    if (not space_columns or not event_columns) and allow_ddl:
        conn.executescript(SPACE_SYSTEM_SQL)
    ensure_table_columns(conn, "campus_spaces", {})
    ensure_table_columns(conn, "campus_events", {})
    # The retired seven-space campus is a fixture only; imported spatial data
    # remains the only production source of geography.
    if not seed_demo_spaces:
        return
    existing_codes = {
        row["code"] for row in conn.execute("SELECT code FROM campus_spaces").fetchall()
    }
    for space in DEFAULT_SPACES:
        if space[0] not in existing_codes:
            conn.execute(
                """INSERT OR IGNORE INTO campus_spaces
                (code, name, location, capacity, open_hour, close_hour, status, crowd_field, purpose)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                space,
            )


def ensure_agent_news_system(conn, *, allow_ddl=False):
    if allow_ddl:
        conn.executescript(AGENT_NEWS_SQL)
    ensure_table_columns(conn, "agent_news_posts", AGENT_NEWS_COLUMN_TYPES, allow_ddl=allow_ddl)


def ensure_external_information_system(conn, *, allow_ddl=False):
    if allow_ddl:
        conn.executescript(EXTERNAL_INFORMATION_SQL)
    ensure_table_columns(conn, "agent_information", AGENT_INFORMATION_COLUMNS, allow_ddl=allow_ddl)


def ensure_roadmap2_observer_system(conn, *, allow_ddl=False):
    if allow_ddl:
        conn.executescript(ROADMAP2_OBSERVER_SQL)
    ensure_table_columns(conn, "group_pattern_candidates", {}, allow_ddl=allow_ddl)
