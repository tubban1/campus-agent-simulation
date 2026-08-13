import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alembic import command
from sqlalchemy import text

import app.main as main
from app.db import create_database_engine
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.models import SCHEMA_SQL
from app.spatial.geo_importer import import_real_world_geojson
from app.spatial.repository import SpatialRepository
from app.spatial.service import SpatialService
from app.spatial.physical_state_service import (
    apply_spatial_physical_event,
    refresh_spatial_physical_states,
)
from app.spatial.location_catalog import best_real_location, rank_real_location_options, real_location_options


class SpatialOpenWorldTest(unittest.TestCase):
    def setUp(self):
        import app.db as db
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "campus.db"
        self.database_url = f"sqlite+pysqlite:///{self.db_path}"

        # Isolate test database from external DATABASE_URL (e.g., PostgreSQL)
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
            mock.patch("app.spatial.router.get_connection", side_effect=_test_get_connection),
            mock.patch("app.main.get_connection", side_effect=_test_get_connection),
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
        main.ensure_space_system(connection, allow_ddl=True, seed_demo_spaces=True)
        connection.commit()
        connection.close()

        config = get_alembic_config(self.database_url)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        self.engine = create_database_engine(self.database_url)
        from app.spatial.models import metadata as spatial_metadata
        spatial_metadata.create_all(self.engine)

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

    def test_import_real_world_geojson_saves_wgs84_facts_and_batch_record(self):
        sample_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "@id": "way/1001",
                        "osm_id": 1001,
                        "building": "university",
                        "name": "清华大学主楼",
                        "building:levels": "10",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [116.320, 39.998],
                                [116.322, 39.998],
                                [116.322, 40.000],
                                [116.320, 40.000],
                                [116.320, 39.998],
                            ]
                        ],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {
                        "@id": "way/2001",
                        "osm_id": 2001,
                        "highway": "footway",
                        "name": "清华路",
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [116.319, 39.999],
                            [116.321, 39.999],
                            [116.323, 39.999],
                        ],
                    },
                },
            ],
        }

        with self.engine.begin() as conn:
            summary = import_real_world_geojson(
                conn,
                sample_geojson,
                world_key="tsinghua_main",
                source="OpenStreetMap contributors / Tsinghua Campus Test Data",
                license_info="ODbL 1.0",
            )

        self.assertGreater(summary.nodes_created, 0)
        self.assertGreater(summary.edges_created, 0)

        with self.engine.connect() as conn:
            repo = SpatialRepository(conn)
            service = SpatialService(repo)

            nodes = repo.list_nodes(world_key="tsinghua_main")
            self.assertTrue(len(nodes) >= 3)
            building_node = next(n for n in nodes if n["node_type"] == "building")
            self.assertEqual(building_node["world_key"], "tsinghua_main")
            self.assertEqual(building_node["name"], "清华大学主楼")
            self.assertIsNotNone(building_node["longitude"])
            self.assertIsNotNone(building_node["latitude"])
            self.assertEqual(building_node["source_element_id"], "way/1001")

            worlds = repo.list_worlds()
            tsinghua_world = next(w for w in worlds if w["world_key"] == "tsinghua_main")
            self.assertEqual(tsinghua_world["name"], "清华大学主校区")
            self.assertTrue(tsinghua_world["is_real_world"])
            self.assertIsNotNone(tsinghua_world["wgs84_bounds"])
            self.assertIsNotNone(tsinghua_world["metric_bounds"])
            self.assertIn("Tsinghua Campus Test Data", tsinghua_world["source"])
            self.assertEqual(tsinghua_world["license"], "ODbL 1.0")

            scene = service.get_scene_graph(world_key="tsinghua_main")
            self.assertEqual(scene["world_key"], "tsinghua_main")
            self.assertIsNotNone(scene["wgs84_bounds"])
            self.assertIsInstance(scene["wgs84_bounds"], list)
            self.assertEqual(len(scene["wgs84_bounds"]), 4)
            min_lon, min_lat, max_lon, max_lat = scene["wgs84_bounds"]
            self.assertEqual(min_lon, 116.319)
            self.assertEqual(min_lat, 39.9988)
            self.assertEqual(max_lon, 116.323)
            self.assertEqual(max_lat, 39.999)
            self.assertIsNotNone(scene["bounds"])
            self.assertEqual(len(scene["nodes"]), len(nodes))

            # A browser viewport must receive only the nearby graph slice,
            # rather than every imported campus node on each refresh.
            local_scene = service.get_scene_graph(
                world_key="tsinghua_main",
                min_x=building_node["x"] - 1,
                min_z=building_node["z"] - 1,
                max_x=building_node["x"] + 1,
                max_z=building_node["z"] + 1,
            )
            self.assertTrue(local_scene["nodes"])
            self.assertLess(len(local_scene["nodes"]), len(scene["nodes"]))
            self.assertTrue(
                all(
                    building_node["x"] - 1 <= node["x"] <= building_node["x"] + 1
                    and building_node["z"] - 1 <= node["z"] <= building_node["z"] + 1
                    for node in local_scene["nodes"]
                )
            )

    def test_worlds_and_scene_api_endpoints(self):
        sample_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"@id": "node/101", "amenity": "library", "name": "清华图书馆"},
                    "geometry": {"type": "Point", "coordinates": [116.321, 40.001]},
                }
            ],
        }
        with self.engine.begin() as conn:
            import_real_world_geojson(conn, sample_geojson, world_key="tsinghua_main")

        with self.engine.connect() as conn:
            service = SpatialService(SpatialRepository(conn))
            worlds = service.list_worlds()
            self.assertIn("worlds", worlds)
            world_keys = {w["world_key"] for w in worlds["worlds"]}
            self.assertIn("tsinghua_main", world_keys)

            scene = service.get_scene_graph(world_key="tsinghua_main")
            self.assertEqual(scene["world_key"], "tsinghua_main")
            self.assertTrue(len(scene["nodes"]) >= 1)
            self.assertIsInstance(scene["wgs84_bounds"], list)
            self.assertEqual(len(scene["wgs84_bounds"]), 4)

    def test_real_location_catalog_returns_imported_pois_not_demo_labels(self):
        from sqlalchemy import text

        sample_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"@id": "node/501", "amenity": "restaurant", "name": "清晏楼餐厅"},
                    "geometry": {"type": "Point", "coordinates": [116.321, 40.001]},
                },
                {
                    "type": "Feature",
                    "properties": {"@id": "node/502", "amenity": "library", "name": "逸夫图书馆"},
                    "geometry": {"type": "Point", "coordinates": [116.322, 40.001]},
                },
                {
                    "type": "Feature",
                    "properties": {"@id": "way/503", "building": "residential", "name": "双清学生公寓"},
                    "geometry": {"type": "Polygon", "coordinates": [[[116.323, 40.001], [116.3232, 40.001], [116.3232, 40.0012], [116.323, 40.0012], [116.323, 40.001]]]},
                },
            ],
        }
        with self.engine.begin() as conn:
            import_real_world_geojson(conn, sample_geojson, world_key="tsinghua_main")
            self.assertEqual(best_real_location(conn, "consume"), "清晏楼餐厅")
            self.assertEqual(best_real_location(conn, "attend_class"), "逸夫图书馆")
            self.assertEqual(best_real_location(conn, "rest"), "双清学生公寓")
            options = [name for name, _ in real_location_options(conn, "大二学生", 12)]
            ranked = rank_real_location_options(conn, 901, "consume", hour=12)
        self.assertIn("清晏楼餐厅", options)
        self.assertNotIn("食堂", options)
        self.assertEqual(ranked[0]["location"], "清晏楼餐厅")
        self.assertIn("travel_minutes_estimate", ranked[0]["reasons"])

    def test_multi_world_importer_road_attachment_isolation(self):
        world_a_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"@id": "way/a_road", "highway": "footway", "name": "A路"},
                    "geometry": {"type": "LineString", "coordinates": [[116.310, 39.990], [116.312, 39.990]]},
                },
                {
                    "type": "Feature",
                    "properties": {"@id": "way/a_building", "building": "yes", "name": "A楼"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[116.310, 39.990], [116.311, 39.990], [116.311, 39.991], [116.310, 39.991], [116.310, 39.990]]]
                    },
                },
            ],
        }
        world_b_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"@id": "way/b_building", "building": "yes", "name": "B楼"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[116.310, 39.990], [116.311, 39.990], [116.311, 39.991], [116.310, 39.991], [116.310, 39.990]]]
                    },
                },
            ],
        }

        with self.engine.begin() as conn:
            import_real_world_geojson(conn, world_a_geojson, world_key="world_a")
            import_real_world_geojson(conn, world_b_geojson, world_key="world_b")

        with self.engine.connect() as conn:
            repo = SpatialRepository(conn)
            service = SpatialService(repo)
            nodes_a = repo.list_nodes(world_key="world_a")
            nodes_b = repo.list_nodes(world_key="world_b")

            # World B has no road in its import, so its building must NOT cross-attach to World A's path points
            scene_b = service.get_scene_graph(world_key="world_b")
            self.assertEqual(len(scene_b["edges"]), 0, "World B must not attach to path points from World A")
            self.assertTrue(all(n["world_key"] == "world_a" for n in nodes_a))
            self.assertTrue(all(n["world_key"] == "world_b" for n in nodes_b))

    def test_agent_spatial_states_contain_wgs84_coords(self):
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO spatial_nodes (id, world_key, code, name, node_type, longitude, latitude, x, y, z, radius, capacity, status, properties) "
                     "VALUES (201, 'tsinghua_main', 'node_201', '宿舍楼', 'building', 116.325, 40.005, 0, 0, 0, 10, 50, 'open', '{}')")
            )
            conn.execute(
                text("INSERT INTO residents (id, name, role, personality, goal, money, location) VALUES (88, '经纬度Agent', '学生', '{}', '{}', 1000, '宿舍楼')")
            )
            conn.execute(
                text("INSERT INTO agent_spatial_states (resident_id, current_node_id, x, y, z, facing_x, facing_z, movement_status, progress, path, path_index, route_distance_meters, remaining_distance_meters, updated_tick, version, branch_key, replan_count, last_replan_reason) "
                     "VALUES (88, 201, 0.0, 0.0, 0.0, 0.0, 1.0, 'idle', 1.0, '[]', 0, 0.0, 0.0, 0, 1, 'main', 0, '')")
            )
            conn.execute(
                text("INSERT INTO agent_spatial_capabilities (resident_id, base_speed_m_per_min, mobility_class, accessibility_needs, perception_radius_m, hearing_radius_m, version, source) "
                     "VALUES (88, 80.0, 'standard', '{}', 100.0, 30.0, 1, 'system')")
            )

        with self.engine.connect() as conn:
            repo = SpatialRepository(conn)
            service = SpatialService(repo)
            states = service.list_agent_states()
            self.assertIn("agents", states)
            agents = states["agents"]
            self.assertGreater(len(agents), 0, "Agent states list must not be empty")
            has_wgs84 = any(a.get("longitude") is not None and a.get("latitude") is not None for a in agents)
            self.assertTrue(has_wgs84, "At least one agent must have non-null WGS84 longitude and latitude")

    def test_set_agent_destination_triggers_movement(self):
        from app.spatial.router import set_agent_destination
        from app.spatial.schemas import SetDestinationRequest
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO spatial_nodes (id, world_key, code, name, node_type, longitude, latitude, x, y, z, radius, capacity, status, properties) "
                     "VALUES (101, 'tsinghua_main', 'origin', '起点', 'path', 116.32, 40.00, 0, 0, 0, 10, 50, 'open', '{}')")
            )
            conn.execute(
                text("INSERT INTO spatial_nodes (id, world_key, code, name, node_type, longitude, latitude, x, y, z, radius, capacity, status, properties) "
                     "VALUES (102, 'tsinghua_main', 'teaching_building', '教学楼', 'building', 116.321, 40.001, 10, 0, 10, 10, 50, 'open', '{}')")
            )
            conn.execute(
                text("INSERT INTO spatial_edges (from_node_id, to_node_id, distance_meters, base_minutes, bidirectional, status, congestion_factor, weather_factor, properties) "
                     "VALUES (101, 102, 100.0, 1.0, 1, 'open', 1.0, 1.0, '{}')")
            )
            conn.execute(
                text("INSERT INTO residents (id, name, role, personality, goal, money, location) VALUES (99, '测试Agent', '学生', '{}', '{}', 1000, '起点')")
            )
            conn.execute(
                text("INSERT INTO agent_spatial_states (resident_id, current_node_id, x, y, z, facing_x, facing_z, movement_status, progress, path, path_index, route_distance_meters, remaining_distance_meters, updated_tick, version, branch_key, replan_count, last_replan_reason) "
                     "VALUES (99, 101, 0.0, 0.0, 0.0, 0.0, 1.0, 'idle', 1.0, '[]', 0, 0.0, 0.0, 0, 1, 'main', 0, '')")
            )
            conn.execute(
                text("INSERT INTO agent_spatial_capabilities (resident_id, base_speed_m_per_min, mobility_class, accessibility_needs, perception_radius_m, hearing_radius_m, version, source) "
                     "VALUES (99, 80.0, 'standard', '{}', 100.0, 30.0, 1, 'system')")
            )

        res = set_agent_destination(99, SetDestinationRequest(destination="教学楼", constraint_response="auto"))

        self.assertEqual(res["resident_id"], 99)
        self.assertEqual(res["movement_status"], "moving")

        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT target_node_id, movement_status FROM agent_spatial_states WHERE resident_id = 99")
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row.movement_status, "moving")
            self.assertEqual(row.target_node_id, 102)

    def test_create_spatial_event_with_wgs84_coordinates(self):
        from app.spatial.router import create_spatial_event
        from app.spatial.schemas import CreateSpatialEventRequest
        from app.main import get_world_events
        from sqlalchemy import text

        payload = CreateSpatialEventRequest(
            world_key="tsinghua_main",
            longitude=116.3214,
            latitude=40.0012,
            event_type="physical_environment_change",
            title="测试物理波动事件",
            description="自动化测试提交的地理物理波动事件"
        )
        res = create_spatial_event(payload)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["longitude"], 116.3214)
        self.assertEqual(res["latitude"], 40.0012)
        self.assertEqual(res["title"], "测试物理波动事件")

        # 1. Database-level persistence assertion
        with self.engine.connect() as conn:
            event_row = conn.execute(
                text("SELECT * FROM world_event_stream WHERE id = :event_id"), {"event_id": res["event_id"]}
            ).fetchone()
            self.assertIsNotNone(event_row, "Event must be recorded in canonical world_event_stream table")
            self.assertEqual(event_row.event_type, "physical_environment_change")
            self.assertEqual(event_row.title, "测试物理波动事件")
            payload_data = json.loads(event_row.payload) if isinstance(event_row.payload, str) else event_row.payload
            self.assertEqual(payload_data["longitude"], 116.3214)
            self.assertEqual(payload_data["latitude"], 40.0012)

        # 2. Verify reading via /api/world/events handler
        world_events_res = get_world_events(after_id=0, limit=50)
        events_list = world_events_res.get("events", []) if isinstance(world_events_res, dict) else world_events_res
        matched_event = next((e for e in events_list if e.get("id") == res["event_id"] or e.get("title") == "测试物理波动事件"), None)
        self.assertIsNotNone(matched_event, "Map event must be readable from /api/world/events")
        self.assertEqual(matched_event.get("title"), "测试物理波动事件")

    def test_scene_exposes_factual_node_physical_state(self):
        """The map must receive physical facts, not a time-slot crowd template."""
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO spatial_nodes
                  (code, name, node_type, world_key, x, y, z, radius, capacity, status, properties)
                VALUES ('physical-test-node', '物理状态测试楼', 'building', 'test_world', 1, 0, 1, 8, 20, 'closed', '{}')
            """))
            refreshed = refresh_spatial_physical_states(
                conn, world_key="test_world",
                environment={"temperature": 31, "rainfall": 4, "weather": "小雨"},
                observed_at="2026-08-13T02:15:00+00:00",
            )
            self.assertEqual(refreshed["updated"], 1)

        with self.engine.connect() as conn:
            scene = SpatialService(SpatialRepository(conn)).get_scene_graph(world_key="test_world")
        self.assertEqual(len(scene["physical_states"]), 1)
        state = scene["physical_states"][0]
        self.assertEqual(state["access_status"], "closed")
        self.assertEqual(state["temperature_c"], 31.0)
        self.assertEqual(state["precipitation"], 4.0)
        self.assertEqual(state["illumination"], 0.25)

    def test_manual_physical_closure_survives_weather_refresh(self):
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO spatial_nodes
                  (code, name, node_type, world_key, x, y, z, radius, capacity, status, properties)
                VALUES ('closure-test-node', '封闭测试楼', 'building', 'test_world', 2, 0, 2, 8, 20, 'open', '{}')
            """))
            node_id = conn.execute(text("SELECT id FROM spatial_nodes WHERE code = 'closure-test-node'")).scalar_one()
            refresh_spatial_physical_states(conn, world_key="test_world", environment={})
            mutation = apply_spatial_physical_event(
                conn, world_key="test_world", node_id=node_id,
                access_status="closed", duration_minutes=30,
            )
            self.assertEqual(mutation["access_status"], "closed")
            refresh_spatial_physical_states(conn, world_key="test_world", environment={"temperature": 20})

        with self.engine.connect() as conn:
            state = conn.execute(text("SELECT access_status, source FROM spatial_physical_states WHERE node_id = :id"), {"id": node_id}).mappings().one()
        self.assertEqual(state["access_status"], "closed")
        self.assertEqual(state["source"], "map_event")

    def test_edge_closure_is_dynamic_and_changes_route_input(self):
        from app.spatial.runtime import _load_edges
        from sqlalchemy import text

        with self.engine.begin() as conn:
            for code, x in (("edge-a", 0), ("edge-b", 10)):
                conn.execute(text("""
                    INSERT INTO spatial_nodes
                    (code, name, node_type, world_key, x, y, z, radius, capacity, status, properties)
                    VALUES (:code, :code, 'poi', 'edge_world', :x, 0, 0, 2, 1, 'open', '{}')
                """), {"code": code, "x": x})
            ids = dict(conn.execute(text("SELECT code, id FROM spatial_nodes WHERE world_key = 'edge_world'")).all())
            conn.execute(text("""
                INSERT INTO spatial_edges
                (from_node_id, to_node_id, distance_meters, base_minutes, bidirectional, status, congestion_factor, weather_factor, properties)
                VALUES (:a, :b, 10, 1, 1, 'open', 1, 1, '{}')
            """), {"a": ids["edge-a"], "b": ids["edge-b"]})
            edge_id = conn.execute(text("SELECT id FROM spatial_edges WHERE from_node_id = :id"), {"id": ids["edge-a"]}).scalar_one()
            apply_spatial_physical_event(conn, world_key="edge_world", edge_id=edge_id, access_status="closed", duration_minutes=30)
            edge = next(item for item in _load_edges(conn) if item["id"] == edge_id)
        self.assertEqual(edge["status"], "closed")


if __name__ == "__main__":
    unittest.main()
