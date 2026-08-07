from __future__ import annotations

from typing import Optional

import os
from pathlib import Path
import re

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "city.db"
DEFAULT_DATABASE_SCHEMA = "public"


def get_database_schema() -> str:
    schema = os.getenv("DATABASE_SCHEMA", DEFAULT_DATABASE_SCHEMA).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError(
            "DATABASE_SCHEMA must start with a letter or underscore and contain "
            "only letters, numbers, and underscores."
        )
    return schema


def get_database_url() -> str:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        if configured.startswith("postgres://"):
            configured = "postgresql://" + configured.removeprefix("postgres://")
        if configured.startswith("postgresql://"):
            return "postgresql+psycopg://" + configured.removeprefix("postgresql://")
        return configured

    db_path = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return f"sqlite+pysqlite:///{db_path.resolve()}"


def create_database_engine(database_url: Optional[str] = None) -> Engine:
    url = database_url or get_database_url()
    options: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        options.update({"pool_size": 5, "max_overflow": 5})

    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    elif engine.dialect.name == "postgresql":
        schema = get_database_schema()
        event.listen(
            engine,
            "connect",
            lambda connection, _record: _set_postgres_search_path(
                connection, schema
            ),
        )
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


def _set_postgres_search_path(dbapi_connection, schema: str) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f'SET search_path TO "{schema}"')
    finally:
        cursor.close()
