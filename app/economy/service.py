from __future__ import annotations

import json
from typing import Optional

from datetime import datetime, timezone
import hashlib
from app.json_utils import json_dumps
from app.world_runtime.clock import get_world_now


CURRENCY = "campus_coin"
MINOR_PER_COIN = 100
LEDGER_RULE_VERSION = "economy-ledger-v1"
AUTHORIZATION_RULE_VERSION = "economy-authorization-v1"


def _json(value) -> str:
    return json_dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return get_world_now().isoformat()


def _insert_actor(
    conn,
    actor_key: str,
    actor_type: str,
    display_name: str,
    *,
    resident_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    metadata: Optional[dict] = None,
):
    existing = conn.execute(
        "SELECT * FROM economic_actors WHERE actor_key = ?",
        (actor_key,),
    ).fetchone()
    if existing:
        return existing
    conn.execute(
        """
        INSERT OR IGNORE INTO economic_actors
        (actor_key, actor_type, display_name, resident_id, organization_id,
         metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            actor_key,
            actor_type,
            display_name,
            resident_id,
            organization_id,
            _json(metadata),
        ),
    )
    return conn.execute(
        "SELECT * FROM economic_actors WHERE actor_key = ?",
        (actor_key,),
    ).fetchone()


def _insert_account(
    conn,
    actor_id: int,
    account_key: str,
    account_code: str,
    account_type: str,
    normal_side: str,
    *,
    metadata: Optional[dict] = None,
):
    existing = conn.execute(
        "SELECT * FROM ledger_accounts WHERE account_key = ?",
        (account_key,),
    ).fetchone()
    if existing:
        return existing
    conn.execute(
        """
        INSERT OR IGNORE INTO ledger_accounts
        (account_key, actor_id, account_code, account_type, normal_side,
         currency, balance_minor, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            account_key,
            actor_id,
            account_code,
            account_type,
            normal_side,
            CURRENCY,
            _json(metadata),
        ),
    )
    return conn.execute(
        "SELECT * FROM ledger_accounts WHERE account_key = ?",
        (account_key,),
    ).fetchone()


def ensure_ledger_account(
    conn,
    *,
    actor_key: str,
    account_code: str,
    account_type: str,
    normal_side: str,
):
    actor = conn.execute(
        "SELECT id FROM economic_actors WHERE actor_key = ?",
        (actor_key,),
    ).fetchone()
    if not actor:
        raise ValueError(f"Economic actor does not exist: {actor_key}")
    account = _insert_account(
        conn,
        int(actor["id"]),
        f"{actor_key}:{account_code}",
        account_code,
        account_type,
        normal_side,
    )
    if account:
        return account
    return conn.execute(
        """
        SELECT * FROM ledger_accounts
        WHERE actor_id = ? AND account_code = ? AND currency = ?
        """,
        (actor["id"], account_code, CURRENCY),
    ).fetchone()


