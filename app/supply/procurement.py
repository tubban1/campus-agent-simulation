"""Pluggable supplier / procurement layer.

Closes the production input loop in a forward-compatible way: each input item
is provisioned by a configured supplier actor (``procurement_suppliers``).  An
``in_world`` supplier sells the item to the producer through the ordinary
ledger trade primitive and regenerates its own stock from an external upstream
via a cross-boundary goods inflow.  An ``external`` supplier imports the item
directly into the producer.

Because provisioning is data-driven, swapping an in-world supplier for a real
external economy later is a configuration change rather than a core rewrite.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from app.economy.service import (
    _insert_account,
    _insert_actor,
    _seed_actor_accounts,
    ensure_ledger_account,
    post_ledger_transaction,
)
from app.supply.service import (
    _ensure_inventory_account,
    _ensure_trade_accounts,
    _item,
    record_inventory_movement,
)
from app.world_runtime.clock import WORLD_TZ, parse_world_datetime


PROCUREMENT_FOUNDATION_SQL = """
CREATE TABLE IF NOT EXISTS procurement_suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_key TEXT NOT NULL UNIQUE,
    supplier_actor_key TEXT NOT NULL,
    upstream_actor_key TEXT,
    item_id INTEGER NOT NULL,
    supply_kind TEXT NOT NULL DEFAULT 'in_world',
    unit_cost_minor INTEGER NOT NULL DEFAULT 0,
    replenish_threshold INTEGER NOT NULL DEFAULT 0,
    replenish_qty INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS procurement_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_key TEXT NOT NULL UNIQUE,
    supplier_key TEXT NOT NULL,
    buyer_actor_key TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_cost_minor INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    reason TEXT NOT NULL DEFAULT '',
    ledger_transaction_id INTEGER,
    requested_at TEXT,
    fulfilled_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
