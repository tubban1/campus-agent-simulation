import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command

import app.main as main
from app.db import create_database_engine
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.economy.service import seed_economy_foundation
from app.models import SCHEMA_SQL
from app.population.service import (
    get_resident_population_history,
    process_population_runtime,
    schedule_population_event,
    seed_population_runtime,
)
from app.spatial.seed import seed_spatial_foundation


class PopulationRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "campus.db"
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')"
        )
        for resident_id, name, role in (
            (1, "即将毕业者", "大四学生"),
            (2, "在校成员", "大二学生"),
        ):
            conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, '测试', '完成阶段目标', 100, '宿舍区')
                """,
                (resident_id, name, role),
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
        main.seed_campus_organizations(conn)
        conn.commit()
        conn.close()

        config = get_alembic_config(f"sqlite+pysqlite:///{self.db_path}")
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{self.db_path}"
        )
        with self.engine.begin() as connection:
            seed_spatial_foundation(connection)
        conn = self.connection()
        seed_economy_foundation(conn)
        self.first_seed = seed_population_runtime(conn)
        self.second_seed = seed_population_runtime(conn)
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

    def test_seed_is_idempotent_and_preserves_existing_population(self):
        self.assertEqual(self.first_seed["profiles"], 2)
        self.assertEqual(self.first_seed["created"], 2)
        self.assertEqual(self.first_seed["roles_created"], 2)
        self.assertEqual(self.first_seed["residencies_created"], 2)
        self.assertEqual(self.second_seed["created"], 0)
        self.assertEqual(self.second_seed["roles_created"], 0)
        self.assertEqual(self.second_seed["residencies_created"], 0)

    def test_entry_role_change_and_residence_move_keep_full_history(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        entry = schedule_population_event(
            conn,
            event_key="new-student-chen",
            event_type="new_student",
            effective_at=now,
            payload={
                "resident": {
                    "name": "陈新",
                    "role": "大一学生",
                    "personality": "谨慎",
                    "goal": "适应大学生活",
                    "location": "宿舍区",
                }
            },
        )
        applied = process_population_runtime(conn, now)
        self.assertEqual(applied["applied"], [entry["id"]])
        resident = conn.execute(
            "SELECT * FROM residents WHERE name = '陈新'"
        ).fetchone()
        self.assertIsNotNone(resident)

        schedule_population_event(
            conn,
            event_key="chen-transfer",
            event_type="transfer_program",
            resident_id=resident["id"],
            effective_at=now + timedelta(hours=1),
            payload={"new_role": "计算机专业学生"},
        )
        schedule_population_event(
            conn,
            event_key="chen-dorm-move",
            event_type="dorm_move",
            resident_id=resident["id"],
            effective_at=now + timedelta(hours=1),
            payload={"location": "交换生公寓"},
        )
        process_population_runtime(conn, now + timedelta(hours=2))
        history = get_resident_population_history(conn, resident["id"])
        self.assertEqual(
            [row["role_key"] for row in history["roles"]],
            ["大一学生", "计算机专业学生"],
        )
        self.assertEqual(
            [row["status"] for row in history["roles"]], ["ended", "active"]
        )
        self.assertEqual(
            [row["location"] for row in history["residencies"]],
            ["宿舍区", "交换生公寓"],
        )
        self.assertEqual(
            [row["status"] for row in history["residencies"]],
            ["ended", "active"],
        )
        for table in (
            "agent_spatial_states",
            "agent_body_states",
            "agent_capability_profiles",
        ):
            self.assertIsNotNone(
                conn.execute(
                    f"SELECT * FROM {table} WHERE resident_id = ?",
                    (resident["id"],),
                ).fetchone()
            )
        self.assertIsNotNone(
            conn.execute(
                """
                SELECT * FROM economic_actors
                WHERE actor_key = ?
                """,
                (f"resident:{resident['id']}",),
            ).fetchone()
        )
        conn.close()

    def test_membership_transition_and_departure_preserve_organization_memory(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        organization = conn.execute(
            "SELECT id FROM campus_organizations ORDER BY id LIMIT 1"
        ).fetchone()
        schedule_population_event(
            conn,
            event_key="member-join",
            event_type="organization_join",
            resident_id=1,
            effective_at=now,
            payload={"organization_id": organization["id"], "member_role": "干事"},
        )
        process_population_runtime(conn, now)
        schedule_population_event(
            conn,
            event_key="member-graduate",
            event_type="graduation",
            resident_id=1,
            effective_at=now + timedelta(days=1),
            payload={"reason": "完成学业"},
        )
        process_population_runtime(conn, now + timedelta(days=1))

        profile = conn.execute(
            "SELECT * FROM population_profiles WHERE resident_id = 1"
        ).fetchone()
        self.assertEqual(profile["lifecycle_status"], "departed")
        membership = conn.execute(
            """
            SELECT * FROM organization_members
            WHERE organization_id = ? AND resident_id = 1
            """,
            (organization["id"],),
        ).fetchone()
        self.assertEqual(membership["status"], "inactive")
        transitions = conn.execute(
            """
            SELECT transition_type FROM membership_transitions
            WHERE resident_id = 1 ORDER BY id
            """
        ).fetchall()
        self.assertEqual(
            [row["transition_type"] for row in transitions],
            ["join", "departure"],
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) AS value FROM population_effects"
            ).fetchone()["value"],
            6,
        )
        self.assertIsNotNone(
            conn.execute("SELECT * FROM residents WHERE id = 1").fetchone()
        )
        conn.close()

    def test_departed_and_leave_agents_are_excluded_from_world_tick_selection(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        schedule_population_event(
            conn,
            event_key="graduate-one",
            event_type="graduation",
            resident_id=1,
            effective_at=now,
        )
        process_population_runtime(conn, now)
        runtime = main.get_world_runtime(conn)
        selected, _, _ = main.select_world_tick_agents(conn, runtime)
        self.assertEqual([row["id"] for row in selected], [2])

        schedule_population_event(
            conn,
            event_key="leave-two",
            event_type="leave_of_absence",
            resident_id=2,
            effective_at=now,
        )
        process_population_runtime(conn, now)
        selected, _, _ = main.select_world_tick_agents(conn, runtime)
        self.assertEqual(selected, [])
        self.assertGreater(
            conn.execute(
                "SELECT COUNT(*) AS value FROM relationships"
            ).fetchone()["value"],
            -1,
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
