from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import hashlib
from app.json_utils import json_dumps

from app.economy.service import (
    ensure_ledger_account,
    post_authorized_balance_change,
    post_money_transfer_minor,
)


RULE_VERSION = "public-policy-v1"
PUBLIC_FUND_ACTOR = "system:public-policy-fund"
PUBLIC_FUND_CASH = f"{PUBLIC_FUND_ACTOR}:cash"


def _json(value) -> str:
    return json_dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _load(value, default=None):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return {} if default is None else default


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def public_policy_runtime_available(conn) -> bool:
    return bool(conn.execute("PRAGMA table_info(public_services)").fetchall())


def _balance(conn, account_key: str) -> int:
    row = conn.execute(
        "SELECT balance_minor FROM ledger_accounts WHERE account_key = ?",
        (account_key,),
    ).fetchone()
    return int(row["balance_minor"]) if row else 0


def _ensure_public_fund(conn) -> int:
    actor = conn.execute(
        "SELECT id FROM economic_actors WHERE actor_key = ?",
        (PUBLIC_FUND_ACTOR,),
    ).fetchone()
    if not actor:
        conn.execute(
            """
            INSERT INTO economic_actors
            (actor_key, actor_type, display_name, metadata_json)
            VALUES (?, 'public', '校园公共政策基金', ?)
            """,
            (PUBLIC_FUND_ACTOR, _json({"purpose": "public_services_and_policy"})),
        )
    ensure_ledger_account(
        conn,
        actor_key=PUBLIC_FUND_ACTOR,
        account_code="cash",
        account_type="asset",
        normal_side="debit",
    )
    funded = 0
    if _balance(conn, PUBLIC_FUND_CASH) == 0:
        result = post_authorized_balance_change(
            conn,
            transaction_key="public-policy:opening-fund:v1",
            operation_type="external_inflow",
            authorization_rule_key="external-inflow-v1",
            authority_actor_key="system:ledger-controller",
            target_account_key=PUBLIC_FUND_CASH,
            amount_coins=1500,
            source_type="public_policy_seed",
            source_id="public-policy-fund-v1",
            description="公共服务与政策试验的有来源期初基金",
            metadata={"authorization": "external-inflow-v1", "one_time": True},
        )
        funded = 150000 if result["created"] else 0
    return funded