def _insert_authorization_rule(
    conn,
    *,
    rule_key: str,
    operation_type: str,
    authority_actor_key: str,
    counterparty_account_key: str,
    counterparty_side: str,
    allowed_target_actor_types: list[str],
    max_amount_minor: int = 0,
    metadata: Optional[dict] = None,
):
    conn.execute(
        """
        INSERT OR IGNORE INTO ledger_authorization_rules
        (rule_key, operation_type, authority_actor_key, counterparty_account_key,
         counterparty_side, max_amount_minor, allowed_target_actor_types,
         rule_version, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule_key,
            operation_type,
            authority_actor_key,
            counterparty_account_key,
            counterparty_side,
            int(max_amount_minor),
            _json(allowed_target_actor_types),
            AUTHORIZATION_RULE_VERSION,
            _json(metadata),
        ),
    )
    return conn.execute(
        "SELECT * FROM ledger_authorization_rules WHERE rule_key = ?",
        (rule_key,),
    ).fetchone()


def _transaction_details(conn, transaction_id: int, *, created: bool) -> dict:
    transaction = conn.execute(
        "SELECT * FROM ledger_transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    entries = conn.execute(
        """
        SELECT le.*, la.account_key
        FROM ledger_entries le
        JOIN ledger_accounts la ON la.id = le.account_id
        WHERE le.transaction_id = ?
        ORDER BY le.id
        """,
        (transaction_id,),
    ).fetchall()
    return {
        "id": int(transaction["id"]),
        "transaction_key": transaction["transaction_key"],
        "status": transaction["status"],
        "created": created,
        "entries": [
            {
                "account_key": entry["account_key"],
                "entry_side": entry["entry_side"],
                "amount_minor": int(entry["amount_minor"]),
                "currency": entry["currency"],
            }
            for entry in entries
        ],
    }


def post_ledger_transaction(
    conn,
    *,
    transaction_key: str,
    transaction_type: str,
    source_type: str,
    source_id: str = "",
    entries: list[dict],
    action_execution_id: Optional[int] = None,
    world_event_id: Optional[int] = None,
    occurred_at: Optional[datetime] = None,
    rule_version: str = LEDGER_RULE_VERSION,
    description: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    existing = conn.execute(
        "SELECT id FROM ledger_transactions WHERE transaction_key = ?",
        (transaction_key,),
    ).fetchone()
    if existing:
        return _transaction_details(conn, int(existing["id"]), created=False)

    if len(entries) < 2:
        raise ValueError("A posted ledger transaction requires at least two entries")

    totals: dict[tuple[str, str], int] = {}
    resolved_entries = []
    for entry in entries:
        side = str(entry["entry_side"])
        amount_minor = int(entry["amount_minor"])
        currency = str(entry.get("currency") or CURRENCY)
        if side not in {"debit", "credit"}:
            raise ValueError(f"Unsupported ledger side: {side}")
        if amount_minor <= 0:
            raise ValueError("Ledger entry amount must be positive")
        account = conn.execute(
            "SELECT * FROM ledger_accounts WHERE account_key = ? AND status = 'active'",
            (entry["account_key"],),
        ).fetchone()
        if not account:
            raise ValueError(f"Ledger account does not exist: {entry['account_key']}")
        if account["currency"] != currency:
            raise ValueError(f"Ledger currency mismatch for {entry['account_key']}")
        key = (currency, side)
        totals[key] = totals.get(key, 0) + amount_minor
        resolved_entries.append((account, side, amount_minor, currency, entry.get("memo", "")))

    currencies = {currency for currency, _side in totals}
    for currency in currencies:
        if totals.get((currency, "debit"), 0) != totals.get((currency, "credit"), 0):
            raise ValueError(f"Unbalanced ledger transaction for {currency}")

    cursor = conn.execute(
        """
        INSERT INTO ledger_transactions
        (transaction_key, transaction_type, status, source_type, source_id,
         action_execution_id, world_event_id, occurred_at, rule_version,
         description, metadata_json)
        VALUES (?, ?, 'posted', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_key,
            transaction_type,
            source_type,
            str(source_id or ""),
            action_execution_id,
            world_event_id,
            (
                occurred_at.isoformat()
                if isinstance(occurred_at, datetime)
                else occurred_at or _now()
            ),
            rule_version,
            description,
            _json(metadata),
        ),
    )
    transaction_id = int(cursor.lastrowid)

    touched_actor_ids = set()
    for account, side, amount_minor, currency, memo in resolved_entries:
        conn.execute(
            """
            INSERT INTO ledger_entries
            (transaction_id, account_id, entry_side, amount_minor, currency, memo)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (transaction_id, account["id"], side, amount_minor, currency, memo),
        )
        delta = amount_minor if side == account["normal_side"] else -amount_minor
        conn.execute(
            """
            UPDATE ledger_accounts
            SET balance_minor = balance_minor + ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (delta, account["id"]),
        )
        touched_actor_ids.add(int(account["actor_id"]))

    for actor_id in touched_actor_ids:
        _sync_legacy_balance_projection(conn, actor_id)
    return _transaction_details(conn, transaction_id, created=True)


