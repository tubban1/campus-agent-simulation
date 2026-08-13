import sqlite3
import unittest
from datetime import datetime, timezone

import app.main as main
from app.budget.schema import BUDGET_RUNTIME_SQL
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.market.schema import MARKET_RUNTIME_SQL
from app.models import SCHEMA_SQL
from app.public_policy.schema import PUBLIC_POLICY_RUNTIME_SQL
from app.public_policy.service import (
    PUBLIC_FUND_CASH,
    generate_externalities,
    market_policy_terms,
    operate_public_services,
    process_public_policy_runtime,
    seed_public_policy_runtime,
    settle_market_policy_benefits,
)
from app.supply.schema import SUPPLY_FOUNDATION_SQL
from app.world_runtime.clock import WORLD_TZ


class PublicPolicyRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        for resident_id in range(1, 7):
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, '学生', '测试', '学习', ?, ?)
                """,
                (
                    resident_id,
                    f"居民{resident_id}",
                    20 if resident_id == 1 else 100,
                    "图书馆" if resident_id <= 5 else "食堂",
                ),
            )
            self.conn.execute(
                """
                INSERT INTO agent_profiles
                (resident_id, gender, avatar_style, energy, mood,
                 current_task, schedule, perception, strategy)
                VALUES (?, '女', '测试', 80, '平稳', '学习', '[]', '{}', '{}')
                """,
                (resident_id,),
            )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(SUPPLY_FOUNDATION_SQL)
        self.conn.executescript(BUDGET_RUNTIME_SQL)
        self.conn.executescript(MARKET_RUNTIME_SQL)
        self.conn.executescript(PUBLIC_POLICY_RUNTIME_SQL)
        self.now = datetime(2026, 7, 29, 10, 0, tzinfo=WORLD_TZ)
        self.conn.execute(
            """
            INSERT INTO catalog_items
            (item_key, name, item_type, base_price_minor, standard_cost_minor)
            VALUES ('item:meal', '套餐饭', 'good', 1000, 600)
            """
        )
        self.conn.execute(
            """
            INSERT INTO market_mechanisms
            (mechanism_key, item_id, provider_actor_key, location, pricing_mode,
             base_price_minor, floor_price_minor, ceiling_price_minor,
             target_supply, target_daily_demand)
            VALUES ('market:meal', 1, 'resident:2', '食堂', 'rationed',
                    1000, 600, 1600, 20, 10)
            """
        )
        for resident_id, income, disposable in (
            (1, 1000, 500),
            (2, 9000, 7000),
            (3, 4000, 3000),
            (4, 4000, 3000),
            (5, 4000, 3000),
            (6, 4000, 3000),
        ):
            self.conn.execute(
                """
                INSERT INTO household_budget_snapshots
                (snapshot_key, resident_id, budget_date, cash_minor,
                 savings_minor, expected_income_minor, disposable_minor,
                 time_budget_minutes, free_time_minutes, liquidity_status)
                VALUES (?, ?, '2026-07-29', 1000, 0, ?, ?, 1440, 600, 'stable')
                """,
                (f"budget:{resident_id}", resident_id, income, disposable),
            )
        self.seed = seed_public_policy_runtime(self.conn, self.now)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def balance(self, account_key):
        row = self.conn.execute(
            "SELECT balance_minor FROM ledger_accounts WHERE account_key = ?",
            (account_key,),
        ).fetchone()
        return int(row["balance_minor"])

    def test_seed_is_funded_and_idempotent(self):
        second = seed_public_policy_runtime(self.conn, self.now)
        self.assertEqual(self.seed["services"], 4)
        self.assertEqual(self.seed["policies"], 3)
        self.assertEqual(second["services_created"], 0)
        self.assertEqual(second["policies_created"], 0)
        self.assertEqual(second["funded_minor"], 0)
        self.assertGreater(self.balance(PUBLIC_FUND_CASH), 0)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_public_services_consume_explicit_budget_and_record_access(self):
        before = self.balance(PUBLIC_FUND_CASH)
        result = operate_public_services(self.conn, self.now)
        after = self.balance(PUBLIC_FUND_CASH)
        operations = self.conn.execute(
            "SELECT * FROM public_service_operations ORDER BY id"
        ).fetchall()
        self.assertEqual(len(result["operations"]), 4)
        self.assertEqual(len(operations), 4)
        self.assertLess(after, before)
        self.assertTrue(all(int(row["funded_cost_minor"]) > 0 for row in operations))
        self.assertGreater(
            self.conn.execute(
                "SELECT COUNT(*) value FROM public_service_usages WHERE status = 'served'"
            ).fetchone()["value"],
            0,
        )
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_underfunding_reduces_capacity_instead_of_creating_resources(self):
        self.conn.execute(
            "UPDATE ledger_accounts SET balance_minor = 100 WHERE account_key = ?",
            (PUBLIC_FUND_CASH,),
        )
        operate_public_services(self.conn, self.now)
        rows = self.conn.execute(
            "SELECT * FROM public_service_operations ORDER BY id"
        ).fetchall()
        self.assertTrue(any(row["status"] == "underfunded" for row in rows))
        self.assertTrue(
            all(int(row["funded_cost_minor"]) <= int(row["operating_cost_minor"]) for row in rows)
        )

    def test_colocation_creates_traceable_negative_externality_exposure(self):
        result = generate_externalities(self.conn, self.now)
        event = self.conn.execute(
            """
            SELECT * FROM externality_events
            WHERE location = '图书馆' AND direction = 'negative'
            """
        ).fetchone()
        exposures = self.conn.execute(
            "SELECT * FROM externality_exposures WHERE externality_event_id = ?",
            (event["id"],),
        ).fetchall()
        self.assertIn(int(event["id"]), result["events"])
        self.assertEqual(len(exposures), 5)
        self.assertTrue(all(int(row["welfare_delta"]) < 0 for row in exposures))

    def test_recorded_waste_creates_local_pollution_externality(self):
        self.conn.execute(
            """
            INSERT INTO inventory_accounts
            (inventory_key, owner_actor_key, item_id, location,
             quantity_on_hand, reorder_point, target_stock)
            VALUES ('inventory:waste-test', 'resident:2', 1, '图书馆', 4, 0, 4)
            """
        )
        self.conn.execute(
            """
            INSERT INTO inventory_movements
            (movement_key, inventory_account_id, movement_type, quantity_delta,
             source_type, occurred_at)
            VALUES ('waste:test', 1, 'waste', -2, 'test', ?)
            """,
            (self.now.isoformat(),),
        )
        generate_externalities(self.conn, self.now)
        event = self.conn.execute(
            """
            SELECT * FROM externality_events
            WHERE externality_type = 'pollution' AND source_type = 'inventory_movement'
            """
        ).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(event["location"], "图书馆")
        self.assertGreater(
            self.conn.execute(
                """
                SELECT COUNT(*) value FROM externality_exposures
                WHERE externality_event_id = ?
                """,
                (event["id"],),
            ).fetchone()["value"],
            0,
        )

    def test_same_policy_has_heterogeneous_income_group_effects(self):
        low = market_policy_terms(
            self.conn,
            resident_id=1,
            mechanism_id=1,
            gross_price_minor=1100,
            world_time=self.now,
        )
        high = market_policy_terms(
            self.conn,
            resident_id=2,
            mechanism_id=1,
            gross_price_minor=1100,
            world_time=self.now,
        )
        self.assertEqual(low["capped_price_minor"], 900)
        self.assertEqual(high["capped_price_minor"], 900)
        self.assertGreater(low["subsidy_minor"], 0)
        self.assertEqual(high["subsidy_minor"], 0)
        self.assertLess(low["private_price_minor"], high["private_price_minor"])

    def test_subsidy_cost_is_paid_to_provider_and_budget_limited(self):
        terms = market_policy_terms(
            self.conn,
            resident_id=1,
            mechanism_id=1,
            gross_price_minor=1000,
            world_time=self.now,
        )
        provider_before = self.balance("resident:2:cash")
        fund_before = self.balance(PUBLIC_FUND_CASH)
        benefits = settle_market_policy_benefits(
            self.conn,
            resident_id=1,
            mechanism_id=1,
            provider_actor_key="resident:2",
            policy_terms=terms,
            quantity=1,
            source_key="test:meal:1",
            world_time=self.now,
        )
        public_cost = sum(int(row["public_cost_minor"]) for row in benefits)
        self.assertGreater(public_cost, 0)
        self.assertEqual(self.balance("resident:2:cash"), provider_before + public_cost)
        self.assertEqual(self.balance(PUBLIC_FUND_CASH), fund_before - public_cost)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_runtime_is_daily_idempotent_and_captures_group_outcomes(self):
        first = process_public_policy_runtime(self.conn, self.now)
        second = process_public_policy_runtime(self.conn, self.now)
        self.assertTrue(first["available"])
        self.assertEqual(
            len(first["public_services"]["operations"]),
            len(second["public_services"]["operations"]),
        )
        self.assertGreater(
            self.conn.execute(
                "SELECT COUNT(*) value FROM policy_outcome_snapshots"
            ).fetchone()["value"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