def seed_public_policy_runtime(conn, world_time=None) -> dict:
    now = _now(world_time)
    funded = _ensure_public_fund(conn)
    services = (
        ("library", "校园图书馆", "library", "图书馆", 160, 1200, 15, 82, "location"),
        ("network", "校园网络", "network", "", 500, 1800, 4, 78, "universal"),
        ("security", "校园安保", "security", "", 500, 1400, 3, 76, "universal"),
        ("public-space", "校园公共空间", "public_space", "操场", 120, 700, 8, 74, "location"),
    )
    service_created = 0
    for key, name, kind, location, capacity, base_cost, marginal, quality, access in services:
        before = conn.execute(
            "SELECT id FROM public_services WHERE service_key = ?", (key,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO public_services
            (service_key, name, service_type, provider_actor_key, location,
             daily_capacity, base_daily_cost_minor, marginal_cost_minor,
             quality, access_mode, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key, name, kind, PUBLIC_FUND_ACTOR, location, capacity,
                base_cost, marginal, quality, access,
                _json({"seed": "2.8.1", "non_excludable": access == "universal"}),
            ),
        )
        service_created += int(before is None)

    meal = conn.execute(
        "SELECT id, item_key FROM catalog_items WHERE name = '套餐饭'"
    ).fetchone()
    policies = [
        (
            "meal-support",
            "低资源居民餐食补贴",
            "subsidy",
            "catalog_item",
            meal["item_key"] if meal else "套餐饭",
            {"income_group": "low"},
            {"rate_basis_points": 3500, "max_per_use_minor": 400},
            5000,
        ),
        (
            "meal-price-cap",
            "基础餐食价格上限",
            "price_cap",
            "catalog_item",
            meal["item_key"] if meal else "套餐饭",
            {},
            {"cap_minor": 900},
            0,
        ),
        (
            "library-investment",
            "图书馆可达性投资",
            "public_investment",
            "public_service",
            "library",
            {},
            {"capacity_increment": 20, "quality_increment": 2},
            2400,
        ),
    ]
    policy_created = 0
    for key, name, kind, target_type, target_key, eligibility, parameters, budget in policies:
        before = conn.execute(
            "SELECT id FROM policy_instruments WHERE policy_key = ?", (key,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO policy_instruments
            (policy_key, name, policy_type, authority_actor_key,
             budget_account_key, target_type, target_key, eligibility_json,
             parameters_json, daily_budget_minor, starts_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key, name, kind, PUBLIC_FUND_ACTOR, PUBLIC_FUND_CASH,
                target_type, target_key, _json(eligibility), _json(parameters),
                budget, now.isoformat(),
            ),
        )
        policy_created += int(before is None)
    baseline_created = ensure_policy_baselines(conn, now)
    return {
        "services": int(conn.execute("SELECT COUNT(*) value FROM public_services").fetchone()["value"]),
        "services_created": service_created,
        "policies": int(conn.execute("SELECT COUNT(*) value FROM policy_instruments").fetchone()["value"]),
        "policies_created": policy_created,
        "public_fund_cash_minor": _balance(conn, PUBLIC_FUND_CASH),
        "funded_minor": funded,
        "baselines_created": len(baseline_created),
    }


def _resident_group(conn, resident_id: int) -> str:
    row = conn.execute(
        """
        SELECT COALESCE(snapshot.expected_income_minor, 0) income,
               COALESCE(snapshot.disposable_minor, 0) disposable
        FROM residents resident
        LEFT JOIN household_budget_snapshots snapshot
          ON snapshot.id = (
              SELECT id FROM household_budget_snapshots
              WHERE resident_id = resident.id ORDER BY id DESC LIMIT 1
          )
        WHERE resident.id = ?
        """,
        (resident_id,),
    ).fetchone()
    if not row:
        return "unknown"
    income = int(row["income"])
    disposable = int(row["disposable"])
    if income <= 2500 or disposable <= 1500:
        return "low"
    if income >= 7000 and disposable >= 5000:
        return "high"
    return "middle"


def _eligible(conn, policy, resident_id: int) -> tuple[bool, str]:
    criteria = _load(policy["eligibility_json"])
    group = _resident_group(conn, resident_id)
    required = criteria.get("income_group")
    if required and group != required:
        return False, group
    role = criteria.get("role")
    if role:
        resident = conn.execute(
            "SELECT role FROM residents WHERE id = ?", (resident_id,)
        ).fetchone()
        if not resident or resident["role"] != role:
            return False, group
    return True, group


def _active_item_policies(conn, mechanism_id: int, now: datetime):
    return conn.execute(
        """
        SELECT policy.*, item.item_key
        FROM policy_instruments policy
        JOIN market_mechanisms mechanism ON mechanism.id = ?
        JOIN catalog_items item ON item.id = mechanism.item_id
        WHERE policy.status = 'active'
          AND policy.target_type IN ('catalog_item', 'market')
          AND (policy.target_key = item.item_key
               OR policy.target_key = item.name
               OR policy.target_key = mechanism.mechanism_key)
          AND policy.starts_at <= ?
          AND (policy.ends_at = '' OR policy.ends_at > ?)
        ORDER BY policy.id
        """,
        (mechanism_id, now.isoformat(), now.isoformat()),
    ).fetchall()


def _daily_policy_spend(conn, policy_id: int, now: datetime) -> int:
    start = f"{now.date().isoformat()}T00:00:00"
    end = f"{(now.date() + timedelta(days=1)).isoformat()}T00:00:00"
    row = conn.execute(
        """
        SELECT COALESCE(SUM(public_cost_minor), 0) value
        FROM policy_benefits
        WHERE policy_id = ? AND status = 'delivered'
          AND occurred_at >= ? AND occurred_at < ?
        """,
        (policy_id, start, end),
    ).fetchone()
    return int(row["value"])


def market_policy_terms(
    conn,
    *,
    resident_id: int,
    mechanism_id: int,
    gross_price_minor: int,
    world_time=None,
) -> dict:
    if not public_policy_runtime_available(conn):
        return {
            "gross_price_minor": gross_price_minor,
            "private_price_minor": gross_price_minor,
            "subsidy_minor": 0,
            "price_cap_minor": 0,
            "policies": [],
        }
    now = _now(world_time)
    capped = int(gross_price_minor)
    subsidy = 0
    applied = []
    for policy in _active_item_policies(conn, mechanism_id, now):
        parameters = _load(policy["parameters_json"])
        eligible, group = _eligible(conn, policy, resident_id)
        if policy["policy_type"] == "price_cap":
            cap = int(parameters.get("cap_minor", capped))
            capped = min(capped, cap)
            applied.append({
                "policy_id": int(policy["id"]),
                "policy_type": "price_cap",
                "eligible": True,
                "income_group": group,
                "value_minor": max(0, gross_price_minor - capped),
            })
        elif policy["policy_type"] == "subsidy" and eligible:
            rate = int(parameters.get("rate_basis_points", 0))
            maximum = int(parameters.get("max_per_use_minor", capped))
            value = min(maximum, int(round(capped * rate / 10000)))
            remaining = max(
                0,
                int(policy["daily_budget_minor"])
                - _daily_policy_spend(conn, int(policy["id"]), now),
            )
            value = min(value, remaining, _balance(conn, policy["budget_account_key"]))
            subsidy += max(0, value)
            applied.append({
                "policy_id": int(policy["id"]),
                "policy_type": "subsidy",
                "eligible": True,
                "income_group": group,
                "value_minor": value,
            })
    return {
        "gross_price_minor": int(gross_price_minor),
        "capped_price_minor": capped,
        "private_price_minor": max(0, capped - subsidy),
        "subsidy_minor": subsidy,
        "price_cap_minor": max(0, gross_price_minor - capped),
        "policies": applied,
        "evaluated_at": now.isoformat(),
    }


def settle_market_policy_benefits(
    conn,
    *,
    resident_id: int,
    mechanism_id: int,
    provider_actor_key: str,
    policy_terms: dict,
    quantity: int,
    source_key: str,
    world_time=None,
) -> list[dict]:
    now = _now(world_time or policy_terms.get("evaluated_at"))
    results = []
    for applied in policy_terms.get("policies", []):
        policy = conn.execute(
            "SELECT * FROM policy_instruments WHERE id = ?",
            (applied["policy_id"],),
        ).fetchone()
        if not policy:
            continue
        value = int(applied["value_minor"]) * int(quantity)
        benefit_key = f"policy-benefit:{policy['id']}:{source_key}"
        existing = conn.execute(
            "SELECT * FROM policy_benefits WHERE benefit_key = ?",
            (benefit_key,),
        ).fetchone()
        if existing:
            results.append(dict(existing))
            continue
        ledger_id = None
        status = "delivered"
        public_cost = value if policy["policy_type"] == "subsidy" else 0
        if public_cost:
            remaining = max(
                0,
                int(policy["daily_budget_minor"])
                - _daily_policy_spend(conn, int(policy["id"]), now),
            )
            if public_cost > remaining or public_cost > _balance(conn, policy["budget_account_key"]):
                status = "unfunded"
                public_cost = 0
            else:
                transfer = post_money_transfer_minor(
                    conn,
                    transaction_key=f"{benefit_key}:ledger",
                    from_account_key=policy["budget_account_key"],
                    to_account_key=f"{provider_actor_key}:cash",
                    amount_minor=public_cost,
                    transaction_type="policy_subsidy",
                    source_type="policy_benefit",
                    source_id=str(policy["id"]),
                    description=policy["name"],
                    metadata={"resident_id": resident_id, "mechanism_id": mechanism_id},
                )
                ledger_id = int(transfer["id"])
                conn.execute(
                    """
                    UPDATE policy_instruments
                    SET spent_minor = spent_minor + ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (public_cost, policy["id"]),
                )
        cursor = conn.execute(
            """
            INSERT INTO policy_benefits
            (benefit_key, policy_id, resident_id, beneficiary_actor_key,
             target_type, target_id, gross_value_minor, public_cost_minor,
             private_cost_minor, welfare_delta, status,
             ledger_transaction_id, occurred_at, details_json)
            VALUES (?, ?, ?, ?, 'market', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                benefit_key, policy["id"], resident_id, f"resident:{resident_id}",
                str(mechanism_id), value, public_cost,
                int(policy_terms["private_price_minor"]) * int(quantity),
                min(100, value // 20), status, ledger_id, now.isoformat(),
                _json({"income_group": applied["income_group"], "source_key": source_key}),
            ),
        )
        results.append(dict(conn.execute(
            "SELECT * FROM policy_benefits WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()))
    return results


def _service_residents(conn, service):
    if service["access_mode"] == "location":
        return conn.execute(
            "SELECT id, role, location FROM residents WHERE location = ? ORDER BY id",
            (service["location"],),
        ).fetchall()
    return conn.execute("SELECT id, role, location FROM residents ORDER BY id").fetchall()


def operate_public_services(conn, world_time=None) -> dict:
    if not public_policy_runtime_available(conn):
        return {"available": False, "operations": [], "usages": []}
    now = _now(world_time)
    day = now.date().isoformat()
    operation_ids = []
    usage_ids = []
    for service in conn.execute(
        "SELECT * FROM public_services WHERE status <> 'paused' ORDER BY id"
    ).fetchall():
        residents = _service_residents(conn, service)
        investment = conn.execute(
            """
            SELECT * FROM policy_instruments
            WHERE policy_type = 'public_investment' AND status = 'active'
              AND target_type = 'public_service' AND target_key = ?
            ORDER BY id LIMIT 1
            """,
            (service["service_key"],),
        ).fetchone()
        capacity = int(service["daily_capacity"])
        quality = int(service["quality"])
        investment_cost = 0
        if investment:
            params = _load(investment["parameters_json"])
            capacity += int(params.get("capacity_increment", 0))
            quality = min(100, quality + int(params.get("quality_increment", 0)))
            investment_cost = min(
                int(investment["daily_budget_minor"]),
                _balance(conn, investment["budget_account_key"]),
            )
        served = min(capacity, len(residents))
        operating_cost = (
            int(service["base_daily_cost_minor"])
            + served * int(service["marginal_cost_minor"])
            + investment_cost
        )
        funded = min(operating_cost, _balance(conn, PUBLIC_FUND_CASH))
        operation_key = f"public-service:{service['service_key']}:{day}"
        existing = conn.execute(
            "SELECT * FROM public_service_operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()
        if existing:
            operation_ids.append(int(existing["id"]))
            continue
        ledger_id = None
        if funded:
            expense = ensure_ledger_account(
                conn,
                actor_key=PUBLIC_FUND_ACTOR,
                account_code="public_service_expense",
                account_type="expense",
                normal_side="debit",
            )
            transaction = post_money_transfer_minor(
                conn,
                transaction_key=f"{operation_key}:cost",
                from_account_key=PUBLIC_FUND_CASH,
                to_account_key="system:campus-services:cash",
                amount_minor=funded,
                transaction_type="public_service_operation",
                source_type="public_service",
                source_id=str(service["id"]),
                description=f"{service['name']} {day} 运行成本",
                metadata={"expense_account_key": expense["account_key"]},
            )
            ledger_id = int(transaction["id"])
        effective_capacity = capacity if funded >= operating_cost else int(capacity * funded / max(1, operating_cost))
        effective_served = min(effective_capacity, len(residents))
        status = "open"
        if funded < operating_cost:
            status = "underfunded"
        elif len(residents) > effective_capacity:
            status = "capacity_limited"
        cursor = conn.execute(
            """
            INSERT INTO public_service_operations
            (operation_key, service_id, operation_date, available_capacity,
             used_capacity, denied_count, operating_cost_minor,
             funded_cost_minor, quality, status, ledger_transaction_id,
             details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_key, service["id"], day, effective_capacity,
                effective_served, max(0, len(residents) - effective_served),
                operating_cost, funded, quality, status, ledger_id,
                _json({"requested": len(residents), "investment_cost_minor": investment_cost}),
            ),
        )
        operation_id = int(cursor.lastrowid)
        operation_ids.append(operation_id)
        for index, resident in enumerate(residents):
            served_now = index < effective_served
            usage_key = f"{operation_key}:resident:{resident['id']}"
            wait = int(index / max(1, effective_capacity) * 45) if served_now else 0
            welfare = max(1, quality // 12 - wait // 15) if served_now else -8
            usage_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO public_service_usages
                (usage_key, operation_id, service_id, resident_id, access_group,
                 location, units, wait_minutes, welfare_delta, status, reason,
                 occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    usage_key, operation_id, service["id"], resident["id"],
                    _resident_group(conn, int(resident["id"])), resident["location"],
                    wait, welfare, "served" if served_now else "denied",
                    "" if served_now else "公共服务容量或财政不足", now.isoformat(),
                ),
            )
            if usage_cursor.lastrowid:
                usage_ids.append(int(usage_cursor.lastrowid))
        if investment and investment_cost:
            conn.execute(
                """
                INSERT OR IGNORE INTO policy_benefits
                (benefit_key, policy_id, beneficiary_actor_key, target_type,
                 target_id, gross_value_minor, public_cost_minor,
                 private_cost_minor, welfare_delta, status,
                 ledger_transaction_id, occurred_at, details_json)
                VALUES (?, ?, ?, 'public_service', ?, ?, ?, 0, ?, 'delivered',
                        ?, ?, ?)
                """,
                (
                    f"policy-benefit:{investment['id']}:{operation_key}",
                    investment["id"], PUBLIC_FUND_ACTOR, str(service["id"]),
                    investment_cost, investment_cost,
                    min(100, int(_load(investment["parameters_json"]).get("quality_increment", 0)) * 5),
                    ledger_id, now.isoformat(),
                    _json({"service_key": service["service_key"], "operation_id": operation_id}),
                ),
            )
            conn.execute(
                "UPDATE policy_instruments SET spent_minor = spent_minor + ? WHERE id = ?",
                (investment_cost, investment["id"]),
            )
    return {"available": True, "operations": operation_ids, "usages": usage_ids}


def generate_externalities(conn, world_time=None) -> dict:
    if not public_policy_runtime_available(conn):
        return {"available": False, "events": [], "exposures": []}
    now = _now(world_time)
    hour = now.replace(minute=0, second=0, microsecond=0)
    event_ids = []
    exposure_ids = []
    locations = conn.execute(
        """
        SELECT location, COUNT(*) resident_count
        FROM residents WHERE location <> '' GROUP BY location ORDER BY location
        """
    ).fetchall()
    for location in locations:
        count = int(location["resident_count"])
        if count < 3:
            continue
        kind = "congestion" if count >= 5 else "noise"
        magnitude = min(100, 20 + count * 8)
        event_key = f"externality:{kind}:{location['location']}:{hour.isoformat()}"
        existing = conn.execute(
            "SELECT id FROM externality_events WHERE event_key = ?", (event_key,)
        ).fetchone()
        if existing:
            event_ids.append(int(existing["id"]))
            continue
        cursor = conn.execute(
            """
            INSERT INTO externality_events
            (event_key, externality_type, source_type, source_id, location,
             magnitude, direction, radius_meters, starts_at, ends_at,
             details_json)
            VALUES (?, ?, 'resident_aggregation', ?, ?, ?, 'negative', 80, ?, ?, ?)
            """,
            (
                event_key, kind, location["location"], location["location"],
                magnitude, hour.isoformat(), (hour + timedelta(hours=1)).isoformat(),
                _json({"resident_count": count, "causal_rule": "co_location_density"}),
            ),
        )
        event_id = int(cursor.lastrowid)
        event_ids.append(event_id)
        residents = conn.execute(
            "SELECT id FROM residents WHERE location = ? ORDER BY id",
            (location["location"],),
        ).fetchall()
        for resident in residents:
            exposure = min(100, magnitude + (int(resident["id"]) % 5) * 2)
            exposure_key = f"{event_key}:resident:{resident['id']}"
            exposure_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO externality_exposures
                (exposure_key, externality_event_id, resident_id, exposure_score,
                 welfare_delta, behavioral_pressure, distance_meters,
                 evidence_type, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 'co_location', ?)
                """,
                (
                    exposure_key, event_id, resident["id"], exposure,
                    -max(1, exposure // 10), min(100, exposure // 2), now.isoformat(),
                ),
            )
            if exposure_cursor.lastrowid:
                exposure_ids.append(int(exposure_cursor.lastrowid))

    if conn.execute("PRAGMA table_info(inventory_movements)").fetchall():
        waste_rows = conn.execute(
            """
            SELECT movement.id, movement.quantity_delta, movement.occurred_at,
                   account.location, account.owner_actor_key
            FROM inventory_movements movement
            JOIN inventory_accounts account
              ON account.id = movement.inventory_account_id
            WHERE movement.movement_type = 'waste'
              AND movement.occurred_at >= ? AND movement.occurred_at < ?
              AND account.location <> ''
            ORDER BY movement.id
            """,
            (hour.isoformat(), (hour + timedelta(hours=1)).isoformat()),
        ).fetchall()
        for waste in waste_rows:
            magnitude = min(100, max(10, abs(int(waste["quantity_delta"])) * 8))
            event_key = f"externality:pollution:waste:{waste['id']}"
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO externality_events
                (event_key, externality_type, source_type, source_id,
                 source_actor_key, location, magnitude, direction,
                 radius_meters, starts_at, ends_at, details_json)
                VALUES (?, 'pollution', 'inventory_movement', ?, ?, ?, ?,
                        'negative', 100, ?, ?, ?)
                """,
                (
                    event_key, str(waste["id"]), waste["owner_actor_key"],
                    waste["location"], magnitude, waste["occurred_at"],
                    (hour + timedelta(hours=2)).isoformat(),
                    _json({"quantity_delta": int(waste["quantity_delta"]), "causal_rule": "recorded_waste"}),
                ),
            )
            event = conn.execute(
                "SELECT id FROM externality_events WHERE event_key = ?", (event_key,)
            ).fetchone()
            event_id = int(event["id"])
            event_ids.append(event_id)
            for resident in conn.execute(
                "SELECT id FROM residents WHERE location = ? ORDER BY id",
                (waste["location"],),
            ).fetchall():
                exposure_cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO externality_exposures
                    (exposure_key, externality_event_id, resident_id,
                     exposure_score, welfare_delta, behavioral_pressure,
                     distance_meters, evidence_type, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 'co_location', ?)
                    """,
                    (
                        f"{event_key}:resident:{resident['id']}", event_id,
                        resident["id"], magnitude, -max(1, magnitude // 8),
                        min(100, magnitude // 2), now.isoformat(),
                    ),
                )
                if exposure_cursor.lastrowid:
                    exposure_ids.append(int(exposure_cursor.lastrowid))

    if conn.execute("PRAGMA table_info(organization_commitments)").fetchall():
        commitments = conn.execute(
            """
            SELECT commitment.id, commitment.organization_id, commitment.status,
                   commitment.resolved_at, organization.name
            FROM organization_commitments commitment
            JOIN campus_organizations organization
              ON organization.id = commitment.organization_id
            WHERE commitment.status IN ('fulfilled', 'breached')
              AND commitment.resolved_at >= ?
              AND commitment.resolved_at < ?
            ORDER BY commitment.id
            """,
            (hour.isoformat(), (hour + timedelta(hours=1)).isoformat()),
        ).fetchall()
        for commitment in commitments:
            positive = commitment["status"] == "fulfilled"
            magnitude = 30 if positive else 55
            event_key = f"externality:reputation:commitment:{commitment['id']}:{commitment['status']}"
            conn.execute(
                """
                INSERT OR IGNORE INTO externality_events
                (event_key, externality_type, source_type, source_id,
                 source_actor_key, magnitude, direction, radius_meters,
                 starts_at, ends_at, details_json)
                VALUES (?, 'reputation', 'organization_commitment', ?, ?, ?, ?,
                        0, ?, ?, ?)
                """,
                (
                    event_key, str(commitment["id"]),
                    f"organization:{commitment['organization_id']}", magnitude,
                    "positive" if positive else "negative",
                    commitment["resolved_at"], (hour + timedelta(days=3)).isoformat(),
                    _json({"organization_name": commitment["name"], "status": commitment["status"]}),
                ),
            )
            event = conn.execute(
                "SELECT id FROM externality_events WHERE event_key = ?", (event_key,)
            ).fetchone()
            event_id = int(event["id"])
            event_ids.append(event_id)
            members = conn.execute(
                """
                SELECT DISTINCT resident_id
                FROM organization_role_assignments
                WHERE organization_id = ? AND status = 'active'
                ORDER BY resident_id
                """,
                (commitment["organization_id"],),
            ).fetchall()
            for member in members:
                welfare = max(1, magnitude // 10) * (1 if positive else -1)
                pressure = magnitude // 3 * (1 if positive else -1)
                exposure_cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO externality_exposures
                    (exposure_key, externality_event_id, resident_id,
                     exposure_score, welfare_delta, behavioral_pressure,
                     distance_meters, evidence_type, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 'organization', ?)
                    """,
                    (
                        f"{event_key}:resident:{member['resident_id']}",
                        event_id, member["resident_id"], magnitude, welfare,
                        pressure, now.isoformat(),
                    ),
                )
                if exposure_cursor.lastrowid:
                    exposure_ids.append(int(exposure_cursor.lastrowid))

    library = conn.execute(
        """
        SELECT operation.id, operation.operation_date, service.location
        FROM public_service_operations operation
        JOIN public_services service ON service.id = operation.service_id
        WHERE service.service_type = 'library' AND operation.operation_date = ?
        """,
        (now.date().isoformat(),),
    ).fetchone()
    if library:
        event_key = f"externality:knowledge:{library['id']}"
        existing = conn.execute(
            "SELECT id FROM externality_events WHERE event_key = ?", (event_key,)
        ).fetchone()
        if not existing:
            cursor = conn.execute(
                """
                INSERT INTO externality_events
                (event_key, externality_type, source_type, source_id, location,
                 magnitude, direction, radius_meters, starts_at, ends_at,
                 details_json)
                VALUES (?, 'knowledge_spillover', 'public_service', ?, ?,
                        35, 'positive', 120, ?, ?, ?)
                """,
                (
                    event_key, str(library["id"]), library["location"],
                    now.isoformat(), (now + timedelta(days=1)).isoformat(),
                    _json({"causal_rule": "shared_library_access"}),
                ),
            )
            event_id = int(cursor.lastrowid)
            event_ids.append(event_id)
            users = conn.execute(
                """
                SELECT resident_id FROM public_service_usages
                WHERE operation_id = ? AND status = 'served'
                """,
                (library["id"],),
            ).fetchall()
            for user in users:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO externality_exposures
                    (exposure_key, externality_event_id, resident_id,
                     exposure_score, welfare_delta, behavioral_pressure,
                     distance_meters, evidence_type, occurred_at)
                    VALUES (?, ?, ?, 35, 5, 8, 0, 'global_service', ?)
                    """,
                    (f"{event_key}:resident:{user['resident_id']}", event_id, user["resident_id"], now.isoformat()),
                )
                if cursor.lastrowid:
                    exposure_ids.append(int(cursor.lastrowid))
    return {"available": True, "events": event_ids, "exposures": exposure_ids}


def capture_policy_outcomes(conn, world_time=None) -> dict:
    if not public_policy_runtime_available(conn):
        return {"available": False, "snapshots": []}
    now = _now(world_time)
    day_start = f"{now.date().isoformat()}T00:00:00"
    day_end = f"{(now.date() + timedelta(days=1)).isoformat()}T00:00:00"
    snapshot_ids = []
    policies = conn.execute(
        "SELECT * FROM policy_instruments WHERE status IN ('active', 'budget_exhausted') ORDER BY id"
    ).fetchall()
    groups = ("low", "middle", "high", "unknown")
    residents_by_group = {group: [] for group in groups}
    for resident in conn.execute("SELECT id FROM residents ORDER BY id").fetchall():
        residents_by_group[_resident_group(conn, int(resident["id"]))].append(int(resident["id"]))
    for policy in policies:
        for group in groups:
            resident_ids = residents_by_group[group]
            if not resident_ids:
                continue
            eligible_ids = [
                resident_id
                for resident_id in resident_ids
                if _eligible(conn, policy, resident_id)[0]
            ]
            placeholders = ",".join("?" for _ in resident_ids)
            row = conn.execute(
                f"""
                SELECT COUNT(DISTINCT resident_id) reached,
                       COALESCE(SUM(public_cost_minor), 0) public_cost,
                       COALESCE(AVG(private_cost_minor), 0) private_cost,
                       COALESCE(AVG(welfare_delta), 0) welfare,
                       COUNT(*) behavior_count
                FROM policy_benefits
                WHERE policy_id = ? AND resident_id IN ({placeholders})
                  AND occurred_at >= ? AND occurred_at < ?
                """,
                (policy["id"], *resident_ids, day_start, day_end),
            ).fetchone()
            key = f"policy-outcome:{policy['id']}:{now.date().isoformat()}:{group}"
            baseline = conn.execute(
                """
                SELECT * FROM policy_outcome_snapshots
                WHERE policy_id = ? AND window_type = 'baseline' AND group_key = ?
                ORDER BY id LIMIT 1
                """,
                (policy["id"], group),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO policy_outcome_snapshots
                (snapshot_key, policy_id, window_type, window_start, window_end,
                 group_key, eligible_count, reached_count, public_cost_minor,
                 average_private_cost_minor, average_welfare_delta,
                 behavior_count, baseline_json, outcome_json)
                VALUES (?, ?, 'daily', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key, policy["id"], day_start, day_end, group, len(eligible_ids),
                    int(row["reached"]), int(row["public_cost"]),
                    int(round(float(row["private_cost"]))),
                    float(row["welfare"]), int(row["behavior_count"]),
                    _json(dict(baseline) if baseline else {"metric_source": "bottom_up"}),
                    _json({
                        "coverage_rate": int(row["reached"]) / max(1, len(eligible_ids)),
                        "group_population": len(resident_ids),
                    }),
                ),
            )
            if cursor.lastrowid:
                snapshot_ids.append(int(cursor.lastrowid))
    return {"available": True, "snapshots": snapshot_ids}


def ensure_policy_baselines(conn, world_time=None) -> list[int]:
    if not public_policy_runtime_available(conn):
        return []
    now = _now(world_time)
    groups: dict[str, list[int]] = {"low": [], "middle": [], "high": [], "unknown": []}
    for resident in conn.execute("SELECT id FROM residents ORDER BY id").fetchall():
        resident_id = int(resident["id"])
        groups[_resident_group(conn, resident_id)].append(resident_id)
    created = []
    for policy in conn.execute(
        "SELECT * FROM policy_instruments ORDER BY id"
    ).fetchall():
        for group, resident_ids in groups.items():
            if not resident_ids:
                continue
            eligible_count = sum(
                1 for resident_id in resident_ids if _eligible(conn, policy, resident_id)[0]
            )
            key = f"policy-baseline:{policy['id']}:{group}"
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO policy_outcome_snapshots
                (snapshot_key, policy_id, window_type, window_start, window_end,
                 group_key, eligible_count, reached_count, public_cost_minor,
                 average_private_cost_minor, average_welfare_delta,
                 behavior_count, baseline_json, outcome_json)
                VALUES (?, ?, 'baseline', ?, ?, ?, ?, 0, 0, 0, 0, 0, ?, ?)
                """,
                (
                    key, policy["id"], policy["starts_at"], now.isoformat(), group,
                    eligible_count,
                    _json({"metric_source": "pre_implementation", "group_population": len(resident_ids)}),
                    _json({"coverage_rate": 0, "public_cost_minor": 0}),
                ),
            )
            if cursor.lastrowid:
                created.append(int(cursor.lastrowid))
    return created


def process_public_policy_runtime(conn, world_time=None) -> dict:
    if not public_policy_runtime_available(conn):
        return {
            "available": False,
            "public_services": {},
            "externalities": {},
            "policy_outcomes": {},
        }
    services = operate_public_services(conn, world_time)
    externalities = generate_externalities(conn, world_time)
    outcomes = capture_policy_outcomes(conn, world_time)
    return {
        "available": True,
        "public_services": services,
        "externalities": externalities,
        "policy_outcomes": outcomes,
        "public_fund_cash_minor": _balance(conn, PUBLIC_FUND_CASH),
        "rule_version": RULE_VERSION,
    }
