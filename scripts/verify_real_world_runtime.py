"""Read-only production acceptance checks for an imported real-world campus.

Run this after deployment against the target PostgreSQL/Supabase database.  It
does not seed, migrate, alter data, or start ticks; it verifies the actual
world that the API will serve.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import create_database_engine  # noqa: E402
from app.spatial.planner import RouteNotFoundError, plan_route  # noqa: E402
from app.spatial.repository import SpatialRepository  # noqa: E402
from app.spatial.service import SpatialService  # noqa: E402


def _seconds_since(start: float) -> float:
    return round(time.perf_counter() - start, 3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-key", default="tsinghua_main")
    parser.add_argument("--min-nodes", type=int, default=6000)
    parser.add_argument("--min-edges", type=int, default=7000)
    parser.add_argument("--max-scene-seconds", type=float, default=8.0)
    parser.add_argument("--max-route-seconds", type=float, default=3.0)
    args = parser.parse_args()

    engine = create_database_engine()
    try:
        with engine.connect() as conn:
            repo = SpatialRepository(conn)
            nodes = repo.list_nodes(world_key=args.world_key)
            node_ids = {int(node["id"]) for node in nodes}
            edges = repo.list_edges(node_ids=node_ids)
            if len(nodes) < args.min_nodes or len(edges) < args.min_edges:
                raise SystemExit(
                    f"{args.world_key} data is incomplete: nodes={len(nodes)}, edges={len(edges)}"
                )

            scene_start = time.perf_counter()
            scene = SpatialService(repo).get_scene_graph(world_key=args.world_key)
            scene_seconds = _seconds_since(scene_start)
            if scene_seconds > args.max_scene_seconds:
                raise SystemExit(
                    f"scene assembly exceeded budget: {scene_seconds}s > {args.max_scene_seconds}s"
                )

            route_start = time.perf_counter()
            if not edges:
                raise SystemExit("world has no route candidates")
            # An edge's endpoints are guaranteed to belong to one connected
            # component, unlike arbitrary database IDs from an OSM import.
            edge = edges[0]
            route = plan_route(
                nodes, edges,
                start_node_id=edge["from_node_id"],
                target_node_id=edge["to_node_id"],
                speed_m_per_min=70.0,
            )
            route_seconds = _seconds_since(route_start)
            if route_seconds > args.max_route_seconds:
                raise SystemExit(
                    f"route planning exceeded budget: {route_seconds}s > {args.max_route_seconds}s"
                )
    finally:
        engine.dispose()

    print(
        "Real-world runtime acceptance passed: "
        f"world={args.world_key}, nodes={len(nodes)}, edges={len(edges)}, "
        f"scene_seconds={scene_seconds}, route_seconds={route_seconds}, "
        f"scene_nodes={len(scene['nodes'])}, route_nodes={len(route['node_ids'])}."
    )


if __name__ == "__main__":
    main()
