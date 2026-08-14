/**
 * MapLibre Map Component Module
 * Manages 2D MapLibre campus map, OSM raster layer, spatial nodes, agent markers, and interactive event handlers.
 */
import { $, avatarFiles, escapeHtml, WorldStore } from "./world-store.js";

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

  if (maplibreInstance.getSource('campus-agents') && latestAgentFeatures) {
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

    maplibreInstance.getSource('campus-agents').setData({
      type: 'FeatureCollection',
      features: interpolatedFeatures
    });
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
      const element = document.createElement("button");
      element.type = "button";
      element.className = "map-agent-avatar";
      element.title = `${props.name} · ${props.location}`;
      element.innerHTML = `<img alt="${escapeHtml(props.name)}的头像">`;
      element.onclick = event => {
        event.stopPropagation();
        if (typeof window.focusAgentOnMap === "function") window.focusAgentOnMap(props.resident_id);
      };
      marker = new maplibregl.Marker({ element, anchor: "center" }).setLngLat(feature.geometry.coordinates).addTo(maplibreInstance);
      agentAvatarMarkers.set(id, marker);
    }
    const element = marker.getElement();
    // Agents are people, not a level-of-detail data layer: keep their avatar
    // visible at every zoom level.  When several residents occupy one node,
    // the small ring offset keeps every person discoverable instead of
    // replacing them with a generic dot at overview zoom.
    element.style.display = "";
    const [offsetX, offsetY] = avatarOffset(index, items.length);
    element.style.marginLeft = `${offsetX}px`;
    element.style.marginTop = `${offsetY}px`;
    const image = element.querySelector("img");
    if (image) image.src = `/avatars/${avatarFileFor(props.resident_id)}`;
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

