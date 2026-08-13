import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app.main as main
from app.models import SCHEMA_SQL


class EnvironmentFoundationTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute("INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')")
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (1, '环境测试学生', '学生', '认真', '完成环境实验', 100, '宿舍区')
            """
        )
        self.conn.execute(
            """
            INSERT INTO agent_profiles
            (resident_id, gender, avatar_style, energy, mood, current_task,
             skills, strategy, schedule, perception)
            VALUES (1, '女', '测试', 80, '平稳', '观察环境', '{}', '{}', '[]', '{}')
            """
        )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_agent_news_system(self.conn, allow_ddl=True)
        main.ensure_external_information_system(self.conn, allow_ddl=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_default_config_is_versioned_and_bound_to_runtime(self):
        config = main.get_active_environment_config(self.conn)
        runtime = dict(
            self.conn.execute(
                "SELECT * FROM world_runtime WHERE id = ?",
                (main.WORLD_RUNTIME_ID,),
            ).fetchone()
        )

        self.assertEqual(config["config_key"], "campus-default")
        self.assertEqual(config["version"], 1)
        self.assertEqual(config["checksum"], main.content_checksum(config["config"]))
        self.assertEqual(runtime["environment_config_id"], config["id"])
        self.assertEqual(runtime["environment_version"], config["version_label"])
        self.assertTrue(runtime["random_seed"])

    def test_default_multiscale_update_schedules_are_seeded(self):
        schedules = self.conn.execute(
            """
            SELECT update_key, cadence, interval_seconds, status
            FROM world_update_schedules
            ORDER BY interval_seconds
            """
        ).fetchall()

        self.assertEqual(
            [row["update_key"] for row in schedules],
            [
                "campus_space_activity",
                "social_dynamics",
                "institutional_resource_review",
            ],
        )
        self.assertEqual([row["interval_seconds"] for row in schedules], [3600, 28800, 86400])
        self.assertTrue(all(row["status"] == "active" for row in schedules))

    def test_multiscale_updates_follow_cadence_and_event_lineage(self):
        world_time = datetime.fromisoformat("2026-07-29T12:00:00+08:00")
        tick = self.conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status)
            VALUES (1, ?, 1, '08:00-16:00', 'test', 'running')
            """,
            (world_time.isoformat(),),
        )
        root_event = main.append_world_event(
            self.conn,
            "world_tick_started",
            "测试 tick",
            "开始多尺度更新测试",
            tick_id=tick.lastrowid,
        )
        main.append_world_event(
            self.conn,
            "agent_tick",
            "测试学生完成协作",
            "测试学生在图书馆与同学协作。",
            tick_id=tick.lastrowid,
            resident_id=1,
            location="图书馆",
            payload={
                "action": "collaborate",
                "social_effect": {
                    "target_id": 2,
                    "effect": "positive",
                    "commitment": {"id": 1},
                },
            },
            parent_event_id=root_event["id"],
        )

        first = main.run_due_world_updates(
            self.conn,
            world_time,
            tick.lastrowid,
            1,
            "08:00-16:00",
            parent_event_id=root_event["id"],
        )
        immediate_retry = main.run_due_world_updates(
            self.conn,
            world_time + timedelta(minutes=1),
            tick.lastrowid,
            1,
            "08:00-16:00",
            parent_event_id=root_event["id"],
        )
        hourly = main.run_due_world_updates(
            self.conn,
            world_time + timedelta(hours=1),
            tick.lastrowid,
            1,
            "08:00-16:00",
            parent_event_id=root_event["id"],
        )

        self.assertEqual(first["due_count"], 3)
        self.assertEqual(len(first["completed"]), 3)
        self.assertFalse(first["failed"])
        self.assertEqual(immediate_retry["due_count"], 0)
        self.assertEqual(hourly["due_count"], 1)
        self.assertEqual(hourly["completed"][0]["update_key"], "campus_space_activity")

        social_run = next(
            item for item in first["completed"] if item["update_key"] == "social_dynamics"
        )
        self.assertEqual(social_run["metrics"]["interaction_count"], 1)
        self.assertEqual(social_run["metrics"]["commitment_count"], 1)
        output_event = self.conn.execute(
            "SELECT * FROM world_event_stream WHERE id = ?",
            (social_run["output_event_id"],),
        ).fetchone()
        self.assertEqual(output_event["parent_event_id"], root_event["id"])
        self.assertEqual(output_event["root_event_id"], root_event["id"])
        self.assertEqual(output_event["source_type"], "world_update_run")

    def test_world_tick_invokes_due_multiscale_updates(self):
        world_time = datetime.fromisoformat("2026-07-29T12:00:00+08:00")
        empty_plan_result = {
            "created": 0,
            "llm_plans": 0,
            "rule_based_plans": 0,
            "backfilled_plans": 0,
            "goals_revised": 0,
        }
        with patch.dict("os.environ", {"WORLD_RUNTIME_EXTENDED_SUBSYSTEMS_ENABLED": "true"}), patch.object(main, "get_connection", return_value=self.conn), patch.object(
            main, "get_world_now", return_value=world_time
        ), patch.object(
            main, "ensure_current_action_plans", return_value=empty_plan_result
        ), patch.object(
            main, "maybe_auto_sync_real_weather", return_value={"skipped": True}
        ), patch.object(
            main, "maybe_auto_sync_external_information", return_value={"skipped": True}
        ), patch.object(
            main, "select_world_tick_agents", return_value=([], 0, set())
        ), patch.object(
            main, "maybe_generate_group_behavior_event", return_value={"skipped": True}
        ), patch.object(
            main, "maybe_publish_campus_news_from_world_window", return_value={"skipped": True}
        ):
            tick = main.advance_world_tick(reason="test")

        self.assertEqual(tick["processed_agents"], 0)
        self.assertEqual(tick["multiscale_updates"]["due_count"], 3)
        self.assertEqual(len(tick["multiscale_updates"]["completed"]), 3)
        stored_tick = self.conn.execute(
            "SELECT status FROM world_ticks WHERE id = ?", (tick["tick_id"],)
        ).fetchone()
        self.assertEqual(stored_tick["status"], "complete")

    def test_world_tick_failure_persists_failed_status(self):
        world_time = datetime.fromisoformat("2026-08-03T06:00:00+08:00")
        with patch.dict("os.environ", {"WORLD_RUNTIME_EXTENDED_SUBSYSTEMS_ENABLED": "true"}), patch.object(main, "get_connection", return_value=self.conn), patch.object(
            main, "get_world_now", return_value=world_time
        ), patch.object(
            main,
            "process_population_runtime",
            side_effect=RuntimeError("population exploded"),
        ):
            with self.assertRaisesRegex(RuntimeError, "population exploded"):
                main.advance_world_tick(reason="test-failure")

        tick = self.conn.execute(
            "SELECT status, error_message, completed_at FROM world_ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        event = self.conn.execute(
            "SELECT tick_id, event_type FROM world_event_stream ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(tick["status"], "failed")
        self.assertIn("RuntimeError: population exploded", tick["error_message"])
        self.assertTrue(tick["completed_at"])
        self.assertEqual(event["event_type"], "world_tick_failed")

    def test_new_config_version_can_be_activated_and_applied(self):
        config = json.loads(json.dumps(main.default_environment_config(), ensure_ascii=False))
        config["spaces"][0]["capacity"] = 321
        config["environment_baseline"]["resource_pressure"] = 72
        created = main.create_environment_config_record(
            self.conn,
            "campus-default",
            "资源紧张校园",
            config,
            parent_config_id=main.get_active_environment_config(self.conn)["id"],
        )
        row = self.conn.execute(
            "SELECT * FROM environment_configs WHERE id = ?",
            (created["id"],),
        ).fetchone()

        applied = main.apply_environment_config(self.conn, dict(row))
        active = main.get_active_environment_config(self.conn)
        dorm = self.conn.execute(
            "SELECT capacity FROM campus_spaces WHERE code = 'dorm'"
        ).fetchone()
        environment = main.get_campus_environment(self.conn, 1)

        self.assertEqual(created["version"], 2)
        self.assertEqual(active["id"], created["id"])
        self.assertEqual(dorm["capacity"], 321)
        self.assertEqual(environment["resource_pressure"], 72)
        self.assertEqual(applied["spaces"], len(config["spaces"]))
        self.assertIn("resource_pressure", applied["baseline_fields"])

    def test_world_events_preserve_parent_and_root_lineage(self):
        root = main.append_world_event(
            self.conn,
            "test_root",
            "根事件",
            "环境改变开始",
            source_type="test",
            source_id="root-1",
            branch_key="experiment-a",
        )
        child = main.append_world_event(
            self.conn,
            "test_child",
            "子事件",
            "Agent 对环境作出反应",
            parent_event_id=root["id"],
            source_type="agent_action",
            source_id="action-1",
            rule_version="test-rule-v1",
        )
        grandchild = main.append_world_event(
            self.conn,
            "test_grandchild",
            "后续事件",
            "反应产生后续影响",
            parent_event_id=child["id"],
        )

        self.assertEqual(root["root_event_id"], root["id"])
        self.assertEqual(child["parent_event_id"], root["id"])
        self.assertEqual(child["root_event_id"], root["id"])
        self.assertEqual(grandchild["root_event_id"], root["id"])
        self.assertEqual(child["source_type"], "agent_action")
        self.assertEqual(child["rule_version"], "test-rule-v1")
        self.assertEqual(child["branch_key"], "experiment-a")
        self.assertEqual(grandchild["branch_key"], "experiment-a")

    def test_snapshot_contains_objective_state_and_replay_metadata(self):
        event = main.append_world_event(
            self.conn,
            "snapshot_test",
            "快照前事件",
            "用于确定事件游标",
        )
        snapshot = main.create_world_snapshot_record(
            self.conn,
            reason="阶段 0 测试",
            run_id="run-test-1",
            branch_key="control",
            external_data_version="external-snapshot-1",
            metadata={"experiment": "foundation"},
        )
        stored = self.conn.execute(
            "SELECT * FROM world_snapshots WHERE id = ?",
            (snapshot["id"],),
        ).fetchone()
        decoded = main.decode_world_snapshot(stored, include_state=True)

        self.assertEqual(snapshot["event_cursor"], event["id"])
        self.assertEqual(snapshot["branch_key"], "control")
        self.assertEqual(snapshot["external_data_version"], "external-snapshot-1")
        self.assertTrue(snapshot["environment_version"])
        self.assertTrue(snapshot["random_seed"])
        self.assertEqual(snapshot["checksum"], main.content_checksum(stored["state_json"]))
        self.assertEqual(decoded["metadata"]["experiment"], "foundation")
        self.assertEqual(decoded["state"]["residents"][0]["name"], "环境测试学生")
        self.assertIn("campus_spaces", decoded["state"])
        self.assertIn("agent_goals", decoded["state"])
        self.assertIn("memories", decoded["state"])
        self.assertEqual(snapshot["schema_version"], "world-snapshot-v3")

    def test_snapshot_restore_rewinds_state_without_deleting_audit_events(self):
        snapshot = main.create_world_snapshot_record(
            self.conn,
            reason="恢复测试基线",
            branch_key="main",
        )
        future_event = main.append_world_event(
            self.conn,
            "future_test_event",
            "未来事件",
            "该事件在状态恢复后仍应保留为审计记录。",
        )
        self.conn.execute(
            "UPDATE residents SET money = 7, location = '商业街' WHERE id = 1"
        )
        self.conn.execute(
            "UPDATE agent_profiles SET energy = 12, mood = '疲惫' WHERE resident_id = 1"
        )
        main.add_memory(
            self.conn,
            1,
            1,
            "这是一条快照之后才出现的记忆。",
            importance=8,
        )

        restored = main.restore_world_snapshot_state(
            self.conn,
            snapshot["id"],
            active_branch_key="main",
        )

        resident = self.conn.execute(
            "SELECT money, location FROM residents WHERE id = 1"
        ).fetchone()
        profile = self.conn.execute(
            "SELECT energy, mood FROM agent_profiles WHERE resident_id = 1"
        ).fetchone()
        memory_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM memories WHERE resident_id = 1"
        ).fetchone()["count"]
        audit_event = self.conn.execute(
            "SELECT id FROM world_event_stream WHERE id = ?",
            (future_event["id"],),
        ).fetchone()

        self.assertEqual(restored["snapshot_id"], snapshot["id"])
        self.assertEqual((resident["money"], resident["location"]), (100, "宿舍区"))
        self.assertEqual((profile["energy"], profile["mood"]), (80, "平稳"))
        self.assertEqual(memory_count, 0)
        self.assertIsNotNone(audit_event)

    def test_branch_heads_keep_independent_mutable_state(self):
        baseline = main.create_world_snapshot_record(
            self.conn,
            reason="分支共同基线",
            branch_key="main",
        )
        branch = main.create_world_branch_record(
            self.conn,
            "treatment-a",
            "处理组 A",
            baseline["id"],
            metadata={"condition": "treatment"},
        )
        self.assertNotEqual(branch["head_snapshot_id"], baseline["id"])

        self.conn.execute("UPDATE residents SET money = 25 WHERE id = 1")
        main_checkpoint = main.create_world_snapshot_record(
            self.conn,
            reason="主分支变化",
            branch_key="main",
            parent_snapshot_id=baseline["id"],
        )
        main.restore_world_snapshot_state(
            self.conn,
            branch["head_snapshot_id"],
            active_branch_key="treatment-a",
            active_run_id=branch["run_id"],
        )
        branch_money = self.conn.execute(
            "SELECT money FROM residents WHERE id = 1"
        ).fetchone()["money"]
        self.assertEqual(branch_money, 100)

        self.conn.execute("UPDATE residents SET money = 70 WHERE id = 1")
        branch_checkpoint = main.create_world_snapshot_record(
            self.conn,
            reason="处理组变化",
            branch_key="treatment-a",
            parent_snapshot_id=branch["head_snapshot_id"],
        )
        main.restore_world_snapshot_state(
            self.conn,
            main_checkpoint["id"],
            active_branch_key="main",
        )
        restored_main_money = self.conn.execute(
            "SELECT money FROM residents WHERE id = 1"
        ).fetchone()["money"]
        self.assertEqual(restored_main_money, 25)

        main.restore_world_snapshot_state(
            self.conn,
            branch_checkpoint["id"],
            active_branch_key="treatment-a",
            active_run_id=branch["run_id"],
        )
        restored_branch_money = self.conn.execute(
            "SELECT money FROM residents WHERE id = 1"
        ).fetchone()["money"]
        self.assertEqual(restored_branch_money, 70)

    def test_admin_branch_switch_checkpoints_outgoing_world(self):
        baseline = main.create_world_snapshot_record(
            self.conn,
            reason="切换测试基线",
            branch_key="main",
        )
        branch = main.create_world_branch_record(
            self.conn,
            "switch-target",
            "切换目标",
            baseline["id"],
        )
        self.conn.execute("UPDATE residents SET money = 33 WHERE id = 1")

        with patch.object(main, "require_admin_token"), patch.object(
            main, "get_connection", return_value=self.conn
        ):
            result = main.switch_world_branch_api(
                "switch-target",
                main.WorldBranchSwitchRequest(reason="测试隔离切换"),
                authorization=None,
            )

        runtime = self.conn.execute(
            "SELECT active_branch_key, status FROM world_runtime WHERE id = ?",
            (main.WORLD_RUNTIME_ID,),
        ).fetchone()
        money = self.conn.execute(
            "SELECT money FROM residents WHERE id = 1"
        ).fetchone()["money"]
        event = self.conn.execute(
            "SELECT branch_key FROM world_event_stream WHERE id = ?",
            (result["event"]["id"],),
        ).fetchone()
        main_branch = self.conn.execute(
            "SELECT head_snapshot_id, status FROM world_branches WHERE branch_key = 'main'"
        ).fetchone()

        self.assertTrue(result["switched"])
        self.assertEqual(runtime["active_branch_key"], "switch-target")
        self.assertEqual(runtime["status"], "paused")
        self.assertEqual(money, 100)
        self.assertEqual(event["branch_key"], "switch-target")
        self.assertEqual(main_branch["head_snapshot_id"], result["outgoing_snapshot"]["id"])
        self.assertEqual(main_branch["status"], "ready")
        self.assertEqual(branch["status"], "ready")


