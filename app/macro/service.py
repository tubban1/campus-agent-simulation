from __future__ import annotations

from typing import Optional

from datetime import date, datetime, time, timedelta, timezone
import hashlib
from app.json_utils import json_dumps

from app.economy.service import reconcile_ledger


RULE_VERSION = "macro-runtime-v1"


METRIC_DEFINITIONS = (
    ("total_cash_minor", "居民现金存量", "money", "minor", "stock", "sum",
     ["ledger_accounts", "economic_actors"], "居民经济主体现金账户余额之和。"),
    ("total_savings_minor", "居民储蓄存量", "money", "minor", "stock", "sum",
     ["ledger_accounts", "economic_actors"], "居民经济主体储蓄账户余额之和。"),
    ("outstanding_debt_minor", "居民未偿债务", "money", "minor", "stock", "sum",
     ["credit_contracts"], "未结清信用合同的本金与应计利息。"),
    ("inventory_value_minor", "库存账面价值", "money", "minor", "stock", "sum",
     ["inventory_accounts"], "现有库存数量乘移动平均成本。"),
    ("income_flow_minor", "期间居民收入", "income", "minor", "flow", "sum",
     ["income_payments"], "窗口内已入账的工资、奖助和转移收入。"),
    ("consumption_flow_minor", "期间消费支出", "consumption", "minor", "flow", "sum",
     ["ledger_transactions", "ledger_entries"], "窗口内有真实结算依据的消费和必要支出。"),
    ("external_inflow_minor", "外部资金流入", "money", "minor", "flow", "sum",
     ["ledger_authorized_operations", "ledger_transactions"], "窗口内获授权的外部流入。"),
    ("price_index_basis_points", "校园价格指数", "market", "basis_points", "index",
     "weighted_mean", ["market_price_snapshots", "market_mechanisms"],
     "各市场最新价格相对基准价的需求加权指数，10000 为基期。"),
    ("fulfilled_demand_count", "已满足需求量", "market", "count", "flow", "sum",
     ["market_demand_signals"], "窗口内已满足或替代满足的需求数量。"),
    ("stockout_rate_basis_points", "缺货配给率", "market", "basis_points", "ratio",
     "ratio", ["market_demand_signals"], "缺货或配给需求占全部已观测需求比例。"),
    ("cash_gini_basis_points", "现金基尼系数", "distribution", "basis_points", "ratio",
     "gini", ["ledger_accounts", "economic_actors"], "居民现金余额分布的基尼系数。"),
    ("income_gini_basis_points", "期间收入基尼系数", "distribution", "basis_points",
     "ratio", "gini", ["income_payments"], "窗口内居民已入账收入分布的基尼系数。"),
    ("public_service_units", "公共服务供给量", "public_service", "count", "flow", "sum",
     ["public_service_usages"], "窗口内成功交付的公共服务单位。"),
    ("service_denial_rate_basis_points", "公共服务拒绝率", "public_service",
     "basis_points", "ratio", "ratio", ["public_service_usages"],
     "被拒绝或不符合资格的使用请求占全部请求比例。"),
    ("policy_coverage_basis_points", "政策覆盖率", "policy", "basis_points", "ratio",
     "ratio", ["policy_benefits"], "已交付政策利益占有资格记录比例。"),
    ("public_cost_minor", "公共财政支出", "policy", "minor", "flow", "sum",
     ["policy_benefits", "public_service_operations"], "政策利益和公共服务的已筹资成本。"),
    ("organization_fulfillment_basis_points", "组织履约率", "organization",
     "basis_points", "ratio", "ratio", ["organization_commitments"],
     "窗口内已履行承诺占已关闭承诺比例。"),
    ("welfare_delta", "可观测福利变化", "welfare", "score", "flow", "sum",
     ["policy_benefits", "public_service_usages", "externality_exposures"],
     "政策、公共服务和外部性产生的可观测福利变化之和。"),
)


CONSUMPTION_TRANSACTION_TYPES = (
    "goods_consumption",
    "service_delivery",
    "market_friction_cost",
    "economic_shock_expense",
    "institutional_sanction",
    "required_tuition",
    "required_housing",
    "required_food",
    "required_transport",
    "required_learning",
)


