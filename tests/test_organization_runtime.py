import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import app.main as main
from app.capability_runtime import get_opportunity_access
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import reconcile_ledger, seed_economy_foundation
from app.models import SCHEMA_SQL
from app.organizations.schema import ORGANIZATION_RUNTIME_SQL
from app.organizations.service import (
    cast_organization_vote,
    create_organization_commitment,
    execute_organization_proposal,
    finalize_organization_proposal,
    organization_budget_state,
    process_organization_runtime,
    record_organization_relationship_evidence,
    seed_organization_runtime,
    submit_organization_proposal,
)


class OrganizationRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        residents = [
            (1, "林小夏", "大一学生", 100),
            (4, "苏晴", "学生会干部", 100),
            (5, "周老板", "食堂商家", 80),
            (6, "李姐", "奶茶店商家", 80),
            (7, "王老师", "辅导员", 100),
            (8, "何管理员", "图书馆管理员", 100),
            (10, "校园后勤", "学校组织", 100),
            (17, "乔安然", "研究生", 100),
            (18, "韩墨", "研究生", 100),
            (19, "白露", "心理委员", 100),
            (20, "秦越", "校园创业者", 100),
        ]
        for resident_id, name, role, money in residents:
            self.conn.execute(
                """
                INSERT INTO residents
                (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, '稳定', '参与校园生活', ?, '教学楼')
                """,
                (resident_id, name, role, money),
            )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        main.seed_campus_organizations(self.conn)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.conn.executescript(ORGANIZATION_RUNTIME_SQL)
        self.seed = seed_organization_runtime(self.conn)
        self.student_union = self.conn.execute(
            "SELECT id FROM campus_organizations WHERE name = '学生会'"
        ).fetchone()["id"]
        self.start = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def _proposal(self, **overrides):
        values = {
            "proposal_key": "test:student-union:service",
            "organization_id": self.student_union,
            "proposer_resident_id": 4,
            "proposal_type": "service_purchase",
            "title": "采购学生服务",
            "description": "为学生服务活动采购物资",
            "requested_budget_minor": 1000,
            "target_actor_key": "resident:1",
            "world_time": self.start,
        }
        values.update(overrides)
        return submit_organization_proposal(self.conn, **values)

    def test_seed_creates_profiles_roles_memberships_and_relationships_idempotently(self):
        second = seed_organization_runtime(self.conn)
        members = self.conn.execute(
            """
            SELECT resident_id, member_role FROM organization_members
            WHERE organization_id = ? ORDER BY resident_id
            """,
            (self.student_union,),
        ).fetchall()

        self.assertEqual(self.seed["organization_runtime_profiles"], 4)
        self.assertEqual(second["assignments_created"], 0)
        self.assertEqual([(row["resident_id"], row["member_role"]) for row in members], [(4, "chair"), (19, "member")])
        self.assertEqual(second["organization_roles"], 12)
        self.assertEqual(second["organization_relationships"], 12)

    def test_proposal_waits_for_delay_then_executes_through_ledger(self):
        proposal = self._proposal()
        voted = cast_organization_vote(
            self.conn,
            proposal_id=proposal["id"],
            resident_id=4,
            decision="approve",
            rationale="符合组织目标",
        )
        with self.assertRaisesRegex(ValueError, "等待期"):
            finalize_organization_proposal(
                self.conn,
                proposal["id"],
                world_time=self.start + timedelta(minutes=59),
            )
        approved = finalize_organization_proposal(
            self.conn,
            proposal["id"],
            world_time=self.start + timedelta(minutes=60),
        )
        executed = execute_organization_proposal(
            self.conn,
            proposal["id"],
            world_time=self.start + timedelta(minutes=61),
        )
        recipient = self.conn.execute(
            "SELECT money FROM residents WHERE id = 1"
        ).fetchone()
        commitment = self.conn.execute(
            """
            SELECT status, responsibility_resident_id, amount_minor
            FROM organization_commitments WHERE proposal_id = ?
            """,
            (proposal["id"],),
        ).fetchone()

        self.assertEqual(voted["approvals_weight"], 2)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(executed["status"], "executed")
        self.assertIsNotNone(executed["ledger_transaction_id"])
        self.assertEqual(recipient["money"], 110)
        self.assertEqual(dict(commitment), {"status": "fulfilled", "responsibility_resident_id": 4, "amount_minor": 1000})
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_pending_proposal_reserves_budget(self):
        budget = organization_budget_state(self.conn, self.student_union)
        self._proposal(requested_budget_minor=budget["cash_minor"] - 1000)
        with self.assertRaisesRegex(ValueError, "可用预算不足"):
            self._proposal(
                proposal_key="test:student-union:second",
                requested_budget_minor=2000,
            )
        reserved = organization_budget_state(self.conn, self.student_union)
        self.assertEqual(reserved["available_minor"], 1000)

    def test_member_proposal_is_executed_by_authorized_chair_during_world_tick(self):
        proposal = self._proposal(
            proposal_key="test:member-proposal",
            proposer_resident_id=19,
            requested_budget_minor=500,
        )
        cast_organization_vote(
            self.conn,
            proposal_id=proposal["id"],
            resident_id=4,
            decision="approve",
        )
        result = process_organization_runtime(
            self.conn,
            self.start + timedelta(minutes=60),
        )
        commitment = self.conn.execute(
            """
            SELECT responsibility_resident_id, status
            FROM organization_commitments WHERE proposal_id = ?
            """,
            (proposal["id"],),
        ).fetchone()

        self.assertEqual(result["executed"], [proposal["id"]])
        self.assertEqual(commitment["responsibility_resident_id"], 4)
        self.assertEqual(commitment["status"], "fulfilled")

    def test_member_cannot_propose_above_role_limit(self):
        with self.assertRaisesRegex(ValueError, "角色权限"):
            self._proposal(
                proposal_key="test:member-over-limit",
                proposer_resident_id=19,
                requested_budget_minor=60000,
            )

    def test_rejection_records_decision_without_spending(self):
        proposal = self._proposal(proposal_key="test:rejected")
        cast_organization_vote(
            self.conn,
            proposal_id=proposal["id"],
            resident_id=4,
            decision="reject",
            rationale="与当前服务目标冲突",
        )
        result = process_organization_runtime(
            self.conn,
            self.start + timedelta(minutes=60),
        )
        rejected = self.conn.execute(
            "SELECT status, ledger_transaction_id FROM organization_proposals WHERE id = ?",
            (proposal["id"],),
        ).fetchone()

        self.assertEqual(result["rejected"], [proposal["id"]])
        self.assertEqual(rejected["status"], "rejected")
        self.assertIsNone(rejected["ledger_transaction_id"])
        dissent = self.conn.execute(
            """
            SELECT event_type FROM organization_events
            WHERE proposal_id = ? AND event_type = 'organization_internal_dissent'
            """,
            (proposal["id"],),
        ).fetchone()
        self.assertIsNotNone(dissent)

    def test_overdue_commitment_reduces_reputation_and_releases_reservation(self):
        commitment = create_organization_commitment(
            self.conn,
            commitment_key="test:student-union:promise",
            organization_id=self.student_union,
            responsible_resident_id=4,
            commitment_type="service_delivery",
            counterparty_actor_key="resident:1",
            amount_minor=2000,
            due_at=(self.start + timedelta(minutes=30)).isoformat(),
        )
        reserved = organization_budget_state(self.conn, self.student_union)
        result = process_organization_runtime(
            self.conn,
            self.start + timedelta(minutes=31),
        )
        profile = self.conn.execute(
            """
            SELECT reputation FROM organization_runtime_profiles
            WHERE organization_id = ?
            """,
            (self.student_union,),
        ).fetchone()
        released = organization_budget_state(self.conn, self.student_union)

        self.assertEqual(reserved["commitment_reserved_minor"], 2000)
        self.assertEqual(result["breached_commitments"], [commitment["id"]])
        self.assertEqual(profile["reputation"], 47)
        self.assertEqual(released["commitment_reserved_minor"], 0)

    def test_relationship_changes_require_evidence_and_are_bounded(self):
        target_id = self.conn.execute(
            "SELECT id FROM campus_organizations WHERE name = '创新社'"
        ).fetchone()["id"]
        first = record_organization_relationship_evidence(
            self.conn,
            from_organization_id=self.student_union,
            to_organization_id=target_id,
            relation_type="alliance",
            trust_delta=8,
            influence_delta=12,
            evidence={"source": "joint_project"},
        )
        bounded = record_organization_relationship_evidence(
            self.conn,
            from_organization_id=self.student_union,
            to_organization_id=target_id,
            relation_type="conflict",
            trust_delta=-200,
            influence_delta=200,
            evidence={"source": "resource_dispute"},
        )

        self.assertEqual(first["trust"], 58)
        self.assertEqual(bounded["trust"], 0)
        self.assertEqual(bounded["influence"], 100)

    def test_organization_role_changes_member_opportunity_access(self):
        self.conn.executescript(
            """
            CREATE TABLE agent_capability_profiles (
                resident_id INTEGER PRIMARY KEY
            );
            CREATE TABLE agent_opportunity_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resident_id INTEGER NOT NULL,
                opportunity_key TEXT NOT NULL,
                access_level INTEGER NOT NULL,
                time_cost_multiplier REAL NOT NULL,
                monetary_barrier INTEGER NOT NULL DEFAULT 0,
                eligibility TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                source_detail TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        self.conn.execute(
            "INSERT INTO agent_capability_profiles (resident_id) VALUES (4)"
        )
        self.conn.execute(
            """
            INSERT INTO agent_opportunity_access
            (resident_id, opportunity_key, access_level,
             time_cost_multiplier, eligibility)
            VALUES (4, 'institutional_services', 50, 1.0, 'eligible')
            """
        )
        opportunity = get_opportunity_access(self.conn, 4)[0]

        self.assertEqual(opportunity["access_level"], 62)
        self.assertEqual(
            opportunity["source_detail"]["organization_access_bonus"],
            12,
        )
        self.assertEqual(
            opportunity["source_detail"]["organization_memberships"][0]["role_key"],
            "chair",
        )


if __name__ == "__main__":
    unittest.main()
