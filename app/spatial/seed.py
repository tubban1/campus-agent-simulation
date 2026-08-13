from __future__ import annotations

from typing import Optional

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection

from app.capability_runtime import (
    seed_capability_foundation,
    spatial_capability_values,
)
from app.spatial.models import (
    agent_spatial_capabilities,
    agent_spatial_states,
    agent_body_states,
    spatial_resources,
    spatial_edges,
    spatial_nodes,
)
from app.spatial.topology import EDGE_SEEDS, NODE_SEEDS, build_edge_seed
from app.spatial.facility_service import ensure_facility_states
from app.spatial.affordance_service import seed_spatial_affordances


RESOURCE_SEEDS = [
    ("dorm", "beds", "宿舍床位", 500, 1000.0, ["rest"]),
    ("teaching", "classroom_seats", "教室席位", 350, 400.0, ["attend_class"]),
    ("library", "study_seats", "图书馆座位", 160, 80.0, ["observe", "collaborate"]),
    ("canteen", "service_windows", "食堂服务窗口", 12, 180.0, ["queue", "consume"]),
    ("canteen", "meal_stock", "食堂餐食库存", 500, 180.0, ["consume"]),
    ("dorm", "water_stock", "宿舍饮水库存", 300, 120.0, ["hydrate"]),
    ("playground", "activity_slots", "活动场地名额", 200, 300.0, ["club_activity"]),
    ("business", "service_counters", "商业服务点", 20, 120.0, ["queue", "consume"]),
    ("admin", "admin_windows", "校务服务窗口", 6, 30.0, ["request_leave"]),
]


def _real_world_node_for_resident(nodes: list[dict], resident: dict) -> dict:
    role = str(resident["role"] or "")
    preferred = (
        ("食堂", "餐厅", "清晏", "清芬") if any(key in role for key in ("商", "餐"))
        else ("教学", "图书", "实验") if any(key in role for key in ("教师", "辅导", "管理"))
        else ("宿舍", "公寓", "图书", "教学", "食堂")
    )
    for token in preferred:
        matches = [node for node in nodes if token in str(node["name"])]
        if matches:
            # Spread similar roles across real POI instead of attaching every
            # student or merchant to the first name-matching building.
            return matches[(int(resident["id"]) - 1) % len(matches)]
    return nodes[int(resident["id"]) % len(nodes)]


def _seed_real_world_agent_states(connection: Connection, world_key: str) -> dict:
    """Attach seeded residents to imported geography without synthetic nodes."""
    nodes = [
        dict(row)
        for row in connection.execute(
            select(spatial_nodes).where(
                spatial_nodes.c.world_key == world_key,
                spatial_nodes.c.node_type.in_(("building", "poi", "outdoor_area")),
            )
        ).mappings()
    ]
    if not nodes:
        raise RuntimeError(f"No real spatial nodes imported for world_key={world_key}")
    residents = list(connection.exec_driver_sql("SELECT id, role FROM residents ORDER BY id").mappings())
    capability_seed = seed_capability_foundation(connection)
    existing_capabilities = set(connection.execute(select(agent_spatial_capabilities.c.resident_id)).scalars())
    existing_states = set(connection.execute(select(agent_spatial_states.c.resident_id)).scalars())
    existing_body_states = set(connection.execute(select(agent_body_states.c.resident_id)).scalars())
    profile_energy = {
        int(row.resident_id): int(row.energy)
        for row in connection.exec_driver_sql("SELECT resident_id, energy FROM agent_profiles")
    }
    states_created = capabilities_created = body_states_created = 0
    for resident in residents:
        resident_id = int(resident["id"])
        if resident_id not in existing_capabilities:
            values = spatial_capability_values(capability_seed["profiles"][resident_id])
            connection.execute(insert(agent_spatial_capabilities).values(
                resident_id=resident_id, **values, mobility_class="standard",
                accessibility_needs={}, source="derived-capability-v1", version=1,
            ))
            capabilities_created += 1
        if resident_id not in existing_states:
            node = _real_world_node_for_resident(nodes, resident)
            connection.execute(insert(agent_spatial_states).values(
                resident_id=resident_id, current_node_id=node["id"], target_node_id=None,
                x=node["x"], y=node["y"], z=node["z"], facing_x=0.0, facing_z=1.0,
                movement_status="idle", path=[], path_index=0, progress=0.0,
                updated_tick=0, version=1, branch_key="main",
            ))
            # Profile cards and non-spatial views must show the actual imported
            # POI, never the old synthetic labels used by the resident fixture.
            connection.exec_driver_sql(
                "UPDATE residents SET location = ? WHERE id = ?",
                (str(node["name"]), resident_id),
            )
            states_created += 1
        if resident_id not in existing_body_states:
            energy = profile_energy.get(resident_id, 80)
            connection.execute(insert(agent_body_states).values(
                resident_id=resident_id, hunger=float(20 + resident_id % 16),
                fatigue=float(max(0, 100 - energy)), sleep_debt=float(8 + resident_id % 12),
                stress=float(22 + resident_id % 18), attention=float(max(35, energy)),
                social_energy=float(50 + resident_id % 31), health=float(88 + resident_id % 10),
                weather_exposure=0.0, hydration=float(18 + resident_id % 14),
                nutrition=float(72 + resident_id % 18), activity_load=float(10 + resident_id % 16),
                illness_load=0.0, last_updated_at=None, last_updated_tick=0,
                source="seeded", version=1,
            ))
            body_states_created += 1
    seed_spatial_affordances(connection)
    return {
        "nodes_created": 0, "edges_created": 0, "resources_created": 0,
        "capabilities_created": capabilities_created, "states_created": states_created,
        "body_states_created": body_states_created,
        "capability_profiles_created": capability_seed["profiles_created"],
        "opportunities_created": capability_seed["opportunities_created"],
        "spatial_capabilities_updated": capability_seed["capabilities_updated"],
        "nodes_total": len(nodes),
        "edges_total": int(connection.execute(select(func.count()).select_from(spatial_edges)).scalar_one()),
        "resources_total": int(connection.execute(select(func.count()).select_from(spatial_resources)).scalar_one()),
        "residents_total": len(residents),
    }


