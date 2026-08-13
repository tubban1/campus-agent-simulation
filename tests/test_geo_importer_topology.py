import pytest
import sqlite3
from pathlib import Path
from sqlalchemy import create_engine, select, func, text, insert

from app.db.engine import create_database_engine
from app.db.metadata import metadata
import app.models  # noqa: F401
from app.spatial.geo_importer import (
    import_real_world_geojson,
    sync_database_sequences,
    LocalProjector,
)
from app.spatial.models import (
    spatial_edges,
    spatial_facility_states,
    spatial_import_batches,
    spatial_nodes,
    spatial_resources,
)
from app.spatial.planner import plan_route
from scripts.fetch_tsinghua_geojson import osm_to_geojson


@pytest.fixture
def memory_db():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(
        engine,
        tables=[
            spatial_nodes,
            spatial_edges,
            spatial_import_batches,
            spatial_resources,
            spatial_facility_states,
        ],
    )
    return engine


def sample_crossroad_geojson():
    """Two crossing roads (Road A: west to east, Road B: south to north) sharing intersection point (116.32, 40.00)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "@id": "way/101",
                    "highway": "footway",
                    "name": "主干主路A",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [116.31, 40.00],  # West point
                        [116.32, 40.00],  # Crossroad point
                        [116.33, 40.00],  # East point
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "@id": "way/102",
                    "highway": "footway",
                    "name": "南北辅路B",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [116.32, 39.99],  # South point
                        [116.32, 40.00],  # Crossroad point (shared!)
                        [116.32, 40.01],  # North point
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "@id": "way/201",
                    "building": "university",
                    "name": "图书馆A",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [116.309, 39.999],
                            [116.311, 39.999],
                            [116.311, 40.001],
                            [116.309, 40.001],
                            [116.309, 39.999],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "@id": "way/202",
                    "building": "university",
                    "name": "体育馆B",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [116.319, 40.009],
                            [116.321, 40.009],
                            [116.321, 40.011],
                            [116.319, 40.011],
                            [116.319, 40.009],
                        ]
                    ],
                },
            },
        ],
    }


def test_crossroad_node_deduplication_and_topology(memory_db):
    geojson = sample_crossroad_geojson()
    with memory_db.begin() as conn:
        summary = import_real_world_geojson(conn, geojson, world_key="test_campus")

    assert summary.nodes_created > 0
    assert summary.edges_created > 0

    with memory_db.connect() as conn:
        # Check intersection node deduplication
        # Crossroad point (116.32, 40.00) must exist as a SINGLE spatial_node
        path_nodes = conn.execute(
            select(spatial_nodes).where(spatial_nodes.c.node_type == "path_point")
        ).mappings().all()

        # Total unique coordinates in roads = (116.31,40), (116.32,40), (116.33,40), (116.32,39.99), (116.32,40.01) = 5 nodes
        assert len(path_nodes) == 5

        # Verify building attachment
        building_nodes = conn.execute(
            select(spatial_nodes).where(spatial_nodes.c.node_type == "building")
        ).mappings().all()
        assert len(building_nodes) == 2


def test_multi_road_astar_pathfinding(memory_db):
    geojson = sample_crossroad_geojson()
    with memory_db.begin() as conn:
        import_real_world_geojson(conn, geojson, world_key="test_campus")

    with memory_db.connect() as conn:
        nodes = [dict(row) for row in conn.execute(select(spatial_nodes)).mappings()]
        edges = [dict(row) for row in conn.execute(select(spatial_edges)).mappings()]
        node_map = {n["name"]: n for n in nodes}

        start_building = node_map["图书馆A"]
        end_building = node_map["体育馆B"]

        # Run A* Pathfinding between Library A (West) and Gymnasium B (North) crossing roads A & B
        route = plan_route(nodes, edges, start_building["id"], end_building["id"], speed_m_per_min=78.0)

        assert route is not None
        assert len(route["node_ids"]) >= 4  # Start building -> West path -> Intersection -> North path -> End building
        assert route["distance_meters"] > 0
        assert route["estimated_minutes"] > 0


def test_import_idempotency(memory_db):
    geojson = sample_crossroad_geojson()
    # First import
    with memory_db.begin() as conn:
        summary1 = import_real_world_geojson(conn, geojson, world_key="test_campus")

    # Second import (duplicate)
    with memory_db.begin() as conn:
        summary2 = import_real_world_geojson(conn, geojson, world_key="test_campus")

    assert summary2.nodes_created == 0
    assert summary2.edges_created == 0
    assert summary2.nodes_updated > 0


def test_database_sequence_and_subsequent_insert(memory_db):
    geojson = sample_crossroad_geojson()
    with memory_db.begin() as conn:
        import_real_world_geojson(conn, geojson, world_key="test_campus")
        sync_database_sequences(conn)

    # Perform a standard subsequent insert without providing explicit 'id'
    with memory_db.begin() as conn:
        result = conn.execute(
            insert(spatial_nodes).values(
                code="subsequent_test_node",
                name="后置测试节点",
                node_type="building",
                x=10.0,
                y=0.0,
                z=10.0,
                radius=5.0,
                capacity=100,
                status="open",
                properties={"source": "unit_test"},
            )
        )

        assert result.inserted_primary_key[0] is not None
        assert result.inserted_primary_key[0] > 0


def test_osm_relation_multipolygon_conversion():
    osm_raw = {
        "elements": [
            {"type": "node", "id": 1, "lon": 116.321, "lat": 40.001, "tags": {}},
            {"type": "node", "id": 2, "lon": 116.323, "lat": 40.001, "tags": {}},
            {"type": "node", "id": 3, "lon": 116.323, "lat": 40.003, "tags": {}},
            {"type": "node", "id": 4, "lon": 116.321, "lat": 40.003, "tags": {}},
            {
                "type": "way",
                "id": 10,
                "nodes": [1, 2, 3, 4, 1],
                "tags": {},
            },
            {
                "type": "relation",
                "id": 100,
                "members": [{"type": "way", "ref": 10, "role": "outer"}],
                "tags": {
                    "type": "multipolygon",
                    "building": "university",
                    "name": "清华中央主楼Relation",
                },
            },
        ]
    }

    geojson = osm_to_geojson(osm_raw)
    features = geojson.get("features", [])

    # Verify relation was converted to GeoJSON Feature with Polygon/MultiPolygon
    relation_features = [f for f in features if f["properties"].get("relation_id") == 100]
    assert len(relation_features) == 1

    rel_f = relation_features[0]
    assert rel_f["properties"]["name"] == "清华中央主楼Relation"
    assert rel_f["properties"]["fetched_at"] is not None
    assert rel_f["properties"]["license"] == "ODbL / OpenStreetMap contributors"
    assert rel_f["geometry"]["type"] in {"Polygon", "MultiPolygon"}
