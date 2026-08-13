import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command

import app.main as main
from app.body_runtime import (
    advance_body_states,
    apply_action_body_effects,
    body_action_checks,
)
from app.capability_runtime import (
    capability_action_checks,
    individualize_action_rule,
)
from app.perception_runtime import (
    capture_tick_observations,
    get_agent_cognitive_context,
    spatial_memory_location_factors,
)
from app.db import create_database_engine
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.economy.service import (
    post_money_transfer,
    reconcile_ledger,
    seed_economy_foundation,
)
from app.models import SCHEMA_SQL
from app.spatial.repository import SpatialRepository
from app.spatial.planner import RouteNotFoundError
from app.spatial.runtime import (
    SpatialAdmissionError,
    _reachable_destination_route,
    advance_active_movements,
    check_action_resource,
    pause_spatial_movement,
    preview_route,
    resume_spatial_movement,
    start_spatial_movement,
)
from app.spatial.seed import seed_spatial_foundation
from app.spatial.service import (
    ResidentNotFoundError,
    SpatialService,
    SpatialStateNotInitializedError,
)


class SpatialFoundationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "campus.db"
        self.database_url = f"sqlite+pysqlite:///{self.db_path}"
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')"
        )
        connection.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES
            (1, '空间测试学生', '学生', '认真', '完成空间实验', 100, '图书馆'),
            (2, '未初始化学生', '学生', '安静', '观察校园', 100, '宿舍区')
            """
        )
        connection.execute(
            """
            INSERT INTO agent_profiles
            (resident_id, gender, avatar_style, energy, mood, current_task,
             skills, strategy, schedule, perception)
            VALUES
            (1, '女', '测试', 80, '平稳', '空间实验', '{}', '{}', '[]', '{}'),
            (2, '男', '测试', 80, '平稳', '观察校园', '{}', '{}', '[]', '{}')
            """
        )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(connection, allow_ddl=True)
        main.ensure_space_system(connection, allow_ddl=True)
        main.ensure_agent_news_system(connection, allow_ddl=True)
        main.ensure_external_information_system(connection, allow_ddl=True)
        main.ensure_world_runtime_tables(connection, allow_ddl=True)
        connection.commit()
        connection.close()

        config = get_alembic_config(self.database_url)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        self.engine = create_database_engine(self.database_url)
        with self.engine.begin() as spatial_connection:
            self.first_seed = seed_spatial_foundation(spatial_connection)
            self.second_seed = seed_spatial_foundation(spatial_connection)
        economy_connection = sqlite3.connect(self.db_path)
        economy_connection.row_factory = sqlite3.Row
        economy_connection.execute("PRAGMA foreign_keys = ON")
        seed_economy_foundation(economy_connection)
        economy_connection.commit()
        economy_connection.close()

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_seed_builds_idempotent_metric_topology(self):
        self.assertEqual(self.first_seed["nodes_total"], 19)
        self.assertEqual(self.first_seed["edges_total"], 20)
        self.assertEqual(self.first_seed["resources_total"], 7)
        self.assertEqual(self.first_seed["capabilities_created"], 2)
        self.assertEqual(self.first_seed["states_created"], 2)
        self.assertEqual(self.first_seed["body_states_created"], 2)
        self.assertEqual(self.first_seed["capability_profiles_created"], 2)
        self.assertEqual(self.first_seed["opportunities_created"], 10)
        self.assertEqual(self.second_seed["nodes_created"], 0)
        self.assertEqual(self.second_seed["edges_created"], 0)
        self.assertEqual(self.second_seed["resources_created"], 0)
        self.assertEqual(self.second_seed["capabilities_created"], 0)
        self.assertEqual(self.second_seed["states_created"], 0)
        self.assertEqual(self.second_seed["body_states_created"], 0)
        self.assertEqual(self.second_seed["capability_profiles_created"], 0)
        self.assertEqual(self.second_seed["opportunities_created"], 0)

    def test_service_exposes_scene_occupancy_and_capability(self):
        with self.engine.connect() as connection:
            service = SpatialService(SpatialRepository(connection))
            scene = service.get_scene_graph()
            occupancy = service.get_occupancy()
            state = service.get_agent_state(1)

        self.assertEqual(scene["coordinate_system"], "right-handed-meters")
        self.assertEqual(len(scene["nodes"]), 19)
        self.assertEqual(len(scene["edges"]), 20)
        self.assertEqual(sum(item["occupancy"] for item in occupancy["spaces"]), 2)
        self.assertEqual(state["current_node_code"], "library")
        self.assertGreater(state["capability"]["base_speed_m_per_min"], 68.0)
        self.assertLess(state["capability"]["base_speed_m_per_min"], 89.0)

    def test_service_distinguishes_missing_and_uninitialized_residents(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM agent_spatial_states WHERE resident_id = 2"
            )
        with self.engine.connect() as connection:
            service = SpatialService(SpatialRepository(connection))
            with self.assertRaises(SpatialStateNotInitializedError):
                service.get_agent_state(2)
            with self.assertRaises(ResidentNotFoundError):
                service.get_agent_state(999)

    def test_trajectory_defaults_to_empty_and_limits_requested_window(self):
        with self.engine.connect() as connection:
            service = SpatialService(SpatialRepository(connection))
            result = service.get_trajectory(1)
            self.assertEqual(result["trajectory"], [])
            with self.assertRaisesRegex(ValueError, "cannot exceed"):
                service.get_trajectory(
                    1,
                    experiment_run_id=1,
                    from_tick=0,
                    to_tick=10_001,
                )

    def test_spatial_routes_are_published_in_openapi(self):
        paths = main.app.openapi()["paths"]
        self.assertIn("/api/spatial/scene", paths)
        self.assertIn("/api/spatial/occupancy", paths)
        self.assertIn("/api/spatial/agents", paths)
        self.assertIn("/api/spatial/resources", paths)
        self.assertIn("/api/spatial/admission-queue", paths)
        self.assertIn("/api/agents/{resident_id}/spatial-state", paths)
        self.assertIn("/api/agents/{resident_id}/trajectory", paths)
        self.assertIn("/api/agents/{resident_id}/movement/plan", paths)
        self.assertIn("/api/agents/{resident_id}/movement/pause", paths)
        self.assertIn("/api/agents/{resident_id}/movement/resume", paths)
        self.assertIn("/api/body-states", paths)
        self.assertIn("/api/agents/{resident_id}/body-state", paths)
        self.assertIn("/api/agents/{resident_id}/perception-evidence", paths)
        self.assertIn("/api/perception/observations", paths)
        self.assertIn("/api/agents/{resident_id}/capability-profile", paths)
        self.assertIn("/api/capabilities", paths)
        self.assertIn("/api/macro/definitions", paths)
        self.assertIn("/api/macro/snapshots/latest", paths)

    def test_capability_profiles_create_explainable_agent_differences(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        profiles = connection.execute(
            """
            SELECT resident_id, physical_endurance, time_management,
                   institutional_access, source, missing_value_policy
            FROM agent_capability_profiles ORDER BY resident_id
            """
        ).fetchall()
        self.assertEqual(len(profiles), 2)
        self.assertNotEqual(
            tuple(profiles[0])[1:4],
            tuple(profiles[1])[1:4],
        )
        self.assertEqual(profiles[0]["source"], "derived-structured-profile")
        self.assertIn("deterministic neutral default", profiles[0]["missing_value_policy"])
        connection.close()

    def test_same_action_has_bounded_capability_cost_and_success_differences(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            UPDATE agent_capability_profiles
            SET physical_endurance = 20, time_management = 20
            WHERE resident_id = 1
            """
        )
        connection.execute(
            """
            UPDATE agent_capability_profiles
            SET physical_endurance = 90, time_management = 90
            WHERE resident_id = 2
            """
        )
        rule = main.get_world_action_rule(connection, "move")
        lower = individualize_action_rule(connection, 1, rule, "move")
        higher = individualize_action_rule(connection, 2, rule, "move")
        self.assertGreater(
            lower["required_resources"]["energy"],
            higher["required_resources"]["energy"],
        )
        self.assertGreater(
            lower["required_resources"]["time_budget"],
            higher["required_resources"]["time_budget"],
        )
        self.assertLess(
            lower["success_probability"],
            higher["success_probability"],
        )
        self.assertEqual(
            lower["individualization"]["version"],
            "capability-defaults-v1",
        )
        connection.close()

    def test_low_structured_opportunity_rejects_institutional_action(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            UPDATE agent_opportunity_access
            SET access_level = 5, eligibility = 'limited'
            WHERE resident_id = 1
              AND opportunity_key = 'institutional_services'
            """
        )
        checks = capability_action_checks(connection, 1, "request_leave")
        self.assertEqual(checks[0]["failure_code"], "opportunity_access_limited")
        self.assertFalse(checks[0]["passed"])
        connection.close()

    def test_local_perception_does_not_leak_distant_event(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO world_ticks
            (id, tick_index, world_time, day, slot, reason, status)
            VALUES
            (1, 1, '2026-07-29T08:00:00+00:00', 1, '上午', 'test', 'complete'),
            (2, 2, '2026-07-29T08:01:00+00:00', 1, '上午', 'test', 'running')
            """
        )
        event = main.append_world_event(
            connection,
            "agent_tick",
            "图书馆发生局部事件",
            "空间测试学生在图书馆完成了一次实验。",
            tick_id=1,
            resident_id=1,
            location="图书馆",
            day=1,
            slot="上午",
        )
        captured = capture_tick_observations(
            connection,
            datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
            tick_id=2,
            day=1,
            branch_key="main",
        )
        observer_ids = {
            item["observer_resident_id"]
            for item in captured
            if item["source_event_id"] == event["id"]
        }
        self.assertIn(1, observer_ids)
        self.assertNotIn(2, observer_ids)

        library = connection.execute(
            "SELECT id, x, y, z FROM spatial_nodes WHERE code = 'library'"
        ).fetchone()
        connection.execute(
            """
            UPDATE agent_spatial_states
            SET current_node_id = ?, x = ?, y = ?, z = ?
            WHERE resident_id = 2
            """,
            (library["id"], library["x"], library["y"], library["z"]),
        )
        nearby = capture_tick_observations(
            connection,
            datetime(2026, 7, 29, 8, 2, tzinfo=timezone.utc),
            tick_id=2,
            day=1,
            branch_key="main",
        )
        self.assertIn(
            2,
            {
                item["observer_resident_id"]
                for item in nearby
                if item["source_event_id"] == event["id"]
            },
        )
        context = get_agent_cognitive_context(connection, 2)
        self.assertTrue(context["observations"])
        self.assertTrue(context["beliefs"])
        self.assertTrue(context["spatial_memories"])
        connection.execute(
            """
            UPDATE agent_spatial_memories
            SET salience = 100, valence = -100
            WHERE resident_id = 2
            """
        )
        self.assertLess(
            spatial_memory_location_factors(connection, 2)["图书馆"],
            1.0,
        )
        connection.close()

    def test_information_literacy_changes_confidence_not_event_access(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO world_ticks
            (id, tick_index, world_time, day, slot, reason, status)
            VALUES
            (1, 1, '2026-07-29T08:00:00+00:00', 1, '上午', 'test', 'complete'),
            (2, 2, '2026-07-29T08:01:00+00:00', 1, '上午', 'test', 'running')
            """
        )
        library = connection.execute(
            "SELECT id, x, y, z FROM spatial_nodes WHERE code = 'library'"
        ).fetchone()
        connection.execute(
            """
            UPDATE agent_spatial_states
            SET current_node_id = ?, x = ?, y = ?, z = ?
            """,
            (library["id"], library["x"], library["y"], library["z"]),
        )
        connection.execute(
            """
            UPDATE agent_capability_profiles
            SET information_literacy = CASE resident_id WHEN 1 THEN 20 ELSE 90 END
            """
        )
        event = main.append_world_event(
            connection,
            "campus_notice",
            "图书馆临时安排",
            "服务台发布了一项需要辨别细节的安排。",
            tick_id=1,
            location="图书馆",
            day=1,
            slot="上午",
        )
        captured = capture_tick_observations(
            connection,
            datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
            tick_id=2,
            day=1,
            branch_key="main",
        )
        observations = {
            item["observer_resident_id"]: item
            for item in captured
            if item["source_event_id"] == event["id"]
        }
        self.assertEqual(set(observations), {1, 2})
        self.assertLess(observations[1]["confidence"], observations[2]["confidence"])
        self.assertGreater(
            observations[1]["error_margin"],
            observations[2]["error_margin"],
        )
        connection.close()

    def test_received_information_is_private_to_recipient_context(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            INSERT INTO external_information
            (title, summary, source_name, category)
            VALUES ('局部订阅消息', '只有订阅者收到', 'test', 'education')
            """
        )
        connection.execute(
            """
            INSERT INTO agent_information
            (information_id, resident_id, channel, relevance, credibility)
            VALUES (?, 1, '定向订阅', 80, 90)
            """,
            (cursor.lastrowid,),
        )
        informed = get_agent_cognitive_context(connection, 1)
        uninformed = get_agent_cognitive_context(connection, 2)
        self.assertEqual(informed["received_information"][0]["title"], "局部订阅消息")
        self.assertEqual(uninformed["received_information"], [])
        connection.close()

    def test_runtime_perception_omits_global_aggregate_truth(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        agent = dict(
            connection.execute("SELECT * FROM residents WHERE id = 1").fetchone()
        )
        perception = main.build_runtime_perception(
            connection,
            agent,
            datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
            1,
            "上午",
            {"intent": "测试", "goal_chain": {}},
            {"action": "observe", "location": "图书馆"},
            observed=True,
        )
        for hidden_key in (
            "campus_flow",
            "campus_mood",
            "exam_pressure",
            "resource_pressure",
            "activity_heat",
        ):
            self.assertNotIn(hidden_key, perception["environment"])
        self.assertIn("information_boundary", perception)
        self.assertNotIn("recent_events", perception)
        self.assertNotIn("observed", perception)
        connection.close()

    def test_body_state_advances_with_time_and_syncs_legacy_energy(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
        environment = {
            "rainfall": 0,
            "temperature": 24,
            "exam_pressure": 20,
        }
        initialized = advance_body_states(
            connection,
            started_at,
            tick_number=1,
            environment=environment,
        )
        advanced = advance_body_states(
            connection,
            started_at + timedelta(hours=2),
            tick_number=2,
            environment=environment,
        )
        self.assertEqual(len(initialized), 2)
        self.assertEqual(advanced[0]["elapsed_hours"], 2.0)
        self.assertGreater(advanced[0]["hunger"], initialized[0]["hunger"])
        self.assertGreater(advanced[0]["fatigue"], initialized[0]["fatigue"])
        profile = connection.execute(
            "SELECT energy FROM agent_profiles WHERE resident_id = 1"
        ).fetchone()
        self.assertEqual(profile["energy"], advanced[0]["energy"])
        connection.close()

    def test_rest_and_consume_recover_distinct_body_needs(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            UPDATE agent_body_states
            SET hunger = 86, fatigue = 82, sleep_debt = 60,
                stress = 70, attention = 20, hydration = 82, nutrition = 22,
                activity_load = 76, illness_load = 12
            WHERE resident_id = 1
            """
        )
        meal = apply_action_body_effects(connection, 1, "consume")
        rest = apply_action_body_effects(connection, 1, "rest")
        self.assertLess(meal["after"]["hunger"], meal["before"]["hunger"])
        self.assertGreater(meal["after"]["health"], meal["before"]["health"])
        self.assertLess(meal["after"]["hydration"], meal["before"]["hydration"])
        self.assertGreater(meal["after"]["nutrition"], meal["before"]["nutrition"])
        self.assertLess(rest["after"]["fatigue"], rest["before"]["fatigue"])
        self.assertLess(rest["after"]["stress"], rest["before"]["stress"])
        self.assertGreater(rest["after"]["attention"], rest["before"]["attention"])
        self.assertGreater(rest["after"]["health"], rest["before"]["health"])
        self.assertLess(rest["after"]["activity_load"], rest["before"]["activity_load"])
        connection.close()

    def test_body_preconditions_allow_recovery_movement_when_exhausted(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "UPDATE agent_body_states SET fatigue = 95 WHERE resident_id = 1"
        )
        move_checks = body_action_checks(connection, 1, "move")
        consume_checks = body_action_checks(connection, 1, "consume")
        self.assertTrue(all(check["passed"] for check in move_checks))
        self.assertTrue(all(check["passed"] for check in consume_checks))
        rule = main.get_world_action_rule(connection, "move")
        integrated, _ = main.evaluate_world_action_preconditions(
            connection,
            1,
            "move",
            "图书馆",
            rule,
            datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
        )
        self.assertNotIn(
            "too_fatigued",
            [check["failure_code"] for check in integrated if not check["passed"]],
        )
        connection.close()

    def test_fatigue_reduces_effective_route_speed(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        baseline = preview_route(connection, 2, "图书馆")
        connection.execute(
            "UPDATE agent_body_states SET fatigue = 85 WHERE resident_id = 2"
        )
        fatigued = preview_route(connection, 2, "图书馆")
        self.assertLess(
            fatigued["effective_speed_m_per_min"],
            baseline["effective_speed_m_per_min"],
        )
        self.assertGreater(fatigued["cost_minutes"], baseline["cost_minutes"])
        connection.close()

    def test_route_reacts_to_closure_and_rejects_disconnected_destination(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        direct = preview_route(connection, 2, "图书馆")
        connection.execute(
            """
            UPDATE spatial_edges SET status = 'closed'
            WHERE from_node_id = (
                SELECT id FROM spatial_nodes WHERE code = 'path_central'
            ) AND to_node_id = (
                SELECT id FROM spatial_nodes WHERE code = 'path_east'
            )
            """
        )
        alternate = preview_route(connection, 2, "图书馆")
        self.assertNotEqual(direct["node_ids"], alternate["node_ids"])
        connection.execute(
            """
            UPDATE spatial_edges SET status = 'closed'
            WHERE from_node_id = (
                SELECT id FROM spatial_nodes WHERE code = 'library'
            ) OR to_node_id = (
                SELECT id FROM spatial_nodes WHERE code = 'library'
            )
            """
        )
        with self.assertRaises(RouteNotFoundError):
            preview_route(connection, 2, "图书馆")
        connection.close()

    def test_food_alias_prefers_reachable_real_building_over_isolated_duplicate(self):
        nodes = [
            {"id": 1, "code": "origin", "name": "清晏楼(食堂/报告厅)", "node_type": "path_point", "status": "open", "world_key": "tsinghua_main", "properties": {}},
            {"id": 2, "code": "reachable_canteen", "name": "清晏楼食堂", "node_type": "building", "status": "open", "world_key": "tsinghua_main", "properties": {}},
            {"id": 99, "code": "isolated_canteen", "name": "紫荆园食堂", "node_type": "building", "status": "open", "world_key": "tsinghua_main", "properties": {}},
        ]
        edges = [{"id": 1, "from_node_id": 1, "to_node_id": 2, "distance_meters": 20.0, "bidirectional": True, "status": "open", "congestion_factor": 1.0, "weather_factor": 1.0, "properties": {}}]
        target, route = _reachable_destination_route(nodes, edges, 1, "食堂", "tsinghua_main", 78.0, {})
        self.assertEqual(target["id"], 2)
        self.assertEqual(route["node_ids"], [1, 2])

    def test_continuous_movement_updates_coordinates_before_legacy_location(self):
        started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        before = connection.execute(
            "SELECT location FROM residents WHERE id = 2"
        ).fetchone()["location"]
        movement = start_spatial_movement(
            connection,
            2,
            "图书馆",
            world_time=started_at,
        )
        during = connection.execute(
            "SELECT location FROM residents WHERE id = 2"
        ).fetchone()["location"]
        self.assertEqual(before, during)
        self.assertGreater(movement["route"]["distance_meters"], 0)

        progress = advance_active_movements(
            connection,
            started_at + timedelta(minutes=1),
            tick_number=1,
        )
        state = connection.execute(
            """
            SELECT movement_status, progress, remaining_distance_meters
            FROM agent_spatial_states WHERE resident_id = 2
            """
        ).fetchone()
        self.assertEqual(progress[0]["movement_status"], "moving")
        self.assertGreater(state["progress"], 0)
        self.assertEqual(
            connection.execute(
                "SELECT location FROM residents WHERE id = 2"
            ).fetchone()["location"],
            before,
        )

        arrival = advance_active_movements(
            connection,
            started_at + timedelta(minutes=20),
            tick_number=2,
        )
        self.assertEqual(arrival[0]["movement_status"], "arrived")
        self.assertEqual(
            connection.execute(
                "SELECT location FROM residents WHERE id = 2"
            ).fetchone()["location"],
            "图书馆",
        )
        self.assertEqual(
            connection.execute(
                """
                SELECT COUNT(*) AS value FROM agent_trajectories
                WHERE resident_id = 2
                """
            ).fetchone()["value"],
            2,
        )
        connection.close()

    def test_movement_can_pause_and_resume_without_time_jump(self):
        started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        start_spatial_movement(
            connection,
            2,
            "教学楼",
            world_time=started_at,
        )
        paused = pause_spatial_movement(connection, 2, "observer_pause")
        self.assertEqual(paused["movement_status"], "paused")
        self.assertEqual(
            advance_active_movements(
                connection,
                started_at + timedelta(minutes=10),
                tick_number=1,
            ),
            [],
        )
        resumed = resume_spatial_movement(
            connection,
            2,
            world_time=started_at + timedelta(minutes=10),
        )
        self.assertEqual(resumed["movement_status"], "moving")
        progress = advance_active_movements(
            connection,
            started_at + timedelta(minutes=11),
            tick_number=2,
        )
        self.assertGreater(progress[0]["distance_traveled_meters"], 0)
        connection.close()

    def test_closed_and_full_destinations_remain_physical_movement_candidates(self):
        started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "UPDATE campus_spaces SET status = '维护中' WHERE location = '图书馆'"
        )
        closed = start_spatial_movement(
            connection,
            2,
            "图书馆",
            world_time=started_at,
        )
        self.assertEqual(closed["movement_status"], "moving")
        self.assertFalse(
            bool(closed["constraint_evaluation"]["officially_permitted"])
        )
        connection.execute(
            """
            UPDATE agent_spatial_states
            SET movement_status = 'idle', target_node_id = NULL, path = '[]',
                path_index = 0, progress = 0, remaining_distance_meters = 0
            WHERE resident_id = 2
            """
        )
        connection.execute(
            """
            UPDATE campus_spaces SET status = '开放', capacity = 0
            WHERE location = '图书馆'
            """
        )
        movement = start_spatial_movement(
            connection,
            2,
            "图书馆",
            world_time=started_at,
        )
        self.assertEqual(movement["movement_status"], "moving")
        connection.close()

    def test_destination_closure_during_trip_waits_outside_until_reopened(self):
        started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        start_spatial_movement(
            connection,
            2,
            "图书馆",
            world_time=started_at,
        )
        connection.execute(
            "UPDATE campus_spaces SET status = '维护中' WHERE location = '图书馆'"
        )
        waiting = advance_active_movements(
            connection,
            started_at + timedelta(minutes=20),
            tick_number=1,
        )
        self.assertEqual(waiting[0]["movement_status"], "waiting")
        self.assertEqual(waiting[0]["admission"]["code"], "location_closed")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) AS value FROM spatial_admission_queue"
            ).fetchone()["value"],
            1,
        )
        self.assertNotEqual(
            connection.execute(
                "SELECT location FROM residents WHERE id = 2"
            ).fetchone()["location"],
            "图书馆",
        )
        connection.execute(
            "UPDATE campus_spaces SET status = '开放' WHERE location = '图书馆'"
        )
        arrival = advance_active_movements(
            connection,
            started_at + timedelta(minutes=21),
            tick_number=2,
        )
        self.assertEqual(arrival[0]["movement_status"], "arrived")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) AS value FROM spatial_admission_queue"
            ).fetchone()["value"],
            0,
        )
        connection.close()

    def test_waiting_agent_abandons_after_patience_expires(self):
        started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        start_spatial_movement(
            connection,
            2,
            "图书馆",
            world_time=started_at,
        )
        connection.execute(
            "UPDATE campus_spaces SET status = '维护中' WHERE location = '图书馆'"
        )
        advance_active_movements(
            connection,
            started_at + timedelta(minutes=20),
            tick_number=1,
        )
        abandoned = advance_active_movements(
            connection,
            started_at + timedelta(minutes=60),
            tick_number=2,
        )
        self.assertEqual(abandoned[0]["movement_status"], "interrupted")
        self.assertEqual(
            abandoned[0]["event_type"],
            "spatial_admission_abandoned",
        )
        self.assertIn(
            "教学楼",
            abandoned[0]["queue"]["suggested_alternatives"],
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) AS value FROM spatial_admission_queue"
            ).fetchone()["value"],
            0,
        )
        connection.close()

    def test_destination_actions_read_specific_resource_availability(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        available = check_action_resource(connection, "图书馆", "observe")
        self.assertTrue(available["required"])
        self.assertTrue(available["available"])
        self.assertEqual(available["resource_key"], "study_seats")
        connection.execute(
            """
            UPDATE spatial_resources SET available_units = 0
            WHERE resource_key = 'study_seats'
            """
        )
        unavailable = check_action_resource(connection, "图书馆", "observe")
        self.assertFalse(unavailable["available"])
        self.assertGreater(unavailable["estimated_wait_minutes"], 0)
        connection.close()

    def test_snapshot_restores_mutable_spatial_truth(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "", "DB_PATH": str(self.db_path)},
            clear=False,
        ):
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            snapshot = main.create_world_snapshot_record(
                connection,
                reason="spatial test",
            )
            self.assertEqual(
                snapshot["schema_version"],
                "world-snapshot-v31-longitudinal-paths",
            )
            original = connection.execute(
                "SELECT current_node_id, x, z FROM agent_spatial_states WHERE resident_id = 1"
            ).fetchone()
            connection.execute(
                """
                UPDATE agent_spatial_states
                SET current_node_id = (
                    SELECT id FROM spatial_nodes WHERE code = 'dorm'
                ), x = -999, z = -999
                WHERE resident_id = 1
                """
            )
            post_money_transfer(
                connection,
                transaction_key="snapshot:test:transfer",
                from_account_key="resident:1:cash",
                to_account_key="system:campus-services:cash",
                amount_coins=8,
                transaction_type="snapshot_test",
                source_type="unit_test",
            )
            main.restore_world_snapshot_state(connection, snapshot["id"])
            restored = connection.execute(
                "SELECT current_node_id, x, z FROM agent_spatial_states WHERE resident_id = 1"
            ).fetchone()
            restored_money = connection.execute(
                "SELECT money FROM residents WHERE id = 1"
            ).fetchone()
            restored_cash = connection.execute(
                """
                SELECT balance_minor FROM ledger_accounts
                WHERE account_key = 'resident:1:cash'
                """
            ).fetchone()
            ledger_balanced = reconcile_ledger(connection)["balanced"]
            connection.close()

            self.assertEqual(tuple(restored), tuple(original))
            self.assertEqual(restored_money["money"], 100)
            self.assertEqual(restored_cash["balance_minor"], 10000)
            self.assertTrue(ledger_balanced)


if __name__ == "__main__":
    unittest.main()
