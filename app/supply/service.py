from __future__ import annotations

import json
from typing import Optional

import hashlib
from app.json_utils import json_dumps
from datetime import datetime, timedelta, timezone

from app.economy.service import ensure_ledger_account, post_ledger_transaction


ITEM_DEFAULTS = {
    "套餐饭": ("good", 800, 500, 12, 78),
    "早餐券": ("good", 500, 300, 24, 72),
    "奶茶": ("good", 1000, 400, 12, 74),
    "咖啡": ("good", 1200, 500, 12, 80),
    "维修工单": ("service", 0, 0, 0, 75),
    "自习座位": ("service", 0, 0, 0, 78),
    "跑腿券": ("service", 600, 0, 0, 70),
    "训练名额": ("service", 0, 0, 0, 72),
    "食材包": ("input", 2000, 2000, 72, 70),
    "饮品原料": ("input", 1800, 1800, 168, 72),
}

RECIPE_DEFAULTS = [
    ("meal-batch-v1", "resident:5", "套餐饭", 40, 120, "食堂", "service_windows", "食材包", 2),
    ("breakfast-batch-v1", "resident:5", "早餐券", 30, 90, "食堂", "service_windows", "食材包", 1),
    ("milk-tea-batch-v1", "resident:6", "奶茶", 30, 60, "商业街", "service_counters", "饮品原料", 2),
    ("coffee-batch-v1", "resident:6", "咖啡", 20, 75, "商业街", "service_counters", "饮品原料", 2),
]

SERVICE_DEFAULTS = {
    "维修工单": ("resident:10", "校务处", "admin_windows", 8, 0, 45),
    "自习座位": ("resident:8", "图书馆", "study_seats", 80, 0, 60),
    "跑腿券": ("resident:20", "商业街", "service_counters", 10, 600, 30),
    "训练名额": ("resident:9", "操场", "activity_slots", 25, 0, 60),
}


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _json(value) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def supply_runtime_available(conn) -> bool:
    return bool(conn.execute("PRAGMA table_info(inventory_accounts)").fetchall())


def _item_key(name: str) -> str:
    return f"item:{hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]}"


def _item(conn, name: str):
    return conn.execute("SELECT * FROM catalog_items WHERE name = ?", (name,)).fetchone()