"""


DEFAULT_LOGISTICS_ACTOR = "supplier:campus-logistics"
DEFAULT_LOGISTICS_NAME = "校园后勤供应中心"
DEFAULT_UPSTREAM_ACTOR = "external:campus-wholesale"
DEFAULT_UPSTREAM_NAME = "外部批发经济体"

# (catalog item name, supplier unit cost minor, replenish threshold, qty)
DEFAULT_SUPPLIERS = [
    ("食材包", "supplier:campus-logistics", 2000, 2, 8),
    ("饮品原料", "supplier:campus-logistics", 1800, 2, 8),
    ("套餐饭", "supplier:campus-logistics", 500, 4, 16),
]


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def procurement_available(conn) -> bool:
    if conn is None:
        return False
    return bool(conn.execute("PRAGMA table_info(procurement_suppliers)").fetchall())


def external_goods_inflow(
    conn,
    *,
    buyer_actor_key: str,
    item_name: str,
    quantity: int,
    unit_cost_minor: int,
    source_actor_key: str,
    transaction_key: str,
    source_id: str,
    occurred_at=None,
    buyer_location: str = "",
) -> dict:
    """Bring goods across the world boundary from an external economy.

    Mirrors ``external_inflow`` for money: the buyer's inventory asset is
    debited and the external upstream's import equity is credited, keeping the
    ledger balanced while recording the cross-boundary provenance.
    """
    item = _item(conn, item_name)
    if not item:
        raise ValueError(f"商品目录中不存在：{item_name}")
    buyer = _ensure_inventory_account(
        conn,
        owner_actor_key=buyer_actor_key,
        item_id=int(item["id"]),
        location=buyer_location,
        average_cost_minor=int(unit_cost_minor),
    )
    source = _insert_actor(
        conn,
        source_actor_key,
        "external",
        f"外部供应：{item_name}",
        metadata={"purpose": "external_goods_inflow"},
    )
    imports_equity = _insert_account(
        conn,
        int(source["id"]),
        f"{source_actor_key}:imports-equity",
        "imports_equity",
        "equity",
        "credit",
    )
    inventory_asset = ensure_ledger_account(
        conn,
        actor_key=buyer_actor_key,
        account_code="inventory_asset",
        account_type="asset",
        normal_side="debit",
    )
    total = int(quantity) * int(unit_cost_minor)
    existing = conn.execute(
        "SELECT id FROM ledger_transactions WHERE transaction_key = ?",
        (transaction_key,),
    ).fetchone()
    if existing:
        ledger = {"id": int(existing["id"])}
    else:
        ledger = post_ledger_transaction(
            conn,
            transaction_key=transaction_key,
            transaction_type="external_goods_inflow",
            source_type="procurement",
            source_id=source_id,
            description=f"外部经济体 {source_actor_key} 向 {buyer_actor_key} 供应 {quantity} 份 {item_name}",
            metadata={
                "item_name": item_name,
                "quantity": int(quantity),
                "unit_cost_minor": int(unit_cost_minor),
                "supplier_actor_key": source_actor_key,
            },
            entries=[
                {
                    "account_key": inventory_asset["account_key"],
                    "entry_side": "debit",
                    "amount_minor": total,
                },
                {
                    "account_key": imports_equity["account_key"],
                    "entry_side": "credit",
                    "amount_minor": total,
                },
            ],
        )
    movement = record_inventory_movement(
        conn,
        movement_key=f"{transaction_key}:import",
        inventory_account_id=int(buyer["id"]),
        movement_type="purchase",
        quantity_delta=int(quantity),
        unit_cost_minor=int(unit_cost_minor),
        source_type="procurement",
        source_id=source_id,
        ledger_transaction_id=int(ledger["id"]),
        occurred_at=occurred_at,
        metadata={"supplier_actor_key": source_actor_key, "cross_boundary": True},
    )
    return {
        "ledger_transaction_id": int(ledger["id"]),
        "quantity": int(quantity),
        "movement_id": int(movement["id"]),
    }


def _supplier_by_item(conn) -> dict:
    rows = conn.execute(
        """
        SELECT * FROM procurement_suppliers
        WHERE status = 'active'
        ORDER BY id
        """
    ).fetchall()
    by_item: dict = {}
    for row in rows:
        item_id = int(row["item_id"])
        by_item.setdefault(item_id, row)
    return by_item


def _transfer_goods(
    conn,
    *,
    buyer_actor_key: str,
    seller_actor_key: str,
    item_name: str,
    quantity: int,
    unit_cost_minor: int,
    transaction_key: str,
    source_id: str,
    occurred_at=None,
    buyer_location: str = "",
) -> dict:
    """Move any catalog item (including production ``input`` goods) between
    an in-world supplier and a producer through the ordinary ledger."""
    item = _item(conn, item_name)
    if not item:
        raise ValueError(f"商品目录中不存在该商品：{item_name}")
    seller_inventory = conn.execute(
        """
        SELECT * FROM inventory_accounts
        WHERE owner_actor_key = ? AND item_id = ? AND status = 'active'
        ORDER BY quantity_on_hand DESC, id LIMIT 1
        """,
        (seller_actor_key, int(item["id"])),
    ).fetchone()
    if not seller_inventory or int(seller_inventory["quantity_on_hand"]) < int(quantity):
        raise ValueError("卖家库存不足")
    buyer_inventory = _ensure_inventory_account(
        conn,
        owner_actor_key=buyer_actor_key,
        item_id=int(item["id"]),
        location=buyer_location,
        average_cost_minor=int(unit_cost_minor),
    )
    total = int(quantity) * int(unit_cost_minor)
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
        conn,
        transaction_key=transaction_key,
        transaction_type="procurement_trade",
        source_type="procurement",
        source_id=source_id,
        description=f"供应商 {seller_actor_key} 向 {buyer_actor_key} 供应 {quantity} 份 {item_name}",
        metadata={"item_name": item_name, "quantity": int(quantity), "unit_cost_minor": int(unit_cost_minor)},
        entries=entries,
    )
    record_inventory_movement(
        conn,
        movement_key=f"{transaction_key}:seller",
        inventory_account_id=int(seller_inventory["id"]),
        movement_type="sale",
        quantity_delta=-int(quantity),
        unit_cost_minor=int(seller_inventory["average_cost_minor"]),
        source_type="procurement",
        source_id=source_id,
        ledger_transaction_id=int(ledger["id"]),
        occurred_at=occurred_at,
    )
    record_inventory_movement(
        conn,
        movement_key=f"{transaction_key}:buyer",
        inventory_account_id=int(buyer_inventory["id"]),
        movement_type="purchase",
        quantity_delta=int(quantity),
        unit_cost_minor=int(unit_cost_minor),
        source_type="procurement",
        source_id=source_id,
        ledger_transaction_id=int(ledger["id"]),
        occurred_at=occurred_at,
    )
    return {"ledger_transaction_id": int(ledger["id"]), "quantity": int(quantity)}


def seed_default_suppliers(conn) -> dict:
    """Create the in-world logistics supplier and its external upstream."""
    if not procurement_available(conn):
        return {"suppliers": 0}
    _seed_actor_accounts(
        conn,
        actor_key=DEFAULT_LOGISTICS_ACTOR,
        actor_type="production_service",
        display_name=DEFAULT_LOGISTICS_NAME,
        opening_coins=200,
    )
    _insert_actor(
        conn,
        DEFAULT_UPSTREAM_ACTOR,
        "external",
        DEFAULT_UPSTREAM_NAME,
        metadata={"purpose": "external_goods_upstream"},
    )
    created = 0
    for name, supplier_actor, unit_cost, threshold, qty in DEFAULT_SUPPLIERS:
        item = _item(conn, name)
        if not item:
            continue
        supplier_key = f"supplier:{name}:v1"
        conn.execute(
            """
            INSERT OR IGNORE INTO procurement_suppliers
            (supplier_key, supplier_actor_key, upstream_actor_key, item_id,
             supply_kind, unit_cost_minor, replenish_threshold, replenish_qty,
             status, metadata_json)
            VALUES (?, ?, ?, ?, 'in_world', ?, ?, ?, 'active', '{}')
            """,
            (
                supplier_key,
                supplier_actor,
                DEFAULT_UPSTREAM_ACTOR,
                int(item["id"]),
                int(unit_cost),
                int(threshold),
                int(qty),
            ),
        )
        _ensure_inventory_account(
            conn,
            owner_actor_key=supplier_actor,
            item_id=int(item["id"]),
            location="",
            reorder_point=int(threshold),
            target_stock=int(qty),
            average_cost_minor=int(unit_cost),
        )
        created += 1
    return {"suppliers": created, "logistics_actor": DEFAULT_LOGISTICS_ACTOR}


def _supplier_upstream_seq(conn, supplier_key: str) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*) value FROM inventory_movements m
            JOIN inventory_accounts a ON a.id = m.inventory_account_id
            JOIN procurement_suppliers s ON s.supplier_actor_key = a.owner_actor_key
            WHERE s.supplier_key = ? AND m.movement_type = 'purchase'
              AND m.source_type = 'procurement'
            """,
            (supplier_key,),
        ).fetchone()["value"]
    )