def seed_spatial_foundation(connection: Connection, *, real_world_key: Optional[str] = None) -> dict:
    if real_world_key:
        return _seed_real_world_agent_states(connection, real_world_key)
    capacities = {
        row.location: int(row.capacity)
        for row in connection.exec_driver_sql(
            "SELECT location, capacity FROM campus_spaces"
        )
    }
    existing_nodes = {
        row.code: dict(row)
        for row in connection.execute(select(spatial_nodes)).mappings()
    }
    nodes_created = 0
    for seed in NODE_SEEDS:
        if seed["code"] in existing_nodes:
            continue
        parent = existing_nodes.get(seed["parent_code"])
        location = seed.get("location") or ""
        properties = {
            "coordinate_unit": "meters",
            "location": location,
            "seed_version": "campus-topology-v1",
        }
        if location:
            properties["campus_space_code"] = seed["code"]
        values = {
            "code": seed["code"],
            "name": seed["name"],
            "node_type": seed["node_type"],
            "parent_id": parent["id"] if parent else None,
            "x": seed["x"],
            "y": seed["y"],
            "z": seed["z"],
            "radius": seed["radius"],
            "capacity": capacities.get(location, seed["capacity"]),
            "status": "open",
            "properties": properties,
        }
        cursor = connection.execute(insert(spatial_nodes).values(**values))
        existing_nodes[seed["code"]] = {"id": cursor.inserted_primary_key[0], **values}
        nodes_created += 1

    existing_edges = {
        (int(row.from_node_id), int(row.to_node_id))
        for row in connection.execute(
            select(spatial_edges.c.from_node_id, spatial_edges.c.to_node_id)
        )
    }
    edges_created = 0
    for from_code, to_code, path_type in EDGE_SEEDS:
        from_node = existing_nodes[from_code]
        to_node = existing_nodes[to_code]
        edge_key = (int(from_node["id"]), int(to_node["id"]))
        if edge_key in existing_edges:
            continue
        values = {
            "from_node_id": edge_key[0],
            "to_node_id": edge_key[1],
            **build_edge_seed(from_node, to_node, path_type),
        }
        connection.execute(insert(spatial_edges).values(**values))
        existing_edges.add(edge_key)
        edges_created += 1

    existing_resources = {
        (int(row.node_id), row.resource_key)
        for row in connection.execute(
            select(spatial_resources.c.node_id, spatial_resources.c.resource_key)
        )
    }
    resources_created = 0
    for node_code, resource_key, name, capacity, service_rate, actions in RESOURCE_SEEDS:
        node_id = int(existing_nodes[node_code]["id"])
        key = (node_id, resource_key)
        if key in existing_resources:
            continue
        connection.execute(
            insert(spatial_resources).values(
                node_id=node_id,
                resource_key=resource_key,
                name=name,
                capacity=capacity,
                available_units=capacity,
                service_rate_per_hour=service_rate,
                status="available",
                properties={"actions": actions, "seed_version": "spatial-resources-v1"},
            )
        )
        existing_resources.add(key)
        resources_created += 1

    active_branch = connection.exec_driver_sql(
        "SELECT active_branch_key FROM world_runtime WHERE id = 1"
    ).first()
    branch_key = active_branch[0] if active_branch and active_branch[0] else "main"
    residents = list(
        connection.exec_driver_sql(
            "SELECT id, location FROM residents ORDER BY id"
        ).mappings()
    )
    capability_seed = seed_capability_foundation(connection)
    location_nodes = {
        node["properties"].get("location"): node
        for node in existing_nodes.values()
        if node.get("properties", {}).get("location")
    }
    fallback_node = existing_nodes["dorm"]
    existing_capabilities = set(
        connection.execute(
            select(agent_spatial_capabilities.c.resident_id)
        ).scalars()
    )
    existing_states = set(
        connection.execute(select(agent_spatial_states.c.resident_id)).scalars()
    )
    existing_body_states = set(
        connection.execute(select(agent_body_states.c.resident_id)).scalars()
    )
    profile_energy = {
        int(row.resident_id): int(row.energy)
        for row in connection.exec_driver_sql(
            "SELECT resident_id, energy FROM agent_profiles"
        )
    }
    capabilities_created = 0
    states_created = 0
    body_states_created = 0
    for resident in residents:
        resident_id = int(resident["id"])
        if resident_id not in existing_capabilities:
            spatial_values = spatial_capability_values(
                capability_seed["profiles"][resident_id]
            )
            connection.execute(
                insert(agent_spatial_capabilities).values(
                    resident_id=resident_id,
                    base_speed_m_per_min=spatial_values["base_speed_m_per_min"],
                    mobility_class="standard",
                    accessibility_needs={},
                    perception_radius_m=spatial_values["perception_radius_m"],
                    hearing_radius_m=spatial_values["hearing_radius_m"],
                    source="derived-capability-v1",
                    version=1,
                )
            )
            capabilities_created += 1
        if resident_id not in existing_states:
            node = location_nodes.get(resident["location"], fallback_node)
            connection.execute(
                insert(agent_spatial_states).values(
                    resident_id=resident_id,
                    current_node_id=node["id"],
                    target_node_id=None,
                    x=node["x"],
                    y=node["y"],
                    z=node["z"],
                    facing_x=0.0,
                    facing_z=1.0,
                    movement_status="idle",
                    path=[],
                    path_index=0,
                    progress=0.0,
                    updated_tick=0,
                    version=1,
                    branch_key=branch_key,
                )
            )
            states_created += 1
        if resident_id not in existing_body_states:
            energy = profile_energy.get(resident_id, 80)
            connection.execute(
                insert(agent_body_states).values(
                    resident_id=resident_id,
                    hunger=float(20 + resident_id % 16),
                    fatigue=float(max(0, 100 - energy)),
                    sleep_debt=float(8 + resident_id % 12),
                    stress=float(22 + resident_id % 18),
                    attention=float(max(35, energy)),
                    social_energy=float(50 + resident_id % 31),
                    health=float(88 + resident_id % 10),
                    weather_exposure=0.0,
                    hydration=float(18 + resident_id % 14),
                    nutrition=float(72 + resident_id % 18),
                    activity_load=float(10 + resident_id % 16),
                    illness_load=0.0,
                    last_updated_at=None,
                    last_updated_tick=0,
                    source="seeded",
                    version=1,
                )
            )
            body_states_created += 1

    ensure_facility_states(connection)
    seed_spatial_affordances(connection)

    return {
        "nodes_created": nodes_created,
        "edges_created": edges_created,
        "resources_created": resources_created,
        "capabilities_created": capabilities_created,
        "states_created": states_created,
        "body_states_created": body_states_created,
        "capability_profiles_created": capability_seed["profiles_created"],
        "opportunities_created": capability_seed["opportunities_created"],
        "spatial_capabilities_updated": capability_seed["capabilities_updated"],
        "nodes_total": len(existing_nodes),
        "edges_total": len(existing_edges),
        "resources_total": len(existing_resources),
        "residents_total": len(residents),
    }
