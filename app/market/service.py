from __future__ import annotations

from typing import Optional

import hashlib
from app.json_utils import json_dumps
from datetime import datetime, timedelta, timezone

from app.budget.service import budget_runtime_available, calculate_budget_state
from app.economy.service import post_money_transfer_minor
from app.public_policy.service import (
    market_policy_terms,
    settle_market_policy_benefits,
)
from app.supply.service import settle_goods_trade


RULE_VERSION = "market-pricing-v1"


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _json(value) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def _clamp(value, low, high):
    return max(low, min(high, value))


def market_runtime_available(conn) -> bool:
    return bool(conn.execute("PRAGMA table_info(market_mechanisms)").fetchall())


def _mode_for_item(name: str, item_type: str) -> str:
    if name in {"套餐饭", "早餐券"}:
        return "rationed"
    if item_type == "service":
        return "rationed"
    return "dynamic"


def _round_price(value: float) -> int:
    return max(0, int(round(value / 100.0)) * 100)


def seed_market_runtime(conn) -> dict:
    created = 0
    inventory_rows = conn.execute(
        """
        SELECT account.owner_actor_key, account.location, account.target_stock,
               account.reorder_point, account.average_cost_minor,
               item.*
        FROM inventory_accounts account
        JOIN catalog_items item ON item.id = account.item_id
        WHERE account.status = 'active' AND item.status = 'active'
          AND account.location <> ''
          AND (account.target_stock > 0 OR account.reorder_point > 0)
        ORDER BY account.id
        """
    ).fetchall()
    for row in inventory_rows:
        base = int(row["base_price_minor"])
        variable_cost = max(int(row["standard_cost_minor"]), int(row["average_cost_minor"]))
        floor = min(base, _round_price(variable_cost * 1.05)) if base else variable_cost
        floor = max(0, floor)
        ceiling = max(base, floor, _round_price(max(base, variable_cost) * 2.5))
        mode = _mode_for_item(row["name"], row["item_type"])
        quota = 3 if mode == "rationed" else 0
        search_cost = 100 if row["location"] == "商业街" else 0
        key = f"market:{row['owner_actor_key']}:{row['item_key']}:{row['location']}"
        before = conn.execute(
            "SELECT id FROM market_mechanisms WHERE mechanism_key = ?",
            (key,),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO market_mechanisms
            (mechanism_key, item_id, provider_actor_key, location, pricing_mode,
             base_price_minor, floor_price_minor, ceiling_price_minor,
             variable_cost_minor, target_supply, target_daily_demand,
             adjustment_rate_basis_points, demand_elasticity_basis_points,
             search_cost_minor, transaction_cost_minor,
             daily_quota_per_resident, rule_version, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key, row["id"], row["owner_actor_key"], row["location"], mode,
                base, floor, ceiling, variable_cost,
                max(1, int(row["target_stock"] or 1)),
                max(1, int(row["target_stock"] or 1) // 2),
                3000 if mode == "dynamic" else 0,
                12000 if mode == "dynamic" else 8000,
                search_cost, 0, quota, RULE_VERSION,
                _json({"item_name": row["name"], "source": "inventory_account"}),
            ),
        )
        created += int(before is None)
    service_rows = conn.execute(
        """
        SELECT offering.provider_actor_key, offering.location,
               offering.capacity_per_hour, offering.price_minor,
               item.*
        FROM service_offerings offering
        JOIN catalog_items item ON item.id = offering.service_item_id
        WHERE offering.status = 'active' AND item.status = 'active'
        ORDER BY offering.id
        """
    ).fetchall()
    for row in service_rows:
        base = int(row["price_minor"])
        ceiling = max(base, 100) * 2
        key = f"market:{row['provider_actor_key']}:{row['item_key']}:{row['location']}"
        before = conn.execute(
            "SELECT id FROM market_mechanisms WHERE mechanism_key = ?",
            (key,),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO market_mechanisms
            (mechanism_key, item_id, provider_actor_key, location, pricing_mode,
             base_price_minor, floor_price_minor, ceiling_price_minor,
             variable_cost_minor, target_supply, target_daily_demand,
             adjustment_rate_basis_points, demand_elasticity_basis_points,
             search_cost_minor, transaction_cost_minor,
             daily_quota_per_resident, rule_version, metadata_json)
            VALUES (?, ?, ?, ?, 'rationed', ?, 0, ?, 0, ?, ?, 0, 8000, 0, 0, 1, ?, ?)
            """,
            (
                key, row["id"], row["provider_actor_key"], row["location"],
                base, ceiling, int(row["capacity_per_hour"]),
                int(row["capacity_per_hour"]) * 8, RULE_VERSION,
                _json({"item_name": row["name"], "source": "service_offering"}),
            ),
        )
        created += int(before is None)
    count = conn.execute(
        "SELECT COUNT(*) value FROM market_mechanisms"
    ).fetchone()["value"]
    return {"mechanisms": int(count), "mechanisms_created": created}


def _mechanism_supply(conn, mechanism, now: datetime) -> tuple[int, int]:
    item = conn.execute(
        "SELECT item_type FROM catalog_items WHERE id = ?",
        (mechanism["item_id"],),
    ).fetchone()
    if item and item["item_type"] == "service":
        offering = conn.execute(
            """
            SELECT id, capacity_per_hour FROM service_offerings
            WHERE provider_actor_key = ? AND service_item_id = ? AND location = ?
              AND status = 'active'
            LIMIT 1
            """,
            (
                mechanism["provider_actor_key"], mechanism["item_id"],
                mechanism["location"],
            ),
        ).fetchone()
        if not offering:
            return 0, int(mechanism["target_supply"])
        start = now.replace(minute=0, second=0, microsecond=0).isoformat()
        end = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat()
        used = conn.execute(
            """
            SELECT COUNT(*) value FROM service_deliveries
            WHERE offering_id = ? AND status = 'delivered'
              AND requested_at >= ? AND requested_at < ?
            """,
            (offering["id"], start, end),
        ).fetchone()["value"]
        return max(0, int(offering["capacity_per_hour"]) - int(used)), int(offering["capacity_per_hour"])
    account = conn.execute(
        """
        SELECT COALESCE(SUM(quantity_on_hand - quantity_reserved), 0) AS available_supply,
               COALESCE(MAX(target_stock), 0) AS target_stock
        FROM inventory_accounts
        WHERE owner_actor_key = ? AND item_id = ? AND location = ?
          AND status = 'active'
        """,
        (
            mechanism["provider_actor_key"], mechanism["item_id"],
            mechanism["location"],
        ),
    ).fetchone()
    if not account or account["available_supply"] is None:
        return 0, int(mechanism["target_supply"])
    return (
        max(0, int(account["available_supply"])),
        max(1, int(account["target_stock"] or mechanism["target_supply"])),
    )


def _recent_demand(conn, mechanism_id: int, now: datetime) -> tuple[int, int]:
    since = (now - timedelta(hours=24)).isoformat()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0) requested,
               COALESCE(SUM(CASE WHEN status = 'fulfilled' THEN quantity ELSE 0 END), 0) fulfilled
        FROM market_demand_signals
        WHERE mechanism_id = ? AND occurred_at >= ? AND occurred_at < ?
        """,
        (mechanism_id, since, now.isoformat()),
    ).fetchone()
    return int(row["requested"]), int(row["fulfilled"])


def _environment_pressure(conn) -> int:
    if not conn.execute("PRAGMA table_info(campus_state)").fetchall():
        return 0
    row = conn.execute(
        """
        SELECT resource_pressure, consumption_index
        FROM campus_state ORDER BY day DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return 0
    resource = int(row["resource_pressure"] or 50)
    consumption = float(row["consumption_index"] or 1.0)
    return int(_clamp((resource - 50) * 30 + (consumption - 1.0) * 1000, -1500, 2500))


def quote_market_offer(conn, mechanism_id: int, world_time=None) -> dict:
    now = _now(world_time)
    hour = now.replace(minute=0, second=0, microsecond=0)
    quote_key = f"quote:{mechanism_id}:{hour.isoformat()}"
    mechanism = conn.execute(
        """
        SELECT mechanism.*, item.name AS item_name, item.item_type
        FROM market_mechanisms mechanism
        JOIN catalog_items item ON item.id = mechanism.item_id
        WHERE mechanism.id = ? AND mechanism.status = 'active'
        """,
        (mechanism_id,),
    ).fetchone()
    if not mechanism:
        raise ValueError("市场机制不存在或已暂停")
    existing = conn.execute(
        "SELECT * FROM market_price_snapshots WHERE quote_key = ?",
        (quote_key,),
    ).fetchone()
    if existing:
        available, _ = _mechanism_supply(conn, mechanism, now)
        if int(existing["available_supply"]) == available:
            return {**dict(existing), "mechanism": dict(mechanism)}
        conn.execute("DELETE FROM market_price_snapshots WHERE id = ?", (existing["id"],))
    available, target_supply = _mechanism_supply(conn, mechanism, now)
    observed, fulfilled = _recent_demand(conn, mechanism_id, now)
    inventory_pressure = int(
        _clamp((target_supply - available) / max(1, target_supply) * 3000, -1500, 5000)
    )
    target_demand = max(1, int(mechanism["target_daily_demand"]))
    demand_pressure = int(
        _clamp((observed - target_demand) / target_demand * 2000, -1500, 4000)
    )
    environment_pressure = _environment_pressure(conn)
    raw_pressure = inventory_pressure + demand_pressure + environment_pressure
    applied = int(
        raw_pressure * int(mechanism["adjustment_rate_basis_points"]) / 10000
    )
    if mechanism["pricing_mode"] in {"fixed", "rationed"}:
        applied = 0
    price = _round_price(
        int(mechanism["base_price_minor"]) * (1 + applied / 10000)
    )
    price = int(
        _clamp(
            price,
            int(mechanism["floor_price_minor"]),
            int(mechanism["ceiling_price_minor"]),
        )
    )
    rationed = int(
        available <= 0
        or (
            int(mechanism["daily_quota_per_resident"]) > 0
            and observed > target_demand
        )
    )
    fingerprint = hashlib.sha256(
        _json({
            "mechanism": mechanism["mechanism_key"],
            "available": available,
            "observed": observed,
            "fulfilled": fulfilled,
            "environment_pressure": environment_pressure,
            "hour": hour.isoformat(),
        }).encode("utf-8")
    ).hexdigest()
    explanation = (
        f"{mechanism['pricing_mode']} 定价：基准 {mechanism['base_price_minor']}，"
        f"库存压力 {inventory_pressure}bp，需求压力 {demand_pressure}bp，"
        f"环境压力 {environment_pressure}bp，最终调整 {applied}bp。"
    )
    cursor = conn.execute(
        """
        INSERT INTO market_price_snapshots
        (quote_key, mechanism_id, price_minor, base_price_minor,
         variable_cost_minor, inventory_pressure_basis_points,
         demand_pressure_basis_points, environment_pressure_basis_points,
         applied_adjustment_basis_points, search_cost_minor,
         transaction_cost_minor, available_supply, observed_demand,
         fulfilled_demand, rationed, explanation, valid_from, valid_until,
         state_fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quote_key, mechanism_id, price, mechanism["base_price_minor"],
            mechanism["variable_cost_minor"], inventory_pressure,
            demand_pressure, environment_pressure, applied,
            mechanism["search_cost_minor"], mechanism["transaction_cost_minor"],
            available, observed, fulfilled, rationed, explanation,
            hour.isoformat(), (hour + timedelta(hours=1)).isoformat(),
            fingerprint,
        ),
    )
    row = conn.execute(
        "SELECT * FROM market_price_snapshots WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return {**dict(row), "mechanism": dict(mechanism)}


def find_market_mechanism(
    conn,
    *,
    item_name: Optional[str] = None,
    provider_actor_key: Optional[str] = None,
    location: Optional[str] = None,
):
    clauses = ["mechanism.status = 'active'"]
    params = []
    if item_name:
        clauses.append("item.name = ?")
        params.append(item_name)
    if provider_actor_key:
        clauses.append("mechanism.provider_actor_key = ?")
        params.append(provider_actor_key)
    if location:
        clauses.append("mechanism.location = ?")
        params.append(location)
    return conn.execute(
        f"""
        SELECT mechanism.*, item.name AS item_name, item.item_type
        FROM market_mechanisms mechanism
        JOIN catalog_items item ON item.id = mechanism.item_id
        WHERE {' AND '.join(clauses)}
        ORDER BY mechanism.id LIMIT 1
        """,
        tuple(params),
    ).fetchone()


def _daily_resident_quantity(conn, mechanism_id: int, resident_id: int, now: datetime) -> int:
    start = f"{now.date().isoformat()}T00:00:00"
    end = f"{(now.date() + timedelta(days=1)).isoformat()}T00:00:00"
    row = conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0) value
        FROM market_demand_signals
        WHERE mechanism_id = ? AND resident_id = ?
          AND status = 'fulfilled' AND occurred_at >= ? AND occurred_at < ?
        """,
        (mechanism_id, resident_id, start, end),
    ).fetchone()
    return int(row["value"])


def _resident_scores(conn, resident_id: int, item_id: int, action_type: str) -> tuple[int, int, int]:
    hunger = 50
    if conn.execute("PRAGMA table_info(agent_body_states)").fetchall():
        body = conn.execute(
            "SELECT hunger FROM agent_body_states WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()
        if body:
            hunger = int(body["hunger"])
    need = hunger if action_type == "consume" else 50
    digest = hashlib.sha256(f"{resident_id}|{item_id}|preference".encode("utf-8")).digest()
    preference = 35 + int.from_bytes(digest[:2], "big") % 51
    social = conn.execute(
        """
        SELECT COUNT(*) value FROM market_demand_signals
        WHERE item_id = ? AND status = 'fulfilled'
        """,
        (item_id,),
    ).fetchone()["value"]
    information_pressure = 0
    if conn.execute("PRAGMA table_info(information_beliefs)").fetchall():
        item = conn.execute(
            "SELECT item_key, name FROM catalog_items WHERE id = ?", (item_id,)
        ).fetchone()
        belief = conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE belief.stance
                    WHEN 'believes' THEN belief.confidence
                    WHEN 'corrected' THEN belief.confidence / 2
                    WHEN 'disbelieves' THEN -belief.confidence
                    ELSE 0
                END
            ), 0) value
            FROM information_beliefs belief
            JOIN information_claims claim ON claim.id = belief.claim_id
            WHERE belief.resident_id = ? AND claim.subject_type = 'catalog_item'
              AND claim.subject_key IN (?, ?, ?)
            """,
            (resident_id, str(item_id), item["item_key"], item["name"]),
        ).fetchone()
        information_pressure = int(belief["value"]) // 10
    return (
        int(_clamp(need, 0, 100)),
        preference,
        int(_clamp(int(social) * 3 + information_pressure, 0, 100)),
    )


