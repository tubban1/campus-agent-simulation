import unittest
from datetime import datetime
from unittest.mock import patch

import psycopg.errors

from app.db import PostgresConnection, db_savepoint
from services import newspaper


class FakeCursor:
    rowcount = 0

    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row is not None else []


class FakeRawConnection:
    """Simulates a psycopg transaction that aborts on a failed statement.

    Once aborted, every later statement raises InFailedSqlTransaction until a
    ``ROLLBACK TO SAVEPOINT`` clears the aborted state, mirroring PostgreSQL.
    """

    def __init__(self):
        self.aborted = False
        self.statements = []

    def execute(self, statement, params=()):
        self.statements.append((statement, params))
        if statement.upper().startswith("ROLLBACK TO SAVEPOINT"):
            self.aborted = False
            return FakeCursor()
        if self.aborted:
            raise psycopg.errors.InFailedSqlTransaction(
                "current transaction is aborted"
            )
        if statement.upper().startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
            return FakeCursor()
        if "BROKEN" in statement:
            self.aborted = True
            raise RuntimeError("boom")
        return FakeCursor()


class TickTransactionIsolationTest(unittest.TestCase):
    def test_db_savepoint_restores_an_aborted_transaction(self):
        conn = PostgresConnection(FakeRawConnection())

        with self.assertRaises(RuntimeError):
            with db_savepoint(conn, "sp"):
                conn.execute("SELECT BROKEN")

        # The rollback-to-savepoint must have cleared the aborted state, so
        # the surrounding transaction is usable again.
        conn.execute("SELECT 1")

    def test_weather_sync_failure_no_longer_poisons_the_tick_transaction(self):
        raw = FakeRawConnection()
        conn = PostgresConnection(raw)
        failure_events = []

        def fake_ensure(c, **kwargs):
            return None

        def fake_append(c, *args, **kwargs):
            failure_events.append(args)
            c.execute("SELECT 1")  # must succeed on the recovered transaction
            return {"id": 1}

        def explode(c, **kwargs):
            c.execute("SELECT BROKEN")  # aborts the transaction
            raise RuntimeError("boom")

        class _NoopLogger:
            def warning(self, *a, **k):
                pass

        with patch.object(
            newspaper, "ensure_world_runtime_tables", side_effect=fake_ensure, create=True
        ), patch.object(
            newspaper, "append_world_event", side_effect=fake_append, create=True
        ), patch.object(
            newspaper, "sync_real_weather_into_world", side_effect=explode
        ), patch.object(
            newspaper, "logger", new=_NoopLogger(), create=True
        ):
            result = newspaper.maybe_auto_sync_real_weather(
                conn,
                datetime(2026, 8, 14, 4, 49),
                tick_id=1,
                day=41,
                slot="00:00-08:00",
            )

        self.assertTrue(result["failed"])
        self.assertIn("boom", result["error"])
        self.assertTrue(failure_events)
        # The tick transaction must still be usable for later subsystems.
        conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()


class TickPhysicalStateIsolationTest(unittest.TestCase):
    def test_refresh_failure_no_longer_poisons_tick_transaction(self):
        import app.world_runtime.orchestrator as orchestrator

        class FakeWithWorlds(FakeRawConnection):
            def execute(self, statement, params=()):
                self.statements.append((statement, params))
                if statement.upper().startswith("ROLLBACK TO SAVEPOINT"):
                    self.aborted = False
                    return FakeCursor()
                if self.aborted:
                    raise psycopg.errors.InFailedSqlTransaction(
                        "current transaction is aborted"
                    )
                if statement.upper().startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
                    return FakeCursor()
                if "BROKEN" in statement:
                    self.aborted = True
                    raise RuntimeError("boom")
                if "SELECT DISTINCT world_key FROM spatial_nodes" in statement:
                    return FakeCursor(row={"world_key": "tsinghua_main"})
                return FakeCursor()

        raw = FakeWithWorlds()
        conn = PostgresConnection(raw)

        def boom(c, **kwargs):
            c.execute("SELECT BROKEN")
            raise RuntimeError("boom")

        with patch(
            "app.spatial.physical_state_service.refresh_spatial_physical_states",
            side_effect=boom,
        ):
            orchestrator._refresh_physical_states_best_effort(
                conn,
                world_time=datetime(2026, 8, 14, 4, 49),
                environment={},
                movement_results=[{"current_node_id": 1}],
                facility_updates={"events": []},
            )

        # The failed refresh must have rolled back to its savepoint, leaving
        # the tick transaction usable for the subsystems that follow.
        conn.execute("SELECT 1")


class TickDreamIsolationTest(unittest.TestCase):
    def test_dream_failure_no_longer_poisons_tick_transaction(self):
        from app.world_runtime.dream_runtime import process_night_dreams

        resident_row = {
            "id": 1, "name": "林小夏", "role": "大学生", "personality": "安静",
            "goal": "完成课程项目", "location": "清华大学双清公寓南楼",
            "stress": 48, "fatigue": 70, "sleep_debt": 32,
        }

        class FakeWithResidents(FakeRawConnection):
            def execute(self, statement, params=()):
                self.statements.append((statement, params))
                if statement.upper().startswith("ROLLBACK TO SAVEPOINT"):
                    self.aborted = False
                    return FakeCursor()
                if self.aborted:
                    raise psycopg.errors.InFailedSqlTransaction(
                        "current transaction is aborted"
                    )
                if statement.upper().startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
                    return FakeCursor()
                if "BROKEN" in statement:
                    self.aborted = True
                    raise RuntimeError("boom")
                if "FROM residents" in statement:
                    return FakeCursor(row=resident_row)
                return FakeCursor()

        raw = FakeWithResidents()
        conn = PostgresConnection(raw)

        def broken_budget(c, *args, **kwargs):
            c.execute("SELECT BROKEN")  # aborts the transaction
            raise RuntimeError("boom")

        def fake_add_memory(c, *args, **kwargs):
            # Must succeed on the recovered transaction; without the dream
            # savepoint this would raise InFailedSqlTransaction.
            c.execute("INSERT INTO memories (resident_id) VALUES (1)")

        with patch("app.world_runtime.dream_runtime.random.random", return_value=0.0):
            result = process_night_dreams(
                conn,
                datetime(2026, 8, 14, 3, 0),
                day=14,
                add_memory=fake_add_memory,
                consume_auto_model_budget=broken_budget,
                ask_llm=lambda _prompt, **_kw: "不应调用",
                is_llm_configured=lambda: True,
                log_model_call=lambda *_args, **_kwargs: None,
            )

        # A dream fragment is still recorded via the deterministic fallback.
        self.assertEqual(len(result["recorded"]), 1)
        # The tick transaction must still be usable for later subsystems.
        conn.execute("SELECT 1")
