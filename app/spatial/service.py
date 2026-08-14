from __future__ import annotations

import hashlib
import json
from typing import Optional

from app.spatial.repository import SpatialRepository
from app.spatial.physical_state_service import (
    list_spatial_edge_physical_states,
    list_spatial_physical_states,
)


class SpatialStateNotInitializedError(LookupError):
    pass


class ResidentNotFoundError(LookupError):
    pass


class SpatialService:
    def __init__(self, repository: SpatialRepository):
        self.repository = repository

    def get_scene_graph(
        self,
        world_key: Optional[str] = None,
        min_x: Optional[float] = None,
        min_z: Optional[float] = None,
        max_x: Optional[float] = None,
        max_z: Optional[float] = None,
    ):
        nodes = [
            dict(row)
            for row in self.repository.list_nodes(
                world_key=world_key,
                min_x=min_x,
                min_z=min_z,
                max_x=max_x,
                max_z=max_z,
            )
        ]
        for n in nodes:
            if isinstance(n.get("properties"), str):
                try:
                    n["properties"] = json.loads(n["properties"])
                except Exception:
                    n["properties"] = {}
        node_ids = {n["id"] for n in nodes}
        edges = [dict(row) for row in self.repository.list_edges(node_ids=node_ids)]
        for e in edges:
            if isinstance(e.get("properties"), str):
                try:
                    e["properties"] = json.loads(e["properties"])
                except Exception:
                    e["properties"] = {}

        effective_world_key = world_key
        if not effective_world_key and nodes:
            effective_world_key = (nodes[0].get("properties") or {}).get("world_key") or nodes[0].get("world_key") or (
                nodes[0]["code"].split("_")[0] if "_" in nodes[0]["code"] else "default"
            )
        if not effective_world_key:
            effective_world_key = "default"

        bounds = None
        if nodes:
            xs = [n["x"] for n in nodes if n.get("x") is not None]
            zs = [n["z"] for n in nodes if n.get("z") is not None]
            if xs and zs:
                min_x, max_x = min(xs), max(xs)
                min_z, max_z = min(zs), max(zs)
                bounds = {
                    "min_x": round(min_x, 2),
                    "max_x": round(max_x, 2),
                    "min_z": round(min_z, 2),
                    "max_z": round(max_z, 2),
                    "center_x": round((min_x + max_x) / 2.0, 2),
                    "center_z": round((min_z + max_z) / 2.0, 2),
                    "span_x": round(max_x - min_x, 2),
                    "span_z": round(max_z - min_z, 2),
                }

        wgs84_bounds = None
        lons = [n["longitude"] for n in nodes if n.get("longitude") is not None]
        lats = [n["latitude"] for n in nodes if n.get("latitude") is not None]
        if lons and lats:
            wgs84_bounds = [
                round(min(lons), 7),
                round(min(lats), 7),
                round(max(lons), 7),
                round(max(lats), 7),
            ]

        has_viewport = all(v is not None for v in (min_x, min_z, max_x, max_z))
        if has_viewport:
            checksum = f"vp-{len(nodes)}-{len(edges)}-{nodes[0]['id'] if nodes else 0}-{edges[0]['id'] if edges else 0}"
        else:
            checksum_source = {
                "nodes": [
                    {
                        key: node[key]
                        for key in (
                            "id",
                            "code",
                            "parent_id",
                            "x",
                            "y",
                            "z",
                            "capacity",
                            "status",
                        )
                    }
                    for node in nodes
                ],
                "edges": [
                    {
                        key: edge[key]
                        for key in (
                            "id",
                            "from_node_id",
                            "to_node_id",
                            "distance_meters",
                            "status",
                            "congestion_factor",
                            "weather_factor",
                        )
                    }
                    for edge in edges
                ],
            }
            checksum = hashlib.sha256(
                json.dumps(checksum_source, sort_keys=True).encode("utf-8")
            ).hexdigest()
        physical_states = list_spatial_physical_states(
            self.repository.connection,
            world_key=effective_world_key,
            node_ids=node_ids,
        )
        return {
            "coordinate_system": "right-handed-meters",
            "schema_version": 1,
            "topology_version": checksum[:16],
            "world_key": effective_world_key,
            "bounds": bounds,
            "wgs84_bounds": wgs84_bounds,
            "nodes": nodes,
            "edges": edges,
            "physical_states": physical_states,
            "edge_physical_states": list_spatial_edge_physical_states(
                self.repository.connection, world_key=world_key
            ),
        }

    def get_physical_states(self, world_key=None, node_ids=None):
        return {
            "physical_states": list_spatial_physical_states(self.repository.connection, world_key=world_key, node_ids=node_ids),
            "edge_physical_states": list_spatial_edge_physical_states(self.repository.connection, world_key=world_key),
        }

    def list_worlds(self):
        return {"worlds": self.repository.list_worlds()}

    def get_occupancy(self):
        spaces = []
        for row in self.repository.list_occupancy():
            item = dict(row)
            capacity = int(item["capacity"])
            occupancy = int(item["occupancy"])
            item["occupancy"] = occupancy
            item["occupancy_ratio"] = round(occupancy / capacity, 4) if capacity else 0
            spaces.append(item)
        return {"spaces": spaces}

    def get_resources(self):
        return {"resources": [dict(row) for row in self.repository.list_resources()]}

    def get_admission_queue(self):
        return {
            "queue": [dict(row) for row in self.repository.list_admission_queue()]
        }

    def get_agent_state(self, resident_id):
        row = self.repository.get_agent_state(resident_id)
        if not row:
            if not self.repository.resident_exists(resident_id):
                raise ResidentNotFoundError(f"Resident {resident_id} does not exist")
            raise SpatialStateNotInitializedError(
                f"Spatial state for resident {resident_id} is not initialized"
            )
        item = dict(row)
        item["capability"] = {
            "base_speed_m_per_min": item.pop("base_speed_m_per_min"),
            "mobility_class": item.pop("mobility_class"),
            "accessibility_needs": item.pop("accessibility_needs"),
            "perception_radius_m": item.pop("perception_radius_m"),
            "hearing_radius_m": item.pop("hearing_radius_m"),
            "source": item.pop("capability_source"),
            "version": item.pop("capability_version"),
        }
        return item

    def list_agent_states(self):
        return {
            "agents": [
                self._serialize_agent_state(row)
                for row in self.repository.list_agent_states()
            ]
        }

    @staticmethod
    def _serialize_agent_state(row):
        item = dict(row)
        item["capability"] = {
            "base_speed_m_per_min": item.pop("base_speed_m_per_min"),
            "mobility_class": item.pop("mobility_class"),
            "accessibility_needs": item.pop("accessibility_needs"),
            "perception_radius_m": item.pop("perception_radius_m"),
            "hearing_radius_m": item.pop("hearing_radius_m"),
            "source": item.pop("capability_source"),
            "version": item.pop("capability_version"),
        }
        return item

    def get_trajectory(
        self,
        resident_id,
        experiment_run_id=None,
        branch_key="main",
        from_tick=None,
        to_tick=None,
    ):
        if not self.repository.resident_exists(resident_id):
            raise ResidentNotFoundError(f"Resident {resident_id} does not exist")
        run_id = experiment_run_id or self.repository.latest_experiment_run_id()
        if run_id is None:
            return {
                "resident_id": resident_id,
                "experiment_run_id": None,
                "branch_key": branch_key,
                "from_tick": 0,
                "to_tick": 0,
                "trajectory": [],
            }
        latest = self.repository.latest_trajectory_tick(
            resident_id, run_id, branch_key
        )
        effective_to = int(to_tick if to_tick is not None else latest or 0)
        effective_from = int(
            from_tick if from_tick is not None else max(0, effective_to - 200)
        )
        if effective_from < 0 or effective_to < effective_from:
            raise ValueError("Invalid trajectory tick window")
        if effective_to - effective_from > 10_000:
            raise ValueError("Trajectory tick window cannot exceed 10000")
        rows = self.repository.list_trajectory(
            resident_id,
            run_id,
            branch_key,
            effective_from,
            effective_to,
        )
        return {
            "resident_id": resident_id,
            "experiment_run_id": run_id,
            "branch_key": branch_key,
            "from_tick": effective_from,
            "to_tick": effective_to,
            "trajectory": [dict(row) for row in rows],
        }


