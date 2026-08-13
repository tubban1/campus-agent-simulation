import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import app.main as main
from app.budget.schema import BUDGET_RUNTIME_SQL
from app.budget.service import (
    calculate_budget_state,
    evaluate_action_choice,
    fund_emergency_action,
    process_budget_runtime,
    record_action_choice,
    seed_budget_runtime,
)
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import (
    post_money_transfer_minor,
    reconcile_ledger,
    seed_economy_foundation,
)
from app.labor.schema import LABOR_RUNTIME_SQL
from app.models import SCHEMA_SQL


class BudgetRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (1, '预算学生', '大一学生', '谨慎', '完成学业', 100, '食堂')
            """
        )
        self.conn.execute(
            """
            INSERT INTO agent_profiles
            (resident_id, gender, avatar_style, energy, mood, current_task,
             schedule, perception, strategy)
            VALUES (1, '女', '测试', 80, '平稳', '学习', '[]', '{}', '{}')
            """
        )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.conn.execute(
            """
            CREATE TABLE agent_capability_profiles (
                resident_id INTEGER PRIMARY KEY,
                risk_tolerance INTEGER NOT NULL,
                economic_access INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            "INSERT INTO agent_capability_profiles VALUES (1, 30, 50)"
        )
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(LABOR_RUNTIME_SQL)
        self.conn.execute(
            """
            INSERT INTO expense_obligations
            (obligation_key, resident_id, expense_type, recipient_actor_key,
             amount_minor, cadence_days, next_due_date, priority)
            VALUES ('housing:1', 1, 'housing', 'system:campus-services',
                    2000, 7, '2026-07-30', 90)
            """
        )
        self.conn.executescript(BUDGET_RUNTIME_SQL)
        self.seed = seed_budget_runtime(self.conn)
        self.start = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_disposable_budget_reserves_required_expenses_and_disables_credit(self):
        state = calculate_budget_state(self.conn, 1, self.start)
        profile = self.conn.execute(
            "SELECT * FROM household_budget_profiles WHERE resident_id = 1"
        ).fetchone()
        self.assertEqual(state["cash_minor"], 10000)
        self.assertEqual(state["required_expenses_minor"], 2000)
        self.assertEqual(state["disposable_minor"], 8000)
        self.assertEqual(state["borrowing_minor"], 0)
        self.assertEqual(profile["credit_enabled"], 0)
        self.assertEqual(profile["credit_limit_minor"], 0)

    def test_periodic_savings_is_idempotent_and_stays_in_ledger(self):
        first = process_budget_runtime(self.conn, self.start)
        second = process_budget_runtime(
            self.conn, self.start + timedelta(minutes=10)
        )
        savings = self.conn.execute(
            "SELECT balance_minor FROM ledger_accounts WHERE account_key = 'resident:1:savings'"
        ).fetchone()["balance_minor"]
        self.assertEqual(len(first["savings_transfers"]), 1)
        self.assertEqual(second["savings_transfers"], [])
        self.assertGreater(savings, 0)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_unaffordable_choice_is_rejected_and_releases_resources(self):
        evaluation = evaluate_action_choice(
            self.conn, resident_id=1, action_type="consume",
            location="食堂", required_money_minor=9000,
            required_time_minutes=20, world_time=self.start,
        )
        cursor = self.conn.execute(
            """
            INSERT INTO world_action_executions
            (resident_id, action_type, location, status, duration_minutes,
             occurred_at)
            VALUES (1, 'consume', '食堂', 'rejected', 20, ?)
            """,
            (self.start.isoformat(),),
        )
        record_action_choice(
            self.conn, action_execution_id=cursor.lastrowid, resident_id=1,
            action_type="consume", location="食堂", evaluation=evaluation,
            world_time=self.start,
        )
        choice = self.conn.execute(
            "SELECT * FROM choice_evaluations WHERE action_execution_id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        self.assertEqual(choice["decision"], "rejected")
        self.assertEqual(choice["released_money_minor"], 9000)
        self.assertEqual(choice["released_time_minutes"], 20)
        self.assertEqual(
            self.conn.execute(
                "SELECT balance_minor FROM ledger_accounts WHERE account_key = 'resident:1:cash'"
            ).fetchone()["balance_minor"],
            10000,
        )

    def test_time_commitment_defers_new_action(self):
        self.conn.execute(
            """
            INSERT INTO world_action_executions
            (resident_id, action_type, location, status, duration_minutes,
             occurred_at)
            VALUES (1, 'attend_class', '教学楼', 'completed', 950, ?)
            """,
            (self.start.isoformat(),),
        )
        evaluation = evaluate_action_choice(
            self.conn, resident_id=1, action_type="club_activity",
            location="操场", required_money_minor=0,
            required_time_minutes=40, world_time=self.start,
        )
        self.assertEqual(evaluation["decision"], "deferred")
        self.assertEqual(evaluation["free_time_minutes"], 10)
        self.assertEqual(evaluation["alternative_action"], "rest")

    def test_emergency_food_can_use_existing_savings_without_credit(self):
        self.conn.execute(
            "UPDATE expense_obligations SET next_due_date = '2026-09-01'"
        )
        self.conn.execute(
            "CREATE TABLE agent_body_states (resident_id INTEGER PRIMARY KEY, hunger INTEGER NOT NULL)"
        )
        self.conn.execute("INSERT INTO agent_body_states VALUES (1, 90)")
        post_money_transfer_minor(
            self.conn, transaction_key="test:fund-savings",
            from_account_key="resident:1:cash",
            to_account_key="resident:1:savings",
            amount_minor=9500, transaction_type="savings_deposit",
            source_type="test", source_id="1",
        )
        evaluation = evaluate_action_choice(
            self.conn, resident_id=1, action_type="consume",
            location="食堂", required_money_minor=800,
            required_time_minutes=20, world_time=self.start,
        )
        action = self.conn.execute(
            """
            INSERT INTO world_action_executions
            (resident_id, action_type, location, status, duration_minutes,
             occurred_at)
            VALUES (1, 'consume', '食堂', 'pending', 20, ?)
            """,
            (self.start.isoformat(),),
        )
        ledger_id = fund_emergency_action(
            self.conn, resident_id=1, amount_minor=800,
            action_execution_id=action.lastrowid, evaluation=evaluation,
            world_time=self.start,
        )
        self.assertTrue(evaluation["emergency_override"])
        self.assertIsNotNone(ledger_id)
        self.assertEqual(
            self.conn.execute(
                "SELECT balance_minor FROM ledger_accounts WHERE account_key = 'resident:1:cash'"
            ).fetchone()["balance_minor"],
            800,
        )
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])


if __name__ == "__main__":
    unittest.main()
