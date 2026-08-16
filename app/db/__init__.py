from contextlib import contextmanager
from pathlib import Path
import logging
import sqlite3
import os
import re
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
load_dotenv(PROJECT_ROOT / ".env")
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "city.db"))).expanduser()
if not DB_PATH.is_absolute():
    DB_PATH = (PROJECT_ROOT / DB_PATH).resolve()

POSTGRES_ID_TABLES = {
    "agent_action_plans", "agent_learning", "agent_news_posts", "campus_events",
    "agent_commitments", "agent_goals", "calibration_observations",
    "calibration_reports", "campus_schedule_rules", "environment_configs",
    "city_events", "collaborations", "competitions", "external_information",
    "goal_dependencies", "goal_revisions", "group_goals", "inventory",
    "long_term_goals", "memories", "model_call_logs", "observer_sessions",
    "participant_actions", "plan_outcomes", "policies", "residents",
    "research_export_jobs", "simulation_action_logs", "transactions",
    "trajectory_episodes", "world_causal_weights", "world_event_stream",
    "world_snapshots", "world_ticks", "experiment_runs", "world_action_rules",
    "world_action_executions", "world_delayed_effects",
    "world_resource_accounts", "world_resource_transfers",
    "world_update_schedules", "world_update_runs",
    "world_branches",
    "economic_actors", "ledger_accounts", "ledger_transactions",
    "ledger_entries", "ledger_authorization_rules", "ledger_audit_events",
    "organization_roles", "organization_proposals", "organization_commitments",
    "organization_events",
    "catalog_items", "inventory_accounts", "production_recipes",
    "production_batches", "inventory_movements", "service_offerings",
    "service_deliveries",
    "labor_positions", "employment_contracts", "labor_shifts",
    "income_programs", "income_payments", "expense_obligations",
    "household_budget_snapshots", "savings_transfers",
    "choice_evaluations",
    "market_mechanisms", "market_price_snapshots",
    "market_demand_signals", "market_friction_events",
    "savings_goals", "economic_shocks", "risk_pool_claims",
    "credit_products", "credit_contracts", "credit_installments",
    "credit_payments", "credit_events",
    "public_services", "public_service_operations", "public_service_usages",
    "externality_events", "externality_exposures", "policy_instruments",
    "policy_benefits", "policy_outcome_snapshots",
    "communication_channels", "information_claims", "information_versions",
    "information_transmissions", "information_exposures",
    "institutional_rules", "institutional_cases", "institutional_decisions",
    "institutional_trust_events",
    "macro_metric_definitions", "macro_snapshots", "macro_metric_values",
    "macro_metric_components", "macro_reconciliation_checks",
    "constraint_rules", "constraint_evaluations", "boundary_attempts",
    "constraint_consequences",
    "experience_records", "adaptive_memories", "memory_revisions",
    "strategy_states", "learning_updates",
    "norm_signals", "norm_candidates", "norm_evidence",
    "norm_state_transitions", "norm_responses",
    "rule_primitives", "institutional_rule_proposals",
    "rule_deliberations", "evolved_rule_versions", "rule_effect_reviews",
    "shock_definitions", "shock_instances", "shock_impacts",
    "resident_shock_exposures", "recovery_actions",
    "shock_state_transitions",
    "population_events", "resident_role_assignments",
    "resident_residency_periods", "membership_transitions",
    "population_effects",
    "external_sources", "external_sync_runs", "external_raw_observations",
    "external_events", "external_event_links", "external_data_snapshots",
    "external_exposures", "external_replay_deliveries",
    "external_impact_rules", "external_event_impacts",
    "external_state_reconciliations", "external_governance_reviews",
    "external_access_audit", "external_snapshot_exports",
    "external_experiment_bindings",
    "life_course_stages", "life_turning_points", "path_dependency_links",
    "longitudinal_aggregations", "trajectory_reconciliations",
    "social_interaction_sessions", "social_session_participants",
    "social_session_turns", "spatial_facility_work_orders",
    "agent_expectations", "continuity_observations", "agent_hypotheses",
    "group_pattern_candidates",
}

POSTGRES_TABLE_COLUMNS_CACHE = {}


def using_postgres() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _postgres_sql(sql: str) -> str:
    """Translate the SQLite syntax used by this project to PostgreSQL."""
    statement = sql.strip()
    pragma = re.fullmatch(r"PRAGMA table_info\((\w+)\)", statement, re.IGNORECASE)
    if pragma:
        return (
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position"
        )

    statement = re.sub(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+simulation_state",
        "INSERT INTO simulation_state",
        statement,
        flags=re.IGNORECASE,
    )
    if statement.upper().startswith("INSERT INTO SIMULATION_STATE"):
        statement = re.sub(
            r"\)\s*VALUES\s*\((.*?)\)$",
            r") VALUES (\1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            statement,
            flags=re.IGNORECASE | re.DOTALL,
        )
    elif "INSERT OR IGNORE" in sql.upper():
        statement = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", statement, flags=re.IGNORECASE)
        if "ON CONFLICT" not in statement.upper():
            statement = f"{statement.rstrip(';')} ON CONFLICT DO NOTHING"

    # PostgreSQL requires the target table on the left side of this upsert.
    statement = re.sub(
        r"SET\s+quantity\s*=\s*quantity\s*\+\s*excluded\.quantity",
        "SET quantity = inventory.quantity + excluded.quantity",
        statement,
        flags=re.IGNORECASE,
    )
    # psycopg treats a literal `%` as the start of a placeholder whenever a
    # parameter tuple is supplied.  Escape such literal percent signs (for
    # example LIKE '%图书馆%' used alongside a `?`) so they are preserved;
    # this must happen before converting `?` -> `%s` so the generated
    # placeholders stay intact.
    statement = statement.replace("%", "%%")
    return statement.replace("?", "%s")


