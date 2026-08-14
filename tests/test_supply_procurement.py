import json
import sqlite3
import unittest
from datetime import datetime

import app.main as main
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.models import SCHEMA_SQL
from app.supply.procurement import (
    PROCUREMENT_FOUNDATION_SQL,
    external_goods_inflow,
    procure_inputs,
    seed_default_suppliers,
)
from app.supply.schema import SUPPLY_FOUNDATION_SQL
from app.supply.service import process_supply_runtime, seed_supply_foundation
from app.world_runtime.clock import WORLD_TZ


class ProcurementRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        for resident_id, name, role, money, location in (
            (1, "消费者", "学生", 100, "食堂"),
            (5, "周老板", "食堂商家", 280, "食堂"),
            (6, "李姐", "奶茶店商家", 240, "商业街"),
        ):
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, '稳定', '测试采购闭环', ?, ?)
                """,
                (resident_id, name, role, money, location),
            )
        for resident_id, item_name, quantity in (
            (5, "套餐饭", 12), (5, "早餐券", 8),
            (6, "奶茶", 10), (6, "咖啡", 6),
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
        self.conn.executescript(PROCUREMENT_FOUNDATION_SQL)
        self.seed = seed_supply_foundation(self.conn)
        self.start = datetime(2026, 7, 29, 10, 0, tzinfo=WORLD_TZ)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def _input_stock(self, owner, name):
        return self.conn.execute(
            """
            SELECT account.quantity_on_hand
            FROM inventory_accounts account
            JOIN catalog_items item ON item.id = account.item_id
            WHERE account.owner_actor_key = ? AND item.name = ?
            """,
            (owner, name),
        ).fetchone()["quantity_on_hand"]

    def test_seed_registers_suppliers_idempotently(self):
        self.assertEqual(self.seed["procurement_suppliers"], 3)
        second = seed_supply_foundation(self.conn)
        self.assertEqual(second["procurement_suppliers"], 3)
        suppliers = self.conn.execute(
            "SELECT COUNT(*) value FROM procurement_suppliers"
        ).fetchone()["value"]
        self.assertEqual(suppliers, 3)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_procure_replenishes_drained_input_and_closes_loop(self):
        # Drain the canteen's 食材包 input to zero; production would otherwise block.
        self.conn.execute(
            """
            UPDATE inventory_accounts SET quantity_on_hand = 0
            WHERE owner_actor_key = 'resident:5'
              AND item_id = (SELECT id FROM catalog_items WHERE name = '食材包')
            """
        )
        self.assertEqual(self._input_stock("resident:5", "食材包"), 0)
        result = procure_inputs(self.conn, self.start)
        self.assertTrue(result["available"])
        self.assertTrue(result["orders"])
        self.assertEqual(result["blocked"], [])
        self.assertGreater(self._input_stock("resident:5", "食材包"), 0)
        fulfilled = self.conn.execute(
            "SELECT COUNT(*) value FROM procurement_orders WHERE status = 'fulfilled'"
        ).fetchone()["value"]
        self.assertGreaterEqual(fulfilled, 1)
        # Goods moved, ledger stays balanced, money flowed producer -> supplier.
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_supply_runtime_runs_procurement_before_production(self):
        self.conn.execute(
            """
            UPDATE inventory_accounts SET quantity_on_hand = 0
            WHERE owner_actor_key = 'resident:5'
              AND item_id = (SELECT id FROM catalog_items WHERE name = '食材包')
            """
        )
        result = process_supply_runtime(self.conn, self.start)
        self.assertTrue(result["procurement"]["available"])
        self.assertGreater(self._input_stock("resident:5", "食材包"), 0)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_external_goods_inflow_is_cross_boundary_and_balanced(self):
        flow = external_goods_inflow(
            self.conn,
            buyer_actor_key="resident:6",
            item_name="饮品原料",
            quantity=5,
            unit_cost_minor=1800,
            source_actor_key="external:wholesale-test",
            transaction_key="test-import:1",
            source_id="test-import:1",
            occurred_at=self.start,
        )
        self.assertGreater(flow["quantity"], 0)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])
        movement = self.conn.execute(
            "SELECT * FROM inventory_movements WHERE movement_key = 'test-import:1:import'"
        ).fetchone()
        self.assertIsNotNone(movement)
        self.assertEqual(movement["movement_type"], "purchase")
        self.assertTrue(json.loads(movement["metadata_json"]).get("cross_boundary"))


if __name__ == "__main__":
    unittest.main()
