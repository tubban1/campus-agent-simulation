from __future__ import annotations

import json
from typing import Optional

from app.json_utils import json_dumps
from datetime import date, datetime, timedelta, timezone

from app.economy.service import (
    ensure_ledger_account,
    post_authorized_balance_change,
    post_ledger_transaction,
    post_money_transfer_minor,
)
from app.organizations.service import organization_budget_state


POSITION_DEFAULTS = (
    ("student-affairs-assistant", "学生会", "学生事务助理", "校务处", ["collaborate", "observe"], "time_management", 45, 2, 900, 120),
    ("innovation-project-assistant", "创新社", "项目研究助理", "教学楼", ["collaborate", "attend_class"], "information_literacy", 50, 3, 1100, 120),
    ("merchant-service-shift", "校园商户联盟", "商户服务班次", "商业街", ["collaborate", "consume"], "economic_access", 45, 3, 1000, 180),
    ("library-service-shift", "图书馆服务组", "图书馆服务班次", "图书馆", ["observe", "collaborate"], "rule_adherence", 50, 3, 950, 180),
)

CONTRACT_DEFAULTS = (
    ("student-affairs-assistant", "苏晴", "part_time"),
    ("student-affairs-assistant", "白露", "part_time"),
    ("innovation-project-assistant", "乔安然", "assistantship"),
    ("innovation-project-assistant", "韩墨", "assistantship"),
    ("merchant-service-shift", "李姐", "staff"),
    ("merchant-service-shift", "秦越", "part_time"),
    ("library-service-shift", "何管理员", "staff"),
    ("library-service-shift", "校园后勤", "staff"),
)


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _json(value) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def labor_runtime_available(conn) -> bool:
    return bool(conn.execute("PRAGMA table_info(labor_positions)").fetchall())


def _actor_cash(conn, actor_key: str):
    return conn.execute(
        """
        SELECT account.* FROM ledger_accounts account
        JOIN economic_actors actor ON actor.id = account.actor_id
        WHERE actor.actor_key = ? AND account.account_code = 'cash'
          AND account.status = 'active'
        """,
        (actor_key,),
    ).fetchone()


def _post_income_transaction(
    conn,
    *,
    transaction_key: str,
    payment_type: str,
    payer_actor_key: str,
    recipient_actor_key: str,
    amount_minor: int,
    source_type: str,
    source_id: str,
    metadata: dict,
):
    payer_cash = _actor_cash(conn, payer_actor_key)
    recipient_cash = _actor_cash(conn, recipient_actor_key)
    if not payer_cash or not recipient_cash:
        raise ValueError("支付方或收款方没有有效现金账户")
    if int(payer_cash["balance_minor"]) < int(amount_minor):
        raise ValueError("支付方预算不足")
    payer_expense = ensure_ledger_account(
        conn, actor_key=payer_actor_key,
        account_code=f"{payment_type}_expense",
        account_type="expense", normal_side="debit",
    )
    recipient_income = ensure_ledger_account(
        conn, actor_key=recipient_actor_key,
        account_code=f"{payment_type}_income",
        account_type="income", normal_side="credit",
    )
    return post_ledger_transaction(
        conn,
        transaction_key=transaction_key,
        transaction_type=payment_type,
        source_type=source_type,
        source_id=source_id,
        description=f"{payer_actor_key} 向 {recipient_actor_key} 支付 {payment_type}",
        metadata=metadata,
        entries=[
            {"account_key": recipient_cash["account_key"], "entry_side": "debit", "amount_minor": amount_minor},
            {"account_key": payer_expense["account_key"], "entry_side": "debit", "amount_minor": amount_minor},
            {"account_key": payer_cash["account_key"], "entry_side": "credit", "amount_minor": amount_minor},
            {"account_key": recipient_income["account_key"], "entry_side": "credit", "amount_minor": amount_minor},
        ],
    )


def _organization_by_name(conn, name: str):
    return conn.execute(
        "SELECT id FROM campus_organizations WHERE name = ?",
        (name,),
    ).fetchone()


