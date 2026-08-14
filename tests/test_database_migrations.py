import os
import sqlite3
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from alembic import command

from app.db import DB_PATH, create_database_engine, get_database_schema, get_database_url
from app.db.engine import DEFAULT_DB_PATH
from app.db.migration_runtime import (
    BASELINE_REQUIRED_TABLES,
    BASELINE_REVISION,
    READINESS_REQUIRED_TABLES,
    get_alembic_config,
    get_current_revision,
    get_head_revision,
    list_business_tables,
    migrate_pending_to_head,
)
from scripts.migrate_db import migrate_database


class DatabaseMigrationFoundationTest(unittest.TestCase):
    def test_readiness_covers_current_spatial_runtime_contract(self):
        self.assertTrue({
            "spatial_nodes",
            "spatial_edges",
            "spatial_physical_states",
            "spatial_edge_physical_states",
            "spatial_facility_states",
            "spatial_facility_work_orders",
            "social_interaction_sessions",
            "social_session_participants",
            "social_session_turns",
        }.issubset(READINESS_REQUIRED_TABLES))

    @staticmethod
    def create_required_baseline_tables(db_path):
        connection = sqlite3.connect(db_path)
        for table_name in BASELINE_REQUIRED_TABLES:
            if table_name == "simulation_state":
                connection.execute(
                    "CREATE TABLE simulation_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
            else:
                connection.execute(
                    f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)"
                )
        connection.commit()
        connection.close()

    def test_sqlite_url_uses_absolute_db_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"DB_PATH": str(Path(tmp_dir) / "campus.db")},
            clear=False,
        ), patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            url = get_database_url()

        self.assertTrue(url.startswith("sqlite+pysqlite:////"))
        self.assertTrue(url.endswith("/campus.db"))

    def test_legacy_and_migration_layers_share_default_sqlite_path(self):
        self.assertEqual(DB_PATH.resolve(), DEFAULT_DB_PATH.resolve())

    def test_database_schema_is_validated(self):
        with patch.dict(
            os.environ,
            {"DATABASE_SCHEMA": "campus_runtime"},
            clear=False,
        ):
            self.assertEqual(get_database_schema(), "campus_runtime")
        with patch.dict(
            os.environ,
            {"DATABASE_SCHEMA": "invalid-schema"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "DATABASE_SCHEMA"):
                get_database_schema()

    def test_render_postgres_url_uses_psycopg_driver(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgres://user:pass@example.test/campus"},
            clear=False,
        ):
            url = get_database_url()

        self.assertEqual(
            url, "postgresql+psycopg://user:pass@example.test/campus"
        )

    def test_sqlite_engine_enables_foreign_keys(self):
        engine = create_database_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.connect() as connection:
                enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            self.assertEqual(enabled, 1)
        finally:
            engine.dispose()

    def test_legacy_database_can_be_stamped_and_upgraded_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "legacy.db"
            sqlite3.connect(db_path).execute(
                "CREATE TABLE residents (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            ).connection.close()
            env = {"DATABASE_URL": "", "DB_PATH": str(db_path)}
            with patch.dict(os.environ, env, clear=False):
                engine = create_database_engine()
                config = get_alembic_config()
                try:
                    self.assertEqual(list_business_tables(engine), ["residents"])
                    self.assertIsNone(get_current_revision(engine))
                    head_revision = get_head_revision(config)
                    self.assertNotEqual(head_revision, BASELINE_REVISION)

                    command.stamp(config, BASELINE_REVISION)
                    command.upgrade(config, "head")
                    command.upgrade(config, "head")

                    self.assertEqual(
                        get_current_revision(engine), head_revision
                    )
                    self.assertTrue(
                        {
                            "economic_actors",
                            "ledger_accounts",
                            "ledger_transactions",
                            "ledger_entries",
                            "ledger_authorization_rules",
                            "ledger_authorized_operations",
                            "ledger_reversals",
                            "ledger_audit_events",
                            "organization_runtime_profiles",
                            "organization_roles",
                            "organization_role_assignments",
                            "organization_proposals",
                            "organization_votes",
                            "organization_commitments",
                            "organization_relationships",
                            "organization_events",
                            "catalog_items",
                            "inventory_accounts",
                            "production_recipes",
                            "production_recipe_inputs",
                            "production_batches",
                            "inventory_movements",
                            "service_offerings",
                            "service_deliveries",
                            "labor_positions",
                            "employment_contracts",
                            "labor_shifts",
                            "income_programs",
                            "income_payments",
                            "expense_obligations",
                            "household_budget_profiles",
                            "household_budget_snapshots",
                            "savings_transfers",
                            "choice_evaluations",
                            "market_mechanisms",
                            "market_price_snapshots",
                            "market_demand_signals",
                            "market_friction_events",
                            "savings_goals",
                            "household_risk_profiles",
                            "economic_shocks",
                            "risk_pool_claims",
                            "credit_products",
                            "credit_profiles",
                            "credit_contracts",
                            "credit_installments",
                            "credit_payments",
                            "credit_events",
                            "public_services",
                            "public_service_operations",
                            "public_service_usages",
                            "externality_events",
                            "externality_exposures",
                            "policy_instruments",
                            "policy_benefits",
                            "policy_outcome_snapshots",
                            "communication_channels",
                            "information_claims",
                            "information_versions",
                            "information_transmissions",
                            "information_exposures",
                            "information_beliefs",
                            "institutional_rules",
                            "institutional_cases",
                            "institutional_decisions",
                            "resident_power_profiles",
                            "institutional_trust_events",
                            "macro_metric_definitions",
                            "macro_snapshots",
                            "macro_metric_values",
                            "macro_metric_components",
                            "macro_reconciliation_checks",
                        }.issubset(set(list_business_tables(engine)))
                    )
                finally:
                    engine.dispose()

    def test_legacy_upsert_index_migration_never_deletes_business_rows(self):
        migration = import_module(
            "app.db.migrations.versions.20260731_0034_legacy_runtime_upsert_indexes"
        )
        migration_source = Path(migration.__file__).read_text()
        indexed_tables = {table for _name, table, _columns in migration.UPSERT_INDEXES}

        self.assertNotIn("DELETE FROM", migration_source.upper())
        self.assertNotIn("agent_goals", indexed_tables)
        self.assertNotIn("world_action_rules", indexed_tables)

    def test_migration_runner_rejects_empty_database(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "DB_PATH": str(Path(tmp_dir) / "empty.db"),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "no fresh-world schema"):
                migrate_database()

    def test_migration_runner_rejects_unmarked_preexisting_schema(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "legacy.db"
            self.create_required_baseline_tables(db_path)
            with patch.dict(
                os.environ,
                {"DATABASE_URL": "", "DB_PATH": str(db_path)},
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "not a freshly bootstrapped world"):
                    migrate_database()

    def test_migration_runner_rejects_incomplete_legacy_schema(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "legacy.db"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE residents (id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            with patch.dict(
                os.environ,
                {"DATABASE_URL": "", "DB_PATH": str(db_path)},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "missing required baseline tables"
                ):
                    migrate_database()


    def test_migrate_pending_to_head_applies_missing_runtime_table(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "campus.db"
            self.create_required_baseline_tables(db_path)
            with patch.dict(
                os.environ,
                {"DATABASE_URL": "", "DB_PATH": str(db_path)},
                clear=False,
            ):
                engine = create_database_engine()
                config = get_alembic_config()
                try:
                    command.stamp(config, BASELINE_REVISION)
                    # Stop one revision before the spatial_edge_physical_states
                    # table so the pending helper has real work to do.
                    command.upgrade(config, "20260813_0045")
                    self.assertNotIn(
                        "spatial_edge_physical_states", list_business_tables(engine)
                    )

                    result = migrate_pending_to_head()

                    self.assertTrue(result["applied"])
                    self.assertEqual(result["from_revision"], "20260813_0045")
                    self.assertIn(
                        "spatial_edge_physical_states", list_business_tables(engine)
                    )
                    self.assertEqual(
                        get_current_revision(engine), get_head_revision(config)
                    )
                finally:
                    engine.dispose()

    def test_migrate_pending_to_head_is_idempotent_at_head(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "campus.db"
            self.create_required_baseline_tables(db_path)
            with patch.dict(
                os.environ,
                {"DATABASE_URL": "", "DB_PATH": str(db_path)},
                clear=False,
            ):
                engine = create_database_engine()
                config = get_alembic_config()
                try:
                    command.stamp(config, BASELINE_REVISION)
                    command.upgrade(config, "head")

                    result = migrate_pending_to_head()

                    self.assertEqual(result["applied"], False)
                    self.assertEqual(result["reason"], "already_at_head")
                finally:
                    engine.dispose()

    def test_migrate_pending_to_head_skips_unversioned_schema(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "campus.db"
            self.create_required_baseline_tables(db_path)
            with patch.dict(
                os.environ,
                {"DATABASE_URL": "", "DB_PATH": str(db_path)},
                clear=False,
            ):
                result = migrate_pending_to_head()

                self.assertEqual(result["applied"], False)
                self.assertEqual(result["reason"], "unversioned_schema")


if __name__ == "__main__":
    unittest.main()
