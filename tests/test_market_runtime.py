import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import app.main as main
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.market.schema import MARKET_RUNTIME_SQL
from app.market.service import (
    evaluate_market_choice,
    find_market_mechanism,
    fulfill_market_goods_trade,
    process_market_runtime,
    quote_market_offer,
    record_market_demand,
    seed_market_runtime,
)
from app.models import SCHEMA_SQL
from app.supply.schema import SUPPLY_FOUNDATION_SQL
from app.supply.service import seed_supply_foundation
from tools.city_tools import buy_sell


class MarketRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        for resident_id, name, role, money, location in (
            (1, "消费者", "学生", 100, "商业街"),
            (5, "周老板", "食堂商家", 280, "食堂"),
            (6, "李姐", "奶茶店商家", 240, "商业街"),
            (8, "何管理员", "图书馆管理员", 100, "图书馆"),
            (9, "张晨", "运动社团负责人", 100, "操场"),
            (10, "校园后勤", "学校组织", 100, "校务处"),
            (20, "秦越", "校园创业者", 180, "商业街"),
        ):
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, '稳定', '测试市场闭环', ?, ?)
                """,
                (resident_id, name, role, money, location),
            )
        for resident_id, item_name, quantity in (
            (5, "套餐饭", 12), (5, "早餐券", 8),
            (6, "奶茶", 10), (6, "咖啡", 6),
            (8, "自习座位", 10), (9, "训练名额", 5),
            (10, "维修工单", 4), (20, "跑腿券", 3),
        ):
            self.conn.execute(
                "INSERT INTO inventory (resident_id, item_name, quantity) VALUES (?, ?, ?)",
                (resident_id, item_name, quantity),
            )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(SUPPLY_FOUNDATION_SQL)
        seed_supply_foundation(self.conn)
        self.conn.executescript(MARKET_RUNTIME_SQL)
        self.seed = seed_market_runtime(self.conn)
        self.start = datetime(2026, 7, 29, 10, 15, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def mechanism(self, item_name):
        return find_market_mechanism(
            self.conn,
            item_name=item_name,
            provider_actor_key="resident:6",
            location="商业街",
        )

    def test_seed_assigns_distinct_market_rules_idempotently(self):
        second = seed_market_runtime(self.conn)
        meal = find_market_mechanism(
            self.conn,
            item_name="套餐饭",
            provider_actor_key="resident:5",
            location="食堂",
        )
        drink = self.mechanism("奶茶")
        self.assertGreaterEqual(self.seed["mechanisms"], 8)
        self.assertEqual(second["mechanisms_created"], 0)
        self.assertEqual(meal["pricing_mode"], "rationed")
        self.assertEqual(drink["pricing_mode"], "dynamic")
        self.assertEqual(drink["search_cost_minor"], 100)

    def test_same_hour_quote_is_reproducible(self):
        mechanism = self.mechanism("奶茶")
        first = quote_market_offer(self.conn, mechanism["id"], self.start)
        second = quote_market_offer(
            self.conn,
            mechanism["id"],
            self.start + timedelta(minutes=30),
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["price_minor"], second["price_minor"])
        self.assertEqual(first["state_fingerprint"], second["state_fingerprint"])

    def test_shortage_and_demand_raise_next_hour_dynamic_price(self):
        mechanism = self.mechanism("奶茶")
        first = quote_market_offer(self.conn, mechanism["id"], self.start)
        self.conn.execute(
            """
            UPDATE inventory_accounts SET quantity_on_hand = 1
            WHERE owner_actor_key = 'resident:6' AND item_id = ?
            """,
            (mechanism["item_id"],),
        )
        for index in range(12):
            evaluation = evaluate_market_choice(
                self.conn,
                resident_id=1,
                mechanism_id=mechanism["id"],
                world_time=self.start + timedelta(minutes=index),
            )
            record_market_demand(
                self.conn,
                resident_id=1,
                evaluation=evaluation,
                world_time=self.start + timedelta(minutes=index),
            )
        later = quote_market_offer(
            self.conn,
            mechanism["id"],
            self.start + timedelta(hours=1),
        )
        self.assertGreater(later["price_minor"], first["price_minor"])
        self.assertGreater(later["inventory_pressure_basis_points"], 0)
        self.assertGreater(later["demand_pressure_basis_points"], 0)
        self.assertIn("库存压力", later["explanation"])

    def test_price_rejection_exposes_cheaper_substitute(self):
        milk_tea = self.mechanism("奶茶")
        coffee = self.mechanism("咖啡")
        self.conn.execute(
            """
            UPDATE market_mechanisms
            SET base_price_minor = 4000, floor_price_minor = 4000,
                ceiling_price_minor = 6000
            WHERE id = ?
            """,
            (milk_tea["id"],),
        )
        self.conn.execute(
            """
            UPDATE market_mechanisms
            SET base_price_minor = 400, floor_price_minor = 400,
                ceiling_price_minor = 800
            WHERE id = ?
            """,
            (coffee["id"],),
        )
        self.conn.execute("UPDATE residents SET money = 10 WHERE id = 1")
        evaluation = evaluate_market_choice(
            self.conn,
            resident_id=1,
            mechanism_id=milk_tea["id"],
            world_time=self.start,
        )
        self.assertEqual(evaluation["status"], "price_rejected")
        self.assertEqual(evaluation["substitute"]["item_name"], "咖啡")
        self.assertLess(
            evaluation["substitute"]["total_unit_cost_minor"],
            evaluation["total_unit_cost_minor"],
        )

    def test_fulfillment_splits_provider_revenue_and_friction_cost(self):
        self.conn.execute(
            "CREATE TABLE agent_body_states (resident_id INTEGER PRIMARY KEY, hunger INTEGER NOT NULL)"
        )
        self.conn.execute("INSERT INTO agent_body_states VALUES (1, 90)")
        mechanism = self.mechanism("奶茶")
        evaluation = evaluate_market_choice(
            self.conn,
            resident_id=1,
            mechanism_id=mechanism["id"],
            world_time=self.start,
        )
        result = fulfill_market_goods_trade(
            self.conn,
            resident_id=1,
            evaluation=evaluation,
            action_execution_id=None,
        )
        signal = self.conn.execute(
            "SELECT * FROM market_demand_signals WHERE id = ?",
            (result["demand_signal_id"],),
        ).fetchone()
        self.assertEqual(evaluation["status"], "accepted")
        self.assertEqual(result["friction_cost_minor"], 100)
        self.assertEqual(signal["status"], "fulfilled")
        self.assertEqual(
            self.conn.execute(
                "SELECT money FROM residents WHERE id = 6"
            ).fetchone()["money"],
            250,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT money FROM residents WHERE id = 1"
            ).fetchone()["money"],
            89,
        )
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_legacy_trade_cannot_override_system_market_price(self):
        with self.assertRaisesRegex(ValueError, "最高出价低于系统报价"):
            buy_sell(self.conn, 1, 6, "奶茶", 1, 1)
        result = buy_sell(self.conn, 1, 6, "奶茶", 1, 20)
        transaction = self.conn.execute(
            "SELECT * FROM transactions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(transaction["unit_price"], 11)
        self.assertIsNotNone(result["ledger_transaction_id"])

    def test_daily_quota_creates_rationing(self):
        meal = find_market_mechanism(
            self.conn,
            item_name="套餐饭",
            provider_actor_key="resident:5",
            location="食堂",
        )
        self.conn.execute(
            """
            INSERT INTO market_demand_signals
            (signal_key, resident_id, mechanism_id, item_id, quantity, status,
             occurred_at)
            VALUES ('quota-used', 1, ?, ?, 3, 'fulfilled', ?)
            """,
            (meal["id"], meal["item_id"], self.start.isoformat()),
        )
        evaluation = evaluate_market_choice(
            self.conn,
            resident_id=1,
            mechanism_id=meal["id"],
            world_time=self.start,
        )
        record_market_demand(
            self.conn,
            resident_id=1,
            evaluation=evaluation,
            world_time=self.start,
        )
        friction = self.conn.execute(
            "SELECT friction_type FROM market_friction_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(evaluation["status"], "rationed")
        self.assertEqual(friction["friction_type"], "rationing")

    def test_tick_generates_quotes_for_all_active_markets(self):
        result = process_market_runtime(self.conn, self.start)
        self.assertTrue(result["available"])
        self.assertEqual(len(result["quotes"]), self.seed["mechanisms"])


if __name__ == "__main__":
    unittest.main()