export function initOrUpdateMapLibreMap(spatialWorlds = [], { fitToBounds = false } = {}) {
  const container = $("maplibreContainer");
  if (!container || typeof maplibregl === "undefined") return;

  const currentWorldKey = WorldStore.selectedWorldKey || "default";
  const spatialScene = WorldStore.spatialScene;

  const activeWorld = (spatialWorlds || []).find(w => w.world_key === currentWorldKey) || {};
  const boundsArr = getWgs84BoundsArray(spatialScene?.wgs84_bounds) || getWgs84BoundsArray(activeWorld?.wgs84_bounds);

  let centerLon = 116.32, centerLat = 40.00;
  if (fitToBounds && boundsArr) {
    const [minLon, minLat, maxLon, maxLat] = boundsArr;
    centerLon = (minLon + maxLon) / 2;
    centerLat = (minLat + maxLat) / 2;
  } else if (spatialScene && spatialScene.origin_lon && spatialScene.origin_lat) {
    centerLon = spatialScene.origin_lon;
    centerLat = spatialScene.origin_lat;
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
    maplibreInstance.on('move', () => {
      const center = maplibreInstance?.getCenter();
      if (!center || typeof window.requestSpatialViewportAtLngLat !== 'function') return;
      clearTimeout(viewportMoveTimer);
      viewportMoveTimer = setTimeout(() => {
        const zoom = maplibreInstance.getZoom();
        if (typeof window.requestSpatialViewportForMapBounds === 'function') {
          window.requestSpatialViewportForMapBounds(maplibreInstance.getBounds(), zoom);
        } else {
          window.requestSpatialViewportAtLngLat(center.lng, center.lat, zoom);
        }
      }, 80);
    });
    maplibreInstance.on('zoomend', () => {
      syncAgentAvatarMarkers(latestAgentFeatures);
      const center = maplibreInstance.getCenter();
      if (center && typeof window.requestSpatialViewportAtLngLat === 'function') {
        // A pure zoom does not always emit a useful pan delta; request the
        // correct-sized geographic window explicitly so POIs can appear.
        const zoom = maplibreInstance.getZoom();
        if (typeof window.requestSpatialViewportForMapBounds === 'function') {
          window.requestSpatialViewportForMapBounds(maplibreInstance.getBounds(), zoom);
        } else {
          window.requestSpatialViewportAtLngLat(center.lng, center.lat, zoom);
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
    const nodeMap = new Map((currentScene.nodes || []).map(n => [n.id, n]));
    // Physical state is a separate runtime observation layer. Join it to the
    // immutable OSM node graph only for display; it must never overwrite map
    // geometry or be inferred from a legacy time-of-day template.
    const physicalByNode = new Map((currentScene.physical_states || []).map(s => [Number(s.node_id), s]));
    const geojsonFeatures = currentScene.nodes
      .filter(n => n.longitude && n.latitude && ["building", "poi", "outdoor_area"].includes(n.node_type))
      .map(n => {
        const physical = physicalByNode.get(Number(n.id)) || {};
        return ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [n.longitude, n.latitude] },
        properties: {
          id: n.id,
          name: n.name || "POI",
          type: n.node_type || "poi",
          tier: n.node_type === "building" && Number(n.capacity || 0) >= 400 ? 0 : (n.node_type === "poi" ? 2 : 1),
          access_status: physical.access_status || n.status || "open",
          crowd_density: Number(physical.crowd_density || 0),
          precipitation: Number(physical.precipitation || 0),
          noise_db: Number(physical.noise_db || 0)
        }
      });
      });

    const edgeFeatures = (currentScene.edges || [])
      .map(e => {
        const fromNode = nodeMap.get(e.from_node_id);
        const toNode = nodeMap.get(e.to_node_id);
        if (!fromNode || !toNode || !fromNode.longitude || !fromNode.latitude || !toNode.longitude || !toNode.latitude) return null;
        return {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [[fromNode.longitude, fromNode.latitude], [toNode.longitude, toNode.latitude]]
          },
          properties: {
            id: e.id,
            weather_factor: Number(e.weather_factor || 1.0),
            congestion_factor: Number(e.congestion_factor || 1.0),
            high_resistance: Number(e.weather_factor || 1.0) > 1.25
            ,closed: String(e.status || 'open') !== 'open'
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
            'line-color': '#42a5f5',
            'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1.5, 18, 3.5],
            'line-opacity': 0.35
          }
        });
      }

      if (!maplibreInstance.getLayer('campus-edges-closed-layer')) {
        maplibreInstance.addLayer({
          id: 'campus-edges-closed-layer', type: 'line', source: 'campus-edges',
          filter: ['==', ['get', 'closed'], true],
          paint: { 'line-color': '#d84315', 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 3, 18, 6], 'line-opacity': 0.9 }
        });
      }

      if (!maplibreInstance.getLayer('campus-edges-resistance-layer')) {
        maplibreInstance.addLayer({
          id: 'campus-edges-resistance-layer',
          type: 'line',
          source: 'campus-edges',
          filter: ['==', ['get', 'high_resistance'], true],
          paint: {
            'line-color': '#ff3d00',
            'line-width': ['interpolate', ['linear'], ['zoom'], 14, 2.5, 18, 5],
            'line-dasharray': [2, 2],
            'line-opacity': 0.85
          }
        });
      }

      // 1. POIs and Building Nodes Layer
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

        maplibreInstance.addLayer({
          id: 'campus-nodes-layer',
          type: 'circle',
          source: 'campus-nodes',
          minzoom: 12.5,
          filter: ['==', ['get', 'tier'], 0],
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 12.5, 4, 16, 7, 19, 10],
            'circle-color': ['case', ['==', ['get', 'access_status'], 'closed'], '#d84315', ['>=', ['get', 'crowd_density'], 0.8], '#f9a825', '#1769aa'],
            'circle-stroke-width': 2,
            'circle-stroke-color': '#ffffff'
          }
        });
        maplibreInstance.addLayer({
          id: 'campus-building-nodes-layer', type: 'circle', source: 'campus-nodes', minzoom: 14.5,
          filter: ['==', ['get', 'tier'], 1],
          paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 15, 4, 18, 8], 'circle-color': ['case', ['==', ['get', 'access_status'], 'closed'], '#d84315', ['>=', ['get', 'crowd_density'], 0.8], '#f9a825', '#3287c5'], 'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff' }
        });
        maplibreInstance.addLayer({
          id: 'campus-poi-nodes-layer', type: 'circle', source: 'campus-nodes', minzoom: 16,
          filter: ['==', ['get', 'tier'], 2],
          paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 16, 2.5, 19, 5], 'circle-color': '#59a978', 'circle-stroke-width': 1, 'circle-stroke-color': '#ffffff' }
        });

        try {
          maplibreInstance.addLayer({
            id: 'campus-nodes-label',
            type: 'symbol',
            source: 'campus-nodes',
            minzoom: 15.5,
            filter: ['==', ['get', 'tier'], 0],
            layout: {
              'text-field': ['get', 'name'],
              'text-size': 11,
              'text-offset': [0, 1.2],
              'text-anchor': 'top'
            },
            paint: {
              'text-color': '#ffffff',
              'text-halo-color': '#102033',
              'text-halo-width': 2
            }
          });
        } catch (labelErr) {
          console.warn("MapLibre node symbol label omitted:", labelErr.message);
        }

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
        .filter(a => a.longitude && a.latitude)
        .map(a => {
          const resident = (WorldStore.agents || []).find(r => Number(r.id) === Number(a.resident_id)) || {};
          return {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [a.longitude, a.latitude] },
            properties: {
              resident_id: a.resident_id,
              name: resident.name || `Agent ${a.resident_id}`,
              role: resident.role || "校园居民",
              status: a.movement_status || "idle",
              location: a.current_node_name || "位置未知",
              current_node_id: a.current_node_id
            }
          };
        });
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
              'text-opacity': ['interpolate', ['linear'], ['zoom'], 16.8, 0, 17.5, 1],
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
    };

    if (maplibreInstance.isStyleLoaded()) {
      updateSource();
    } else {
      maplibreInstance.once('load', updateSource);
    }
  }
}

export function getMapLibreInstance() {
  return maplibreInstance;
}
