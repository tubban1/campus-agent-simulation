/**
 * MapLibre Map Component Module
 * Manages 2D MapLibre campus map, OSM raster layer, spatial nodes, agent markers, and interactive event handlers.
 */
import { $, avatarFiles, getAvatarUrl, escapeHtml, WorldStore } from "./world-store.js?v=20260816-fix-click";

let maplibreInstance = null;
let mapActivePopup = null;
const agentAvatarMarkers = new Map();
let latestAgentFeatures = [];
const agentDisplayPositions = new Map();
let agentAnimationRaf = null;

function animateAgentPositions() {
  if (!maplibreInstance) return;
  let needsMoreFrames = false;
  const alpha = 0.2;

  agentDisplayPositions.forEach((pos) => {
    const dx = pos.targetLng - pos.currentLng;
    const dy = pos.targetLat - pos.currentLat;
    if (Math.abs(dx) > 0.0000001 || Math.abs(dy) > 0.0000001) {
      pos.currentLng += dx * alpha;
      pos.currentLat += dy * alpha;
      needsMoreFrames = true;
    } else {
      pos.currentLng = pos.targetLng;
      pos.currentLat = pos.targetLat;
    }
  });

  if (latestAgentFeatures) {
    const interpolatedFeatures = latestAgentFeatures.map(feat => {
      const id = feat.properties.resident_id;
      const pos = agentDisplayPositions.get(id);
      if (!pos) return feat;
      return {
        ...feat,
        geometry: {
          ...feat.geometry,
          coordinates: [pos.currentLng, pos.currentLat]
        }
      };
    });

    if (maplibreInstance && maplibreInstance.getSource('campus-agents')) {
      maplibreInstance.getSource('campus-agents').setData({
        type: 'FeatureCollection',
        features: interpolatedFeatures
      });
    }
    syncAgentAvatarMarkers(interpolatedFeatures);
  }

  if (needsMoreFrames) {
    agentAnimationRaf = requestAnimationFrame(animateAgentPositions);
  } else {
    agentAnimationRaf = null;
  }
}

function avatarFileFor(residentId) {
  return avatarFiles[(Number(residentId || 1) - 1 + avatarFiles.length) % avatarFiles.length];
}

function avatarOffset(index, count) {
  if (count <= 1) return [0, 0];
  const ring = Math.floor(index / 6);
  const radius = 18 + ring * 12;
  const angle = (index % 6) / Math.min(count, 6) * Math.PI * 2;
  return [Math.cos(angle) * radius, Math.sin(angle) * radius];
}

const nodeLabelMarkers = new Map();

function syncNodeLabelMarkers() {
  if (nodeLabelMarkers.size > 0) {
    nodeLabelMarkers.forEach(marker => marker.remove());
    nodeLabelMarkers.clear();
  }
}

function syncAgentAvatarMarkers(agentFeatures) {
  if (!maplibreInstance) return;
  const groups = new Map();
  agentFeatures.forEach(feature => {
    const key = feature.properties.current_node_id != null
      ? `node:${feature.properties.current_node_id}`
      : feature.geometry.coordinates.map(value => Number(value).toFixed(5)).join(":");
    const items = groups.get(key) || [];
    items.push(feature);
    groups.set(key, items);
  });
  const liveIds = new Set(agentFeatures.map(feature => String(feature.properties.resident_id)));
  agentAvatarMarkers.forEach((marker, id) => {
    if (!liveIds.has(id)) {
      marker.remove();
      agentAvatarMarkers.delete(id);
    }
  });
  groups.forEach(items => items.forEach((feature, index) => {
    const props = feature.properties;
    const id = String(props.resident_id);
    const avatarUrl = getAvatarUrl(props.resident_id);
    let marker = agentAvatarMarkers.get(id);
    if (!marker) {
      const element = document.createElement("div");
      element.className = "map-agent-marker-wrapper";
      element.title = `${props.name} · ${props.location}`;
      element.innerHTML = `
        <div class="map-agent-avatar">
          <img src="${avatarUrl}" alt="${escapeHtml(props.name)}">
        </div>
        <div class="map-agent-label">${escapeHtml(props.name)}</div>
      `;
      element.onclick = event => {
        event.stopPropagation();
        if (typeof window.focusAgentOnMap === "function") window.focusAgentOnMap(props.resident_id);
      };
      marker = new maplibregl.Marker({ element, anchor: "center" }).setLngLat(feature.geometry.coordinates).addTo(maplibreInstance);
      agentAvatarMarkers.set(id, marker);
    }
    const element = marker.getElement();
    element.style.display = "flex";
    element.style.zIndex = "1000";
    const [offsetX, offsetY] = avatarOffset(index, items.length);
    element.style.marginLeft = `${offsetX}px`;
    element.style.marginTop = `${offsetY}px`;
    const image = element.querySelector("img");
    if (image) image.src = avatarUrl;
    const label = element.querySelector(".map-agent-label");
    if (label && props.name) label.textContent = props.name;
    marker.setLngLat(feature.geometry.coordinates);
  }));
}

