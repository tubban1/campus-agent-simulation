import sqlite3
import unittest

import app.main as main
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import (
    audit_ledger,
    post_authorized_balance_change,
    post_ledger_transaction,
    post_money_transfer,
    reconcile_ledger,
    reverse_ledger_transaction,
    seed_economy_foundation,
)
from app.models import SCHEMA_SQL
from tools.city_tools import buy_sell


class EconomyLedgerFoundationTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES
            (1, '账本学生', '学生', '认真', '测试经济闭环', 100, '食堂'),
            (2, '账本商户', '商户', '务实', '提供校园商品', 80, '食堂')
            """
        )
        self.conn.execute(
            """
            INSERT INTO agent_profiles
            (resident_id, gender, avatar_style, energy, mood, current_task,
             skills, strategy, schedule, perception)
            VALUES
            (1, '女', '测试', 80, '平稳', '测试账本', '{}', '{}', '[]', '{}'),
            (2, '男', '测试', 80, '平稳', '经营商户', '{}', '{}', '[]', '{}')
            """
        )
        self.conn.execute(
            """
            INSERT INTO inventory (resident_id, item_name, quantity)
            VALUES (2, '套餐饭', 3)
            """
        )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True, seed_demo_spaces=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        self.first_seed = seed_economy_foundation(self.conn)

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_seed_creates_traceable_opening_balances_idempotently(self):
        second_seed = seed_economy_foundation(self.conn)
        resident_cash = self.conn.execute(
            """
            SELECT balance_minor FROM ledger_accounts
            WHERE account_key = 'resident:1:cash'
            """
        ).fetchone()
        opening = self.conn.execute(
            """
            SELECT id FROM ledger_transactions
            WHERE transaction_key = 'opening:resident:1:campus_coin:v1'
            """
        ).fetchall()

        self.assertEqual(resident_cash["balance_minor"], 10000)
        self.assertEqual(len(opening), 1)
        self.assertGreaterEqual(self.first_seed["actors_total"], 3)
        self.assertEqual(second_seed["opening_transactions_created"], 0)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_transfer_is_balanced_idempotent_and_updates_legacy_projection(self):
        first = post_money_transfer(
            self.conn,
            transaction_key="test:transfer:1",
            from_account_key="resident:1:cash",
            to_account_key="system:campus-services:cash",
            amount_coins=8,
            transaction_type="test_transfer",
            source_type="unit_test",
            source_id="1",
        )
        second = post_money_transfer(
            self.conn,
            transaction_key="test:transfer:1",
            from_account_key="resident:1:cash",
            to_account_key="system:campus-services:cash",
            amount_coins=8,
            transaction_type="test_transfer",
            source_type="unit_test",
            source_id="1",
        )
        resident = self.conn.execute(
            "SELECT money FROM residents WHERE id = 1"
        ).fetchone()
        service = self.conn.execute(
            """
            SELECT balance FROM world_resource_accounts
            WHERE account_key = 'campus-services'
            """
        ).fetchone()

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(resident["money"], 92)
        self.assertEqual(service["balance"], 8)
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_unbalanced_transaction_is_rejected_without_writes(self):
        before = self.conn.execute(
            "SELECT COUNT(*) AS count FROM ledger_transactions"
        ).fetchone()["count"]

        with self.assertRaisesRegex(ValueError, "Unbalanced"):
            post_ledger_transaction(
                self.conn,
                transaction_key="test:unbalanced",
                transaction_type="invalid",
                source_type="unit_test",
                entries=[
                    {
                        "account_key": "resident:1:cash",
                        "entry_side": "debit",
                        "amount_minor": 100,
                    },
                    {
                        "account_key": "system:campus-services:cash",
                        "entry_side": "credit",
                        "amount_minor": 90,
                    },
                ],
            )

        after = self.conn.execute(
            "SELECT COUNT(*) AS count FROM ledger_transactions"
        ).fetchone()["count"]
        self.assertEqual(after, before)

    def test_insufficient_balance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "余额不足"):
            post_money_transfer(
                self.conn,
                transaction_key="test:too-large",
                from_account_key="resident:1:cash",
                to_account_key="system:campus-services:cash",
                amount_coins=101,
                transaction_type="test_transfer",
                source_type="unit_test",
            )

        self.assertEqual(
            self.conn.execute(
                "SELECT money FROM residents WHERE id = 1"
            ).fetchone()["money"],
            100,
        )

    def test_goods_trade_posts_money_ledger_and_inventory_together(self):
        result = buy_sell(self.conn, 1, 2, "套餐饭", 1, 12)
        balances = self.conn.execute(
            "SELECT id, money FROM residents ORDER BY id"
        ).fetchall()
        inventory = self.conn.execute(
            """
            SELECT resident_id, quantity FROM inventory
            WHERE item_name = '套餐饭'
            ORDER BY resident_id
            """
        ).fetchall()
        ledger = self.conn.execute(
            """
            SELECT transaction_type, source_type
            FROM ledger_transactions
            WHERE id = ?
            """,
            (result["ledger_transaction_id"],),
        ).fetchone()

        self.assertEqual([(row["id"], row["money"]) for row in balances], [(1, 88), (2, 92)])
        self.assertEqual(
            [(row["resident_id"], row["quantity"]) for row in inventory],
            [(1, 1), (2, 2)],
        )
        self.assertEqual(ledger["transaction_type"], "goods_trade")
        self.assertEqual(ledger["source_type"], "legacy_transaction")
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_authorized_issue_destroy_and_external_inflow_are_balanced(self):
        issue = post_authorized_balance_change(
            self.conn,
            transaction_key="test:issue:1",
            operation_type="issue",
            authorization_rule_key="campus-coin-issue-v1",
            authority_actor_key="system:ledger-controller",
            target_account_key="resident:1:cash",
            amount_coins=20,
            source_type="unit_test",
            source_id="issue-1",
            description="测试授权发行",
        )
        destroy = post_authorized_balance_change(
            self.conn,
            transaction_key="test:destroy:1",
            operation_type="destroy",
            authorization_rule_key="campus-coin-destroy-v1",
            authority_actor_key="system:ledger-controller",
            target_account_key="resident:1:cash",
            amount_coins=5,
            source_type="unit_test",
            source_id="destroy-1",
            description="测试授权销毁",
        )
        inflow = post_authorized_balance_change(
            self.conn,
            transaction_key="test:external-inflow:1",
            operation_type="external_inflow",
            authorization_rule_key="external-inflow-v1",
            authority_actor_key="system:ledger-controller",
            target_account_key="resident:1:cash",
            amount_coins=10,
            source_type="external_event",
            source_id="external-1",
            description="测试外部资金流入",
        )

        self.assertTrue(issue["created"])
        self.assertTrue(destroy["created"])
        self.assertTrue(inflow["created"])
        self.assertEqual(
            self.conn.execute(
                "SELECT money FROM residents WHERE id = 1"
            ).fetchone()["money"],
            125,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS count FROM ledger_authorized_operations"
            ).fetchone()["count"],
            3,
        )
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_unauthorized_issue_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "无权"):
            post_authorized_balance_change(
                self.conn,
                transaction_key="test:unauthorized",
                operation_type="issue",
                authorization_rule_key="campus-coin-issue-v1",
                authority_actor_key="resident:1",
                target_account_key="resident:1:cash",
                amount_coins=10,
                source_type="unit_test",
                description="未授权发行",
            )

        self.assertEqual(
            self.conn.execute(
                "SELECT money FROM residents WHERE id = 1"
            ).fetchone()["money"],
            100,
        )

    def test_reversal_preserves_original_and_restores_balances(self):
        transfer = post_money_transfer(
            self.conn,
            transaction_key="test:reversible-transfer",
            from_account_key="resident:1:cash",
            to_account_key="system:campus-services:cash",
            amount_coins=8,
            transaction_type="test_transfer",
            source_type="unit_test",
        )
        reversal = reverse_ledger_transaction(
            self.conn,
            original_transaction_key="test:reversible-transfer",
            reversal_transaction_key="test:reversible-transfer:reversal",
            authorization_rule_key="ledger-reversal-v1",
            authority_actor_key="system:ledger-controller",
            source_type="unit_test",
            reason="测试纠正错误交易",
        )
        repeated = reverse_ledger_transaction(
            self.conn,
            original_transaction_key="test:reversible-transfer",
            reversal_transaction_key="test:reversible-transfer:reversal",
            authorization_rule_key="ledger-reversal-v1",
            authority_actor_key="system:ledger-controller",
            source_type="unit_test",
            reason="测试纠正错误交易",
        )
        original = self.conn.execute(
            "SELECT status FROM ledger_transactions WHERE id = ?",
            (transfer["id"],),
        ).fetchone()

        self.assertEqual(original["status"], "reversed")
        self.assertTrue(reversal["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(
            self.conn.execute(
                "SELECT money FROM residents WHERE id = 1"
            ).fetchone()["money"],
            100,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS count FROM ledger_reversals"
            ).fetchone()["count"],
            1,
        )
        self.assertTrue(reconcile_ledger(self.conn)["balanced"])

    def test_reconciliation_anomaly_is_recorded_once(self):
        self.conn.execute(
            """
            UPDATE ledger_accounts
            SET balance_minor = balance_minor + 1
            WHERE account_key = 'resident:1:cash'
            """
        )
        first = audit_ledger(self.conn, source_type="unit_test")
        second = audit_ledger(self.conn, source_type="unit_test")
        events = self.conn.execute(
            """
            SELECT event_type, severity, status
            FROM ledger_audit_events
            WHERE event_type = 'ledger_reconciliation_failed'
            """
        ).fetchall()

        self.assertFalse(first["balanced"])
        self.assertFalse(second["balanced"])
        self.assertEqual(len(events), 1)
        self.assertEqual(
            (events[0]["severity"], events[0]["status"]),
            ("critical", "open"),
        )


if __name__ == "__main__":
    unittest.main()
