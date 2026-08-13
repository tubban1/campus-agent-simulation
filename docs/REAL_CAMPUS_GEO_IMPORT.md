# 真实世界地理信息导入

本文说明如何把真实存在的学校、街区、城市片区、山川湖海、建筑、街道和公共设施导入当前空间真值层。

导入器优先服务校园数字孪生，但不再局限于校园。推荐先导入一个小片区，验证建筑、道路、水体、绿地、交通线和 POI 的渲染与移动逻辑，再扩大尺度。

## 数据源优先级

1. OpenStreetMap / Overpass：首选，适合导出建筑、道路、水系、绿地、边界、交通和 POI。
2. 学校/城市/政府开放 GIS：用于校正楼名、道路等级、行政边界、设施属性和用地功能。
3. Microsoft Global ML Building Footprints：用于补齐 OSM 缺失的建筑轮廓。
4. Natural Earth：适合国家、海岸线和大尺度自然地理。
5. LiDAR / DEM：后期用于地形、高度、坡度和真实 3D 地形；第一版不依赖。

中国校园建议先用 OSM 小范围 bbox 做 MVP 验证。高德和百度更适合搜索、路线、底图展示、
人工核对和 POI 参考；通常不应作为批量抽取建筑 footprint、道路拓扑和本地几何数据库的主要来源。

## 导出 OSM GeoJSON

在 Overpass Turbo 里选择目标范围后，可使用类似查询：

```overpass
[out:json][timeout:25];
(
  way["building"]({{bbox}});
  relation["building"]({{bbox}});
  way["highway"~"footway|path|pedestrian|steps|cycleway|service|residential|living_street|unclassified|tertiary|secondary|primary|trunk|motorway"]({{bbox}});
  way["railway"~"rail|light_rail|subway|tram|monorail"]({{bbox}});
  way["waterway"~"river|stream|canal|ditch|drain"]({{bbox}});
  way["leisure"~"garden|park|pitch|track"]({{bbox}});
  way["landuse"~"grass|forest|meadow|orchard|reservoir"]({{bbox}});
  way["natural"~"water|wood|scrub|peak|cliff|ridge|valley|coastline"]({{bbox}});
  way["amenity"~"school|university|college|library|canteen|restaurant|cafe|hospital|clinic|pharmacy|police|fire_station|townhall|marketplace|bus_station|ferry_terminal|parking"]({{bbox}});
  node["amenity"~"school|university|college|library|canteen|restaurant|cafe|hospital|clinic|pharmacy|police|fire_station|townhall|marketplace|bus_station|ferry_terminal|parking"]({{bbox}});
);
out body;
>;
out skel qt;
```

然后使用 Overpass Turbo 的 `Export -> GeoJSON` 保存为本地文件，例如：

```text
data/geo/eth_zentrum.geojson
```

## 典型导入数据结构

导入文件应是一个 GeoJSON `FeatureCollection`。通常不是只放一栋楼或一个湖，而是把一个区域内的建筑、道路、水体、绿地和 POI 一起导出：

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "@id": "way/123",
        "building": "university",
        "name": "图书馆",
        "building:levels": "5"
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[8.5480, 47.3764], [8.5483, 47.3764], [8.5483, 47.3766], [8.5480, 47.3764]]]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "@id": "way/456",
        "highway": "footway",
        "name": "主步道"
      },
      "geometry": {
        "type": "LineString",
        "coordinates": [[8.5479, 47.3763], [8.5484, 47.3767]]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "@id": "way/789",
        "natural": "water",
        "name": "湖"
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[8.5490, 47.3760], [8.5495, 47.3760], [8.5495, 47.3764], [8.5490, 47.3760]]]
      }
    }
  ]
}
```

## 导入粒度

推荐按区域整体导入：

- 一个校园；
- 一个街区；
- 一个城市片区；
- 一个湖区或公园周边；
- 一个研究实验范围的 bbox。

单个建筑、湖泊、道路也可以导入，但更适合后期补丁或人工校正，例如某栋楼缺失、楼名错误、湖岸线需要替换。

## 导入命令

先做 dry run：

```bash
source venv/bin/activate
python scripts/import_real_world_geojson.py data/geo/eth_zentrum.geojson --world-key eth_zentrum --dry-run
```

确认摘要后正式导入：

```bash
python scripts/import_real_world_geojson.py data/geo/eth_zentrum.geojson --world-key eth_zentrum
```

如果需要固定局部坐标原点，可传入：

```bash
python scripts/import_real_world_geojson.py data/geo/eth_zentrum.geojson \
  --world-key eth_zentrum \
  --origin-lat 47.3764 \
  --origin-lon 8.5480
```

统一使用 `scripts/import_real_world_geojson.py`；不再维护旧导入入口。

## 当前导入规则

- `building=*` 会导入为 `spatial_nodes.node_type = building`。
- `leisure/landuse/natural` 中的花园、草地、森林、操场等会导入为 `outdoor_area`。
- `natural=water`、`water=*`、`landuse=reservoir` 会导入为 `water_area`。
- 山脊、山峰、悬崖、山谷等会导入为 `terrain`。
- 行政边界、保护区和城市/街区等会导入为 `boundary_area`。
- 学校、医院、餐馆、车站、政府机构等会导入为 `poi`。
- `highway=*` 会导入为路径端点和 `spatial_edges`，边属性含 `path_kind=path`。
- `railway=*` 会导入为铁路端点和 `spatial_edges`，边属性含 `path_kind=rail`。
- `waterway=*` 会导入为水系端点和 `spatial_edges`，边属性含 `path_kind=waterway`。
- 经纬度会投影为以校园中心为原点的米制 `x/z` 坐标。
- polygon footprint、OSM tags、面积、高度估算会保存在 `properties`。

## 边界

第一版导入器只建立真实空间骨架，不会覆盖现有 Agent、行动计划或校园日程。

导入后仍需要人工校正：

- 楼宇中文名和功能；
- 建筑入口；
- 路径交叉点拆分；
- 室内空间抽象卡片映射；
- 食堂、图书馆、教室、宿舍等容量规则。

城市或自然场景还需要人工校正：

- 道路交叉点拆分；
- 机动车、步行、铁路、水路的可达性规则；
- 桥梁、隧道、楼梯、电梯和无障碍通行；
- 水域、悬崖、高速路等不可通行边界；
- DEM/LiDAR 高程和坡度。