def _json(value) -> str:
    return json_dumps(value or {}, ensure_ascii=False, sort_keys=True)


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def _table_exists(conn, table_name: str) -> bool:
    return bool(conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def macro_runtime_available(conn) -> bool:
    return _table_exists(conn, "macro_snapshots")


def _window(now: datetime, window_type: str) -> tuple[datetime, datetime]:
    if window_type == "weekly":
        start_date = now.date() - timedelta(days=now.weekday())
    else:
        start_date = now.date()
    start = datetime.combine(start_date, time.min, tzinfo=now.tzinfo or timezone.utc)
    if window_type == "weekly":
        return start, start + timedelta(days=7)
    return start, start + timedelta(days=1)


def seed_macro_runtime(conn) -> dict:
    created = 0
    for definition in METRIC_DEFINITIONS:
        existing = conn.execute(
            "SELECT id FROM macro_metric_definitions WHERE metric_key = ?",
            (definition[0],),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO macro_metric_definitions
            (metric_key, name, category, unit, stock_flow_type,
             aggregation_method, source_tables_json, description, rule_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*definition[:6], _json(definition[6]), definition[7], RULE_VERSION),
        )
        created += int(existing is None)
    return {
        "definitions": int(conn.execute(
            "SELECT COUNT(*) value FROM macro_metric_definitions"
        ).fetchone()["value"]),
        "created": created,
    }


def _gini(values: list[int]) -> int:
    ordered = sorted(max(0, int(value)) for value in values)
    count = len(ordered)
    total = sum(ordered)
    if not count or not total:
        return 0
    weighted = sum((index + 1) * value for index, value in enumerate(ordered))
    value = (2 * weighted) / (count * total) - (count + 1) / count
    return max(0, min(10000, round(value * 10000)))


def _income_group(expected_income: int, disposable: int) -> str:
    if expected_income <= 2500 or disposable <= 1500:
        return "low"
    if expected_income >= 7000 and disposable >= 5000:
        return "high"
    return "middle"


def _resident_groups(conn) -> dict[int, dict[str, str]]:
    rows = conn.execute(
        """
        WITH latest_budget AS (
            SELECT resident_id, expected_income_minor, disposable_minor,
                   ROW_NUMBER() OVER (PARTITION BY resident_id ORDER BY budget_date DESC, id DESC) AS rn
            FROM household_budget_snapshots
        )
        SELECT resident.id, resident.role,
               COALESCE(budget.expected_income_minor, 0) expected_income_minor,
               COALESCE(budget.disposable_minor, 0) disposable_minor
        FROM residents resident
        LEFT JOIN latest_budget budget ON budget.resident_id = resident.id AND budget.rn = 1
        ORDER BY resident.id
        """
    ).fetchall()
    return {
        int(row["id"]): {
            "role": str(row["role"] or "unknown"),
            "income_group": _income_group(
                int(row["expected_income_minor"]), int(row["disposable_minor"])
            ),
        }
        for row in rows
    }


def _source_fingerprint(conn) -> tuple[str, int, int, dict]:
    cursors = {}
    for table in (
        "world_event_stream",
        "ledger_transactions",
        "income_payments",
        "inventory_movements",
        "household_budget_snapshots",
        "credit_contracts",
        "market_price_snapshots",
        "market_demand_signals",
        "public_service_operations",
        "public_service_usages",
        "policy_benefits",
        "organization_commitments",
        "externality_exposures",
    ):
        if not _table_exists(conn, table):
            cursors[table] = {"count": 0, "max_id": 0}
            continue
        row = conn.execute(
            f"SELECT COUNT(*) value, COALESCE(MAX(id), 0) max_id FROM {table}"
        ).fetchone()
        cursors[table] = {"count": int(row["value"]), "max_id": int(row["max_id"])}
    payload = _json(cursors)
    return (
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        cursors["world_event_stream"]["max_id"],
        cursors["ledger_transactions"]["max_id"],
        cursors,
    )


def _metric_ids(conn) -> dict[str, int]:
    return {
        row["metric_key"]: int(row["id"])
        for row in conn.execute(
            "SELECT id, metric_key FROM macro_metric_definitions WHERE status = 'active'"
        ).fetchall()
    }


def _save_metric(
    conn,
    metric_ids: dict[str, int],
    snapshot_id: int,
    metric_key: str,
    value: float,
    *,
    numerator: float = 0,
    denominator: float = 0,
    sample_count: int = 0,
    group_type: str = "overall",
    group_key: str = "all",
    explanation: str = "",
    components: Optional[list[dict]] = None,
    quality_status: str = "verified",
    metadata: Optional[dict] = None,
) -> int:
    definition_id = metric_ids[metric_key]
    conn.execute(
        """
        INSERT INTO macro_metric_values
        (snapshot_id, metric_definition_id, group_type, group_key, value,
         numerator, denominator, sample_count, quality_status, explanation,
         metadata_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (snapshot_id, metric_definition_id, group_type, group_key)
        DO UPDATE SET
            value = excluded.value,
            numerator = excluded.numerator,
            denominator = excluded.denominator,
            sample_count = excluded.sample_count,
            quality_status = excluded.quality_status,
            explanation = excluded.explanation,
            metadata_json = excluded.metadata_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            snapshot_id, definition_id, group_type, group_key, float(value),
            float(numerator), float(denominator), int(sample_count), quality_status,
            explanation, _json(metadata),
        ),
    )
    metric_value = conn.execute(
        """
        SELECT id FROM macro_metric_values
        WHERE snapshot_id = ? AND metric_definition_id = ?
          AND group_type = ? AND group_key = ?
        """,
        (snapshot_id, definition_id, group_type, group_key),
    ).fetchone()
    metric_value_id = int(metric_value["id"])
    conn.execute(
        "DELETE FROM macro_metric_components WHERE metric_value_id = ?",
        (metric_value_id,),
    )
    for component in components or []:
        conn.execute(
            """
            INSERT INTO macro_metric_components
            (metric_value_id, source_table, source_id, component_key,
             contribution, weight, occurred_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metric_value_id,
                component["source_table"],
                str(component["source_id"]),
                str(component.get("component_key", "")),
                float(component.get("contribution", 0)),
                float(component.get("weight", 1)),
                str(component.get("occurred_at", "")),
                _json(component.get("metadata")),
            ),
        )
    return metric_value_id


def _cash_and_savings(conn, groups, metric_ids, snapshot_id) -> dict:
    rows = conn.execute(
        """
        SELECT resident.id resident_id, actor.actor_key, account.id account_id,
               account.account_code, account.balance_minor
        FROM residents resident
        JOIN economic_actors actor ON actor.resident_id = resident.id
        JOIN ledger_accounts account ON account.actor_id = actor.id
        WHERE account.account_code IN ('cash', 'savings')
          AND account.status = 'active'
        ORDER BY resident.id, account.id
        """
    ).fetchall()
    result = {}
    for account_code, metric_key in (
        ("cash", "total_cash_minor"),
        ("savings", "total_savings_minor"),
    ):
        selected = [row for row in rows if row["account_code"] == account_code]
        components = [
            {
                "source_table": "ledger_accounts",
                "source_id": row["account_id"],
                "component_key": row["actor_key"],
                "contribution": int(row["balance_minor"]),
                "metadata": {"resident_id": int(row["resident_id"])},
            }
            for row in selected
        ]
        total = sum(int(row["balance_minor"]) for row in selected)
        _save_metric(
            conn, metric_ids, snapshot_id, metric_key, total,
            sample_count=len(selected), components=components,
            explanation="居民经济主体对应账户的当前余额求和。",
        )
        result[account_code] = total
        for group_type in ("role", "income_group"):
            grouped = {}
            for row in selected:
                key = groups[int(row["resident_id"])][group_type]
                grouped.setdefault(key, []).append(row)
            for key, group_rows in grouped.items():
                _save_metric(
                    conn, metric_ids, snapshot_id, metric_key,
                    sum(int(row["balance_minor"]) for row in group_rows),
                    group_type=group_type, group_key=key,
                    sample_count=len(group_rows),
                    components=[
                        {
                            "source_table": "ledger_accounts",
                            "source_id": row["account_id"],
                            "component_key": row["actor_key"],
                            "contribution": int(row["balance_minor"]),
                            "metadata": {"resident_id": int(row["resident_id"])},
                        }
                        for row in group_rows
                    ],
                    explanation=f"按 {group_type} 分组的居民账户余额。",
                )
    cash_values = [
        int(row["balance_minor"]) for row in rows if row["account_code"] == "cash"
    ]
    _save_metric(
        conn, metric_ids, snapshot_id, "cash_gini_basis_points",
        _gini(cash_values), numerator=_gini(cash_values), denominator=10000,
        sample_count=len(cash_values),
        components=[
            {
                "source_table": "ledger_accounts",
                "source_id": row["account_id"],
                "component_key": row["actor_key"],
                "contribution": int(row["balance_minor"]),
                "metadata": {"observation": True},
            }
            for row in rows if row["account_code"] == "cash"
        ],
        explanation="按居民现金余额计算，10000 表示 1.0。",
    )
    return result


def _income_metrics(conn, groups, metric_ids, snapshot_id, start, end) -> dict:
    rows = conn.execute(
        """
        SELECT payment.id, payment.recipient_actor_key, payment.amount_minor,
               payment.paid_at, actor.resident_id
        FROM income_payments payment
        JOIN economic_actors actor
          ON actor.actor_key = payment.recipient_actor_key
        WHERE payment.status = 'posted'
          AND payment.paid_at >= ? AND payment.paid_at < ?
          AND actor.resident_id IS NOT NULL
        ORDER BY payment.id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    components = [
        {
            "source_table": "income_payments",
            "source_id": row["id"],
            "component_key": row["recipient_actor_key"],
            "contribution": int(row["amount_minor"]),
            "occurred_at": row["paid_at"],
            "metadata": {"resident_id": int(row["resident_id"])},
        }
        for row in rows
    ]
    total = sum(int(row["amount_minor"]) for row in rows)
    _save_metric(
        conn, metric_ids, snapshot_id, "income_flow_minor", total,
        sample_count=len(rows), components=components,
        explanation="窗口内状态为 posted 的居民收入支付。",
    )
    per_resident = {resident_id: 0 for resident_id in groups}
    for row in rows:
        per_resident[int(row["resident_id"])] += int(row["amount_minor"])
    _save_metric(
        conn, metric_ids, snapshot_id, "income_gini_basis_points",
        _gini(list(per_resident.values())), numerator=_gini(list(per_resident.values())),
        denominator=10000, sample_count=len(per_resident),
        components=[
            {
                "source_table": "income_payments",
                "source_id": f"resident:{resident_id}",
                "component_key": "resident_window_income",
                "contribution": amount,
                "metadata": {"resident_id": resident_id, "observation": True},
            }
            for resident_id, amount in sorted(per_resident.items())
        ],
        explanation="包含零收入居民的窗口收入基尼系数。",
    )
    for group_type in ("role", "income_group"):
        grouped = {}
        for row in rows:
            key = groups[int(row["resident_id"])][group_type]
            grouped.setdefault(key, []).append(row)
        for key, group_rows in grouped.items():
            _save_metric(
                conn, metric_ids, snapshot_id, "income_flow_minor",
                sum(int(row["amount_minor"]) for row in group_rows),
                group_type=group_type, group_key=key, sample_count=len(group_rows),
                components=[
                    component for component in components
                    if groups[int(component["metadata"]["resident_id"])][group_type] == key
                ],
                explanation=f"按 {group_type} 分组的窗口收入。",
            )
    return {"total": total, "per_resident": per_resident}


def _ledger_flow_metrics(conn, metric_ids, snapshot_id, start, end) -> dict:
    placeholders = ", ".join("?" for _ in CONSUMPTION_TRANSACTION_TYPES)
    rows = conn.execute(
        f"""
        SELECT tx.id, tx.transaction_key,
               tx.transaction_type, tx.occurred_at,
               SUM(CASE WHEN entry.entry_side = 'debit'
                        THEN entry.amount_minor ELSE 0 END) debit_minor
        FROM ledger_transactions tx
        JOIN ledger_entries entry ON entry.transaction_id = tx.id
        WHERE tx.status IN ('posted', 'reversed')
          AND tx.occurred_at >= ? AND tx.occurred_at < ?
          AND tx.transaction_type IN ({placeholders})
        GROUP BY tx.id, tx.transaction_key,
                 tx.transaction_type, tx.occurred_at
        ORDER BY tx.id
        """,
        (start.isoformat(), end.isoformat(), *CONSUMPTION_TRANSACTION_TYPES),
    ).fetchall()
    def consumption_amount(row):
        debit = int(row["debit_minor"])
        return debit // 2 if row["transaction_type"] == "service_delivery" else debit

    components = [
        {
            "source_table": "ledger_transactions",
            "source_id": row["id"],
            "component_key": row["transaction_type"],
            "contribution": consumption_amount(row),
            "occurred_at": row["occurred_at"],
            "metadata": {"transaction_key": row["transaction_key"]},
        }
        for row in rows
    ]
    consumption = sum(consumption_amount(row) for row in rows)
    _save_metric(
        conn, metric_ids, snapshot_id, "consumption_flow_minor", consumption,
        sample_count=len(rows), components=components,
        explanation="消费类交易的借方总额；每笔平衡交易只计一次。",
    )
    inflows = conn.execute(
        """
        SELECT operation.transaction_id, tx.transaction_key,
               tx.occurred_at,
               SUM(CASE WHEN entry.entry_side = 'debit'
                        THEN entry.amount_minor ELSE 0 END) amount_minor
        FROM ledger_authorized_operations operation
        JOIN ledger_transactions tx
          ON tx.id = operation.transaction_id
        JOIN ledger_entries entry ON entry.transaction_id = tx.id
        WHERE operation.operation_type = 'external_inflow'
          AND tx.occurred_at >= ? AND tx.occurred_at < ?
        GROUP BY operation.transaction_id, tx.transaction_key,
                 tx.occurred_at
        ORDER BY operation.transaction_id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    external = sum(int(row["amount_minor"]) for row in inflows)
    _save_metric(
        conn, metric_ids, snapshot_id, "external_inflow_minor", external,
        sample_count=len(inflows),
        components=[
            {
                "source_table": "ledger_transactions",
                "source_id": row["transaction_id"],
                "component_key": "external_inflow",
                "contribution": int(row["amount_minor"]),
                "occurred_at": row["occurred_at"],
                "metadata": {"transaction_key": row["transaction_key"]},
            }
            for row in inflows
        ],
        explanation="仅统计具有授权记录的 external_inflow 交易。",
    )
    return {"consumption": consumption, "external_inflow": external}


def _stock_metrics(conn, metric_ids, snapshot_id) -> dict:
    contracts = conn.execute(
        """
        SELECT id, contract_key, outstanding_principal_minor,
               accrued_interest_minor, status
        FROM credit_contracts
        WHERE status IN ('active', 'late', 'defaulted', 'restructured')
        ORDER BY id
        """
    ).fetchall()
    debt = sum(
        int(row["outstanding_principal_minor"]) + int(row["accrued_interest_minor"])
        for row in contracts
    )
    _save_metric(
        conn, metric_ids, snapshot_id, "outstanding_debt_minor", debt,
        sample_count=len(contracts),
        components=[
            {
                "source_table": "credit_contracts",
                "source_id": row["id"],
                "component_key": row["contract_key"],
                "contribution": (
                    int(row["outstanding_principal_minor"])
                    + int(row["accrued_interest_minor"])
                ),
                "metadata": {"status": row["status"]},
            }
            for row in contracts
        ],
        explanation="所有未结清合同本金与应计利息。",
    )
    inventory = conn.execute(
        """
        SELECT id, inventory_key, quantity_on_hand, average_cost_minor
        FROM inventory_accounts WHERE status = 'active' ORDER BY id
        """
    ).fetchall()
    inventory_value = sum(
        int(row["quantity_on_hand"]) * int(row["average_cost_minor"])
        for row in inventory
    )
    _save_metric(
        conn, metric_ids, snapshot_id, "inventory_value_minor", inventory_value,
        sample_count=len(inventory),
        components=[
            {
                "source_table": "inventory_accounts",
                "source_id": row["id"],
                "component_key": row["inventory_key"],
                "contribution": (
                    int(row["quantity_on_hand"]) * int(row["average_cost_minor"])
                ),
                "metadata": {
                    "quantity": int(row["quantity_on_hand"]),
                    "unit_cost_minor": int(row["average_cost_minor"]),
                },
            }
            for row in inventory
        ],
        explanation="库存数量乘移动平均成本。",
    )
    return {"debt": debt, "inventory_value": inventory_value}


def _market_metrics(conn, metric_ids, snapshot_id, start, end, observed_at) -> dict:
    prices = conn.execute(
        """
        WITH latest_prices AS (
            SELECT id, mechanism_id, price_minor, base_price_minor, fulfilled_demand, valid_from,
                   ROW_NUMBER() OVER (PARTITION BY mechanism_id ORDER BY valid_from DESC, id DESC) AS rn
            FROM market_price_snapshots
            WHERE valid_from <= ?
        )
        SELECT price.id, price.mechanism_id, price.price_minor,
               price.base_price_minor, price.fulfilled_demand,
               price.valid_from, mechanism.mechanism_key
        FROM latest_prices price
        JOIN market_mechanisms mechanism ON mechanism.id = price.mechanism_id
        WHERE price.rn = 1
        ORDER BY price.mechanism_id
        """,
        (observed_at.isoformat(),),
    ).fetchall()
    weighted = []
    for row in prices:
        base = max(1, int(row["base_price_minor"]))
        index = round(int(row["price_minor"]) * 10000 / base)
        weight = max(1, int(row["fulfilled_demand"]))
        weighted.append((row, index, weight))
    numerator = sum(index * weight for _, index, weight in weighted)
    denominator = sum(weight for _, _, weight in weighted)
    price_index = round(numerator / denominator) if denominator else 0
    _save_metric(
        conn, metric_ids, snapshot_id, "price_index_basis_points", price_index,
        numerator=numerator, denominator=denominator, sample_count=len(prices),
        quality_status="verified" if prices else "insufficient",
        components=[
            {
                "source_table": "market_price_snapshots",
                "source_id": row["id"],
                "component_key": row["mechanism_key"],
                "contribution": index,
                "weight": weight,
                "occurred_at": row["valid_from"],
                "metadata": {
                    "price_minor": int(row["price_minor"]),
                    "base_price_minor": int(row["base_price_minor"]),
                },
            }
            for row, index, weight in weighted
        ],
        explanation="每个市场最新价格相对基准价，以已满足需求加一作为权重。",
    )
    demand = conn.execute(
        """
        SELECT id, status, quantity, occurred_at FROM market_demand_signals
        WHERE occurred_at >= ? AND occurred_at < ? ORDER BY id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    fulfilled_rows = [
        row for row in demand if row["status"] in ("fulfilled", "substituted")
    ]
    fulfilled = sum(int(row["quantity"]) for row in fulfilled_rows)
    _save_metric(
        conn, metric_ids, snapshot_id, "fulfilled_demand_count", fulfilled,
        sample_count=len(fulfilled_rows),
        components=[
            {
                "source_table": "market_demand_signals",
                "source_id": row["id"],
                "component_key": row["status"],
                "contribution": int(row["quantity"]),
                "occurred_at": row["occurred_at"],
            }
            for row in fulfilled_rows
        ],
        explanation="fulfilled 与 substituted 状态的需求数量。",
    )
    constrained = [
        row for row in demand if row["status"] in ("out_of_stock", "rationed")
    ]
    stockout_rate = round(len(constrained) * 10000 / len(demand)) if demand else 0
    _save_metric(
        conn, metric_ids, snapshot_id, "stockout_rate_basis_points", stockout_rate,
        numerator=len(constrained), denominator=len(demand), sample_count=len(demand),
        quality_status="verified" if demand else "insufficient",
        components=[
            {
                "source_table": "market_demand_signals",
                "source_id": row["id"],
                "component_key": row["status"],
                "contribution": 1 if row in constrained else 0,
                "occurred_at": row["occurred_at"],
            }
            for row in demand
        ],
        explanation="out_of_stock 或 rationed 请求数除以全部需求信号数。",
    )
    return {"price_index": price_index, "fulfilled": fulfilled, "stockout_rate": stockout_rate}


def _social_output_metrics(conn, metric_ids, snapshot_id, start, end) -> dict:
    usages = conn.execute(
        """
        SELECT id, status, units, welfare_delta, occurred_at
        FROM public_service_usages
        WHERE occurred_at >= ? AND occurred_at < ? ORDER BY id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    served = [row for row in usages if row["status"] == "served"]
    denied = [
        row for row in usages if row["status"] in ("denied", "not_eligible")
    ]
    units = sum(int(row["units"]) for row in served)
    denial_rate = round(len(denied) * 10000 / len(usages)) if usages else 0
    _save_metric(
        conn, metric_ids, snapshot_id, "public_service_units", units,
        sample_count=len(served),
        components=[
            {
                "source_table": "public_service_usages",
                "source_id": row["id"],
                "component_key": row["status"],
                "contribution": int(row["units"]),
                "occurred_at": row["occurred_at"],
            }
            for row in served
        ],
        explanation="状态为 served 的公共服务单位。",
    )
    _save_metric(
        conn, metric_ids, snapshot_id, "service_denial_rate_basis_points",
        denial_rate, numerator=len(denied), denominator=len(usages),
        sample_count=len(usages),
        quality_status="verified" if usages else "insufficient",
        components=[
            {
                "source_table": "public_service_usages",
                "source_id": row["id"],
                "component_key": row["status"],
                "contribution": 1 if row in denied else 0,
                "occurred_at": row["occurred_at"],
            }
            for row in usages
        ],
        explanation="denied 与 not_eligible 请求数除以全部公共服务请求数。",
    )
    benefits = conn.execute(
        """
        SELECT id, status, public_cost_minor, welfare_delta, occurred_at
        FROM policy_benefits
        WHERE occurred_at >= ? AND occurred_at < ? ORDER BY id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    eligible = [
        row for row in benefits
        if row["status"] in ("eligible", "delivered", "rationed", "unfunded")
    ]
    delivered = [row for row in benefits if row["status"] == "delivered"]
    coverage = round(len(delivered) * 10000 / len(eligible)) if eligible else 0
    _save_metric(
        conn, metric_ids, snapshot_id, "policy_coverage_basis_points", coverage,
        numerator=len(delivered), denominator=len(eligible), sample_count=len(eligible),
        quality_status="verified" if eligible else "insufficient",
        components=[
            {
                "source_table": "policy_benefits",
                "source_id": row["id"],
                "component_key": row["status"],
                "contribution": 1 if row["status"] == "delivered" else 0,
                "occurred_at": row["occurred_at"],
            }
            for row in eligible
        ],
        explanation="已交付利益数除以有资格、已交付、配给或未获资金的利益记录数。",
    )
    operations = conn.execute(
        """
        SELECT id, funded_cost_minor, operation_date
        FROM public_service_operations
        WHERE operation_date >= ? AND operation_date < ? ORDER BY id
        """,
        (start.date().isoformat(), end.date().isoformat()),
    ).fetchall()
    public_cost = (
        sum(int(row["public_cost_minor"]) for row in delivered)
        + sum(int(row["funded_cost_minor"]) for row in operations)
    )
    cost_components = [
        {
            "source_table": "policy_benefits",
            "source_id": row["id"],
            "component_key": "policy_benefit",
            "contribution": int(row["public_cost_minor"]),
            "occurred_at": row["occurred_at"],
        }
        for row in delivered
    ] + [
        {
            "source_table": "public_service_operations",
            "source_id": row["id"],
            "component_key": "funded_operation",
            "contribution": int(row["funded_cost_minor"]),
            "occurred_at": row["operation_date"],
        }
        for row in operations
    ]
    _save_metric(
        conn, metric_ids, snapshot_id, "public_cost_minor", public_cost,
        sample_count=len(cost_components), components=cost_components,
        explanation="已交付政策利益公共成本与公共服务已筹资运营成本。",
    )
    commitments = conn.execute(
        """
        SELECT id, status, resolved_at FROM organization_commitments
        WHERE status IN ('fulfilled', 'breached')
          AND resolved_at >= ? AND resolved_at < ? ORDER BY id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    fulfilled = [row for row in commitments if row["status"] == "fulfilled"]
    fulfillment = (
        round(len(fulfilled) * 10000 / len(commitments)) if commitments else 0
    )
    _save_metric(
        conn, metric_ids, snapshot_id, "organization_fulfillment_basis_points",
        fulfillment, numerator=len(fulfilled), denominator=len(commitments),
        sample_count=len(commitments),
        quality_status="verified" if commitments else "insufficient",
        components=[
            {
                "source_table": "organization_commitments",
                "source_id": row["id"],
                "component_key": row["status"],
                "contribution": 1 if row["status"] == "fulfilled" else 0,
                "occurred_at": row["resolved_at"],
            }
            for row in commitments
        ],
        explanation="窗口内 fulfilled 承诺除以 fulfilled 与 breached 承诺。",
    )
    exposures = conn.execute(
        """
        SELECT id, welfare_delta, occurred_at FROM externality_exposures
        WHERE occurred_at >= ? AND occurred_at < ? ORDER BY id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    welfare_components = [
        {
            "source_table": "public_service_usages",
            "source_id": row["id"],
            "component_key": "service",
            "contribution": int(row["welfare_delta"]),
            "occurred_at": row["occurred_at"],
        }
        for row in usages
    ] + [
        {
            "source_table": "policy_benefits",
            "source_id": row["id"],
            "component_key": "policy",
            "contribution": int(row["welfare_delta"]),
            "occurred_at": row["occurred_at"],
        }
        for row in benefits
    ] + [
        {
            "source_table": "externality_exposures",
            "source_id": row["id"],
            "component_key": "externality",
            "contribution": int(row["welfare_delta"]),
            "occurred_at": row["occurred_at"],
        }
        for row in exposures
    ]
    welfare = sum(int(item["contribution"]) for item in welfare_components)
    _save_metric(
        conn, metric_ids, snapshot_id, "welfare_delta", welfare,
        sample_count=len(welfare_components), components=welfare_components,
        explanation="政策、公共服务和外部性记录中的福利变化求和。",
    )
    return {
        "service_units": units,
        "denial_rate": denial_rate,
        "policy_coverage": coverage,
        "public_cost": public_cost,
        "organization_fulfillment": fulfillment,
        "welfare_delta": welfare,
    }


def _save_check(
    conn,
    snapshot_id: int,
    check_key: str,
    check_type: str,
    severity: str,
    status: str,
    expected: float,
    actual: float,
    source_tables: list[str],
    *,
    details=None,
    checked_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO macro_reconciliation_checks
        (snapshot_id, check_key, check_type, severity, status,
         expected_value, actual_value, difference, source_tables_json,
         details_json, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (snapshot_id, check_key) DO UPDATE SET
            status = excluded.status,
            expected_value = excluded.expected_value,
            actual_value = excluded.actual_value,
            difference = excluded.difference,
            source_tables_json = excluded.source_tables_json,
            details_json = excluded.details_json,
            checked_at = excluded.checked_at
        """,
        (
            snapshot_id, check_key, check_type, severity, status,
            float(expected), float(actual), float(actual - expected),
            _json(source_tables), _json(details), checked_at.isoformat(),
        ),
    )


def reconcile_macro_snapshot(conn, snapshot_id: int, checked_at=None) -> dict:
    now = _now(checked_at)
    conn.execute(
        "DELETE FROM macro_reconciliation_checks WHERE snapshot_id = ?",
        (snapshot_id,),
    )
    ledger = reconcile_ledger(conn)
    _save_check(
        conn, snapshot_id, "ledger-balanced", "ledger", "critical",
        "passed" if ledger["balanced"] else "failed", 0,
        len(ledger["transaction_imbalances"]) + len(ledger["account_mismatches"]),
        ["ledger_transactions", "ledger_entries", "ledger_accounts"],
        details=ledger, checked_at=now,
    )
    projection = conn.execute(
        """
        SELECT
          COALESCE(SUM(resident.money * 100), 0) projected_minor,
          COALESCE(SUM(cash.balance_minor), 0) ledger_minor
        FROM residents resident
        JOIN economic_actors actor ON actor.resident_id = resident.id
        JOIN ledger_accounts cash
          ON cash.actor_id = actor.id AND cash.account_code = 'cash'
        """
    ).fetchone()
    expected = int(projection["ledger_minor"])
    actual = int(projection["projected_minor"])
    _save_check(
        conn, snapshot_id, "resident-cash-projection", "projection", "warning",
        "passed" if expected == actual else "warning", expected, actual,
        ["residents", "economic_actors", "ledger_accounts"],
        checked_at=now,
    )
    inventory = conn.execute(
        """
        SELECT account.id, account.inventory_key, account.quantity_on_hand,
               COALESCE(SUM(movement.quantity_delta), 0) movement_quantity
        FROM inventory_accounts account
        LEFT JOIN inventory_movements movement
          ON movement.inventory_account_id = account.id
        GROUP BY account.id, account.inventory_key, account.quantity_on_hand
        ORDER BY account.id
        """
    ).fetchall()
    inventory_difference = sum(
        abs(int(row["quantity_on_hand"]) - int(row["movement_quantity"]))
        for row in inventory
    )
    _save_check(
        conn, snapshot_id, "inventory-movements", "inventory", "critical",
        "passed" if inventory_difference == 0 else "failed",
        0, inventory_difference,
        ["inventory_accounts", "inventory_movements"],
        details={
            "mismatches": [
                {
                    "inventory_key": row["inventory_key"],
                    "quantity_on_hand": int(row["quantity_on_hand"]),
                    "movement_quantity": int(row["movement_quantity"]),
                }
                for row in inventory
                if int(row["quantity_on_hand"]) != int(row["movement_quantity"])
            ]
        },
        checked_at=now,
    )
    credit = conn.execute(
        """
        SELECT
          COALESCE((SELECT SUM(outstanding_principal_minor)
                    FROM credit_profiles), 0) profile_minor,
          COALESCE((SELECT SUM(outstanding_principal_minor)
                    FROM credit_contracts
                    WHERE status IN ('active', 'late', 'defaulted', 'restructured')), 0)
                    contract_minor
        """
    ).fetchone()
    expected = int(credit["contract_minor"])
    actual = int(credit["profile_minor"])
    _save_check(
        conn, snapshot_id, "credit-profile-contracts", "credit", "critical",
        "passed" if expected == actual else "failed", expected, actual,
        ["credit_profiles", "credit_contracts"], checked_at=now,
    )
    missing_benefits = int(conn.execute(
        """
        SELECT COUNT(*) value FROM policy_benefits
        WHERE status = 'delivered' AND public_cost_minor > 0
          AND ledger_transaction_id IS NULL
        """
    ).fetchone()["value"])
    _save_check(
        conn, snapshot_id, "policy-ledger-coverage", "coverage", "critical",
        "passed" if missing_benefits == 0 else "failed", 0, missing_benefits,
        ["policy_benefits", "ledger_transactions"], checked_at=now,
    )
    missing_income = int(conn.execute(
        """
        SELECT COUNT(*) value FROM income_payments
        WHERE status = 'posted' AND ledger_transaction_id IS NULL
        """
    ).fetchone()["value"])
    _save_check(
        conn, snapshot_id, "income-ledger-coverage", "coverage", "critical",
        "passed" if missing_income == 0 else "failed", 0, missing_income,
        ["income_payments", "ledger_transactions"], checked_at=now,
    )
    additive = conn.execute(
        """
        SELECT value.id, definition.metric_key, value.value,
               COALESCE(SUM(component.contribution), 0) component_total
        FROM macro_metric_values value
        JOIN macro_metric_definitions definition
          ON definition.id = value.metric_definition_id
        LEFT JOIN macro_metric_components component
          ON component.metric_value_id = value.id
        WHERE value.snapshot_id = ? AND value.group_type = 'overall'
          AND definition.aggregation_method = 'sum'
        GROUP BY value.id, definition.metric_key, value.value
        ORDER BY value.id
        """,
        (snapshot_id,),
    ).fetchall()
    component_difference = sum(
        abs(float(row["value"]) - float(row["component_total"]))
        for row in additive
    )
    _save_check(
        conn, snapshot_id, "additive-component-sums", "components", "critical",
        "passed" if component_difference < 0.001 else "failed",
        0, component_difference,
        ["macro_metric_values", "macro_metric_components"],
        details={
            "mismatches": [
                {
                    "metric_key": row["metric_key"],
                    "value": float(row["value"]),
                    "component_total": float(row["component_total"]),
                }
                for row in additive
                if abs(float(row["value"]) - float(row["component_total"])) >= 0.001
            ]
        },
        checked_at=now,
    )
    checks = conn.execute(
        """
        SELECT status, severity, COUNT(*) value
        FROM macro_reconciliation_checks WHERE snapshot_id = ?
        GROUP BY status, severity
        """,
        (snapshot_id,),
    ).fetchall()
    failed = sum(
        int(row["value"]) for row in checks
        if row["status"] == "failed" and row["severity"] == "critical"
    )
    warnings = sum(
        int(row["value"]) for row in checks
        if row["status"] in ("warning", "failed") and row["severity"] != "critical"
    )
    status = "invalid" if failed else ("warning" if warnings else "valid")
    conn.execute(
        "UPDATE macro_snapshots SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, snapshot_id),
    )
    return {
        "status": status,
        "critical_failures": failed,
        "warnings": warnings,
        "checks": sum(int(row["value"]) for row in checks),
    }


def build_macro_snapshot(conn, world_time=None, window_type: str = "daily") -> dict:
    if not macro_runtime_available(conn):
        return {"available": False}
    if window_type not in ("daily", "weekly", "manual"):
        raise ValueError("不支持的宏观聚合窗口")
    now = _now(world_time)
    effective_window = "daily" if window_type == "manual" else window_type
    start, end = _window(now, effective_window)
    seed_macro_runtime(conn)
    fingerprint, event_cursor, transaction_cursor, source_cursors = _source_fingerprint(conn)
    snapshot_key = (
        f"macro:{window_type}:{start.isoformat()}:{end.isoformat()}"
        if window_type != "manual"
        else f"macro:manual:{now.isoformat()}:{fingerprint[:12]}"
    )
    existing = conn.execute(
        "SELECT * FROM macro_snapshots WHERE snapshot_key = ?", (snapshot_key,)
    ).fetchone()
    if existing and existing["state_fingerprint"] == fingerprint:
        return {
            "available": True,
            "snapshot_id": int(existing["id"]),
            "snapshot_key": snapshot_key,
            "status": existing["status"],
            "changed": False,
        }
    population = int(conn.execute("SELECT COUNT(*) value FROM residents").fetchone()["value"])
    if existing:
        snapshot_id = int(existing["id"])
        conn.execute(
            """
            UPDATE macro_snapshots
            SET observed_at = ?, observed_through_event_id = ?,
                observed_through_transaction_id = ?, population = ?,
                status = 'valid', state_fingerprint = ?, metadata_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                now.isoformat(), event_cursor, transaction_cursor, population,
                fingerprint, _json({"source_cursors": source_cursors}), snapshot_id,
            ),
        )
    else:
        cursor = conn.execute(
            """
            INSERT INTO macro_snapshots
            (snapshot_key, window_type, window_start, window_end, observed_at,
             observed_through_event_id, observed_through_transaction_id,
             population, state_fingerprint, rule_version, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_key, window_type, start.isoformat(), end.isoformat(),
                now.isoformat(), event_cursor, transaction_cursor, population,
                fingerprint, RULE_VERSION, _json({"source_cursors": source_cursors}),
            ),
        )
        snapshot_id = int(cursor.lastrowid)
    metric_ids = _metric_ids(conn)
    groups = _resident_groups(conn)
    money = _cash_and_savings(conn, groups, metric_ids, snapshot_id)
    income = _income_metrics(conn, groups, metric_ids, snapshot_id, start, end)
    flows = _ledger_flow_metrics(conn, metric_ids, snapshot_id, start, end)
    stocks = _stock_metrics(conn, metric_ids, snapshot_id)
    market = _market_metrics(conn, metric_ids, snapshot_id, start, end, now)
    social = _social_output_metrics(conn, metric_ids, snapshot_id, start, end)
    reconciliation = reconcile_macro_snapshot(conn, snapshot_id, now)
    return {
        "available": True,
        "snapshot_id": snapshot_id,
        "snapshot_key": snapshot_key,
        "window_type": window_type,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "changed": True,
        "status": reconciliation["status"],
        "metrics": {
            **money, **income, **flows, **stocks, **market, **social,
        },
        "reconciliation": reconciliation,
        "rule_version": RULE_VERSION,
    }


def process_macro_runtime(conn, world_time=None) -> dict:
    return build_macro_snapshot(conn, world_time, "daily")
