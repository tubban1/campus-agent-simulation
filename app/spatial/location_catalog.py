"""Real-world location selection backed by imported spatial facts.

The catalogue deliberately returns concrete node names, not the retired
``食堂/图书馆/宿舍区`` demonstration labels.  If no imported world exists it
returns an empty set so isolated legacy test fixtures can opt into their own
topology without changing production behaviour.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional, Set


_CATEGORY_TERMS = {
    "rest": ("宿舍", "公寓", "寝室", "住宅", "residence"),
    "consume": ("食堂", "餐厅", "餐饮", "咖啡", "清晏", "清芬", "观畴", "桃李", "紫荆园"),
    "study": ("图书馆", "教学", "教室", "学堂", "实验", "科研", "逸夫"),
    "service": ("主楼", "行政", "办公", "服务", "校务"),
    "activity": ("操场", "体育", "球场", "运动"),
    "business": ("商业", "商店", "超市", "便利", "咖啡"),
}


def _rows(conn, statement, parameters=()):
    if hasattr(conn, "exec_driver_sql"):
        result = conn.exec_driver_sql(statement, tuple(parameters))
        return [dict(row) for row in result.mappings().all()]
    return [dict(row) for row in conn.execute(statement, parameters).fetchall()]


def _categories(name: str, resource_keys: set[str]) -> set[str]:
    text = str(name or "").lower()
    categories = {
        category
        for category, terms in _CATEGORY_TERMS.items()
        if any(term.lower() in text for term in terms)
    }
    if {"meal_stock", "service_windows"} & resource_keys:
        categories.add("consume")
    if "beds" in resource_keys:
        categories.add("rest")
    if "study_seats" in resource_keys:
        categories.add("study")
    return categories or {"general"}


def real_world_locations(conn) -> list[dict]:
    """Return concrete locations from the most recently imported real world."""
    # Several deterministic policy helpers intentionally run without a
    # database (for example weather-only unit tests).  They have no physical
    # catalogue to consult, so return no candidates rather than pretending a
    # demo campus exists.  Real database errors below are still surfaced.
    if conn is None:
        return []
    try:
        worlds = _rows(
            conn,
            "SELECT world_key FROM spatial_import_batches ORDER BY imported_at DESC, id DESC LIMIT 1",
        )
    except Exception as exc:
        # This is an optional candidate source for small non-spatial unit
        # fixtures.  It does not invent a fallback place; it merely reports
        # that no imported world catalogue is available.  Any other database
        # error remains visible to the caller.
        if "spatial_import_batches" in str(exc):
            return []
        raise
    if not worlds:
        return []
    world_key = worlds[0]["world_key"]
    nodes = _rows(
        conn,
        """SELECT id, name, node_type, capacity, status, x, y, z
           FROM spatial_nodes
           WHERE world_key = ? AND node_type IN ('building', 'poi', 'outdoor_area')
             AND status IN ('open', '开放')
           ORDER BY capacity DESC, id""",
        (world_key,),
    )
    resources = _rows(
        conn,
        """SELECT r.node_id, r.resource_key
           FROM spatial_resources r JOIN spatial_nodes n ON n.id = r.node_id
           WHERE n.world_key = ? AND r.status IN ('available', 'open', '开放')""",
        (world_key,),
    )
    keys_by_node: dict[int, set[str]] = defaultdict(set)
    for resource in resources:
        keys_by_node[int(resource["node_id"])].add(str(resource["resource_key"]))
    result = []
    seen_names = set()
    for node in nodes:
        name = str(node["name"] or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        result.append({
            **node,
            "world_key": world_key,
            "resource_keys": keys_by_node[int(node["id"])],
            "categories": _categories(name, keys_by_node[int(node["id"])]),
        })
    return result


def is_real_world_location(conn, location: str) -> bool:
    name = str(location or "").strip()
    return bool(name and any(item["name"] == name for item in real_world_locations(conn)))


def supports_action(location: str, action: str, resource_keys: Optional[Set[str]] = None) -> bool:
    categories = _categories(location, resource_keys or set())
    return {
        "rest": "rest",
        "consume": "consume",
        "queue": "consume",
        "hydrate": "consume",
        "attend_class": "study",
        "observe": "study",
        "request_leave": "service",
        "club_activity": "activity",
    }.get(action, "general") in categories or action in {"move", "chat", "reflect", "collaborate", "conflict"}


def best_real_location(conn, action: str, *, current_location: str = "") -> str | None:
    candidates = real_world_locations(conn)
    if not candidates:
        return None
    for candidate in candidates:
        if supports_action(candidate["name"], action, candidate["resource_keys"]):
            if candidate["name"] != current_location or len(candidates) == 1:
                return candidate["name"]
    return current_location if is_real_world_location(conn, current_location) else None


def real_location_options(conn, role: str, hour: int, *, current_location: str = "", weather: str = "") -> list[tuple[str, float]]:
    """Offer concrete, category-balanced POI candidates for an Agent tick."""
    candidates = real_world_locations(conn)
    if not candidates:
        return []
    if 0 <= int(hour) < 6:
        priorities = (("rest", 90), ("study", 4), ("general", 2))
    elif 6 <= int(hour) < 9 or 11 <= int(hour) < 14 or 17 <= int(hour) < 21:
        priorities = (("consume", 50), ("study", 24), ("rest", 14), ("activity", 8))
    elif "商" in str(role) or "店" in str(role):
        priorities = (("business", 48), ("consume", 25), ("service", 12), ("rest", 10))
    elif "教师" in str(role) or "辅导" in str(role) or "管理" in str(role):
        priorities = (("study", 45), ("service", 28), ("consume", 12), ("rest", 10))
    else:
        priorities = (("study", 48), ("consume", 20), ("activity", 13), ("rest", 12))
    rainy = any(token in str(weather) for token in ("雨", "雪", "雷", "大风"))
    result: list[tuple[str, float]] = []
    used_names = set()
    for category, weight in priorities:
        matches = [
            item for item in candidates
            if category in item["categories"]
            and item["name"] != current_location
            and item["name"] not in used_names
        ][:3]
        for rank, match in enumerate(matches):
            adjusted = float(weight) * (0.2 if rainy and category == "activity" else 1.0)
            # Keep alternatives explicit instead of collapsing a category to
            # its first database row.  The selector can therefore re-plan to
            # the next real POI if the preferred service fails.
            result.append((match["name"], adjusted * (1.0 - rank * 0.18)))
            used_names.add(match["name"])
    if current_location and is_real_world_location(conn, current_location):
        result.append((current_location, 18.0))
    return result or [(candidates[0]["name"], 1.0)]


def rank_real_location_options(conn, resident_id: int, action: str, *, hour: int, weather: str = "") -> list[dict]:
    """Return auditable competing real-POI options for an Agent action."""
    try:
        state = _rows(
            conn,
            """SELECT s.current_node_id, s.x, s.y, s.z, c.base_speed_m_per_min,
                      b.hunger, b.fatigue, b.health, r.location, r.role
               FROM agent_spatial_states s
               JOIN residents r ON r.id = s.resident_id
               LEFT JOIN agent_spatial_capabilities c ON c.resident_id = s.resident_id
               LEFT JOIN agent_body_states b ON b.resident_id = s.resident_id
               WHERE s.resident_id = ?""",
            (resident_id,),
        )
    except Exception as exc:
        # Optional spatial runtime tables may not exist in isolated legacy
        # fixtures.  Fall back to a default physical state so real-POI ranking
        # stays available with reduced fidelity instead of failing the tick.
        if any(token in str(exc) for token in ("agent_spatial_states", "agent_spatial_capabilities", "agent_body_states")):
            state = []
        else:
            raise
    # A partially initialized Agent must still be able to discover actual
    # locations.  Missing optional physiology/capability records lower score
    # fidelity, not the existence of the physical world.
    current = state[0] if state else {
        "current_node_id": None, "x": 0.0, "y": 0.0, "z": 0.0,
        "base_speed_m_per_min": 70.0, "hunger": 0.0, "fatigue": 0.0,
        "health": 100.0, "location": "", "role": "",
    }
    candidates = real_world_locations(conn)
    # A decision compares a small, meaningful frontier rather than scanning
    # every classroom in a 13k-node campus.  This keeps one Agent tick bounded
    # while preserving several genuine alternatives for rerouting.
    desired = [
        item for item in candidates
        if supports_action(item["name"], action, item["resource_keys"])
    ][:12]
    if not desired:
        return []
    queue_rows = _rows(
        conn,
        "SELECT node_id, COUNT(*) AS count FROM spatial_admission_queue WHERE status IN ('waiting', 'queued') GROUP BY node_id",
    )
    queues = {int(row["node_id"]): int(row["count"]) for row in queue_rows}
    physical_rows = _rows(conn, "SELECT node_id, access_status, crowd_density FROM spatial_physical_states")
    physical = {int(row["node_id"]): row for row in physical_rows}
    speed = max(20.0, float(current.get("base_speed_m_per_min") or 70.0))
    hunger = float(current.get("hunger") or 0)
    fatigue = float(current.get("fatigue") or 0)
    scored = []
    for candidate in desired:
        dx = float(candidate.get("x") or 0) - float(current.get("x") or 0)
        dz = float(candidate.get("z") or 0) - float(current.get("z") or 0)
        travel = (dx * dx + dz * dz) ** 0.5 / speed
        physical_state = physical.get(int(candidate["id"]), {})
        access = str(physical_state.get("access_status") or "open")
        queue_penalty = queues.get(int(candidate["id"]), 0) * 2.5
        crowd_penalty = float(physical_state.get("crowd_density") or 0) * 12.0
        weather_penalty = 10.0 if "activity" in candidate["categories"] and any(token in str(weather) for token in ("雨", "雪", "雷", "大风")) else 0.0
        need_bonus = 0.0
        if action == "consume":
            need_bonus = hunger * 0.35
        elif action == "rest":
            need_bonus = fatigue * 0.30
        facility = None
        if action in {"consume", "hydrate", "rest", "observe", "collaborate"}:
            from app.spatial.facility_service import facility_service_status
            facility = facility_service_status(conn, int(candidate["id"]), action, hour=hour)
        available = access == "open" and (facility is None or facility["available"])
        score = round(100.0 + need_bonus - travel - queue_penalty - crowd_penalty - weather_penalty, 3)
        scored.append({
            "node_id": int(candidate["id"]), "location": candidate["name"], "score": score,
            "available": available,
            "reasons": {
                "travel_minutes_estimate": round(travel, 2), "queue_penalty": queue_penalty,
                "crowd_penalty": crowd_penalty, "weather_penalty": weather_penalty,
                "need_bonus": round(need_bonus, 2), "access_status": access,
                "facility": facility,
            },
        })
    return sorted(scored, key=lambda item: (not item["available"], -item["score"], item["location"]))
