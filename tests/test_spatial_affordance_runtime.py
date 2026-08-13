"""Automated isolated tests for Phase 3.6A Spatial Affordances & Atomic Actions."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alembic import command
import app.main as main
from app.db import create_database_engine
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.models import SCHEMA_SQL
from app.spatial.affordance_service import (
    seed_spatial_affordances,
    get_spatial_affordances,
    discover_agent_affordance_opportunities,
)
from app.world_runtime.action_execution import (
    get_current_spatial_action_context,
    process_world_agent_tick,
)
from app.world_runtime.atomic_action_runtime import (
    create_agent_action_plan,
    execute_next_atomic_step,
    get_agent_active_plan,
    process_atomic_action_plan_for_agent_tick,
)


class SpatialAffordanceRuntimeTest(unittest.TestCase):
    def setUp(self):
        import app.db as db
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "campus.db"
        self.database_url = f"sqlite+pysqlite:///{self.db_path}"

        self._orig_db_url = os.environ.get("DATABASE_URL")
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]

        db.DB_PATH = self.db_path

        def _test_get_connection():
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            return conn

        self._test_get_connection = _test_get_connection

        self.patchers = [
            mock.patch("app.db.using_postgres", return_value=False),
            mock.patch("app.db.get_connection", side_effect=_test_get_connection),
            mock.patch("app.spatial.router.get_connection", side_effect=_test_get_connection, create=True),
            mock.patch("app.spatial.affordance_service.get_connection", side_effect=_test_get_connection, create=True),
            mock.patch("app.world_runtime.atomic_action_runtime.get_connection", side_effect=_test_get_connection, create=True),
            mock.patch("app.main.get_connection", side_effect=_test_get_connection, create=True),
            mock.patch("app.spatial.runtime.get_connection", side_effect=_test_get_connection, create=True),
        ]
        for p in self.patchers:
            p.start()

        connection = _test_get_connection()
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT OR IGNORE INTO simulation_state (key, value) VALUES ('current_day', '1')"
        )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        import app.world_state.runtime_schema as rs
        rs.WORLD_SCHEMA_READY = False
        rs.ensure_world_runtime_tables(connection, allow_ddl=True)
        main.ensure_space_system(connection, allow_ddl=True)
        main.ensure_campus_state_table(connection, allow_ddl=True)
        connection.commit()
        connection.close()

        config = get_alembic_config(self.database_url)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        self.engine = create_database_engine(self.database_url)
        from app.spatial.models import metadata as spatial_metadata
        spatial_metadata.create_all(self.engine)

        connection = _test_get_connection()
        # Seed spatial test nodes & edges
        connection.execute(
            """
            INSERT OR IGNORE INTO spatial_nodes
            (id, world_key, code, name, node_type, x, y, z, radius, capacity, status, properties)
            VALUES
            (10, 'default', 'dorm', '紫荆宿舍楼', 'building', 0.0, 0.0, 0.0, 10.0, 50, 'open', '{}'),
            (11, 'default', 'canteen', '清晏楼食堂', 'building', 20.0, 0.0, 0.0, 10.0, 50, 'open', '{}'),
            (12, 'default', 'library', '校图书馆', 'building', 50.0, 0.0, 0.0, 10.0, 50, 'open', '{}'),
            (13, 'default', 'far_building', '远端实验楼', 'building', 500.0, 0.0, 0.0, 10.0, 50, 'open', '{}')
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO spatial_edges
            (from_node_id, to_node_id, distance_meters, base_minutes, bidirectional, status, congestion_factor, weather_factor, properties)
            VALUES
            (10, 11, 20.0, 0.25, 1, 'open', 1.0, 1.0, '{}'),
            (11, 12, 30.0, 0.35, 1, 'open', 1.0, 1.0, '{}')
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO residents (id, name, role, personality, goal, money, location)
            VALUES (1, '测试Agent', '学生', '{}', '{}', 100, '紫荆宿舍楼')
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO agent_profiles (resident_id, energy, current_task)
            VALUES (1, 80, '学习与就餐')
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO agent_body_states
            (resident_id, hunger, fatigue, sleep_debt, stress, attention, social_energy, health, weather_exposure)
            VALUES (1, 40, 20, 10, 20, 80, 50, 90, 0)
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO agent_spatial_states
            (resident_id, current_node_id, x, y, z, facing_x, facing_z, movement_status, progress, path, path_index, route_distance_meters, remaining_distance_meters, updated_tick, version, branch_key, replan_count, last_replan_reason)
            VALUES (1, 10, 0.0, 0.0, 0.0, 0.0, 1.0, 'idle', 1.0, '[]', 0, 0.0, 0.0, 0, 1, 'main', 0, '')
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO agent_spatial_capabilities
            (resident_id, base_speed_m_per_min, mobility_class, accessibility_needs, perception_radius_m, hearing_radius_m, version, source)
            VALUES (1, 80.0, 'standard', '{}', 100.0, 30.0, 1, 'system')
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()
        for p in self.patchers:
            p.stop()
        if self._orig_db_url is not None:
            os.environ["DATABASE_URL"] = self._orig_db_url
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        import app.world_state.runtime_schema as rs
        rs.WORLD_SCHEMA_READY = False

    def test_seed_and_get_spatial_affordances(self):
        conn = self._test_get_connection()
        try:
            count = seed_spatial_affordances(conn)
            conn.commit()

            affordances = get_spatial_affordances(conn)
            self.assertGreater(len(affordances), 0, "应配置至少核心空间节点的可供性")

            keys = {aff["affordance_key"] for aff in affordances}
            self.assertIn("rest", keys)
            self.assertTrue("use_facility" in keys or "consume" in keys or "observe" in keys)
        finally:
            conn.close()

    def test_real_canteen_node_is_recognized_as_already_at_consume_destination(self):
        """A concrete canteen node must not be treated as the legacy label mismatch."""
        conn = self._test_get_connection()
        try:
            seed_spatial_affordances(conn)
            conn.execute(
                "UPDATE agent_spatial_states SET current_node_id = 11, movement_status = 'arrived' WHERE resident_id = 1"
            )
            context = get_current_spatial_action_context(conn, 1, "consume")
            self.assertEqual(context["node_id"], 11)
            self.assertEqual(context["node_name"], "清晏楼食堂")
        finally:
            conn.close()

    def test_discover_agent_affordance_opportunities(self):
        conn = self._test_get_connection()
        try:
            res = discover_agent_affordance_opportunities(conn, resident_id=1)
            self.assertIn("opportunities", res)
            self.assertIn("current_node_id", res)
            opportunities = res["opportunities"]
            self.assertGreater(len(opportunities), 0, "Agent 应能够感知发现周围可供性机会")

            far_opp = next((o for o in opportunities if o["node_id"] == 13), None)
            if far_opp:
                self.assertFalse(far_opp["is_available"], "远端节点超出于感知半径 100m 应该不可达")
                self.assertTrue(any("超出感知" in r for r in far_opp["reasons"]))
        finally:
            conn.close()

    def test_atomic_action_plan_creation_and_execution(self):
        conn = self._test_get_connection()
        try:
            seed_spatial_affordances(conn)
            plan = create_agent_action_plan(
                conn,
                resident_id=1,
                target_affordance_key="consume",
                target_node_id=11,
            )
            conn.commit()

            self.assertEqual(plan["status"], "executing")
            self.assertGreaterEqual(len(plan["steps"]), 2)

            exec_res = execute_next_atomic_step(conn, resident_id=1)
            conn.commit()

            self.assertTrue(exec_res["success"])
            self.assertIn(exec_res["status"], ("step_executed", "movement_started", "moving"))

            active = get_agent_active_plan(conn, resident_id=1)
            self.assertIsNotNone(active)
        finally:
            conn.close()

    def test_new_atomic_plan_supersedes_previous_executing_plan(self):
        """Only one atomic plan may drive one Agent at a time."""
        conn = self._test_get_connection()
        try:
            seed_spatial_affordances(conn)
            first = create_agent_action_plan(conn, 1, "consume", 11)
            second = create_agent_action_plan(conn, 1, "rest", 10)
            rows = conn.execute(
                "SELECT id, status FROM agent_action_plans WHERE resident_id = 1 ORDER BY id"
            ).fetchall()
            statuses = {int(row["id"]): row["status"] for row in rows}
            self.assertEqual(statuses[first["id"]], "superseded")
            self.assertEqual(statuses[second["id"]], "executing")
            self.assertEqual(sum(status == "executing" for status in statuses.values()), 1)
        finally:
            conn.close()

    def test_insufficient_funds_failure_handling(self):
        conn = self._test_get_connection()
        try:
            conn.execute("UPDATE residents SET money = 0 WHERE id = 1")
            conn.commit()

            plan = create_agent_action_plan(
                conn,
                resident_id=1,
                target_affordance_key="consume",
                target_node_id=11,
            )
            conn.commit()

            steps = plan["steps"]
            consume_idx = next((i for i, s in enumerate(steps) if s["action"] == "consume"), None)

            if consume_idx is not None:
                conn.execute(
                    "UPDATE agent_action_plans SET current_step_index = ? WHERE id = ?",
                    (consume_idx, plan["id"]),
                )
                conn.commit()

                exec_res = execute_next_atomic_step(conn, resident_id=1)
                conn.commit()

                self.assertFalse(exec_res["success"])
                self.assertIn("资金不足", exec_res["failure_reason"])
        finally:
            conn.close()

    def test_autonomous_affordance_tick_integration(self):
        conn = self._test_get_connection()
        try:
            conn.execute("UPDATE agent_body_states SET hunger = 80 WHERE resident_id = 1")
            agent = {"id": 1, "name": "测试Agent", "location": "紫荆宿舍楼"}
            res = process_atomic_action_plan_for_agent_tick(conn, agent)
            conn.commit()

            self.assertIn(res["status"], ("step_executed", "movement_started", "moving"))
            active = get_agent_active_plan(conn, resident_id=1)
            self.assertIsNotNone(active, "Tick 应自动触发可供性发现并创建行动计划")
        finally:
            conn.close()

    def test_acute_hunger_prioritizes_reachable_real_canteen_and_recovers(self):
        """A hungry Agent searches beyond passive radius, reaches food, then settles a meal."""
        conn = self._test_get_connection()
        try:
            conn.execute("UPDATE agent_body_states SET hunger = 92 WHERE resident_id = 1")
            # Prove this is goal-directed food search, not merely a nearby
            # affordance: keep the canteen outside the passive 10 m radius.
            conn.execute("UPDATE agent_spatial_capabilities SET perception_radius_m = 10 WHERE resident_id = 1")
            conn.commit()
            self.assertEqual(conn.execute("SELECT hunger FROM agent_body_states WHERE resident_id = 1").fetchone()["hunger"], 92)
            discovered = discover_agent_affordance_opportunities(conn, 1)
            meal = next(item for item in discovered["opportunities"] if item["node_id"] == 11 and item["affordance_key"] == "consume")
            self.assertTrue(meal["is_available"], meal["reasons"])
            self.assertTrue(meal["essential_food_search"])

            plan = create_agent_action_plan(conn, 1, "consume", 11)
            consume_index = next(i for i, step in enumerate(plan["steps"]) if step["action"] == "consume")
            conn.execute("UPDATE agent_action_plans SET current_step_index = ? WHERE id = ?", (consume_index, plan["id"]))
            before = conn.execute("SELECT hunger FROM agent_body_states WHERE resident_id = 1").fetchone()["hunger"]
            outcome = execute_next_atomic_step(conn, 1)
            after = conn.execute("SELECT hunger FROM agent_body_states WHERE resident_id = 1").fetchone()["hunger"]
            self.assertTrue(outcome["success"])
            self.assertEqual(after, max(0, before - 62))
        finally:
            conn.close()

    def test_dehydrated_agent_uses_real_canteen_hydration_affordance(self):
        """Hydration is an independent recovery loop, not a renamed meal."""
        conn = self._test_get_connection()
        try:
            seed_spatial_affordances(conn)
            conn.execute("UPDATE agent_body_states SET hydration = 82, hunger = 25 WHERE resident_id = 1")
            plan = create_agent_action_plan(conn, 1, "hydrate", 11)
            hydrate_index = next(i for i, step in enumerate(plan["steps"]) if step["action"] == "hydrate")
            conn.execute("UPDATE agent_action_plans SET current_step_index = ? WHERE id = ?", (hydrate_index, plan["id"]))
            before = conn.execute("SELECT hydration FROM agent_body_states WHERE resident_id = 1").fetchone()["hydration"]
            outcome = execute_next_atomic_step(conn, 1)
            after = conn.execute("SELECT hydration FROM agent_body_states WHERE resident_id = 1").fetchone()["hydration"]
            self.assertTrue(outcome["success"])
            self.assertLess(after, before)
        finally:
            conn.close()

    def test_route_failure_preserves_position(self):
        """Verify that when route planning fails, agent position is untouched and plan is marked failed."""
        conn = self._test_get_connection()
        try:
            steps_json = json.dumps([
                {"step_index": 0, "action": "move", "target_node_id": 999, "location": "不存在的节点", "expected_cost": {}}
            ])
            conn.execute(
                "INSERT INTO agent_action_plans (resident_id, target_affordance_key, status, current_step_index, steps_json, window_start, window_end) VALUES (?, ?, ?, ?, ?, '00:00', '24:00')",
                (1, "test_fail", "executing", 0, steps_json),
            )
            conn.commit()

            state_before = conn.execute("SELECT current_node_id FROM agent_spatial_states WHERE resident_id = 1").fetchone()
            orig_node_id = state_before["current_node_id"]

            res = execute_next_atomic_step(conn, resident_id=1)
            conn.commit()

            self.assertEqual(res["status"], "plan_failed")
            self.assertFalse(res["success"])
            self.assertIn("无法规划", res["failure_reason"])

            state_after = conn.execute("SELECT current_node_id FROM agent_spatial_states WHERE resident_id = 1").fetchone()
            self.assertEqual(state_after["current_node_id"], orig_node_id, "规划失败时不应改变 Agent 当前节点位置")

            plan = get_agent_active_plan(conn, resident_id=1)
            self.assertEqual(plan["status"], "failed")
        finally:
            conn.close()

    def test_topological_unreachability_filtering(self):
        """Verify that nodes within perception radius but disconnected from edge graph are marked unreachable."""
        conn = self._test_get_connection()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO spatial_nodes
                (id, world_key, code, name, node_type, x, y, z, radius, capacity, status, properties)
                VALUES
                (14, 'default', 'isolated', '孤立景观台', 'poi', 10.0, 0.0, 0.0, 10.0, 50, 'open', '{}')
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO spatial_affordances
                (world_key, node_id, affordance_key, name, requirements, effects, capacity, status)
                VALUES
                ('default', 14, 'observe', '观景', '{}', '{}', 50, 'open')
                """
            )
            conn.commit()

            res = discover_agent_affordance_opportunities(conn, resident_id=1)
            opportunities = res["opportunities"]

            iso_opp = next((o for o in opportunities if o["node_id"] == 14), None)
            self.assertIsNotNone(iso_opp)
            self.assertFalse(iso_opp["is_available"], "即使在感知距离内，无连通边节点的 Affordance 也应不可达")
            self.assertTrue(any("无连通路径" in r for r in iso_opp["reasons"]))
        finally:
            conn.close()

    def test_single_action_execution_per_tick(self):
        """Verify that when an atomic plan is active, process_world_agent_tick short-circuits and executes only one action."""
        conn = self._test_get_connection()
        try:
            seed_spatial_affordances(conn)
            conn.execute("UPDATE agent_body_states SET hunger = 80 WHERE resident_id = 1")
            plan = create_agent_action_plan(
                conn,
                resident_id=1,
                target_affordance_key="consume",
                target_node_id=11,
            )
            conn.commit()

            agent = conn.execute("SELECT * FROM residents WHERE id = 1").fetchone()
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)

            res = process_world_agent_tick(conn, dict(agent), world_time=now, tick_id="test_tick_1", day=1, slot=1)
            conn.commit()

            self.assertTrue(res["success"])
            self.assertEqual(res.get("source"), "atomic_action_plan", "应当由原子行动计划接管并短路旧行动链路")
            self.assertIsNone(res.get("action_execution_id"), "不应再生成旧链路的 action_execution_id")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
