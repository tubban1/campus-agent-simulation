import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone

import app.main as main
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.labor.schema import LABOR_RUNTIME_SQL
from app.labor.service import (
    income_distribution_summary,
    process_labor_runtime,
    seed_labor_runtime,
)
from app.models import SCHEMA_SQL
from app.organizations.schema import ORGANIZATION_RUNTIME_SQL
from app.organizations.service import seed_organization_runtime


CAPABILITY_SQL = """
CREATE TABLE agent_capability_profiles (
    resident_id INTEGER PRIMARY KEY,
    physical_endurance INTEGER NOT NULL,
    time_management INTEGER NOT NULL,
    risk_tolerance INTEGER NOT NULL,
    rule_adherence INTEGER NOT NULL,
    information_literacy INTEGER NOT NULL,
    economic_access INTEGER NOT NULL,
    social_capital INTEGER NOT NULL,
    institutional_access INTEGER NOT NULL,
    language_access INTEGER NOT NULL,
    stress_resilience INTEGER NOT NULL
);
"""


class LaborRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        residents = (
            (1, "林小夏", "大一学生", 100),
            (4, "苏晴", "学生会干部", 100),
            (5, "周老板", "食堂商家", 100),
            (6, "李姐", "奶茶店商家", 100),
            (8, "何管理员", "图书馆管理员", 100),
            (10, "校园后勤", "学校组织", 100),
            (11, "顾南星", "大一学生", 100),
            (13, "孟雨桐", "大二学生", 100),
            (16, "陆子昂", "大三学生", 100),
            (17, "乔安然", "研究生", 100),
            (18, "韩墨", "研究生", 100),
            (19, "白露", "心理委员", 100),
            (20, "秦越", "校园创业者", 100),
        )
        for resident_id, name, role, money in residents:
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, '稳定', '测试劳动运行时', ?, '教学楼')
                """,
                (resident_id, name, role, money),
            )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        main.seed_campus_organizations(self.conn)
        self.conn.executescript(CAPABILITY_SQL)
        for resident in residents:
            score = 40 if resident[0] == 16 else 60
            self.conn.execute(
                """
                INSERT INTO agent_capability_profiles
                VALUES (?, 60, 60, 60, 60, 60, ?, 60, 60, 60, 60)
                """,
                (resident[0], score),
            )
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(ORGANIZATION_RUNTIME_SQL)
        seed_organization_runtime(self.conn)
        self.conn.executescript(LABOR_RUNTIME_SQL)
        self.start = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
        self.seed = seed_labor_runtime(self.conn, date(2026, 7, 29))

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_seed_is_idempotent_and_uses_skill_eligibility(self):
        second = seed_labor_runtime(self.conn, date(2026, 7, 29))
        aid = self.conn.execute(
            "SELECT * FROM income_programs WHERE program_key = 'need-aid-lu'"
        ).fetchone()
        self.assertEqual(self.seed["labor_positions"], 4)
        self.assertEqual(self.seed["employment_contracts"], 8)
        self.assertEqual(second["contracts_created"], 0)
        self.assertIsNotNone(aid)

    def test_shift_requires_action_evidence_and_pays_from_org_budget(self):
        process_labor_runtime(self.conn, self.start)
        contract = self.conn.execute(
            """
            SELECT contract.*, position.location
            FROM employment_contracts contract
            JOIN labor_positions position ON position.id = contract.position_id
            WHERE contract.resident_id = 4
            """
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO world_action_executions
            (resident_id, action_type, location, status, duration_minutes,
             occurred_at, completed_at)
            VALUES (4, 'collaborate', ?, 'completed', 120, ?, ?)
            """,
            (
                contract["location"], self.start.isoformat(),
                (self.start + timedelta(minutes=120)).isoformat(),
            ),
        )
        result = process_labor_runtime(
            self.conn, self.start + timedelta(days=1)
        )
        shift = self.conn.execute(
            """
            SELECT * FROM labor_shifts
            WHERE contract_id = ? AND work_date = '2026-07-29'
            """,
            (contract["id"],),
        ).fetchone()
        payment = self.conn.execute(
            "SELECT * FROM income_payments WHERE labor_shift_id = ?",
            (shift["id"],),
        ).fetchone()
        self.assertIn(shift["status"], {"completed", "partial"})
        self.assertGreater(shift["gross_pay_minor"], 0)
        self.assertEqual(payment["status"], "posted")
        self.assertTrue(result["shifts_settled"])
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_no_evidence_means_no_wage(self):
        process_labor_runtime(self.conn, self.start)
        process_labor_runtime(self.conn, self.start + timedelta(days=1))
        absent = self.conn.execute(
            "SELECT COUNT(*) value FROM labor_shifts WHERE status = 'absent'"
        ).fetchone()["value"]
        wages = self.conn.execute(
            "SELECT COUNT(*) value FROM income_payments WHERE payment_type = 'wage'"
        ).fetchone()["value"]
        self.assertGreater(absent, 0)
        self.assertEqual(wages, 0)

    def test_external_family_support_has_explicit_source(self):
        self.conn.execute(
            """
            UPDATE income_programs SET next_due_date = '2026-07-29'
            WHERE program_type = 'family_support'
            """
        )
        result = process_labor_runtime(self.conn, self.start)
        payments = self.conn.execute(
            """
            SELECT * FROM income_payments
            WHERE payment_type = 'family_support'
            """
        ).fetchall()
        self.assertEqual(len(payments), 2)
        self.assertTrue(all(row["payer_actor_key"] == "external:outside-world" for row in payments))
        self.assertTrue(all(row["status"] == "posted" for row in payments))
        self.assertTrue(result["income_payments"])
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_required_expense_has_receiver_and_respects_cash(self):
        self.conn.execute(
            """
            UPDATE expense_obligations SET next_due_date = '2026-07-29'
            WHERE resident_id = 1
            """
        )
        result = process_labor_runtime(self.conn, self.start)
        posted = [
            item for item in result["expenses"] if item["status"] == "posted"
        ]
        self.assertEqual(len(posted), 3)
        recipient = self.conn.execute(
            """
            SELECT balance_minor FROM ledger_accounts
            WHERE account_key = 'system:campus-services:cash'
            """
        ).fetchone()
        self.assertEqual(recipient["balance_minor"], 800)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_unaffordable_obligation_records_failure_once_per_day(self):
        self.conn.execute(
            """
            UPDATE expense_obligations
            SET next_due_date = '2026-07-29', amount_minor = 999999
            WHERE resident_id = 1 AND expense_type = 'housing'
            """
        )
        first = process_labor_runtime(self.conn, self.start)
        second = process_labor_runtime(
            self.conn, self.start + timedelta(minutes=10)
        )
        row = self.conn.execute(
            """
            SELECT last_attempt_date, failure_reason
            FROM expense_obligations
            WHERE resident_id = 1 AND expense_type = 'housing'
            """
        ).fetchone()
        self.assertTrue(any(item["status"] == "blocked" for item in first["expenses"]))
        self.assertEqual(second["expenses"], [])
        self.assertEqual(row["last_attempt_date"], "2026-07-29")
        self.assertIn("余额不足", row["failure_reason"])

    def test_distribution_is_measured_from_ledger_and_payments(self):
        summary = income_distribution_summary(self.conn)
        self.assertEqual(summary["population"], 13)
        self.assertGreaterEqual(summary["cash_gini"], 0)
        self.assertEqual(summary["source"], "ledger_accounts+income_payments")


if __name__ == "__main__":
    unittest.main()