def update_spatial_weather_factors(conn, weather_data, day=None, add_event_func=None):
    if not conn.execute("PRAGMA table_info(spatial_edges)").fetchall():
        return {"updated_edges": 0, "outdoor_factor": 1.0}

    rainfall = float(weather_data.get("rainfall", 0) or 0)
    wind_speed = float(weather_data.get("wind_speed_10m", 0) or 0)
    temperature = float(weather_data.get("temperature", 24) or 24)
    weather = str(weather_data.get("weather", "晴"))

    if rainfall > 0 or wind_speed > 20 or abs(temperature - 22) > 10 or weather in {"小雨", "中雨", "大雨", "暴雨", "小雪", "大雪", "雷雨", "闷热"}:
        rain_mult = (rainfall / 20.0)
        wind_mult = max(0.0, (wind_speed - 15.0) / 10.0)
        temp_mult = max(0.0, (abs(temperature - 22.0) - 8.0) / 8.0)
        outdoor_factor = round(max(1.0, 1.0 + rain_mult + wind_mult + temp_mult), 2)
    else:
        outdoor_factor = 1.0

    # This runs inside a world tick.  Updating every imported OSM edge one by
    # one turns a 15k-edge campus into a multi-minute transaction.  Use one
    # set-based statement instead; an edge is indoor only when both endpoints
    # are building-like nodes, matching the former Python implementation.
    indoor_node = """
        (LOWER(COALESCE(node_type, '')) IN
           ('building', 'room', 'library', 'classroom', 'canteen', 'dorm', 'hall', 'indoor')
         OR name LIKE '%图书馆%' OR name LIKE '%教室%' OR name LIKE '%食堂%'
         OR name LIKE '%宿舍%' OR name LIKE '%公寓%' OR name LIKE '%教学楼%'
         OR name LIKE '%综合楼%' OR name LIKE '%馆%' OR name LIKE '%楼%')
    """
    indoor_edge = f"""
        EXISTS (
            SELECT 1 FROM spatial_nodes start_node
            JOIN spatial_nodes end_node ON end_node.id = spatial_edges.to_node_id
            WHERE start_node.id = spatial_edges.from_node_id
              AND {indoor_node.replace('node_type', 'start_node.node_type').replace('name', 'start_node.name')}
              AND {indoor_node.replace('node_type', 'end_node.node_type').replace('name', 'end_node.name')}
        )
    """
    target_factor = f"CASE WHEN {indoor_edge} THEN 1.0 ELSE ? END"
    changed = conn.execute(
        f"SELECT COUNT(*) AS count FROM spatial_edges WHERE ABS(weather_factor - ({target_factor})) > 0.01",
        (outdoor_factor,),
    ).fetchone()
    updated_count = int(changed["count"] or 0)
    if updated_count:
        conn.execute(
            f"UPDATE spatial_edges SET weather_factor = {target_factor} WHERE ABS(weather_factor - ({target_factor})) > 0.01",
            (outdoor_factor, outdoor_factor),
        )

    if updated_count > 0 and day is not None and add_event_func is not None:
        try:
            add_event_func(conn, day, "real_weather_edge_factor_updated", f"天气防风雨阻力规则触发：室外道路 weather_factor 调整为 {outdoor_factor} (天气: {weather}, 降雨: {int(rainfall)}, 风速: {wind_speed}km/h)，受影响道路 {updated_count} 条。")
        except Exception:
            pass

    return {"updated_edges": updated_count, "outdoor_factor": outdoor_factor}
