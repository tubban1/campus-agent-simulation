from __future__ import annotations

from app.json_utils import json_dumps
from datetime import date, datetime, timedelta, timezone

from app.economy.service import (
    ensure_ledger_account,
    post_money_transfer_minor,
)
from app.credit.service import (
    available_credit,
    credit_runtime_available,
    originate_credit,
)


RULE_VERSION = "budget-choice-v1"


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _json(value) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def budget_runtime_available(conn) -> bool:
    return bool(conn.execute("PRAGMA table_info(household_budget_profiles)").fetchall())


def _account_balance(conn, resident_id: int, account_code: str) -> int:
    row = conn.execute(
        """
        SELECT account.balance_minor
        FROM ledger_accounts account
        JOIN economic_actors actor ON actor.id = account.actor_id
        WHERE actor.resident_id = ? AND account.account_code = ?
          AND account.status = 'active'
        """,
        (resident_id, account_code),
    ).fetchone()
    return int(row["balance_minor"]) if row else 0


def _active_long_goal(conn, resident_id: int):
    if not conn.execute("PRAGMA table_info(agent_goals)").fetchall():
        return None
    return conn.execute(
        """
        SELECT id, title, category, priority
        FROM agent_goals
        WHERE resident_id = ? AND horizon = 'long' AND status = 'active'
        ORDER BY priority DESC, id LIMIT 1
        """,
        (resident_id,),
    ).fetchone()


