import json
import random
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app.main as main
from app.models import SCHEMA_SQL


class MultiscaleGoalRuntimeTest(unittest.TestCase):
    def setUp(self):
        random.seed(7)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute("INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')")
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (1, '测试学生', '学生', '认真、自律', '完成课程论文并争取奖学金', 100, '宿舍区')
            """
        )
        self.conn.execute(
            """
            INSERT INTO agent_profiles
            (resident_id, gender, avatar_style, energy, mood, current_task,
             skills, strategy, schedule, perception)
            VALUES (1, '女', '测试', 80, '平稳', '学习', '{}', '{}', '[]', '{}')
            """
        )
        main.SOCIAL_SCHEMA_READY = False
        main.ensure_social_system_tables(self.conn, allow_ddl=True)
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.WORLD_SCHEMA_READY = False
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.world_time = datetime.fromisoformat("2026-07-28T15:30:00+08:00")

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def resident(self):
        return dict(
            self.conn.execute(
                """
                SELECT r.*, p.strategy
                FROM residents r
                JOIN agent_profiles p ON p.resident_id = r.id
                WHERE r.id = 1
                """
            ).fetchone()
        )

    def ensure_plan(self):
        with patch.object(main, "build_llm_action_plan", return_value=None), patch(
            "app.world_runtime.planning_decision.build_llm_action_plan", return_value=None
        ):
            return main.ensure_current_action_plans(self.conn, self.world_time)

    def test_plan_execution_updates_all_goal_horizons_and_trajectory(self):
        result = self.ensure_plan()
        self.assertEqual(result["created"], 1)
        plan_row = self.conn.execute(
            "SELECT * FROM agent_action_plans WHERE resident_id = 1"
        ).fetchone()
        plan = json.loads(plan_row["plan_json"])
        self.assertEqual(
            set(plan["goal_chain"]),
            {
                "long_goal_id",
                "long_goal",
                "medium_goal_id",
                "medium_goal",
                "short_goal_id",
                "short_goal",
                "commitment_id",
                "commitment",
            },
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM agent_goals").fetchone()["count"],
            3,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM goal_dependencies").fetchone()["count"],
            2,
        )
        tick = self.conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status)
            VALUES (1, ?, 1, '08:00-16:00', 'test', 'running')
            """,
            (self.world_time.isoformat(),),
        )
        with patch.object(
            main,
            "build_autonomous_tick_decision",
            side_effect=lambda conn, agent, perception, step: main.fallback_runtime_decision(
                agent, step, "测试按计划执行", "test-rule"
            ),
        ):
            action_result = main.process_world_agent_tick(
                self.conn,
                self.resident(),
                self.world_time,
                tick.lastrowid,
                1,
                "08:00-16:00",
            )
        self.assertTrue(action_result["success"])
        self.assertIsNotNone(action_result["plan_outcome"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM plan_outcomes").fetchone()["count"],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM trajectory_episodes").fetchone()["count"],
            3,
        )
        progress = {
            row["horizon"]: row["progress"]
            for row in self.conn.execute("SELECT horizon, progress FROM agent_goals")
        }
        self.assertGreater(progress["short"], 0)
        self.assertGreater(progress["medium"], 0)
        self.assertGreater(progress["long"], 0)

    def test_expired_short_goal_is_reviewed_and_replaced(self):
        self.ensure_plan()
        old_short = self.conn.execute(
            "SELECT * FROM agent_goals WHERE horizon = 'short'"
        ).fetchone()
        self.conn.execute(
            """
            UPDATE agent_goals
            SET deadline_at = '2026-07-27T00:00:00+08:00',
                last_reviewed_day = 0, progress = 10
            WHERE id = ?
            """,
            (old_short["id"],),
        )
        self.conn.execute(
            "UPDATE simulation_state SET value = '2' WHERE key = 'current_day'"
        )
        context = main.ensure_multiscale_goal_structure(
            self.conn, self.resident(), self.world_time
        )
        old_status = self.conn.execute(
            "SELECT status FROM agent_goals WHERE id = ?", (old_short["id"],)
        ).fetchone()["status"]
        self.assertEqual(old_status, "paused")
        self.assertNotEqual(context["short"]["id"], old_short["id"])
        self.assertEqual(context["commitment"]["goal_id"], context["short"]["id"])
        revision = self.conn.execute(
            """
            SELECT revision_type FROM goal_revisions
            WHERE goal_id = ? ORDER BY id DESC LIMIT 1
            """,
            (old_short["id"],),
        ).fetchone()
        self.assertEqual(revision["revision_type"], "paused")

    def test_relationship_history_is_loaded_in_one_bounded_batch(self):
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (2, '测试同学', '学生', '外向', '参与校园协作', 100, '教学楼')
            """
        )
        self.conn.execute(
            """
            INSERT INTO relationships
            (from_resident_id, to_resident_id, score, notes)
            VALUES (1, 2, 65, '多次协作')
            """
        )
        self.conn.execute(
            """
            INSERT INTO relationship_dynamics
            (from_resident_id, to_resident_id, affinity, trust, cooperation,
             competition, conflict, tension, interaction_count, last_day)
            VALUES (1, 2, 70, 72, 78, 10, 5, 8, 15, 1)
            """
        )
        for index in range(15):
            self.conn.execute(
                """
                INSERT INTO relationship_change_events
                (day, from_resident_id, to_resident_id, interaction, reason)
                VALUES (1, 1, 2, 'collaborate', ?)
                """,
                (f"协作证据 {index}",),
            )
        histories = main.relationship_histories_by_target(self.conn, 1, [2], per_target=12)
        self.assertEqual(len(histories[2]), 12)
        interpretation = main.infer_emergent_relationship(
            self.conn,
            1,
            2,
            dynamics=dict(
                self.conn.execute(
                    """
                    SELECT * FROM relationship_dynamics
                    WHERE from_resident_id = 1 AND to_resident_id = 2
                    """
                ).fetchone()
            ),
            score=65,
            history_rows=histories[2],
        )
        self.assertIn("合作伙伴", [item["label"] for item in interpretation["candidates"]])
        self.assertTrue(interpretation["evidence"])
        graph = main.build_agent_social_graph(self.conn, 1, limit=10)
        self.assertEqual(len(graph["links"]), 1)
        self.conn.execute(
            """
            INSERT INTO simulation_action_logs
            (day, resident_id, perception, retrieved_memories, decision, execution,
             environment_feedback, state_before, state_after)
            VALUES (1, 1, '{}', '[]', ?, ?, '{}', '{}', '{}')
            """,
            (
                json.dumps({"action": "collaborate", "reason": "共同推进任务"}),
                json.dumps({"result": {"description": "完成一次协作"}}),
            ),
        )
        timeline = main.fetch_agent_timeline(self.conn, 1, limit=20)
        self.assertEqual(timeline[0]["decision"]["action"], "collaborate")
        self.assertEqual(timeline[0]["execution"]["result"]["description"], "完成一次协作")


if __name__ == "__main__":
    unittest.main()