def evaluate_market_choice(
    conn,
    *,
    resident_id: int,
    mechanism_id: int,
    quantity: int = 1,
    action_type: str = "consume",
    world_time=None,
) -> dict:
    now = _now(world_time)
    quote = quote_market_offer(conn, mechanism_id, now)
    mechanism = quote["mechanism"]
    need, preference, social = _resident_scores(
        conn, resident_id, int(mechanism["item_id"]), action_type
    )
    disposable = 0
    if budget_runtime_available(conn):
        profile = conn.execute(
            "SELECT resident_id FROM household_budget_profiles WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()
        if profile:
            disposable = int(calculate_budget_state(conn, resident_id, now)["disposable_minor"])
    if not disposable:
        cash = conn.execute(
            "SELECT money FROM residents WHERE id = ?",
            (resident_id,),
        ).fetchone()
        disposable = int(cash["money"]) * 100 if cash else 0
    elasticity = int(mechanism["demand_elasticity_basis_points"]) / 10000
    willingness_factor = 0.55 + need / 200 + preference / 500 + social / 1000
    maximum = _round_price(int(mechanism["base_price_minor"]) * willingness_factor * elasticity)
    maximum = min(disposable, max(maximum, int(mechanism["floor_price_minor"])))
    policy_terms = market_policy_terms(
        conn,
        resident_id=resident_id,
        mechanism_id=mechanism_id,
        gross_price_minor=int(quote["price_minor"]),
        world_time=now,
    )
    unit_total = (
        int(policy_terms["private_price_minor"])
        + int(quote["search_cost_minor"])
        + int(quote["transaction_cost_minor"])
    )
    quota = int(mechanism["daily_quota_per_resident"])
    used = _daily_resident_quantity(conn, mechanism_id, resident_id, now)
    status = "accepted"
    reason = "报价在预算和支付意愿内"
    if int(quote["available_supply"]) < int(quantity):
        status, reason = "out_of_stock", "当前可售库存或服务容量不足"
    elif quota and used + int(quantity) > quota:
        status, reason = "rationed", f"每日配额为 {quota}，当前已使用 {used}"
    elif unit_total > maximum and need < 85:
        status, reason = "price_rejected", f"含摩擦成本报价 {unit_total} 超过最高支付意愿 {maximum}"
    substitute = None
    if status in {"out_of_stock", "price_rejected", "rationed"}:
        candidates = conn.execute(
            """
            SELECT mechanism.id, item.id AS item_id, item.name
            FROM market_mechanisms mechanism
            JOIN catalog_items item ON item.id = mechanism.item_id
            WHERE mechanism.location = ? AND mechanism.id <> ?
              AND mechanism.status = 'active' AND item.item_type = ?
            ORDER BY mechanism.base_price_minor, mechanism.id
            """,
            (mechanism["location"], mechanism_id, mechanism["item_type"]),
        ).fetchall()
        for candidate in candidates:
            candidate_quote = quote_market_offer(conn, candidate["id"], now)
            candidate_total = (
                int(candidate_quote["price_minor"])
                + int(candidate_quote["search_cost_minor"])
                + int(candidate_quote["transaction_cost_minor"])
            )
            if int(candidate_quote["available_supply"]) >= quantity and candidate_total <= maximum:
                substitute = {
                    "mechanism_id": int(candidate["id"]),
                    "item_id": int(candidate["item_id"]),
                    "item_name": candidate["name"],
                    "total_unit_cost_minor": candidate_total,
                }
                break
    return {
        "mechanism_id": mechanism_id,
        "item_id": int(mechanism["item_id"]),
        "item_name": mechanism["item_name"],
        "provider_actor_key": mechanism["provider_actor_key"],
        "location": mechanism["location"],
        "quantity": int(quantity),
        "need_score": need,
        "preference_score": preference,
        "social_influence_score": social,
        "disposable_budget_minor": disposable,
        "maximum_unit_price_minor": maximum,
        "gross_unit_price_minor": int(policy_terms["gross_price_minor"]),
        "quoted_unit_price_minor": int(policy_terms["private_price_minor"]),
        "policy_subsidy_minor": int(policy_terms["subsidy_minor"]),
        "policy_price_cap_minor": int(policy_terms["price_cap_minor"]),
        "policy_terms": policy_terms,
        "search_cost_minor": int(quote["search_cost_minor"]),
        "transaction_cost_minor": int(quote["transaction_cost_minor"]),
        "total_unit_cost_minor": unit_total,
        "available_supply": int(quote["available_supply"]),
        "status": status,
        "reason": reason,
        "substitute": substitute,
        "quote_key": quote["quote_key"],
        "pricing_explanation": quote["explanation"],
    }


def record_market_demand(
    conn,
    *,
    resident_id: int,
    evaluation: dict,
    action_execution_id: Optional[int] = None,
    world_time=None,
) -> dict:
    now = _now(world_time)
    signal_key = (
        f"market-demand:action:{action_execution_id}"
        if action_execution_id is not None
        else f"market-demand:{resident_id}:{evaluation['mechanism_id']}:{now.isoformat()}"
    )
    existing = conn.execute(
        "SELECT * FROM market_demand_signals WHERE signal_key = ?",
        (signal_key,),
    ).fetchone()
    if existing:
        return dict(existing)
    substitute = evaluation.get("substitute")
    cursor = conn.execute(
        """
        INSERT INTO market_demand_signals
        (signal_key, resident_id, mechanism_id, item_id, action_execution_id,
         quantity, need_score, preference_score, social_influence_score,
         disposable_budget_minor, maximum_unit_price_minor,
         quoted_unit_price_minor, status, substitute_item_id, reason, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_key, resident_id, evaluation["mechanism_id"],
            evaluation["item_id"], action_execution_id, evaluation["quantity"],
            evaluation["need_score"], evaluation["preference_score"],
            evaluation["social_influence_score"],
            evaluation["disposable_budget_minor"],
            evaluation["maximum_unit_price_minor"],
            evaluation["quoted_unit_price_minor"], evaluation["status"],
            substitute["item_id"] if substitute else None,
            evaluation["reason"], now.isoformat(),
        ),
    )
    signal_id = int(cursor.lastrowid)
    friction = None
    if evaluation["status"] == "out_of_stock":
        friction = "stockout"
    elif evaluation["status"] == "rationed":
        friction = "rationing"
    elif substitute:
        friction = "substitution"
    elif evaluation["search_cost_minor"]:
        friction = "search_cost"
    if friction:
        conn.execute(
            """
            INSERT OR IGNORE INTO market_friction_events
            (event_key, mechanism_id, resident_id, demand_signal_id,
             friction_type, monetary_cost_minor, details_json, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"friction:{signal_key}:{friction}",
                evaluation["mechanism_id"], resident_id, signal_id, friction,
                evaluation["search_cost_minor"] if friction == "search_cost" else 0,
                _json({
                    "reason": evaluation["reason"],
                    "substitute": substitute,
                }),
                now.isoformat(),
            ),
        )
    return dict(
        conn.execute(
            "SELECT * FROM market_demand_signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
    )


def fulfill_market_goods_trade(
    conn,
    *,
    resident_id: int,
    evaluation: dict,
    action_execution_id: Optional[int],
    source_type: str = "world_action_execution",
    consume_immediately: bool = True,
) -> dict:
    if evaluation["status"] != "accepted":
        raise ValueError(evaluation["reason"])
    cash = conn.execute(
        """
        SELECT account.balance_minor
        FROM ledger_accounts account
        JOIN economic_actors actor ON actor.id = account.actor_id
        WHERE actor.resident_id = ? AND account.account_code = 'cash'
          AND account.status = 'active'
        """,
        (resident_id,),
    ).fetchone()
    total_due = (
        evaluation["quoted_unit_price_minor"]
        + evaluation["search_cost_minor"]
        + evaluation["transaction_cost_minor"]
    ) * evaluation["quantity"]
    if not cash or int(cash["balance_minor"]) < int(total_due):
        raise ValueError("买家余额不足以支付商品与市场摩擦成本")
    source_id = str(action_execution_id or "")
    transaction_key = (
        f"market-action:{action_execution_id}"
        if action_execution_id is not None
        else f"market-direct:{resident_id}:{evaluation['mechanism_id']}:{_now().isoformat()}"
    )
    trade = settle_goods_trade(
        conn,
        transaction_key=transaction_key,
        buyer_actor_key=f"resident:{resident_id}",
        seller_actor_key=evaluation["provider_actor_key"],
        item_name=evaluation["item_name"],
        quantity=evaluation["quantity"],
        unit_price_minor=evaluation["quoted_unit_price_minor"],
        source_type=source_type,
        source_id=source_id,
        action_execution_id=action_execution_id,
        consume_immediately=consume_immediately,
    )
    policy_benefits = settle_market_policy_benefits(
        conn,
        resident_id=resident_id,
        mechanism_id=evaluation["mechanism_id"],
        provider_actor_key=evaluation["provider_actor_key"],
        policy_terms=evaluation.get("policy_terms") or {},
        quantity=evaluation["quantity"],
        source_key=transaction_key,
    )
    friction_cost = (
        evaluation["search_cost_minor"] + evaluation["transaction_cost_minor"]
    ) * evaluation["quantity"]
    friction_ledger_id = None
    if friction_cost:
        friction = post_money_transfer_minor(
            conn,
            transaction_key=f"{transaction_key}:friction",
            from_account_key=f"resident:{resident_id}:cash",
            to_account_key="system:campus-services:cash",
            amount_minor=friction_cost,
            transaction_type="market_friction_cost",
            source_type=source_type,
            source_id=source_id,
            action_execution_id=action_execution_id,
            description="市场搜索与交易摩擦成本",
            metadata={
                "mechanism_id": evaluation["mechanism_id"],
                "search_cost_minor": evaluation["search_cost_minor"],
                "transaction_cost_minor": evaluation["transaction_cost_minor"],
            },
        )
        friction_ledger_id = friction["id"]
    signal = record_market_demand(
        conn,
        resident_id=resident_id,
        evaluation=evaluation,
        action_execution_id=action_execution_id,
    )
    conn.execute(
        """
        UPDATE market_demand_signals
        SET status = 'fulfilled', final_unit_price_minor = ?
        WHERE id = ?
        """,
        (evaluation["quoted_unit_price_minor"], signal["id"]),
    )
    return {
        **trade,
        "mechanism_id": evaluation["mechanism_id"],
        "quoted_unit_price_minor": evaluation["quoted_unit_price_minor"],
        "friction_cost_minor": friction_cost,
        "friction_ledger_transaction_id": friction_ledger_id,
        "demand_signal_id": int(signal["id"]),
        "policy_benefits": policy_benefits,
    }


def process_market_runtime(conn, world_time=None) -> dict:
    if not market_runtime_available(conn):
        return {"available": False, "quotes": [], "imbalances": []}
    now = _now(world_time)
    quotes = []
    imbalances = []
    mechanisms = conn.execute(
        "SELECT id FROM market_mechanisms WHERE status = 'active' ORDER BY id"
    ).fetchall()
    for row in mechanisms:
        quote = quote_market_offer(conn, int(row["id"]), now)
        quotes.append(int(quote["id"]))
        if int(quote["available_supply"]) == 0 or int(quote["rationed"]):
            imbalances.append({
                "mechanism_id": int(row["id"]),
                "available_supply": int(quote["available_supply"]),
                "observed_demand": int(quote["observed_demand"]),
                "friction": "stockout" if int(quote["available_supply"]) == 0 else "rationing",
            })
    return {
        "available": True,
        "quotes": quotes,
        "imbalances": imbalances,
        "rule_version": RULE_VERSION,
    }
