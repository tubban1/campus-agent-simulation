from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from app.spatial.models import spatial_edges, spatial_nodes


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


@dataclass(frozen=True)
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON must be a FeatureCollection.")
    return payload


def import_campus_geojson(
    connection: Connection,
    geojson: dict[str, Any],
    *,
    campus_key: str,
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
    dry_run: bool = False,
) -> GeoImportSummary:
    return import_real_world_geojson(
        connection,
        geojson,
        world_key=campus_key,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        dry_run=dry_run,
    )


def import_real_world_geojson(
    connection: Connection,
    geojson: dict[str, Any],
    *,
    world_key: str,
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
    dry_run: bool = False,
) -> GeoImportSummary:
    features = list(geojson.get("features") or [])
    if origin_lat is None or origin_lon is None:
        origin_lat, origin_lon = infer_origin(features)
    transformer = LocalProjector(origin_lat, origin_lon)
    existing_nodes = {
        row.code: dict(row)
        for row in connection.execute(select(spatial_nodes)).mappings()
    }
    existing_edges = {
        (int(row.from_node_id), int(row.to_node_id))
        for row in connection.execute(
            select(spatial_edges.c.from_node_id, spatial_edges.c.to_node_id)
        )
    }
    nodes_created = nodes_updated = edges_created = edges_skipped = features_imported = 0
    imported_nodes: dict[str, dict[str, Any]] = {}

    def upsert_node(values: dict[str, Any]) -> dict[str, Any]:
        nonlocal nodes_created, nodes_updated
        existing = existing_nodes.get(values["code"])
        if existing:
            merged = {**existing, **values, "id": existing["id"]}
            existing_nodes[values["code"]] = merged
            imported_nodes[values["code"]] = merged
            if not dry_run:
                connection.execute(
                    update(spatial_nodes)
                    .where(spatial_nodes.c.code == values["code"])
                    .values(**{key: value for key, value in values.items() if key != "code"})
                )
            nodes_updated += 1
            return merged
        node = {"id": -len(imported_nodes) - 1, **values}
        if not dry_run:
            cursor = connection.execute(insert(spatial_nodes).values(**values))
            node["id"] = cursor.inserted_primary_key[0]
        existing_nodes[values["code"]] = node
        imported_nodes[values["code"]] = node
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
            for line_index, line in enumerate(lines):
                if len(line) < 2:
                    continue
                from_values, to_values, edge_values = path_feature_to_nodes_and_edge(
                    feature,
                    index,
                    line_index,
                    world_key,
                    line,
                    transformer,
                    path_kind,
                )
                from_node = upsert_node(from_values)
                to_node = upsert_node(to_values)
                edge_key = (int(from_node["id"]), int(to_node["id"]))
                if edge_key in existing_edges:
                    edges_skipped += 1
                    continue
                if not dry_run:
                    connection.execute(
                        insert(spatial_edges).values(
                            from_node_id=edge_key[0],
                            to_node_id=edge_key[1],
                            **edge_values,
                        )
                    )
                existing_edges.add(edge_key)
                edges_created += 1
                features_imported += 1

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
    return {
        "code": code,
        "name": name,
        "node_type": node_type,
        "parent_id": None,
        "x": centroid_x,
        "y": 0.0,
        "z": centroid_z,
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
    x, z = projector.project(float(coordinates[0]), float(coordinates[1]))
    code = stable_code(world_key, node_type, properties, index)
    return {
        "code": code,
        "name": feature_name(properties, code),
        "node_type": node_type,
        "parent_id": None,
        "x": x,
        "y": numeric_tag(properties.get("ele")) or 0.0,
        "z": z,
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


def path_feature_to_nodes_and_edge(
    feature: dict[str, Any],
    feature_index: int,
    line_index: int,
    world_key: str,
    line: list[Any],
    projector: LocalProjector,
    path_kind: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    properties = feature.get("properties") or {}
    start = line[0]
    end = line[-1]
    start_x, start_z = projector.project(float(start[0]), float(start[1]))
    end_x, end_z = projector.project(float(end[0]), float(end[1]))
    distance = path_distance(line, projector)
    base_minutes = max(0.1, distance / 78.0)
    edge_node_type = f"{path_kind}_point"
    base_code = stable_code(world_key, path_kind, properties, feature_index)
    path_name = feature_name(properties, base_code)
    from_code = short_code(f"{base_code}_a{line_index}")
    to_code = short_code(f"{base_code}_b{line_index}")
    node_common = {
        "node_type": edge_node_type,
        "parent_id": None,
        "y": 0.0,
        "radius": 2.0,
        "capacity": 0,
        "status": "open",
        "properties": {
            "coordinate_unit": "meters",
            "source": "geojson",
            "world_key": world_key,
            "campus_key": world_key,
            "osm_tags": compact_osm_tags(properties),
            "path_name": path_name,
            "path_kind": path_kind,
            "real_world": True,
        },
    }
    from_values = {
        **node_common,
        "code": from_code,
        "name": f"{path_name} 起点",
        "x": start_x,
        "z": start_z,
    }
    to_values = {
        **node_common,
        "code": to_code,
        "name": f"{path_name} 终点",
        "x": end_x,
        "z": end_z,
    }
    edge_values = {
        "distance_meters": distance,
        "base_minutes": base_minutes,
        "bidirectional": not is_oneway(properties),
        "status": "open",
        "congestion_factor": 1.0,
        "weather_factor": 1.0,
        "properties": {
            "source": "geojson",
            "world_key": world_key,
            "campus_key": world_key,
            "osm_tags": compact_osm_tags(properties),
            "path_kind": path_kind,
            "geometry": [
                [round(projector.project(float(point[0]), float(point[1]))[0], 3), 0.0, round(projector.project(float(point[0]), float(point[1]))[1], 3)]
                for point in line
            ],
            "real_world": True,
        },
    }
    return from_values, to_values, edge_values


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
    parser.add_argument("--campus-key", help="Backward-compatible alias for --world-key.")
    parser.add_argument("--origin-lat", type=float)
    parser.add_argument("--origin-lon", type=float)
    parser.add_argument("--dry-run", action="store_true")
    return parser