def seed_budget_runtime(conn) -> dict:
    residents = conn.execute("SELECT id FROM residents ORDER BY id").fetchall()
    created = 0
    for resident in residents:
        capability = conn.execute(
            """
            SELECT risk_tolerance, economic_access
            FROM agent_capability_profiles WHERE resident_id = ?
            """,
            (resident["id"],),
        ).fetchone()
        risk = int(capability["risk_tolerance"]) if capability else 50
        economic = int(capability["economic_access"]) if capability else 50
        savings_rate = max(300, min(1500, 1200 - risk * 8 + max(0, economic - 50) * 4))
        emergency = max(800, min(3000, 2200 - risk * 14))
        before = conn.execute(
            "SELECT resident_id FROM household_budget_profiles WHERE resident_id = ?",
            (resident["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO household_budget_profiles
            (resident_id, planning_horizon_days, savings_rate_basis_points,
             emergency_reserve_minor, risk_tolerance, credit_enabled,
             credit_limit_minor, outstanding_debt_minor, metadata_json)
            VALUES (?, 7, ?, ?, ?, 0, 0, 0, ?)
            """,
            (
                resident["id"], savings_rate, emergency, risk,
                _json({
                    "rule_version": RULE_VERSION,
                    "credit_policy": "disabled_until_2.7.2",
                }),
            ),
        )
        ensure_ledger_account(
            conn, actor_key=f"resident:{resident['id']}",
            account_code="savings", account_type="asset", normal_side="debit",
        )
        created += int(before is None)
    return {
        "profiles": int(
            conn.execute(
                "SELECT COUNT(*) value FROM household_budget_profiles"
            ).fetchone()["value"]
        ),
        "profiles_created": created,
    }


def calculate_budget_state(conn, resident_id: int, world_time=None) -> dict:
    now = _now(world_time)
    profile = conn.execute(
        "SELECT * FROM household_budget_profiles WHERE resident_id = ? AND status = 'active'",
        (resident_id,),
    ).fetchone()
    if not profile:
        raise ValueError("居民预算档案不存在")
    horizon_end = (
        now.date() + timedelta(days=int(profile["planning_horizon_days"]))
    ).isoformat()
    cash = _account_balance(conn, resident_id, "cash")
    savings = _account_balance(conn, resident_id, "savings")
    required = conn.execute(
        """
        SELECT COALESCE(SUM(amount_minor), 0) value
        FROM expense_obligations
        WHERE resident_id = ? AND status = 'active'
          AND next_due_date <= ?
        """,
        (resident_id, horizon_end),
    ).fetchone()
    required_minor = int(required["value"])
    programs = conn.execute(
        """
        SELECT program_type, COALESCE(SUM(amount_minor), 0) value
        FROM income_programs
        WHERE recipient_resident_id = ? AND status = 'active'
          AND next_due_date <= ?
        GROUP BY program_type
        """,
        (resident_id, horizon_end),
    ).fetchall()
    transfer_types = {"scholarship", "financial_aid", "family_support", "subsidy"}
    transfer_income = sum(
        int(row["value"]) for row in programs
        if row["program_type"] in transfer_types
    )
    expected_income = transfer_income
    contracts = conn.execute(
        """
        SELECT COALESCE(SUM(
            contract.hourly_wage_minor * contract.scheduled_daily_minutes / 60
        ), 0) value
        FROM employment_contracts contract
        WHERE contract.resident_id = ? AND contract.status = 'active'
        """,
        (resident_id,),
    ).fetchone()
    expected_income += round(float(contracts["value"]) * int(profile["planning_horizon_days"]))
    completed_time = conn.execute(
        """
        SELECT COALESCE(SUM(duration_minutes), 0) value
        FROM world_action_executions
        WHERE resident_id = ? AND status = 'completed'
          AND occurred_at >= ? AND occurred_at < ?
        """,
        (
            resident_id, f"{now.date().isoformat()}T00:00:00",
            f"{(now.date() + timedelta(days=1)).isoformat()}T00:00:00",
        ),
    ).fetchone()
    shifts = conn.execute(
        """
        SELECT COALESCE(SUM(shift.scheduled_minutes), 0) value
        FROM labor_shifts shift
        JOIN employment_contracts contract ON contract.id = shift.contract_id
        WHERE contract.resident_id = ? AND shift.work_date = ?
          AND shift.status IN ('scheduled', 'partial', 'completed')
        """,
        (resident_id, now.date().isoformat()),
    ).fetchone()
    time_budget = 16 * 60
    committed_time = min(
        time_budget,
        int(completed_time["value"]) + int(shifts["value"]),
    )
    free_time = max(0, time_budget - committed_time)
    credit = (
        available_credit(conn, resident_id)
        if credit_runtime_available(conn)
        else {
            "enabled": False,
            "credit_limit_minor": 0,
            "outstanding_principal_minor": 0,
            "accrued_interest_minor": 0,
            "available_credit_minor": 0,
            "due_debt_minor": 0,
            "status": "disabled",
        }
    )
    due_debt = int(credit["due_debt_minor"])
    disposable = max(0, cash - required_minor - due_debt)
    if cash < required_minor + due_debt:
        liquidity = "shortfall"
    elif disposable < int(profile["emergency_reserve_minor"]):
        liquidity = "tight"
    else:
        liquidity = "stable"
    return {
        "resident_id": resident_id,
        "budget_date": now.date().isoformat(),
        "cash_minor": cash,
        "savings_minor": savings,
        "expected_income_minor": expected_income,
        "transfer_income_minor": transfer_income,
        "required_expenses_minor": required_minor,
        "due_debt_minor": due_debt,
        "borrowing_minor": int(credit["available_credit_minor"]),
        "credit_limit_minor": int(credit["credit_limit_minor"]),
        "outstanding_principal_minor": int(
            credit["outstanding_principal_minor"]
        ),
        "accrued_interest_minor": int(credit["accrued_interest_minor"]),
        "credit_enabled": bool(credit["enabled"]),
        "credit_status": credit["status"],
        "disposable_minor": disposable,
        "time_budget_minutes": time_budget,
        "committed_time_minutes": committed_time,
        "free_time_minutes": free_time,
        "liquidity_status": liquidity,
        "emergency_reserve_minor": int(profile["emergency_reserve_minor"]),
    }


def _save_budget_snapshot(conn, state: dict) -> None:
    conn.execute(
        """
        INSERT INTO household_budget_snapshots
        (snapshot_key, resident_id, budget_date, cash_minor, savings_minor,
         expected_income_minor, transfer_income_minor, required_expenses_minor,
         due_debt_minor, borrowing_minor, disposable_minor,
         time_budget_minutes, committed_time_minutes, free_time_minutes,
         liquidity_status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (resident_id, budget_date) DO UPDATE SET
            cash_minor = excluded.cash_minor,
            savings_minor = excluded.savings_minor,
            expected_income_minor = excluded.expected_income_minor,
            transfer_income_minor = excluded.transfer_income_minor,
            required_expenses_minor = excluded.required_expenses_minor,
            due_debt_minor = excluded.due_debt_minor,
            borrowing_minor = excluded.borrowing_minor,
            disposable_minor = excluded.disposable_minor,
            time_budget_minutes = excluded.time_budget_minutes,
            committed_time_minutes = excluded.committed_time_minutes,
            free_time_minutes = excluded.free_time_minutes,
            liquidity_status = excluded.liquidity_status,
            metadata_json = excluded.metadata_json
        """,
        (
            f"budget:{state['resident_id']}:{state['budget_date']}",
            state["resident_id"], state["budget_date"], state["cash_minor"],
            state["savings_minor"], state["expected_income_minor"],
            state["transfer_income_minor"], state["required_expenses_minor"],
            state["due_debt_minor"], state["borrowing_minor"],
            state["disposable_minor"], state["time_budget_minutes"],
            state["committed_time_minutes"], state["free_time_minutes"],
            state["liquidity_status"],
            _json({
                "credit_enabled": state["credit_enabled"],
                "credit_limit_minor": state["credit_limit_minor"],
                "outstanding_principal_minor": state[
                    "outstanding_principal_minor"
                ],
                "accrued_interest_minor": state["accrued_interest_minor"],
                "credit_status": state["credit_status"],
                "rule_version": RULE_VERSION,
            }),
        ),
    )


def evaluate_action_choice(
    conn,
    *,
    resident_id: int,
    action_type: str,
    location: str,
    required_money_minor: int,
    required_time_minutes: int,
    world_time=None,
) -> dict:
    state = calculate_budget_state(conn, resident_id, world_time)
    hunger = 0
    if conn.execute("PRAGMA table_info(agent_body_states)").fetchall():
        body = conn.execute(
            "SELECT hunger FROM agent_body_states WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()
        hunger = int(body["hunger"]) if body else 0
    is_basic_dining = (
        action_type == "consume"
        and (
            hunger >= 85
            or (
                int(required_money_minor) <= 1500
                and (
                    hunger >= 35
                    or any(
                        k in str(location)
                        for k in ("食堂", "清晏", "紫荆", "听涛", "观稠", "桃李", "餐饮", "商业", "小吃", "便利店", "超市", "canteen", "dining")
                    )
                )
            )
        )
    )
    essential_emergency = bool(is_basic_dining)
    savings = int(state["savings_minor"])
    usable_credit = (
        int(state["borrowing_minor"])
        if int(state["borrowing_minor"]) >= 100
        else 0
    )
    money_available = int(state["disposable_minor"])
    emergency_override = bool(
        essential_emergency
        and int(state["cash_minor"]) + savings + usable_credit
        >= int(required_money_minor)
    )
    money_ok = money_available >= int(required_money_minor) or emergency_override
    time_ok = int(state["free_time_minutes"]) >= int(required_time_minutes)
    if not money_ok:
        decision = "rejected"
        rationale = "可支配资金不足，必要支出和储蓄不应被普通消费挤占"
        alternative = "seek_financial_aid" if action_type == "consume" else "defer_action"
    elif not time_ok:
        decision = "deferred"
        rationale = "当日自由时间不足，行动延期以释放时间给既有承诺"
        alternative = "rest"
    else:
        decision = "allowed"
        rationale = "资金与时间预算允许"
        alternative = ""
    goal = _active_long_goal(conn, resident_id)
    return {
        **state,
        "decision": decision,
        "passed": decision == "allowed",
        "required_money_minor": int(required_money_minor),
        "required_time_minutes": int(required_time_minutes),
        "money_opportunity_cost_minor": min(
            int(required_money_minor), money_available
        ),
        "time_opportunity_cost_minutes": min(
            int(required_time_minutes), int(state["free_time_minutes"])
        ),
        "released_money_minor": (
            int(required_money_minor) if decision != "allowed" else 0
        ),
        "released_time_minutes": (
            int(required_time_minutes) if decision != "allowed" else 0
        ),
        "alternative_action": alternative,
        "long_term_goal_id": int(goal["id"]) if goal else None,
        "long_term_goal": goal["title"] if goal else "",
        "emergency_override": emergency_override,
        "rationale": rationale,
        "rule_version": RULE_VERSION,
    }


def record_action_choice(
    conn,
    *,
    action_execution_id: int,
    resident_id: int,
    action_type: str,
    location: str,
    evaluation: dict,
    world_time=None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO choice_evaluations
        (evaluation_key, resident_id, action_execution_id, action_type,
         location, decision, required_money_minor, required_time_minutes,
         disposable_before_minor, free_time_before_minutes,
         money_opportunity_cost_minor, time_opportunity_cost_minutes,
         released_money_minor, released_time_minutes, alternative_action,
         long_term_goal_id, emergency_override, rationale, rule_version,
         occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"choice:action:{action_execution_id}", resident_id,
            action_execution_id, action_type, location, evaluation["decision"],
            evaluation["required_money_minor"],
            evaluation["required_time_minutes"],
            evaluation["disposable_minor"], evaluation["free_time_minutes"],
            evaluation["money_opportunity_cost_minor"],
            evaluation["time_opportunity_cost_minutes"],
            evaluation["released_money_minor"],
            evaluation["released_time_minutes"],
            evaluation["alternative_action"], evaluation["long_term_goal_id"],
            int(evaluation["emergency_override"]), evaluation["rationale"],
            evaluation["rule_version"], _now(world_time).isoformat(),
        ),
    )


def _deposit_savings(conn, resident_id: int, amount_minor: int, now: datetime):
    goal = _active_long_goal(conn, resident_id)
    key = f"savings:auto:{resident_id}:{now.date().isoformat()}"
    ledger = post_money_transfer_minor(
        conn, transaction_key=key,
        from_account_key=f"resident:{resident_id}:cash",
        to_account_key=f"resident:{resident_id}:savings",
        amount_minor=amount_minor, transaction_type="savings_deposit",
        source_type="budget_runtime", source_id=str(resident_id),
        description="按个人预算规则转入基础储蓄",
        metadata={"interest": 0, "credit_creation": False},
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO savings_transfers
        (transfer_key, resident_id, direction, amount_minor, reason,
         goal_id, ledger_transaction_id, occurred_at, metadata_json)
        VALUES (?, ?, 'deposit', ?, 'periodic_budget_saving', ?, ?, ?, ?)
        """,
        (
            key, resident_id, amount_minor, goal["id"] if goal else None,
            ledger["id"], now.isoformat(),
            _json({"interest": 0, "rule_version": RULE_VERSION}),
        ),
    )
    return int(ledger["id"])


def fund_emergency_action(
    conn,
    *,
    resident_id: int,
    amount_minor: int,
    action_execution_id: int,
    evaluation: dict,
    world_time=None,
):
    if not evaluation.get("emergency_override"):
        return None
    cash = _account_balance(conn, resident_id, "cash")
    shortfall = max(0, int(amount_minor) - cash)
    if not shortfall:
        return None
    savings = _account_balance(conn, resident_id, "savings")
    savings_draw = min(shortfall, savings)
    last_ledger_id = None
    if savings_draw:
        key = f"savings:emergency-action:{action_execution_id}"
        ledger = post_money_transfer_minor(
            conn, transaction_key=key,
            from_account_key=f"resident:{resident_id}:savings",
            to_account_key=f"resident:{resident_id}:cash",
            amount_minor=savings_draw, transaction_type="savings_withdrawal",
            source_type="world_action_execution",
            source_id=str(action_execution_id),
            action_execution_id=action_execution_id,
            description="基本生活行动动用应急储蓄",
            metadata={"action_type": "consume", "interest": 0},
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO savings_transfers
            (transfer_key, resident_id, direction, amount_minor, reason,
             ledger_transaction_id, occurred_at, metadata_json)
            VALUES (?, ?, 'withdrawal', ?, 'essential_consumption', ?, ?, ?)
            """,
            (
                key, resident_id, savings_draw, ledger["id"],
                _now(world_time).isoformat(),
                _json({"action_execution_id": action_execution_id}),
            ),
        )
        last_ledger_id = int(ledger["id"])
        shortfall -= savings_draw
    if shortfall:
        credit = available_credit(conn, resident_id)
        draw = max(100, shortfall)
        if (
            not credit["enabled"]
            or int(credit["available_credit_minor"]) < draw
        ):
            raise ValueError("应急储蓄与可用信用额度不足")
        contract = originate_credit(
            conn,
            resident_id=resident_id,
            amount_minor=draw,
            purpose="essential_consumption",
            world_time=world_time,
            contract_key=f"credit:emergency-action:{action_execution_id}",
        )
        last_ledger_id = int(contract["ledger_transaction_id"])
    return last_ledger_id


def process_budget_runtime(conn, world_time=None) -> dict:
    if not budget_runtime_available(conn):
        return {"available": False, "snapshots": 0, "savings_transfers": []}
    now = _now(world_time)
    transfers = []
    residents = conn.execute(
        """
        SELECT resident_id, savings_rate_basis_points, last_savings_date
        FROM household_budget_profiles
        WHERE status = 'active' ORDER BY resident_id
        """
    ).fetchall()
    for profile in residents:
        state = calculate_budget_state(conn, int(profile["resident_id"]), now)
        last = profile["last_savings_date"]
        savings_due = not last or (
            now.date() - date.fromisoformat(last)
        ).days >= 7
        if savings_due and state["disposable_minor"] > state["emergency_reserve_minor"]:
            amount = (
                state["disposable_minor"]
                * int(profile["savings_rate_basis_points"])
                // 10000
            )
            amount = min(
                amount,
                state["disposable_minor"] - state["emergency_reserve_minor"],
            )
            if amount > 0:
                ledger_id = _deposit_savings(
                    conn, int(profile["resident_id"]), amount, now
                )
                transfers.append({
                    "resident_id": int(profile["resident_id"]),
                    "amount_minor": amount,
                    "ledger_transaction_id": ledger_id,
                })
                conn.execute(
                    """
                    UPDATE household_budget_profiles
                    SET last_savings_date = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE resident_id = ?
                    """,
                    (now.date().isoformat(), profile["resident_id"]),
                )
                state = calculate_budget_state(
                    conn, int(profile["resident_id"]), now
                )
        _save_budget_snapshot(conn, state)
    return {
        "available": True,
        "snapshots": len(residents),
        "savings_transfers": transfers,
        "credit_enabled": credit_runtime_available(conn),
        "borrowing_minor": sum(
            available_credit(conn, int(row["resident_id"]))[
                "available_credit_minor"
            ]
            for row in residents
        ) if credit_runtime_available(conn) else 0,
        "due_debt_minor": sum(
            available_credit(conn, int(row["resident_id"]))[
                "due_debt_minor"
            ]
            for row in residents
        ) if credit_runtime_available(conn) else 0,
    }