def _restock_supplier(conn, supplier, now, quantity=None) -> int:
    """Regenerate an in-world supplier's stock from its external upstream.

    ``quantity`` supplies exactly the requested amount (unique import per
    call); otherwise the supplier is topped up to its replenish threshold.
    """
    item = conn.execute(
        "SELECT * FROM catalog_items WHERE id = ?", (int(supplier["item_id"]),)
    ).fetchone()
    if not item:
        return 0
    account = _ensure_inventory_account(
        conn,
        owner_actor_key=supplier["supplier_actor_key"],
        item_id=int(item["id"]),
        location="",
        reorder_point=int(supplier["replenish_threshold"]),
        target_stock=int(supplier["replenish_qty"]),
        average_cost_minor=int(supplier["unit_cost_minor"]),
    )
    on_hand = int(account["quantity_on_hand"])
    if quantity is None:
        shortfall = max(0, int(supplier["replenish_threshold"]) - on_hand)
        if shortfall <= 0:
            return 0
        qty = min(shortfall, int(supplier["replenish_qty"]) or shortfall)
    else:
        qty = max(0, int(quantity))
    if qty <= 0:
        return 0
    upstream = supplier["upstream_actor_key"] or DEFAULT_UPSTREAM_ACTOR
    seq = _supplier_upstream_seq(conn, supplier["supplier_key"]) + 1
    transaction_key = f"upstream:{supplier['supplier_key']}:{now.isoformat()}:{seq}"
    external_goods_inflow(
        conn,
        buyer_actor_key=supplier["supplier_actor_key"],
        item_name=item["name"],
        quantity=qty,
        unit_cost_minor=int(supplier["unit_cost_minor"]),
        source_actor_key=upstream,
        transaction_key=transaction_key,
        source_id=transaction_key,
        occurred_at=now,
    )
    return qty


