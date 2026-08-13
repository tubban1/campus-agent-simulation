import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command

import app.main as main
from app.adaptation.service import (
    evaluate_space_constraint,
    resolve_boundary_attempt,
    seed_constraint_runtime,
)
from app.adaptation.learning import (
    decay_adaptive_memories,
    get_adaptive_cognitive_context,
    process_adaptive_learning,
)
from app.adaptation.norms import detect_norms, record_norm_signal
from app.adaptation.institutions import (
    enact_approved_rule,
    seed_rule_primitives,
    submit_rule_proposal,
)
from app.economy.service import seed_economy_foundation
from app.organizations.service import (
    cast_organization_vote,
    execute_organization_proposal,
    finalize_organization_proposal,
    seed_organization_runtime,
)
from app.db.migration_runtime import BASELINE_REVISION, get_alembic_config
from app.models import SCHEMA_SQL
from app.spatial.runtime import advance_active_movements, start_spatial_movement
from app.spatial.seed import seed_spatial_foundation
from app.db import create_database_engine


class SoftConstraintRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "campus.db"
        self.database_url = f"sqlite+pysqlite:///{self.db_path}"
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')"
        )
        for resident_id, name, location in (
            (1, "守规学生", "宿舍区"),
            (2, "冒险学生", "宿舍区"),
            (3, "旁观学生", "宿舍区"),
            (4, "苏晴", "宿舍区"),
        ):
            conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, '学生', '测试', '进入图书馆', 100, ?)
                """,
                (resident_id, name, location),
            )
            conn.execute(
                """
                INSERT INTO agent_profiles
                (resident_id, gender, avatar_style, energy, mood, current_task,
                 skills, strategy, schedule, perception)
                VALUES (?, '女', '测试', 80, '平稳', '移动',
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
        config = get_alembic_config(self.database_url)
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
        self.engine = create_database_engine(self.database_url)
        with self.engine.begin() as connection:
            seed_spatial_foundation(connection)
        conn = self.connection()
        seed_economy_foundation(conn)
        seed_organization_runtime(conn)
        self.first_seed = seed_constraint_runtime(conn)
        self.second_seed = seed_constraint_runtime(conn)
        self.primitive_seed = seed_rule_primitives(conn)
        self.primitive_seed_again = seed_rule_primitives(conn)
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

    def target_node(self, conn, location="图书馆"):
        row = conn.execute(
            """
            SELECT * FROM spatial_nodes
            WHERE json_extract(properties, '$.location') = ?
            """,
            (location,),
        ).fetchone()
        item = dict(row)
        item["properties"] = {"location": location}
        return item

    def test_seed_is_idempotent_and_records_versioned_rule_layers(self):
        self.assertEqual(self.first_seed, {"rules": 4, "created": 4})
        self.assertEqual(self.second_seed, {"rules": 4, "created": 0})
        conn = self.connection()
        layers = {
            row["constraint_layer"]
            for row in conn.execute("SELECT constraint_layer FROM constraint_rules")
        }
        self.assertEqual(
            layers, {"institutional", "service", "capacity", "enforcement"}
        )
        conn.close()

    def test_closed_space_is_not_a_hard_route_rejection(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        conn.execute(
            "UPDATE campus_spaces SET status = '维护中' WHERE location = '图书馆'"
        )
        result = start_spatial_movement(
            conn, 1, "图书馆", world_time=now, constraint_response="queue"
        )
        self.assertEqual(result["movement_status"], "moving")
        evaluation = result["constraint_evaluation"]
        self.assertTrue(bool(evaluation["physically_possible"]))
        self.assertFalse(bool(evaluation["officially_permitted"]))
        self.assertFalse(bool(evaluation["service_available"]))
        self.assertEqual(evaluation["selected_response"], "queue")
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) value FROM constraint_evaluations"
            ).fetchone()["value"],
            1,
        )
        conn.close()

    def test_bypass_resolves_success_detection_harm_and_consequences_separately(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        conn.execute(
            "UPDATE campus_spaces SET status = '维护中' WHERE location = '图书馆'"
        )
        evaluation = evaluate_space_constraint(
            conn,
            resident_id=2,
            target_node=self.target_node(conn),
            world_time=now,
            requested_response="bypass",
        )
        attempt = resolve_boundary_attempt(conn, evaluation, now)
        self.assertEqual(attempt["strategy"], "bypass")
        self.assertIn(attempt["status"], {"succeeded", "failed"})
        self.assertIn(int(attempt["succeeded"]), {0, 1})
        self.assertIn(int(attempt["detected"]), {0, 1})
        self.assertIn(int(attempt["harmed"]), {0, 1})
        types = {
            row["consequence_type"]
            for row in conn.execute(
                "SELECT consequence_type FROM constraint_consequences WHERE attempt_id = ?",
                (attempt["id"],),
            )
        }
        if attempt["succeeded"]:
            self.assertIn("admission", types)
        if attempt["detected"]:
            self.assertIn("detection", types)
        if attempt["harmed"]:
            self.assertIn("injury", types)
        conn.close()

    def test_capacity_pressure_can_queue_without_erasing_the_trip(self):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        conn = self.connection()
        conn.execute(
            "UPDATE campus_spaces SET capacity = 0 WHERE location = '图书馆'"
        )
        start = start_spatial_movement(
            conn, 1, "图书馆", world_time=now, constraint_response="queue"
        )
        self.assertEqual(start["movement_status"], "moving")
        progress = advance_active_movements(
            conn, now + timedelta(minutes=30), tick_number=1
        )
        self.assertEqual(progress[0]["movement_status"], "waiting")
        self.assertEqual(
            progress[0]["admission"]["selected_response"], "queue"
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) value FROM spatial_admission_queue"
            ).fetchone()["value"],
            1,
        )
        conn.close()

    def test_world_event_becomes_memory_and_updates_strategy_with_evidence(self):
        now = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
        conn = self.connection()
        event = main.append_world_event(
            conn,
            "spatial_boundary_bypassed",
            "冒险学生越过关闭边界",
            "冒险学生进入了未正式开放的图书馆。",
            tick_id=7,
            resident_id=2,
            location="图书馆",
            payload={
                "success": True,
                "target_node_id": 3,
                "boundary_attempt": {
                    "strategy": "bypass",
                    "succeeded": 1,
                    "detected": 0,
                    "harmed": 0,
                },
            },
            day=1,
            slot="08:00-16:00",
        )
        result = process_adaptive_learning(
            conn,
            world_time=now,
            tick_id=7,
            tick_number=7,
            branch_key="main",
            resident_ids=[2],
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["experiences_created"], 1)
        experience = conn.execute(
            "SELECT * FROM experience_records WHERE source_id = ?",
            (str(event["id"]),),
        ).fetchone()
        memory = conn.execute(
            "SELECT * FROM adaptive_memories WHERE experience_id = ?",
            (experience["id"],),
        ).fetchone()
        strategy = conn.execute(
            """
            SELECT * FROM strategy_states
            WHERE resident_id = 2 AND strategy_key = 'bypass'
            """
        ).fetchone()
        update = conn.execute(
            "SELECT * FROM learning_updates WHERE experience_id = ?",
            (experience["id"],),
        ).fetchone()
        self.assertEqual(memory["memory_type"], "strategy")
        self.assertGreater(memory["salience"], 50)
        self.assertEqual(strategy["success_count"], 1)
        self.assertGreater(strategy["expected_utility"], 0)
        self.assertEqual(update["target_type"], "strategy")
        context = get_adaptive_cognitive_context(conn, 2)
        self.assertEqual(context["adaptive_memories"][0]["id"], memory["id"])
        self.assertEqual(context["strategy_states"][0]["id"], strategy["id"])
        conn.close()

    def test_memory_decay_weakens_old_unused_memory_without_deleting_evidence(self):
        now = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
        conn = self.connection()
        experience = conn.execute(
            """
            INSERT INTO experience_records
            (experience_key, resident_id, source_type, source_id, event_type,
             objective_summary, outcome, occurred_at)
            VALUES ('old:experience', 1, 'manual', 'old', 'test',
                    '一段很久以前的经历', 'unknown', ?)
            """,
            ((now - timedelta(days=40)).isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO adaptive_memories
            (memory_key, resident_id, experience_id, memory_type,
             interpretation, confidence, salience, valence, strength,
             last_reinforced_at)
            VALUES ('old:memory', 1, ?, 'episodic', '旧记忆', 0.8, 50, 0,
                    0.3, ?)
            """,
            (
                experience.lastrowid,
                (now - timedelta(days=40)).isoformat(),
            ),
        )
        decay = decay_adaptive_memories(conn, now)
        stored = conn.execute(
            "SELECT * FROM adaptive_memories WHERE memory_key = 'old:memory'"
        ).fetchone()
        self.assertEqual(decay["forgotten"], 1)
        self.assertEqual(stored["status"], "forgotten")
        self.assertIsNotNone(
            conn.execute(
                "SELECT id FROM experience_records WHERE id = ?",
                (experience.lastrowid,),
            ).fetchone()
        )
        conn.close()

    def _norm_signal(
        self,
        conn,
        index,
        *,
        resident_id,
        signal_type,
        stance,
        group_key="学生",
        behavior_key="queue:quietly",
        context_key="食堂",
    ):
        return record_norm_signal(
            conn,
            signal_key=f"test:norm:{group_key}:{context_key}:{index}",
            behavior_key=behavior_key,
            signal_type=signal_type,
            stance=stance,
            group_type="role",
            group_key=group_key,
            context_type="space",
            context_key=context_key,
            source_type="manual_test",
            source_id=index,
            observed_at=(
                datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
                + timedelta(minutes=index)
            ),
            resident_id=resident_id,
            tick_number=index,
        )

    def test_repetition_without_social_feedback_does_not_create_a_norm(self):
        conn = self.connection()
        self._norm_signal(
            conn, 1, resident_id=1, signal_type="behavior", stance="neutral"
        )
        self._norm_signal(
            conn, 2, resident_id=2, signal_type="behavior", stance="neutral"
        )
        result = detect_norms(
            conn, datetime(2026, 7, 30, 11, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(result["updated_norm_ids"], [])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) value FROM norm_candidates").fetchone()[
                "value"
            ],
            0,
        )
        conn.close()

    def test_social_feedback_creates_traceable_local_norm_and_agent_beliefs(self):
        conn = self.connection()
        for index, resident_id in enumerate((1, 2, 3, 1, 2), start=1):
            self._norm_signal(
                conn,
                index,
                resident_id=resident_id,
                signal_type="behavior",
                stance="neutral",
            )
        for index, resident_id in enumerate((1, 2, 3), start=20):
            self._norm_signal(
                conn,
                index,
                resident_id=resident_id,
                signal_type="approval",
                stance="support",
            )
        result = detect_norms(
            conn, datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(len(result["updated_norm_ids"]), 1)
        norm = conn.execute("SELECT * FROM norm_candidates").fetchone()
        self.assertEqual(norm["state"], "established")
        self.assertEqual(norm["behavior_count"], 5)
        self.assertEqual(norm["distinct_actor_count"], 3)
        self.assertEqual(norm["feedback_count"], 3)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) value FROM norm_evidence WHERE norm_id = ?",
                (norm["id"],),
            ).fetchone()["value"],
            8,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) value FROM agent_norm_beliefs WHERE norm_id = ?",
                (norm["id"],),
            ).fetchone()["value"],
            3,
        )
        transition = conn.execute(
            "SELECT * FROM norm_state_transitions WHERE norm_id = ?",
            (norm["id"],),
        ).fetchone()
        self.assertEqual(transition["from_state"], "none")
        self.assertEqual(transition["to_state"], "established")
        conn.close()

    def test_local_groups_can_form_conflicting_norms(self):
        conn = self.connection()
        for group_key, stance in (("学生", "support"), ("管理员", "oppose")):
            for index, resident_id in enumerate((1, 2, 3), start=1):
                self._norm_signal(
                    conn,
                    index + (100 if group_key == "管理员" else 0),
                    resident_id=resident_id,
                    signal_type="behavior",
                    stance="neutral",
                    group_key=group_key,
                    behavior_key="after-hours-entry",
                    context_key="操场",
                )
            self._norm_signal(
                conn,
                90 if group_key == "学生" else 190,
                resident_id=1,
                signal_type="approval" if stance == "support" else "disapproval",
                stance=stance,
                group_key=group_key,
                behavior_key="after-hours-entry",
                context_key="操场",
            )
        detect_norms(conn, datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc))
        rows = conn.execute(
            """
            SELECT group_key, support_score, opposition_score
            FROM norm_candidates WHERE behavior_key = 'after-hours-entry'
            ORDER BY group_key
            """
        ).fetchall()
        self.assertEqual(len(rows), 2)
        by_group = {row["group_key"]: dict(row) for row in rows}
        self.assertGreater(by_group["学生"]["support_score"], 0)
        self.assertGreater(by_group["管理员"]["opposition_score"], 0)
        conn.close()

    def _approve_and_execute_rule_proposal(self, conn, proposal, now):
        organization_proposal_id = proposal["organization_proposal_id"]
        cast_organization_vote(
            conn,
            proposal_id=organization_proposal_id,
            resident_id=4,
            decision="approve",
            rationale="试点后复核",
        )
        decided = finalize_organization_proposal(
            conn,
            organization_proposal_id,
            world_time=now + timedelta(minutes=61),
        )
        self.assertEqual(decided["status"], "approved")
        executed = execute_organization_proposal(
            conn,
            organization_proposal_id,
            world_time=now + timedelta(minutes=62),
        )
        self.assertEqual(executed["status"], "executed")
        return enact_approved_rule(
            conn, proposal["id"], world_time=now + timedelta(minutes=63)
        )

    def test_rule_primitives_are_idempotent_and_reject_arbitrary_capabilities(self):
        self.assertEqual(self.primitive_seed, {"primitives": 5, "created": 5})
        self.assertEqual(
            self.primitive_seed_again, {"primitives": 5, "created": 0}
        )
        conn = self.connection()
        organization = conn.execute(
            "SELECT id FROM campus_organizations WHERE name = '学生会'"
        ).fetchone()
        with self.assertRaisesRegex(ValueError, "规则原语不存在"):
            submit_rule_proposal(
                conn,
                proposal_key="unsupported-code-rule",
                organization_id=organization["id"],
                proposer_resident_id=4,
                primitive_key="execute-arbitrary-python",
                title="任意代码规则",
                rationale="不应允许",
                scope_type="campus",
                scope_key="campus",
                parameters={"code": "pass"},
            )
        conn.close()

    def test_organization_process_enacts_and_versions_a_new_formal_rule(self):
        now = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)
        conn = self.connection()
        organization = conn.execute(
            "SELECT id FROM campus_organizations WHERE name = '学生会'"
        ).fetchone()
        first = submit_rule_proposal(
            conn,
            proposal_key="library-hours-v1",
            organization_id=organization["id"],
            proposer_resident_id=4,
            primitive_key="set-space-hours",
            title="延长图书馆开放时间",
            rationale="晚间学习需求持续出现",
            scope_type="space",
            scope_key="图书馆",
            parameters={"open_hour": 6, "close_hour": 23},
            world_time=now,
            monitoring_plan={"metric": "after_hours_demand"},
            repeal_conditions={"complaints_above": 20},
        )
        version1 = self._approve_and_execute_rule_proposal(conn, first, now)
        hours = conn.execute(
            "SELECT open_hour, close_hour FROM campus_spaces WHERE location = '图书馆'"
        ).fetchone()
        self.assertEqual((hours["open_hour"], hours["close_hour"]), (6, 23))
        self.assertEqual(version1["version"], 1)
        stored = conn.execute(
            "SELECT status FROM institutional_rule_proposals WHERE id = ?",
            (first["id"],),
        ).fetchone()
        self.assertEqual(stored["status"], "enacted")

        second = submit_rule_proposal(
            conn,
            proposal_key="library-hours-v2",
            organization_id=organization["id"],
            proposer_resident_id=4,
            primitive_key="set-space-hours",
            title="缩短试点开放时间",
            rationale="夜间维护成本高于预期",
            scope_type="space",
            scope_key="图书馆",
            parameters={"open_hour": 7, "close_hour": 22},
            world_time=now + timedelta(days=1),
        )
        version2 = self._approve_and_execute_rule_proposal(
            conn, second, now + timedelta(days=1)
        )
        versions = conn.execute(
            """
            SELECT version, status, effective_to
            FROM evolved_rule_versions
            WHERE lineage_key = ?
            ORDER BY version
            """,
            (version2["lineage_key"],),
        ).fetchall()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["status"], "superseded")
        self.assertTrue(versions[0]["effective_to"])
        self.assertEqual(versions[1]["status"], "active")
        self.assertEqual(version2["replaces_rule_version_id"], version1["id"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
