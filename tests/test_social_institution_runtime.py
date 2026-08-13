import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import app.main as main
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.models import SCHEMA_SQL
from app.organizations.schema import ORGANIZATION_RUNTIME_SQL
from app.social_institutions.schema import SOCIAL_INSTITUTION_RUNTIME_SQL
from app.social_institutions.service import (
    calculate_power_profiles,
    generate_clarifications,
    process_institutional_cases,
    propagate_information,
    seed_social_institution_runtime,
    submit_appeal,
    submit_institutional_case,
    transmit_information,
)


class SocialInstitutionRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        for resident_id, money, location in (
            (1, 50, "图书馆"),
            (2, 100, "图书馆"),
            (3, 100, "图书馆"),
            (4, 100, "宿舍区"),
        ):
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, '学生', '测试', '学习', ?, ?)
                """,
                (resident_id, f"居民{resident_id}", money, location),
            )
            self.conn.execute(
                """
                INSERT INTO agent_profiles
                (resident_id, gender, avatar_style, energy, mood,
                 current_task, schedule, perception, strategy)
                VALUES (?, '女', '测试', 80, '平稳', '学习', '[]', '{}', '{}')
                """,
                (resident_id,),
            )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        main.ensure_social_system_tables(self.conn, allow_ddl=True)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(ORGANIZATION_RUNTIME_SQL)
        self.conn.executescript(SOCIAL_INSTITUTION_RUNTIME_SQL)
        self.conn.execute(
            """
            CREATE TABLE agent_capability_profiles (
                resident_id INTEGER PRIMARY KEY,
                information_literacy INTEGER NOT NULL,
                language_access INTEGER NOT NULL,
                institutional_access INTEGER NOT NULL
            )
            """
        )
        for resident_id, literacy, access in (
            (1, 35, 35), (2, 80, 75), (3, 55, 50), (4, 45, 45)
        ):
            self.conn.execute(
                "INSERT INTO agent_capability_profiles VALUES (?, ?, 70, ?)",
                (resident_id, literacy, access),
            )
        self.conn.execute(
            "INSERT INTO relationships VALUES (1, 2, 70, '信任')"
        )
        self.conn.execute(
            "INSERT INTO relationships VALUES (2, 3, 60, '熟悉')"
        )
        self.now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
        self.seed = seed_social_institution_runtime(self.conn, self.now)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def create_claim(self, claim_type="rumor", truth_status="disputed"):
        cursor = self.conn.execute(
            """
            INSERT INTO information_claims
            (claim_key, claim_type, title, canonical_content, source_type,
             source_id, origin_resident_id, truth_status,
             source_reliability, occurred_at)
            VALUES (?, ?, '奖学金传闻', '奖学金名单尚未公布', 'test', '1', 1,
                    ?, 50, ?)
            """,
            (f"claim:{claim_type}:{truth_status}", claim_type, truth_status, self.now.isoformat()),
        )
        claim_id = int(cursor.lastrowid)
        version = self.conn.execute(
            """
            INSERT INTO information_versions
            (version_key, claim_id, content, fidelity, distortion_score,
             transformation_type, created_by_resident_id, created_at_world)
            VALUES (?, ?, '奖学金名单尚未公布', 100, 0, 'original', 1, ?)
            """,
            (f"version:{claim_id}", claim_id, self.now.isoformat()),
        )
        return claim_id, int(version.lastrowid)

    def test_seed_is_idempotent_and_power_is_evidence_based(self):
        second = seed_social_institution_runtime(self.conn, self.now)
        self.assertEqual(self.seed["channels"], 4)
        self.assertEqual(self.seed["rules"], 5)
        self.assertEqual(second["channels_created"], 0)
        self.assertEqual(second["rules_created"], 0)
        profiles = calculate_power_profiles(self.conn, self.now)
        self.assertEqual(len(profiles), 4)
        influence = self.conn.execute(
            "SELECT informal_influence FROM resident_power_profiles WHERE resident_id = 2"
        ).fetchone()["informal_influence"]
        self.assertGreater(int(influence), 0)

    def test_transmission_preserves_path_and_creates_distinct_version(self):
        claim_id, version_id = self.create_claim()
        first = transmit_information(
            self.conn,
            claim_id=claim_id,
            version_id=version_id,
            sender_resident_id=1,
            recipient_resident_id=2,
            channel_key="in-person",
            world_time=self.now,
            evidence_type="co_location",
            location="图书馆",
        )
        second = transmit_information(
            self.conn,
            claim_id=claim_id,
            version_id=first["version_id"],
            sender_resident_id=2,
            recipient_resident_id=3,
            channel_key="social-feed",
            world_time=self.now,
            parent_transmission_id=first["id"],
            evidence_type="relationship",
            evidence_id="2:3",
        )
        self.assertEqual(second["parent_transmission_id"], first["id"])
        self.assertNotEqual(second["version_id"], first["version_id"])
        self.assertGreaterEqual(second["version"]["distortion_score"], first["version"]["distortion_score"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) value FROM information_beliefs").fetchone()["value"],
            2,
        )

    def test_distorted_rumor_triggers_authority_clarification(self):
        claim_id, version_id = self.create_claim()
        for resident_id in (1, 2, 3):
            transmit_information(
                self.conn,
                claim_id=claim_id,
                version_id=version_id,
                sender_resident_id=4,
                recipient_resident_id=resident_id,
                channel_key="social-feed",
                world_time=self.now,
                evidence_type="relationship",
            )
        self.conn.execute(
            "UPDATE information_versions SET distortion_score = 45, fidelity = 55 WHERE claim_id = ? AND transformation_type <> 'original'",
            (claim_id,),
        )
        created = generate_clarifications(self.conn, self.now)
        self.assertEqual(len(created), 1)
        claim = self.conn.execute(
            "SELECT truth_status FROM information_claims WHERE id = ?", (claim_id,)
        ).fetchone()
        self.assertEqual(claim["truth_status"], "corrected")
        self.assertGreater(
            self.conn.execute(
                """
                SELECT COUNT(*) value FROM information_versions
                WHERE claim_id = ? AND transformation_type = 'clarification'
                """,
                (claim_id,),
            ).fetchone()["value"],
            0,
        )

    def test_same_rule_has_different_delay_from_procedural_access(self):
        self.conn.execute(
            "UPDATE residents SET role = '大一学生' WHERE id = 1"
        )
        low = submit_institutional_case(
            self.conn,
            case_key="leave:low",
            rule_key="student-leave",
            subject_resident_id=1,
            world_time=self.now,
            evidence={"reason": "身体不适", "time_window": "上午"},
        )
        high = submit_institutional_case(
            self.conn,
            case_key="leave:high",
            rule_key="student-leave",
            subject_resident_id=2,
            world_time=self.now,
            evidence={"reason": "身体不适", "time_window": "上午"},
        )
        low_delay = datetime.fromisoformat(low["due_at"]) - self.now
        high_delay = datetime.fromisoformat(high["due_at"]) - self.now
        self.assertLess(high_delay, low_delay)

    def test_sanction_uses_ledger_and_changes_trust_and_opportunity(self):
        case = submit_institutional_case(
            self.conn,
            case_key="conduct:1",
            rule_key="conduct-review",
            subject_resident_id=1,
            world_time=self.now,
            evidence={"source_event_id": "event-1", "witness": "2"},
        )
        result = process_institutional_cases(
            self.conn, self.now + timedelta(hours=3)
        )
        decision = self.conn.execute(
            "SELECT * FROM institutional_decisions WHERE case_id = ?", (case["id"],)
        ).fetchone()
        self.assertEqual(len(result["decisions"]), 1)
        self.assertEqual(decision["outcome"], "sanctioned")
        self.assertGreater(int(decision["consequence_minor"]), 0)
        self.assertLess(int(decision["opportunity_delta"]), 0)
        self.assertIsNotNone(decision["ledger_transaction_id"])
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_unfair_decision_can_be_overturned_by_appeal(self):
        parent = submit_institutional_case(
            self.conn,
            case_key="bypass:1",
            rule_key="restricted-access",
            subject_resident_id=1,
            world_time=self.now,
            evidence={"purpose": "实验", "resource": "实验室"},
            bypass_attempted=True,
        )
        process_institutional_cases(self.conn, self.now + timedelta(hours=3))
        appeal = submit_appeal(
            self.conn,
            parent_case_id=parent["id"],
            resident_id=1,
            reason="原流程未给予陈述机会",
            world_time=self.now + timedelta(hours=4),
        )
        process_institutional_cases(self.conn, self.now + timedelta(hours=8))
        decision = self.conn.execute(
            "SELECT outcome FROM institutional_decisions WHERE case_id = ?",
            (appeal["id"],),
        ).fetchone()
        self.assertEqual(decision["outcome"], "appeal_upheld")


if __name__ == "__main__":
    unittest.main()