function getWgs84BoundsArray(bounds) {
  if (!bounds) return null;
  if (Array.isArray(bounds) && bounds.length === 4) return bounds;
  if (typeof bounds === "object" && bounds.min_lon != null && bounds.min_lat != null && bounds.max_lon != null && bounds.max_lat != null) {
    return [bounds.min_lon, bounds.min_lat, bounds.max_lon, bounds.max_lat];
  }
  return null;
}

export function resolveCoordinates(entity, originLon = 116.3221954, originLat = 40.0023657) {
  if (!entity) return null;
  if (entity.longitude != null && entity.latitude != null) {
    const lon = Number(entity.longitude);
    const lat = Number(entity.latitude);
    if (!Number.isNaN(lon) && !Number.isNaN(lat)) return [lon, lat];
  }
  if (entity.x != null || entity.z != null || entity.y != null) {
    const x = Number(entity.x || 0);
    const z = Number(entity.z ?? entity.y ?? 0);
    const latRads = (originLat * Math.PI) / 180;
    const metersPerDegLon = 111320 * Math.cos(latRads);
    const metersPerDegLat = 110574;
    const lon = originLon + x / metersPerDegLon;
    const lat = originLat + z / metersPerDegLat;
    return [lon, lat];
  }
  return null;
}

export function initOrUpdateMapLibreMap(spatialWorlds = [], { fitToBounds = false } = {}) {
  const container = $("maplibreContainer");
  if (!container || typeof maplibregl === "undefined") return;

  const currentWorldKey = WorldStore.selectedWorldKey || "tsinghua_main";
  const spatialScene = WorldStore.spatialScene;

  const activeWorld = (spatialWorlds || []).find(w => w.world_key === currentWorldKey) || {};
  const boundsArr = getWgs84BoundsArray(spatialScene?.wgs84_bounds) || getWgs84BoundsArray(activeWorld?.wgs84_bounds);

  let centerLon = 116.3221954, centerLat = 40.0085;
  if (WorldStore.spatialAgents && WorldStore.spatialAgents.size > 0) {
    const agentCoords = Array.from(WorldStore.spatialAgents.values())
      .map(a => resolveCoordinates(a, centerLon, centerLat))
      .filter(Boolean);
    if (agentCoords.length > 0) {
      centerLon = agentCoords.reduce((sum, v) => sum + v[0], 0) / agentCoords.length;
      centerLat = agentCoords.reduce((sum, v) => sum + v[1], 0) / agentCoords.length;
    }
  } else if (fitToBounds && boundsArr) {
    const [minLon, minLat, maxLon, maxLat] = boundsArr;
    centerLon = (minLon + maxLon) / 2;
    centerLat = (minLat + maxLat) / 2;
  } else if (spatialScene && spatialScene.origin_lon && spatialScene.origin_lat) {
    centerLon = Number(spatialScene.origin_lon);
    centerLat = Number(spatialScene.origin_lat);
  }

  const mapTileUrl = window.MAP_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

  if (!maplibreInstance) {
    maplibreInstance = new maplibregl.Map({
      container: 'maplibreContainer',
      style: {
        version: 8,
        glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        sources: {
          'osm-tiles': {
            type: 'raster',
            tiles: [mapTileUrl],
            tileSize: 256,
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          }
        },
        layers: [{
          id: 'osm-tiles-layer',
          type: 'raster',
          source: 'osm-tiles',
          minzoom: 0,
          maxzoom: 19
        }]
      },
      center: [centerLon, centerLat],
      zoom: 15.5,
      // Keep the page's vertical wheel gesture available for section paging.
      scrollZoom: false
    });
    maplibreInstance.scrollZoom.disable();
    maplibreInstance.addControl(new maplibregl.NavigationControl(), 'top-right');

    // The 2D and 3D modes share one local spatial window.  Moving the map to
    // a different campus area asks the application for that window only.
    let viewportMoveTimer = null;
    const updateViewportBounds = () => {
      if (!maplibreInstance) return;
      const bounds = maplibreInstance.getBounds();
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();
      const originLon = Number(spatialScene?.origin_lon || 116.3221954);
      const originLat = Number(spatialScene?.origin_lat || 40.0023657);
      const latRads = (originLat * Math.PI) / 180;
      const metersPerDegLon = 111320 * Math.cos(latRads);
      const metersPerDegLat = 110574;

      const minX = (sw.lng - originLon) * metersPerDegLon;
      const maxX = (ne.lng - originLon) * metersPerDegLon;
      const minZ = (sw.lat - originLat) * metersPerDegLat;
      const maxZ = (ne.lat - originLat) * metersPerDegLat;

      if (typeof window.onMapViewportChanged === "function") {
        window.onMapViewportChanged({ minX, maxX, minZ, maxZ });
      }
    };

    maplibreInstance.on('move', () => {
      clearTimeout(viewportMoveTimer);
      viewportMoveTimer = setTimeout(updateViewportBounds, 220);
    });

    maplibreInstance.on('moveend', () => {
      clearTimeout(viewportMoveTimer);
      updateViewportBounds();
    });

    maplibreInstance.on('zoomend', () => {
      syncAgentAvatarMarkers(latestAgentFeatures);
      updateViewportBounds();
    });

    maplibreInstance.on('styledata', () => {
      if (maplibreInstance && maplibreInstance.isStyleLoaded()) {
        const currentScene = WorldStore.spatialScene;
        if (currentScene && Array.isArray(currentScene.nodes)) {
          initOrUpdateMapLibreMap(WorldStore.spatialWorlds || []);
        }
      }
    });

    // Handle background click on blank map area to trigger physical map environment event
    maplibreInstance.on('click', (e) => {
      if (!maplibreInstance) return;
      const bbox = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
      const hitLayers = ['campus-nodes-layer', 'campus-building-nodes-layer', 'campus-poi-nodes-layer', 'campus-agents-layer'].filter(l => maplibreInstance.getLayer(l));
      const hitFeatures = hitLayers.length ? maplibreInstance.queryRenderedFeatures(bbox, { layers: hitLayers }) : [];
      if (!hitFeatures || hitFeatures.length === 0) {
        if (typeof window.triggerMapEventAt === "function") {
          window.triggerMapEventAt(e.lngLat.lng, e.lngLat.lat);
        }
      }
    });
  }

  if (fitToBounds && boundsArr) {
    const [minLon, minLat, maxLon, maxLat] = boundsArr;
    maplibreInstance.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 40, duration: 800 });
  } else if (fitToBounds) {
    maplibreInstance.flyTo({ center: [centerLon, centerLat], zoom: 15.5 });
  }

  const currentScene = WorldStore.spatialScene;
  if (currentScene && Array.isArray(currentScene.nodes)) {
    const sceneOriginLon = Number(currentScene.origin_lon || 116.3221954);
    const sceneOriginLat = Number(currentScene.origin_lat || 40.0023657);
    const nodeMap = new Map((currentScene.nodes || []).map(n => [n.id, n]));
    const physicalByNode = new Map((currentScene.physical_states || []).map(p => [Number(p.node_id), p]));
    // Physical state is a separate runtime observation layer. Join it to the
    // immutable OSM node graph only for display; it must never overwrite map
    // geometry or be inferred from a legacy time-of-day template.
    const isLandmarkName = (name) => {
      if (!name || typeof name !== 'string') return false;
      if (name.startsWith('tsinghua_') || name.startsWith('node_') || name.startsWith('building_')) return false;
      return /图书馆|主楼|学堂|食堂|餐厅|体育馆|大礼堂|二校门|博物馆|清芬|听涛|紫荆|观畴|桃李|逸夫|新水|理科楼|西体|东体|清华医院|艺术馆/.test(name);
    };

    const isHumanReadableName = (name) => {
      if (!name || typeof name !== 'string') return false;
      if (name.startsWith('tsinghua_') || name.startsWith('node_') || name.startsWith('building_')) return false;
      return true;
    };

    const geojsonFeatures = currentScene.nodes
      .filter(n => ["building", "poi", "outdoor_area"].includes(n.node_type))
      .map(n => {
        const coords = resolveCoordinates(n, sceneOriginLon, sceneOriginLat);
        if (!coords) return null;
        const physical = physicalByNode.get(Number(n.id)) || {};
        const rawName = String(n.name || "").trim();
        const validName = isHumanReadableName(rawName) ? rawName : "";
        const capacity = Number(n.capacity || 0);

        let tier = 2; // Default to minor POI (minzoom: 12.0)
        if (isLandmarkName(rawName) || capacity >= 300) {
          tier = 0; // Key Landmark (minzoom: 0)
        } else if (n.node_type === "building" && validName) {
          tier = 1; // Named Building (minzoom: 10.0)
        }

        // Suppress raw unnamed OSM technical nodes from cluttering the visual map
        if (tier === 2 && !validName) {
          return null;
        }

        return ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: coords },
          properties: {
            id: n.id,
            name: validName || "POI",
            type: n.node_type || "poi",
            tier: tier,
            access_status: physical.access_status || n.status || "open",
            crowd_density: Number(physical.crowd_density || 0),
            precipitation: Number(physical.precipitation || 0),
            noise_db: Number(physical.noise_db || 0)
          }
        });
      })
      .filter(Boolean);

    const edgeFeatures = (currentScene.edges || [])
      .map(e => {
        const fromNode = nodeMap.get(e.from_node_id);
        const toNode = nodeMap.get(e.to_node_id);
        const fromCoords = resolveCoordinates(fromNode, sceneOriginLon, sceneOriginLat);
        const toCoords = resolveCoordinates(toNode, sceneOriginLon, sceneOriginLat);
        if (!fromCoords || !toCoords) return null;
        return {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [fromCoords, toCoords]
          },
          properties: {
            id: e.id,
            weather_factor: Number(e.weather_factor || 1.0),
            congestion_factor: Number(e.congestion_factor || 1.0),
            high_resistance: Number(e.weather_factor || 1.0) > 1.25,
            closed: String(e.status || 'open') !== 'open'
          }
        };
      })
      .filter(Boolean);

    const updateSource = () => {
      if (!maplibreInstance) return;
      if (!maplibreInstance.isStyleLoaded()) return;

      // 0. Spatial Edges & Weather Resistance Layer
      try {
        if (!maplibreInstance.getSource('campus-edges')) {
          maplibreInstance.addSource('campus-edges', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: edgeFeatures }
          });
        } else {
          maplibreInstance.getSource('campus-edges').setData({
            type: 'FeatureCollection',
            features: edgeFeatures
          });
        }

        if (!maplibreInstance.getLayer('campus-edges-layer')) {
          maplibreInstance.addLayer({
            id: 'campus-edges-layer',
            type: 'line',
            source: 'campus-edges',
            paint: {
              'line-color': '#0288d1',
              'line-width': ['interpolate', ['linear'], ['zoom'], 12, 0.75, 16, 1.2, 18, 1.8],
              'line-opacity': 0.45
            }
          });
        }

        if (!maplibreInstance.getLayer('campus-edges-closed-layer')) {
          maplibreInstance.addLayer({
            id: 'campus-edges-closed-layer', type: 'line', source: 'campus-edges',
            filter: ['==', ['get', 'closed'], true],
            paint: {
              'line-color': '#ffb74d',
              'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1.0, 18, 1.8],
              'line-opacity': 0.6
            }
          });
        }

        if (!maplibreInstance.getLayer('campus-edges-resistance-layer')) {
          maplibreInstance.addLayer({
            id: 'campus-edges-resistance-layer',
            type: 'line',
            source: 'campus-edges',
            filter: ['==', ['get', 'high_resistance'], true],
            paint: {
              'line-color': '#0288d1',
              'line-width': ['interpolate', ['linear'], ['zoom'], 14, 0.8, 18, 1.5],
              'line-opacity': 0.3
            }
          });
        }
      } catch (edgeErr) {
        console.warn("MapLibre edges layer update warning:", edgeErr);
      }

      // 1. POIs and Building Nodes Layer (3 Hierarchical Tiers based on zoom)
      try {
        if (maplibreInstance.getSource('campus-nodes')) {
          maplibreInstance.getSource('campus-nodes').setData({
            type: 'FeatureCollection',
            features: geojsonFeatures
          });
        } else {
          maplibreInstance.addSource('campus-nodes', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: geojsonFeatures }
          });

          // Tier 0: 大节点 · 核心地标 (Visible at all zoom levels, radius: 7px)
          maplibreInstance.addLayer({
            id: 'campus-nodes-layer',
            type: 'circle',
            source: 'campus-nodes',
            minzoom: 0,
            filter: ['==', ['get', 'tier'], 0],
            paint: {
              'circle-radius': 7,
              'circle-color': ['case', ['==', ['get', 'access_status'], 'closed'], '#d84315', ['>=', ['get', 'crowd_density'], 0.8], '#f9a825', '#1e88e5'],
              'circle-stroke-width': 2,
              'circle-stroke-color': '#ffffff'
            }
          });

          // Tier 1: 中节点 · 具名建筑 (Visible when zoom > 14.1, radius: 5px)
          maplibreInstance.addLayer({
            id: 'campus-building-nodes-layer',
            type: 'circle',
            source: 'campus-nodes',
            minzoom: 14.1,
            filter: ['==', ['get', 'tier'], 1],
            paint: {
              'circle-radius': 5,
              'circle-color': ['case', ['==', ['get', 'access_status'], 'closed'], '#d84315', ['>=', ['get', 'crowd_density'], 0.8], '#f9a825', '#5c6bc0'],
              'circle-opacity': 0.9,
              'circle-stroke-width': 1.5,
              'circle-stroke-color': '#ffffff'
            }
          });

          // Tier 2: 小节点 · 设施/次要POI (Visible when zoom > 15.4, radius: 3px)
          maplibreInstance.addLayer({
            id: 'campus-poi-nodes-layer',
            type: 'circle',
            source: 'campus-nodes',
            minzoom: 15.4,
            filter: ['==', ['get', 'tier'], 2],
            paint: {
              'circle-radius': 3,
              'circle-color': '#009688',
              'circle-opacity': 0.8,
              'circle-stroke-width': 1,
              'circle-stroke-color': '#ffffff'
            }
          });

          const openNodePopup = (e) => {
            if (!e.features || !e.features.length) return;
            const feat = e.features[0];
            const props = feat.properties;
            const coords = feat.geometry.coordinates.slice();
            const latestScene = WorldStore.spatialScene;
            const nodeObj = (latestScene?.nodes || []).find(n => String(n.id) === String(props.id)) || props;

            const html = `
              <div class="map-popup-card">
                <div class="popup-title">🏢 ${escapeHtml(nodeObj.name || "建筑/节点")}</div>
                <div class="popup-sub">编号: ${escapeHtml(nodeObj.code || "")} · 类型: ${escapeHtml(nodeObj.node_type || "POI")}</div>
                <div class="popup-body">
                  <div><strong>核定容量：</strong>${nodeObj.capacity ?? "不限"} 人</div>
                  <div><strong>经纬度坐标：</strong>${coords[0].toFixed(5)}, ${coords[1].toFixed(5)}</div>
                  <div><strong>运营状态：</strong><span style="color:#00e676;font-weight:bold;">${escapeHtml(nodeObj.status || "正常开放")}</span></div>
                  <div><strong>实时物理状态：</strong>${escapeHtml(props.access_status || "open")} · 拥挤 ${Math.round(Number(props.crowd_density || 0) * 100)}% · 噪声 ${Math.round(Number(props.noise_db || 0))}dB</div>
                </div>
                <div class="popup-actions" style="display:flex;flex-direction:column;gap:6px;">
                  <button onclick="window.selectNodeDestination('${nodeObj.id}')">🎯 设为 Agent 目标导航点</button>
                  <button onclick="if(typeof window.openLocationDetails==='function')window.openLocationDetails('${escapeHtml(nodeObj.name)}')">📜 查看地点交互历史（谁何时做了什么）</button>
                </div>
              </div>
            `;

            if (mapActivePopup) mapActivePopup.remove();
            mapActivePopup = new maplibregl.Popup({ offset: 12, closeButton: true })
              .setLngLat(coords)
              .setHTML(html)
              .addTo(maplibreInstance);
          };
          ['campus-nodes-layer', 'campus-building-nodes-layer', 'campus-poi-nodes-layer'].forEach(layerId => {
            maplibreInstance.on('click', layerId, openNodePopup);
            maplibreInstance.on('mouseenter', layerId, () => { maplibreInstance.getCanvas().style.cursor = 'pointer'; });
            maplibreInstance.on('mouseleave', layerId, () => { maplibreInstance.getCanvas().style.cursor = ''; });
          });
        }
      } catch (nodeErr) {
        console.warn("MapLibre nodes layer update warning:", nodeErr);
      }

      // 2. Agent Live Markers Layer on 2D Map
      const agentFeatures = Array.from(WorldStore.spatialAgents.values())
        .map(a => {
          const coords = resolveCoordinates(a, sceneOriginLon, sceneOriginLat);
          if (!coords) return null;
          const resident = (WorldStore.agents || []).find(r => Number(r.id) === Number(a.resident_id)) || {};
          return {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: coords },
            properties: {
              resident_id: a.resident_id,
              name: resident.name || `Agent ${a.resident_id}`,
              role: resident.role || "校园居民",
              status: a.movement_status || "idle",
              location: a.current_node_name || "位置未知",
              current_node_id: a.current_node_id
            }
          };
        })
        .filter(Boolean);
      latestAgentFeatures = agentFeatures;

      agentFeatures.forEach(feat => {
        const id = feat.properties.resident_id;
        const targetLng = feat.geometry.coordinates[0];
        const targetLat = feat.geometry.coordinates[1];
        const existing = agentDisplayPositions.get(id);
        if (!existing) {
          agentDisplayPositions.set(id, {
            currentLng: targetLng, currentLat: targetLat,
            targetLng, targetLat
          });
        } else {
          existing.targetLng = targetLng;
          existing.targetLat = targetLat;
        }
      });

      if (!agentAnimationRaf) {
        agentAnimationRaf = requestAnimationFrame(animateAgentPositions);
      }

      // Synchronize DOM markers immediately
      syncAgentAvatarMarkers(agentFeatures);

      try {
        if (!maplibreInstance.getSource('campus-agents')) {
          maplibreInstance.addSource('campus-agents', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: agentFeatures }
          });

          maplibreInstance.addLayer({
            id: 'campus-agents-layer',
            type: 'circle',
            source: 'campus-agents',
            paint: {
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 15.5, 4, 17, 8],
              'circle-color': '#00e676',
              'circle-opacity': ['interpolate', ['linear'], ['zoom'], 15, 0, 15.5, 0.72, 17, 0.18],
              'circle-stroke-width': 2.5,
              'circle-stroke-color': '#003311',
              'circle-stroke-opacity': ['interpolate', ['linear'], ['zoom'], 15, 0, 15.5, 0.8, 17, 0]
            }
          });

          maplibreInstance.on('click', 'campus-agents-layer', (e) => {
            if (!e.features || !e.features.length) return;
            const feat = e.features[0];
            const props = feat.properties;
            const residentId = props.resident_id;
            if (typeof window.focusAgentOnMap === "function") {
              window.focusAgentOnMap(residentId);
            }
          });
          maplibreInstance.on('mouseenter', 'campus-agents-layer', () => {
            maplibreInstance.getCanvas().style.cursor = 'pointer';
          });
          maplibreInstance.on('mouseleave', 'campus-agents-layer', () => {
            maplibreInstance.getCanvas().style.cursor = '';
          });
        } else {
          maplibreInstance.getSource('campus-agents').setData({
            type: 'FeatureCollection',
            features: agentFeatures
          });
        }
      } catch (agentErr) {
        console.warn("MapLibre agents layer update warning:", agentErr);
      }
    };

    const runUpdate = () => {
      try {
        if (maplibreInstance && maplibreInstance.isStyleLoaded()) {
          updateSource();
        }
      } catch (err) {
        console.warn("MapLibre updateSource error:", err);
      }
    };

    runUpdate();
    if (maplibreInstance && !maplibreInstance.isStyleLoaded()) {
      const onStyleReady = () => {
        if (maplibreInstance && maplibreInstance.isStyleLoaded()) {
          runUpdate();
        }
      };
      maplibreInstance.once('load', onStyleReady);
      maplibreInstance.once('styledata', onStyleReady);
      setTimeout(onStyleReady, 300);
    }
  }
}

export function refreshMapLibreMarkers() {
  if (maplibreInstance) {
    initOrUpdateMapLibreMap(WorldStore.spatialWorlds || []);
  }
}

export function getMapLibreInstance() {
  return maplibreInstance;
}