def _capability_score(conn, resident_id: int, dimension: str) -> int:
    allowed = {
        "physical_endurance", "time_management", "risk_tolerance",
        "rule_adherence", "information_literacy", "economic_access",
        "social_capital", "institutional_access", "language_access",
        "stress_resilience",
    }
    if dimension not in allowed:
        raise ValueError("职位使用了未知能力维度")
    row = conn.execute(
        f"SELECT {dimension} AS value FROM agent_capability_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    return int(row["value"]) if row else 50


def _runtime_world_date(conn) -> date:
    if conn.execute("PRAGMA table_info(world_runtime)").fetchall():
        row = conn.execute(
            "SELECT world_time FROM world_runtime ORDER BY id LIMIT 1"
        ).fetchone()
        if row and row["world_time"]:
            return _now(row["world_time"]).date()
    return _now().date()


def seed_labor_runtime(conn, world_date: Optional[date] = None) -> dict:
    today = world_date or _runtime_world_date(conn)
    for (
        key, organization_name, title, location, actions, skill_dimension,
        minimum_skill, capacity, wage, daily_minutes,
    ) in POSITION_DEFAULTS:
        organization = _organization_by_name(conn, organization_name)
        if not organization:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO labor_positions
            (position_key, organization_id, title, location,
             allowed_actions_json, skill_dimension, minimum_skill, capacity,
             hourly_wage_minor, standard_daily_minutes, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key, organization["id"], title, location, _json(actions),
                skill_dimension, minimum_skill, capacity, wage, daily_minutes,
                _json({"seed_version": "labor-runtime-v1"}),
            ),
        )
    contracts_created = 0
    for position_key, resident_name, contract_type in CONTRACT_DEFAULTS:
        position = conn.execute(
            "SELECT * FROM labor_positions WHERE position_key = ?",
            (position_key,),
        ).fetchone()
        resident = conn.execute(
            "SELECT id FROM residents WHERE name = ?",
            (resident_name,),
        ).fetchone()
        if not position or not resident:
            continue
        score = _capability_score(
            conn, int(resident["id"]), position["skill_dimension"]
        )
        if score < int(position["minimum_skill"]):
            continue
        wage = round(
            int(position["hourly_wage_minor"])
            * min(1.20, 1 + (score - int(position["minimum_skill"])) / 500)
        )
        before = conn.execute(
            "SELECT id FROM employment_contracts WHERE position_id = ? AND resident_id = ?",
            (position["id"], resident["id"]),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO employment_contracts
            (contract_key, position_id, resident_id, contract_type,
             hourly_wage_minor, scheduled_daily_minutes, start_date,
             skill_score_at_hire, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"contract:{position_key}:resident:{resident['id']}",
                position["id"], resident["id"], contract_type, wage,
                position["standard_daily_minutes"], today.isoformat(), score,
                _json({"wage_basis": "position_rate_and_skill"}),
            ),
        )
        contracts_created += int(before is None)
    _seed_income_programs_and_expenses(conn, today)
    counts = {}
    for table in (
        "labor_positions", "employment_contracts", "income_programs",
        "expense_obligations",
    ):
        counts[table] = int(
            conn.execute(f"SELECT COUNT(*) value FROM {table}").fetchone()["value"]
        )
    return {**counts, "contracts_created": contracts_created}


