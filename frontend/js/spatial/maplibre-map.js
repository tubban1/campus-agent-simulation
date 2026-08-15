/**
 * MapLibre Map Component Module
 * Manages 2D MapLibre campus map, OSM raster layer, spatial nodes, agent markers, and interactive event handlers.
 */
import { $, avatarFiles, escapeHtml, WorldStore } from "./world-store.js?v=20260814-png-avatars";

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
    let marker = agentAvatarMarkers.get(id);
    if (!marker) {
      const element = document.createElement("div");
      element.className = "map-agent-marker-wrapper";
      element.title = `${props.name} · ${props.location}`;
      element.innerHTML = `
        <div class="map-agent-avatar">
          <img src="/avatars/${avatarFileFor(props.resident_id)}" alt="${escapeHtml(props.name)}">
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
    element.style.display = "";
    element.style.zIndex = "99";
    const [offsetX, offsetY] = avatarOffset(index, items.length);
    element.style.marginLeft = `${offsetX}px`;
    element.style.marginTop = `${offsetY}px`;
    const image = element.querySelector("img");
    if (image) image.src = `/avatars/${avatarFileFor(props.resident_id)}`;
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

  const currentWorldKey = WorldStore.selectedWorldKey || "default";
  const spatialScene = WorldStore.spatialScene;

  const activeWorld = (spatialWorlds || []).find(w => w.world_key === currentWorldKey) || {};
  const boundsArr = getWgs84BoundsArray(spatialScene?.wgs84_bounds) || getWgs84BoundsArray(activeWorld?.wgs84_bounds);

  let centerLon = 116.3221954, centerLat = 40.0023657;
  if (fitToBounds && boundsArr) {
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
      const center = maplibreInstance?.getCenter();
      if (!center) return;
      const zoom = maplibreInstance.getZoom();
      if (typeof window.requestSpatialViewportForMapBounds === 'function') {
        window.requestSpatialViewportForMapBounds(maplibreInstance.getBounds(), zoom);
      } else if (typeof window.requestSpatialViewportAtLngLat === 'function') {
        window.requestSpatialViewportAtLngLat(center.lng, center.lat, zoom);
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
    if (minLon !== maxLon && minLat !== maxLat) {
      maplibreInstance.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 45, maxZoom: 18.2 });
    } else {
      maplibreInstance.flyTo({ center: [centerLon, centerLat], zoom: 15.5 });
    }
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

        let tier = 2; // Default to minor POI (minzoom: 17.2)
        if (isLandmarkName(rawName) || capacity >= 300) {
          tier = 0; // Key Landmark (minzoom: 13.5)
        } else if (n.node_type === "building" && validName) {
          tier = 1; // Named Building (minzoom: 15.8)
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
            high_resistance: Number(e.weather_factor || 1.0) > 1.25
            , closed: String(e.status || 'open') !== 'open'
          }
        };
      })
      .filter(Boolean);

    const updateSource = () => {
      if (!maplibreInstance) return;

      // 0. Spatial Edges & Weather Resistance Layer
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
            'line-color': '#90caf9',
            'line-width': ['interpolate', ['linear'], ['zoom'], 14, 0.8, 18, 2.0],
            'line-opacity': 0.22
          }
        });
      } else {
        maplibreInstance.setPaintProperty('campus-edges-layer', 'line-color', '#90caf9');
        maplibreInstance.setPaintProperty('campus-edges-layer', 'line-opacity', 0.22);
        maplibreInstance.setPaintProperty('campus-edges-layer', 'line-width', ['interpolate', ['linear'], ['zoom'], 14, 0.8, 18, 2.0]);
      }

      if (!maplibreInstance.getLayer('campus-edges-closed-layer')) {
        maplibreInstance.addLayer({
          id: 'campus-edges-closed-layer', type: 'line', source: 'campus-edges',
          filter: ['==', ['get', 'closed'], true],
          paint: {
            'line-color': '#ffb74d',
            'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1.2, 18, 2.5],
            'line-opacity': 0.35
          }
        });
      } else {
        maplibreInstance.setPaintProperty('campus-edges-closed-layer', 'line-color', '#ffb74d');
        maplibreInstance.setPaintProperty('campus-edges-closed-layer', 'line-opacity', 0.35);
        maplibreInstance.setPaintProperty('campus-edges-closed-layer', 'line-width', ['interpolate', ['linear'], ['zoom'], 14, 1.2, 18, 2.5]);
      }

      if (!maplibreInstance.getLayer('campus-edges-resistance-layer')) {
        maplibreInstance.addLayer({
          id: 'campus-edges-resistance-layer',
          type: 'line',
          source: 'campus-edges',
          filter: ['==', ['get', 'high_resistance'], true],
          paint: {
            'line-color': '#90caf9',
            'line-width': ['interpolate', ['linear'], ['zoom'], 14, 0.8, 18, 2.0],
            'line-opacity': 0.25
          }
        });
      } else {
        maplibreInstance.setPaintProperty('campus-edges-resistance-layer', 'line-color', '#90caf9');
        maplibreInstance.setPaintProperty('campus-edges-resistance-layer', 'line-opacity', 0.25);
        maplibreInstance.setPaintProperty('campus-edges-resistance-layer', 'line-width', ['interpolate', ['linear'], ['zoom'], 14, 0.8, 18, 2.0]);
      }

      // 1. POIs and Building Nodes Layer (Hierarchical Tiers)
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

        // Tier 0: Major Landmarks / Big Nodes (Visible at all zoom levels, minzoom: 0)
        maplibreInstance.addLayer({
          id: 'campus-nodes-layer',
          type: 'circle',
          source: 'campus-nodes',
          minzoom: 0,
          filter: ['==', ['get', 'tier'], 0],
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 14, 5, 18, 9],
            'circle-color': ['case', ['==', ['get', 'access_status'], 'closed'], '#d84315', ['>=', ['get', 'crowd_density'], 0.8], '#f9a825', '#1e88e5'],
            'circle-stroke-width': 2,
            'circle-stroke-color': '#ffffff'
          }
        });

        // Tier 1: Named Buildings / Medium Nodes (Shown when zoomed in, minzoom: 14.5)
        maplibreInstance.addLayer({
          id: 'campus-building-nodes-layer',
          type: 'circle',
          source: 'campus-nodes',
          minzoom: 14.5,
          filter: ['==', ['get', 'tier'], 1],
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 14.5, 3, 18, 6],
            'circle-color': ['case', ['==', ['get', 'access_status'], 'closed'], '#d84315', ['>=', ['get', 'crowd_density'], 0.8], '#f9a825', '#5c6bc0'],
            'circle-opacity': 0.85,
            'circle-stroke-width': 1.5,
            'circle-stroke-color': '#ffffff'
          }
        });

        // Tier 2: Minor POIs / Small Nodes (Shown on high zoom detail, minzoom: 16.0)
        maplibreInstance.addLayer({
          id: 'campus-poi-nodes-layer',
          type: 'circle',
          source: 'campus-nodes',
          minzoom: 16.0,
          filter: ['==', ['get', 'tier'], 2],
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 16.0, 2, 19, 4],
            'circle-color': '#78909c',
            'circle-opacity': 0.6,
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
              <div class="popup-actions">
                <button onclick="window.selectNodeDestination('${nodeObj.id}')">🎯 设为 Agent 目标导航点</button>
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
      syncNodeLabelMarkers(geojsonFeatures);

      if (!maplibreInstance.isStyleLoaded()) {
        return;
      }

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

        try {
          maplibreInstance.addLayer({
            id: 'campus-agents-label',
            type: 'symbol',
            source: 'campus-agents',
            layout: {
              'text-field': ['get', 'name'],
              'text-size': 12,
              'text-offset': [0, -1.5],
              'text-anchor': 'bottom'
            },
            paint: {
              'text-color': '#64ffda',
              'text-opacity': ['interpolate', ['linear'], ['zoom'], 14.5, 0.4, 15.5, 1],
              'text-halo-color': '#000000',
              'text-halo-width': 2
            }
          });
        } catch (labelErr) {
          console.warn("MapLibre agent symbol label omitted:", labelErr.message);
        }

        // Add interactive click and hover handlers for Agent markers layer
        maplibreInstance.on('click', 'campus-agents-layer', (e) => {
          if (!e.features || !e.features.length) return;
          const feat = e.features[0];
          const props = feat.properties;
          const coords = feat.geometry.coordinates.slice();
          const residentId = props.resident_id;
          const resident = (WorldStore.agents || []).find(r => Number(r.id) === Number(residentId)) || props;

          if (typeof window.focusAgentOnMap === "function") {
            window.focusAgentOnMap(residentId);
          }

          const html = `
            <div class="map-popup-card">
              <div class="popup-title">👤 ${escapeHtml(resident.name || props.name || "Agent")}</div>
              <div class="popup-sub">${escapeHtml(resident.role || props.role || "校园居民")} · ID: ${residentId}</div>
              <div class="popup-body">
                <div><strong>移动状态：</strong><span style="color:#64ffda;font-weight:bold;">${escapeHtml(props.status || "idle")}</span></div>
                <div><strong>所在节点：</strong>${escapeHtml(props.location || resident.location || "未知")}</div>
              </div>
              <div class="popup-actions">
                <button onclick="window.focusAgentOnMap('${residentId}')">🔍 查看 Agent 详细档案</button>
              </div>
            </div>
          `;

          if (mapActivePopup) mapActivePopup.remove();
          mapActivePopup = new maplibregl.Popup({ offset: 12, closeButton: true })
            .setLngLat(coords)
            .setHTML(html)
            .addTo(maplibreInstance);
        });

        maplibreInstance.on('mouseenter', 'campus-agents-layer', () => {
          maplibreInstance.getCanvas().style.cursor = 'pointer';
        });
        maplibreInstance.on('mouseleave', 'campus-agents-layer', () => {
          maplibreInstance.getCanvas().style.cursor = '';
        });
      }
      syncAgentAvatarMarkers(agentFeatures);
      syncNodeLabelMarkers(geojsonFeatures);
    };

    const runUpdate = () => {
      try {
        updateSource();
      } catch (err) {
        console.warn("MapLibre updateSource error:", err);
      }
    };

    if (maplibreInstance.isStyleLoaded() || maplibreInstance.loaded()) {
      runUpdate();
    } else {
      maplibreInstance.once('load', runUpdate);
      maplibreInstance.once('styledata', runUpdate);
      setTimeout(runUpdate, 200);
    }
  }
}

export function refreshMapLibreMarkers() {
  if (maplibreInstance) {
    initOrUpdateMapLibreMap(spatialWorldsCache);
  }
}

export function getMapLibreInstance() {
  return maplibreInstance;
}
