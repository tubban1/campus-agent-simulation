import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from alembic import command

import app.main as main
from app.db import create_database_engine
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.models import SCHEMA_SQL
from app.spatial.facility_service import advance_facility_lifecycle, ensure_facility_states
from app.spatial.models import metadata as spatial_metadata
from app.supply.procurement import PROCUREMENT_FOUNDATION_SQL
from app.supply.schema import SUPPLY_FOUNDATION_SQL
from app.supply.service import seed_supply_foundation
from app.world_runtime.clock import WORLD_TZ


class FacilitySupplyWiringTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "campus.db"
        self.database_url = f"sqlite+pysqlite:///{self.db_path}"
        seed_conn = sqlite3.connect(str(self.db_path))
        seed_conn.row_factory = sqlite3.Row
        seed_conn.execute("PRAGMA foreign_keys = ON")
        seed_conn.executescript(SCHEMA_SQL)
        for rid, name, role, money, location in (
            (5, "周老板", "食堂商家", 1000, "清晏楼食堂"),
            (6, "李姐", "奶茶店商家", 240, "清晏楼食堂"),
        ):
            seed_conn.execute(
                "INSERT INTO residents (id, name, role, personality, goal, money, location) "
                "VALUES (?, ?, ?, '精打细算', '测试设施工单采购', ?, ?)",
                (rid, name, role, money, location),
            )
        seed_conn.commit()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(seed_conn, allow_ddl=True)
        main.ensure_space_system(seed_conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(seed_conn, allow_ddl=True)
        seed_conn.commit()
        seed_conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        config = get_alembic_config(self.database_url)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        self.engine = create_database_engine(self.database_url)
        spatial_metadata.create_all(self.engine)

        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(SUPPLY_FOUNDATION_SQL)
        self.conn.executescript(PROCUREMENT_FOUNDATION_SQL)
        seed_supply_foundation(self.conn)

        self.conn.execute(
            "INSERT INTO spatial_nodes (id, world_key, code, name, node_type, x, y, z, radius, capacity, status, properties) "
            "VALUES (11, 'default', 'canteen', '清晏楼食堂', 'building', 20.0, 0.0, 0.0, 10.0, 50, 'open', '{}')"
        )
        self.conn.execute(
            "INSERT INTO spatial_resources (node_id, resource_key, name, capacity, available_units, service_rate_per_hour, status, properties) "
            "VALUES (11, 'meal_stock', '食堂餐食库存', 120, 120, 120.0, 'available', '{\"actions\": [\"consume\"]}')"
        )
        ensure_facility_states(self.conn)
        self.conn.execute(
            "UPDATE spatial_facility_states SET open_hour = 0, close_hour = 24, "
            "inventory_units = 0, condition = 90, maintenance_status = 'operational' "
            "WHERE resource_id = (SELECT id FROM spatial_resources WHERE node_id = 11 AND resource_key = 'meal_stock')"
        )
        self.conn.execute(
            "INSERT INTO agent_spatial_states "
            "(resident_id, current_node_id, x, y, z, facing_x, facing_z, movement_status, progress, path, path_index, route_distance_meters, remaining_distance_meters, updated_tick, version, branch_key, replan_count, last_replan_reason) "
            "VALUES (5, 11, 20.0, 0.0, 0.0, 0.0, 1.0, 'idle', 1.0, '[]', 0, 0.0, 0.0, 0, 1, 'main', 0, '')"
        )
        self.conn.commit()
        self.start = datetime(2026, 7, 29, 12, 0, tzinfo=WORLD_TZ)

    def tearDown(self):
        self.engine.dispose()
        self.conn.close()
        self.temp_dir.cleanup()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_facility_restock_creates_procurement_order_and_fills_shelf(self):
        first = advance_facility_lifecycle(self.conn, day=2, hour=12)
        self.assertGreater(first["work_orders_created"], 0)
        second = advance_facility_lifecycle(self.conn, day=2, hour=12)
        self.assertGreater(second["work_orders_completed"], 0)
        orders = self.conn.execute(
            "SELECT * FROM procurement_orders WHERE status = 'fulfilled'"
        ).fetchall()
        self.assertGreaterEqual(len(orders), 1)
        state = self.conn.execute(
            """SELECT f.inventory_units, f.inventory_capacity
               FROM spatial_facility_states f
               JOIN spatial_resources r ON r.id = f.resource_id
               WHERE r.node_id = 11 AND r.resource_key = 'meal_stock'"""
        ).fetchone()
        self.assertGreater(state["inventory_units"], 0)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])


if __name__ == "__main__":
    unittest.main()
