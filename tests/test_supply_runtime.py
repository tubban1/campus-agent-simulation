import sqlite3
import unittest
import json
from datetime import datetime, timedelta, timezone

import app.main as main
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.models import SCHEMA_SQL
from app.supply.schema import SUPPLY_FOUNDATION_SQL
from app.supply.service import (
    consumption_availability,
    deliver_service,
    fulfill_runtime_consumption,
    process_supply_runtime,
    seed_supply_foundation,
)
from app.world_runtime.clock import WORLD_TZ
from tools.city_tools import buy_sell


class SupplyRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        for resident_id, name, role, money, location in (
            (1, "消费者", "学生", 100, "食堂"),
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
                VALUES (?, ?, ?, '稳定', '测试供应闭环', ?, ?)
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
        main.ensure_space_system(self.conn, allow_ddl=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(SUPPLY_FOUNDATION_SQL)
        self.first_seed = seed_supply_foundation(self.conn)
        self.start = datetime(2026, 7, 29, 10, 0, tzinfo=WORLD_TZ)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_seed_migrates_inventory_idempotently(self):
        second = seed_supply_foundation(self.conn)
        opening = self.conn.execute(
            "SELECT COUNT(*) value FROM inventory_movements WHERE movement_type = 'opening'"
        ).fetchone()["value"]
        self.assertEqual(self.first_seed["catalog_items"], 10)
        self.assertEqual(self.first_seed["production_recipes"], 4)
        self.assertEqual(self.first_seed["service_offerings"], 4)
        self.assertEqual(second["opening_movements_created"], 0)
        self.assertEqual(opening, 10)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_goods_trade_updates_stock_cash_and_cost(self):
        result = buy_sell(self.conn, 1, 5, "套餐饭", 2, 8)
        stocks = self.conn.execute(
            """
            SELECT owner_actor_key, quantity_on_hand
            FROM inventory_accounts account
            JOIN catalog_items item ON item.id = account.item_id
            WHERE item.name = '套餐饭' ORDER BY owner_actor_key
            """
        ).fetchall()
        self.assertEqual(
            [(row["owner_actor_key"], row["quantity_on_hand"]) for row in stocks],
            [("resident:1", 2), ("resident:5", 10)],
        )
        self.assertEqual(self.conn.execute("SELECT money FROM residents WHERE id = 5").fetchone()["money"], 296)
        self.assertIsNotNone(result["ledger_transaction_id"])
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_runtime_consumption_pays_provider_and_consumes_item(self):
        result = fulfill_runtime_consumption(self.conn, 1, "食堂", 800, None)
        buyer_stock = self.conn.execute(
            """
            SELECT quantity_on_hand FROM inventory_accounts account
            JOIN catalog_items item ON item.id = account.item_id
            WHERE account.owner_actor_key = 'resident:1' AND item.name = '套餐饭'
            """
        ).fetchone()
        self.assertEqual(result["provider_actor_key"], "resident:5")
        self.assertEqual(buyer_stock["quantity_on_hand"], 0)
        self.assertEqual(self.conn.execute("SELECT money FROM residents WHERE id = 1").fetchone()["money"], 92)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_stockout_is_visible_before_consumption(self):
        self.conn.execute(
            """
            UPDATE inventory_accounts SET quantity_on_hand = 0
            WHERE owner_actor_key = 'resident:5'
              AND item_id = (SELECT id FROM catalog_items WHERE name = '套餐饭')
            """
        )
        self.assertFalse(consumption_availability(self.conn, "食堂")["available"])
        with self.assertRaisesRegex(ValueError, "缺货"):
            fulfill_runtime_consumption(self.conn, 1, "食堂", 800, 78)

    def test_low_stock_starts_delayed_production(self):
        self.conn.execute(
            """
            UPDATE inventory_accounts SET quantity_on_hand = 0
            WHERE owner_actor_key = 'resident:5'
              AND item_id = (SELECT id FROM catalog_items WHERE name = '套餐饭')
            """
        )
        started = process_supply_runtime(self.conn, self.start)
        meal_batch = next(
            row for row in self.conn.execute(
                """
                SELECT batch.* FROM production_batches batch
                JOIN production_recipes recipe ON recipe.id = batch.recipe_id
                JOIN catalog_items item ON item.id = recipe.output_item_id
                WHERE item.name = '套餐饭'
                """
            ).fetchall()
        )
        completed = process_supply_runtime(
            self.conn,
            datetime.fromisoformat(meal_batch["due_at"]) + timedelta(seconds=1),
        )
        stock = self.conn.execute(
            """
            SELECT quantity_on_hand FROM inventory_accounts
            WHERE owner_actor_key = 'resident:5'
              AND item_id = (SELECT id FROM catalog_items WHERE name = '套餐饭')
            """
        ).fetchone()
        self.assertTrue(started["started"])
        self.assertIn(meal_batch["id"], completed["completed"])
        self.assertEqual(stock["quantity_on_hand"], 40)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_service_delivery_tracks_provider_and_payment(self):
        item_key = self.conn.execute(
            "SELECT item_key FROM catalog_items WHERE name = '跑腿券'"
        ).fetchone()["item_key"]
        delivery = deliver_service(
            self.conn,
            delivery_key="test:errand:1",
            offering_key=f"offering:{item_key}",
            consumer_actor_key="resident:1",
            consumer_resident_id=1,
            requested_at=self.start,
        )
        self.assertEqual(delivery["status"], "delivered")
        self.assertIsNotNone(delivery["ledger_transaction_id"])
        self.assertEqual(self.conn.execute("SELECT money FROM residents WHERE id = 1").fetchone()["money"], 94)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_service_capacity_creates_queue_without_payment(self):
        item_key = self.conn.execute(
            "SELECT item_key FROM catalog_items WHERE name = '跑腿券'"
        ).fetchone()["item_key"]
        offering_key = f"offering:{item_key}"
        self.conn.execute(
            "UPDATE service_offerings SET capacity_per_hour = 1 WHERE offering_key = ?",
            (offering_key,),
        )
        deliver_service(
            self.conn,
            delivery_key="test:errand:capacity:1",
            offering_key=offering_key,
            consumer_actor_key="resident:1",
            consumer_resident_id=1,
            requested_at=self.start,
        )
        queued = deliver_service(
            self.conn,
            delivery_key="test:errand:capacity:2",
            offering_key=offering_key,
            consumer_actor_key="resident:1",
            consumer_resident_id=1,
            requested_at=self.start + timedelta(minutes=1),
        )
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(json.loads(queued["result_json"])["queue_reason"], "capacity_full")
        self.assertIsNone(queued["ledger_transaction_id"])
        self.assertEqual(self.conn.execute("SELECT money FROM residents WHERE id = 1").fetchone()["money"], 94)

    def test_daily_waste_is_idempotent_and_balanced(self):
        late = self.start.replace(hour=22)
        first = process_supply_runtime(self.conn, late)
        second = process_supply_runtime(self.conn, late + timedelta(minutes=10))
        self.assertTrue(first["wasted"])
        self.assertEqual(second["wasted"], [])
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])


if __name__ == "__main__":
    unittest.main()
