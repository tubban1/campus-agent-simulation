from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import bindparam, func, insert, select, text, update
from sqlalchemy.engine import Connection

from app.spatial.models import spatial_edges, spatial_import_batches, spatial_nodes
from app.spatial.facility_service import sync_real_world_facility_resources


EARTH_RADIUS_METERS = 6_371_000
PATH_HIGHWAYS = {
    "footway",
    "path",
    "pedestrian",
    "steps",
    "cycleway",
    "service",
    "residential",
    "living_street",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "trunk",
    "motorway",
}
RAILWAY_KEYS = {"rail", "light_rail", "subway", "tram", "monorail"}
WATERWAY_KEYS = {"river", "stream", "canal", "ditch", "drain"}
OUTDOOR_KEYS = {"garden", "park", "pitch", "track", "grass", "wood", "scrub", "meadow", "forest", "orchard"}
WATER_KEYS = {"water", "reservoir", "basin", "salt_pond"}
BOUNDARY_KEYS = {"administrative", "national_park", "protected_area"}
POI_AMENITIES = {
    "school",
    "university",
    "college",
    "library",
    "canteen",
    "restaurant",
    "cafe",
    "hospital",
    "clinic",
    "pharmacy",
    "police",
    "fire_station",
    "townhall",
    "marketplace",
    "bus_station",
    "ferry_terminal",
    "parking",
}
TRANSIT_KEYS = {"station", "halt", "stop", "platform", "subway_entrance"}


@dataclass
class GeoImportSummary:
    world_key: str
    origin_lat: float
    origin_lon: float
    nodes_created: int
    nodes_updated: int
    edges_created: int
    edges_skipped: int
    features_seen: int
    features_imported: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "world_key": self.world_key,
            "origin_lat": self.origin_lat,
            "origin_lon": self.origin_lon,
            "nodes_created": self.nodes_created,
            "nodes_updated": self.nodes_updated,
            "edges_created": self.edges_created,
            "edges_skipped": self.edges_skipped,
            "features_seen": self.features_seen,
            "features_imported": self.features_imported,
        }


