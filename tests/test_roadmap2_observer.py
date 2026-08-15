import json
import unittest

from app.db import get_connection
from app.db.bootstrap_schema import (
    ensure_campus_state_table,
    ensure_roadmap2_observer_system,
    ensure_space_system,
)
from app.perception.continuity import (
    detect_continuity_gaps,
    generate_agent_hypotheses,
    update_agent_expectations,
)
from app.perception.pattern_observer import (
    get_active_group_patterns,
    promote_or_expire_patterns,
    scan_group_pattern_candidates,
)
from services.newspaper import audit_candidate_evidence, collect_candidates


class TestRoadmap2ObserverSystem(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection()
        ensure_campus_state_table(self.conn, allow_ddl=True)
        ensure_space_system(self.conn, allow_ddl=True)
        ensure_roadmap2_observer_system(self.conn, allow_ddl=True)

        # Seed isolated test resident without deleting live residents
        self.conn.execute(
            """
            INSERT INTO residents (id, name, role, location, personality, goal)
            VALUES (9999, '测试苏晴', '学生', '食堂', '开朗', '好好学习')
            ON CONFLICT (id) DO NOTHING
            """
        )
        self.conn.commit()

        # Seed spatial node & spatial memory for resident
        node_id = 1
        if self.conn.execute("PRAGMA table_info(spatial_nodes)").fetchall():
            row = self.conn.execute("SELECT id FROM spatial_nodes LIMIT 1").fetchone()
            if row:
                node_id = int(row["id"])
            else:
                self.conn.execute(
                    "INSERT INTO spatial_nodes (id, code, name, node_type, capacity, radius, x, y, z, status) VALUES (1, '食堂', '食堂', 'canteen', 50, 5.0, 0, 0, 0, 'active') ON CONFLICT DO NOTHING"
                )

        if self.conn.execute("PRAGMA table_info(agent_spatial_memories)").fetchall():
            obs = self.conn.execute("SELECT id FROM agent_observations WHERE observer_resident_id = 9999 LIMIT 1").fetchone()
            if obs:
                obs_id = int(obs["id"])
            else:
                obs_id = self.conn.execute(
                    """
                    INSERT INTO agent_observations
                    (observer_resident_id, subject_type, subject_id, modality, fact_type, summary, confidence, error_margin, metadata, observed_at, branch_key)
                    VALUES (9999, 'location', '食堂', 'direct', 'canteen_visit', '在食堂', 90, 0.0, '{}', '2026-08-14T08:00:00', 'main')
                    RETURNING id
                    """
                ).fetchone()["id"]
            self.conn.execute(
                """
                INSERT INTO agent_spatial_memories
                (resident_id, observation_id, node_id, memory_type, summary, salience, confidence, valence, visit_count, metadata, formed_at, branch_key)
                VALUES (9999, ?, ?, 'episodic', '常常在食堂吃饭', 80, 90, 0, 1, '{}', '2026-08-14T08:00:00', 'main')
                ON CONFLICT DO NOTHING
                """,
                (obs_id, node_id),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM agent_expectations WHERE resident_id = 9999")
        self.conn.execute("DELETE FROM continuity_observations WHERE resident_id = 9999")
        self.conn.execute("DELETE FROM agent_hypotheses WHERE resident_id = 9999")
        self.conn.execute("DELETE FROM agent_spatial_memories WHERE resident_id = 9999")
        self.conn.execute("DELETE FROM agent_observations WHERE observer_resident_id = 9999")
        self.conn.execute("DELETE FROM world_event_stream WHERE resident_id IN (9998, 9999)")
        self.conn.execute("DELETE FROM residents WHERE id IN (9998, 9999)")
        self.conn.execute("DELETE FROM group_pattern_candidates")
        self.conn.commit()
        self.conn.close()

    def test_r2_1_subject_continuity_and_cognitive_gaps(self):
        # 1. Test building baseline expectations
        expectations = update_agent_expectations(self.conn, resident_id=9999, day=1)
        self.assertIsInstance(expectations, list)

        # 2. Test gap detection
        self.conn.execute("DELETE FROM agent_observations WHERE observer_resident_id = 9999")
        self.conn.commit()
        gaps = detect_continuity_gaps(self.conn, resident_id=9999, day=2)
        self.assertTrue(len(gaps) > 0)
        gap_id = self.conn.execute("SELECT id FROM continuity_observations WHERE resident_id = 9999").fetchone()["id"]
        
        # 3. Test hypothesis generation
        hypotheses = generate_agent_hypotheses(self.conn, resident_id=9999, continuity_observation_id=gap_id)
        self.assertTrue(len(hypotheses) > 0)
        status = self.conn.execute("SELECT status FROM continuity_observations WHERE id = ?", (gap_id,)).fetchone()["status"]
        self.assertEqual(status, "investigating")

    def test_r2_2_group_pattern_observer(self):
        # Insert test events into world_event_stream
        self.conn.execute(
            """
            INSERT INTO world_event_stream (day, slot, location, event_type, resident_id, title, content, branch_key)
            VALUES (1, '08:00-12:00', '食堂', 'canteen_visit', 9999, '去食堂吃饭', '在食堂用餐', 'main')
            """
        )
        self.conn.execute(
            """
            INSERT INTO world_event_stream (day, slot, location, event_type, resident_id, title, content, branch_key)
            VALUES (1, '08:00-12:00', '食堂', 'canteen_visit', 9998, '也去食堂', '也在食堂用餐', 'main')
            """
        )
        self.conn.commit()

        # 1. Test scanning group patterns
        candidates = scan_group_pattern_candidates(self.conn, day=1)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]["location"], "食堂")

        # 2. Test promotion & active querying
        promo_res = promote_or_expire_patterns(self.conn, day=1)
        self.assertIn("promoted", promo_res)

        active = get_active_group_patterns(self.conn)
        self.assertTrue(len(active) > 0)

    def test_r2_3_editorial_agent_evidence_auditing(self):
        # Valid event
        ev_cursor = self.conn.execute(
            """
            INSERT INTO world_event_stream (day, slot, location, event_type, resident_id, title, content, branch_key)
            VALUES (1, '08:00-12:00', '图书馆', 'study', 9999, '学习', '在图书馆自习', 'main')
            RETURNING id
            """
        )
        ev_id = ev_cursor.fetchone()["id"]
        self.conn.commit()

        valid_candidate = {
            "resident_id": 9999,
            "source_event_id": ev_id,
            "category": "校园环境",
            "score": 60,
        }
        res_valid = audit_candidate_evidence(self.conn, valid_candidate)
        self.assertEqual(res_valid["decision"], "publish")

        invalid_candidate = {
            "resident_id": 9999,
            "source_event_id": 999999, # non-existent
            "category": "突发异常",
            "score": 90,
        }
        res_invalid = audit_candidate_evidence(self.conn, invalid_candidate)
        self.assertEqual(res_invalid["decision"], "reject")


if __name__ == "__main__":
    unittest.main()