def _seed_income_programs_and_expenses(conn, today: date) -> None:
    student_union = _organization_by_name(conn, "学生会")
    if student_union:
        for key, kind, resident_name, amount, eligibility in (
            ("merit-scholarship-meng", "scholarship", "孟雨桐", 1800, "information_literacy>=55"),
            ("need-aid-lu", "financial_aid", "陆子昂", 1200, "economic_access<55"),
        ):
            resident = conn.execute(
                "SELECT id FROM residents WHERE name = ?", (resident_name,)
            ).fetchone()
            if resident:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO income_programs
                    (program_key, program_type, payer_actor_key,
                     recipient_resident_id, amount_minor, cadence_days,
                     next_due_date, eligibility_rule, metadata_json)
                    VALUES (?, ?, ?, ?, ?, 30, ?, ?, ?)
                    """,
                    (
                        key, kind, f"organization:{student_union['id']}",
                        resident["id"], amount,
                        (today + timedelta(days=30)).isoformat(), eligibility,
                        _json({"award_source": "student_union_program"}),
                    ),
                )
    for resident_name, amount in (("林小夏", 1000), ("顾南星", 800)):
        resident = conn.execute(
            "SELECT id FROM residents WHERE name = ?", (resident_name,)
        ).fetchone()
        if resident:
            conn.execute(
                """
                INSERT OR IGNORE INTO income_programs
                (program_key, program_type, payer_actor_key,
                 recipient_resident_id, amount_minor, cadence_days,
                 next_due_date, eligibility_rule, metadata_json)
                VALUES (?, 'family_support', 'external:outside-world', ?,
                        ?, 7, ?, 'active_student', ?)
                """,
                (
                    f"family-support:resident:{resident['id']}",
                    resident["id"], amount,
                    (today + timedelta(days=7)).isoformat(),
                    _json({"source": "family_economy"}),
                ),
            )
    students = conn.execute(
        "SELECT id FROM residents WHERE role LIKE '%学生%' OR role IN ('研究生', '心理委员', '学生会干部')"
    ).fetchall()
    for resident in students:
        for expense_type, amount, priority in (
            ("housing", 500, 90),
            ("study", 200, 60),
            ("transport", 100, 40),
        ):
            conn.execute(
                """
                INSERT OR IGNORE INTO expense_obligations
                (obligation_key, resident_id, expense_type,
                 recipient_actor_key, amount_minor, cadence_days,
                 next_due_date, priority, metadata_json)
                VALUES (?, ?, ?, 'system:campus-services', ?, 7, ?, ?, ?)
                """,
                (
                    f"{expense_type}:resident:{resident['id']}",
                    resident["id"], expense_type, amount,
                    (today + timedelta(days=7)).isoformat(), priority,
                    _json({"basis": "baseline_student_cost"}),
                ),
            )


def _create_daily_shifts(conn, work_date: date) -> list[int]:
    created = []
    contracts = conn.execute(
        """
        SELECT contract.* FROM employment_contracts contract
        WHERE contract.status = 'active' AND contract.start_date <= ?
          AND (contract.end_date = '' OR contract.end_date >= ?)
        ORDER BY contract.id
        """,
        (work_date.isoformat(), work_date.isoformat()),
    ).fetchall()
    for contract in contracts:
        existing = conn.execute(
            "SELECT id FROM labor_shifts WHERE contract_id = ? AND work_date = ?",
            (contract["id"], work_date.isoformat()),
        ).fetchone()
        if existing:
            continue
        cursor = conn.execute(
            """
            INSERT INTO labor_shifts
            (shift_key, contract_id, work_date, scheduled_minutes)
            VALUES (?, ?, ?, ?)
            """,
            (
                f"shift:{contract['id']}:{work_date.isoformat()}",
                contract["id"], work_date.isoformat(),
                contract["scheduled_daily_minutes"],
            ),
        )
        created.append(int(cursor.lastrowid))
    return created


def _shift_evidence(conn, shift) -> tuple[int, list[dict]]:
    actions = json.loads(shift["allowed_actions_json"] or "[]")
    if not actions:
        return 0, []
    placeholders = ", ".join("?" for _ in actions)
    start = f"{shift['work_date']}T00:00:00"
    end = f"{(date.fromisoformat(shift['work_date']) + timedelta(days=1)).isoformat()}T00:00:00"
    rows = conn.execute(
        f"""
        SELECT id, action_type, location, duration_minutes, occurred_at
        FROM world_action_executions
        WHERE resident_id = ? AND status = 'completed'
          AND occurred_at >= ? AND occurred_at < ?
          AND location = ? AND action_type IN ({placeholders})
        ORDER BY id
        """,
        (
            shift["resident_id"], start, end, shift["location"], *actions,
        ),
    ).fetchall()
    return (
        sum(max(0, int(row["duration_minutes"])) for row in rows),
        [dict(row) for row in rows],
    )


def _settle_shift(conn, shift, now: datetime) -> dict:
    evidenced, evidence = _shift_evidence(conn, shift)
    productivity = min(
        1.20,
        max(
            0.75,
            1 + (
                int(shift["current_skill"]) - int(shift["minimum_skill"])
            ) / 500,
        ),
    )
    payable = min(
        int(shift["scheduled_minutes"]),
        round(evidenced * productivity),
    )
    if payable <= 0:
        conn.execute(
            """
            UPDATE labor_shifts
            SET status = 'absent', evidenced_minutes = 0,
                evidence_json = ?, failure_reason = 'no_work_evidence',
                processed_at = ?
            WHERE id = ?
            """,
            (_json(evidence), now.isoformat(), shift["id"]),
        )
        return {"shift_id": int(shift["id"]), "status": "absent"}
    amount = round(int(shift["hourly_wage_minor"]) * payable / 60)
    payer_actor_key = f"organization:{shift['organization_id']}"
    recipient_actor_key = f"resident:{shift['resident_id']}"
    try:
        budget = organization_budget_state(conn, int(shift["organization_id"]))
        if amount > int(budget["available_minor"]):
            raise ValueError("组织工资预算不足")
        ledger = _post_income_transaction(
            conn,
            transaction_key=f"wage:{shift['shift_key']}",
            payment_type="wage",
            payer_actor_key=payer_actor_key,
            recipient_actor_key=recipient_actor_key,
            amount_minor=amount,
            source_type="labor_shift",
            source_id=str(shift["id"]),
            metadata={
                "minutes": payable,
                "skill_score": shift["current_skill"],
                "position": shift["title"],
            },
        )
    except ValueError as exc:
        conn.execute(
            """
            UPDATE labor_shifts
            SET status = 'blocked', evidenced_minutes = ?,
                payable_minutes = ?, gross_pay_minor = ?,
                evidence_json = ?, failure_reason = ?, processed_at = ?
            WHERE id = ?
            """,
            (
                evidenced, payable, amount, _json(evidence), str(exc),
                now.isoformat(), shift["id"],
            ),
        )
        _record_payment(
            conn, payment_key=f"wage:{shift['shift_key']}",
            payment_type="wage", payer_actor_key=payer_actor_key,
            recipient_actor_key=recipient_actor_key, amount_minor=amount,
            due_date=shift["work_date"], status="blocked",
            labor_shift_id=shift["id"], failure_reason=str(exc),
        )
        return {"shift_id": int(shift["id"]), "status": "blocked", "reason": str(exc)}
    status = "completed" if payable >= int(shift["scheduled_minutes"]) else "partial"
    conn.execute(
        """
        UPDATE labor_shifts
        SET status = ?, evidenced_minutes = ?, payable_minutes = ?,
            gross_pay_minor = ?, evidence_json = ?,
            ledger_transaction_id = ?, processed_at = ?
        WHERE id = ?
        """,
        (
            status, evidenced, payable, amount, _json(evidence),
            ledger["id"], now.isoformat(), shift["id"],
        ),
    )
    _record_payment(
        conn, payment_key=f"wage:{shift['shift_key']}",
        payment_type="wage", payer_actor_key=payer_actor_key,
        recipient_actor_key=recipient_actor_key, amount_minor=amount,
        due_date=shift["work_date"], status="posted",
        labor_shift_id=shift["id"], ledger_transaction_id=ledger["id"],
        paid_at=now.isoformat(),
    )
    return {
        "shift_id": int(shift["id"]),
        "status": status,
        "amount_minor": amount,
        "payable_minutes": payable,
    }


def _record_payment(
    conn,
    *,
    payment_key: str,
    payment_type: str,
    payer_actor_key: str,
    recipient_actor_key: str,
    amount_minor: int,
    due_date: str,
    status: str,
    labor_shift_id=None,
    income_program_id=None,
    ledger_transaction_id=None,
    paid_at="",
    failure_reason="",
    metadata=None,
):
    conn.execute(
        """
        INSERT OR IGNORE INTO income_payments
        (payment_key, payment_type, payer_actor_key, recipient_actor_key,
         amount_minor, labor_shift_id, income_program_id, status,
         ledger_transaction_id, due_date, paid_at, failure_reason,
         metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payment_key, payment_type, payer_actor_key, recipient_actor_key,
            amount_minor, labor_shift_id, income_program_id, status,
            ledger_transaction_id, due_date, paid_at, failure_reason,
            _json(metadata or {}),
        ),
    )


