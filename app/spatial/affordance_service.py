"""Spatial Affordance Discovery & Management Service for Phase 3.6A."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional


DEFAULT_AFFORDANCE_SEED_DATA = [
    # 图书馆 (Library)
    {
        "location_match": ["图书馆", "library", "六教", "教学楼"],
        "affordances": [
            {
                "affordance_key": "use_facility",
                "name": "图书馆学习",
                "requirements": {"min_energy": 10, "location_open": True, "money": 0},
                "effects": {"attention": 15, "study_progress": 10, "fatigue": 8},
                "capacity": 50,
            },
            {
                "affordance_key": "rest",
                "name": "阅览室修整",
                "requirements": {"location_open": True},
                "effects": {"fatigue": -12, "energy": 10},
                "capacity": 30,
            },
            {
                "affordance_key": "observe",
                "name": "观察学术氛围",
                "requirements": {},
                "effects": {"attention": 5},
                "capacity": 100,
            },
        ],
    },
    # 食堂 (Canteen)
    {
        # Imported OSM names vary: a canteen may be named after the building
        # (for example 清晏楼(食堂/报告厅)) instead of the legacy “食堂” space.
        "location_match": ["食堂", "餐厅", "餐饮", "canteen", "清晏楼", "紫荆园", "桃李园", "观畴园", "芝兰园", "玉树园"],
        "affordances": [
            {
                "affordance_key": "consume",
                "name": "食堂用餐",
                "requirements": {"money": 15, "location_open": True},
                "effects": {"hunger": -45, "energy": 30},
                "capacity": 80,
            },
            {
                "affordance_key": "hydrate",
                "name": "饮水补给",
                "requirements": {"location_open": True},
                "effects": {"hydration": -42, "attention": 3},
                "capacity": 120,
            },
            {
                "affordance_key": "rest",
                "name": "餐后休憩",
                "requirements": {"location_open": True},
                "effects": {"fatigue": -8, "energy": 5},
                "capacity": 40,
            },
            {
                "affordance_key": "observe",
                "name": "观察就餐人流",
                "requirements": {},
                "effects": {},
                "capacity": 150,
            },
        ],
    },
    # 宿舍区 (Dormitory)
    {
        "location_match": ["宿舍", "dorm", "宿舍区", "紫荆公寓", "南区公寓"],
        "affordances": [
            {
                "affordance_key": "rest",
                "name": "宿舍深度休息",
                "requirements": {},
                "effects": {"fatigue": -35, "energy": 40, "sleep_debt": -30},
                "capacity": 10,
            },
            {
                "affordance_key": "hydrate",
                "name": "宿舍饮水",
                "requirements": {},
                "effects": {"hydration": -38},
                "capacity": 100,
            },
            {
                "affordance_key": "socialize",
                "name": "室友与邻居交流",
                "requirements": {"min_social_energy": 10},
                "effects": {"social_energy": 25, "stress": -15},
                "capacity": 8,
            },
            {
                "affordance_key": "observe",
                "name": "观察宿舍周边环境",
                "requirements": {},
                "effects": {},
                "capacity": 50,
            },
        ],
    },
]


def seed_spatial_affordances(conn) -> int:
    """Ensure at least the 3 core spatial node categories have configured affordances."""
    nodes = conn.execute("SELECT id, world_key, name, node_type FROM spatial_nodes").fetchall()
    if not nodes:
        return 0

    inserted_count = 0
    for node in nodes:
        node_id = int(node["id"])
        node_name = str(node["name"])
        world_key = str(node["world_key"])

        for seed in DEFAULT_AFFORDANCE_SEED_DATA:
            if any(match in node_name for match in seed["location_match"]):
                for aff in seed["affordances"]:
                    aff_key = aff["affordance_key"]
                    existing = conn.execute(
                        "SELECT id FROM spatial_affordances WHERE node_id = ? AND affordance_key = ?",
                        (node_id, aff_key),
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            """
                            INSERT INTO spatial_affordances
                            (world_key, node_id, affordance_key, name, requirements, effects, capacity, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                            """,
                            (
                                world_key,
                                node_id,
                                aff_key,
                                aff["name"],
                                json.dumps(aff["requirements"], ensure_ascii=False),
                                json.dumps(aff["effects"], ensure_ascii=False),
                                aff.get("capacity", 50),
                            ),
                        )
                        inserted_count += 1
    return inserted_count


def get_spatial_affordances(conn, world_key: Optional[str] = None, node_id: Optional[int] = None) -> List[Dict[str, Any]]:
    seed_spatial_affordances(conn)
    query = """
        SELECT a.id, a.world_key, a.node_id, n.name AS node_name, a.affordance_key,
               a.name, a.requirements, a.effects, a.capacity, a.status
        FROM spatial_affordances a
        JOIN spatial_nodes n ON n.id = a.node_id
        WHERE 1=1
    """
    params = []
    if world_key:
        query += " AND a.world_key = ?"
        params.append(world_key)
    if node_id:
        query += " AND a.node_id = ?"
        params.append(node_id)
    query += " ORDER BY a.node_id, a.id"

    rows = conn.execute(query, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("requirements"), str):
            item["requirements"] = json.loads(item["requirements"])
        if isinstance(item.get("effects"), str):
            item["effects"] = json.loads(item["effects"])
        result.append(item)
    return result


def discover_agent_affordance_opportunities(conn, resident_id: int) -> Dict[str, Any]:
    """Dynamically discover available affordance opportunities for an agent based on location, perception radius, body state, money, and capacity."""
    seed_spatial_affordances(conn)

    agent_state_row = conn.execute(
        "SELECT current_node_id, x, y, z FROM agent_spatial_states WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()

    cap_row = conn.execute(
        "SELECT perception_radius_m FROM agent_spatial_capabilities WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    perception_radius_m = float(cap_row["perception_radius_m"]) if cap_row and cap_row["perception_radius_m"] is not None else 100.0

    if not agent_state_row:
        curr_node_id = 1
        curr_x, curr_y, curr_z = 0.0, 0.0, 0.0
    else:
        curr_node_id = int(agent_state_row["current_node_id"])
        curr_x = float(agent_state_row["x"]) if agent_state_row["x"] is not None else 0.0
        curr_y = float(agent_state_row["y"]) if agent_state_row["y"] is not None else 0.0
        curr_z = float(agent_state_row["z"]) if agent_state_row["z"] is not None else 0.0

    curr_node = conn.execute("SELECT id, name, world_key, x, y, z FROM spatial_nodes WHERE id = ?", (curr_node_id,)).fetchone()
    world_key = curr_node["world_key"] if curr_node else "default"
    if curr_node and (curr_x == 0.0 and curr_y == 0.0 and curr_z == 0.0):
        curr_x = float(curr_node["x"]) if curr_node["x"] is not None else 0.0
        curr_y = float(curr_node["y"]) if curr_node["y"] is not None else 0.0
        curr_z = float(curr_node["z"]) if curr_node["z"] is not None else 0.0

    res_row = conn.execute("SELECT money FROM residents WHERE id = ?", (resident_id,)).fetchone()
    money = int(res_row["money"]) if res_row and res_row["money"] is not None else 100

    prof_row = conn.execute("SELECT energy FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    energy = int(prof_row["energy"]) if prof_row and prof_row["energy"] is not None else 50

    body_row = conn.execute(
        "SELECT hunger, fatigue, sleep_debt, attention, social_energy FROM agent_body_states WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    body_state = dict(body_row) if body_row else {"hunger": 50, "fatigue": 30, "sleep_debt": 20, "social_energy": 50}

    all_nodes = {int(n["id"]): dict(n) for n in conn.execute(
        "SELECT id, name, x, y, z, capacity, status FROM spatial_nodes WHERE world_key = ?",
        (world_key,),
    ).fetchall()}

    from app.spatial.planner import plan_route, RouteNotFoundError
    all_spatial_nodes_list = []
    for r in conn.execute("SELECT * FROM spatial_nodes WHERE world_key = ?", (world_key,)).fetchall():
        item = dict(r)
        if isinstance(item.get("properties"), str):
            try:
                item["properties"] = json.loads(item["properties"])
            except Exception:
                item["properties"] = {}
        all_spatial_nodes_list.append(item)

    all_spatial_edges_list = []
    for r in conn.execute("SELECT * FROM spatial_edges WHERE status = 'open'").fetchall():
        item = dict(r)
        if isinstance(item.get("properties"), str):
            try:
                item["properties"] = json.loads(item["properties"])
            except Exception:
                item["properties"] = {}
        all_spatial_edges_list.append(item)

    affordances = get_spatial_affordances(conn, world_key=world_key)
    opportunities = []

    for aff in affordances:
        reqs = aff["requirements"]
        is_reachable = True
        reasons = []

        target_node = all_nodes.get(int(aff["node_id"]))
        if target_node:
            t_x = float(target_node["x"]) if target_node["x"] is not None else 0.0
            t_y = float(target_node["y"]) if target_node["y"] is not None else 0.0
            t_z = float(target_node["z"]) if target_node["z"] is not None else 0.0
            dist_m = math.dist((curr_x, curr_y, curr_z), (t_x, t_y, t_z))
        else:
            dist_m = 0.0

        # At acute hunger, food is an essential, goal-directed search: the
        # Agent may deliberately seek a known campus dining facility beyond
        # its passive perception radius.  It still must pass the same road
        # topology check; this is not a teleport or a knowledge injection.
        essential_food_search = (
            aff["affordance_key"] == "consume"
            and float(body_state.get("hunger") or 0) >= 75
        )
        if aff["node_id"] != curr_node_id:
            if dist_m > perception_radius_m and not essential_food_search:
                is_reachable = False
                reasons.append(f"超出感知/可达范围（{dist_m:.1f}m > {perception_radius_m:.1f}m）")
            if is_reachable:
                # Validate topological path reachability via road graph
                try:
                    plan_route(
                        all_spatial_nodes_list,
                        all_spatial_edges_list,
                        start_node_id=curr_node_id,
                        target_node_id=aff["node_id"],
                        speed_m_per_min=80.0,
                    )
                except RouteNotFoundError:
                    is_reachable = False
                    reasons.append("无连通路径（拓扑不可达）")

        if target_node:
            cap = int(target_node.get("capacity") or aff.get("capacity") or 50)
            status = str(target_node.get("status") or "open")
            if status not in ("open", "开放"):
                is_reachable = False
                reasons.append(f"{target_node['name']}当前已关闭")

            occupancy_row = conn.execute(
                "SELECT COUNT(*) AS count FROM agent_spatial_states WHERE current_node_id = ? AND resident_id != ?",
                (aff["node_id"], resident_id),
            ).fetchone()
            occupancy = int(occupancy_row["count"]) if occupancy_row else 0
            if cap > 0 and occupancy >= cap:
                is_reachable = False
                reasons.append(f"{target_node['name']}当前满员（{occupancy}/{cap}，需排队）")

        if reqs.get("money", 0) > money:
            is_reachable = False
            reasons.append(f"资金不足（需 {reqs['money']} 元，当前 {money} 元）")
        if reqs.get("min_energy", 0) > energy:
            is_reachable = False
            reasons.append(f"体能不足（需 {reqs['min_energy']}，当前 {energy}）")
        if reqs.get("min_social_energy", 0) > body_state.get("social_energy", 0):
            is_reachable = False
            reasons.append(f"社交能量不足（需 {reqs['min_social_energy']}）")
        if aff["status"] != "open":
            is_reachable = False
            reasons.append("该可供性服务当前暂停")

        opportunities.append({
            "affordance_id": aff["id"],
            "node_id": aff["node_id"],
            "node_name": aff["node_name"],
            "affordance_key": aff["affordance_key"],
            "name": aff["name"],
            "distance_meters": round(dist_m, 1),
            "is_current_node": (aff["node_id"] == curr_node_id),
            "is_available": is_reachable,
            "reasons": reasons,
            "effects": aff["effects"],
            "requirements": aff["requirements"],
            "essential_food_search": essential_food_search,
        })

    return {
        "resident_id": resident_id,
        "current_node_id": curr_node_id,
        "current_node_name": curr_node["name"] if curr_node else "未知",
        "perception_radius_m": perception_radius_m,
        "opportunities": opportunities,
    }
