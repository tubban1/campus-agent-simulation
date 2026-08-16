from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from math import dist
from app.world_runtime.clock import get_world_now

from app.adaptation.service import (
    constraint_runtime_available,
    evaluate_space_constraint,
    latest_explicit_constraint_response,
    resolve_boundary_attempt,
)
from app.spatial.planner import RouteNotFoundError, edge_travel_minutes, plan_route

from app.db import db_savepoint


ACTIVE_MOVEMENT_STATUSES = {"moving", "replanning", "waiting"}


class SpatialAdmissionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _json_value(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _iso_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_world_datetime(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=WORLD_TZ)


def spatial_runtime_available(conn):
    return bool(conn.execute("PRAGMA table_info(agent_spatial_states)").fetchall())


def effective_movement_speed(base_speed_m_per_min, body_state=None):
    body = body_state or {}
    fatigue = float(body.get("fatigue") or 0)
    hunger = float(body.get("hunger") or 0)
    health = float(body.get("health") if body.get("health") is not None else 100)
    factor = 1.0
    factor -= max(0.0, fatigue - 40.0) * 0.006
    factor -= max(0.0, hunger - 70.0) * 0.003
    if health < 60:
        factor *= max(0.65, health / 60.0)
    factor = max(0.45, min(1.0, factor))
    return round(float(base_speed_m_per_min) * factor, 3)


def _load_nodes(conn):
    nodes = []
    for row in conn.execute("SELECT * FROM spatial_nodes ORDER BY id").fetchall():
        item = dict(row)
        item["properties"] = _json_value(item.get("properties"), {})
        nodes.append(item)
    return nodes


def _load_edges(conn):
    dynamic = {}
    try:
        # The physical-state table is optional; isolated topology tests
        # deliberately omit it.  Run inside a savepoint so a missing table
        # (UndefinedTable) rolls back only this probe instead of poisoning the
        # surrounding tick transaction -- otherwise every later statement in
        # the tick raises InFailedSqlTransaction.
        with db_savepoint(conn, "edge_physical_states"):
            now_str = datetime.now(timezone.utc).isoformat()
            query = """SELECT edge_id, access_status, travel_factor FROM spatial_edge_physical_states
                       WHERE expires_at IS NULL OR expires_at > ?"""
            result = conn.exec_driver_sql(query, (now_str,)) if hasattr(conn, "exec_driver_sql") else conn.execute(query, (now_str,))
            for raw in result.fetchall():
                row = dict(raw._mapping) if hasattr(raw, "_mapping") else dict(raw)
                dynamic[int(row["edge_id"])] = row
    except Exception:
        # Schema readiness prevents this in production; isolated topology
        # tests deliberately omit optional physical-state tables.
        dynamic = {}
    edges = []
    result = conn.exec_driver_sql("SELECT * FROM spatial_edges ORDER BY id") if hasattr(conn, "exec_driver_sql") else conn.execute("SELECT * FROM spatial_edges ORDER BY id")
    for raw in result.fetchall():
        item = dict(raw._mapping) if hasattr(raw, "_mapping") else dict(raw)
        item["properties"] = _json_value(item.get("properties"), {})
        state = dynamic.get(int(item["id"]))
        if state:
            item["status"] = state["access_status"]
            item["weather_factor"] = float(item.get("weather_factor") or 1.0) * float(state.get("travel_factor") or 1.0)
        edges.append(item)
    return edges


LOCATION_CATEGORY_ALIASES = {
    "宿舍区": ["宿舍", "紫荆公寓", "南区公寓", "双清公寓", "学生公寓", "公寓", "dorm", "dormitory"],
    "宿舍": ["宿舍区", "紫荆公寓", "南区公寓", "双清公寓", "学生公寓", "公寓", "dorm"],
    "食堂": ["清晏楼", "观愁园", "紫荆园", "桃李园", "听涛园", "玉树园", "丁香园", "芝兰园", "食堂", "餐饮", "餐厅", "饭堂", "canteen", "dining"],
    "图书馆": ["图书馆", "逸夫馆", "李文正馆", "凯风馆", "library"],
    "教学楼": ["教学楼", "六教", "四教", "三教", "二教", "一教", "理科楼", "文科楼", "教学", "teaching"],
    "商业街": ["商业街", "综合超市", "便利店", "CVS", "market", "shop"],
    "操场": ["操场", "西大操场", "东大操场", "紫荆操场", "体育场", "playground", "stadium"],
    "校务处": ["校务处", "主楼", "行政楼", "办公楼", "office", "admin"],
}


def _resolve_category_terms(destination_str):
    terms = LOCATION_CATEGORY_ALIASES.get(destination_str, [])
    if terms:
        return terms
    for cat_key, cat_terms in LOCATION_CATEGORY_ALIASES.items():
        if cat_key in destination_str:
            return cat_terms
        for term in cat_terms:
            if term in destination_str:
                return cat_terms
    if any(w in destination_str for w in ["餐", "食", "饭", "饮", "canteen", "dining"]):
        return LOCATION_CATEGORY_ALIASES.get("食堂", [])
    if any(w in destination_str for w in ["宿", "寓", "楼", "区", "dorm"]):
        return LOCATION_CATEGORY_ALIASES.get("宿舍区", [])
    if any(w in destination_str for w in ["书", "阅", "自习", "library"]):
        return LOCATION_CATEGORY_ALIASES.get("图书馆", [])
    if any(w in destination_str for w in ["教", "课", "学", "lab"]):
        return LOCATION_CATEGORY_ALIASES.get("教学楼", [])
    if any(w in destination_str for w in ["操", "场", "体", "育"]):
        return LOCATION_CATEGORY_ALIASES.get("操场", [])
    if any(w in destination_str for w in ["务", "政", "办", "主楼"]):
        return LOCATION_CATEGORY_ALIASES.get("校务处", [])
    return []


def _destination_candidates(nodes, destination, world_key=None):
    destination_str = str(destination or "").strip()
    if not destination_str or not nodes:
        return []

    # First try exact numeric ID match
    if destination_str.isdigit():
        target_id = int(destination_str)
        by_id = next((n for n in nodes if int(n["id"]) == target_id), None)
        if by_id:
            return [by_id]

    # Filter candidate nodes by active world_key
    candidate_nodes = nodes
    if world_key:
        filtered = [
            n for n in nodes
            if n.get("world_key") == world_key
            or (n.get("properties") or {}).get("world_key") == world_key
            or (n.get("code") and str(n["code"]).startswith(f"{world_key}_"))
        ]
        if filtered:
            candidate_nodes = filtered

    exact_matches = []
    partial_matches = []
    category_terms = _resolve_category_terms(destination_str)

    for node in candidate_nodes:
        properties = node.get("properties") or {}
        code = str(node.get("code") or "")
        name = str(node.get("name") or "")
        location = str(properties.get("location") or "")
        if code == destination_str or name == destination_str or location == destination_str:
            exact_matches.append(node)
        elif (name and (destination_str in name or name in destination_str)) or (location and (destination_str in location or location in destination_str)):
            partial_matches.append(node)
        elif category_terms and any((term in name or name in term or term in location) for term in category_terms if term):
            partial_matches.append(node)

    matches = exact_matches or partial_matches
    if not matches:
        for node in nodes:
            properties = node.get("properties") or {}
            code = str(node.get("code") or "")
            name = str(node.get("name") or "")
            location = str(properties.get("location") or "")
            if code == destination_str or name == destination_str or location == destination_str or (name and (destination_str in name or name in destination_str)):
                matches.append(node)
            elif category_terms and any((term in name or name in term or term in location) for term in category_terms if term):
                matches.append(node)

    if not matches:
        # Fallback: Prefer tier 0 or landmark/building nodes in candidate pool to ensure non-empty candidate list
        landmark_fallback = [n for n in candidate_nodes if n.get("node_type") in {"building", "poi", "outdoor_area"}]
        matches = landmark_fallback[:5] if landmark_fallback else candidate_nodes[:5]

    if not matches:
        return []

    # Keep exact names ahead of aliases, then prefer actual destinations over
    # road points. Reachability is resolved by the caller using A*.
    unique = {int(node["id"]): node for node in matches}
    return sorted(
        unique.values(),
        key=lambda node: (
            node not in exact_matches,
            node.get("node_type") not in {"building", "poi", "outdoor_area"},
            -int(node["id"]),
        ),
    )


def _destination_node(nodes, destination, world_key=None):
    candidates = _destination_candidates(nodes, destination, world_key=world_key)
    return candidates[0] if candidates else None


def _reachable_destination_route(
    nodes,
    edges,
    start_node_id,
    destination,
    world_key,
    speed_m_per_min,
    accessibility_needs,
):
    """Choose a real destination candidate that is reachable from the Agent.

    Imported maps can legitimately contain several locations matching a legacy
    action label such as ``食堂``.  Selecting by database ID alone can choose a
    disconnected building even when another real canteen is reachable.
    """
    candidates = _destination_candidates(nodes, destination, world_key=world_key)
    if not candidates:
        # Fallback to start node if candidate set is unexpectedly empty
        start_node = next((n for n in nodes if int(n["id"]) == int(start_node_id)), None)
        if start_node:
            return start_node, {"edge_ids": [], "node_ids": [int(start_node_id)], "distance_meters": 0.0, "cost_minutes": 0.0}
        raise ValueError("地点不存在")
    last_error = None
    for candidate in candidates:
        try:
            return candidate, plan_route(
                nodes,
                edges,
                start_node_id,
                candidate["id"],
                speed_m_per_min,
                accessibility_needs,
            )
        except RouteNotFoundError as exc:
            last_error = exc

    if last_error:
        raise last_error
    # Fallback to stay at starting node if no route can be planned to any candidate
    start_node = next((n for n in nodes if int(n["id"]) == int(start_node_id)), None)
    if start_node:
        return start_node, {"edge_ids": [], "node_ids": [int(start_node_id)], "distance_meters": 0.0, "cost_minutes": 0.0}
    raise RouteNotFoundError("No traversable route to destination")


def _check_destination_admission(conn, target_node, world_time, resident_id):
    location = target_node.get("properties", {}).get("location") or target_node["name"]
    space = conn.execute(
        """
        SELECT capacity, open_hour, close_hour, status
        FROM campus_spaces WHERE location = ?
        """,
        (location,),
    ).fetchone()
    if not space:
        return {"allowed": True, "location": location}
    hour = world_time.hour
    open_hour = int(space["open_hour"])
    close_hour = int(space["close_hour"])
    within_hours = (
        open_hour <= hour < close_hour
        if close_hour != 24
        else hour >= open_hour
    )
    if space["status"] != "开放" or not within_hours:
        return {
            "allowed": False,
            "code": "location_closed",
            "reason": f"{location}当前未开放",
            "location": location,
        }
    occupancy = conn.execute(
        """
        SELECT COUNT(*) AS value
        FROM agent_spatial_states
        WHERE current_node_id = ? AND resident_id != ?
          AND movement_status NOT IN ('moving', 'replanning')
        """,
        (target_node["id"], resident_id),
    ).fetchone()["value"]
    capacity = int(space["capacity"])
    if int(occupancy) >= capacity:
        return {
            "allowed": False,
            "code": "space_full",
            "reason": f"{location}当前满员",
            "location": location,
            "occupancy": int(occupancy),
            "capacity": capacity,
        }
    return {
        "allowed": True,
        "location": location,
        "occupancy": int(occupancy),
        "capacity": capacity,
    }


def _evaluate_destination_constraint(
    conn,
    target_node,
    world_time,
    resident_id,
    requested_response="auto",
):
    if not constraint_runtime_available(conn):
        admission = _check_destination_admission(
            conn, target_node, world_time, resident_id
        )
        return {
            **admission,
            "physically_possible": True,
            "officially_permitted": bool(admission["allowed"]),
            "service_available": bool(admission["allowed"]),
            "selected_response": (
                "enter"
                if admission["allowed"]
                else ("queue" if admission.get("code") == "space_full" else "blocked")
            ),
        }
    evaluation = evaluate_space_constraint(
        conn,
        resident_id=resident_id,
        target_node=target_node,
        world_time=world_time,
        requested_response=requested_response,
    )
    return {
        **evaluation,
        "allowed": bool(evaluation["officially_permitted"])
        and not bool(evaluation["full"]),
        "code": (
            ""
            if bool(evaluation["officially_permitted"]) and not bool(evaluation["full"])
            else (
                "space_full"
                if bool(evaluation["full"])
                else "location_closed"
            )
        ),
        "reason": (
            ""
            if bool(evaluation["officially_permitted"]) and not bool(evaluation["full"])
            else (
                f"{evaluation['location']}当前容量紧张"
                if bool(evaluation["full"])
                else f"{evaluation['location']}当前没有正式进入许可"
            )
        ),
    }


def _primary_resource(conn, node_id):
    row = conn.execute(
        """
        SELECT * FROM spatial_resources
        WHERE node_id = ? AND status = 'available'
        ORDER BY id LIMIT 1
        """,
        (node_id,),
    ).fetchone()
    return dict(row) if row else None


def _suggested_alternatives(location):
    return {
        "图书馆": ["教学楼", "宿舍区"],
        "食堂": ["商业街", "宿舍区"],
        "教学楼": ["图书馆", "宿舍区"],
        "商业街": ["食堂", "宿舍区"],
        "校务处": ["教学楼"],
        "操场": ["宿舍区", "教学楼"],
    }.get(location, ["宿舍区"])


def _ensure_admission_queue(
    conn,
    state,
    admission,
    world_time,
    tick_number,
    branch_key,
):
    existing = conn.execute(
        "SELECT * FROM spatial_admission_queue WHERE resident_id = ?",
        (state["resident_id"],),
    ).fetchone()
    if existing:
        return dict(existing)
    resource = _primary_resource(conn, state["target_node_id"])
    ahead = conn.execute(
        """
        SELECT COUNT(*) AS value FROM spatial_admission_queue
        WHERE node_id = ? AND status = 'waiting'
        """,
        (state["target_node_id"],),
    ).fetchone()["value"]
    position = int(ahead) + 1
    service_rate = float(resource["service_rate_per_hour"]) if resource else 1.0
    estimated_wait = round(position / service_rate * 60.0, 2)
    patience = float(10 + (int(state["resident_id"]) * 7) % 21)
    cursor = conn.execute(
        """
        INSERT INTO spatial_admission_queue
        (resident_id, node_id, resource_id, requested_at, queue_position,
         patience_minutes, estimated_wait_minutes, reason_code, branch_key,
         requested_tick, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting')
        """,
        (
            state["resident_id"],
            state["target_node_id"],
            resource["id"] if resource else None,
            world_time.isoformat(),
            position,
            patience,
            estimated_wait,
            admission["code"],
            branch_key,
            tick_number,
        ),
    )
    return {
        "id": cursor.lastrowid,
        "resident_id": state["resident_id"],
        "node_id": state["target_node_id"],
        "resource_id": resource["id"] if resource else None,
        "requested_at": world_time.isoformat(),
        "queue_position": position,
        "patience_minutes": patience,
        "estimated_wait_minutes": estimated_wait,
        "reason_code": admission["code"],
        "branch_key": branch_key,
        "requested_tick": tick_number,
        "status": "waiting",
    }


def _waiting_has_expired(queue, world_time):
    requested_at = _iso_datetime(queue.get("requested_at"))
    if not requested_at:
        return False, 0.0
    waited = max(0.0, (world_time - requested_at).total_seconds() / 60.0)
    return waited >= float(queue["patience_minutes"]), waited


def _movement_context(conn, resident_id):
    row = conn.execute(
        """
        SELECT s.*, c.base_speed_m_per_min, c.accessibility_needs,
               body.hunger, body.fatigue, body.health,
               r.name AS resident_name, r.location AS legacy_location
        FROM agent_spatial_states s
        JOIN agent_spatial_capabilities c ON c.resident_id = s.resident_id
        JOIN residents r ON r.id = s.resident_id
        LEFT JOIN agent_body_states body ON body.resident_id = s.resident_id
        WHERE s.resident_id = ?
        """,
        (resident_id,),
    ).fetchone()
    if not row:
        raise ValueError("Agent 空间状态或移动能力尚未初始化")
    item = dict(row)
    item["path"] = _json_value(item.get("path"), [])
    item["accessibility_needs"] = _json_value(
        item.get("accessibility_needs"),
        {},
    )
    item["effective_speed_m_per_min"] = effective_movement_speed(
        item["base_speed_m_per_min"],
        item,
    )
    return item


def get_active_movement(conn, resident_id):
    if not spatial_runtime_available(conn):
        return None
    row = conn.execute(
        """
        SELECT resident_id, movement_status, target_node_id, progress,
               remaining_distance_meters, estimated_arrival_at
        FROM agent_spatial_states
        WHERE resident_id = ?
        """,
        (resident_id,),
    ).fetchone()
    if not row or row["movement_status"] not in ACTIVE_MOVEMENT_STATUSES:
        return None
    return dict(row)


def preview_route(conn, resident_id, destination):
    if not spatial_runtime_available(conn):
        raise ValueError("空间运行时尚未初始化")
    context = _movement_context(conn, resident_id)
    nodes = _load_nodes(conn)
    curr_node = next((n for n in nodes if int(n["id"]) == int(context["current_node_id"])), None)
    curr_world = (curr_node.get("world_key") if curr_node else None) or "tsinghua_main"
    target, route = _reachable_destination_route(
        nodes,
        _load_edges(conn),
        context["current_node_id"],
        destination,
        curr_world,
        context["effective_speed_m_per_min"],
        context["accessibility_needs"],
    )
    return {
        **route,
        "resident_id": resident_id,
        "origin_node_id": int(context["current_node_id"]),
        "target_node_id": int(target["id"]),
        "destination": target["name"],
        "base_speed_m_per_min": float(context["base_speed_m_per_min"]),
        "effective_speed_m_per_min": context["effective_speed_m_per_min"],
    }


def check_action_resource(conn, location, action):
    if not spatial_runtime_available(conn):
        return {"required": False, "available": True}
    node = _destination_node(_load_nodes(conn), location)
    if not node:
        return {"required": False, "available": True}
    for row in conn.execute(
        "SELECT * FROM spatial_resources WHERE node_id = ? ORDER BY id",
        (node["id"],),
    ).fetchall():
        resource = dict(row)
        properties = _json_value(resource.get("properties"), {})
        if action not in properties.get("actions", []):
            continue
        available = (
            resource["status"] == "available"
            and int(resource["available_units"]) > 0
        )
        waiting = conn.execute(
            """
            SELECT COUNT(*) AS value FROM spatial_admission_queue
            WHERE resource_id = ? AND status = 'waiting'
            """,
            (resource["id"],),
        ).fetchone()["value"]
        estimated_wait = (
            (int(waiting) + 1)
            / float(resource["service_rate_per_hour"])
            * 60.0
        )
        return {
            "required": True,
            "available": available,
            "resource_id": int(resource["id"]),
            "resource_key": resource["resource_key"],
            "resource_name": resource["name"],
            "available_units": int(resource["available_units"]),
            "queue_length": int(waiting),
            "estimated_wait_minutes": round(estimated_wait, 2),
        }
    return {"required": False, "available": True}


def start_spatial_movement(
    conn,
    resident_id,
    destination,
    world_time=None,
    replan_reason="",
    force_replan=False,
    constraint_response="auto",
):
    if not spatial_runtime_available(conn):
        return None
    now = world_time or get_world_now()
    context = _movement_context(conn, resident_id)
    nodes = _load_nodes(conn)
    curr_node = next((n for n in nodes if int(n["id"]) == int(context["current_node_id"])), None)
    curr_world = (curr_node.get("world_key") if curr_node else None) or "tsinghua_main"
    edges = _load_edges(conn)
    target, route = _reachable_destination_route(
        nodes,
        edges,
        context["current_node_id"],
        destination,
        curr_world,
        context["effective_speed_m_per_min"],
        context["accessibility_needs"],
    )
    admission = _evaluate_destination_constraint(
        conn,
        target,
        now,
        resident_id,
        requested_response=constraint_response,
    )
    if not admission["physically_possible"]:
        raise SpatialAdmissionError("physically_blocked", "目标地点物理上不可达")
    if (
        context["movement_status"] in ACTIVE_MOVEMENT_STATUSES
        and int(context["target_node_id"] or 0) == int(target["id"])
        and not force_replan
    ):
        return {
            "message": "已经在前往该地点",
            "movement_status": context["movement_status"],
            "resident_id": resident_id,
            "target_node_id": int(target["id"]),
            "destination": target["name"],
            "progress": float(context["progress"]),
        }

    if not route["edge_ids"]:
        return {
            "message": "Agent 已在目标地点",
            "movement_status": "idle",
            "resident_id": resident_id,
            "target_node_id": int(target["id"]),
            "destination": target["name"],
            "progress": 1.0,
            "route": route,
        }

    estimated_arrival = now + timedelta(minutes=route["cost_minutes"])
    replan_count = int(context.get("replan_count") or 0)
    if force_replan or context["movement_status"] in ACTIVE_MOVEMENT_STATUSES:
        replan_count += 1
    conn.execute(
        """
        UPDATE agent_spatial_states
        SET origin_node_id = current_node_id,
            target_node_id = ?,
            x = COALESCE((SELECT x FROM spatial_nodes WHERE id = current_node_id LIMIT 1), x),
            y = COALESCE((SELECT y FROM spatial_nodes WHERE id = current_node_id LIMIT 1), y),
            z = COALESCE((SELECT z FROM spatial_nodes WHERE id = current_node_id LIMIT 1), z),
            movement_status = 'moving',
            path = ?,
            path_index = 0,
            progress = 0,
            route_distance_meters = ?,
            remaining_distance_meters = ?,
            planned_at = ?,
            started_at = ?,
            last_progress_at = ?,
            estimated_arrival_at = ?,
            replan_count = ?,
            last_replan_reason = ?,
            interrupted_reason = '',
            version = version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (
            target["id"],
            json.dumps(route["node_ids"]),
            route["distance_meters"],
            route["distance_meters"],
            now.isoformat(),
            now.isoformat(),
            now.isoformat(),
            estimated_arrival.isoformat(),
            replan_count,
            replan_reason[:240],
            resident_id,
        ),
    )
    return {
        "message": "已开始移动",
        "movement_status": "moving",
        "resident_id": resident_id,
        "origin_node_id": int(context["current_node_id"]),
        "target_node_id": int(target["id"]),
        "destination": target["name"],
        "estimated_arrival_at": estimated_arrival.isoformat(),
        "base_speed_m_per_min": float(context["base_speed_m_per_min"]),
        "effective_speed_m_per_min": context["effective_speed_m_per_min"],
        "route": route,
        "constraint_evaluation": admission,
        "description": (
            f"{context['resident_name']} 开始从 {context['legacy_location']} "
            f"前往 {target['name']}，路线约 {route['distance_meters']:.0f} 米，"
            f"预计 {route['cost_minutes']:.1f} 分钟。"
        ),
    }


def pause_spatial_movement(conn, resident_id, reason="manual_pause"):
    movement = get_active_movement(conn, resident_id)
    if not movement:
        raise ValueError("Agent 当前不在移动中")
    conn.execute(
        """
        UPDATE agent_spatial_states
        SET movement_status = 'paused', interrupted_reason = ?,
            version = version + 1, updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (reason[:240], resident_id),
    )
    return {"resident_id": resident_id, "movement_status": "paused", "reason": reason}


def resume_spatial_movement(conn, resident_id, world_time=None):
    now = world_time or get_world_now()
    row = conn.execute(
        """
        SELECT movement_status, target_node_id
        FROM agent_spatial_states WHERE resident_id = ?
        """,
        (resident_id,),
    ).fetchone()
    if not row or row["movement_status"] != "paused" or not row["target_node_id"]:
        raise ValueError("Agent 没有可恢复的暂停路线")
    conn.execute(
        """
        UPDATE agent_spatial_states
        SET movement_status = 'moving', last_progress_at = ?,
            interrupted_reason = '', version = version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (now.isoformat(), resident_id),
    )
    return {"resident_id": resident_id, "movement_status": "moving"}


def _edge_lookup(edges):
    lookup = {}
    for edge in edges:
        key = (int(edge["from_node_id"]), int(edge["to_node_id"]))
        lookup[key] = edge
        if edge.get("bidirectional"):
            lookup[(key[1], key[0])] = edge
    return lookup


def _ensure_runtime_experiment(conn, branch_key):
    row = conn.execute(
        """
        SELECT id FROM experiment_runs
        WHERE branch_key = ? AND status = 'running'
        ORDER BY id DESC LIMIT 1
        """,
        (branch_key,),
    ).fetchone()
    if row:
        return int(row["id"])
    run_id = f"world-runtime-{branch_key}"
    existing = conn.execute(
        "SELECT id FROM experiment_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE experiment_runs SET status = 'running' WHERE id = ?",
            (existing["id"],),
        )
        return int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO experiment_runs
        (run_id, experiment_name, control_or_treatment, branch_key, status,
         world_rules_version, metadata_json)
        VALUES (?, 'Autonomous campus runtime', 'natural', ?, 'running',
                'world-runtime-v4-spatial', ?)
        """,
        (
            run_id,
            branch_key,
            json.dumps({"source": "continuous_spatial_movement"}),
        ),
    )
    return int(cursor.lastrowid)


def _record_trajectory(
    conn,
    experiment_run_id,
    branch_key,
    tick_number,
    state,
    movement_status,
    metadata,
):
    conn.execute(
        """
        INSERT INTO agent_trajectories
        (experiment_run_id, branch_key, tick_number, resident_id, node_id,
         x, y, z, movement_status, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_run_id,
            branch_key,
            tick_number,
            state["resident_id"],
            state["current_node_id"],
            state["x"],
            state["y"],
            state["z"],
            movement_status,
            json.dumps(metadata, default=str),
        ),
    )


def advance_active_movements(conn, world_time, tick_number):
    if not spatial_runtime_available(conn):
        return []
    rows = conn.execute(
        """
        SELECT s.*, c.base_speed_m_per_min, c.accessibility_needs,
               body.hunger, body.fatigue, body.health,
               r.name AS resident_name
        FROM agent_spatial_states s
        JOIN agent_spatial_capabilities c ON c.resident_id = s.resident_id
        JOIN residents r ON r.id = s.resident_id
        LEFT JOIN agent_body_states body ON body.resident_id = s.resident_id
        WHERE s.movement_status IN ('moving', 'replanning', 'waiting')
        ORDER BY s.resident_id
        """
    ).fetchall()
    if not rows:
        return []
    nodes = _load_nodes(conn)
    node_by_id = {int(node["id"]): node for node in nodes}
    edges = _load_edges(conn)
    edge_by_nodes = _edge_lookup(edges)
    branch_row = conn.execute(
        "SELECT active_branch_key FROM world_runtime WHERE id = 1"
    ).fetchone()
    branch_key = branch_row["active_branch_key"] if branch_row else "main"
    experiment_run_id = _ensure_runtime_experiment(conn, branch_key or "main")
    results = []

    for raw_row in rows:
        state = dict(raw_row)
        effective_speed = effective_movement_speed(
            state["base_speed_m_per_min"],
            state,
        )
        path = _json_value(state.get("path"), [])
        last_progress = _iso_datetime(state.get("last_progress_at")) or world_time
        elapsed_minutes = max(
            0.0,
            (world_time - last_progress).total_seconds() / 60.0,
        )
        index = int(state.get("path_index") or 0)
        time_left = elapsed_minutes
        traveled = 0.0
        replan_reason = ""
        admission_denied = None
        boundary_attempt = None
        abandoned = None

        while time_left > 0 and index < len(path) - 1:
            from_id = int(path[index])
            to_id = int(path[index + 1])
            edge = edge_by_nodes.get((from_id, to_id))
            if not edge or edge.get("status") != "open":
                replan_reason = "route_edge_closed_or_missing"
                break
            target_node = node_by_id[to_id]
            if to_id == int(state["target_node_id"]):
                admission = _evaluate_destination_constraint(
                    conn,
                    target_node,
                    world_time,
                    state["resident_id"],
                    requested_response=latest_explicit_constraint_response(
                        conn,
                        state["resident_id"],
                        target_node["id"],
                    ),
                )
                if not admission["allowed"]:
                    if constraint_runtime_available(conn):
                        boundary_attempt = resolve_boundary_attempt(
                            conn, admission, world_time
                        )
                        if not boundary_attempt["admitted"]:
                            admission_denied = {
                                **admission,
                                "code": (
                                    "boundary_bypass_failed"
                                    if admission["selected_response"] == "bypass"
                                    else admission["code"]
                                ),
                                "attempt": boundary_attempt,
                            }
                            break
                    else:
                        admission_denied = admission
                        break
            remaining_segment = dist(
                (float(state["x"]), float(state["y"]), float(state["z"])),
                (
                    float(target_node["x"]),
                    float(target_node["y"]),
                    float(target_node["z"]),
                ),
            )
            edge_minutes = edge_travel_minutes(
                {**edge, "distance_meters": remaining_segment},
                effective_speed,
            )
            if edge_minutes <= time_left + 1e-9:
                state["x"] = float(target_node["x"])
                state["y"] = float(target_node["y"])
                state["z"] = float(target_node["z"])
                state["current_node_id"] = to_id
                index += 1
                time_left -= edge_minutes
                traveled += remaining_segment
                continue
            ratio = time_left / edge_minutes if edge_minutes else 1.0
            state["x"] += (float(target_node["x"]) - float(state["x"])) * ratio
            state["y"] += (float(target_node["y"]) - float(state["y"])) * ratio
            state["z"] += (float(target_node["z"]) - float(state["z"])) * ratio
            traveled += remaining_segment * ratio
            time_left = 0

        if replan_reason:
            result = start_spatial_movement(
                conn,
                state["resident_id"],
                node_by_id[int(state["target_node_id"])]["code"],
                world_time=world_time,
                replan_reason=replan_reason,
                force_replan=True,
            )
            result["event_type"] = "spatial_route_replanned"
            results.append(result)
            continue

        if admission_denied:
            queue = _ensure_admission_queue(
                conn,
                state,
                admission_denied,
                world_time,
                tick_number,
                branch_key or "main",
            )
            expired, waited_minutes = _waiting_has_expired(queue, world_time)
            if expired:
                abandoned = {
                    "queue_position": int(queue["queue_position"]),
                    "waited_minutes": round(waited_minutes, 2),
                    "patience_minutes": float(queue["patience_minutes"]),
                    "suggested_alternatives": _suggested_alternatives(
                        admission_denied["location"]
                    ),
                }
                conn.execute(
                    "DELETE FROM spatial_admission_queue WHERE resident_id = ?",
                    (state["resident_id"],),
                )
                conn.execute(
                    """
                    UPDATE agent_spatial_states
                    SET movement_status = 'interrupted', target_node_id = NULL,
                        path = '[]', path_index = 0, progress = 0,
                        route_distance_meters = 0, remaining_distance_meters = 0,
                        interrupted_reason = 'admission_patience_exhausted',
                        last_progress_at = ?, updated_tick = ?,
                        version = version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE resident_id = ?
                    """,
                    (
                        world_time.isoformat(),
                        tick_number,
                        state["resident_id"],
                    ),
                )
                results.append(
                    {
                        "resident_id": int(state["resident_id"]),
                        "resident_name": state["resident_name"],
                        "movement_status": "interrupted",
                        "event_type": "spatial_admission_abandoned",
                        "current_node_id": int(state["current_node_id"]),
                        "target_node_id": int(state["target_node_id"]),
                        "progress": float(state.get("progress") or 0),
                        "remaining_distance_meters": 0.0,
                        "distance_traveled_meters": round(traveled, 3),
                        "x": state["x"],
                        "y": state["y"],
                        "z": state["z"],
                        "admission": admission_denied,
                        "queue": abandoned,
                    }
                )
                continue
            admission_denied = {**admission_denied, "queue": queue}
        else:
            conn.execute(
                "DELETE FROM spatial_admission_queue WHERE resident_id = ?",
                (state["resident_id"],),
            )

        remaining = max(
            0.0,
            float(state.get("remaining_distance_meters") or 0) - traveled,
        )
        route_distance = float(state.get("route_distance_meters") or 0)
        progress = (
            min(1.0, max(0.0, 1.0 - remaining / route_distance))
            if route_distance
            else 1.0
        )
        arrived = index >= len(path) - 1
        movement_status = (
            "waiting"
            if admission_denied
            else ("arrived" if arrived else "moving")
        )
        target_node = node_by_id.get(int(state["target_node_id"] or 0))
        conn.execute(
            """
            UPDATE agent_spatial_states
            SET current_node_id = ?, x = ?, y = ?, z = ?, path_index = ?,
                progress = ?, remaining_distance_meters = ?,
                movement_status = ?, last_progress_at = ?, updated_tick = ?,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ?
            """,
            (
                state["current_node_id"],
                state["x"],
                state["y"],
                state["z"],
                index,
                progress,
                remaining,
                movement_status,
                world_time.isoformat(),
                tick_number,
                state["resident_id"],
            ),
        )
        if arrived and target_node:
            location = (
                target_node.get("properties", {}).get("location")
                or target_node["name"]
            )
            conn.execute(
                "UPDATE residents SET location = ? WHERE id = ?",
                (location, state["resident_id"]),
            )
        state.update(
            {
                "path_index": index,
                "progress": progress,
                "remaining_distance_meters": remaining,
            }
        )
        _record_trajectory(
            conn,
            experiment_run_id,
            branch_key or "main",
            tick_number,
            state,
            movement_status,
            {
                "elapsed_minutes": round(elapsed_minutes, 4),
                "distance_traveled_meters": round(traveled, 3),
                "base_speed_m_per_min": float(state["base_speed_m_per_min"]),
                "effective_speed_m_per_min": effective_speed,
                "target_node_id": state["target_node_id"],
                "route_distance_meters": route_distance,
                "admission": admission_denied or {"allowed": True},
                "boundary_attempt": boundary_attempt,
            },
        )
        results.append(
            {
                "resident_id": int(state["resident_id"]),
                "resident_name": state["resident_name"],
                "movement_status": movement_status,
                "event_type": (
                    "spatial_admission_waiting"
                    if admission_denied
                    else (
                        "spatial_boundary_bypassed"
                        if arrived
                        and boundary_attempt
                        and boundary_attempt["strategy"] == "bypass"
                        else (
                            "spatial_agent_arrived"
                            if arrived
                            else "spatial_agent_movement_progress"
                        )
                    )
                ),
                "current_node_id": int(state["current_node_id"]),
                "target_node_id": int(state["target_node_id"]),
                "progress": round(progress, 4),
                "remaining_distance_meters": round(remaining, 3),
                "distance_traveled_meters": round(traveled, 3),
                "base_speed_m_per_min": float(state["base_speed_m_per_min"]),
                "effective_speed_m_per_min": effective_speed,
                "x": state["x"],
                "y": state["y"],
                "z": state["z"],
                "admission": admission_denied or {"allowed": True},
                "boundary_attempt": boundary_attempt,
            }
        )
    return results