def _process_income_program(conn, program, now: datetime) -> dict:
    recipient = f"resident:{program['recipient_resident_id']}"
    key = f"program:{program['id']}:{program['next_due_date']}"
    try:
        if program["program_type"] == "family_support":
            if int(program["amount_minor"]) % 100:
                raise ValueError("外部家庭支持必须使用完整校园币单位")
            ledger = post_authorized_balance_change(
                conn, transaction_key=key, operation_type="external_inflow",
                authorization_rule_key="external-inflow-v1",
                authority_actor_key="system:ledger-controller",
                target_account_key=f"{recipient}:cash",
                amount_coins=int(program["amount_minor"]) // 100,
                source_type="income_program", source_id=str(program["id"]),
                description="可追溯家庭经济支持",
                metadata={"payer_actor_key": program["payer_actor_key"]},
            )
        else:
            ledger = _post_income_transaction(
                conn, transaction_key=key,
                payment_type=program["program_type"],
                payer_actor_key=program["payer_actor_key"],
                recipient_actor_key=recipient,
                amount_minor=int(program["amount_minor"]),
                source_type="income_program", source_id=str(program["id"]),
                metadata={"eligibility_rule": program["eligibility_rule"]},
            )
        status, reason = "posted", ""
    except ValueError as exc:
        ledger, status, reason = None, "blocked", str(exc)
    _record_payment(
        conn, payment_key=key, payment_type=program["program_type"],
        payer_actor_key=program["payer_actor_key"],
        recipient_actor_key=recipient, amount_minor=program["amount_minor"],
        due_date=program["next_due_date"], status=status,
        income_program_id=program["id"],
        ledger_transaction_id=ledger["id"] if ledger else None,
        paid_at=now.isoformat() if ledger else "", failure_reason=reason,
    )
    if ledger:
        next_due = (
            date.fromisoformat(program["next_due_date"])
            + timedelta(days=int(program["cadence_days"]))
        )
        conn.execute(
            "UPDATE income_programs SET next_due_date = ? WHERE id = ?",
            (next_due.isoformat(), program["id"]),
        )
    return {"program_id": int(program["id"]), "status": status, "reason": reason}