class EnvironmentFoundationMigrationTest(unittest.TestCase):
    def tearDown(self):
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_existing_runtime_tables_receive_foundation_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.execute("INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')")
        conn.executescript(
            """
            CREATE TABLE world_runtime (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'paused',
                world_timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                world_time TEXT NOT NULL DEFAULT '',
                tick_interval_seconds INTEGER NOT NULL DEFAULT 60,
                agents_per_tick INTEGER NOT NULL DEFAULT 3,
                daily_auto_model_budget INTEGER NOT NULL DEFAULT 500,
                auto_model_calls_used INTEGER NOT NULL DEFAULT 0,
                budget_date TEXT NOT NULL DEFAULT '',
                current_agent_cursor INTEGER NOT NULL DEFAULT 0,
                last_tick_started_at TEXT NOT NULL DEFAULT '',
                last_tick_completed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE world_event_stream (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tick_id INTEGER,
                day INTEGER NOT NULL,
                slot TEXT NOT NULL,
                event_type TEXT NOT NULL,
                resident_id INTEGER,
                location TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE world_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL DEFAULT '',
                snapshot_type TEXT NOT NULL DEFAULT 'manual_checkpoint',
                world_time TEXT NOT NULL DEFAULT '',
                day INTEGER NOT NULL DEFAULT 0,
                tick_id INTEGER,
                reason TEXT NOT NULL DEFAULT '',
                state_json TEXT NOT NULL DEFAULT '{}',
                schema_version TEXT NOT NULL DEFAULT 'research-v1',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

        main.ensure_world_runtime_tables(conn, allow_ddl=True)

        runtime_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(world_runtime)")
        }
        event_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(world_event_stream)")
        }
        snapshot_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(world_snapshots)")
        }
        self.assertIn("environment_config_id", runtime_columns)
        self.assertIn("active_branch_key", runtime_columns)
        self.assertIn("parent_event_id", event_columns)
        self.assertIn("branch_key", event_columns)
        self.assertIn("event_cursor", snapshot_columns)
        self.assertIn("checksum", snapshot_columns)
        branch = conn.execute(
            "SELECT branch_key, status FROM world_branches WHERE branch_key = 'main'"
        ).fetchone()
        self.assertEqual((branch["branch_key"], branch["status"]), ("main", "active"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
