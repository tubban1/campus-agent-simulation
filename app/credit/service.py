from __future__ import annotations

from typing import Optional

import hashlib
from app.json_utils import json_dumps
import math
from datetime import date, datetime, timedelta, timezone

from app.economy.service import (
    ensure_ledger_account,
    post_authorized_balance_change,
    post_ledger_transaction,
    post_money_transfer_minor,
)


RULE_VERSION = "household-credit-v1"
CREDIT_UNION_ACTOR = "system:campus-credit-union"
CREDIT_UNION_CASH = f"{CREDIT_UNION_ACTOR}:cash"


def _json(value) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def _clamp(value, low, high):
    return max(low, min(high, value))


def credit_runtime_available(conn) -> bool:
    return bool(conn.execute("PRAGMA table_info(credit_profiles)").fetchall())


def _account_balance(conn, account_key: str) -> int:
    row = conn.execute(
        "SELECT balance_minor FROM ledger_accounts WHERE account_key = ? AND status = 'active'",
        (account_key,),
    ).fetchone()
    return int(row["balance_minor"]) if row else 0


def _ensure_credit_union(conn) -> dict:
    existing = conn.execute(
        "SELECT id FROM economic_actors WHERE actor_key = ?",
        (CREDIT_UNION_ACTOR,),
    ).fetchone()
    if not existing:
        conn.execute(
            """
            INSERT OR IGNORE INTO economic_actors
            (actor_key, actor_type, display_name, metadata_json)
            VALUES (?, 'public', '校园信用合作社', ?)
            """,
            (
                CREDIT_UNION_ACTOR,
                _json({
                    "purpose": "funded_household_credit_and_mutual_aid",
                    "credit_creation": False,
                }),
            ),
        )
    for code, account_type, normal_side in (
        ("cash", "asset", "debit"),
        ("loan_receivable", "asset", "debit"),
        ("interest_receivable", "asset", "debit"),
        ("penalty_receivable", "asset", "debit"),
        ("interest_income", "income", "credit"),
        ("penalty_income", "income", "credit"),
        ("credit_loss", "expense", "debit"),
        ("mutual_aid_expense", "expense", "debit"),
    ):
        ensure_ledger_account(
            conn,
            actor_key=CREDIT_UNION_ACTOR,
            account_code=code,
            account_type=account_type,
            normal_side=normal_side,
        )
    current = _account_balance(conn, CREDIT_UNION_CASH)
    source = _account_balance(conn, "system:campus-services:cash")
    funded = 0
    if current == 0 and source > 0:
        funded = min(200000, source // 5)
        if funded:
            post_money_transfer_minor(
                conn,
                transaction_key="credit-union:initial-reserve:v1",
                from_account_key="system:campus-services:cash",
                to_account_key=CREDIT_UNION_CASH,
                amount_minor=funded,
                transaction_type="credit_reserve_funding",
                source_type="credit_seed",
                source_id=CREDIT_UNION_ACTOR,
                description="公共服务账户拨付可追溯的信用与互助准备金",
                metadata={"credit_creation": False, "rule_version": RULE_VERSION},
            )
    if current == 0 and funded == 0:
        capitalization_coins = 1000
        post_authorized_balance_change(
            conn,
            transaction_key="credit-union:external-capitalization:v1",
            operation_type="external_inflow",
            authorization_rule_key="external-inflow-v1",
            authority_actor_key="system:ledger-controller",
            target_account_key=CREDIT_UNION_CASH,
            amount_coins=capitalization_coins,
            source_type="credit_seed",
            source_id="external:outside-world",
            description="外部部门对校园信用合作社的一次性可追溯资本投入",
            metadata={
                "purpose": "funded_credit_and_mutual_aid_reserve",
                "loan_level_credit_creation": False,
                "rule_version": RULE_VERSION,
            },
        )
        funded = capitalization_coins * 100
    return {
        "cash_minor": _account_balance(conn, CREDIT_UNION_CASH),
        "funded_minor": funded,
    }


def _ensure_borrower_accounts(conn, resident_id: int) -> None:
    actor = f"resident:{resident_id}"
    for code, account_type, normal_side in (
        ("loan_payable", "liability", "credit"),
        ("interest_payable", "liability", "credit"),
        ("penalty_payable", "liability", "credit"),
        ("interest_expense", "expense", "debit"),
        ("penalty_expense", "expense", "debit"),
    ):
        ensure_ledger_account(
            conn,
            actor_key=actor,
            account_code=code,
            account_type=account_type,
            normal_side=normal_side,
        )


def _capability(conn, resident_id: int) -> tuple[int, int]:
    if not conn.execute("PRAGMA table_info(agent_capability_profiles)").fetchall():
        return 50, 50
    row = conn.execute(
        """
        SELECT economic_access, risk_tolerance
        FROM agent_capability_profiles WHERE resident_id = ?
        """,
        (resident_id,),
    ).fetchone()
    return (
        int(row["economic_access"]) if row else 50,
        int(row["risk_tolerance"]) if row else 50,
    )


def _savings_balance(conn, resident_id: int) -> int:
    return _account_balance(conn, f"resident:{resident_id}:savings")


def _sync_budget_credit(conn, resident_id: int) -> None:
    if not conn.execute("PRAGMA table_info(household_budget_profiles)").fetchall():
        return
    profile = conn.execute(
        "SELECT * FROM credit_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    if not profile:
        return
    conn.execute(
        """
        UPDATE household_budget_profiles
        SET credit_enabled = ?,
            credit_limit_minor = ?,
            outstanding_debt_minor = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (
            int(profile["status"] == "active"),
            int(profile["credit_limit_minor"]),
            int(profile["outstanding_principal_minor"])
            + int(profile["accrued_interest_minor"]),
            resident_id,
        ),
    )


def seed_credit_runtime(conn, world_time=None) -> dict:
    now = _now(world_time)
    reserve = _ensure_credit_union(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO credit_products
        (product_key, name, lender_actor_key, product_type,
         min_principal_minor, max_principal_minor,
         annual_interest_basis_points, penalty_interest_basis_points,
         term_days, payment_cadence_days, grace_days,
         minimum_credit_score, collateral_required, guarantor_allowed,
         rule_version, metadata_json)
        VALUES ('campus-emergency-credit-v1', '校园应急周转',
                ?, 'emergency', 100, 10000, 800, 1200,
                28, 7, 3, 500, 0, 1, ?, ?)
        """,
        (
            CREDIT_UNION_ACTOR,
            RULE_VERSION,
            _json({
                "funding": "pre-funded_reserve",
                "automatic_use": "essential_emergency_only",
            }),
        ),
    )
    created_profiles = 0
    created_risk = 0
    created_goals = 0
    residents = conn.execute("SELECT id, money FROM residents ORDER BY id").fetchall()
    for resident in residents:
        resident_id = int(resident["id"])
        economic, risk_tolerance = _capability(conn, resident_id)
        score = int(_clamp(500 + economic * 2 - (100 - risk_tolerance) // 2, 300, 850))
        limit = int(_clamp(1500 + economic * 80 + int(resident["money"]) * 5, 2000, 12000))
        before = conn.execute(
            "SELECT resident_id FROM credit_profiles WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO credit_profiles
            (resident_id, credit_score, credit_limit_minor,
             outstanding_principal_minor, accrued_interest_minor,
             last_review_date, status, metadata_json)
            VALUES (?, ?, ?, 0, 0, ?, 'active', ?)
            """,
            (
                resident_id, score, limit, now.date().isoformat(),
                _json({
                    "funding_rule": "lender_cash_required",
                    "credit_creation": False,
                    "rule_version": RULE_VERSION,
                }),
            ),
        )
        created_profiles += int(before is None)
        risk_before = conn.execute(
            "SELECT resident_id FROM household_risk_profiles WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()
        income_volatility = int(_clamp(70 - economic // 2, 15, 85))
        risk_score = int(_clamp((income_volatility + (100 - risk_tolerance)) / 2, 0, 100))
        conn.execute(
            """
            INSERT OR IGNORE INTO household_risk_profiles
            (resident_id, income_volatility, health_exposure,
             essential_cost_exposure, shock_sensitivity,
             mutual_aid_enrolled, coverage_basis_points,
             coverage_limit_minor, risk_score, metadata_json)
            VALUES (?, ?, ?, ?, ?, 1, 3000, 3000, ?, ?)
            """,
            (
                resident_id, income_volatility,
                int(_clamp(25 + (resident_id * 7) % 35, 0, 100)),
                int(_clamp(35 + (resident_id * 11) % 35, 0, 100)),
                int(_clamp(65 - risk_tolerance // 2, 10, 90)),
                risk_score,
                _json({"rule_version": RULE_VERSION}),
            ),
        )
        created_risk += int(risk_before is None)
        target = conn.execute(
            """
            SELECT emergency_reserve_minor
            FROM household_budget_profiles WHERE resident_id = ?
            """,
            (resident_id,),
        ).fetchone()
        target_minor = int(target["emergency_reserve_minor"]) if target else 2000
        goal_key = f"savings-goal:emergency:{resident_id}"
        goal_before = conn.execute(
            "SELECT id FROM savings_goals WHERE goal_key = ?",
            (goal_key,),
        ).fetchone()
        if not goal_before:
            conn.execute(
                """
                INSERT OR IGNORE INTO savings_goals
                (goal_key, resident_id, goal_type, target_amount_minor,
                 current_amount_minor, target_date, priority, metadata_json)
                VALUES (?, ?, 'emergency_reserve', ?, ?, ?, 90, ?)
                """,
                (
                    goal_key, resident_id, target_minor,
                    _savings_balance(conn, resident_id),
                    (now.date() + timedelta(days=90)).isoformat(),
                    _json({"rule_version": RULE_VERSION}),
                ),
            )
        created_goals += int(goal_before is None)
        _ensure_borrower_accounts(conn, resident_id)
        _sync_budget_credit(conn, resident_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO credit_events
            (event_key, resident_id, event_type, score_before, score_after,
             credit_limit_before_minor, credit_limit_after_minor,
             details_json, occurred_at)
            VALUES (?, ?, 'profile_created', ?, ?, 0, ?, ?, ?)
            """,
            (
                f"credit-profile-created:{resident_id}",
                resident_id, score, score, limit,
                _json({"rule_version": RULE_VERSION}),
                now.isoformat(),
            ),
        )
    return {
        "profiles": len(residents),
        "profiles_created": created_profiles,
        "risk_profiles_created": created_risk,
        "savings_goals_created": created_goals,
        "credit_union_cash_minor": reserve["cash_minor"],
        "credit_union_funded_minor": reserve["funded_minor"],
    }


def available_credit(conn, resident_id: int) -> dict:
    profile = conn.execute(
        "SELECT * FROM credit_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    if not profile:
        return {
            "enabled": False,
            "credit_limit_minor": 0,
            "outstanding_principal_minor": 0,
            "accrued_interest_minor": 0,
            "available_credit_minor": 0,
            "due_debt_minor": 0,
        }
    due = conn.execute(
        """
        SELECT COALESCE(SUM(
            principal_due_minor - principal_paid_minor
            + interest_due_minor - interest_paid_minor
            + penalty_due_minor - penalty_paid_minor
        ), 0) value
        FROM credit_installments installment
        JOIN credit_contracts contract ON contract.id = installment.contract_id
        WHERE contract.borrower_resident_id = ?
          AND installment.status IN ('due', 'partial', 'late', 'defaulted')
        """,
        (resident_id,),
    ).fetchone()
    outstanding = int(profile["outstanding_principal_minor"])
    enabled = profile["status"] == "active"
    return {
        "enabled": enabled,
        "credit_score": int(profile["credit_score"]),
        "credit_limit_minor": int(profile["credit_limit_minor"]),
        "outstanding_principal_minor": outstanding,
        "accrued_interest_minor": int(profile["accrued_interest_minor"]),
        "available_credit_minor": (
            max(0, int(profile["credit_limit_minor"]) - outstanding)
            if enabled else 0
        ),
        "due_debt_minor": int(due["value"]),
        "delinquency_count": int(profile["delinquency_count"]),
        "default_count": int(profile["default_count"]),
        "status": profile["status"],
    }


def originate_credit(
    conn,
    *,
    resident_id: int,
    amount_minor: int,
    product_key: str = "campus-emergency-credit-v1",
    purpose: str = "emergency",
    world_time=None,
    guarantor_resident_id: Optional[int] = None,
    collateral: Optional[dict] = None,
    contract_key: Optional[str] = None,
) -> dict:
    now = _now(world_time)
    product = conn.execute(
        "SELECT * FROM credit_products WHERE product_key = ? AND status = 'active'",
        (product_key,),
    ).fetchone()
    if not product:
        raise ValueError("信用产品不存在或不可用")
    profile = conn.execute(
        "SELECT * FROM credit_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    if not profile or profile["status"] != "active":
        raise ValueError("借款人信用档案不可用")
    amount_minor = int(amount_minor)
    if not int(product["min_principal_minor"]) <= amount_minor <= int(product["max_principal_minor"]):
        raise ValueError("借款金额超出产品范围")
    if int(profile["credit_score"]) < int(product["minimum_credit_score"]):
        raise ValueError("信用评分未达到产品要求")
    if int(profile["outstanding_principal_minor"]) + amount_minor > int(profile["credit_limit_minor"]):
        raise ValueError("借款金额超过可用信用额度")
    if int(product["collateral_required"]) and not collateral:
        raise ValueError("该信用产品要求抵押物")
    if guarantor_resident_id and not int(product["guarantor_allowed"]):
        raise ValueError("该信用产品不接受担保人")
    if _account_balance(conn, f"{product['lender_actor_key']}:cash") < amount_minor:
        raise ValueError("放贷主体准备金不足")
    contract_key = contract_key or (
        f"credit:{resident_id}:{product_key}:{now.isoformat()}"
    )
    existing = conn.execute(
        "SELECT * FROM credit_contracts WHERE contract_key = ?",
        (contract_key,),
    ).fetchone()
    if existing:
        return {**dict(existing), "created": False}
    _ensure_borrower_accounts(conn, resident_id)
    borrower = f"resident:{resident_id}"
    lender = product["lender_actor_key"]
    ledger = post_ledger_transaction(
        conn,
        transaction_key=f"{contract_key}:origination",
        transaction_type="credit_origination",
        source_type="credit_contract",
        source_id=contract_key,
        occurred_at=now,
        description=f"{purpose} 信用合同放款",
        metadata={
            "credit_creation": False,
            "product_key": product_key,
            "guarantor_resident_id": guarantor_resident_id,
            "collateral": collateral or {},
        },
        entries=[
            {
                "account_key": f"{borrower}:cash",
                "entry_side": "debit",
                "amount_minor": amount_minor,
            },
            {
                "account_key": f"{lender}:loan_receivable",
                "entry_side": "debit",
                "amount_minor": amount_minor,
            },
            {
                "account_key": f"{lender}:cash",
                "entry_side": "credit",
                "amount_minor": amount_minor,
            },
            {
                "account_key": f"{borrower}:loan_payable",
                "entry_side": "credit",
                "amount_minor": amount_minor,
            },
        ],
    )
    maturity = now.date() + timedelta(days=int(product["term_days"]))
    next_due = now.date() + timedelta(days=int(product["payment_cadence_days"]))
    cursor = conn.execute(
        """
        INSERT INTO credit_contracts
        (contract_key, product_id, borrower_resident_id, lender_actor_key,
         guarantor_resident_id, principal_minor, outstanding_principal_minor,
         annual_interest_basis_points, penalty_interest_basis_points,
         originated_at, maturity_date, next_due_date, last_accrual_date,
         status, collateral_json, ledger_transaction_id, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            contract_key, product["id"], resident_id, lender,
            guarantor_resident_id, amount_minor, amount_minor,
            product["annual_interest_basis_points"],
            product["penalty_interest_basis_points"],
            now.isoformat(), maturity.isoformat(), next_due.isoformat(),
            now.date().isoformat(), _json(collateral or {}), ledger["id"],
            _json({"purpose": purpose, "rule_version": RULE_VERSION}),
        ),
    )
    contract_id = int(cursor.lastrowid)
    count = int(math.ceil(int(product["term_days"]) / int(product["payment_cadence_days"])))
    base_principal = amount_minor // count
    remainder = amount_minor - base_principal * count
    for sequence in range(1, count + 1):
        principal = base_principal + (1 if sequence <= remainder else 0)
        due = now.date() + timedelta(days=int(product["payment_cadence_days"]) * sequence)
        conn.execute(
            """
            INSERT INTO credit_installments
            (installment_key, contract_id, sequence_number, due_date,
             principal_due_minor, status)
            VALUES (?, ?, ?, ?, ?, 'scheduled')
            """,
            (
                f"{contract_key}:installment:{sequence}",
                contract_id, sequence, due.isoformat(), principal,
            ),
        )
    before_score = int(profile["credit_score"])
    before_limit = int(profile["credit_limit_minor"])
    conn.execute(
        """
        UPDATE credit_profiles
        SET outstanding_principal_minor = outstanding_principal_minor + ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (amount_minor, resident_id),
    )
    conn.execute(
        """
        INSERT INTO credit_events
        (event_key, resident_id, contract_id, event_type,
         score_before, score_after, credit_limit_before_minor,
         credit_limit_after_minor, details_json, occurred_at)
        VALUES (?, ?, ?, 'loan_originated', ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{contract_key}:event:originated", resident_id, contract_id,
            before_score, before_score, before_limit, before_limit,
            _json({"principal_minor": amount_minor, "purpose": purpose}),
            now.isoformat(),
        ),
    )
    _sync_budget_credit(conn, resident_id)
    return {
        **dict(
            conn.execute(
                "SELECT * FROM credit_contracts WHERE id = ?",
                (contract_id,),
            ).fetchone()
        ),
        "created": True,
    }


def accrue_contract_interest(conn, contract_id: int, world_time=None) -> dict:
    now = _now(world_time)
    contract = conn.execute(
        "SELECT * FROM credit_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    if not contract or contract["status"] not in {"active", "late", "restructured"}:
        return {"contract_id": contract_id, "interest_minor": 0, "skipped": True}
    last = date.fromisoformat(contract["last_accrual_date"])
    days = (now.date() - last).days
    if days <= 0 or int(contract["outstanding_principal_minor"]) <= 0:
        return {"contract_id": contract_id, "interest_minor": 0, "skipped": True}
    interest = round(
        int(contract["outstanding_principal_minor"])
        * int(contract["annual_interest_basis_points"])
        * days
        / 10000
        / 365
    )
    interest = max(1, interest)
    borrower = f"resident:{contract['borrower_resident_id']}"
    lender = contract["lender_actor_key"]
    ledger = post_ledger_transaction(
        conn,
        transaction_key=f"{contract['contract_key']}:interest:{now.date().isoformat()}",
        transaction_type="credit_interest_accrual",
        source_type="credit_contract",
        source_id=str(contract_id),
        occurred_at=now,
        description=f"信用合同累计 {days} 日利息",
        entries=[
            {
                "account_key": f"{lender}:interest_receivable",
                "entry_side": "debit",
                "amount_minor": interest,
            },
            {
                "account_key": f"{borrower}:interest_expense",
                "entry_side": "debit",
                "amount_minor": interest,
            },
            {
                "account_key": f"{borrower}:interest_payable",
                "entry_side": "credit",
                "amount_minor": interest,
            },
            {
                "account_key": f"{lender}:interest_income",
                "entry_side": "credit",
                "amount_minor": interest,
            },
        ],
    )
    installment = conn.execute(
        """
        SELECT id FROM credit_installments
        WHERE contract_id = ? AND status IN ('scheduled', 'due', 'partial', 'late')
        ORDER BY sequence_number LIMIT 1
        """,
        (contract_id,),
    ).fetchone()
    if installment:
        conn.execute(
            """
            UPDATE credit_installments
            SET interest_due_minor = interest_due_minor + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (interest, installment["id"]),
        )
    conn.execute(
        """
        UPDATE credit_contracts
        SET accrued_interest_minor = accrued_interest_minor + ?,
            last_accrual_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (interest, now.date().isoformat(), contract_id),
    )
    conn.execute(
        """
        UPDATE credit_profiles
        SET accrued_interest_minor = accrued_interest_minor + ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (interest, contract["borrower_resident_id"]),
    )
    profile = conn.execute(
        "SELECT credit_score, credit_limit_minor FROM credit_profiles WHERE resident_id = ?",
        (contract["borrower_resident_id"],),
    ).fetchone()
    conn.execute(
        """
        INSERT OR IGNORE INTO credit_events
        (event_key, resident_id, contract_id, event_type,
         score_before, score_after, credit_limit_before_minor,
         credit_limit_after_minor, details_json, occurred_at)
        VALUES (?, ?, ?, 'interest_accrued', ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{contract['contract_key']}:event:interest:{now.date().isoformat()}",
            contract["borrower_resident_id"], contract_id,
            profile["credit_score"], profile["credit_score"],
            profile["credit_limit_minor"], profile["credit_limit_minor"],
            _json({"days": days, "interest_minor": interest, "ledger_transaction_id": ledger["id"]}),
            now.isoformat(),
        ),
    )
    _sync_budget_credit(conn, int(contract["borrower_resident_id"]))
    return {
        "contract_id": contract_id,
        "interest_minor": interest,
        "ledger_transaction_id": int(ledger["id"]),
        "skipped": False,
    }


def pay_credit_installment(
    conn,
    *,
    contract_id: int,
    amount_minor: Optional[int] = None,
    world_time=None,
) -> dict:
    now = _now(world_time)
    contract = conn.execute(
        "SELECT * FROM credit_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    if not contract or contract["status"] not in {"active", "late", "restructured"}:
        raise ValueError("信用合同不可还款")
    installment = conn.execute(
        """
        SELECT * FROM credit_installments
        WHERE contract_id = ? AND status IN ('scheduled', 'due', 'partial', 'late')
        ORDER BY sequence_number LIMIT 1
        """,
        (contract_id,),
    ).fetchone()
    if not installment:
        raise ValueError("信用合同没有待付分期")
    principal_due = int(installment["principal_due_minor"]) - int(installment["principal_paid_minor"])
    interest_due = int(installment["interest_due_minor"]) - int(installment["interest_paid_minor"])
    penalty_due = int(installment["penalty_due_minor"]) - int(installment["penalty_paid_minor"])
    total_due = principal_due + interest_due + penalty_due
    cash = _account_balance(conn, f"resident:{contract['borrower_resident_id']}:cash")
    amount = min(total_due, cash, int(amount_minor) if amount_minor is not None else total_due)
    if amount <= 0:
        raise ValueError("借款人现金不足")
    remaining = amount
    interest_paid = min(interest_due, remaining)
    remaining -= interest_paid
    penalty_paid = min(penalty_due, remaining)
    remaining -= penalty_paid
    principal_paid = min(principal_due, remaining)
    entries = [
        {
            "account_key": f"{contract['lender_actor_key']}:cash",
            "entry_side": "debit",
            "amount_minor": amount,
        },
        {
            "account_key": f"resident:{contract['borrower_resident_id']}:cash",
            "entry_side": "credit",
            "amount_minor": amount,
        },
    ]
    if principal_paid:
        entries.extend([
            {
                "account_key": f"resident:{contract['borrower_resident_id']}:loan_payable",
                "entry_side": "debit",
                "amount_minor": principal_paid,
            },
            {
                "account_key": f"{contract['lender_actor_key']}:loan_receivable",
                "entry_side": "credit",
                "amount_minor": principal_paid,
            },
        ])
    if interest_paid:
        entries.extend([
            {
                "account_key": f"resident:{contract['borrower_resident_id']}:interest_payable",
                "entry_side": "debit",
                "amount_minor": interest_paid,
            },
            {
                "account_key": f"{contract['lender_actor_key']}:interest_receivable",
                "entry_side": "credit",
                "amount_minor": interest_paid,
            },
        ])
    if penalty_paid:
        entries.extend([
            {
                "account_key": f"resident:{contract['borrower_resident_id']}:penalty_payable",
                "entry_side": "debit",
                "amount_minor": penalty_paid,
            },
            {
                "account_key": f"{contract['lender_actor_key']}:penalty_receivable",
                "entry_side": "credit",
                "amount_minor": penalty_paid,
            },
        ])
    payment_key = f"{contract['contract_key']}:payment:{installment['sequence_number']}:{now.isoformat()}"
    ledger = post_ledger_transaction(
        conn,
        transaction_key=payment_key,
        transaction_type="credit_payment",
        source_type="credit_contract",
        source_id=str(contract_id),
        occurred_at=now,
        description="信用合同分期还款",
        metadata={
            "principal_minor": principal_paid,
            "interest_minor": interest_paid,
            "penalty_minor": penalty_paid,
        },
        entries=entries,
    )
    paid_in_full = amount == total_due
    conn.execute(
        """
        UPDATE credit_installments
        SET principal_paid_minor = principal_paid_minor + ?,
            interest_paid_minor = interest_paid_minor + ?,
            penalty_paid_minor = penalty_paid_minor + ?,
            status = ?, paid_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            principal_paid, interest_paid, penalty_paid,
            "paid" if paid_in_full else "partial",
            now.isoformat() if paid_in_full else "",
            installment["id"],
        ),
    )
    conn.execute(
        """
        UPDATE credit_contracts
        SET outstanding_principal_minor = outstanding_principal_minor - ?,
            accrued_interest_minor = CASE
                WHEN accrued_interest_minor > ?
                THEN accrued_interest_minor - ?
                ELSE 0
            END,
            next_due_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            principal_paid, interest_paid, interest_paid,
            (
                now.date() + timedelta(days=7)
            ).isoformat(),
            contract_id,
        ),
    )
    conn.execute(
        """
        UPDATE credit_profiles
        SET outstanding_principal_minor = outstanding_principal_minor - ?,
            accrued_interest_minor = CASE
                WHEN accrued_interest_minor > ?
                THEN accrued_interest_minor - ?
                ELSE 0
            END,
            credit_score = CASE
                WHEN credit_score + ? > 850 THEN 850
                ELSE credit_score + ?
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (
            principal_paid, interest_paid, interest_paid,
            5 if paid_in_full else 1,
            5 if paid_in_full else 1,
            contract["borrower_resident_id"],
        ),
    )
    cursor = conn.execute(
        """
        INSERT INTO credit_payments
        (payment_key, contract_id, installment_id, borrower_resident_id,
         principal_minor, interest_minor, penalty_minor, total_minor,
         ledger_transaction_id, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payment_key, contract_id, installment["id"],
            contract["borrower_resident_id"], principal_paid,
            interest_paid, penalty_paid, amount, ledger["id"],
            now.isoformat(),
        ),
    )
    remaining_contract = conn.execute(
        "SELECT outstanding_principal_minor, accrued_interest_minor FROM credit_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    if (
        int(remaining_contract["outstanding_principal_minor"]) == 0
        and int(remaining_contract["accrued_interest_minor"]) == 0
    ):
        conn.execute(
            "UPDATE credit_contracts SET status = 'paid', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (contract_id,),
        )
    profile = conn.execute(
        "SELECT credit_score, credit_limit_minor FROM credit_profiles WHERE resident_id = ?",
        (contract["borrower_resident_id"],),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO credit_events
        (event_key, resident_id, contract_id, event_type,
         score_before, score_after, credit_limit_before_minor,
         credit_limit_after_minor, details_json, occurred_at)
        VALUES (?, ?, ?, 'payment', ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{payment_key}:event", contract["borrower_resident_id"],
            contract_id,
            max(300, int(profile["credit_score"]) - (5 if paid_in_full else 1)),
            profile["credit_score"], profile["credit_limit_minor"],
            profile["credit_limit_minor"],
            _json({"payment_id": cursor.lastrowid, "total_minor": amount}),
            now.isoformat(),
        ),
    )
    _sync_budget_credit(conn, int(contract["borrower_resident_id"]))
    return {
        "payment_id": int(cursor.lastrowid),
        "ledger_transaction_id": int(ledger["id"]),
        "principal_minor": principal_paid,
        "interest_minor": interest_paid,
        "penalty_minor": penalty_paid,
        "total_minor": amount,
        "paid_in_full": paid_in_full,
    }


def _mark_contract_late_or_default(conn, contract, installment, now: datetime) -> str:
    product = conn.execute(
        "SELECT * FROM credit_products WHERE id = ?",
        (contract["product_id"],),
    ).fetchone()
    overdue_days = (now.date() - date.fromisoformat(installment["due_date"])).days
    if overdue_days <= int(product["grace_days"]):
        return contract["status"]
    borrower_id = int(contract["borrower_resident_id"])
    profile = conn.execute(
        "SELECT * FROM credit_profiles WHERE resident_id = ?",
        (borrower_id,),
    ).fetchone()
    event_type = "late"
    score_delta = -20
    status = "late"
    if overdue_days > int(product["grace_days"]) + int(product["payment_cadence_days"]):
        event_type = "default"
        score_delta = -80
        status = "defaulted"
    event_key = (
        f"{contract['contract_key']}:event:{event_type}:"
        f"{installment['sequence_number']}"
    )
    if conn.execute(
        "SELECT id FROM credit_events WHERE event_key = ?",
        (event_key,),
    ).fetchone():
        return status
    score_after = int(_clamp(int(profile["credit_score"]) + score_delta, 300, 850))
    limit_after = max(
        int(contract["outstanding_principal_minor"]),
        int(profile["credit_limit_minor"]) // (2 if status == "late" else 4),
    )
    if status == "defaulted":
        limit_after = int(contract["outstanding_principal_minor"])
    conn.execute(
        """
        UPDATE credit_contracts
        SET status = ?, defaulted_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            status,
            now.isoformat() if status == "defaulted" else "",
            contract["id"],
        ),
    )
    conn.execute(
        "UPDATE credit_installments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, installment["id"]),
    )
    conn.execute(
        """
        UPDATE credit_profiles
        SET credit_score = ?, credit_limit_minor = ?,
            delinquency_count = delinquency_count + 1,
            default_count = default_count + ?,
            status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (
            score_after, limit_after, int(status == "defaulted"),
            "defaulted" if status == "defaulted" else "restricted",
            borrower_id,
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO credit_events
        (event_key, resident_id, contract_id, event_type,
         score_before, score_after, credit_limit_before_minor,
         credit_limit_after_minor, details_json, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            borrower_id, contract["id"], event_type,
            profile["credit_score"], score_after,
            profile["credit_limit_minor"], limit_after,
            _json({"overdue_days": overdue_days, "installment_id": installment["id"]}),
            now.isoformat(),
        ),
    )
    _sync_budget_credit(conn, borrower_id)
    return status


def create_economic_shock(
    conn,
    *,
    resident_id: int,
    shock_type: str,
    severity: int,
    amount_minor: int,
    shock_key: str,
    world_time=None,
    source_type: str = "credit_runtime",
    source_id: str = "",
) -> dict:
    existing = conn.execute(
        "SELECT * FROM economic_shocks WHERE shock_key = ?",
        (shock_key,),
    ).fetchone()
    if existing:
        return {**dict(existing), "created": False}
    now = _now(world_time)
    cursor = conn.execute(
        """
        INSERT INTO economic_shocks
        (shock_key, resident_id, shock_type, severity, amount_minor,
         source_type, source_id, occurred_at, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shock_key, resident_id, shock_type,
            int(_clamp(severity, 1, 100)), int(amount_minor),
            source_type, source_id, now.isoformat(),
            _json({"rule_version": RULE_VERSION}),
        ),
    )
    return {
        **dict(
            conn.execute(
                "SELECT * FROM economic_shocks WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        ),
        "created": True,
    }


def settle_economic_shock(
    conn,
    *,
    shock_id: int,
    allow_credit: bool = False,
    world_time=None,
) -> dict:
    now = _now(world_time)
    shock = conn.execute(
        "SELECT * FROM economic_shocks WHERE id = ?",
        (shock_id,),
    ).fetchone()
    if not shock or shock["status"] != "pending":
        raise ValueError("经济冲击不存在或已经结算")
    resident_id = int(shock["resident_id"])
    risk = conn.execute(
        "SELECT * FROM household_risk_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    amount = int(shock["amount_minor"])
    remaining = amount
    reserve = conn.execute(
        "SELECT emergency_reserve_minor FROM household_budget_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    reserve_minor = int(reserve["emergency_reserve_minor"]) if reserve else 0
    cash = _account_balance(conn, f"resident:{resident_id}:cash")
    cash_used = min(remaining, max(0, cash - reserve_minor))
    if cash_used:
        post_money_transfer_minor(
            conn,
            transaction_key=f"{shock['shock_key']}:cash",
            from_account_key=f"resident:{resident_id}:cash",
            to_account_key="system:campus-services:cash",
            amount_minor=cash_used,
            transaction_type="economic_shock_expense",
            source_type="economic_shock",
            source_id=str(shock_id),
            description=f"{shock['shock_type']} 冲击现金支出",
        )
        remaining -= cash_used
    savings = _savings_balance(conn, resident_id)
    savings_used = min(remaining, savings)
    if savings_used:
        ledger = post_money_transfer_minor(
            conn,
            transaction_key=f"{shock['shock_key']}:savings",
            from_account_key=f"resident:{resident_id}:savings",
            to_account_key="system:campus-services:cash",
            amount_minor=savings_used,
            transaction_type="economic_shock_savings",
            source_type="economic_shock",
            source_id=str(shock_id),
            description=f"{shock['shock_type']} 冲击动用应急储蓄",
        )
        if conn.execute("PRAGMA table_info(savings_transfers)").fetchall():
            conn.execute(
                """
                INSERT OR IGNORE INTO savings_transfers
                (transfer_key, resident_id, direction, amount_minor, reason,
                 ledger_transaction_id, occurred_at, metadata_json)
                VALUES (?, ?, 'withdrawal', ?, 'economic_shock', ?, ?, ?)
                """,
                (
                    f"{shock['shock_key']}:savings", resident_id,
                    savings_used, ledger["id"], now.isoformat(),
                    _json({"shock_id": shock_id, "shock_type": shock["shock_type"]}),
                ),
            )
        remaining -= savings_used
    requested_claim = remaining
    claim_paid = 0
    claim_status = "rejected"
    if risk and int(risk["mutual_aid_enrolled"]) and remaining:
        approved = min(
            remaining,
            int(risk["coverage_limit_minor"]),
            amount * int(risk["coverage_basis_points"]) // 10000,
            _account_balance(conn, CREDIT_UNION_CASH),
        )
        if approved:
            payout = post_money_transfer_minor(
                conn,
                transaction_key=f"{shock['shock_key']}:risk-payout",
                from_account_key=CREDIT_UNION_CASH,
                to_account_key=f"resident:{resident_id}:cash",
                amount_minor=approved,
                transaction_type="mutual_aid_claim",
                source_type="economic_shock",
                source_id=str(shock_id),
                description="风险共济池支付已批准赔付",
            )
            post_money_transfer_minor(
                conn,
                transaction_key=f"{shock['shock_key']}:risk-expense",
                from_account_key=f"resident:{resident_id}:cash",
                to_account_key="system:campus-services:cash",
                amount_minor=approved,
                transaction_type="economic_shock_expense",
                source_type="economic_shock",
                source_id=str(shock_id),
                description="风险赔付用于冲击支出",
            )
            claim_paid = approved
            remaining -= approved
            claim_status = "approved" if approved == requested_claim else "partial"
            claim_ledger_id = payout["id"]
        else:
            claim_ledger_id = None
    else:
        claim_ledger_id = None
    if requested_claim:
        conn.execute(
            """
            INSERT OR IGNORE INTO risk_pool_claims
            (claim_key, shock_id, resident_id, requested_minor,
             approved_minor, status, ledger_transaction_id, reason, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"claim:{shock['shock_key']}", shock_id, resident_id,
                requested_claim, claim_paid, claim_status, claim_ledger_id,
                (
                    "按覆盖比例与共济池余额结算"
                    if claim_paid else "未参保、超出覆盖范围或共济池余额不足"
                ),
                now.isoformat(),
            ),
        )
    credit_used = 0
    if allow_credit and remaining:
        credit = available_credit(conn, resident_id)
        draw = min(
            remaining,
            int(credit["available_credit_minor"]),
            10000,
        )
        if draw >= 100:
            originate_credit(
                conn,
                resident_id=resident_id,
                amount_minor=draw,
                purpose=f"economic_shock:{shock['shock_type']}",
                world_time=now,
                contract_key=f"credit-shock:{shock['shock_key']}",
            )
            post_money_transfer_minor(
                conn,
                transaction_key=f"{shock['shock_key']}:credit-expense",
                from_account_key=f"resident:{resident_id}:cash",
                to_account_key="system:campus-services:cash",
                amount_minor=draw,
                transaction_type="economic_shock_expense",
                source_type="economic_shock",
                source_id=str(shock_id),
                description="应急信用用于冲击支出",
            )
            credit_used = draw
            remaining -= draw
    credit = available_credit(conn, resident_id)
    savings_after = _savings_balance(conn, resident_id)
    debt_pressure = (
        int(credit["outstanding_principal_minor"])
        / max(100, savings_after + reserve_minor)
    )
    impact = int(_clamp(
        int(shock["severity"]) * 0.35
        + (remaining / amount) * 50
        + min(15, debt_pressure * 5),
        0,
        100,
    ))
    status = (
        "settled"
        if remaining == 0
        else ("partially_covered" if remaining < amount else "uncovered")
    )
    conn.execute(
        """
        UPDATE economic_shocks
        SET cash_used_minor = ?, savings_used_minor = ?,
            risk_pool_paid_minor = ?, credit_used_minor = ?,
            uncovered_minor = ?, impact_score = ?, status = ?,
            settled_at = ?, details_json = ?
        WHERE id = ?
        """,
        (
            cash_used, savings_used, claim_paid, credit_used,
            remaining, impact, status, now.isoformat(),
            _json({
                "debt_pressure": round(debt_pressure, 4),
                "savings_after_minor": savings_after,
                "credit_status": credit["status"],
            }),
            shock_id,
        ),
    )
    return {
        "shock_id": shock_id,
        "status": status,
        "cash_used_minor": cash_used,
        "savings_used_minor": savings_used,
        "risk_pool_paid_minor": claim_paid,
        "credit_used_minor": credit_used,
        "uncovered_minor": remaining,
        "impact_score": impact,
    }


def _maybe_create_daily_shocks(conn, now: datetime) -> list[int]:
    created = []
    runtime = conn.execute(
        "SELECT random_seed FROM world_runtime WHERE id = 1"
    ).fetchone()
    seed = runtime["random_seed"] if runtime else "campus-default-seed-v1"
    profiles = conn.execute(
        "SELECT * FROM household_risk_profiles WHERE status = 'active' ORDER BY resident_id"
    ).fetchall()
    for profile in profiles:
        key = f"shock:auto:{profile['resident_id']}:{now.date().isoformat()}"
        if conn.execute(
            "SELECT id FROM economic_shocks WHERE shock_key = ?",
            (key,),
        ).fetchone():
            continue
        digest = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest()
        roll = int.from_bytes(digest[:8], "big") / float(2**64)
        probability = int(profile["risk_score"]) / 10000
        if roll >= probability:
            continue
        selector = digest[8] % 4
        shock_type = (
            "income_loss", "medical", "essential_repair", "family_emergency"
        )[selector]
        severity = 35 + digest[9] % 51
        amount = 500 + (int.from_bytes(digest[10:12], "big") % 2501)
        shock = create_economic_shock(
            conn,
            resident_id=int(profile["resident_id"]),
            shock_type=shock_type,
            severity=severity,
            amount_minor=amount,
            shock_key=key,
            world_time=now,
            source_type="deterministic_daily_risk",
            source_id=now.date().isoformat(),
        )
        settle_economic_shock(
            conn,
            shock_id=int(shock["id"]),
            allow_credit=severity >= 75,
            world_time=now,
        )
        created.append(int(shock["id"]))
    return created


def _refresh_savings_goals(conn) -> None:
    """Refresh goals safely even when a legacy database has duplicate accounts."""
    conn.execute(
        """
        UPDATE savings_goals
        SET current_amount_minor = COALESCE((
                SELECT MAX(account.balance_minor)
                FROM ledger_accounts account
                JOIN economic_actors actor ON actor.id = account.actor_id
                WHERE actor.resident_id = savings_goals.resident_id
                  AND account.account_code = 'savings'
            ), 0),
            status = CASE
                WHEN COALESCE((
                    SELECT MAX(account.balance_minor)
                    FROM ledger_accounts account
                    JOIN economic_actors actor ON actor.id = account.actor_id
                    WHERE actor.resident_id = savings_goals.resident_id
                      AND account.account_code = 'savings'
                ), 0) >= target_amount_minor
                THEN 'achieved' ELSE status
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('active', 'achieved')
        """
    )


def process_credit_runtime(conn, world_time=None) -> dict:
    if not credit_runtime_available(conn):
        return {
            "available": False,
            "interest_accruals": [],
            "payments": [],
            "late": [],
            "defaulted": [],
            "shocks": [],
        }
    now = _now(world_time)
    _refresh_savings_goals(conn)
    accruals = []
    payments = []
    late = []
    defaulted = []
    contracts = conn.execute(
        """
        SELECT * FROM credit_contracts
        WHERE status IN ('active', 'late', 'restructured')
        ORDER BY id
        """
    ).fetchall()
    for contract in contracts:
        accrual = accrue_contract_interest(conn, int(contract["id"]), now)
        if not accrual["skipped"]:
            accruals.append(accrual)
        current = conn.execute(
            "SELECT * FROM credit_contracts WHERE id = ?",
            (contract["id"],),
        ).fetchone()
        installment = conn.execute(
            """
            SELECT * FROM credit_installments
            WHERE contract_id = ? AND status IN ('scheduled', 'due', 'partial', 'late')
            ORDER BY sequence_number LIMIT 1
            """,
            (contract["id"],),
        ).fetchone()
        if not installment or date.fromisoformat(installment["due_date"]) > now.date():
            continue
        conn.execute(
            """
            UPDATE credit_installments
            SET status = CASE WHEN status = 'scheduled' THEN 'due' ELSE status END
            WHERE id = ?
            """,
            (installment["id"],),
        )
        total_due = (
            int(installment["principal_due_minor"])
            - int(installment["principal_paid_minor"])
            + int(installment["interest_due_minor"])
            - int(installment["interest_paid_minor"])
            + int(installment["penalty_due_minor"])
            - int(installment["penalty_paid_minor"])
        )
        reserve = conn.execute(
            "SELECT emergency_reserve_minor FROM household_budget_profiles WHERE resident_id = ?",
            (current["borrower_resident_id"],),
        ).fetchone()
        reserve_minor = int(reserve["emergency_reserve_minor"]) if reserve else 0
        available = max(
            0,
            _account_balance(conn, f"resident:{current['borrower_resident_id']}:cash")
            - reserve_minor,
        )
        if available > 0 and total_due > 0:
            payments.append(
                pay_credit_installment(
                    conn,
                    contract_id=int(current["id"]),
                    amount_minor=min(available, total_due),
                    world_time=now,
                )
            )
            installment = conn.execute(
                "SELECT * FROM credit_installments WHERE id = ?",
                (installment["id"],),
            ).fetchone()
        if installment["status"] != "paid":
            status = _mark_contract_late_or_default(
                conn, current, installment, now
            )
            if status == "late":
                late.append(int(current["id"]))
            elif status == "defaulted":
                defaulted.append(int(current["id"]))
    shocks = _maybe_create_daily_shocks(conn, now)
    for profile in conn.execute("SELECT resident_id FROM credit_profiles").fetchall():
        _sync_budget_credit(conn, int(profile["resident_id"]))
    return {
        "available": True,
        "interest_accruals": accruals,
        "payments": payments,
        "late": late,
        "defaulted": defaulted,
        "shocks": shocks,
        "credit_union_cash_minor": _account_balance(conn, CREDIT_UNION_CASH),
        "rule_version": RULE_VERSION,
    }
