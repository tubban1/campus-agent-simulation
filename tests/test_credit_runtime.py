import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import app.main as main
from app.budget.schema import BUDGET_RUNTIME_SQL
from app.budget.service import (
    calculate_budget_state,
    evaluate_action_choice,
    fund_emergency_action,
    seed_budget_runtime,
)
from app.credit.schema import CREDIT_RUNTIME_SQL
from app.credit.service import (
    CREDIT_UNION_CASH,
    _refresh_savings_goals,
    accrue_contract_interest,
    available_credit,
    create_economic_shock,
    originate_credit,
    pay_credit_installment,
    process_credit_runtime,
    seed_credit_runtime,
    settle_economic_shock,
)
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import (
    post_money_transfer_minor,
    reconcile_ledger,
    seed_economy_foundation,
)
from app.labor.schema import LABOR_RUNTIME_SQL
from app.models import SCHEMA_SQL


class CreditRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        for resident_id, name, money in (
            (1, "稳健学生", 100),
            (2, "脆弱学生", 100),
        ):
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, '学生', '谨慎', '完成学业', ?, '食堂')
                """,
                (resident_id, name, money),
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
            "INSERT INTO agent_capability_profiles VALUES (1, 40, 70)"
        )
        self.conn.execute(
            "INSERT INTO agent_capability_profiles VALUES (2, 20, 35)"
        )
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(LABOR_RUNTIME_SQL)
        self.conn.executescript(BUDGET_RUNTIME_SQL)
        seed_budget_runtime(self.conn)
        self.conn.executescript(CREDIT_RUNTIME_SQL)
        self.start = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
        self.seed = seed_credit_runtime(self.conn, self.start)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def cash(self, account_key):
        return int(
            self.conn.execute(
                "SELECT balance_minor FROM ledger_accounts WHERE account_key = ?",
                (account_key,),
            ).fetchone()["balance_minor"]
        )

    def drain_resident_cash(self, resident_id, keep_minor=0):
        balance = self.cash(f"resident:{resident_id}:cash")
        amount = balance - keep_minor
        if amount > 0:
            post_money_transfer_minor(
                self.conn,
                transaction_key=f"test:drain:{resident_id}:{balance}:{keep_minor}",
                from_account_key=f"resident:{resident_id}:cash",
                to_account_key="system:campus-services:cash",
                amount_minor=amount,
                transaction_type="test_expense",
                source_type="test",
            )

    def test_seed_is_funded_idempotent_and_enables_budget_credit(self):
        second = seed_credit_runtime(self.conn, self.start)
        budget = self.conn.execute(
            "SELECT * FROM household_budget_profiles WHERE resident_id = 1"
        ).fetchone()
        self.assertEqual(self.seed["profiles"], 2)
        self.assertEqual(second["profiles_created"], 0)
        self.assertEqual(second["credit_union_funded_minor"], 0)
        self.assertGreater(self.cash(CREDIT_UNION_CASH), 0)
        self.assertEqual(budget["credit_enabled"], 1)
        self.assertGreater(budget["credit_limit_minor"], 0)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_legacy_duplicate_savings_accounts_do_not_break_goal_refresh(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE economic_actors (
                id INTEGER PRIMARY KEY,
                resident_id INTEGER
            );
            CREATE TABLE ledger_accounts (
                id INTEGER PRIMARY KEY,
                actor_id INTEGER,
                account_code TEXT,
                balance_minor INTEGER
            );
            CREATE TABLE savings_goals (
                id INTEGER PRIMARY KEY,
                resident_id INTEGER,
                target_amount_minor INTEGER,
                current_amount_minor INTEGER,
                status TEXT,
                updated_at TEXT
            );
            INSERT INTO economic_actors VALUES (1, 1);
            INSERT INTO ledger_accounts VALUES (1, 1, 'savings', 10);
            INSERT INTO ledger_accounts VALUES (2, 1, 'savings', 20);
            INSERT INTO ledger_accounts VALUES (3, 1, 'savings', 0);
            INSERT INTO savings_goals VALUES (1, 1, 15, 0, 'active', '');
            """
        )

        _refresh_savings_goals(conn)

        goal = conn.execute("SELECT * FROM savings_goals WHERE id = 1").fetchone()
        self.assertEqual(goal["current_amount_minor"], 20)
        self.assertEqual(goal["status"], "achieved")
        conn.close()

    def test_origination_records_cash_asset_and_liability_without_money_creation(self):
        lender_before = self.cash(CREDIT_UNION_CASH)
        borrower_before = self.cash("resident:1:cash")
        contract = originate_credit(
            self.conn,
            resident_id=1,
            amount_minor=2000,
            world_time=self.start,
            contract_key="test:loan:1",
        )
        credit = available_credit(self.conn, 1)
        entries = self.conn.execute(
            """
            SELECT account.account_code, entry.entry_side, entry.amount_minor
            FROM ledger_entries entry
            JOIN ledger_accounts account ON account.id = entry.account_id
            WHERE entry.transaction_id = ?
            ORDER BY entry.id
            """,
            (contract["ledger_transaction_id"],),
        ).fetchall()
        self.assertEqual(self.cash(CREDIT_UNION_CASH), lender_before - 2000)
        self.assertEqual(self.cash("resident:1:cash"), borrower_before + 2000)
        self.assertEqual(credit["outstanding_principal_minor"], 2000)
        self.assertEqual(len(entries), 4)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_interest_and_repayment_change_future_budget(self):
        contract = originate_credit(
            self.conn,
            resident_id=1,
            amount_minor=2800,
            world_time=self.start,
            contract_key="test:loan:interest",
        )
        accrual = accrue_contract_interest(
            self.conn,
            contract["id"],
            self.start + timedelta(days=7),
        )
        payment = pay_credit_installment(
            self.conn,
            contract_id=contract["id"],
            world_time=self.start + timedelta(days=7),
        )
        credit = available_credit(self.conn, 1)
        budget = calculate_budget_state(
            self.conn, 1, self.start + timedelta(days=7)
        )
        self.assertGreater(accrual["interest_minor"], 0)
        self.assertGreater(payment["principal_minor"], 0)
        self.assertGreater(payment["interest_minor"], 0)
        self.assertLess(credit["outstanding_principal_minor"], 2800)
        self.assertGreater(budget["borrowing_minor"], 0)
        self.assertTrue(budget["credit_enabled"])
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_lender_reserve_prevents_unfunded_credit(self):
        reserve = self.cash(CREDIT_UNION_CASH)
        post_money_transfer_minor(
            self.conn,
            transaction_key="test:empty-credit-reserve",
            from_account_key=CREDIT_UNION_CASH,
            to_account_key="system:campus-services:cash",
            amount_minor=reserve,
            transaction_type="reserve_reallocation",
            source_type="test",
        )
        with self.assertRaisesRegex(ValueError, "准备金不足"):
            originate_credit(
                self.conn,
                resident_id=1,
                amount_minor=1000,
                world_time=self.start,
                contract_key="test:unfunded-loan",
            )
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_default_reduces_score_limit_and_future_access(self):
        contract = originate_credit(
            self.conn,
            resident_id=2,
            amount_minor=2000,
            world_time=self.start,
            contract_key="test:default-loan",
        )
        before = available_credit(self.conn, 2)
        self.drain_resident_cash(2)
        first = self.conn.execute(
            """
            SELECT id FROM credit_installments
            WHERE contract_id = ? ORDER BY sequence_number LIMIT 1
            """,
            (contract["id"],),
        ).fetchone()
        self.conn.execute(
            "UPDATE credit_installments SET due_date = '2026-07-01' WHERE id = ?",
            (first["id"],),
        )
        result = process_credit_runtime(
            self.conn, self.start + timedelta(days=10)
        )
        after = available_credit(self.conn, 2)
        self.assertIn(contract["id"], result["defaulted"])
        self.assertEqual(after["status"], "defaulted")
        self.assertLess(after["credit_score"], before["credit_score"])
        self.assertEqual(after["available_credit_minor"], 0)

    def test_low_savings_high_debt_increases_shock_impact(self):
        contract = originate_credit(
            self.conn,
            resident_id=2,
            amount_minor=3000,
            world_time=self.start,
            contract_key="test:shock-debt",
        )
        self.assertIsNotNone(contract["id"])
        self.drain_resident_cash(1)
        self.drain_resident_cash(2)
        self.conn.execute(
            "UPDATE household_risk_profiles SET mutual_aid_enrolled = 0"
        )
        stable = create_economic_shock(
            self.conn,
            resident_id=1,
            shock_type="medical",
            severity=60,
            amount_minor=2000,
            shock_key="test:shock:stable",
            world_time=self.start,
        )
        fragile = create_economic_shock(
            self.conn,
            resident_id=2,
            shock_type="medical",
            severity=60,
            amount_minor=2000,
            shock_key="test:shock:fragile",
            world_time=self.start,
        )
        stable_result = settle_economic_shock(
            self.conn, shock_id=stable["id"], world_time=self.start
        )
        fragile_result = settle_economic_shock(
            self.conn, shock_id=fragile["id"], world_time=self.start
        )
        self.assertGreater(
            fragile_result["impact_score"], stable_result["impact_score"]
        )
        self.assertEqual(fragile_result["uncovered_minor"], 2000)

    def test_essential_consumption_can_draw_tracked_emergency_credit(self):
        self.conn.execute(
            "CREATE TABLE agent_body_states (resident_id INTEGER PRIMARY KEY, hunger INTEGER NOT NULL)"
        )
        self.conn.execute("INSERT INTO agent_body_states VALUES (1, 92)")
        self.drain_resident_cash(1)
        evaluation = evaluate_action_choice(
            self.conn,
            resident_id=1,
            action_type="consume",
            location="食堂",
            required_money_minor=800,
            required_time_minutes=20,
            world_time=self.start,
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
            self.conn,
            resident_id=1,
            amount_minor=800,
            action_execution_id=action.lastrowid,
            evaluation=evaluation,
            world_time=self.start,
        )
        credit = available_credit(self.conn, 1)
        self.assertTrue(evaluation["emergency_override"])
        self.assertIsNotNone(ledger_id)
        self.assertEqual(credit["outstanding_principal_minor"], 800)
        self.assertEqual(self.cash("resident:1:cash"), 800)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])


if __name__ == "__main__":
    unittest.main()
