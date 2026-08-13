import sqlite3
import unittest
from datetime import datetime, timezone

import app.main as main
from app.budget.schema import BUDGET_RUNTIME_SQL
from app.credit.schema import CREDIT_RUNTIME_SQL
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.labor.schema import LABOR_RUNTIME_SQL
from app.labor.service import _post_income_transaction
from app.macro.schema import MACRO_RUNTIME_SQL
from app.macro.service import (
    build_macro_snapshot,
    reconcile_macro_snapshot,
    seed_macro_runtime,
)
from app.market.schema import MARKET_RUNTIME_SQL
from app.models import SCHEMA_SQL
from app.organizations.schema import ORGANIZATION_RUNTIME_SQL
from app.public_policy.schema import PUBLIC_POLICY_RUNTIME_SQL
from app.supply.schema import SUPPLY_FOUNDATION_SQL


class MacroRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        for resident_id, role, money in (
            (1, "大一学生", 20),
            (2, "教师", 100),
            (3, "学生会干部", 60),
        ):
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, '测试', '参与校园经济', ?, '图书馆')
                """,
                (resident_id, f"居民{resident_id}", role, money),
            )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(ORGANIZATION_RUNTIME_SQL)
        self.conn.executescript(SUPPLY_FOUNDATION_SQL)
        self.conn.executescript(LABOR_RUNTIME_SQL)
        self.conn.executescript(BUDGET_RUNTIME_SQL)
        self.conn.executescript(MARKET_RUNTIME_SQL)
        self.conn.executescript(CREDIT_RUNTIME_SQL)
        self.conn.executescript(PUBLIC_POLICY_RUNTIME_SQL)
        self.conn.executescript(MACRO_RUNTIME_SQL)
        self.now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        for resident_id, expected, disposable in (
            (1, 1000, 500),
            (2, 9000, 7000),
            (3, 4000, 3000),
        ):
            self.conn.execute(
                """
                INSERT INTO household_budget_snapshots
                (snapshot_key, resident_id, budget_date, cash_minor,
                 savings_minor, expected_income_minor, disposable_minor,
                 time_budget_minutes, free_time_minutes, liquidity_status)
                VALUES (?, ?, '2026-07-29', 1000, 0, ?, ?,
                        1440, 600, 'stable')
                """,
                (f"budget:{resident_id}", resident_id, expected, disposable),
            )
        self.conn.execute(
            """
            INSERT INTO catalog_items
            (item_key, name, item_type, base_price_minor, standard_cost_minor)
            VALUES ('meal', '套餐饭', 'good', 1000, 600)
            """
        )
        self.conn.execute(
            """
            INSERT INTO inventory_accounts
            (inventory_key, owner_actor_key, item_id, location,
             quantity_on_hand, reorder_point, target_stock, average_cost_minor)
            VALUES ('inventory:meal', 'resident:2', 1, '食堂', 10, 2, 10, 600)
            """
        )
        self.conn.execute(
            """
            INSERT INTO inventory_movements
            (movement_key, inventory_account_id, movement_type, quantity_delta,
             unit_cost_minor, source_type, occurred_at)
            VALUES ('opening:meal', 1, 'opening', 10, 600, 'test', ?)
            """,
            (self.now.isoformat(),),
        )
        self.conn.execute(
            """
            INSERT INTO market_mechanisms
            (mechanism_key, item_id, provider_actor_key, location, pricing_mode,
             base_price_minor, floor_price_minor, ceiling_price_minor,
             target_supply, target_daily_demand)
            VALUES ('market:meal', 1, 'resident:2', '食堂', 'dynamic',
                    1000, 600, 1800, 10, 10)
            """
        )
        self.conn.execute(
            """
            INSERT INTO market_price_snapshots
            (quote_key, mechanism_id, price_minor, base_price_minor,
             available_supply, observed_demand, fulfilled_demand,
             explanation, valid_from, valid_until, state_fingerprint)
            VALUES ('quote:meal', 1, 1200, 1000, 10, 4, 3,
                    '需求上升', ?, ?, 'test')
            """,
            (self.now.isoformat(), self.now.replace(hour=13).isoformat()),
        )
        transaction = _post_income_transaction(
            self.conn,
            transaction_key="income:test:1",
            payment_type="wage",
            payer_actor_key="resident:2",
            recipient_actor_key="resident:1",
            amount_minor=1000,
            source_type="test",
            source_id="income:1",
            metadata={},
        )
        self.conn.execute(
            """
            INSERT INTO income_payments
            (payment_key, payment_type, payer_actor_key, recipient_actor_key,
             amount_minor, status, ledger_transaction_id, due_date, paid_at)
            VALUES ('income:1', 'wage', 'resident:2', 'resident:1',
                    1000, 'posted', ?, '2026-07-29', ?)
            """,
            (transaction["id"], self.now.isoformat()),
        )

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def metric(self, snapshot_id, metric_key, group_type="overall", group_key="all"):
        return self.conn.execute(
            """
            SELECT value.* FROM macro_metric_values value
            JOIN macro_metric_definitions definition
              ON definition.id = value.metric_definition_id
            WHERE value.snapshot_id = ? AND definition.metric_key = ?
              AND value.group_type = ? AND value.group_key = ?
            """,
            (snapshot_id, metric_key, group_type, group_key),
        ).fetchone()

    def test_snapshot_is_traceable_grouped_and_idempotent(self):
        seeded = seed_macro_runtime(self.conn)
        first = build_macro_snapshot(self.conn, self.now)
        second = build_macro_snapshot(self.conn, self.now)
        income = self.metric(first["snapshot_id"], "income_flow_minor")
        low_cash = self.metric(
            first["snapshot_id"], "total_cash_minor", "income_group", "low"
        )
        components = self.conn.execute(
            """
            SELECT COUNT(*) value FROM macro_metric_components
            WHERE metric_value_id = ?
            """,
            (income["id"],),
        ).fetchone()["value"]
        self.assertEqual(seeded["definitions"], 18)
        self.assertEqual(first["status"], "valid")
        self.assertEqual(int(income["value"]), 1000)
        self.assertGreater(int(low_cash["value"]), 0)
        self.assertEqual(components, 1)
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertFalse(second["changed"])
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_new_source_fact_refreshes_current_daily_snapshot(self):
        first = build_macro_snapshot(self.conn, self.now)
        self.conn.execute(
            """
            INSERT INTO market_demand_signals
            (signal_key, resident_id, mechanism_id, item_id, quantity,
             need_score, preference_score, disposable_budget_minor,
             maximum_unit_price_minor, quoted_unit_price_minor,
             final_unit_price_minor, status, occurred_at)
            VALUES ('demand:1', 1, 1, 1, 2, 80, 70, 2000,
                    1500, 1200, 1200, 'fulfilled', ?)
            """,
            (self.now.isoformat(),),
        )
        second = build_macro_snapshot(self.conn, self.now)
        fulfilled = self.metric(second["snapshot_id"], "fulfilled_demand_count")
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertTrue(second["changed"])
        self.assertEqual(int(fulfilled["value"]), 2)

    def test_inventory_mismatch_invalidates_snapshot(self):
        snapshot = build_macro_snapshot(self.conn, self.now)
        self.conn.execute(
            "UPDATE inventory_accounts SET quantity_on_hand = 8 WHERE id = 1"
        )
        reconciliation = reconcile_macro_snapshot(
            self.conn, snapshot["snapshot_id"], self.now
        )
        check = self.conn.execute(
            """
            SELECT * FROM macro_reconciliation_checks
            WHERE snapshot_id = ? AND check_key = 'inventory-movements'
            """,
            (snapshot["snapshot_id"],),
        ).fetchone()
        self.assertEqual(reconciliation["status"], "invalid")
        self.assertEqual(check["status"], "failed")
        self.assertEqual(int(check["actual_value"]), 2)


if __name__ == "__main__":
    unittest.main()
