import json
import urllib.request
import urllib.parse
import sys
from pathlib import Path

# Bounding Box for Tsinghua University Main Campus (清华园)
# south=39.993, west=116.310, north=40.012, east=116.335
BBOX = "39.993,116.310,40.012,116.335"

OVERPASS_QUERY = f"""
[out:json][timeout:60];
(
  way["building"]({BBOX});
  relation["building"]({BBOX});
  way["highway"~"footway|path|pedestrian|steps|cycleway|service|residential|living_street|unclassified|tertiary|secondary|primary"]({BBOX});
  way["leisure"~"garden|park|pitch|track"]({BBOX});
  way["landuse"~"grass|forest|meadow|reservoir"]({BBOX});
  way["natural"~"water|wood"]({BBOX});
  way["amenity"]({BBOX});
  node["amenity"]({BBOX});
);
out body;
>;
out skel qt;
"""

OVERPASS_URLS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

def fetch_overpass_data(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    for url in OVERPASS_URLS:
        print(f"尝试从 {url} 下载清华大学 OSM 数据...")
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "CampusAgentSimulation/1.0"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if "elements" in result and len(result["elements"]) > 0:
                    print(f"成功获取 {len(result['elements'])} 个 OSM 元素！")
                    return result
        except Exception as err:
            print(f"从 {url} 下载失败: {err}")
    raise RuntimeError("所有 Overpass API 镜像均响应超时或失败。")


def osm_to_geojson(osm_data: dict) -> dict:
    from app.world_runtime.clock import get_world_now
    fetched_at = get_world_now().isoformat()
    license_text = "ODbL / OpenStreetMap contributors"

    nodes = {}
    ways = {}
    relations = {}

    for elem in osm_data.get("elements", []):
        elem_type = elem.get("type")
        elem_id = elem.get("id")
        if elem_type == "node":
            nodes[elem_id] = (elem.get("lon"), elem.get("lat"), elem.get("tags", {}))
        elif elem_type == "way":
            ways[elem_id] = (elem.get("nodes", []), elem.get("tags", {}))
        elif elem_type == "relation":
            relations[elem_id] = elem

    features = []

    # Process nodes with tags (POIs, amenities)
    for node_id, (lon, lat, tags) in nodes.items():
        if not tags:
            continue
        if lon is None or lat is None:
            continue
        feature_props = {
            "@id": f"node/{node_id}",
            "osm_id": node_id,
            "fetched_at": fetched_at,
            "license": license_text,
            **tags,
        }
        features.append({
            "type": "Feature",
            "properties": feature_props,
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            }
        })

    # Process ways (buildings, roads, areas)
    processed_way_ids = set()
    for way_id, (node_ids, tags) in ways.items():
        if not tags or len(node_ids) < 2:
            continue
        coords = []
        valid = True
        for nid in node_ids:
            if nid in nodes and nodes[nid][0] is not None and nodes[nid][1] is not None:
                coords.append([nodes[nid][0], nodes[nid][1]])
            else:
                valid = False
                break
        if not valid or len(coords) < 2:
            continue

        processed_way_ids.add(way_id)
        feature_props = {
            "@id": f"way/{way_id}",
            "osm_id": way_id,
            "fetched_at": fetched_at,
            "license": license_text,
            **tags,
        }

        # Is it a closed area (polygon) or line (highway/path)?
        is_closed = (coords[0] == coords[-1]) and len(coords) >= 4
        is_area = is_closed and ("building" in tags or "leisure" in tags or "landuse" in tags or "natural" in tags or "amenity" in tags)

        if is_area:
            features.append({
                "type": "Feature",
                "properties": feature_props,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords]
                }
            })
        else:
            features.append({
                "type": "Feature",
                "properties": feature_props,
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords
                }
            })

    # Process relations (building / multipolygons)
    for rel_id, rel in relations.items():
        tags = rel.get("tags", {})
        if not tags:
            continue
        members = rel.get("members", [])
        outer_way_ids = [m["ref"] for m in members if m.get("type") == "way" and m.get("role") in ("outer", "main", "")]
        inner_way_ids = [m["ref"] for m in members if m.get("type") == "way" and m.get("role") == "inner"]

        outer_rings = assemble_ring_from_ways(outer_way_ids, ways, nodes)
        inner_rings = assemble_ring_from_ways(inner_way_ids, ways, nodes)

        if not outer_rings:
            continue

        feature_props = {
            "@id": f"relation/{rel_id}",
            "relation_id": rel_id,
            "osm_id": rel_id,
            "fetched_at": fetched_at,
            "license": license_text,
            **tags,
        }

        if len(outer_rings) == 1 and not inner_rings:
            features.append({
                "type": "Feature",
                "properties": feature_props,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [outer_rings[0]]
                }
            })
        else:
            polygon_list = []
            for outer in outer_rings:
                polygon_list.append([outer] + inner_rings)
            features.append({
                "type": "Feature",
                "properties": feature_props,
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": polygon_list
                }
            })

    return {
        "type": "FeatureCollection",
        "features": features
    }


def assemble_ring_from_ways(way_refs: list[int], ways: dict, nodes: dict) -> list[list[list[float]]]:
    way_coords_list = []
    for wid in way_refs:
        if wid in ways:
            nids, _ = ways[wid]
            coords = [[nodes[nid][0], nodes[nid][1]] for nid in nids if nid in nodes and nodes[nid][0] is not None and nodes[nid][1] is not None]
            if len(coords) >= 2:
                way_coords_list.append(coords)
    if not way_coords_list:
        return []

    rings = []
    current_ring = way_coords_list.pop(0)

    while way_coords_list:
        joined = False
        for i, segment in enumerate(way_coords_list):
            if current_ring[-1] == segment[0]:
                current_ring.extend(segment[1:])
                way_coords_list.pop(i)
                joined = True
                break
            elif current_ring[-1] == segment[-1]:
                current_ring.extend(list(reversed(segment[:-1])))
                way_coords_list.pop(i)
                joined = True
                break
            elif current_ring[0] == segment[-1]:
                current_ring = segment[:-1] + current_ring
                way_coords_list.pop(i)
                joined = True
                break
            elif current_ring[0] == segment[0]:
                current_ring = list(reversed(segment[1:])) + current_ring
                way_coords_list.pop(i)
                joined = True
                break
        if not joined:
            if len(current_ring) >= 4 and current_ring[0] == current_ring[-1]:
                rings.append(current_ring)
            current_ring = way_coords_list.pop(0)

    if len(current_ring) >= 4 and current_ring[0] == current_ring[-1]:
        rings.append(current_ring)

    return rings


def main():
    output_dir = Path("data/geo")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "tsinghua_main.geojson"

    osm_data = fetch_overpass_data(OVERPASS_QUERY)
    geojson_data = osm_to_geojson(osm_data)

    print(f"生成的 GeoJSON 包含 {len(geojson_data['features'])} 个 Feature。")
    output_path.write_text(json.dumps(geojson_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已成功保存至 {output_path}")

if __name__ == "__main__":
    main()