def load_geojson(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def sync_database_sequences(connection: Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text(
            "SELECT setval(pg_get_serial_sequence('spatial_nodes', 'id'), COALESCE((SELECT MAX(id) FROM spatial_nodes), 1), true);"
        ))
        connection.execute(text(
            "SELECT setval(pg_get_serial_sequence('spatial_edges', 'id'), COALESCE((SELECT MAX(id) FROM spatial_edges), 1), true);"
        ))


def import_real_world_geojson(
    connection: Connection,
    geojson: dict[str, Any],
    *,
    world_key: str,
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
    source: str = "OpenStreetMap contributors / Overpass API",
    license_info: str = "ODbL 1.0",
    dry_run: bool = False,
) -> GeoImportSummary:
    features = list(geojson.get("features") or [])
    if origin_lat is None or origin_lon is None:
        origin_lat, origin_lon = infer_origin(features)
    transformer = LocalProjector(origin_lat, origin_lon)
    existing_nodes = {
        row.code: dict(row)
        for row in connection.execute(
            select(spatial_nodes).where(spatial_nodes.c.world_key == world_key)
        ).mappings()
    }
    existing_edges = {
        (int(row.from_node_id), int(row.to_node_id))
        for row in connection.execute(
            select(spatial_edges.c.from_node_id, spatial_edges.c.to_node_id)
        )
    }
    existing_max_node_id = connection.execute(
        select(func.max(spatial_nodes.c.id))
    ).scalar() or 0
    next_node_id = existing_max_node_id

    nodes_created = nodes_updated = edges_created = edges_skipped = features_imported = 0
    imported_nodes: dict[str, dict[str, Any]] = {}
    pending_node_inserts: list[dict[str, Any]] = []
    pending_node_updates: list[dict[str, Any]] = []
    pending_edge_inserts: list[dict[str, Any]] = []

    db_existing_codes = set(existing_nodes.keys())

    def upsert_node(values: dict[str, Any]) -> dict[str, Any]:
        nonlocal nodes_created, nodes_updated, next_node_id
        existing = existing_nodes.get(values["code"])
        if existing:
            merged = {**existing, **values, "id": existing["id"]}
            existing_nodes[values["code"]] = merged
            imported_nodes[values["code"]] = merged
            if values["code"] in db_existing_codes:
                pending_node_updates.append(merged)
                nodes_updated += 1
            return merged
        next_node_id += 1
        node = {"id": next_node_id, **values}
        existing_nodes[values["code"]] = node
        imported_nodes[values["code"]] = node
        pending_node_inserts.append(node)
        nodes_created += 1
        return node

    for index, feature in enumerate(features):
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "Point" and coordinates:
            node_values = point_feature_to_node(feature, index, world_key, transformer)
            if node_values:
                upsert_node(node_values)
                features_imported += 1
        elif geometry_type in {"Polygon", "MultiPolygon"} and coordinates:
            node_values = polygon_feature_to_node(
                feature,
                index,
                world_key,
                transformer,
            )
            if node_values:
                upsert_node(node_values)
                features_imported += 1
        elif geometry_type in {"LineString", "MultiLineString"} and coordinates:
            path_kind = classify_path(properties)
            if not path_kind:
                continue
            lines = coordinates if geometry_type == "MultiLineString" else [coordinates]
            path_name = feature_name(properties, "")
            line_imported = False
            for line in lines:
                if len(line) < 2:
                    continue
                for i in range(len(line) - 1):
                    p1 = line[i]
                    p2 = line[i + 1]
                    node1_values = make_path_point_node(p1, world_key, transformer, properties, path_kind, path_name)
                    node2_values = make_path_point_node(p2, world_key, transformer, properties, path_kind, path_name)
                    n1 = upsert_node(node1_values)
                    n2 = upsert_node(node2_values)
                    if n1["id"] == n2["id"]:
                        continue
                    dist_m = math.hypot(n2["x"] - n1["x"], n2["z"] - n1["z"])
                    if dist_m < 0.01:
                        continue
                    base_min = max(0.01, dist_m / 78.0)

                    oneway_val = str(properties.get("oneway") or "").lower()
                    if oneway_val in {"-1"}:
                        f_id, t_id = int(n2["id"]), int(n1["id"])
                        bidirectional = False
                    elif oneway_val in {"yes", "true", "1"}:
                        f_id, t_id = int(n1["id"]), int(n2["id"])
                        bidirectional = False
                    else:
                        f_id, t_id = int(n1["id"]), int(n2["id"])
                        bidirectional = True

                    edge_key = (f_id, t_id)
                    if edge_key in existing_edges:
                        edges_skipped += 1
                        continue

                    pending_edge_inserts.append({
                        "from_node_id": f_id,
                        "to_node_id": t_id,
                        "distance_meters": round(dist_m, 2),
                        "base_minutes": round(base_min, 3),
                        "bidirectional": bidirectional,
                        "status": "open",
                        "congestion_factor": 1.0,
                        "weather_factor": 1.0,
                        "properties": {
                            "source": "geojson",
                            "world_key": world_key,
                            "path_kind": path_kind,
                            "bridge": properties.get("bridge"),
                            "tunnel": properties.get("tunnel"),
                            "layer": properties.get("layer"),
                            "osm_tags": compact_osm_tags(properties),
                            "real_world": True,
                        },
                    })
                    existing_edges.add(edge_key)
                    edges_created += 1
                    line_imported = True
            if line_imported:
                features_imported += 1

    # Connect buildings, POIs, outdoor_areas to nearest path_point node using spatial grid index
    path_nodes = [
        node for node in existing_nodes.values()
        if node.get("node_type") == "path_point" and node.get("world_key", "default") == world_key
    ]
    if path_nodes:
        # Build adjacency graph of path nodes to identify the main road network components
        path_adj: dict[int, set[int]] = {int(n["id"]): set() for n in path_nodes}
        for e in pending_edge_inserts:
            u, v = int(e["from_node_id"]), int(e["to_node_id"])
            if u in path_adj and v in path_adj:
                path_adj[u].add(v)
                if e.get("bidirectional", True):
                    path_adj[v].add(u)

        visited_ids = set()
        road_components = []
        for pid in path_adj:
            if pid not in visited_ids:
                comp = []
                q = deque([pid])
                visited_ids.add(pid)
                while q:
                    curr = q.popleft()
                    comp.append(curr)
                    for nxt in path_adj[curr]:
                        if nxt not in visited_ids:
                            visited_ids.add(nxt)
                            q.append(nxt)
                road_components.append(comp)

        road_components.sort(key=len, reverse=True)
        # Select path nodes belonging to main road components (component size >= 10)
        main_road_node_ids = set()
        for comp in road_components:
            if len(comp) >= 10 or (road_components and comp == road_components[0]):
                main_road_node_ids.update(comp)

        attachable_path_nodes = [n for n in path_nodes if int(n["id"]) in main_road_node_ids]
        if not attachable_path_nodes:
            attachable_path_nodes = path_nodes

        grid_cell_size = 100.0
        grid: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for p_node in attachable_path_nodes:
            cell = (int(p_node["x"] // grid_cell_size), int(p_node["z"] // grid_cell_size))
            grid.setdefault(cell, []).append(p_node)

        target_nodes = [
            node for node in imported_nodes.values()
            if node.get("node_type") in {"building", "poi", "outdoor_area"}
        ]
        for target_node in target_nodes:
            target_id = int(target_node["id"])
            tx, tz = target_node["x"], target_node["z"]
            best_node = None
            best_dist = 250.0  # Max attachment radius 250m
            cx, cz = int(tx // grid_cell_size), int(tz // grid_cell_size)
            for dx in (-2, -1, 0, 1, 2):
                for dz in (-2, -1, 0, 1, 2):
                    neighbor_nodes = grid.get((cx + dx, cz + dz))
                    if not neighbor_nodes:
                        continue
                    for p_node in neighbor_nodes:
                        d = math.hypot(p_node["x"] - tx, p_node["z"] - tz)
                        if d < best_dist:
                            best_dist = d
                            best_node = p_node

            if best_node:
                p_id = int(best_node["id"])
                edge_key = (target_id, p_id)
                if edge_key not in existing_edges:
                    dist_m = max(0.1, round(best_dist, 2))
                    pending_edge_inserts.append({
                        "from_node_id": target_id,
                        "to_node_id": p_id,
                        "distance_meters": dist_m,
                        "base_minutes": round(dist_m / 78.0, 3),
                        "bidirectional": True,
                        "status": "open",
                        "congestion_factor": 1.0,
                        "weather_factor": 1.0,
                        "properties": {
                            "source": "geojson_attachment",
                            "world_key": world_key,
                            "path_kind": "attachment",
                            "real_world": True,
                        },
                    })
                    existing_edges.add(edge_key)
                    edges_created += 1

    if not dry_run:
        chunk_size = 500
        if pending_node_inserts:
            for i in range(0, len(pending_node_inserts), chunk_size):
                connection.execute(insert(spatial_nodes), pending_node_inserts[i : i + chunk_size])
        if pending_node_updates:
            update_params = [
                {
                    "b_code": item["code"],
                    "name": item.get("name"),
                    "node_type": item.get("node_type"),
                    "parent_id": item.get("parent_id"),
                    "world_key": item.get("world_key", world_key),
                    "x": item.get("x"),
                    "y": item.get("y"),
                    "z": item.get("z"),
                    "longitude": item.get("longitude"),
                    "latitude": item.get("latitude"),
                    "elevation_m": item.get("elevation_m", 0.0),
                    "geometry_json": item.get("geometry_json"),
                    "source_element_id": item.get("source_element_id"),
                    "radius": item.get("radius"),
                    "capacity": item.get("capacity"),
                    "status": item.get("status"),
                    "properties": item.get("properties"),
                }
                for item in pending_node_updates
            ]
            stmt = (
                update(spatial_nodes)
                .where(spatial_nodes.c.code == bindparam("b_code"))
                .values(
                    name=bindparam("name"),
                    node_type=bindparam("node_type"),
                    parent_id=bindparam("parent_id"),
                    world_key=bindparam("world_key"),
                    x=bindparam("x"),
                    y=bindparam("y"),
                    z=bindparam("z"),
                    longitude=bindparam("longitude"),
                    latitude=bindparam("latitude"),
                    elevation_m=bindparam("elevation_m"),
                    geometry_json=bindparam("geometry_json"),
                    source_element_id=bindparam("source_element_id"),
                    radius=bindparam("radius"),
                    capacity=bindparam("capacity"),
                    status=bindparam("status"),
                    properties=bindparam("properties"),
                )
            )
            for i in range(0, len(update_params), chunk_size):
                connection.execute(stmt, update_params[i : i + chunk_size])
        if pending_edge_inserts:
            for i in range(0, len(pending_edge_inserts), chunk_size):
                connection.execute(insert(spatial_edges), pending_edge_inserts[i : i + chunk_size])

        batch_key = f"{world_key}_import_batch"
        batch_values = {
            "batch_key": batch_key,
            "world_key": world_key,
            "source": source,
            "license": license_info,
            "original_crs": "EPSG:4326",
            "projection_meta": json.dumps({
                "origin_lat": origin_lat,
                "origin_lon": origin_lon,
                "projector": "LocalProjector",
                "earth_radius_m": EARTH_RADIUS_METERS,
            }),
            "nodes_count": len(imported_nodes),
            "edges_count": edges_created,
            "features_count": features_imported,
            "quality_meta": json.dumps({
                "features_seen": len(features),
                "features_imported": features_imported,
                "edges_skipped": edges_skipped,
                "nodes_created": nodes_created,
                "nodes_updated": nodes_updated,
                "edges_created": edges_created,
            }),
        }
        existing_batch = connection.execute(
            select(spatial_import_batches.c.id).where(spatial_import_batches.c.batch_key == batch_key)
        ).scalar()
        if existing_batch:
            connection.execute(
                update(spatial_import_batches)
                .where(spatial_import_batches.c.batch_key == batch_key)
                .values(**batch_values)
            )
        else:
            connection.execute(insert(spatial_import_batches).values(**batch_values))
        sync_real_world_facility_resources(connection, world_key=world_key)

        sync_database_sequences(connection)

    return GeoImportSummary(
        world_key=world_key,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        edges_created=edges_created,
        edges_skipped=edges_skipped,
        features_seen=len(features),
        features_imported=features_imported,
    )


class LocalProjector:
    def __init__(self, origin_lat: float, origin_lon: float):
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.origin_lat_rad = math.radians(origin_lat)

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        x = (
            math.radians(lon - self.origin_lon)
            * EARTH_RADIUS_METERS
            * math.cos(self.origin_lat_rad)
        )
        z = math.radians(lat - self.origin_lat) * EARTH_RADIUS_METERS
        return x, z


def infer_origin(features: list[dict[str, Any]]) -> tuple[float, float]:
    lon_lat_pairs = []
    for feature in features:
        coordinates = (feature.get("geometry") or {}).get("coordinates")
        lon_lat_pairs.extend(iter_lon_lat_pairs(coordinates))
    if not lon_lat_pairs:
        raise ValueError("GeoJSON contains no coordinates.")
    lon = sum(pair[0] for pair in lon_lat_pairs) / len(lon_lat_pairs)
    lat = sum(pair[1] for pair in lon_lat_pairs) / len(lon_lat_pairs)
    return lat, lon


def iter_lon_lat_pairs(coordinates: Any):
    if not isinstance(coordinates, list):
        return
    if len(coordinates) >= 2 and all(isinstance(value, (int, float)) for value in coordinates[:2]):
        yield float(coordinates[0]), float(coordinates[1])
        return
    for item in coordinates:
        yield from iter_lon_lat_pairs(item)


def polygon_feature_to_node(
    feature: dict[str, Any],
    index: int,
    world_key: str,
    projector: LocalProjector,
) -> Optional[dict[str, Any]]:
    geometry = feature.get("geometry") or {}
    properties = feature.get("properties") or {}
    rings = polygon_rings(geometry)
    if not rings:
        return None
    local_ring = [projector.project(float(lon), float(lat)) for lon, lat in rings[0]]
    if len(local_ring) < 3:
        return None
    centroid_x, centroid_z = polygon_centroid(local_ring)
    area = abs(polygon_area(local_ring))
    tags = compact_osm_tags(properties)
    node_type = classify_area(properties)
    if not node_type:
        return None
    code = stable_code(world_key, node_type, properties, index)
    name = feature_name(properties, code)
    levels = numeric_tag(properties.get("building:levels"))
    height = numeric_tag(properties.get("height"))
    radius = max(2.0, min(80.0, math.sqrt(max(area, 1.0) / math.pi)))
    capacity = capacity_for_area(node_type, area, properties)
    footprint = [[round(x, 3), 0.0, round(z, 3)] for x, z in local_ring]
    avg_lon = sum(float(p[0]) for p in rings[0]) / len(rings[0])
    avg_lat = sum(float(p[1]) for p in rings[0]) / len(rings[0])
    source_elem_id = str(properties.get("@id") or properties.get("id") or properties.get("osm_id") or "")
    return {
        "code": code,
        "name": name,
        "node_type": node_type,
        "parent_id": None,
        "world_key": world_key,
        "x": centroid_x,
        "y": 0.0,
        "z": centroid_z,
        "longitude": round(avg_lon, 7),
        "latitude": round(avg_lat, 7),
        "elevation_m": numeric_tag(properties.get("ele")) or 0.0,
        "geometry_json": geometry,
        "source_element_id": source_elem_id,
        "radius": radius,
        "capacity": capacity,
        "status": "open",
        "properties": {
            "coordinate_unit": "meters",
            "source": "geojson",
            "world_key": world_key,
            "campus_key": world_key,
            "osm_tags": tags,
            "area_m2": round(area, 2),
            "footprint": footprint,
            "height_m": height or (levels * 3.2 if levels else None),
            "building_levels": levels,
            "real_world": True,
        },
    }


def point_feature_to_node(
    feature: dict[str, Any],
    index: int,
    world_key: str,
    projector: LocalProjector,
) -> Optional[dict[str, Any]]:
    geometry = feature.get("geometry") or {}
    properties = feature.get("properties") or {}
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        return None
    node_type = classify_point(properties)
    if not node_type:
        return None
    lon, lat = float(coordinates[0]), float(coordinates[1])
    x, z = projector.project(lon, lat)
    code = stable_code(world_key, node_type, properties, index)
    source_elem_id = str(properties.get("@id") or properties.get("id") or properties.get("osm_id") or "")
    return {
        "code": code,
        "name": feature_name(properties, code),
        "node_type": node_type,
        "parent_id": None,
        "world_key": world_key,
        "x": x,
        "y": numeric_tag(properties.get("ele")) or 0.0,
        "z": z,
        "longitude": round(lon, 7),
        "latitude": round(lat, 7),
        "elevation_m": numeric_tag(properties.get("ele")) or 0.0,
        "geometry_json": geometry,
        "source_element_id": source_elem_id,
        "radius": 2.0,
        "capacity": capacity_for_point(node_type, properties),
        "status": "open",
        "properties": {
            "coordinate_unit": "meters",
            "source": "geojson",
            "world_key": world_key,
            "campus_key": world_key,
            "osm_tags": compact_osm_tags(properties),
            "real_world": True,
        },
    }


def make_path_point_node(
    coord: list[Any],
    world_key: str,
    projector: LocalProjector,
    properties: dict[str, Any],
    path_kind: str,
    path_name: str,
) -> dict[str, Any]:
    lon, lat = float(coord[0]), float(coord[1])
    coord_key = f"{round(lon, 7)},{round(lat, 7)}"
    point_hash = hashlib.sha1(coord_key.encode("utf-8")).hexdigest()[:10]
    code = short_code(f"{world_key}_pt_{point_hash}")
    x, z = projector.project(lon, lat)
    source_elem_id = str(properties.get("@id") or properties.get("id") or properties.get("osm_id") or "")
    return {
        "code": code,
        "name": f"{path_name} 途经点" if path_name else f"路径节点 {code[-10:]}",
        "node_type": "path_point",
        "parent_id": None,
        "world_key": world_key,
        "x": round(x, 3),
        "y": numeric_tag(properties.get("ele")) or 0.0,
        "z": round(z, 3),
        "longitude": round(lon, 7),
        "latitude": round(lat, 7),
        "elevation_m": numeric_tag(properties.get("ele")) or 0.0,
        "geometry_json": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
        "source_element_id": source_elem_id,
        "radius": 2.0,
        "capacity": 0,
        "status": "open",
        "properties": {
            "coordinate_unit": "meters",
            "source": "geojson",
            "world_key": world_key,
            "campus_key": world_key,
            "lon": round(lon, 7),
            "lat": round(lat, 7),
            "path_kind": path_kind,
            "osm_tags": compact_osm_tags(properties),
            "real_world": True,
        },
    }


def polygon_rings(geometry: dict[str, Any]) -> list[list[Any]]:
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        return coordinates
    if geometry.get("type") == "MultiPolygon" and coordinates:
        return max(coordinates, key=lambda polygon: len(polygon[0]) if polygon else 0)
    return []


def classify_area(properties: dict[str, Any]) -> Optional[str]:
    if properties.get("building"):
        return "building"
    amenity = str(properties.get("amenity") or "")
    leisure = str(properties.get("leisure") or "")
    landuse = str(properties.get("landuse") or "")
    natural = str(properties.get("natural") or "")
    boundary = str(properties.get("boundary") or "")
    place = str(properties.get("place") or "")
    tourism = str(properties.get("tourism") or "")
    man_made = str(properties.get("man_made") or "")
    water = str(properties.get("water") or "")
    if amenity in POI_AMENITIES or tourism or man_made in {"pier", "bridge", "tower", "works"}:
        return "poi"
    if leisure in OUTDOOR_KEYS or landuse in OUTDOOR_KEYS or natural in OUTDOOR_KEYS:
        return "outdoor_area"
    if natural in {"water", "bay", "strait", "coastline"} or water in WATER_KEYS or landuse in WATER_KEYS:
        return "water_area"
    if natural in {"peak", "cliff", "ridge", "saddle", "valley"}:
        return "terrain"
    if boundary in BOUNDARY_KEYS or place in {"city", "town", "village", "suburb", "neighbourhood", "island"}:
        return "boundary_area"
    return None


def classify_point(properties: dict[str, Any]) -> Optional[str]:
    amenity = str(properties.get("amenity") or "")
    railway = str(properties.get("railway") or "")
    public_transport = str(properties.get("public_transport") or "")
    highway = str(properties.get("highway") or "")
    natural = str(properties.get("natural") or "")
    tourism = str(properties.get("tourism") or "")
    place = str(properties.get("place") or "")
    if amenity in POI_AMENITIES or tourism:
        return "poi"
    if railway in TRANSIT_KEYS or public_transport in TRANSIT_KEYS or highway == "bus_stop":
        return "transit_stop"
    if natural in {"peak", "saddle", "spring", "cave_entrance"}:
        return "terrain"
    if place in {"city", "town", "village", "suburb", "neighbourhood", "island"}:
        return "boundary_area"
    return None


def classify_path(properties: dict[str, Any]) -> Optional[str]:
    highway = str(properties.get("highway") or "")
    railway = str(properties.get("railway") or "")
    waterway = str(properties.get("waterway") or "")
    route = str(properties.get("route") or "")
    if highway in PATH_HIGHWAYS:
        return "path"
    if railway in RAILWAY_KEYS or route in {"train", "subway", "tram", "railway"}:
        return "rail"
    if waterway in WATERWAY_KEYS or route in {"ferry"}:
        return "waterway"
    return None


def is_oneway(properties: dict[str, Any]) -> bool:
    return str(properties.get("oneway") or "").lower() in {"yes", "true", "1"}


def feature_name(properties: dict[str, Any], fallback: str) -> str:
    for key in ("name:zh", "name:zh-Hans", "name", "alt_name"):
        value = str(properties.get(key) or "").strip()
        if value:
            return value[:120]
    return fallback[:120]


def stable_code(
    campus_key: str,
    node_type: str,
    properties: dict[str, Any],
    index: int,
) -> str:
    source_id = (
        properties.get("@id")
        or properties.get("id")
        or properties.get("osm_id")
        or properties.get("name")
        or index
    )
    digest = hashlib.sha1(str(source_id).encode("utf-8")).hexdigest()[:10]
    slug = slugify(str(properties.get("name") or node_type))
    return short_code(f"{campus_key}_{node_type}_{slug}_{digest}")


def short_code(value: str) -> str:
    if len(value) <= 64:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return f"{value[:53]}_{digest}"


def slugify(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("_").lower()
    return value or "feature"


def compact_osm_tags(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in properties.items()
        if not str(key).startswith("_") and value not in (None, "")
    }


def numeric_tag(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def polygon_area(points: list[tuple[float, float]]) -> float:
    return sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    ) / 2


def polygon_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    area = polygon_area(points)
    if abs(area) < 1e-6:
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
    factor = 1 / (6 * area)
    x = sum(
        (points[index][0] + points[(index + 1) % len(points)][0])
        * (
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
        )
        for index in range(len(points))
    ) * factor
    z = sum(
        (points[index][1] + points[(index + 1) % len(points)][1])
        * (
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
        )
        for index in range(len(points))
    ) * factor
    return x, z


def path_distance(line: list[Any], projector: LocalProjector) -> float:
    total = 0.0
    previous = None
    for point in line:
        current = projector.project(float(point[0]), float(point[1]))
        if previous:
            total += math.dist(previous, current)
        previous = current
    return max(total, 0.1)


def capacity_for_area(node_type: str, area: float, properties: dict[str, Any]) -> int:
    if node_type == "building":
        levels = numeric_tag(properties.get("building:levels")) or 1
        return max(1, int(area * levels / 6))
    if node_type == "poi":
        return max(1, int(area / 8))
    if node_type in {"water_area", "terrain", "boundary_area"}:
        return 0
    return max(1, int(area / 12))


def capacity_for_point(node_type: str, properties: dict[str, Any]) -> int:
    if node_type == "transit_stop":
        return 40
    if node_type == "poi":
        amenity = str(properties.get("amenity") or "")
        if amenity in {"hospital", "school", "university", "college", "marketplace"}:
            return 120
        if amenity in {"restaurant", "cafe", "canteen"}:
            return 40
        return 20
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import real-world GeoJSON into spatial truth tables.")
    parser.add_argument("geojson_path", type=Path)
    parser.add_argument("--world-key", help="Stable short key, e.g. eth_zentrum or west_lake.")
    parser.add_argument("--origin-lat", type=float)
    parser.add_argument("--origin-lon", type=float)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main():
    from app.db.engine import create_database_engine
    from app.db.metadata import metadata
    parser = build_parser()
    args = parser.parse_args()
    world_key = args.world_key or "tsinghua_main"
    geojson = load_geojson(args.geojson_path)
    engine = create_database_engine()
    metadata.create_all(engine, tables=[spatial_nodes, spatial_edges, spatial_import_batches])
    with engine.begin() as conn:
        summary = import_real_world_geojson(
            conn,
            geojson,
            world_key=world_key,
            origin_lat=args.origin_lat,
            origin_lon=args.origin_lon,
            dry_run=args.dry_run,
        )
    print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
