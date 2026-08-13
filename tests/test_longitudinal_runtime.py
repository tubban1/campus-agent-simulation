import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command

import app.main as main
from app.adaptation.institutions import seed_rule_primitives
from app.adaptation.service import seed_constraint_runtime
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.longitudinal.service import (
    get_life_course,
    process_longitudinal_runtime,
    seed_longitudinal_runtime,
)
from app.models import SCHEMA_SQL
from app.population.service import (
    process_population_runtime,
    schedule_population_event,
    seed_population_runtime,
)


class LongitudinalRuntimeTest(unittest.TestCase):
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
        conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (1, '林程', '大四学生', '谨慎', '完成毕业项目', 100, '宿舍区')
            """
        )
        conn.execute(
            """
            INSERT INTO agent_profiles
            (resident_id, gender, avatar_style, energy, mood, current_task,
             skills, strategy, schedule, perception)
            VALUES (1, '男', '测试', 80, '平稳', '毕业项目',
                    '{}', '{}', '[]', '{}')
            """
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
        conn = self.connection()
        seed_population_runtime(conn)
        seed_constraint_runtime(conn)
        seed_rule_primitives(conn)
        self.first_seed = seed_longitudinal_runtime(
            conn, datetime(2026, 7, 1, tzinfo=timezone.utc)
        )
        self.second_seed = seed_longitudinal_runtime(
            conn, datetime(2026, 7, 1, tzinfo=timezone.utc)
        )
        conn.commit()
        conn.close()
        self.now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_seed_is_idempotent_and_high_score_diary_is_not_objective_turning_point(self):
        self.assertEqual(self.first_seed["created"], 1)
        self.assertEqual(self.first_seed["stages_created"], 1)
        self.assertEqual(self.second_seed["created"], 0)
        self.assertEqual(self.second_seed["stages_created"], 0)
        conn = self.connection()
        conn.execute(
            """
            INSERT INTO memories
            (resident_id, day, content, importance, memory_type, source)
            VALUES (1, 22, '个人日记：今天感觉不错', 5, 'diary', 'diary')
            """
        )
        process_longitudinal_runtime(conn, self.now)
        points = conn.execute(
            "SELECT COUNT(*) AS value FROM life_turning_points"
        ).fetchone()["value"]
        self.assertEqual(points, 0)
        check = conn.execute(
            """
            SELECT * FROM trajectory_reconciliations
            WHERE resident_id = 1 AND check_type = 'turning_point_evidence'
            """
        ).fetchone()
        self.assertEqual(check["status"], "passed")
        conn.close()

    def test_population_transition_creates_stage_and_confirmed_turning_point(self):
        conn = self.connection()
        schedule_population_event(
            conn,
            event_key="lin-graduation",
            event_type="graduation",
            resident_id=1,
            effective_at=self.now,
            payload={"reason": "完成毕业要求"},
        )
        process_population_runtime(conn, self.now)
        result = process_longitudinal_runtime(conn, self.now)
        self.assertGreaterEqual(result["new_turning_points"], 1)
        stages = conn.execute(
            """
            SELECT stage_type, status FROM life_course_stages
            WHERE resident_id = 1 ORDER BY id
            """
        ).fetchall()
        self.assertEqual(
            [(row["stage_type"], row["status"]) for row in stages],
            [("academic", "completed"), ("graduated", "active")],
        )
        point = conn.execute(
            """
            SELECT * FROM life_turning_points
            WHERE source_type = 'population_event'
            """
        ).fetchone()
        self.assertEqual(point["category"], "transition")
        self.assertEqual(point["evidence_layer"], "formal_institution")
        self.assertGreater(point["objective_evidence_count"], 0)
        history = get_life_course(conn, 1)
        self.assertEqual(
            history["profile"]["current_stage_key"], "life-stage:1:2"
        )
        conn.close()

    def test_experience_learning_creates_traceable_path_and_persistent_profile(self):
        conn = self.connection()
        experience = conn.execute(
            """
            INSERT INTO experience_records
            (experience_key, branch_key, tick_number, resident_id,
             source_type, source_id, event_type, objective_summary, outcome,
             location, occurred_at, evidence_json)
            VALUES ('experience:route:1', 'main', 10, 1, 'world_event', '55',
                    'route_blocked', '早期路线受阻', 'failed', '图书馆', ?, '{}')
            """,
            (self.now.isoformat(),),
        )
        memory = conn.execute(
            """
            INSERT INTO adaptive_memories
            (memory_key, resident_id, experience_id, memory_type,
             interpretation, confidence, salience, valence, strength,
             last_reinforced_at)
            VALUES ('adaptive-memory:route:1', 1, ?, 'strategy',
                    '下次提前绕行', 0.8, 75, -20, 0.9, ?)
            """,
            (experience.lastrowid, self.now.isoformat()),
        )
        update = conn.execute(
            """
            INSERT INTO learning_updates
            (update_key, branch_key, tick_number, resident_id, experience_id,
             memory_id, target_type, target_key, before_json, after_json,
             update_reason, occurred_at)
            VALUES ('learning:route:1', 'main', 10, 1, ?, ?, 'strategy',
                    'avoid_congestion', '{"utility": 0.1}', '{"utility": 0.7}',
                    '路线失败后提高绕行策略效用', ?)
            """,
            (experience.lastrowid, memory.lastrowid, self.now.isoformat()),
        )
        first = process_longitudinal_runtime(conn, self.now)
        self.assertEqual(first["new_path_links"], 1)
        link = conn.execute(
            "SELECT * FROM path_dependency_links WHERE to_id = ?",
            (str(update.lastrowid),),
        ).fetchone()
        self.assertEqual(link["from_id"], str(experience.lastrowid))
        self.assertEqual(link["direction"], "reinforces")
        profile_before = conn.execute(
            "SELECT version, habit_state_json FROM longitudinal_profiles WHERE resident_id = 1"
        ).fetchone()
        self.assertIn("avoid_congestion", profile_before["habit_state_json"])
        second = process_longitudinal_runtime(
            conn, self.now + timedelta(days=1)
        )
        self.assertEqual(second["new_path_links"], 0)
        profile_after = conn.execute(
            "SELECT version, habit_state_json FROM longitudinal_profiles WHERE resident_id = 1"
        ).fetchone()
        self.assertGreater(profile_after["version"], profile_before["version"])
        self.assertIn("avoid_congestion", profile_after["habit_state_json"])
        conn.close()

    def test_norm_and_formal_institution_remain_distinct_evidence_layers(self):
        conn = self.connection()
        norm = conn.execute(
            """
            INSERT INTO norm_candidates
            (norm_key, name, behavior_key, group_type, group_key,
             context_type, context_key, state, evidence_window_start,
             evidence_window_end, first_detected_at, last_updated_at)
            VALUES ('norm:quiet', '图书馆保持安静', 'quiet',
                    'role', '学生', 'location', '图书馆', 'established',
                    ?, ?, ?, ?)
            """,
            (
                (self.now - timedelta(days=7)).isoformat(),
                self.now.isoformat(),
                (self.now - timedelta(days=7)).isoformat(),
                self.now.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO norm_responses
            (response_key, norm_id, resident_id, response_type,
             public_behavior, private_stance, detected, consequence_json,
             source_type, source_id, occurred_at)
            VALUES ('norm-response:challenge', ?, 1, 'open_challenge',
                    '公开质疑安静规范', 'oppose', 1, '{"discussion": true}',
                    'world_event', '99', ?)
            """,
            (norm.lastrowid, self.now.isoformat()),
        )
        primitive = conn.execute(
            "SELECT id FROM rule_primitives ORDER BY id LIMIT 1"
        ).fetchone()
        organization = conn.execute(
            "SELECT id FROM campus_organizations ORDER BY id LIMIT 1"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO institutional_rule_proposals
            (proposal_key, source_norm_id, organization_id,
             proposer_resident_id, primitive_id, title, rationale,
             scope_type, scope_key, submitted_at, decided_at, enacted_at,
             status)
            VALUES ('proposal:quiet', ?, ?, 1, ?, '试行安静时段',
                    '回应规范冲突', 'space', '图书馆', ?, ?, ?, 'enacted')
            """,
            (
                norm.lastrowid,
                organization["id"],
                primitive["id"],
                self.now.isoformat(),
                self.now.isoformat(),
                self.now.isoformat(),
            ),
        )
        process_longitudinal_runtime(conn, self.now)
        layers = conn.execute(
            """
            SELECT evidence_layer, category FROM life_turning_points
            WHERE resident_id = 1 ORDER BY id
            """
        ).fetchall()
        self.assertIn(("group_norm", "norm"), [(r["evidence_layer"], r["category"]) for r in layers])
        self.assertIn(("formal_institution", "institution"), [(r["evidence_layer"], r["category"]) for r in layers])
        conn.close()


if __name__ == "__main__":
    unittest.main()
