from __future__ import annotations

from typing import Optional

from sqlalchemy import and_, func, select, text
from sqlalchemy.engine import Connection

from app.spatial.models import (
    agent_spatial_capabilities,
    agent_spatial_states,
    agent_trajectories,
    spatial_admission_queue,
    spatial_edges,
    spatial_nodes,
    spatial_resources,
)


class SpatialRepository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def _execute_rows(self, query):
        res = self.connection.execute(query)
        if hasattr(res, "mappings"):
            return [dict(r) for r in res.mappings()]
        if hasattr(res, "fetchall"):
            return [dict(r) for r in res.fetchall()]
        return [dict(r) for r in res]

    def list_nodes(
        self,
        world_key: Optional[str] = None,
        min_x: Optional[float] = None,
        min_z: Optional[float] = None,
        max_x: Optional[float] = None,
        max_z: Optional[float] = None,
    ):
        """Return nodes for one world, optionally clipped to a metric viewport.

        The clip is performed in SQL so a large imported campus is not
        serialized in full for every browser refresh.  A missing schema is a
        deployment error and deliberately propagates to readiness checks.
        """
        has_viewport = all(value is not None for value in (min_x, min_z, max_x, max_z))
        statement = select(spatial_nodes).order_by(spatial_nodes.c.id)
        if world_key:
            statement = statement.where(spatial_nodes.c.world_key == world_key)
        if has_viewport:
            statement = statement.where(
                and_(
                    spatial_nodes.c.x >= min_x,
                    spatial_nodes.c.x <= max_x,
                    spatial_nodes.c.z >= min_z,
                    spatial_nodes.c.z <= max_z,
                )
            )
        return self._execute_rows(statement)

    def list_edges(self, node_ids: Optional[set[int]] = None):
        all_edges = self._execute_rows(select(spatial_edges).order_by(spatial_edges.c.id))
        if node_ids is not None:
            return [
                e
                for e in all_edges
                if e["from_node_id"] in node_ids and e["to_node_id"] in node_ids
            ]
        return all_edges

    def list_worlds(self):
        all_nodes = self._execute_rows(select(spatial_nodes).order_by(spatial_nodes.c.id))
        all_edges = self._execute_rows(select(spatial_edges).order_by(spatial_edges.c.id))

        batches_by_world = {}
        from app.spatial.models import spatial_import_batches
        batch_rows = self._execute_rows(select(spatial_import_batches))
        for b in batch_rows:
            batches_by_world[b["world_key"]] = dict(b)

        world_nodes: dict[str, list[dict]] = {}
        for n in all_nodes:
            wk = n["world_key"]
            world_nodes.setdefault(wk, []).append(dict(n))

        name_map = {
            "tsinghua_main": "清华大学主校区",
            "tsinghua": "清华大学",
            "eth_zentrum": "ETH 站前校区",
        }

        worlds = []
        for wk, nodes in world_nodes.items():
            node_id_set = {n["id"] for n in nodes}
            edges_count = sum(
                1 for e in all_edges if e["from_node_id"] in node_id_set and e["to_node_id"] in node_id_set
            )

            xs = [n["x"] for n in nodes if n.get("x") is not None]
            zs = [n["z"] for n in nodes if n.get("z") is not None]
            lons = [n["longitude"] for n in nodes if n.get("longitude") is not None]
            lats = [n["latitude"] for n in nodes if n.get("latitude") is not None]

            batch_info = batches_by_world.get(wk, {})
            source = batch_info.get("source") or "OpenStreetMap contributors"
            license_info = batch_info.get("license") or "ODbL 1.0"

            worlds.append(
                {
                    "world_key": wk,
                    "name": name_map.get(wk, f"校园世界 ({wk})"),
                    "node_count": len(nodes),
                    "edge_count": edges_count,
                    "is_real_world": True,
                    "metric_bounds": [min(xs), min(zs), max(xs), max(zs)] if xs and zs else None,
                    "wgs84_bounds": [min(lons), min(lats), max(lons), max(lats)] if lons and lats else None,
                    "source": source,
                    "license": license_info,
                    "imported_at": str(batch_info.get("imported_at")) if batch_info.get("imported_at") else None,
                }
            )
        return sorted(worlds, key=lambda w: w["world_key"])

    def list_occupancy(self):
        occupancy = (
            select(
                agent_spatial_states.c.current_node_id.label("node_id"),
                func.count().label("occupancy"),
            )
            .group_by(agent_spatial_states.c.current_node_id)
            .subquery()
        )
        statement = (
            select(
                spatial_nodes.c.id.label("node_id"),
                spatial_nodes.c.code,
                spatial_nodes.c.name,
                spatial_nodes.c.capacity,
                func.coalesce(occupancy.c.occupancy, 0).label("occupancy"),
            )
            .outerjoin(occupancy, occupancy.c.node_id == spatial_nodes.c.id)
            .where(spatial_nodes.c.capacity > 0)
            .order_by(spatial_nodes.c.id)
        )
        return list(self.connection.execute(statement).mappings())

    def list_resources(self):
        statement = (
            select(
                spatial_resources,
                spatial_nodes.c.code.label("node_code"),
                spatial_nodes.c.name.label("node_name"),
            )
            .join(spatial_nodes, spatial_nodes.c.id == spatial_resources.c.node_id)
            .order_by(spatial_resources.c.node_id, spatial_resources.c.id)
        )
        return list(self.connection.execute(statement).mappings())

    def list_admission_queue(self):
        statement = (
            select(
                spatial_admission_queue,
                spatial_nodes.c.code.label("node_code"),
                spatial_nodes.c.name.label("node_name"),
                spatial_resources.c.resource_key,
                spatial_resources.c.name.label("resource_name"),
            )
            .join(
                spatial_nodes,
                spatial_nodes.c.id == spatial_admission_queue.c.node_id,
            )
            .outerjoin(
                spatial_resources,
                spatial_resources.c.id == spatial_admission_queue.c.resource_id,
            )
            .order_by(
                spatial_admission_queue.c.node_id,
                spatial_admission_queue.c.queue_position,
            )
        )
        return list(self.connection.execute(statement).mappings())

    def _agent_state_statement(self):
        current = spatial_nodes.alias("current_node")
        origin = spatial_nodes.alias("origin_node")
        target = spatial_nodes.alias("target_node")
        return (
            select(
                agent_spatial_states,
                current.c.code.label("current_node_code"),
                current.c.name.label("current_node_name"),
                current.c.longitude.label("longitude"),
                current.c.latitude.label("latitude"),
                origin.c.code.label("origin_node_code"),
                origin.c.name.label("origin_node_name"),
                target.c.code.label("target_node_code"),
                target.c.name.label("target_node_name"),
                agent_spatial_capabilities.c.base_speed_m_per_min,
                agent_spatial_capabilities.c.mobility_class,
                agent_spatial_capabilities.c.accessibility_needs,
                agent_spatial_capabilities.c.perception_radius_m,
                agent_spatial_capabilities.c.hearing_radius_m,
                agent_spatial_capabilities.c.source.label("capability_source"),
                agent_spatial_capabilities.c.version.label("capability_version"),
            )
            .join(current, current.c.id == agent_spatial_states.c.current_node_id)
            .outerjoin(origin, origin.c.id == agent_spatial_states.c.origin_node_id)
            .outerjoin(target, target.c.id == agent_spatial_states.c.target_node_id)
            .join(
                agent_spatial_capabilities,
                agent_spatial_capabilities.c.resident_id
                == agent_spatial_states.c.resident_id,
            )
        )

    def get_agent_state(self, resident_id):
        statement = self._agent_state_statement().where(
            agent_spatial_states.c.resident_id == resident_id
        )
        return self.connection.execute(statement).mappings().first()

    def list_agent_states(self):
        statement = self._agent_state_statement().order_by(
            agent_spatial_states.c.resident_id
        )
        return list(self.connection.execute(statement).mappings())

    def resident_exists(self, resident_id):
        row = self.connection.execute(
            text("SELECT 1 FROM residents WHERE id = :resident_id"),
            {"resident_id": resident_id},
        ).first()
        return row is not None

    def latest_experiment_run_id(self):
        row = self.connection.exec_driver_sql(
            "SELECT id FROM experiment_runs ORDER BY id DESC LIMIT 1"
        ).first()
        return int(row[0]) if row else None

    def latest_trajectory_tick(self, resident_id, experiment_run_id, branch_key):
        statement = select(func.max(agent_trajectories.c.tick_number)).where(
            and_(
                agent_trajectories.c.resident_id == resident_id,
                agent_trajectories.c.experiment_run_id == experiment_run_id,
                agent_trajectories.c.branch_key == branch_key,
            )
        )
        return self.connection.execute(statement).scalar_one_or_none()

    def list_trajectory(
        self,
        resident_id,
        experiment_run_id,
        branch_key,
        from_tick,
        to_tick,
    ):
        statement = (
            select(agent_trajectories)
            .where(
                and_(
                    agent_trajectories.c.resident_id == resident_id,
                    agent_trajectories.c.experiment_run_id == experiment_run_id,
                    agent_trajectories.c.branch_key == branch_key,
                    agent_trajectories.c.tick_number >= from_tick,
                    agent_trajectories.c.tick_number <= to_tick,
                )
            )
            .order_by(agent_trajectories.c.tick_number, agent_trajectories.c.id)
        )
        return list(self.connection.execute(statement).mappings())