def _postgres_script(sql: str) -> list[str]:
    normalized = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", sql, flags=re.IGNORECASE)
    return [statement.strip() for statement in normalized.split(";") if statement.strip()]



@contextmanager
def db_savepoint(conn, name: str = "sp"):
    """Nest a recoverable sub-transaction inside the caller's transaction.

    If the body raises after aborting the underlying transaction (which in
    PostgreSQL leaves the whole transaction unusable until rolled back), roll
    back only this savepoint so the caller can keep working on a clean
    transaction.  Works for both SQLite, SQLAlchemy connections, and the psycopg-backed wrapper.
    """
    def _exec_sql(stmt: str):
        if hasattr(conn, "exec_driver_sql"):
            conn.exec_driver_sql(stmt)
        else:
            try:
                conn.execute(stmt)
            except Exception:
                from sqlalchemy import text
                conn.execute(text(stmt))

    _exec_sql(f"SAVEPOINT {name}")
    try:
        yield
    except Exception:
        try:
            _exec_sql(f"ROLLBACK TO SAVEPOINT {name}")
        except Exception:
            pass
        raise
    else:
        try:
            _exec_sql(f"RELEASE SAVEPOINT {name}")
        except Exception:
            pass


class PostgresCursor:
    def __init__(self, cursor, lastrowid=None, rowcount=0):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class CachedPostgresCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.lastrowid = None
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    def execute(self, sql, params=()):
        if not isinstance(sql, str) and hasattr(sql, "compile"):
            from sqlalchemy.dialects import postgresql
            compiled = sql.compile(dialect=postgresql.dialect())
            statement = str(compiled)
            execute_params = compiled.params
            raw_sql_str = statement
        else:
            raw_sql_str = str(sql)
            statement = _postgres_sql(raw_sql_str)
            execute_params = params

        pragma = re.fullmatch(r"PRAGMA table_info\((\w+)\)", raw_sql_str.strip(), re.IGNORECASE)
        if pragma:
            from app.db.engine import get_database_schema

            schema = get_database_schema()
            table_name = pragma.group(1)
            cache_key = (schema, table_name)
            cached_rows = POSTGRES_TABLE_COLUMNS_CACHE.get(cache_key)
            if cached_rows is not None:
                return CachedPostgresCursor(cached_rows)
            execute_params = (schema, table_name)
        table_match = re.search(r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)", raw_sql_str, re.IGNORECASE)
        table = table_match.group(1).lower() if table_match else ""
        needs_id = table in POSTGRES_ID_TABLES and "RETURNING" not in statement.upper()
        if needs_id:
            statement = f"{statement.rstrip(';')} RETURNING id"

        try:
            if execute_params:
                cursor = self._connection.execute(statement, execute_params)
            else:
                # Passing an empty tuple makes psycopg parse literal percent signs
                # as placeholders (for example LIKE '%学生%').
                cursor = self._connection.execute(statement)
        except Exception as exc:
            # Log the statement that actually failed so a swallowed abort inside
            # a world tick cannot hide the underlying cause behind the later
            # generic InFailedSqlTransaction error.
            logger.warning(
                "Postgres statement failed (%s): %s\nSQL: %s",
                type(exc).__name__, exc, statement,
            )
            raise
        if pragma:
            rows = cursor.fetchall()
            POSTGRES_TABLE_COLUMNS_CACHE[cache_key] = tuple(rows)
            return CachedPostgresCursor(rows)
        rowcount = cursor.rowcount
        inserted_row = cursor.fetchone() if needs_id else None
        lastrowid = inserted_row["id"] if inserted_row else None
        return PostgresCursor(cursor, lastrowid, rowcount)

    def executescript(self, sql: str):
        for statement in _postgres_script(sql):
            self._connection.execute(statement)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def get_connection():
    if using_postgres():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL support requires psycopg[binary].") from exc
        from app.db.engine import get_database_schema

        try:
            connection = psycopg.connect(
                os.environ["DATABASE_URL"].strip(),
                row_factory=dict_row,
                prepare_threshold=None,
            )
            connection.execute(
                f'SET search_path TO "{get_database_schema()}"'
            )
            return PostgresConnection(connection)
        except Exception as exc:
            logger.warning(
                "PostgreSQL connection failed (%s: %s). Falling back to local SQLite DB at %s",
                type(exc).__name__, exc, DB_PATH,
            )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def execute_script(sql: str) -> None:
    with get_connection() as conn:
        conn.executescript(sql)
        conn.commit()


from app.db.engine import (  # noqa: E402
    create_database_engine,
    get_database_schema,
    get_database_url,
)

__all__ = [
    "DB_PATH",
    "PostgresConnection",
    "create_database_engine",
    "db_savepoint",
    "execute_script",
    "get_connection",
    "get_database_url",
    "get_database_schema",
    "using_postgres",
]