def post_money_transfer(
    conn,
    *,
    transaction_key: str,
    from_account_key: str,
    to_account_key: str,
    amount_coins: int,
    transaction_type: str,
    source_type: str,
    source_id: str = "",
    action_execution_id: Optional[int] = None,
    world_event_id: Optional[int] = None,
    description: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    return post_money_transfer_minor(
        conn,
        transaction_key=transaction_key,
        from_account_key=from_account_key,
        to_account_key=to_account_key,
        amount_minor=int(amount_coins) * MINOR_PER_COIN,
        transaction_type=transaction_type,
        source_type=source_type,
        source_id=source_id,
        action_execution_id=action_execution_id,
        world_event_id=world_event_id,
        description=description,
        metadata=metadata,
    )


def post_money_transfer_minor(
    conn,
    *,
    transaction_key: str,
    from_account_key: str,
    to_account_key: str,
    amount_minor: int,
    transaction_type: str,
    source_type: str,
    source_id: str = "",
    action_execution_id: Optional[int] = None,
    world_event_id: Optional[int] = None,
    description: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    existing = conn.execute(
        "SELECT id FROM ledger_transactions WHERE transaction_key = ?",
        (transaction_key,),
    ).fetchone()
    if existing:
        return _transaction_details(conn, int(existing["id"]), created=False)

    amount_minor = int(amount_minor)
    if amount_minor <= 0:
        raise ValueError("Transfer amount must be positive")
    sender = conn.execute(
        "SELECT balance_minor FROM ledger_accounts WHERE account_key = ?",
        (from_account_key,),
    ).fetchone()
    if not sender:
        raise ValueError(f"Ledger account does not exist: {from_account_key}")
    if int(sender["balance_minor"]) < amount_minor:
        raise ValueError("账本账户余额不足")
    return post_ledger_transaction(
        conn,
        transaction_key=transaction_key,
        transaction_type=transaction_type,
        source_type=source_type,
        source_id=source_id,
        action_execution_id=action_execution_id,
        world_event_id=world_event_id,
        description=description,
        metadata=metadata,
        entries=[
            {
                "account_key": to_account_key,
                "entry_side": "debit",
                "amount_minor": amount_minor,
            },
            {
                "account_key": from_account_key,
                "entry_side": "credit",
                "amount_minor": amount_minor,
            },
        ],
    )
def _authorized_rule(
    conn,
    *,
    rule_key: str,
    operation_type: str,
    authority_actor_key: str,
):
    rule = conn.execute(
        """
        SELECT * FROM ledger_authorization_rules
        WHERE rule_key = ? AND status = 'active'
        """,
        (rule_key,),
    ).fetchone()
    if not rule:
        raise ValueError(f"账本授权规则不存在或未启用：{rule_key}")
    if rule["operation_type"] != operation_type:
        raise ValueError("账本授权操作类型不匹配")
    if rule["authority_actor_key"] != authority_actor_key:
        raise ValueError("经济主体无权执行该账本操作")
    return rule


def post_authorized_balance_change(
    conn,
    *,
    transaction_key: str,
    operation_type: str,
    authorization_rule_key: str,
    authority_actor_key: str,
    target_account_key: str,
    amount_coins: int,
    source_type: str,
    source_id: str = "",
    description: str,
    metadata: Optional[dict] = None,
) -> dict:
    if operation_type not in {"issue", "destroy", "external_inflow"}:
        raise ValueError(f"不支持的授权余额操作：{operation_type}")
    existing = conn.execute(
        "SELECT id FROM ledger_transactions WHERE transaction_key = ?",
        (transaction_key,),
    ).fetchone()
    if existing:
        return _transaction_details(conn, int(existing["id"]), created=False)

    amount_minor = int(amount_coins) * MINOR_PER_COIN
    if amount_minor <= 0:
        raise ValueError("授权余额操作金额必须大于零")
    rule = _authorized_rule(
        conn,
        rule_key=authorization_rule_key,
        operation_type=operation_type,
        authority_actor_key=authority_actor_key,
    )
    if int(rule["max_amount_minor"]) and amount_minor > int(rule["max_amount_minor"]):
        raise ValueError("授权余额操作超过单笔上限")

    target = conn.execute(
        """
        SELECT la.*, ea.actor_type
        FROM ledger_accounts la
        JOIN economic_actors ea ON ea.id = la.actor_id
        WHERE la.account_key = ? AND la.status = 'active'
        """,
        (target_account_key,),
    ).fetchone()
    if not target or target["account_code"] != "cash":
        raise ValueError("授权余额操作目标必须是有效现金账户")
    allowed_types = json.loads(rule["allowed_target_actor_types"] or "[]")
    if allowed_types and target["actor_type"] not in allowed_types:
        raise ValueError("目标经济主体类型不在授权范围内")

    counterparty_side = rule["counterparty_side"]
    target_side = "credit" if counterparty_side == "debit" else "debit"
    if (
        target_side == "credit"
        and target["normal_side"] == "debit"
        and int(target["balance_minor"]) < amount_minor
    ):
        raise ValueError("销毁金额超过目标现金余额")

    transaction = post_ledger_transaction(
        conn,
        transaction_key=transaction_key,
        transaction_type=operation_type,
        source_type=source_type,
        source_id=source_id,
        rule_version=rule["rule_version"],
        description=description,
        metadata={
            **(metadata or {}),
            "authorization_rule_key": authorization_rule_key,
            "authority_actor_key": authority_actor_key,
        },
        entries=[
            {
                "account_key": target_account_key,
                "entry_side": target_side,
                "amount_minor": amount_minor,
            },
            {
                "account_key": rule["counterparty_account_key"],
                "entry_side": counterparty_side,
                "amount_minor": amount_minor,
            },
        ],
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO ledger_authorized_operations
        (transaction_id, authorization_rule_id, authority_actor_key,
         operation_type)
        VALUES (?, ?, ?, ?)
        """,
        (
            transaction["id"],
            rule["id"],
            authority_actor_key,
            operation_type,
        ),
    )
    return transaction


def reverse_ledger_transaction(
    conn,
    *,
    original_transaction_key: str,
    reversal_transaction_key: str,
    authorization_rule_key: str,
    authority_actor_key: str,
    source_type: str,
    source_id: str = "",
    reason: str,
) -> dict:
    original = conn.execute(
        """
        SELECT * FROM ledger_transactions
        WHERE transaction_key = ?
        """,
        (original_transaction_key,),
    ).fetchone()
    if not original:
        raise ValueError("待冲正交易不存在")
    existing_reversal = conn.execute(
        """
        SELECT reversal_transaction_id FROM ledger_reversals
        WHERE original_transaction_id = ?
        """,
        (original["id"],),
    ).fetchone()
    if existing_reversal:
        return _transaction_details(
            conn,
            int(existing_reversal["reversal_transaction_id"]),
            created=False,
        )
    if original["status"] != "posted":
        raise ValueError("交易当前状态不允许冲正")

    rule = _authorized_rule(
        conn,
        rule_key=authorization_rule_key,
        operation_type="reverse",
        authority_actor_key=authority_actor_key,
    )
    original_entries = conn.execute(
        """
        SELECT le.*, la.account_key, la.normal_side, la.balance_minor
        FROM ledger_entries le
        JOIN ledger_accounts la ON la.id = le.account_id
        WHERE le.transaction_id = ?
        ORDER BY le.id
        """,
        (original["id"],),
    ).fetchall()
    reverse_entries = []
    for entry in original_entries:
        reverse_side = "credit" if entry["entry_side"] == "debit" else "debit"
        if (
            reverse_side == "credit"
            and entry["normal_side"] == "debit"
            and int(entry["balance_minor"]) < int(entry["amount_minor"])
        ):
            raise ValueError(f"账户余额不足以冲正：{entry['account_key']}")
        reverse_entries.append(
            {
                "account_key": entry["account_key"],
                "entry_side": reverse_side,
                "amount_minor": int(entry["amount_minor"]),
                "currency": entry["currency"],
                "memo": f"冲正 {original_transaction_key}",
            }
        )

    reversal = post_ledger_transaction(
        conn,
        transaction_key=reversal_transaction_key,
        transaction_type="reversal",
        source_type=source_type,
        source_id=source_id,
        rule_version=rule["rule_version"],
        description=reason,
        metadata={
            "original_transaction_key": original_transaction_key,
            "authorization_rule_key": authorization_rule_key,
            "authority_actor_key": authority_actor_key,
        },
        entries=reverse_entries,
    )
    conn.execute(
        """
        INSERT INTO ledger_reversals
        (original_transaction_id, reversal_transaction_id,
         authorization_rule_id, reason)
        VALUES (?, ?, ?, ?)
        """,
        (original["id"], reversal["id"], rule["id"], reason),
    )
    conn.execute(
        "UPDATE ledger_transactions SET status = 'reversed' WHERE id = ?",
        (original["id"],),
    )
    _insert_audit_event(
        conn,
        event_key=f"reversal:{original['id']}:{reversal['id']}",
        event_type="transaction_reversed",
        severity="info",
        transaction_id=int(original["id"]),
        source_type=source_type,
        source_id=source_id,
        details={
            "original_transaction_key": original_transaction_key,
            "reversal_transaction_key": reversal_transaction_key,
            "reason": reason,
        },
    )
    return reversal


def _sync_legacy_balance_projection(conn, actor_id: int) -> None:
    actor = conn.execute(
        "SELECT * FROM economic_actors WHERE id = ?",
        (actor_id,),
    ).fetchone()
    if not actor:
        return
    cash = conn.execute(
        """
        SELECT balance_minor FROM ledger_accounts
        WHERE actor_id = ? AND account_code = 'cash' AND currency = ?
        """,
        (actor_id, CURRENCY),
    ).fetchone()
    if not cash:
        return
    balance_minor = int(cash["balance_minor"])
    # Legacy balance fields only support whole campus coins. The ledger remains
    # authoritative and retains the exact minor-unit remainder.
    balance_coins = balance_minor // MINOR_PER_COIN
    if actor["resident_id"] is not None:
        conn.execute(
            "UPDATE residents SET money = ? WHERE id = ?",
            (balance_coins, actor["resident_id"]),
        )
    if actor["organization_id"] is not None:
        conn.execute(
            "UPDATE campus_organizations SET budget = ? WHERE id = ?",
            (balance_coins, actor["organization_id"]),
        )
    if actor["actor_key"] == "system:campus-services":
        conn.execute(
            """
            UPDATE world_resource_accounts
            SET balance = ?, updated_at = CURRENT_TIMESTAMP
            WHERE account_key = 'campus-services'
            """,
            (balance_coins,),
        )


def _seed_actor_accounts(
    conn,
    *,
    actor_key: str,
    actor_type: str,
    display_name: str,
    opening_coins: int,
    resident_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> tuple[int, bool]:
    actor = _insert_actor(
        conn,
        actor_key,
        actor_type,
        display_name,
        resident_id=resident_id,
        organization_id=organization_id,
        metadata=metadata,
    )
    cash_key = f"{actor_key}:cash"
    equity_key = f"{actor_key}:opening-equity"
    _insert_account(conn, actor["id"], cash_key, "cash", "asset", "debit")
    _insert_account(
        conn,
        actor["id"],
        equity_key,
        "opening_equity",
        "equity",
        "credit",
    )
    created = False
    if int(opening_coins or 0) > 0:
        result = post_ledger_transaction(
            conn,
            transaction_key=f"opening:{actor_key}:{CURRENCY}:v1",
            transaction_type="opening_balance",
            source_type="legacy_balance_migration",
            source_id=actor_key,
            description=f"{display_name} 期初校园币余额",
            metadata={"authorized": True, "migration": "20260729_0008"},
            entries=[
                {
                    "account_key": cash_key,
                    "entry_side": "debit",
                    "amount_minor": int(opening_coins) * MINOR_PER_COIN,
                },
                {
                    "account_key": equity_key,
                    "entry_side": "credit",
                    "amount_minor": int(opening_coins) * MINOR_PER_COIN,
                },
            ],
        )
        created = bool(result["created"])
    return int(actor["id"]), created


def seed_economy_foundation(conn) -> dict:
    _insert_actor(
        conn,
        "external:opening-balance",
        "external",
        "期初余额授权来源",
        metadata={"purpose": "opening_balance_provenance"},
    )
    controller = _insert_actor(
        conn,
        "system:ledger-controller",
        "system",
        "账本控制主体",
        metadata={"purpose": "ledger_authorization"},
    )
    monetary = _insert_actor(
        conn,
        "system:monetary-authority",
        "public",
        "校园币授权主体",
        metadata={"purpose": "authorized_issue_and_destroy"},
    )
    outside = _insert_actor(
        conn,
        "external:outside-world",
        "external",
        "外部经济部门",
        metadata={"purpose": "traceable_external_inflow"},
    )
    _insert_account(
        conn,
        monetary["id"],
        "system:monetary-authority:issuance-equity",
        "issuance_equity",
        "equity",
        "credit",
    )
    _insert_account(
        conn,
        monetary["id"],
        "system:monetary-authority:destruction-equity",
        "destruction_equity",
        "equity",
        "credit",
    )
    _insert_account(
        conn,
        outside["id"],
        "external:outside-world:inflow-equity",
        "inflow_equity",
        "equity",
        "credit",
    )
    allowed_targets = ["person", "production_service", "organization", "public"]
    _insert_authorization_rule(
        conn,
        rule_key="campus-coin-issue-v1",
        operation_type="issue",
        authority_actor_key=controller["actor_key"],
        counterparty_account_key="system:monetary-authority:issuance-equity",
        counterparty_side="credit",
        allowed_target_actor_types=allowed_targets,
        metadata={"requires_reason": True},
    )
    _insert_authorization_rule(
        conn,
        rule_key="campus-coin-destroy-v1",
        operation_type="destroy",
        authority_actor_key=controller["actor_key"],
        counterparty_account_key="system:monetary-authority:destruction-equity",
        counterparty_side="debit",
        allowed_target_actor_types=allowed_targets,
        metadata={"requires_reason": True},
    )
    _insert_authorization_rule(
        conn,
        rule_key="external-inflow-v1",
        operation_type="external_inflow",
        authority_actor_key=controller["actor_key"],
        counterparty_account_key="external:outside-world:inflow-equity",
        counterparty_side="credit",
        allowed_target_actor_types=allowed_targets,
        metadata={"requires_source_record": True},
    )
    _insert_authorization_rule(
        conn,
        rule_key="ledger-reversal-v1",
        operation_type="reverse",
        authority_actor_key=controller["actor_key"],
        counterparty_account_key="system:monetary-authority:issuance-equity",
        counterparty_side="credit",
        allowed_target_actor_types=[],
        metadata={"requires_reason": True},
    )
    service_row = conn.execute(
        """
        SELECT balance FROM world_resource_accounts
        WHERE account_key = 'campus-services'
        """
    ).fetchone()
    service_balance = int(float(service_row["balance"])) if service_row else 0
    _, service_created = _seed_actor_accounts(
        conn,
        actor_key="system:campus-services",
        actor_type="public",
        display_name="校园公共服务账户",
        opening_coins=service_balance,
    )

    resident_count = 0
    opening_count = int(service_created)
    residents = conn.execute(
        "SELECT id, name, role, money FROM residents ORDER BY id"
    ).fetchall()
    for resident in residents:
        _, created = _seed_actor_accounts(
            conn,
            actor_key=f"resident:{resident['id']}",
            actor_type="person",
            display_name=resident["name"],
            opening_coins=int(resident["money"]),
            resident_id=int(resident["id"]),
            metadata={"role": resident["role"]},
        )
        resident_count += 1
        opening_count += int(created)

    organization_count = 0
    organizations = conn.execute(
        """
        SELECT id, name, organization_type, budget
        FROM campus_organizations
        ORDER BY id
        """
    ).fetchall()
    for organization in organizations:
        actor_type = (
            "production_service"
            if organization["organization_type"] in {"business", "service"}
            else "organization"
        )
        _, created = _seed_actor_accounts(
            conn,
            actor_key=f"organization:{organization['id']}",
            actor_type=actor_type,
            display_name=organization["name"],
            opening_coins=int(organization["budget"]),
            organization_id=int(organization["id"]),
            metadata={"organization_type": organization["organization_type"]},
        )
        organization_count += 1
        opening_count += int(created)

    reconciliation = reconcile_ledger(conn)
    if not reconciliation["balanced"]:
        raise RuntimeError(f"Economy ledger reconciliation failed: {reconciliation}")
    return {
        "residents": resident_count,
        "organizations": organization_count,
        "opening_transactions_created": opening_count,
        "actors_total": reconciliation["actor_count"],
        "accounts_total": reconciliation["account_count"],
        "transactions_total": reconciliation["transaction_count"],
        "authorization_rules": reconciliation["authorization_rule_count"],
        "balanced": True,
    }


def _insert_audit_event(
    conn,
    *,
    event_key: str,
    event_type: str,
    severity: str,
    details: dict,
    transaction_id: Optional[int] = None,
    source_type: str = "ledger_audit",
    source_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO ledger_audit_events
        (event_key, event_type, severity, transaction_id, source_type,
         source_id, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            event_type,
            severity,
            transaction_id,
            source_type,
            source_id,
            _json(details),
        ),
    )


def reconcile_ledger(conn) -> dict:
    transaction_imbalances = conn.execute(
        """
        SELECT lt.id, lt.transaction_key, le.currency,
               SUM(CASE WHEN le.entry_side = 'debit' THEN le.amount_minor ELSE 0 END)
                   AS debit_minor,
               SUM(CASE WHEN le.entry_side = 'credit' THEN le.amount_minor ELSE 0 END)
                   AS credit_minor
        FROM ledger_transactions lt
        JOIN ledger_entries le ON le.transaction_id = lt.id
        WHERE lt.status IN ('posted', 'reversed')
        GROUP BY lt.id, lt.transaction_key, le.currency
        HAVING
            SUM(CASE WHEN le.entry_side = 'debit' THEN le.amount_minor ELSE 0 END)
            <>
            SUM(CASE WHEN le.entry_side = 'credit' THEN le.amount_minor ELSE 0 END)
        ORDER BY lt.id
        """
    ).fetchall()
    account_rows = conn.execute(
        """
        SELECT la.id, la.account_key, la.balance_minor, la.normal_side,
               COALESCE(SUM(
                   CASE
                       WHEN le.entry_side = la.normal_side THEN le.amount_minor
                       ELSE -le.amount_minor
                   END
               ), 0) AS computed_minor
        FROM ledger_accounts la
        LEFT JOIN ledger_entries le ON le.account_id = la.id
        GROUP BY la.id, la.account_key, la.balance_minor, la.normal_side
        ORDER BY la.id
        """
    ).fetchall()
    account_mismatches = [
        {
            "account_key": row["account_key"],
            "cached_minor": int(row["balance_minor"]),
            "computed_minor": int(row["computed_minor"]),
        }
        for row in account_rows
        if int(row["balance_minor"]) != int(row["computed_minor"])
    ]
    counts = {}
    for table in (
        "economic_actors",
        "ledger_accounts",
        "ledger_transactions",
        "ledger_entries",
        "ledger_authorization_rules",
    ):
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        counts[table] = int(row["count"])
    return {
        "balanced": not transaction_imbalances and not account_mismatches,
        "transaction_imbalances": [
            {
                "transaction_key": row["transaction_key"],
                "currency": row["currency"],
                "debit_minor": int(row["debit_minor"]),
                "credit_minor": int(row["credit_minor"]),
            }
            for row in transaction_imbalances
        ],
        "account_mismatches": account_mismatches,
        "actor_count": counts["economic_actors"],
        "account_count": counts["ledger_accounts"],
        "transaction_count": counts["ledger_transactions"],
        "entry_count": counts["ledger_entries"],
        "authorization_rule_count": counts["ledger_authorization_rules"],
    }


def audit_ledger(conn, *, source_type: str = "manual_audit", source_id: str = "") -> dict:
    result = reconcile_ledger(conn)
    if result["balanced"]:
        return result
    anomaly_payload = {
        "transaction_imbalances": result["transaction_imbalances"],
        "account_mismatches": result["account_mismatches"],
    }
    signature = hashlib.sha256(_json(anomaly_payload).encode("utf-8")).hexdigest()[:24]
    _insert_audit_event(
        conn,
        event_key=f"reconciliation:{signature}",
        event_type="ledger_reconciliation_failed",
        severity="critical",
        source_type=source_type,
        source_id=source_id,
        details=anomaly_payload,
    )
    return result
