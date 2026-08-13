import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command

import app.main as main
from app.db import create_database_engine
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.models import SCHEMA_SQL
from app.resilience.service import (
    create_shock,
    process_resilience_runtime,
    replay_shock,
    seed_resilience_runtime,
)
from app.spatial.runtime import start_spatial_movement
from app.spatial.seed import seed_spatial_foundation


class ResilienceRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "campus.db"
        database_url = f"sqlite+pysqlite:///{self.db_path}"
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')"
        )
        for resident_id, name, location in (
            (1, "脆弱学生", "图书馆"),
            (2, "韧性学生", "图书馆"),
            (3, "未暴露学生", "宿舍区"),
        ):
            conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, '学生', '测试', '应对冲击', 100, ?)
                """,
                (resident_id, name, location),
            )
            conn.execute(
                """
                INSERT INTO agent_profiles
                (resident_id, gender, avatar_style, energy, mood, current_task,
                 skills, strategy, schedule, perception)
                VALUES (?, '女', '测试', 80, '平稳', '学习',
                        '{}', '{}', '[]', '{}')
                """,
                (resident_id,),
            )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(conn, allow_ddl=True)
        main.ensure_space_system(conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_external_information_system(conn, allow_ddl=True)
        main.ensure_world_runtime_tables(conn, allow_ddl=True)
        conn.commit()
        conn.close()
        config = get_alembic_config(database_url)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        self.engine = create_database_engine(database_url)
        with self.engine.begin() as connection:
            seed_spatial_foundation(connection)
        conn = self.connection()
        self.first_seed = seed_resilience_runtime(conn)
        self.second_seed = seed_resilience_runtime(conn)
        conn.execute(
            """
            UPDATE agent_capability_profiles
            SET stress_resilience = 10, economic_access = 10, social_capital = 10
            WHERE resident_id = 1
            """
        )
        conn.execute(
            """
            UPDATE agent_capability_profiles
            SET stress_resilience = 90, economic_access = 90, social_capital = 90
            WHERE resident_id = 2
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_seed_is_idempotent_and_covers_internal_shock_catalog(self):
        self.assertEqual(self.first_seed, {"definitions": 11, "created": 11})
        self.assertEqual(self.second_seed, {"definitions": 11, "created": 0})

    def test_facility_shock_soft_closes_space_and_records_heterogeneous_exposure(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        shock = create_shock(
            conn,
            instance_key="library-failure",
            shock_key="facility-closure",
            scheduled_at=now,
            severity=0.9,
            scope={"space": "图书馆"},
            random_seed=42,
            duration_minutes=60,
        )
        result = process_resilience_runtime(conn, now)
        self.assertEqual(result["activated"], [shock["id"]])
        status = conn.execute(
            "SELECT status FROM campus_spaces WHERE location = '图书馆'"
        ).fetchone()["status"]
        self.assertEqual(status, "临时关闭")
        movement = start_spatial_movement(
            conn,
            3,
            "图书馆",
            world_time=now,
            constraint_response="queue",
        )
        self.assertEqual(movement["movement_status"], "moving")
        self.assertFalse(
            bool(movement["constraint_evaluation"]["officially_permitted"])
        )
        exposures = conn.execute(
            """
            SELECT resident_id, consequence_score
            FROM resident_shock_exposures
            WHERE shock_instance_id = ? ORDER BY resident_id
            """,
            (shock["id"],),
        ).fetchall()
        self.assertEqual([row["resident_id"] for row in exposures], [1, 2])
        self.assertGreater(
            exposures[0]["consequence_score"], exposures[1]["consequence_score"]
        )
        conn.close()

    def test_recovery_restores_prior_state_and_preserves_history(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        shock = create_shock(
            conn,
            instance_key="short-library-failure",
            shock_key="facility-closure",
            scheduled_at=now,
            severity=0.7,
            scope={"space": "图书馆"},
            duration_minutes=30,
        )
        process_resilience_runtime(conn, now)
        result = process_resilience_runtime(conn, now + timedelta(minutes=31))
        self.assertEqual(result["resolved"], [shock["id"]])
        status = conn.execute(
            "SELECT status FROM campus_spaces WHERE location = '图书馆'"
        ).fetchone()["status"]
        self.assertEqual(status, "开放")
        impact = conn.execute(
            "SELECT * FROM shock_impacts WHERE shock_instance_id = ?",
            (shock["id"],),
        ).fetchone()
        self.assertEqual(impact["status"], "reverted")
        self.assertTrue(impact["applied_at"])
        self.assertTrue(impact["reverted_at"])
        transitions = conn.execute(
            """
            SELECT to_status FROM shock_state_transitions
            WHERE shock_instance_id = ? ORDER BY id
            """,
            (shock["id"],),
        ).fetchall()
        self.assertEqual(
            [row["to_status"] for row in transitions],
            ["active", "recovering", "resolved"],
        )
        conn.close()

    def test_replay_uses_same_definition_scope_severity_and_seed(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        original = create_shock(
            conn,
            instance_key="replay-source",
            shock_key="facility-closure",
            scheduled_at=now,
            severity=0.55,
            scope={"space": "图书馆"},
            random_seed=99,
            duration_minutes=45,
        )
        replay = replay_shock(
            conn,
            original["id"],
            "replay-copy",
            now + timedelta(days=1),
        )
        self.assertEqual(replay["source_type"], "replay")
        self.assertEqual(replay["replay_of_instance_id"], original["id"])
        self.assertEqual(replay["severity"], original["severity"])
        self.assertEqual(replay["scope_json"], original["scope_json"])
        self.assertEqual(replay["parameters_json"], original["parameters_json"])
        self.assertEqual(replay["random_seed"], original["random_seed"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