def _process_expense(conn, obligation, now: datetime) -> dict:
    payer = f"resident:{obligation['resident_id']}"
    key = f"expense:{obligation['id']}:{obligation['next_due_date']}"
    try:
        ledger = post_money_transfer_minor(
            conn, transaction_key=key,
            from_account_key=f"{payer}:cash",
            to_account_key=f"{obligation['recipient_actor_key']}:cash",
            amount_minor=int(obligation["amount_minor"]),
            transaction_type=f"required_{obligation['expense_type']}",
            source_type="expense_obligation", source_id=str(obligation["id"]),
            description=f"居民周期必要支出：{obligation['expense_type']}",
            metadata={"priority": obligation["priority"]},
        )
    except ValueError as exc:
        conn.execute(
            """
            UPDATE expense_obligations
            SET last_attempt_date = ?, failure_reason = ?
            WHERE id = ?
            """,
            (now.date().isoformat(), str(exc), obligation["id"]),
        )
        return {
            "obligation_id": int(obligation["id"]),
            "status": "blocked",
            "reason": str(exc),
        }
    next_due = (
        date.fromisoformat(obligation["next_due_date"])
        + timedelta(days=int(obligation["cadence_days"]))
    )
    conn.execute(
        """
        UPDATE expense_obligations
        SET next_due_date = ?, last_ledger_transaction_id = ?,
            last_attempt_date = ?, failure_reason = ''
        WHERE id = ?
        """,
        (
            next_due.isoformat(), ledger["id"], now.date().isoformat(),
            obligation["id"],
        ),
    )
    return {
        "obligation_id": int(obligation["id"]),
        "status": "posted",
        "ledger_transaction_id": ledger["id"],
    }


