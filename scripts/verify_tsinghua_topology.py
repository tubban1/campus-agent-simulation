import random
from collections import deque
from sqlalchemy import select
from app.db.engine import create_database_engine
from app.spatial.models import spatial_nodes, spatial_edges
from app.spatial.planner import plan_route


def verify_topology(world_key: str = "tsinghua_main"):
    engine = create_database_engine()
    with engine.connect() as conn:
        all_nodes = [dict(row) for row in conn.execute(select(spatial_nodes)).mappings()]
        all_edges = [dict(row) for row in conn.execute(select(spatial_edges)).mappings()]

    # Filter nodes for world_key
    nodes = [
        n for n in all_nodes
        if (n.get("properties") or {}).get("world_key") == world_key
        or str(n.get("code")).startswith(world_key)
    ]
    node_ids = {n["id"] for n in nodes}
    edges = [
        e for e in all_edges
        if int(e["from_node_id"]) in node_ids and int(e["to_node_id"]) in node_ids
    ]

    node_by_id = {int(n["id"]): n for n in nodes}
    print(f"数据库中总节点数: {len(nodes)}")
    print(f"数据库中总边数: {len(edges)}")

    # Group nodes by node_type
    buildings = [n for n in nodes if n["node_type"] == "building"]
    pois = [n for n in nodes if n["node_type"] == "poi"]
    path_points = [n for n in nodes if n["node_type"] == "path_point"]
    outdoor = [n for n in nodes if n["node_type"] == "outdoor_area"]

    print(f"  - 建筑节点 (building): {len(buildings)}")
    print(f"  - POI节点 (poi): {len(pois)}")
    print(f"  - 路径途经节点 (path_point): {len(path_points)}")
    print(f"  - 户外区域节点 (outdoor_area): {len(outdoor)}")

    # Build undirected adjacency for LCC calculation
    adj = {node_id: set() for node_id in node_by_id}
    for e in edges:
        u = int(e["from_node_id"])
        v = int(e["to_node_id"])
        if u in adj and v in adj:
            adj[u].add(v)
            if e.get("bidirectional", True):
                adj[v].add(u)

    # Compute Connected Components
    visited = set()
    components = []

    for node_id in node_by_id:
        if node_id not in visited:
            comp = []
            q = deque([node_id])
            visited.add(node_id)
            while q:
                curr = q.popleft()
                comp.append(curr)
                for nxt in adj[curr]:
                    if nxt not in visited:
                        visited.add(nxt)
                        q.append(nxt)
            components.append(comp)

    components.sort(key=len, reverse=True)
    lcc_size = len(components[0]) if components else 0
    lcc_ratio = (lcc_size / len(nodes) * 100) if nodes else 0
    isolated_nodes = sum(1 for c in components if len(c) == 1)

    print("\n--- 图连通性分析 (Graph Connectivity Analysis) ---")
    print(f"连通分量总数 (Total Connected Components): {len(components)}")
    print(f"最大连通分量 (LCC) 节点数: {lcc_size} / {len(nodes)} ({lcc_ratio:.2f}%)")
    print(f"孤立无边节点数 (Isolated Nodes): {isolated_nodes}")

    # A* Reachability Test on 100 Random Building Pairs
    random.seed(42)
    sample_size = min(100, len(buildings))
    if len(buildings) >= 2:
        pairs = []
        for _ in range(sample_size):
            u = random.choice(buildings)
            v = random.choice(buildings)
            while u["id"] == v["id"]:
                v = random.choice(buildings)
            pairs.append((u, v))

        success_count = 0
        failed_count = 0

        for u, v in pairs:
            try:
                res = plan_route(nodes, edges, u["id"], v["id"], speed_m_per_min=78.0)
                if res and len(res.get("node_ids", [])) > 0:
                    success_count += 1
                else:
                    failed_count += 1
            except Exception:
                failed_count += 1

        success_rate = (success_count / sample_size) * 100
        print("\n--- A* 路径规划连通性抽样验证 (A* Reachability Test) ---")
        print(f"测试建筑对数量: {sample_size}")
        print(f"成功可达节点对: {success_count}")
        print(f"不可达节点对: {failed_count}")
        print(f"A* 寻路成功率: {success_rate:.2f}%")


if __name__ == "__main__":
    verify_topology()
