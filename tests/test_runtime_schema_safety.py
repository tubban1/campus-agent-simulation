import sqlite3
import unittest
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import app.main as main
from app.models import SCHEMA_SQL


class RuntimeSchemaSafetyTest(unittest.TestCase):
    def setUp(self):
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.WORLD_RUNNER_THREAD = None

    def tearDown(self):
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.WORLD_RUNNER_THREAD = None

    def _prepared_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')"
        )
        main.ensure_campus_state_table(conn, allow_ddl=True)
        main.ensure_space_system(conn, allow_ddl=True)
        main.ensure_agent_news_system(conn, allow_ddl=True)
        main.ensure_external_information_system(conn, allow_ddl=True)
        main.ensure_world_runtime_tables(conn, allow_ddl=True)
        conn.commit()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        return conn

    def test_runtime_schema_check_never_executes_ddl(self):
        conn = self._prepared_connection()
        statements = []
        conn.set_trace_callback(statements.append)

        main.ensure_campus_state_table(conn)
        main.ensure_space_system(conn)
        main.ensure_agent_news_system(conn)
        main.ensure_external_information_system(conn)
        main.ensure_world_runtime_tables(conn)

        ddl = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(("ALTER ", "CREATE ", "DROP "))
        ]
        self.assertEqual(ddl, [])
        conn.close()

    def test_runtime_requires_build_migration_instead_of_creating_tables(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        statements = []
        conn.set_trace_callback(statements.append)

        with self.assertRaises(main.SchemaMigrationRequired):
            main.ensure_world_runtime_tables(conn)

        ddl = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(("ALTER ", "CREATE ", "DROP "))
        ]
        self.assertEqual(ddl, [])
        conn.close()

    def test_startup_only_starts_runner_thread(self):
        runner = Mock()
        runner.is_alive.return_value = False

        with (
            patch.dict("os.environ", {"WORLD_RUNNER_ENABLED": "true"}),
            patch.object(main, "Thread", return_value=runner) as thread_factory,
            patch.object(
                main,
                "get_connection",
                side_effect=AssertionError("startup must not access the database"),
            ),
        ):
            main.start_world_runner_thread()

        thread_factory.assert_called_once_with(
            target=main.world_runner_loop,
            daemon=True,
        )
        runner.start.assert_called_once_with()

    def test_startup_can_disable_runner_for_read_only_instance(self):
        with (
            patch.dict("os.environ", {"WORLD_RUNNER_ENABLED": "false"}),
            patch.object(main, "Thread") as thread_factory,
            patch.object(
                main,
                "get_connection",
                side_effect=AssertionError("disabled runner must not access the database"),
            ),
        ):
            main.start_world_runner_thread()

        thread_factory.assert_not_called()
        self.assertIsNone(main.WORLD_RUNNER_THREAD)

    def test_only_world_tick_is_a_runtime_write_entrypoint(self):
        paths = set(main.app.openapi()["paths"].keys())
        self.assertIn("/api/admin/world/tick", paths)
        self.assertFalse(any(path.startswith("/api/simulate") for path in paths))
        self.assertFalse(hasattr(main, "run_lifecycle_step"))

    def test_read_world_runtime_does_not_write(self):
        conn = self._prepared_connection()
        statements = []
        conn.set_trace_callback(statements.append)

        runtime = main.read_world_runtime(conn)

        writes = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE "))
        ]
        self.assertEqual(runtime["id"], main.WORLD_RUNTIME_ID)
        self.assertEqual(writes, [])
        conn.close()

    def test_world_events_endpoint_does_not_write(self):
        conn = self._prepared_connection()
        statements = []
        conn.set_trace_callback(statements.append)

        with patch.object(main, "get_connection", return_value=nullcontext(conn)):
            response = main.get_world_events()

        writes = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE "))
        ]
        self.assertEqual(response["branch_key"], "main")
        self.assertEqual(writes, [])
        conn.close()

    def test_observer_session_does_not_update_world_runtime(self):
        conn = self._prepared_connection()
        statements = []
        conn.set_trace_callback(statements.append)
        payload = main.ObserverSessionRequest(
            user_id="local-observer",
            session_type="observer",
        )

        with patch.object(main, "get_connection", return_value=nullcontext(conn)):
            response = main.upsert_observer_session(payload)

        runtime_updates = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("UPDATE WORLD_RUNTIME")
        ]
        self.assertEqual(response["session"]["user_id"], "local-observer")
        self.assertEqual(runtime_updates, [])
        conn.close()

    def test_world_action_rule_seed_is_idempotent_without_unique_constraint(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE world_action_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_key TEXT NOT NULL,
                action_type TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                preconditions_json TEXT NOT NULL,
                required_resources_json TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                success_probability REAL NOT NULL,
                direct_effects_json TEXT NOT NULL,
                delayed_effects_json TEXT NOT NULL,
                failure_policy_json TEXT NOT NULL
            )
            """
        )

        main.seed_world_action_rules(conn)
        main.seed_world_action_rules(conn)

        count = conn.execute("SELECT COUNT(*) AS total FROM world_action_rules").fetchone()
        self.assertEqual(count["total"], len(main.DEFAULT_WORLD_ACTION_RULES))
        conn.close()

    def test_runner_auto_starts_default_paused_runtime(self):
        conn = self._prepared_connection()
        runtime = main.get_world_runtime(conn)

        resumed = main.ensure_world_runtime_running_unless_manually_paused(conn, runtime)

        self.assertEqual(resumed["status"], "running")
        event = conn.execute(
            "SELECT event_type FROM world_event_stream ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(event["event_type"], "world_runtime_auto_start")
        conn.close()

    def test_runner_respects_manual_pause_marker(self):
        conn = self._prepared_connection()
        main.set_simulation_state_value(conn, "world_runtime_manual_pause", "true")
        conn.commit()
        runtime = main.get_world_runtime(conn)

        resumed = main.ensure_world_runtime_running_unless_manually_paused(conn, runtime)

        self.assertEqual(resumed["status"], "paused")
        event = conn.execute(
            "SELECT event_type FROM world_event_stream ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIsNone(event)
        conn.close()

    def test_reconcile_stale_world_tick_marks_it_failed(self):
        conn = self._prepared_connection()
        now = datetime.fromisoformat("2026-08-03T06:00:00+08:00")
        cursor = conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status, started_at)
            VALUES (1, ?, 1, '00:00-08:00', 'test', 'running', ?)
            """,
            (now.isoformat(), (now - timedelta(hours=1)).isoformat()),
        )
        conn.execute(
            "UPDATE world_runtime SET last_tick_started_at = ? WHERE id = ?",
            ((now - timedelta(hours=1)).isoformat(), main.WORLD_RUNTIME_ID),
        )

        with patch.dict("os.environ", {"WORLD_STALE_TICK_SECONDS": "1800"}):
            recovered = main.reconcile_stale_world_ticks(conn, now=now)

        tick = conn.execute(
            "SELECT status, error_message, completed_at FROM world_ticks WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        runtime = main.read_world_runtime(conn)
        self.assertEqual(recovered, [cursor.lastrowid])
        self.assertEqual(tick["status"], "failed")
        self.assertIn("stale", tick["error_message"])
        self.assertTrue(tick["completed_at"])
        self.assertEqual(runtime["last_tick_started_at"], "")
        conn.close()

    def test_reconcile_recovers_running_tick_with_unparseable_started_at(self):
        """A running tick with a corrupt/missing timestamp must not wedge the
        world; it self-heals by falling back to the runtime wall-clock marker."""
        conn = self._prepared_connection()
        now = datetime.fromisoformat("2026-08-03T06:00:00+08:00")
        cursor = conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status, started_at)
            VALUES (1, ?, 1, '00:00-08:00', 'test', 'running', ?)
            """,
            (now.isoformat(), "not-a-real-timestamp"),
        )
        conn.execute(
            "UPDATE world_runtime SET last_tick_started_at = ? WHERE id = ?",
            ((now - timedelta(hours=1)).isoformat(), main.WORLD_RUNTIME_ID),
        )

        with patch.dict("os.environ", {"WORLD_STALE_TICK_SECONDS": "1800"}):
            recovered = main.reconcile_stale_world_ticks(conn, now=now)

        tick = conn.execute(
            "SELECT status FROM world_ticks WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        self.assertEqual(recovered, [cursor.lastrowid])
        self.assertEqual(tick["status"], "failed")
        conn.close()

    def test_record_world_tick_failure_updates_tick_and_event(self):
        conn = self._prepared_connection()
        cursor = conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status)
            VALUES (1, '2026-08-03T06:00:00+08:00', 1, '00:00-08:00', 'test', 'running')
            """
        )
        conn.commit()

        with patch.object(main, "get_connection", return_value=nullcontext(conn)):
            main.record_world_tick_failure(cursor.lastrowid, "test", ValueError("boom"))

        tick = conn.execute(
            "SELECT status, error_message, completed_at FROM world_ticks WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        event = conn.execute(
            "SELECT tick_id, event_type FROM world_event_stream ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(tick["status"], "failed")
        self.assertIn("ValueError: boom", tick["error_message"])
        self.assertTrue(tick["completed_at"])
        self.assertEqual(event["tick_id"], cursor.lastrowid)
        self.assertEqual(event["event_type"], "world_tick_failed")
        conn.close()

    def test_database_lease_prevents_second_tick_runner(self):
        @contextmanager
        def unavailable_lease():
            yield False

        with (
            patch.object(main, "world_tick_database_lease", unavailable_lease),
            patch.object(
                main,
                "_advance_world_tick_locked",
                side_effect=AssertionError("tick must not start without the lease"),
            ),
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.advance_world_tick(reason="test")

        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