def process_labor_runtime(conn, world_time=None) -> dict:
    if not labor_runtime_available(conn):
        return {
            "available": False, "shifts_created": [], "shifts_settled": [],
            "income_payments": [], "expenses": [],
        }
    now = _now(world_time)
    today = now.date()
    created = _create_daily_shifts(conn, today)
    due_shifts = conn.execute(
        """
        SELECT shift.*, contract.resident_id, contract.hourly_wage_minor,
               position.organization_id, position.title, position.location,
               position.allowed_actions_json, position.skill_dimension,
               position.minimum_skill
        FROM labor_shifts shift
        JOIN employment_contracts contract ON contract.id = shift.contract_id
        JOIN labor_positions position ON position.id = contract.position_id
        WHERE shift.status = 'scheduled' AND shift.work_date < ?
        ORDER BY shift.work_date, shift.id
        """,
        (today.isoformat(),),
    ).fetchall()
    settled = []
    for raw in due_shifts:
        shift = dict(raw)
        shift["current_skill"] = _capability_score(
            conn, int(shift["resident_id"]), shift["skill_dimension"]
        )
        settled.append(_settle_shift(conn, shift, now))
    programs = conn.execute(
        """
        SELECT * FROM income_programs
        WHERE status = 'active' AND next_due_date <= ?
        ORDER BY next_due_date, id
        """,
        (today.isoformat(),),
    ).fetchall()
    incomes = [_process_income_program(conn, row, now) for row in programs]
    obligations = conn.execute(
        """
        SELECT * FROM expense_obligations
        WHERE status = 'active' AND next_due_date <= ?
          AND last_attempt_date <> ?
        ORDER BY priority DESC, id
        """,
        (today.isoformat(), today.isoformat()),
    ).fetchall()
    expenses = [_process_expense(conn, row, now) for row in obligations]
    return {
        "available": True,
        "shifts_created": created,
        "shifts_settled": settled,
        "income_payments": incomes,
        "expenses": expenses,
    }


def income_distribution_summary(conn) -> dict:
    rows = conn.execute(
        """
        SELECT resident.id AS resident_id, resident.name,
               cash.balance_minor AS cash_minor,
               COALESCE(SUM(
                   CASE WHEN payment.status = 'posted'
                        THEN payment.amount_minor ELSE 0 END
               ), 0) AS recorded_income_minor
        FROM residents resident
        JOIN economic_actors actor ON actor.resident_id = resident.id
        JOIN ledger_accounts cash
          ON cash.actor_id = actor.id AND cash.account_code = 'cash'
        LEFT JOIN income_payments payment
          ON payment.recipient_actor_key = actor.actor_key
        GROUP BY resident.id, cash.id
        ORDER BY resident.id
        """
    ).fetchall()
    balances = sorted(max(0, int(row["cash_minor"])) for row in rows)
    total = sum(balances)
    count = len(balances)
    if not count or not total:
        gini = 0.0
    else:
        weighted = sum(
            (index + 1) * value for index, value in enumerate(balances)
        )
        gini = (2 * weighted) / (count * total) - (count + 1) / count
    return {
        "currency": "campus_coin",
        "population": count,
        "cash_gini": round(max(0.0, min(1.0, gini)), 6),
        "total_cash_minor": total,
        "residents": [dict(row) for row in rows],
        "source": "ledger_accounts+income_payments",
    }