def _ensure_inventory_account(
    conn,
    *,
    owner_actor_key: str,
    item_id: int,
    location: str,
    reorder_point: int = 0,
    target_stock: int = 0,
    average_cost_minor: int = 0,
):
    key = f"{owner_actor_key}:item:{item_id}:{location or 'portable'}"
    conn.execute(
        """
        INSERT OR IGNORE INTO inventory_accounts
        (inventory_key, owner_actor_key, item_id, location, reorder_point,
         target_stock, average_cost_minor)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (key, owner_actor_key, item_id, location, reorder_point, target_stock, average_cost_minor),
    )
    return conn.execute(
        """
        SELECT * FROM inventory_accounts
        WHERE inventory_key = ?
        """,
        (key,),
    ).fetchone()


def _sync_legacy_inventory(conn, inventory_account_id: int) -> None:
    row = conn.execute(
        """
        SELECT account.quantity_on_hand, item.name, actor.resident_id
        FROM inventory_accounts account
        JOIN catalog_items item ON item.id = account.item_id
        JOIN economic_actors actor ON actor.actor_key = account.owner_actor_key
        WHERE account.id = ?
        """,
        (inventory_account_id,),
    ).fetchone()
    if row and row["resident_id"] is not None:
        conn.execute(
            """
            INSERT INTO inventory (resident_id, item_name, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(resident_id, item_name)
            DO UPDATE SET quantity = excluded.quantity
            """,
            (row["resident_id"], row["name"], row["quantity_on_hand"]),
        )


def record_inventory_movement(
    conn,
    *,
    movement_key: str,
    inventory_account_id: int,
    movement_type: str,
    quantity_delta: int,
    unit_cost_minor: int,
    source_type: str,
    source_id: str = "",
    ledger_transaction_id: Optional[int] = None,
    production_batch_id: Optional[int] = None,
    occurred_at=None,
    metadata: Optional[dict] = None,
):
    existing = conn.execute(
        "SELECT * FROM inventory_movements WHERE movement_key = ?",
        (movement_key,),
    ).fetchone()
    if existing:
        return {**dict(existing), "created": False}
    account = conn.execute(
        "SELECT * FROM inventory_accounts WHERE id = ?",
        (inventory_account_id,),
    ).fetchone()
    if not account:
        raise ValueError("库存账户不存在")
    after = int(account["quantity_on_hand"]) + int(quantity_delta)
    if after < 0:
        raise ValueError("库存不足")
    average_cost = int(account["average_cost_minor"])
    if int(quantity_delta) > 0 and after:
        average_cost = round(
            (
                int(account["quantity_on_hand"]) * average_cost
                + int(quantity_delta) * int(unit_cost_minor)
            )
            / after
        )
    conn.execute(
        """
        UPDATE inventory_accounts
        SET quantity_on_hand = ?, average_cost_minor = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (after, average_cost, inventory_account_id),
    )
    cursor = conn.execute(
        """
        INSERT INTO inventory_movements
        (movement_key, inventory_account_id, movement_type, quantity_delta,
         unit_cost_minor, source_type, source_id, ledger_transaction_id,
         production_batch_id, occurred_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            movement_key, inventory_account_id, movement_type, int(quantity_delta),
            int(unit_cost_minor), source_type, source_id, ledger_transaction_id,
            production_batch_id, _now(occurred_at).isoformat(), _json(metadata or {}),
        ),
    )
    _sync_legacy_inventory(conn, inventory_account_id)
    return {
        **dict(conn.execute("SELECT * FROM inventory_movements WHERE id = ?", (cursor.lastrowid,)).fetchone()),
        "created": True,
    }


def _ensure_trade_accounts(conn, actor_key: str, seller=False):
    result = {
        "cash": ensure_ledger_account(conn, actor_key=actor_key, account_code="cash", account_type="asset", normal_side="debit"),
        "inventory": ensure_ledger_account(conn, actor_key=actor_key, account_code="inventory_asset", account_type="asset", normal_side="debit"),
    }
    if seller:
        result["revenue"] = ensure_ledger_account(conn, actor_key=actor_key, account_code="sales_revenue", account_type="income", normal_side="credit")
        result["cogs"] = ensure_ledger_account(conn, actor_key=actor_key, account_code="cost_of_goods_sold", account_type="expense", normal_side="debit")
    return result


def settle_goods_trade(
    conn,
    *,
    transaction_key: str,
    buyer_actor_key: str,
    seller_actor_key: str,
    item_name: str,
    quantity: int,
    unit_price_minor: int,
    source_type: str,
    source_id: str = "",
    action_execution_id: Optional[int] = None,
    consume_immediately: bool = False,
):
    item = _item(conn, item_name)
    if not item or item["item_type"] != "good":
        raise ValueError("商品目录中不存在该商品")
    seller_inventory = conn.execute(
        """
        SELECT * FROM inventory_accounts
        WHERE owner_actor_key = ? AND item_id = ? AND status = 'active'
        ORDER BY quantity_on_hand DESC, id LIMIT 1
        """,
        (seller_actor_key, item["id"]),
    ).fetchone()
    if not seller_inventory or int(seller_inventory["quantity_on_hand"]) < int(quantity):
        raise ValueError("卖家库存不足")
    buyer_inventory = _ensure_inventory_account(
        conn, owner_actor_key=buyer_actor_key, item_id=int(item["id"]),
        location="", average_cost_minor=int(unit_price_minor),
    )
    total = int(quantity) * int(unit_price_minor)
    cost = int(quantity) * int(seller_inventory["average_cost_minor"])
    buyer = _ensure_trade_accounts(conn, buyer_actor_key)
    seller = _ensure_trade_accounts(conn, seller_actor_key, seller=True)
    if int(buyer["cash"]["balance_minor"]) < total:
        raise ValueError("买家余额不足")
    entries = [
        {"account_key": seller["cash"]["account_key"], "entry_side": "debit", "amount_minor": total},
        {"account_key": buyer["inventory"]["account_key"], "entry_side": "debit", "amount_minor": total},
        {"account_key": buyer["cash"]["account_key"], "entry_side": "credit", "amount_minor": total},
        {"account_key": seller["revenue"]["account_key"], "entry_side": "credit", "amount_minor": total},
    ]
    if cost:
        entries.extend([
            {"account_key": seller["cogs"]["account_key"], "entry_side": "debit", "amount_minor": cost},
            {"account_key": seller["inventory"]["account_key"], "entry_side": "credit", "amount_minor": cost},
        ])
    ledger = post_ledger_transaction(
        conn, transaction_key=transaction_key, transaction_type="goods_trade",
        source_type=source_type, source_id=source_id,
        action_execution_id=action_execution_id,
        description=f"{buyer_actor_key} 向 {seller_actor_key} 购买 {quantity} 份 {item_name}",
        metadata={"item_name": item_name, "quantity": quantity, "unit_price_minor": unit_price_minor},
        entries=entries,
    )
    record_inventory_movement(
        conn, movement_key=f"{transaction_key}:seller", inventory_account_id=seller_inventory["id"],
        movement_type="sale", quantity_delta=-int(quantity),
        unit_cost_minor=seller_inventory["average_cost_minor"], source_type=source_type,
        source_id=source_id, ledger_transaction_id=ledger["id"],
    )
    record_inventory_movement(
        conn, movement_key=f"{transaction_key}:buyer", inventory_account_id=buyer_inventory["id"],
        movement_type="purchase", quantity_delta=int(quantity), unit_cost_minor=unit_price_minor,
        source_type=source_type, source_id=source_id, ledger_transaction_id=ledger["id"],
    )
    if consume_immediately:
        expense = ensure_ledger_account(
            conn, actor_key=buyer_actor_key, account_code="consumption_expense",
            account_type="expense", normal_side="debit",
        )
        consumption = post_ledger_transaction(
            conn, transaction_key=f"{transaction_key}:consumption",
            transaction_type="goods_consumption", source_type=source_type,
            source_id=source_id, action_execution_id=action_execution_id,
            entries=[
                {"account_key": expense["account_key"], "entry_side": "debit", "amount_minor": total},
                {"account_key": buyer["inventory"]["account_key"], "entry_side": "credit", "amount_minor": total},
            ],
        )
        record_inventory_movement(
            conn, movement_key=f"{transaction_key}:consumed", inventory_account_id=buyer_inventory["id"],
            movement_type="consumption", quantity_delta=-int(quantity), unit_cost_minor=unit_price_minor,
            source_type=source_type, source_id=source_id, ledger_transaction_id=consumption["id"],
        )
    return {
        "ledger_transaction_id": ledger["id"],
        "item_name": item_name,
        "quantity": int(quantity),
        "provider_actor_key": seller_actor_key,
    }


def consumption_availability(conn, location: str):
    if not supply_runtime_available(conn):
        return {"available": True, "managed": False}
    preferred = "套餐饭" if location == "食堂" else "奶茶"
    row = conn.execute(
        """
        SELECT account.owner_actor_key,
               COALESCE(SUM(account.quantity_on_hand - account.quantity_reserved), 0) AS quantity_on_hand
        FROM inventory_accounts account
        JOIN catalog_items item ON item.id = account.item_id
        WHERE item.name = ? AND account.location = ? AND account.status = 'active'
        GROUP BY account.owner_actor_key
        ORDER BY quantity_on_hand DESC, account.owner_actor_key
        LIMIT 1
        """,
        (preferred, location),
    ).fetchone()
    return {
        "available": bool(row and int(row["quantity_on_hand"]) > 0),
        "managed": True,
        "item_name": preferred,
        "quantity_on_hand": int(row["quantity_on_hand"]) if row else 0,
        "provider_actor_key": row["owner_actor_key"] if row else "",
    }


def fulfill_runtime_consumption(
    conn,
    resident_id: int,
    location: str,
    amount_minor: int,
    action_execution_id: Optional[int],
    world_time: Optional[datetime] = None,
):
    availability = consumption_availability(conn, location)
    if not availability["available"]:
        raise ValueError("商品缺货")
    now = _now(world_time)
    source_id = str(action_execution_id) if action_execution_id is not None else ""
    transaction_key = (
        f"action-consume:{action_execution_id}"
        if action_execution_id is not None
        else f"direct-consume:{resident_id}:{location}:{now.isoformat()}"
    )
    return settle_goods_trade(
        conn, transaction_key=transaction_key,
        buyer_actor_key=f"resident:{resident_id}",
        seller_actor_key=availability["provider_actor_key"],
        item_name=availability["item_name"], quantity=1,
        unit_price_minor=int(amount_minor), source_type="world_action_execution",
        source_id=source_id, action_execution_id=action_execution_id,
        consume_immediately=True,
    )


def seed_supply_foundation(conn):
    for name, (item_type, price, cost, shelf, quality) in ITEM_DEFAULTS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO catalog_items
            (item_key, name, item_type, unit, base_price_minor,
             standard_cost_minor, shelf_life_hours, quality)
            VALUES (?, ?, ?, 'unit', ?, ?, ?, ?)
            """,
            (_item_key(name), name, item_type, price, cost, shelf, quality),
        )
    opening_created = 0
    legacy = conn.execute(
        """
        SELECT inventory.resident_id, inventory.item_name, inventory.quantity,
               residents.location
        FROM inventory JOIN residents ON residents.id = inventory.resident_id
        ORDER BY inventory.resident_id, inventory.item_name
        """
    ).fetchall()
    for row in legacy:
        item = _item(conn, row["item_name"])
        if not item:
            continue
        account = _ensure_inventory_account(
            conn, owner_actor_key=f"resident:{row['resident_id']}", item_id=item["id"],
            location=row["location"] if item["item_type"] != "service" else "",
            reorder_point=max(0, int(row["quantity"]) // 4),
            target_stock=int(row["quantity"]), average_cost_minor=item["standard_cost_minor"],
        )
        if int(account["quantity_on_hand"]) == 0 and int(row["quantity"]) > 0:
            ledger_id = _post_inventory_opening(conn, account, int(row["quantity"]), int(item["standard_cost_minor"]))
            record_inventory_movement(
                conn, movement_key=f"opening:{account['inventory_key']}",
                inventory_account_id=account["id"], movement_type="opening",
                quantity_delta=row["quantity"], unit_cost_minor=item["standard_cost_minor"],
                source_type="legacy_inventory_migration", source_id=str(row["resident_id"]),
                ledger_transaction_id=ledger_id,
            )
            opening_created += 1
    for name, owner, quantity, location in (
        ("食材包", "resident:5", 12, "食堂"),
        ("饮品原料", "resident:6", 12, "商业街"),
    ):
        item = _item(conn, name)
        account = _ensure_inventory_account(
            conn, owner_actor_key=owner, item_id=item["id"], location=location,
            reorder_point=2, target_stock=12, average_cost_minor=item["standard_cost_minor"],
        )
        if int(account["quantity_on_hand"]) == 0:
            ledger_id = _post_inventory_opening(conn, account, quantity, item["standard_cost_minor"])
            record_inventory_movement(
                conn, movement_key=f"opening:{account['inventory_key']}",
                inventory_account_id=account["id"], movement_type="opening",
                quantity_delta=quantity, unit_cost_minor=item["standard_cost_minor"],
                source_type="supply_seed", ledger_transaction_id=ledger_id,
            )
    _seed_recipes_and_services(conn)
    counts = {}
    for table in ("catalog_items", "inventory_accounts", "production_recipes", "service_offerings"):
        counts[table] = int(conn.execute(f"SELECT COUNT(*) value FROM {table}").fetchone()["value"])
    return {**counts, "opening_movements_created": opening_created}


def _post_inventory_opening(conn, account, quantity, unit_cost):
    value = int(quantity) * int(unit_cost)
    if not value:
        return None
    asset = ensure_ledger_account(conn, actor_key=account["owner_actor_key"], account_code="inventory_asset", account_type="asset", normal_side="debit")
    equity = ensure_ledger_account(conn, actor_key=account["owner_actor_key"], account_code="opening_equity", account_type="equity", normal_side="credit")
    result = post_ledger_transaction(
        conn, transaction_key=f"supply-opening:{account['inventory_key']}",
        transaction_type="inventory_opening", source_type="supply_seed",
        source_id=account["inventory_key"],
        entries=[
            {"account_key": asset["account_key"], "entry_side": "debit", "amount_minor": value},
            {"account_key": equity["account_key"], "entry_side": "credit", "amount_minor": value},
        ],
    )
    return result["id"]


def _seed_recipes_and_services(conn):
    for recipe_key, owner, output_name, output_qty, duration, location, resource, input_name, input_qty in RECIPE_DEFAULTS:
        output, input_item = _item(conn, output_name), _item(conn, input_name)
        conn.execute(
            """
            INSERT OR IGNORE INTO production_recipes
            (recipe_key, producer_actor_key, output_item_id, output_quantity,
             duration_minutes, location, spatial_resource_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (recipe_key, owner, output["id"], output_qty, duration, location, resource),
        )
        recipe = conn.execute("SELECT id FROM production_recipes WHERE recipe_key = ?", (recipe_key,)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO production_recipe_inputs (recipe_id, item_id, quantity) VALUES (?, ?, ?)",
            (recipe["id"], input_item["id"], input_qty),
        )
    for service_name, (provider, location, resource, capacity, price, duration) in SERVICE_DEFAULTS.items():
        item = _item(conn, service_name)
        conn.execute(
            """
            INSERT OR IGNORE INTO service_offerings
            (offering_key, provider_actor_key, service_item_id, location,
             spatial_resource_key, capacity_per_hour, price_minor, duration_minutes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"offering:{item['item_key']}", provider, item["id"], location, resource, capacity, price, duration),
        )


def deliver_service(
    conn,
    *,
    delivery_key: str,
    offering_key: str,
    consumer_actor_key: str,
    consumer_resident_id: Optional[int] = None,
    world_action_execution_id: Optional[int] = None,
    requested_at=None,
):
    existing = conn.execute(
        "SELECT * FROM service_deliveries WHERE delivery_key = ?",
        (delivery_key,),
    ).fetchone()
    if existing:
        return dict(existing)
    now = _now(requested_at)
    offering = conn.execute(
        """
        SELECT offering.*, item.name AS service_name
        FROM service_offerings offering
        JOIN catalog_items item ON item.id = offering.service_item_id
        WHERE offering.offering_key = ? AND offering.status = 'active'
        """,
        (offering_key,),
    ).fetchone()
    if not offering:
        raise ValueError("服务项目不存在或不可用")
    hour_start_at = now.replace(minute=0, second=0, microsecond=0)
    hour_start = hour_start_at.isoformat()
    hour_end = (hour_start_at + timedelta(hours=1)).isoformat()
    delivered = conn.execute(
        """
        SELECT COUNT(*) value FROM service_deliveries
        WHERE offering_id = ? AND status = 'delivered'
          AND requested_at >= ? AND requested_at < ?
        """,
        (offering["id"], hour_start, hour_end),
    ).fetchone()["value"]
    queue_reason = ""
    if int(delivered) >= int(offering["capacity_per_hour"]):
        queue_reason = "capacity_full"
    resource = None
    if conn.execute("PRAGMA table_info(spatial_resources)").fetchall():
        resource = conn.execute(
            """
            SELECT available_units, status FROM spatial_resources
            WHERE resource_key = ? LIMIT 1
            """,
            (offering["spatial_resource_key"],),
        ).fetchone()
    if resource and (
        resource["status"] != "available" or int(resource["available_units"]) <= 0
    ):
        queue_reason = "spatial_resource_unavailable"
    price = int(offering["price_minor"])
    if queue_reason:
        cursor = conn.execute(
            """
            INSERT INTO service_deliveries
            (delivery_key, offering_id, consumer_actor_key, consumer_resident_id,
             status, quantity, price_minor, requested_at,
             world_action_execution_id, result_json)
            VALUES (?, ?, ?, ?, 'queued', 1, ?, ?, ?, ?)
            """,
            (
                delivery_key, offering["id"], consumer_actor_key,
                consumer_resident_id, price, now.isoformat(),
                world_action_execution_id,
                _json({
                    "queue_reason": queue_reason,
                    "service_name": offering["service_name"],
                }),
            ),
        )
        return dict(
            conn.execute(
                "SELECT * FROM service_deliveries WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        )
    ledger_id = None
    if price:
        consumer_cash = ensure_ledger_account(conn, actor_key=consumer_actor_key, account_code="cash", account_type="asset", normal_side="debit")
        provider_cash = ensure_ledger_account(conn, actor_key=offering["provider_actor_key"], account_code="cash", account_type="asset", normal_side="debit")
        expense = ensure_ledger_account(conn, actor_key=consumer_actor_key, account_code="service_expense", account_type="expense", normal_side="debit")
        revenue = ensure_ledger_account(conn, actor_key=offering["provider_actor_key"], account_code="service_revenue", account_type="income", normal_side="credit")
        if int(consumer_cash["balance_minor"]) < price:
            raise ValueError("服务消费者余额不足")
        ledger = post_ledger_transaction(
            conn, transaction_key=f"service-delivery:{delivery_key}",
            transaction_type="service_delivery", source_type="service_delivery",
            source_id=delivery_key, action_execution_id=world_action_execution_id,
            entries=[
                {"account_key": provider_cash["account_key"], "entry_side": "debit", "amount_minor": price},
                {"account_key": expense["account_key"], "entry_side": "debit", "amount_minor": price},
                {"account_key": consumer_cash["account_key"], "entry_side": "credit", "amount_minor": price},
                {"account_key": revenue["account_key"], "entry_side": "credit", "amount_minor": price},
            ],
        )
        ledger_id = ledger["id"]
    cursor = conn.execute(
        """
        INSERT INTO service_deliveries
        (delivery_key, offering_id, consumer_actor_key, consumer_resident_id,
         status, quantity, price_minor, requested_at, delivered_at,
         world_action_execution_id, ledger_transaction_id, result_json)
        VALUES (?, ?, ?, ?, 'delivered', 1, ?, ?, ?, ?, ?, ?)
        """,
        (
            delivery_key, offering["id"], consumer_actor_key, consumer_resident_id,
            price, now.isoformat(), now.isoformat(), world_action_execution_id,
            ledger_id, _json({"quality": offering["quality"], "service_name": offering["service_name"]}),
        ),
    )
    return dict(conn.execute("SELECT * FROM service_deliveries WHERE id = ?", (cursor.lastrowid,)).fetchone())


def _start_production_batch(conn, recipe, now):
    running = conn.execute(
        "SELECT id FROM production_batches WHERE recipe_id = ? AND status = 'running'",
        (recipe["id"],),
    ).fetchone()
    if running:
        return None
    output = conn.execute(
        """
        SELECT * FROM inventory_accounts
        WHERE owner_actor_key = ? AND item_id = ? AND location = ?
        ORDER BY quantity_on_hand DESC, id
        LIMIT 1
        """,
        (recipe["producer_actor_key"], recipe["output_item_id"], recipe["location"]),
    ).fetchone()
    if not output or int(output["quantity_on_hand"]) > int(output["reorder_point"]):
        return None
    inputs = conn.execute(
        """
        SELECT input.quantity, account.*, item.name
        FROM production_recipe_inputs input
        JOIN inventory_accounts account
          ON account.item_id = input.item_id
         AND account.owner_actor_key = ?
         AND account.location = ?
        JOIN catalog_items item ON item.id = input.item_id
        WHERE input.recipe_id = ?
        """,
        (recipe["producer_actor_key"], recipe["location"], recipe["id"]),
    ).fetchall()
    if not inputs or any(int(row["quantity_on_hand"]) < int(row["quantity"]) for row in inputs):
        return {"recipe_id": int(recipe["id"]), "status": "blocked", "reason": "input_shortage"}
    batch_key = f"batch:{recipe['recipe_key']}:{now.isoformat()}"
    due = now + timedelta(minutes=int(recipe["duration_minutes"]))
    total_cost = sum(int(row["quantity"]) * int(row["average_cost_minor"]) for row in inputs)
    cursor = conn.execute(
        """
        INSERT INTO production_batches
        (batch_key, recipe_id, status, output_quantity, started_at, due_at, metadata_json)
        VALUES (?, ?, 'running', ?, ?, ?, ?)
        """,
        (batch_key, recipe["id"], recipe["output_quantity"], now.isoformat(), due.isoformat(), _json({"input_cost_minor": total_cost})),
    )
    batch_id = int(cursor.lastrowid)
    asset = ensure_ledger_account(conn, actor_key=recipe["producer_actor_key"], account_code="inventory_asset", account_type="asset", normal_side="debit")
    wip = ensure_ledger_account(conn, actor_key=recipe["producer_actor_key"], account_code="work_in_process", account_type="asset", normal_side="debit")
    ledger_id = None
    if total_cost:
        ledger = post_ledger_transaction(
            conn, transaction_key=f"{batch_key}:inputs", transaction_type="production_start",
            source_type="production_batch", source_id=str(batch_id),
            entries=[
                {"account_key": wip["account_key"], "entry_side": "debit", "amount_minor": total_cost},
                {"account_key": asset["account_key"], "entry_side": "credit", "amount_minor": total_cost},
            ],
        )
        ledger_id = ledger["id"]
    for row in inputs:
        record_inventory_movement(
            conn, movement_key=f"{batch_key}:input:{row['item_id']}",
            inventory_account_id=row["id"], movement_type="production_input",
            quantity_delta=-int(row["quantity"]), unit_cost_minor=row["average_cost_minor"],
            source_type="production_batch", source_id=str(batch_id),
            ledger_transaction_id=ledger_id, production_batch_id=batch_id,
        )
    conn.execute("UPDATE production_batches SET ledger_transaction_id = ? WHERE id = ?", (ledger_id, batch_id))
    return {"batch_id": batch_id, "status": "running", "due_at": due.isoformat()}


def _complete_production_batch(conn, batch, now):
    recipe = conn.execute("SELECT * FROM production_recipes WHERE id = ?", (batch["recipe_id"],)).fetchone()
    output = _ensure_inventory_account(
        conn, owner_actor_key=recipe["producer_actor_key"], item_id=recipe["output_item_id"],
        location=recipe["location"], reorder_point=5, target_stock=recipe["output_quantity"],
    )
    metadata = json.loads(batch["metadata_json"] or "{}")
    total_cost = int(metadata.get("input_cost_minor", 0))
    unit_cost = round(total_cost / int(batch["output_quantity"])) if total_cost else 0
    asset = ensure_ledger_account(conn, actor_key=recipe["producer_actor_key"], account_code="inventory_asset", account_type="asset", normal_side="debit")
    wip = ensure_ledger_account(conn, actor_key=recipe["producer_actor_key"], account_code="work_in_process", account_type="asset", normal_side="debit")
    ledger_id = None
    if total_cost:
        ledger = post_ledger_transaction(
            conn, transaction_key=f"{batch['batch_key']}:output", transaction_type="production_complete",
            source_type="production_batch", source_id=str(batch["id"]),
            entries=[
                {"account_key": asset["account_key"], "entry_side": "debit", "amount_minor": total_cost},
                {"account_key": wip["account_key"], "entry_side": "credit", "amount_minor": total_cost},
            ],
        )
        ledger_id = ledger["id"]
    record_inventory_movement(
        conn, movement_key=f"{batch['batch_key']}:output", inventory_account_id=output["id"],
        movement_type="production_output", quantity_delta=batch["output_quantity"],
        unit_cost_minor=unit_cost, source_type="production_batch", source_id=str(batch["id"]),
        ledger_transaction_id=ledger_id, production_batch_id=batch["id"], occurred_at=now,
    )
    conn.execute(
        "UPDATE production_batches SET status = 'completed', completed_at = ? WHERE id = ?",
        (now.isoformat(), batch["id"]),
    )
    return int(batch["id"])


def _process_daily_waste(conn, now):
    if now.hour < 22:
        return []
    wasted = []
    accounts = conn.execute(
        """
        SELECT account.*, item.name, item.shelf_life_hours
        FROM inventory_accounts account
        JOIN catalog_items item ON item.id = account.item_id
        WHERE account.status = 'active'
          AND account.quantity_on_hand > 0
          AND item.item_type = 'good'
          AND item.shelf_life_hours > 0
        ORDER BY account.id
        """
    ).fetchall()
    for account in accounts:
        movement_key = f"waste:{now.date().isoformat()}:{account['id']}"
        if conn.execute(
            "SELECT id FROM inventory_movements WHERE movement_key = ?",
            (movement_key,),
        ).fetchone():
            continue
        quantity = max(1, int(account["quantity_on_hand"]) // 50)
        value = quantity * int(account["average_cost_minor"])
        ledger_id = None
        if value:
            inventory_asset = ensure_ledger_account(
                conn, actor_key=account["owner_actor_key"],
                account_code="inventory_asset", account_type="asset",
                normal_side="debit",
            )
            loss = ensure_ledger_account(
                conn, actor_key=account["owner_actor_key"],
                account_code="inventory_loss", account_type="expense",
                normal_side="debit",
            )
            ledger = post_ledger_transaction(
                conn,
                transaction_key=f"supply-{movement_key}",
                transaction_type="inventory_waste",
                source_type="supply_runtime",
                source_id=movement_key,
                entries=[
                    {
                        "account_key": loss["account_key"],
                        "entry_side": "debit",
                        "amount_minor": value,
                    },
                    {
                        "account_key": inventory_asset["account_key"],
                        "entry_side": "credit",
                        "amount_minor": value,
                    },
                ],
            )
            ledger_id = ledger["id"]
        movement = record_inventory_movement(
            conn,
            movement_key=movement_key,
            inventory_account_id=account["id"],
            movement_type="waste",
            quantity_delta=-quantity,
            unit_cost_minor=account["average_cost_minor"],
            source_type="supply_runtime",
            source_id=now.date().isoformat(),
            ledger_transaction_id=ledger_id,
            occurred_at=now,
            metadata={
                "item_name": account["name"],
                "shelf_life_hours": account["shelf_life_hours"],
            },
        )
        wasted.append(
            {
                "inventory_account_id": int(account["id"]),
                "quantity": quantity,
                "movement_id": int(movement["id"]),
            }
        )
    return wasted


def process_supply_runtime(conn, world_time=None):
    if not supply_runtime_available(conn):
        return {
            "available": False,
            "completed": [],
            "started": [],
            "blocked": [],
            "wasted": [],
        }
    now = _now(world_time)
    completed = [
        _complete_production_batch(conn, row, now)
        for row in conn.execute(
            "SELECT * FROM production_batches WHERE status = 'running' AND due_at <= ? ORDER BY id",
            (now.isoformat(),),
        ).fetchall()
    ]
    started, blocked = [], []
    for recipe in conn.execute(
        "SELECT * FROM production_recipes WHERE status = 'active' ORDER BY id"
    ).fetchall():
        result = _start_production_batch(conn, recipe, now)
        if result and result["status"] == "running":
            started.append(result)
        elif result:
            blocked.append(result)
    return {
        "available": True,
        "completed": completed,
        "started": started,
        "blocked": blocked,
        "wasted": _process_daily_waste(conn, now),
    }
