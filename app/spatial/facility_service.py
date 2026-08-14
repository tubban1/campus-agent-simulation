"""Operational lifecycle for food, water and other spatial facilities."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import insert, select

from app.db import db_savepoint
from app.spatial.models import spatial_nodes, spatial_resources


def _execute(conn, statement, parameters=()):
    if hasattr(conn, "exec_driver_sql"):
        return conn.exec_driver_sql(statement, tuple(parameters))
    return conn.execute(statement, parameters)


def _node_properties(value):
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def _value(row, key):
    """Read a DBAPI sqlite Row or SQLAlchemy Row without schema fallback."""
    try:
        return row[key]
    except (TypeError, KeyError):
        return row._mapping[key]


def _facility_specs(node: dict):
    properties = _node_properties(node.get("properties"))
    tags = properties.get("osm_tags") or {}
    amenity = str(tags.get("amenity") or "").lower()
    name = str(node.get("name") or "")
    capacity = max(10, int(node.get("capacity") or 0))
    is_food = amenity in {"canteen", "restaurant", "cafe", "fast_food"} or any(
        token in name for token in ("食堂", "餐厅", "餐饮", "咖啡", "清晏", "清芬", "观畴")
    )
    is_water = is_food or amenity in {"drinking_water", "water_point"}
    is_library = amenity == "library" or "图书馆" in name
    is_dorm = any(token in name for token in ("宿舍", "公寓", "寝室", "住宅"))
    specs = []
    if is_food:
        specs.extend((
            ("service_windows", "餐饮服务窗口", max(1, capacity // 40), 40.0, ["queue", "consume"]),
            ("meal_stock", "餐食库存", max(60, capacity * 3), 120.0, ["consume"]),
        ))
    if is_water:
        specs.append(("water_stock", "饮水库存", max(40, capacity * 2), 100.0, ["hydrate"]))
    if is_library:
        specs.append(("study_seats", "学习座位", capacity, 30.0, ["observe", "collaborate"]))
    if is_dorm:
        specs.extend((
            ("beds", "住宿床位", capacity, 20.0, ["rest"]),
            ("water_stock", "宿舍饮水库存", max(40, capacity), 80.0, ["hydrate"]),
        ))
    return specs


def sync_real_world_facility_resources(connection, *, world_key: str) -> int:
    """Derive service resources only from imported OSM POI/building facts."""
    nodes = [
        dict(row)
        for row in connection.execute(
            select(spatial_nodes).where(spatial_nodes.c.world_key == world_key)
        ).mappings()
    ]
    existing = {
        (int(row.node_id), str(row.resource_key))
        for row in connection.execute(
            select(spatial_resources.c.node_id, spatial_resources.c.resource_key)
        )
    }
    created = 0
    for node in nodes:
        for key, name, capacity, rate, actions in _facility_specs(node):
            identity = (int(node["id"]), key)
            if identity in existing:
                continue
            connection.execute(
                insert(spatial_resources).values(
                    node_id=node["id"],
                    resource_key=key,
                    name=name,
                    capacity=capacity,
                    available_units=capacity,
                    service_rate_per_hour=rate,
                    status="available",
                    properties={
                        "actions": actions,
                        "source": "osm_facility_mapping_v1",
                        "world_key": world_key,
                    },
                )
            )
            existing.add(identity)
            created += 1
    ensure_facility_states(connection)
    return created


def ensure_facility_states(conn):
    """Create lifecycle records for declared resources only.

    This belongs to import/bootstrap, not the request path.  An absent record
    is therefore a schema/data integrity error rather than an implicit open
    facility.
    """
    rows = _execute(conn, 
        """SELECT r.id, r.resource_key, r.capacity
           FROM spatial_resources r
           LEFT JOIN spatial_facility_states f ON f.resource_id = r.id
           WHERE f.resource_id IS NULL"""
    ).fetchall()
    for row in rows:
        key = str(_value(row, "resource_key"))
        consumable = key in {"meal_stock", "water_stock"}
        if key in {"service_windows", "meal_stock"}:
            hours = (6, 21)
        elif "business" in key or "counter" in key:
            hours = (8, 22)
        else:
            hours = (0, 24)
        capacity = int(_value(row, "capacity") or 0) if consumable else 0
        _execute(conn, 
            """INSERT INTO spatial_facility_states
               (resource_id, open_hour, close_hour, condition, maintenance_status,
                inventory_units, inventory_capacity, last_replenished_day)
               VALUES (?, ?, ?, 100, 'operational', ?, ?, 0)""",
            (_value(row, "id"), hours[0], hours[1], capacity, capacity),
        )
    return len(rows)


def facility_service_status(conn, node_id, action, hour=None):
    hour = datetime.now().hour if hour is None else int(hour)
    rows = _execute(conn, """
        SELECT r.id, r.resource_key, r.available_units, r.status, r.properties,
               f.open_hour, f.close_hour, f.condition, f.maintenance_status,
               f.inventory_units, f.inventory_capacity
        FROM spatial_resources r JOIN spatial_facility_states f ON f.resource_id = r.id
        WHERE r.node_id = ?
    """, (node_id,)).fetchall()
    relevant = []
    for row in rows:
        text = str(_value(row, "properties") or "")
        resource_key = _value(row, "resource_key")
        if action in text or (action == "consume" and resource_key in {"meal_stock", "service_windows"}) or (action == "hydrate" and resource_key == "water_stock"):
            relevant.append(row)
    if not relevant:
        return {"available": False, "quality": 0, "reason": "目标地点未声明此项服务"}
    for row in relevant:
        open_hour = _value(row, "open_hour")
        close_hour = _value(row, "close_hour")
        open_now = open_hour <= hour < close_hour if close_hour != 24 else hour >= open_hour
        if not open_now:
            return {"available": False, "quality": 0, "reason": "设施当前不在服务窗口"}
        if _value(row, "maintenance_status") != "operational" or float(_value(row, "condition") or 0) < 20:
            return {"available": False, "quality": 0, "reason": "设施正在维护或故障"}
        if _value(row, "resource_key") in {"meal_stock", "water_stock"} and int(_value(row, "inventory_units") or 0) <= 0:
            return {"available": False, "quality": 0, "reason": "设施库存已耗尽，等待补给"}
    quality = min(float(_value(row, "condition") or 100) for row in relevant)
    return {"available": True, "quality": round(quality), "reason": ""}


def settle_facility_service(conn, node_id, action, day=0):
    """Consume a unit only after a successful recovery/service action."""
    stock_key = "meal_stock" if action == "consume" else "water_stock" if action == "hydrate" else None
    if not stock_key:
        return None
    row = _execute(conn, """SELECT r.id FROM spatial_resources r
        WHERE r.node_id = ? AND r.resource_key = ? LIMIT 1""", (node_id, stock_key)).fetchone()
    if not row:
        raise RuntimeError(f"facility service resource is missing: {stock_key}")
    _execute(conn, """UPDATE spatial_facility_states
        SET inventory_units = CASE WHEN inventory_units > 0 THEN inventory_units - 1 ELSE 0 END,
            condition = CASE WHEN condition > 0 THEN condition - 0.05 ELSE 0 END,
            updated_at = CURRENT_TIMESTAMP WHERE resource_id = ?""", (_value(row, "id"),))
    return stock_key


def _open_work_order(conn, *, resource_id, order_type, day, units, cost_minor):
    existing = _execute(conn, """
        SELECT id FROM spatial_facility_work_orders
        WHERE resource_id = ? AND order_type = ? AND status IN ('open', 'assigned')
        LIMIT 1
    """, (resource_id, order_type)).fetchone()
    if existing:
        return int(_value(existing, "id")), False
    cursor = _execute(conn, """
        INSERT INTO spatial_facility_work_orders
        (resource_id, order_type, status, requested_day, requested_units, cost_minor)
        VALUES (?, ?, 'open', ?, ?, ?)
    """, (resource_id, order_type, day, units, cost_minor))
    return int(getattr(cursor, "lastrowid", 0) or 0), True


_FACILITY_SUPPLY_ITEM = {
    "meal_stock": "套餐饭",
}


def _try_supply_restock(conn, row, worker_id, day) -> bool:
    """Fill a facility shelf from an auditable procurement purchase.

    Falls back to ``False`` (caller keeps legacy behaviour) whenever the supply
    subsystem, a mapped catalog item, or buyer funds are unavailable.
    """
    resource_key = str(_value(row, "resource_key") or "")
    item_name = _FACILITY_SUPPLY_ITEM.get(resource_key)
    if not item_name:
        return False
    try:
        from app.supply.procurement import procure_item

        result = procure_item(
            conn,
            item_name=item_name,
            quantity=int(_value(row, "requested_units") or 0),
            buyer_actor_key=f"resident:{int(worker_id)}",
            location=str(_value(row, "node_name") or ""),
        )
    except Exception:
        return False
    if not result["fulfilled"]:
        return False
    qty = int(result["quantity"])
    inventory = min(
        int(_value(row, "inventory_capacity") or 0),
        int(_value(row, "inventory_units") or 0) + qty,
    )
    _execute(
        conn,
        "UPDATE spatial_facility_states SET inventory_units = ?, last_replenished_day = ? WHERE resource_id = ?",
        (inventory, day, _value(row, "resource_id")),
    )
    _execute(
        conn,
        "UPDATE spatial_resources SET available_units = ?, status = 'available' WHERE id = ?",
        (inventory, _value(row, "resource_id")),
    )
    return True


def _complete_colocated_work_orders(conn, *, day):
    """Complete only work for which an eligible Agent is physically present."""
    rows = _execute(conn, """
        SELECT o.id, o.resource_id, o.order_type, o.requested_units, o.cost_minor,
               r.node_id, r.resource_key, n.name AS node_name,
               r.capacity, f.inventory_units, f.inventory_capacity, f.condition,
               worker.id AS worker_id, worker.money
        FROM spatial_facility_work_orders o
        JOIN spatial_resources r ON r.id = o.resource_id
        JOIN spatial_facility_states f ON f.resource_id = r.id
        JOIN spatial_nodes n ON n.id = r.node_id
        JOIN agent_spatial_states state ON state.current_node_id = r.node_id
          AND state.movement_status IN ('idle', 'arrived')
        JOIN residents worker ON worker.id = state.resident_id
        WHERE o.status IN ('open', 'assigned')
          AND (o.assigned_resident_id IS NULL OR o.assigned_resident_id = worker.id)
          AND ((o.order_type = 'restock' AND (worker.role LIKE '%商%' OR worker.role LIKE '%后勤%'))
            OR (o.order_type = 'repair' AND worker.role LIKE '%后勤%'))
        ORDER BY o.id, worker.id
    """).fetchall()
    completed, events, seen = 0, [], set()
    for row in rows:
        order_id = int(_value(row, "id"))
        if order_id in seen:
            continue
        seen.add(order_id)
        cost = int(_value(row, "cost_minor") or 0)
        if int(_value(row, "money") or 0) * 100 < cost:
            continue
        if cost:
            _execute(conn, "UPDATE residents SET money = money - ? WHERE id = ?", ((cost + 99) // 100, _value(row, "worker_id")))
        if _value(row, "order_type") == "restock":
            # Supply chain is the only way a shelf is refilled.  A work order
            # that cannot yet be fulfilled stays open (a genuine shortage);
            # inventory is never fabricated from nothing.
            if not _try_supply_restock(conn, row, _value(row, "worker_id"), day):
                continue
            kind = "restocked"
        else:
            _execute(conn, "UPDATE spatial_facility_states SET condition = 70, maintenance_status = 'operational' WHERE resource_id = ?", (_value(row, "resource_id"),))
            _execute(conn, "UPDATE spatial_resources SET status = 'available' WHERE id = ?", (_value(row, "resource_id"),))
            kind = "repaired"
        _execute(conn, "UPDATE spatial_facility_work_orders SET status = 'completed', completed_by_resident_id = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?", (_value(row, "worker_id"), order_id))
        completed += 1
        events.append({"type": kind, "order_id": order_id, "resource_id": int(_value(row, "resource_id")), "worker_id": int(_value(row, "worker_id"))})
    return completed, events


def _assign_open_work_orders(conn):
    """Assign each open order to one eligible Agent and start real movement."""
    orders = _execute(conn, """
        SELECT o.id, o.order_type, r.node_id, n.name AS node_name,
               n.x, n.z
        FROM spatial_facility_work_orders o
        JOIN spatial_resources r ON r.id = o.resource_id
        JOIN spatial_nodes n ON n.id = r.node_id
        WHERE o.status = 'open' AND o.assigned_resident_id IS NULL
        ORDER BY o.id
    """).fetchall()
    assignments = []
    for order in orders:
        order_type = _value(order, "order_type")
        roles = ("%后勤%",) if order_type == "repair" else ("%商%", "%后勤%")
        worker = _execute(conn, """
            SELECT r.id, r.name, s.current_node_id,
                   ((s.x - ?) * (s.x - ?) + (s.z - ?) * (s.z - ?)) AS distance_sq
            FROM residents r JOIN agent_spatial_states s ON s.resident_id = r.id
            WHERE s.movement_status IN ('idle', 'arrived')
              AND (r.role LIKE ? OR r.role LIKE ?)
            ORDER BY distance_sq, r.id LIMIT 1
        """, (
            _value(order, "x"), _value(order, "x"), _value(order, "z"), _value(order, "z"),
            roles[0], roles[-1],
        )).fetchone()
        if not worker:
            continue
        _execute(conn, """
            UPDATE spatial_facility_work_orders
            SET status = 'assigned', assigned_resident_id = ? WHERE id = ?
        """, (_value(worker, "id"), _value(order, "id")))
        if int(_value(worker, "current_node_id")) != int(_value(order, "node_id")):
            try:
                from app.spatial.runtime import start_spatial_movement
                start_spatial_movement(conn, _value(worker, "id"), _value(order, "node_name"))
            except Exception:
                # Assignment remains auditable and can be retried; never
                # teleport a worker or silently complete a failed route.
                pass
        assignments.append({
            "type": "work_order_assigned", "order_id": int(_value(order, "id")),
            "worker_id": int(_value(worker, "id")), "node_id": int(_value(order, "node_id")),
        })
    return assignments


def advance_facility_lifecycle(conn, *, day: int, hour: int) -> dict:
    """Advance declared facility operations from world time and observed demand.

    Consumables are restocked once per simulated day after their service window
    opens.  A depleted or worn facility is unavailable until the overnight
    maintenance window repairs it.  This never creates resources: only OSM
    imported facilities that already declared a resource participate.

    The whole body runs inside a savepoint so a failure (for example a schema
    still catching up, or a best-effort supply lookup) can never leave the
    enclosing world-tick transaction aborted in PostgreSQL.
    """
    try:
        with db_savepoint(conn, "facility_lifecycle"):
            return _advance_facility_lifecycle_body(conn, day=day)
    except Exception as exc:
        # A deliberately topology-free test world has no facility subsystem
        # to advance.  Do not turn that fact into a tick failure; production
        # readiness independently rejects a schema that lacks these tables.
        if "spatial_resources" in str(exc) or "spatial_facility_states" in str(exc):
            return {
                "restocked": 0, "repaired": 0, "closed_for_maintenance": 0,
                "events": [], "skipped": True, "reason": "no_spatial_facilities",
            }
        raise


def _advance_facility_lifecycle_body(conn, *, day: int) -> dict:
    rows = _execute(conn, """
        SELECT n.id AS node_id, n.name AS node_name, r.id AS resource_id,
               r.resource_key, r.capacity, r.available_units,
               f.open_hour, f.close_hour, f.condition, f.maintenance_status,
               f.inventory_units, f.inventory_capacity, f.last_replenished_day,
               (SELECT COUNT(*) FROM spatial_admission_queue q
                 WHERE q.node_id = n.id AND q.status IN ('waiting', 'queued')) AS queue_length
        FROM spatial_resources r
        JOIN spatial_nodes n ON n.id = r.node_id
        JOIN spatial_facility_states f ON f.resource_id = r.id
        """).fetchall()
    restocked = repaired = closed_for_maintenance = work_orders_created = 0
    events = []
    completed_orders, completed_events = _complete_colocated_work_orders(conn, day=day)
    events.extend(completed_events)
    for row in rows:
        resource_key = str(_value(row, "resource_key"))
        consumable = resource_key in {"meal_stock", "water_stock"}
        condition = float(_value(row, "condition") or 0)
        maintenance = str(_value(row, "maintenance_status") or "operational")
        inventory = int(_value(row, "inventory_units") or 0)
        capacity = int(_value(row, "inventory_capacity") or 0)
        last_day = int(_value(row, "last_replenished_day") or 0)
        open_hour = int(_value(row, "open_hour") or 0)
        queue_length = int(_value(row, "queue_length") or 0)
        changed = False

        if consumable and inventory <= 0:
            order_id, created = _open_work_order(conn, resource_id=_value(row, "resource_id"), order_type="restock", day=day, units=capacity, cost_minor=max(100, capacity * 5))
            work_orders_created += int(created)
            events.append({"type": "restock_requested", "order_id": order_id, "node_id": int(_value(row, "node_id")), "node_name": _value(row, "node_name"), "resource_key": resource_key})

        # A facility is not silently usable at zero health.  The actual wear
        # comes from settled service use; this lifecycle advances the state
        # transition and gives a maintenance crew an overnight repair window.
        if condition < 20 and maintenance == "operational":
            maintenance = "maintenance"
            closed_for_maintenance += 1
            changed = True
            events.append({"type": "maintenance_started", "node_id": int(_value(row, "node_id")), "node_name": _value(row, "node_name"), "resource_key": resource_key, "queue_length": queue_length})
            order_id, created = _open_work_order(conn, resource_id=_value(row, "resource_id"), order_type="repair", day=day, units=1, cost_minor=500)
            work_orders_created += int(created)
            events.append({"type": "repair_requested", "order_id": order_id, "node_id": int(_value(row, "node_id")), "node_name": _value(row, "node_name"), "resource_key": resource_key})

        if changed:
            available = inventory if consumable else int(_value(row, "available_units") or 0)
            _execute(conn, """
                UPDATE spatial_facility_states
                SET condition = ?, maintenance_status = ?, inventory_units = ?,
                    last_replenished_day = ?, updated_at = CURRENT_TIMESTAMP
                WHERE resource_id = ?
            """, (condition, maintenance, inventory, last_day, _value(row, "resource_id")))
            _execute(conn, """
                UPDATE spatial_resources
                SET available_units = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                available,
                "available" if maintenance == "operational" and (not consumable or inventory > 0) else "unavailable",
                _value(row, "resource_id"),
            ))
    assignments = _assign_open_work_orders(conn)
    events.extend(assignments)
    return {
        "restocked": restocked,
        "repaired": repaired,
        "closed_for_maintenance": closed_for_maintenance,
        "work_orders_created": work_orders_created,
        "work_orders_completed": completed_orders,
        "work_orders_assigned": len(assignments),
        "events": events,
        "skipped": False,
    }