def _record_order(
    conn,
    *,
    order_key,
    supplier_key,
    buyer_actor_key,
    item_id,
    quantity,
    unit_cost_minor,
    status,
    reason,
    ledger_transaction_id,
    now,
):
    conn.execute(
        """
        INSERT OR IGNORE INTO procurement_orders
        (order_key, supplier_key, buyer_actor_key, item_id, quantity,
         unit_cost_minor, status, reason, ledger_transaction_id,
         requested_at, fulfilled_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
        """,
        (
            order_key,
            supplier_key,
            buyer_actor_key,
            int(item_id),
            int(quantity),
            int(unit_cost_minor),
            status,
            reason,
            ledger_transaction_id,
            now.isoformat() if now else None,
            now.isoformat() if status == "fulfilled" and now else None,
        ),
    )


def procure_item(
    conn,
    *,
    item_name: str,
    quantity: int,
    buyer_actor_key: str,
    location: str = "",
    now=None,
) -> dict:
    """Buy ``quantity`` of ``item_name`` into a buyer's supply account from the
    item's configured supplier.  Used by facility restock work orders so the
    spatial shelf is refilled from an auditable procurement, not from nothing.
    Returns ``fulfilled`` False (with ``reason``) when no supplier, no funds,
    or insufficient supplier stock after restocking.
    """
    now = _now(now)
    item = _item(conn, item_name)
    if not item:
        return {"fulfilled": False, "quantity": 0, "reason": "no_catalog_item"}
    if not procurement_available(conn):
        return {"fulfilled": False, "quantity": 0, "reason": "procurement_unavailable"}
    suppliers = _supplier_by_item(conn)
    supplier = suppliers.get(int(item["id"]))
    if not supplier:
        return {"fulfilled": False, "quantity": 0, "reason": "no_supplier"}
    qty = max(0, int(quantity))
    if qty <= 0:
        return {"fulfilled": False, "quantity": 0, "reason": "zero_quantity"}
    unit_cost = int(supplier["unit_cost_minor"])
    order_key = (
        f"proc-item:{item_name}:{buyer_actor_key}:"
        f"{now.isoformat()}:{hashlib.sha1(item_name.encode('utf-8')).hexdigest()[:6]}"
    )
    if conn.execute(
        "SELECT id FROM procurement_orders WHERE order_key = ?", (order_key,)
    ).fetchone():
        return {"fulfilled": False, "quantity": 0, "reason": "duplicate"}
    try:
        if supplier["supply_kind"] == "external":
            upstream = supplier["upstream_actor_key"] or DEFAULT_UPSTREAM_ACTOR
            result = external_goods_inflow(
                conn,
                buyer_actor_key=buyer_actor_key,
                item_name=item_name,
                quantity=qty,
                unit_cost_minor=unit_cost,
                source_actor_key=upstream,
                transaction_key=order_key,
                source_id=order_key,
                occurred_at=now,
                buyer_location=location,
            )
            ledger_id = int(result["ledger_transaction_id"])
        else:
            _restock_supplier(conn, supplier, now, quantity=qty)
            result = _transfer_goods(
                conn,
                transaction_key=order_key,
                buyer_actor_key=buyer_actor_key,
                seller_actor_key=supplier["supplier_actor_key"],
                item_name=item_name,
                quantity=qty,
                unit_cost_minor=unit_cost,
                source_id=order_key,
                occurred_at=now,
                buyer_location=location,
            )
            ledger_id = int(result["ledger_transaction_id"])
        _record_order(
            conn,
            order_key=order_key,
            supplier_key=supplier["supplier_key"],
            buyer_actor_key=buyer_actor_key,
            item_id=int(item["id"]),
            quantity=qty,
            unit_cost_minor=unit_cost,
            status="fulfilled",
            reason="",
            ledger_transaction_id=ledger_id,
            now=now,
        )
        return {"fulfilled": True, "quantity": qty, "reason": ""}
    except ValueError as exc:
        _record_order(
            conn,
            order_key=order_key,
            supplier_key=supplier["supplier_key"],
            buyer_actor_key=buyer_actor_key,
            item_id=int(item["id"]),
            quantity=qty,
            unit_cost_minor=unit_cost,
            status="blocked",
            reason=str(exc),
            ledger_transaction_id=None,
            now=now,
        )
        return {"fulfilled": False, "quantity": 0, "reason": str(exc)}


def procure_inputs(conn, now=None, *, day: int = 0) -> dict:
    """Replenish below-threshold production inputs from configured suppliers."""
    now = _now(now)
    if not procurement_available(conn):
        return {"available": False, "orders": [], "restocked": 0, "blocked": []}
    suppliers = _supplier_by_item(conn)
    recipes = conn.execute(
        "SELECT * FROM production_recipes WHERE status = 'active' ORDER BY id"
    ).fetchall()
    orders, blocked = [], []
    restocked = 0
    for recipe in recipes:
        recipe_location = str(recipe["location"] or "")
        producer = str(recipe["producer_actor_key"])
        inputs = conn.execute(
            "SELECT * FROM production_recipe_inputs WHERE recipe_id = ?",
            (int(recipe["id"]),),
        ).fetchall()
        for input_row in inputs:
            item_id = int(input_row["item_id"])
            supplier = suppliers.get(item_id)
            item = conn.execute(
                "SELECT * FROM catalog_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not item:
                continue
            threshold = max(int(input_row["quantity"]) * 2, 2)
            producer_account = _ensure_inventory_account(
                conn,
                owner_actor_key=producer,
                item_id=item_id,
                location=recipe_location,
                reorder_point=threshold,
                target_stock=threshold,
            )
            if int(producer_account["quantity_on_hand"]) >= int(producer_account["reorder_point"]):
                continue
            if not supplier:
                blocked.append(
                    {"item": item["name"], "buyer": producer, "reason": "no_supplier"}
                )
                continue
            qty = int(supplier["replenish_qty"]) or threshold
            unit_cost = int(supplier["unit_cost_minor"])
            order_key = (
                f"proc:{recipe['recipe_key']}:{item['name']}:"
                f"{now.isoformat()}:{hashlib.sha1(item['name'].encode('utf-8')).hexdigest()[:6]}"
            )
            if conn.execute(
                "SELECT id FROM procurement_orders WHERE order_key = ?", (order_key,)
            ).fetchone():
                continue
            try:
                if supplier["supply_kind"] == "in_world":
                    _restock_supplier(conn, supplier, now, quantity=qty)
                if supplier["supply_kind"] == "external":
                    upstream = supplier["upstream_actor_key"] or DEFAULT_UPSTREAM_ACTOR
                    result = external_goods_inflow(
                        conn,
                        buyer_actor_key=producer,
                        item_name=item["name"],
                        quantity=qty,
                        unit_cost_minor=unit_cost,
                        source_actor_key=upstream,
                        transaction_key=order_key,
                        source_id=order_key,
                        occurred_at=now,
                        buyer_location=recipe_location,
                    )
                    ledger_id = int(result["ledger_transaction_id"])
                else:
                    result = _transfer_goods(
                        conn,
                        transaction_key=order_key,
                        buyer_actor_key=producer,
                        seller_actor_key=supplier["supplier_actor_key"],
                        item_name=item["name"],
                        quantity=qty,
                        unit_cost_minor=unit_cost,
                        source_id=order_key,
                        occurred_at=now,
                        buyer_location=recipe_location,
                    )
                    ledger_id = int(result["ledger_transaction_id"])
                restocked += qty
                _record_order(
                    conn,
                    order_key=order_key,
                    supplier_key=supplier["supplier_key"],
                    buyer_actor_key=producer,
                    item_id=item_id,
                    quantity=qty,
                    unit_cost_minor=unit_cost,
                    status="fulfilled",
                    reason="",
                    ledger_transaction_id=ledger_id,
                    now=now,
                )
                orders.append(
                    {
                        "order_key": order_key,
                        "item": item["name"],
                        "buyer": producer,
                        "quantity": qty,
                        "status": "fulfilled",
                    }
                )
            except ValueError as exc:
                _record_order(
                    conn,
                    order_key=order_key,
                    supplier_key=supplier["supplier_key"],
                    buyer_actor_key=producer,
                    item_id=item_id,
                    quantity=qty,
                    unit_cost_minor=unit_cost,
                    status="blocked",
                    reason=str(exc),
                    ledger_transaction_id=None,
                    now=now,
                )
                blocked.append({"item": item["name"], "buyer": producer, "reason": str(exc)})
    return {"available": True, "orders": orders, "restocked": restocked, "blocked": blocked}


def process_procurement_runtime(conn, world_time=None) -> dict:
    if not procurement_available(conn):
        return {"available": False, "orders": [], "restocked": 0, "blocked": []}
    now = _now(world_time)
    supplier_restocked = 0
    for supplier in _supplier_by_item(conn).values():
        supplier_restocked += _restock_supplier(conn, supplier, now)
    result = procure_inputs(conn, now)
    result["supplier_restocked"] = supplier_restocked
    return result
