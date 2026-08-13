/**
 * Main Frontend Application Script
 * Orchestrates the 2D geographic map, Agent state polling, life-course viewer, newspaper, and admin runtime UI.
 */
import { ApiClient } from "./api-client.js?v=20260811-profile-timeoutfix";
import { $, colors, avatarFiles, defaultSpaces, WorldStore, escapeHtml } from "./spatial/world-store.js";
import { initOrUpdateMapLibreMap, getMapLibreInstance } from "./spatial/maplibre-map.js?v=20260811-poiviewport1";
import {
  initThreeScene,
  resizeProfile,
  renderProfileCharacter
} from "./spatial/three-scene.js?v=20260811-profileonly1";

function showToast(message) {
  let toast = $("appToast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "appToast";
    toast.className = "app-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3200);
}

let selectedAgent = null;
let currentWorldKey = new URLSearchParams(window.location.search).get("world_key") || localStorage.getItem("spatial_world_key") || "tsinghua_main";
let spatialWorlds = [];
let runtimeClockBase = null;
let runtimeClockSyncedAt = 0;
// One neighbourhood-sized tile: roughly four to five campus buildings at
// the default zoom, rather than an unreadable full-campus overview.
const LOCAL_SCENE_SPAN_METERS = 360;
const LOCAL_SCENE_EDGE_BUFFER = 0.22;
let sceneViewport = null;
let viewportLoadTimer = null;
let hasInitializedAgentViewport = false;
let profileRequestToken = 0;
let lifeCourseResidentId = null;
let lifeCoursePayload = null;
let lifeCourseOldestDay = null;
let lifeCourseView = "actions";
let profileRelationshipMetric = "trust";

function activeWorldBounds() {
  const world = spatialWorlds.find(item => item.world_key === currentWorldKey);
  const bounds = world?.metric_bounds;
  return Array.isArray(bounds) && bounds.length === 4 ? bounds.map(Number) : null;
}

function sceneSpanForZoom(zoom = 16.5) {
  // Lower zooms request a wider real-world window; higher zooms keep the
  // payload focused on nearby buildings and POIs.
  const scaled = LOCAL_SCENE_SPAN_METERS * Math.pow(2, 16.5 - Number(zoom || 16.5));
  return Math.max(260, Math.min(1800, scaled));
}

function clampViewport(centerX, centerZ, spanMeters = LOCAL_SCENE_SPAN_METERS) {
  const worldBounds = activeWorldBounds();
  if (!worldBounds) return null;
  const [worldMinX, worldMinZ, worldMaxX, worldMaxZ] = worldBounds;
  const halfSpan = Math.min(
    spanMeters / 2,
    Math.max(80, Math.max(worldMaxX - worldMinX, worldMaxZ - worldMinZ) / 2)
  );
  const x = Math.max(worldMinX + halfSpan, Math.min(worldMaxX - halfSpan, Number(centerX)));
  const z = Math.max(worldMinZ + halfSpan, Math.min(worldMaxZ - halfSpan, Number(centerZ)));
  return {
    minX: Math.max(worldMinX, x - halfSpan),
    minZ: Math.max(worldMinZ, z - halfSpan),
    maxX: Math.min(worldMaxX, x + halfSpan),
    maxZ: Math.min(worldMaxZ, z + halfSpan)
  };
}

function ensureSceneViewport() {
  if (sceneViewport) return sceneViewport;
  const bounds = activeWorldBounds();
  if (!bounds) return null;
  sceneViewport = clampViewport((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2);
  return sceneViewport;
}

function densestAgentCluster(states) {
  const clusters = new Map();
  (states || []).forEach(state => {
    const x = Number(state.x), z = Number(state.z);
    if (!Number.isFinite(x) || !Number.isFinite(z)) return;
    const key = state.current_node_id != null
      ? `node:${state.current_node_id}`
      : `grid:${Math.round(x / 20)}:${Math.round(z / 20)}`;
    const cluster = clusters.get(key) || { x: 0, z: 0, count: 0 };
    cluster.x += x;
    cluster.z += z;
    cluster.count += 1;
    clusters.set(key, cluster);
  });
  const winner = Array.from(clusters.values()).sort((a, b) => b.count - a.count)[0];
  return winner ? { x: winner.x / winner.count, z: winner.z / winner.count, count: winner.count } : null;
}

function viewportKey(viewport) {
  if (!viewport) return "full";
  return [viewport.minX, viewport.minZ, viewport.maxX, viewport.maxZ]
    .map(value => Math.round(value / 25) * 25)
    .join(":");
}

function viewportContains(viewport, x, z, withBuffer = true) {
  if (!viewport || !Number.isFinite(Number(x)) || !Number.isFinite(Number(z))) return true;
  const padX = withBuffer ? (viewport.maxX - viewport.minX) * LOCAL_SCENE_EDGE_BUFFER : 0;
  const padZ = withBuffer ? (viewport.maxZ - viewport.minZ) * LOCAL_SCENE_EDGE_BUFFER : 0;
  return x >= viewport.minX + padX && x <= viewport.maxX - padX && z >= viewport.minZ + padZ && z <= viewport.maxZ - padZ;
}

function requestViewportAtMetric(x, z, { fitCamera = false, zoom = 16.5 } = {}) {
  const nextViewport = clampViewport(x, z, sceneSpanForZoom(zoom));
  requestSpatialViewport(nextViewport, fitCamera);
}

function requestSpatialViewport(nextViewport, fitCamera = false) {
  const currentSpan = sceneViewport ? Math.max(sceneViewport.maxX - sceneViewport.minX, sceneViewport.maxZ - sceneViewport.minZ) : 0;
  const nextSpan = nextViewport ? Math.max(nextViewport.maxX - nextViewport.minX, nextViewport.maxZ - nextViewport.minZ) : 0;
  const sameScale = currentSpan && Math.abs(currentSpan - nextSpan) / currentSpan < 0.12;
  if (!nextViewport || (sameScale && viewportKey(nextViewport) === viewportKey(sceneViewport))) return;
  sceneViewport = nextViewport;
  clearTimeout(viewportLoadTimer);
  viewportLoadTimer = setTimeout(() => loadSpatialWorld(true, false, fitCamera), 120);
}

window.requestSpatialViewportAtMetric = requestViewportAtMetric;
window.requestSpatialViewportAtLngLat = function(lng, lat, zoom) {
  const scene = WorldStore.spatialScene;
  const metric = scene?.bounds;
  const geographic = scene?.wgs84_bounds;
  if (!metric || !Array.isArray(geographic) || geographic.length !== 4) return;
  const [minLon, minLat, maxLon, maxLat] = geographic;
  if (maxLon === minLon || maxLat === minLat) return;
  const x = metric.min_x + ((Number(lng) - minLon) / (maxLon - minLon)) * (metric.max_x - metric.min_x);
  const z = metric.min_z + ((Number(lat) - minLat) / (maxLat - minLat)) * (metric.max_z - metric.min_z);
  requestViewportAtMetric(x, z, { zoom });
};

window.requestSpatialViewportForMapBounds = function(bounds, zoom) {
  const scene = WorldStore.spatialScene;
  const metric = scene?.bounds;
  const geographic = scene?.wgs84_bounds;
  if (!bounds || !metric || !Array.isArray(geographic) || geographic.length !== 4) return;
  const [minLon, minLat, maxLon, maxLat] = geographic;
  if (maxLon === minLon || maxLat === minLat) return;
  const project = (lng, lat) => ({
    x: metric.min_x + ((Number(lng) - minLon) / (maxLon - minLon)) * (metric.max_x - metric.min_x),
    z: metric.min_z + ((Number(lat) - minLat) / (maxLat - minLat)) * (metric.max_z - metric.min_z)
  });
  const southwest = project(bounds.getWest(), bounds.getSouth());
  const northeast = project(bounds.getEast(), bounds.getNorth());
  const centerX = (southwest.x + northeast.x) / 2;
  const centerZ = (southwest.z + northeast.z) / 2;
  // Query the visible area plus a small buffer. The cap prevents a far zoom
  // from turning into a full-campus payload, while close zooms stay local.
  const visibleSpan = Math.max(Math.abs(northeast.x - southwest.x), Math.abs(northeast.z - southwest.z));
  const span = Math.max(sceneSpanForZoom(zoom), Math.min(1800, visibleSpan * 1.12));
  requestSpatialViewport(clampViewport(centerX, centerZ, span));
};

window.selectNodeDestination = async function(nodeId) {
  const activeAgent = selectedAgent || (WorldStore.selected !== null && WorldStore.selected !== undefined && WorldStore.agents[WorldStore.selected] ? WorldStore.agents[WorldStore.selected] : null);
  if (!activeAgent) {
    showToast("请先在居民列表或地图上点击选择一名要调度的 Agent");
    return;
  }
  const nodeObj = (WorldStore.spatialScene?.nodes || []).find(n => String(n.id) === String(nodeId));
  const destName = nodeObj ? (nodeObj.code || nodeObj.name) : String(nodeId);

  try {
    const res = await fetch(`/api/agents/${activeAgent.id}/destination`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination: destName, constraint_response: "auto" })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "指定导航目标失败");
    }
    const data = await res.json();
    showToast(`🚀 已成功开启导航：${activeAgent.name} 正在前往【${data.destination || destName}】`);
    loadSpatialWorld(true, false, true);
    loadWorldRuntime();
  } catch (e) {
    showToast(`导航提示：${e.message}`);
  }
};

window.focusAgentOnMap = function(agentId) {
  const agent = (WorldStore.agents || []).find(a => Number(a.id) === Number(agentId));
  if (agent) {
    selectedAgent = agent;
    const index = WorldStore.agents.findIndex(a => Number(a.id) === Number(agentId));
    if (index >= 0) openProfile(index);
    const spatialState = WorldStore.spatialAgents.get(Number(agentId));
    if (spatialState) requestViewportAtMetric(spatialState.x, spatialState.z, { fitCamera: true });
    showToast(`已锁定并放大观察 Agent: ${agent.name}`);
  }
};

window.triggerMapEventAt = async function(lon, lat) {
  try {
    const res = await fetch("/api/spatial/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        world_key: currentWorldKey || "tsinghua_main",
        longitude: parseFloat(lon),
        latitude: parseFloat(lat),
        event_type: "physical_environment_change",
        title: "地图环境物理波动",
        description: `在坐标 (${parseFloat(lon).toFixed(5)}, ${parseFloat(lat).toFixed(5)}) 处触发了环境物理波动`
      })
    });
    if (!res.ok) throw new Error("提交地图物理事件失败");
    const data = await res.json();
    showToast(`⚡️ 已成功在地图发布环境事件：${data.title}`);
    loadWorldRuntime();
  } catch (e) {
    showToast(`发布地图事件失败：${e.message}`);
  }
};

function zoomActiveMap(direction) {
  const map = getMapLibreInstance();
  if (!map) return;
  const nextZoom = map.getZoom() + direction * 0.65;
  map.easeTo({ zoom: nextZoom, duration: 180 });
  if ($("cameraZoomValue")) $("cameraZoomValue").textContent = `Z${nextZoom.toFixed(1)}`;
}

async function loadSpatialWorlds() {
  try {
    const payload = await ApiClient.fetchWorlds();
    spatialWorlds = payload.worlds || [];
    const urlParamWorld = new URLSearchParams(window.location.search).get("world_key");
    const activeWorldObj = spatialWorlds.find(w => w.world_key === currentWorldKey && (w.node_count || 0) > 0);
    if (!urlParamWorld && (!activeWorldObj || currentWorldKey === "default")) {
      const targetWorld = spatialWorlds.find(w => w.world_key === "tsinghua_main") || spatialWorlds.find(w => w.is_real_world && (w.node_count || 0) > 0) || spatialWorlds.find(w => (w.node_count || 0) > 0);
      if (targetWorld) {
        currentWorldKey = targetWorld.world_key;
        localStorage.setItem("spatial_world_key", currentWorldKey);
      }
    }

    // Do not discard a ready spatial window when the world list is refreshed.
    if (WorldStore.selectedWorldKey !== currentWorldKey || WorldStore.scenePhase === "idle") {
      WorldStore.selectWorld(currentWorldKey);
    }

    const select = $("worldKeySelector");
    if (select) {
      select.disabled = false;
      if (spatialWorlds.length) {
        select.innerHTML = spatialWorlds.map(w =>
          `<option value="${w.world_key}" ${w.world_key === currentWorldKey ? "selected" : ""}>${w.name} (${w.node_count} 节点 / ${w.edge_count} 边)</option>`
        ).join("");
      } else {
        select.innerHTML = `<option value="default">数据库未找到空间节点 (需在 Supabase 执行 schema.sql 并导入节点)</option>`;
      }
      select.onchange = (e) => {
        currentWorldKey = e.target.value;
        localStorage.setItem("spatial_world_key", currentWorldKey);
        sceneViewport = null;
        hasInitializedAgentViewport = false;
        WorldStore.selectWorld(currentWorldKey);
        loadSpatialWorld(true, false, true);
      };
    }
  } catch (e) {
    console.warn("Failed to load spatial worlds list", e);
    const select = $("worldKeySelector");
    if (select) {
      select.disabled = false;
      select.innerHTML = `<option value="default">空间数据读取受阻 (${e.message})</option>`;
    }
  }
}

const spatialSceneCache = new Map();
const SPATIAL_SCENE_TTL_MS = 60000;

export function invalidateSpatialSceneCache(worldKey = null) {
  if (worldKey) {
    for (const key of spatialSceneCache.keys()) {
      if (key.startsWith(`${worldKey}:`)) spatialSceneCache.delete(key);
    }
  } else {
    spatialSceneCache.clear();
  }
}

let currentAbortController = null;

async function loadSpatialWorld(shouldRender = true, forceRefresh = false, isInitialFit = false) {
  if (currentAbortController) {
    currentAbortController.abort();
  }
  currentAbortController = new AbortController();
  const signal = currentAbortController.signal;

  WorldStore.sceneRequestToken += 1;
  const requestToken = WorldStore.sceneRequestToken;

  const isAlreadyReady = currentWorldKey !== "default" &&
    WorldStore.selectedWorldKey === currentWorldKey &&
    WorldStore.scenePhase === "ready" &&
    (WorldStore.spatialScene?.nodes?.length || 0) > 0;

  if (!isAlreadyReady) {
    if (currentWorldKey !== "default") {
      WorldStore.scenePhase = "loading";
    } else {
      WorldStore.scenePhase = "ready";
      WorldStore.selectedWorldKey = "default";
    }
  }

  try {
    const viewport = ensureSceneViewport();
    const cacheKey = `${currentWorldKey}:${viewportKey(viewport)}`;
    const cached = spatialSceneCache.get(cacheKey);
    const isExpired = !cached || (Date.now() - cached.timestamp > SPATIAL_SCENE_TTL_MS);

    let scenePromise;
    if (!forceRefresh && !isExpired && cached) {
      scenePromise = Promise.resolve(cached.data);
    } else {
      const params = new URLSearchParams({ world_key: currentWorldKey });
      if (viewport) {
        params.set("min_x", viewport.minX.toFixed(2));
        params.set("min_z", viewport.minZ.toFixed(2));
        params.set("max_x", viewport.maxX.toFixed(2));
        params.set("max_z", viewport.maxZ.toFixed(2));
      }
      scenePromise = fetch(`/api/spatial/scene?${params.toString()}`, { signal })
        .then(res => res.ok ? res.json() : null)
        .catch(err => {
          if (err.name === "AbortError") return null;
          return null;
        });
    }

    const [sceneData, agentsResponse, queueResponse, bodyResponse] = await Promise.all([
      scenePromise,
      fetch("/api/spatial/agents", { signal }).catch(() => null),
      fetch("/api/spatial/admission-queue", { signal }).catch(() => null),
      fetch("/api/body-states", { signal }).catch(() => null)
    ]);

    if (signal.aborted || requestToken !== WorldStore.sceneRequestToken) {
      return;
    }

    if (sceneData && sceneData.world_key === currentWorldKey) {
      // The API topology checksum is intentionally viewport-independent.
      // Track the client viewport as well, otherwise two equally sized tiles
      // can be mistaken for the same scene and leave stale buildings visible.
      sceneData.__viewportKey = cacheKey;
      spatialSceneCache.set(cacheKey, {
        data: sceneData,
        timestamp: Date.now(),
        version: sceneData.scene_version ?? sceneData.version ?? Date.now()
      });

      const currentVersion = WorldStore.spatialScene?.scene_version ?? WorldStore.spatialScene?.version;
      const newVersion = sceneData.scene_version ?? sceneData.version;
      const versionChanged = forceRefresh ||
        currentVersion !== newVersion ||
        WorldStore.spatialScene?.__viewportKey !== cacheKey ||
        WorldStore.selectedWorldKey !== currentWorldKey;

      if (versionChanged) {
        WorldStore.sceneVersion += 1;
      }

      WorldStore.setSpatialScene(sceneData);

      // Rendering waits for the accompanying Agent state below. This avoids
      // a first paint at the world centre followed by a second paint at the
      // actually populated local area.
    } else if (currentWorldKey !== "default" && !isAlreadyReady) {
      WorldStore.scenePhase = "error";
    }

    const payload = agentsResponse && agentsResponse.ok ? await agentsResponse.json() : { agents: [] };
    const queuePayload = queueResponse && queueResponse.ok ? await queueResponse.json() : { queue: [] };
    const bodyPayload = bodyResponse && bodyResponse.ok ? await bodyResponse.json() : { agents: [] };

    if (signal.aborted || requestToken !== WorldStore.sceneRequestToken) {
      return;
    }

    WorldStore.spatialAgents = new Map((payload.agents || []).map(state => [Number(state.resident_id), state]));
    WorldStore.spatialQueue = new Map((queuePayload.queue || []).map(item => [Number(item.resident_id), item]));
    WorldStore.bodyStates = new Map((bodyPayload.agents || []).map(state => [Number(state.resident_id), state]));
    WorldStore.agents.forEach(agent => {
      const state = WorldStore.spatialAgents.get(Number(agent.id));
      if (state && ["idle", "arrived"].includes(state.movement_status) && state.current_node_name) agent.location = state.current_node_name;
    });

    // On first entry, show the busiest inhabited area rather than the centre
    // of the whole campus. Subsequent ticks keep the user's chosen view.
    const focusState = selectedAgent
      ? WorldStore.spatialAgents.get(Number(selectedAgent.id))
      : densestAgentCluster(Array.from(WorldStore.spatialAgents.values()));
    if (!hasInitializedAgentViewport && focusState) {
      hasInitializedAgentViewport = true;
      const clusterViewport = clampViewport(focusState.x, focusState.z);
      if (clusterViewport && viewportKey(clusterViewport) !== viewportKey(sceneViewport)) {
        sceneViewport = clusterViewport;
        setTimeout(() => loadSpatialWorld(true, false, true), 0);
        return;
      }
    }

    initOrUpdateMapLibreMap(spatialWorlds, { fitToBounds: isInitialFit });

    if (shouldRender) {
      renderCampusMap();
      renderSpaces();
      renderList();
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    if (requestToken === WorldStore.sceneRequestToken && currentWorldKey !== "default" && !isAlreadyReady) {
      WorldStore.scenePhase = "error";
    }
  }
}

async function loadBodyStates(shouldRender = true) {
  try {
    const response = await fetch("/api/body-states");
    if (!response.ok) return;
    const payload = await response.json();
    WorldStore.bodyStates = new Map((payload.agents || []).map(state => [Number(state.resident_id), state]));
    if (shouldRender) renderList();
  } catch (error) {
    console.warn("Body states unavailable", error.message);
  }
}

function applyWorldEvents(events) {
  events.forEach(event => {
    if (event.event_type === "agent_tick" && event.payload?.action !== "move" && event.resident_id && event.location) {
      const agent = WorldStore.agents.find(item => Number(item.id) === Number(event.resident_id));
      if (agent && agent.location !== event.location) agent.location = event.location;
    }
  });
  renderObserverHud();
  initOrUpdateMapLibreMap(spatialWorlds);
  renderCampusMap();
  renderSpaces();
  renderList();
}

async function pollWorldEvents() {
  if (!WorldStore.observerStateLoaded && WorldStore.lastWorldEventId <= 0) return;
  try {
    const response = await fetch(`/api/world/events?after_id=${WorldStore.lastWorldEventId}&limit=60`);
    if (!response.ok) throw new Error("events 接口失败");
    const payload = await response.json();
    const events = payload.events || [];
    if (events.length) {
      WorldStore.worldEvents = WorldStore.worldEvents.concat(events).slice(-120);
      WorldStore.lastWorldEventId = payload.next_after_id || events[events.length - 1].id;
      renderWorldEvents();
      if (events.some(event => String(event.event_type || "").startsWith("spatial_"))) {
        invalidateSpatialSceneCache(currentWorldKey);
        await loadSpatialWorld(false, true);
      } else if (events.some(event => event.event_type === "world_tick_complete")) {
        await loadBodyStates(false);
      }
      applyWorldEvents(events);
      if (events.some(event => event.event_type === "campus_news_published")) await loadNewsPosts();
    }
  } catch (e) {
    ensureRuntimePanel();
    $("worldEventStream").innerHTML = `<div class="empty" style="padding:8px 0">事件流暂不可用：${e.message}</div>`;
  }
}

function setPaperLoading(loading, message = "") {
  const content = $("newspaperContent");
  if (content) content.classList.toggle("loading", loading);
  if ($("paperLoadStatus")) $("paperLoadStatus").textContent = message;
  if (loading) [$("paperPrev"), $("paperToday"), $("paperNext")].filter(Boolean).forEach(button => button.disabled = true);
}

async function loadNewsPosts(day = null) {
  const requestId = ++WorldStore.newspaperRequestId;
  const targetLabel = day != null ? `第 ${day} 天` : "今日";
  setPaperLoading(true, `正在读取${targetLabel}日报...`);
  try {
    const query = day != null ? `?day=${day}` : "";
    const response = await fetch(`/api/newspaper/agent-posts${query}`);
    if (!response.ok) throw new Error("日报接口失败");
    const payload = await response.json();
    if (requestId !== WorldStore.newspaperRequestId) return;
    WorldStore.newsPosts = payload.posts || [];
    WorldStore.newspaperDay = payload.day || day || WorldStore.world?.current_day || 1;
    WorldStore.newspaperEdition = payload.edition || {};
    WorldStore.newspaperArchive = { available_days: payload.available_days || [], previous_day: payload.previous_day, next_day: payload.next_day, current_day: payload.current_day };
    renderActivities();
    renderNewspaper();
    setPaperLoading(false, `${WorldStore.newspaperEdition.label || `第 ${WorldStore.newspaperDay} 天日报`}已载入`);
  } catch (error) {
    if (requestId !== WorldStore.newspaperRequestId) return;
    renderNewspaper();
    setPaperLoading(false, "读取失败，请稍后重试");
  }
}

async function loadObserverState() {
  if ($("status")) $("status").textContent = "正在接入校园世界...";
  try {
    const obsResponse = await fetch("/api/world/observer-state");
    if (obsResponse && obsResponse.ok) {
      const payload = await obsResponse.json();
      WorldStore.world = { ...WorldStore.world, ...payload, events: payload.events || [] };
      applyRandomAgentOrder(payload.agents || []);
      WorldStore.worldRuntime = payload.runtime || WorldStore.worldRuntime;
      WorldStore.worldEvents = (payload.events || []).slice(-80);
      WorldStore.lastWorldEventId = WorldStore.worldEvents.at(-1)?.id || WorldStore.worldRuntime?.latest_event_id || WorldStore.lastWorldEventId;
      WorldStore.observerStateLoaded = true;
      if (WorldStore.worldRuntime?.world_time) {
        runtimeClockBase = new Date(WorldStore.worldRuntime.world_time);
        runtimeClockSyncedAt = Date.now();
      }
      renderWorldPulse();
      renderWorldRuntime();
      renderWorldEvents();
      renderCampusMap();
      renderSpaces();
      renderList();
      renderObserverHud();
      if ($("status")) $("status").textContent = "观察世界已接入";
    } else {
      const statusText = obsResponse ? `HTTP ${obsResponse.status}` : "无响应";
      if ($("status")) $("status").textContent = `数据库/接口异常 (${statusText})，请检查 Supabase 表和配置`;
    }
  } catch (e) {
    if ($("status")) $("status").textContent = `观察世界接入受阻：${e.message}`;
  }
}

async function touchObserverSession(focus = {}) {
  try {
    if (focus.location) WorldStore.observedFocus = focus.location;
    if (focus.resident_id) {
      const agent = WorldStore.agents.find(item => Number(item.id) === Number(focus.resident_id));
      WorldStore.observedFocus = agent?.name || `Agent ${focus.resident_id}`;
    }
    renderObserverHud();
    const adminToken = localStorage.getItem("ADMIN_TOKEN") || "";
    // The legacy observer-session endpoint still validates locations against
    // its former seven-space enum. Keep the real geographic focus in the UI,
    // but do not submit it as a legacy location until that contract is widened.
    const payload = { session_id: WorldStore.observerSessionId, user_id: "browser-observer", session_type: adminToken ? "admin" : "observer", focused_resident_id: focus.resident_id ?? null, focused_location: "" };
    const response = await fetch("/api/world/observer-sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) return;
    const data = await response.json();
    WorldStore.observerSessionId = data.session?.id || WorldStore.observerSessionId;
  } catch { }
}

async function adminWorldAction(url) {
  const adminToken = localStorage.getItem("ADMIN_TOKEN") || "";
  if (!adminToken) {
    if ($("status")) $("status").textContent = "需要在 localStorage.ADMIN_TOKEN 中配置 admin token";
    return;
  }
  if ($("status")) $("status").textContent = "正在更新世界运行状态...";
  try {
    const response = await fetch(url, { method: "POST", headers: { Authorization: `Bearer ${adminToken}` } });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "admin 操作失败");
    await loadWorldRuntime();
    await pollWorldEvents();
    await loadNewsPosts();
    if ($("status")) $("status").textContent = payload.message || "世界运行状态已更新";
  } catch (e) {
    if ($("status")) $("status").textContent = `admin 操作失败：${e.message}`;
  }
}

function renderExternalInformation(message = "") {
  if ($("externalInfo")) {
    // The Chengdu source was retired when the campus weather anchor moved to
    // Beijing.  Hide a stale client/cache payload rather than presenting it as
    // current campus context before the database reconciliation runs.
    const visibleItems = WorldStore.externalInformation.filter(item =>
      !String(item.title || "").startsWith("成都天气更新：")
      && String(item.source_name || "") !== "Open-Meteo 成都天气"
    );
    $("externalInfo").innerHTML = message ? `<div class="empty">${message}</div>` : (visibleItems.slice(0, 3).map(item => `<div class="external-item"><strong>${item.title}</strong><span>${item.category} · ${item.source_name}</span></div>`).join("") || '<div class="empty">等待北京天气与外部资讯同步。</div>');
  }
}

function isRealSpatialWorld() {
  return Boolean(spatialWorlds.find(world => world.world_key === currentWorldKey)?.is_real_world) || currentWorldKey !== "default";
}

function humanSpatialStatus(status) {
  const value = String(status || "open").toLowerCase();
  if (["closed", "inactive", "blocked"].includes(value)) return "关闭";
  if (["maintenance", "repair"].includes(value)) return "维护中";
  return "开放";
}

function spatialNodeKind(node) {
  const tags = node.properties?.osm_tags || node.properties || {};
  return String(tags.amenity || tags.building || node.node_type || "校园建筑");
}

function realCampusPlaces(limit = 16) {
  const seen = new Set();
  const states = Array.from(WorldStore.spatialAgents.values());
  return (WorldStore.spatialScene?.nodes || [])
    .filter(node => ["building", "poi", "outdoor_area"].includes(node.node_type) && node.name)
    .filter(node => {
      const key = `${node.node_type}:${node.name}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map(node => {
      // Geo import attaches buildings to road nodes. An Agent can therefore
      // be physically at an attached path node while its real place name is
      // the building's name; both are one real location for presentation.
      const occupants = states.filter(state => Number(state.current_node_id) === Number(node.id) || String(state.current_node_name || "") === String(node.name));
      return { ...node, occupancy: occupants.length, hasAgent: occupants.length > 0 };
    })
    .sort((a, b) => Number(b.hasAgent) - Number(a.hasAgent) || b.occupancy - a.occupancy || Number(b.capacity || 0) - Number(a.capacity || 0) || String(a.name).localeCompare(String(b.name), "zh-CN"))
    .slice(0, limit);
}

function renderSpaces() {
  if (!$("spaceList")) return;
  if (isRealSpatialWorld()) {
    const places = realCampusPlaces(16);
    $("spaceList").innerHTML = places.length ? places.map(node => {
      const capacity = Math.max(0, Number(node.capacity || 0));
      const occupancy = Number(node.occupancy || 0);
      const ratio = capacity > 0 ? Math.min(100, Math.round(occupancy / capacity * 100)) : 0;
      return `<div class="space-item ${humanSpatialStatus(node.status) !== "开放" ? "warning" : ""}"><div class="space-top"><strong>${escapeHtml(node.name)}</strong><span class="space-state">${humanSpatialStatus(node.status)}</span></div><div class="small" style="padding:2px 0 0">${escapeHtml(spatialNodeKind(node))} · 容量 ${capacity || "未标注"} · 在场 Agent ${occupancy}</div><div class="bar"><i style="width:${ratio}%"></i></div></div>`;
    }).join("") : '<div class="empty">正在载入当前真实地理窗口的建筑与设施…</div>';
    return;
  }
  const spaces = (WorldStore.world.spaces || {}).spaces || [];
  $("spaceList").innerHTML = spaces.map(s => {
    const level = Number(s.crowd_percent ?? s.occupancy ?? 0), warn = s.effective_status !== "开放" || level >= 85;
    return `<div class="space-item ${warn ? "warning" : ""}"><div class="space-top"><strong>${s.name || s.location || "校园空间"}</strong><span class="space-state">${s.effective_status || s.status || "运行中"}</span></div><div class="small" style="padding:2px 0 0">拥挤度 ${level}% · 在场 Agent ${s.actual_agents ?? 0}</div><div class="bar"><i style="width:${Math.max(0, Math.min(100, level))}%"></i></div></div>`;
  }).join("") || '<div class="empty">暂无空间运行数据。</div>';
}

function renderCampusMap() {
  const map = $("campusMap");
  if (!map) return;
  if (isRealSpatialWorld()) {
    const scene = WorldStore.spatialScene || {};
    const places = realCampusPlaces(12);
    const bounds = scene.wgs84_bounds || spatialWorlds.find(world => world.world_key === currentWorldKey)?.wgs84_bounds;
    const coordLabel = Array.isArray(bounds) && bounds.length === 4
      ? `WGS84 ${Number(bounds[0]).toFixed(3)}, ${Number(bounds[1]).toFixed(3)} — ${Number(bounds[2]).toFixed(3)}, ${Number(bounds[3]).toFixed(3)}`
      : "正在读取 WGS84 坐标范围";
    const meta = $("campusMapMeta");
    const occupiedCount = places.filter(place => place.hasAgent).length;
    if (meta) meta.textContent = `${coordLabel} · 优先显示 ${occupiedCount} 栋有 Agent 的真实建筑`;
    if (!places.length) {
      map.innerHTML = '<div class="empty">正在载入当前真实地理窗口的建筑、道路与 Agent…</div>';
      return;
    }
    map.innerHTML = places.map(node => {
      const residents = WorldStore.agents.filter(agent => {
        const state = WorldStore.spatialAgents.get(Number(agent.id));
        return Number(state?.current_node_id) === Number(node.id) || String(state?.current_node_name || "") === String(node.name);
      });
      const capacity = Math.max(0, Number(node.capacity || 0));
      return `<section class="map-space ${node.hasAgent ? "occupied" : ""} ${humanSpatialStatus(node.status) !== "开放" ? "warning" : ""}" data-node-id="${node.id}">
        <strong>${escapeHtml(node.name)}</strong>
        <small>${escapeHtml(spatialNodeKind(node))} · ${humanSpatialStatus(node.status)} · 容量 ${capacity || "未标注"}</small>
        <small class="map-real-place">${node.hasAgent ? "● Agent 当前在此" : "真实坐标"} · 在场 Agent ${residents.length}</small>
        <div class="map-agent-row">${residents.length ? residents.slice(0, 8).map((agent, index) => {
          const avatar = avatarFiles[(Number(agent.id || index + 1) - 1 + avatarFiles.length) % avatarFiles.length];
          return `<button class="map-agent" data-agent-id="${agent.id}" title="${escapeHtml(agent.name || "Agent")} · ${escapeHtml(node.name)}"><img src="/avatars/${avatar}" alt="${escapeHtml(agent.name || "Agent")}"></button>`;
        }).join("") : "<small>暂无 Agent</small>"}</div>
      </section>`;
    }).join("");
    map.querySelectorAll(".map-agent").forEach(button => button.onclick = event => {
      event.stopPropagation();
      const index = WorldStore.agents.findIndex(agent => Number(agent.id) === Number(button.dataset.agentId));
      if (index >= 0) openProfile(index);
    });
    map.querySelectorAll(".map-space").forEach(card => card.onclick = () => {
      const node = places.find(item => Number(item.id) === Number(card.dataset.nodeId));
      if (!node) return;
      touchObserverSession({ location: node.name });
      requestViewportAtMetric(node.x, node.z);
    });
    return;
  }
  const spaces = (WorldStore.world.spaces || {}).spaces || [];
  const scene = WorldStore.spatialScene || {};
  const nodes = scene.nodes || [];
  const buildings = nodes.filter(node => node.node_type === "building");
  const paths = nodes.filter(node => node.node_type === "path_point");
  const layout = { admin: "1 / 1", teaching: "1 / 2", business: "1 / 3", library: "1 / 4", dorm: "2 / 1", canteen: "2 / 2", playground: "2 / 3" };
  const bounds = scene.wgs84_bounds || spatialWorlds.find(world => world.world_key === currentWorldKey)?.wgs84_bounds;
  const coordLabel = Array.isArray(bounds) && bounds.length === 4
    ? `WGS84 ${Number(bounds[0]).toFixed(3)}, ${Number(bounds[1]).toFixed(3)} — ${Number(bounds[2]).toFixed(3)}, ${Number(bounds[3]).toFixed(3)}`
    : "本地米制空间窗口";
  const meta = $("campusMapMeta");
  if (meta) meta.textContent = `${coordLabel} · 当前窗口 ${buildings.length} 栋建筑、${paths.length} 道路节点`;

  const locationFor = agent => {
    const state = WorldStore.spatialAgents.get(Number(agent.id)) || {};
    return String(state.current_node_name || agent.location || "");
  };
  const matchesSpace = (agent, space) => {
    const location = locationFor(agent);
    const code = String(space.code || "").toLowerCase();
    const name = String(space.name || space.location || "");
    if (location === name || location === String(space.location || "") || (name && location.includes(name))) return true;
    const categories = {
      admin: /校务|行政|办公|校门|室外/,
      teaching: /教学|教室|实验|科研/,
      business: /商业|商店|超市|咖啡|服务/, library: /图书馆|阅览/,
      dorm: /宿舍|公寓|寝室/, canteen: /食堂|餐厅|报告厅|清晏楼|清芬/,
      playground: /操场|体育|球场|运动/
    };
    return Boolean(categories[code]?.test(location));
  };
  const selectedLocation = selectedAgent ? locationFor(selectedAgent) : "";

  map.innerHTML = spaces.map(space => {
    const level = Number(space.crowd_percent ?? space.occupancy ?? 0);
    const residents = WorldStore.agents.filter(agent => matchesSpace(agent, space));
    const isSelected = selectedLocation && matchesSpace(selectedAgent, space);
    const activePlace = residents.map(locationFor).find(Boolean);
    const warning = space.effective_status !== "开放" || level >= 85;
    return `<section class="map-space ${warning ? "warning" : ""} ${isSelected ? "selected-space" : ""}" data-space="${escapeHtml(space.location || space.name || "")}" style="grid-area:${layout[space.code] || "2 / 4"}">
      <strong>${escapeHtml(space.name || space.location || "校园空间")}</strong>
      <small>${escapeHtml(space.effective_status || space.status || "运行中")} · 拥挤度 ${level}% · 在场 Agent ${residents.length}</small>
      ${activePlace && activePlace !== (space.name || space.location) ? `<small class="map-real-place">真实地点：${escapeHtml(activePlace)}</small>` : ""}
      <div class="map-agent-row">${residents.length ? residents.slice(0, 8).map((agent, index) => {
        const avatar = avatarFiles[(Number(agent.id || index + 1) - 1 + avatarFiles.length) % avatarFiles.length];
        return `<button class="map-agent" data-agent-id="${agent.id}" title="${escapeHtml(agent.name || "Agent")} · ${escapeHtml(locationFor(agent))}"><img src="/avatars/${avatar}" alt="${escapeHtml(agent.name || "Agent")}"></button>`;
      }).join("") : "<small>暂无 Agent</small>"}</div>
    </section>`;
  }).join("") || '<div class="empty">暂无校园空间数据。</div>';

  map.querySelectorAll(".map-agent").forEach(button => button.onclick = event => {
    event.stopPropagation();
    const index = WorldStore.agents.findIndex(agent => Number(agent.id) === Number(button.dataset.agentId));
    if (index >= 0) openProfile(index);
  });
  map.querySelectorAll(".map-space").forEach(card => card.onclick = () => {
    const space = spaces.find(item => String(item.location || item.name) === card.dataset.space);
    const resident = WorldStore.agents.find(agent => space && matchesSpace(agent, space));
    touchObserverSession({ location: card.dataset.space });
    if (resident) {
      const state = WorldStore.spatialAgents.get(Number(resident.id));
      if (state) requestViewportAtMetric(state.x, state.z);
    }
  });
}

function sparkline(values, color) {
  const max = Math.max(...values, 1), points = values.map((value, index) => `${index * (100 / (values.length - 1 || 1))},${38 - (value / max) * 30}`).join(" ");
  return `<svg viewBox="0 0 100 40" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="3" points="${points}"/><line x1="0" y1="38" x2="100" y2="38" stroke="#dfe6ec" stroke-width="1"/></svg>`;
}

function renderActivities() {
  const eventDays = {};
  (WorldStore.world.events || []).forEach(event => { const day = Number(event.day || 0); if (day) eventDays[day] = (eventDays[day] || 0) + 1; });
  const days = Array.from({ length: 7 }, (_, index) => Math.max(1, (WorldStore.world.current_day || 1) - 6 + index));
  const eventTrend = days.map(day => eventDays[day] || 0), crowdTrend = days.map((_, index) => Math.max(0, Math.min(100, Number((WorldStore.world.environment || {}).campus_flow || 0) - 12 + index * 4)));
  if ($("activityList")) {
    $("activityList").innerHTML = `<div class="trend-strip"><div class="trend-card"><div class="trend-title"><span>近 7 天事件</span><strong>${eventTrend.reduce((sum, value) => sum + value, 0)}</strong></div>${sparkline(eventTrend, "#d39142")}</div><div class="trend-card"><div class="trend-title"><span>当前人流趋势</span><strong>${(WorldStore.world.environment || {}).campus_flow ?? "--"}</strong></div>${sparkline(crowdTrend, "#1769aa")}</div></div>` + (WorldStore.newsPosts.map(p => `<div class="activity-item"><strong>${articleMeta(p).section} · ${p.name || "校园 Agent"}</strong>${p.content || "runtime 记录到一件值得关注的新鲜事。"}</div>`).join("") || '<div class="empty">日报会在 world runtime 捕捉到突发异常、关系风向、反常行为、群体现象或内心发现后发布。</div>');
  }
}

function articleMeta(post) {
  const agent = WorldStore.agents.find(item => Number(item.id) === Number(post.resident_id)) || {};
  const text = `${post.headline || ""} ${post.content || ""}`;
  const location = agent.location || (/图书馆/.test(text) ? "图书馆" : /食堂|套餐|餐/.test(text) ? "食堂" : /实验|项目|代码/.test(text) ? "教学科研区" : "校园");
  let section = "校园环境", headline = `${location}出现新的环境变化`;
  if (/突发异常|异常|失败|降级|风险|迟到/.test(text)) {
    section = "突发异常"; headline = `${location}出现需要关注的异常信号`;
  } else if (/关系风向|关系|八卦|信任|合作|好感|竞争|紧张/.test(text)) {
    section = "关系风向"; headline = "校园关系网络出现新动向";
  } else if (/反常行为|不同寻常|请假|迟到|冲突/.test(text)) {
    section = "反常行为"; headline = `${post.name || "Agent"}的反常行动引发关注`;
  } else if (/群体现象|涌现|扩散|动员|集体|小组|社团/.test(text)) {
    section = "群体现象"; headline = `${location}涌现出新的集体动态`;
  } else if (/内心发现|特别|发现|观察|想法|反思/.test(text)) {
    section = "内心发现"; headline = `${post.name || "Agent"}记录到一条内心发现`;
  } else if (/维修|检修|桌椅|施工/.test(text)) {
    section = "校园服务"; headline = `${location}启动设施检修，保障师生使用`;
  } else if (/套餐|供餐|补货|高蛋白/.test(text)) {
    section = "生活服务"; headline = "食堂推出专项餐饮服务，回应师生需求";
  } else if (/实验|项目|代码|接口|科研/.test(text)) {
    section = "教学科研"; headline = "校园项目取得新进展，协作团队完成关键任务";
  } else if (/调研|错峰|效率/.test(text)) {
    section = "校园治理"; headline = "师生开展错峰调研，关注校园运行效率";
  } else if (/考试|复习|压力/.test(text)) {
    section = "考试周关注"; headline = "考试周保障措施持续推进";
  }
  return { agent, location, section, headline };
}

function newsBody(post) { return String(post.content || "编辑部正在核实这条校园动态。").replace(/^我今天/, "当天").replace(/^我/, "").replace(/我们/g, "相关人员").replace(/大家/g, "师生").replace(/\s+/g, " ").trim(); }
function postTitle(post) { const generic = new RegExp(`^${post.name || "校园 Agent"}(的校园来信|的今日观察)$`); return post.headline && !generic.test(post.headline) ? post.headline : articleMeta(post).headline; }
function avatarFor(post) { return avatarFiles[(Number(post.resident_id || 1) - 1 + avatarFiles.length) % avatarFiles.length]; }

function openPostProfile(post) {
  const index = WorldStore.agents.findIndex(a => Number(a.id) === Number(post.resident_id));
  if (index >= 0) {
    $("newspaperOverlay").classList.remove("open");
    openProfile(index);
  }
}

function paperTimeLabel(post) { return post.source_slot ? `${post.source_slot} 快讯` : "历史快讯 · 时间未记录"; }
function newsPriority(post) { return Number(post.news_value || (/突发异常|异常/.test(`${post.headline || ""}${post.content || ""}`) ? 100 : /关系风向|关系|反常/.test(`${post.headline || ""}${post.content || ""}`) ? 86 : 50)); }

function setNewspaperView(view) {
  WorldStore.newspaperView = view === "flashes" ? "flashes" : "edition";
  document.querySelectorAll("[data-paper-view]").forEach(button => button.classList.toggle("active", button.dataset.paperView === WorldStore.newspaperView));
  renderNewspaper();
}

function renderNewspaper() {
  const day = WorldStore.newspaperDay || WorldStore.world?.current_day || "—";
  const emptyPost = { name: "校园编辑部", role: "编辑部", content: "今天暂未出现达到发布标准的特别发现。编辑部仍在观察行动偏移、关系变化、异常信号和群体涌现。", resident_id: 1, news_value: 0 };
  const posts = WorldStore.newsPosts.length ? WorldStore.newsPosts : [emptyPost];
  const ranked = [...posts].sort((a, b) => newsPriority(b) - newsPriority(a));
  const hero = ranked[0];
  const columns = ranked.slice(1, 7);
  const chronological = [...WorldStore.newsPosts].sort((a, b) => String(a.source_slot || a.created_at || "").localeCompare(String(b.source_slot || b.created_at || "")));
  const env = WorldStore.world?.environment || {};
  const spaces = (WorldStore.world?.spaces || {}).spaces || [];
  const openSpaces = spaces.filter(space => (space.effective_status || space.status) === "开放").length;
  const isToday = Number(day) === Number(WorldStore.newspaperArchive.current_day || WorldStore.world?.current_day);
  const editionLabel = isToday ? "今日滚动版" : "归档日报";
  const editionContext = isToday ? (env.real_date || "校园世界") : "仿真归档";

  $("paperDate").textContent = `第 ${day} 天 · ${editionLabel} · ${editionContext} · 每日一期`;
  if ($("paperPrev")) $("paperPrev").disabled = !WorldStore.newspaperArchive.previous_day;
  if ($("paperNext")) $("paperNext").disabled = !WorldStore.newspaperArchive.next_day;
  if ($("paperToday")) $("paperToday").disabled = isToday;
  document.querySelectorAll("[data-paper-view]").forEach(button => button.classList.toggle("active", button.dataset.paperView === WorldStore.newspaperView));

  const brief = `<section class="paper-brief"><div><span>${isToday ? "今日天气" : "归档日期"}</span><strong>${isToday ? `${env.weather || "校园天气"} ${env.temperature ?? "--"}°C` : `第 ${day} 天`}</strong></div><div><span>${isToday ? "校园人流" : "期刊状态"}</span><strong>${isToday ? `${env.campus_flow ?? "--"}/100` : "已归档"}</strong></div><div><span>${isToday ? "开放空间" : "出版逻辑"}</span><strong>${isToday ? `${openSpaces}/${spaces.length || 7}` : "每日一期"}</strong></div><div><span>分时快讯</span><strong>${WorldStore.newsPosts.length} 条</strong></div></section>`;

  if (WorldStore.newspaperView === "flashes") {
    $("newspaperContent").innerHTML = brief + `<section class="paper-flashes">${chronological.map(post => `<article class="paper-flash" data-agent-id="${post.resident_id}"><time>${paperTimeLabel(post)}</time><div><span class="paper-label">${articleMeta(post).section}</span><h3>${postTitle(post)}</h3><p>${newsBody(post)}</p><p class="paper-byline">线索来源：${post.name}（${post.role || "校园居民"}）</p></div></article>`).join("") || '<div class="empty">本日尚无分时快讯。runtime 会在每个已完成的 8 小时窗口后评估是否有值得发布的新发现。</div>'}</section>`;
  } else {
    $("newspaperContent").innerHTML = brief + `<section class="paper-hero"><img src="/avatars/${avatarFor(hero)}" alt="${hero.name}的卡通形象"><div><span class="paper-label">头条 · ${articleMeta(hero).section}</span><h2>${postTitle(hero)}</h2><p>${newsBody(hero)}</p><p class="paper-byline">runtime 编辑部 · 线索来源：${hero.name}（${hero.role || "校园居民"}）${hero.source_slot ? ` · ${paperTimeLabel(hero)}` : ""}</p></div></section><section class="paper-columns">${columns.map(post => `<article class="paper-story" data-agent-id="${post.resident_id}"><img src="/avatars/${avatarFor(post)}" alt="${post.name}"><span class="paper-label">${articleMeta(post).section}</span><h3>${postTitle(post)}</h3><p>${newsBody(post)}</p><p class="paper-byline">${paperTimeLabel(post)} · 线索来源：${post.name}</p></article>`).join("") || '<div class="empty">本期暂时只有一条入选报道，新的快讯会继续汇入今日滚动版。</div>'}</section>`;
  }
  document.querySelectorAll(".paper-story,.paper-flash").forEach(card => card.onclick = () => openPostProfile({ resident_id: card.dataset.agentId }));
}

function applyRandomAgentOrder(items = []) {
  const previousId = WorldStore.agents[WorldStore.selected]?.id;
  items.forEach(agent => {
    const id = Number(agent.id);
    if (!WorldStore.agentOrderKeys.has(id)) WorldStore.agentOrderKeys.set(id, Math.random());
  });
  WorldStore.agents = [...items].sort((a, b) => (WorldStore.agentOrderKeys.get(Number(a.id)) ?? 0) - (WorldStore.agentOrderKeys.get(Number(b.id)) ?? 0));
  if (previousId) {
    const nextIndex = WorldStore.agents.findIndex(agent => Number(agent.id) === Number(previousId));
    WorldStore.selected = nextIndex >= 0 ? nextIndex : 0;
  } else WorldStore.selected = Math.min(WorldStore.selected, Math.max(0, WorldStore.agents.length - 1));
}

function movementLabel(agent) {
  const state = WorldStore.spatialAgents.get(Number(agent.id));
  const queue = WorldStore.spatialQueue.get(Number(agent.id));
  if (!state) return agent.location || "校园";
  if (state.movement_status === "waiting") return `${queue?.node_name || state.target_node_name || "入口"}等待 · 第${queue?.queue_position || "--"}位`;
  if (state.movement_status === "moving") return `前往${state.target_node_name || "目的地"} · ${Math.round(Number(state.progress || 0) * 100)}%`;
  if (state.movement_status === "paused") return "移动已暂停";
  if (state.movement_status === "interrupted") return "已放弃等待";
  return agent.location || state.current_node_name || "校园";
}

function describeSpatialPresence(state, fallbackLocation = "校园") {
  const status = String(state?.movement_status || "idle");
  const current = state?.current_node_name || fallbackLocation;
  const origin = state?.origin_node_name || current;
  const target = state?.target_node_name || "未设定目的地";
  const progress = Math.max(0, Math.min(1, Number(state?.progress || 0)));
  const remaining = Math.max(0, Number(state?.remaining_distance_meters || 0));

  if (status === "moving" || status === "replanning") {
    return {
      location: "校园道路上（移动中）",
      recent: origin,
      target,
      status: status === "replanning" ? "正在重新规划路线" : "正在移动",
      progress: `${Math.round(progress * 100)}% · 约剩 ${Math.round(remaining)} 米`,
      narrative: `正从${origin}前往${target}，当前在路径上，并非位于目的地。`
    };
  }
  if (status === "waiting") {
    return {
      location: current,
      recent: current,
      target,
      status: "排队 / 等待进入",
      progress: "正在等待服务或准入",
      narrative: `已到达${current}附近，正在等待进入或获得服务。`
    };
  }
  if (status === "paused" || status === "interrupted") {
    return {
      location: current,
      recent: current,
      target,
      status: status === "paused" ? "移动已暂停" : "行动已中断",
      progress: state?.interrupted_reason || state?.last_replan_reason || "等待后续决策",
      narrative: `当前停留在${current}，原目标为${target}。`
    };
  }
  return {
    location: current,
    recent: current,
    target: state?.target_node_name || "—",
    status: status === "arrived" ? "已抵达" : "在此处",
    progress: "—",
    narrative: `当前位于${current}。`
  };
}

function describeActionPlan(planPayload, spatialState) {
  const plan = planPayload?.plan || {};
  const steps = Array.isArray(plan.steps) ? plan.steps : (Array.isArray(plan.steps_json) ? plan.steps_json : []);
  const stepIndex = Number(plan.current_step_index || 0);
  const step = steps[stepIndex] || {};
  const actionLabels = {
    move: "前往目的地", enter: "进入场所", queue: "排队等待", consume: "使用餐饮服务",
    rest: "休息恢复", use_facility: "使用设施", observe: "观察环境", interact: "与他人互动"
  };
  const movement = String(spatialState?.movement_status || "");
  if (movement === "moving" || movement === "replanning") return `${describeSpatialPresence(spatialState).status} · ${actionLabels.move}`;
  if (movement === "waiting") return "排队 / 等待准入";
  if (!plan || !plan.status || plan.status === "none") return "自主行动中";
  if (plan.status === "failed") return "行动计划失败，等待重新选择";
  if (plan.status === "completed") return "刚完成计划，等待下一项安排";
  return `${actionLabels[step.action] || step.action || "执行行动"} · 第 ${stepIndex + 1}/${Math.max(steps.length, 1)} 步`;
}

function bodyAlertLabel(agent) {
  const state = WorldStore.bodyStates.get(Number(agent.id));
  const alerts = state?.alerts || [];
  return alerts.length ? ` · ${alerts.slice(0, 2).join("、")}` : "";
}

function renderList() {
  const list = $("agentList");
  if (!list) return;
  list.innerHTML = "";
  WorldStore.agents.forEach((a, i) => {
    const b = document.createElement("button");
    b.className = "agent";
    const avatar = avatarFiles[(Number(a.id || i + 1) - 1 + avatarFiles.length) % avatarFiles.length];
    b.innerHTML = `<img class="avatar-photo" src="/avatars/${avatar}" alt="${a.name || "Agent"}的卡通形象"><span><span class="agent-name">${a.name || "未命名"}</span><span class="agent-meta">${a.role || "校园居民"}${bodyAlertLabel(a)}<br>${movementLabel(a)}</span></span>`;
    b.onclick = () => openProfile(i);
    list.append(b);
  });
  if ($("agentCount")) $("agentCount").textContent = `${WorldStore.agents.length} 位 Agent 正在校园中生活`;
}

function renderEnvironment() {
  const e = WorldStore.world.environment || WorldStore.world.campus_state || {};
  const entries = [["天气", `${e.weather || "校园天气"} ${e.temperature ?? ""}°C`], ["校园时间", e.real_time || e.time_slot || "模拟运行中"], ["考试压力", `${e.exam_pressure ?? "--"}/100`], ["校园人流", `${e.campus_flow ?? "--"}/100`], ["校园情绪", e.campus_mood || "平稳"], ["活动热度", `${e.activity_heat ?? "--"}/100`]];
  if ($("environment")) {
    $("environment").innerHTML = entries.map(([k, v]) => `<div class="env"><span>${k}</span><strong>${v}</strong></div>`).join("");
  }
  renderGeoSummary();
}

function renderGeoSummary() {
  const box = $("geoSummary");
  if (!box) return;
  const scene = WorldStore.spatialScene || {};
  const world = spatialWorlds.find(item => item.world_key === currentWorldKey) || {};
  const bounds = scene.wgs84_bounds || world.wgs84_bounds;
  const metric = scene.bounds || {};
  const buildings = (scene.nodes || []).filter(node => node.node_type === "building").length;
  const agents = Array.from(WorldStore.spatialAgents.values()).filter(agent => !metric.min_x || (agent.x >= metric.min_x && agent.x <= metric.max_x && agent.z >= metric.min_z && agent.z <= metric.max_z)).length;
  const coordinateLabel = Array.isArray(bounds) && bounds.length === 4
    ? `${Number(bounds[0]).toFixed(4)}, ${Number(bounds[1]).toFixed(4)} → ${Number(bounds[2]).toFixed(4)}, ${Number(bounds[3]).toFixed(4)}`
    : "等待 WGS84 坐标范围";
  box.innerHTML = `<div class="geo-row"><span>地图</span><strong>${escapeHtml(world.name || currentWorldKey || "校园空间")}</strong></div><div class="geo-row"><span>坐标系</span><strong>WGS84 + 本地米制</strong></div><div class="geo-row"><span>地理范围</span><strong>${escapeHtml(coordinateLabel)}</strong></div><div class="geo-row"><span>当前窗口</span><strong>${buildings} 建筑 · ${agents} Agent</strong></div>`;
}

function renderWorldPulse() {
  const e = WorldStore.world.environment || {};
  let pulse = $("worldPulse");
  if (!pulse) {
    pulse = document.createElement("section");
    pulse.id = "worldPulse";
    pulse.className = "world-pulse";
    document.querySelector(".centerboard")?.insertAdjacentElement("afterbegin", pulse);
  }
  const spaces = (WorldStore.world.spaces || {}).spaces || [];
  const activeSpaces = spaces.filter(space => (space.effective_status || space.status) === "开放").length;
  const eventCount = (WorldStore.world.events || []).length;
  pulse.innerHTML = [["活跃居民", WorldStore.agents.length, "正在自主生活"], ["开放空间", `${activeSpaces}/${spaces.length || 7}`, "校园可达区域"], ["今日事件", eventCount, "环境与行动记录"], ["世界温度", `${e.temperature ?? "--"}°C`, e.weather || "模拟环境"]].map(([label, value, note]) => `<div class="pulse-item"><span class="pulse-label"><i></i>${label}</span><strong class="pulse-value">${value}</strong><span class="pulse-note">${note}</span></div>`).join("");
}

function currentWorldClock() {
  if (runtimeClockBase) return new Date(runtimeClockBase.getTime() + Date.now() - runtimeClockSyncedAt);
  const e = WorldStore.world?.environment || {}, text = e.real_date && e.real_time ? `${e.real_date}T${e.real_time}` : "";
  const parsed = text ? new Date(text) : null;
  return parsed && !Number.isNaN(parsed.getTime()) ? parsed : new Date();
}

function formatWorldClock(short = false) {
  const clock = currentWorldClock();
  return short ? clock.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }) : clock.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

function formatWorldTimestamp(value) {
  if (!value) return "";
  const normalized = String(value).trim().replace(" ", "T");
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

function eventTag(event) { return event.event_type === "observer_model_detail" ? '<span class="event-tag">观察者触发</span>' : ""; }
function eventContent(event) {
  const text = event.content || "";
  if (event.event_type === "world_tick_failed" && event.payload?.error) {
    return `${text.replace(/：TypeError$/, "")}：${event.payload.error}`;
  }
  return text;
}

function worldTickFailureDisplay(event) {
  const error = String(event?.payload?.error || event?.content || "").toLowerCase();
  if (error.includes("schemamigrationrequired") || error.includes("missing columns")) {
    return {
      title: "运行健康提示",
      content: "后台 tick 未完成：数据库结构尚未更新。完成数据库迁移后，下一轮会自动恢复。"
    };
  }
  if (error.includes("正在执行中") || error.includes("concurrent") || error.includes("lock")) {
    return {
      title: "运行健康提示",
      content: "后台 tick 已由另一个运行实例接管；本轮跳过，当前运行不会被中断。"
    };
  }
  return {
    title: event?.title || "运行健康提示",
    content: "后台 tick 未完成，系统将在下一轮重试；技术详情已保留在审计层。"
  };
}

function compactEventKey(event) {
  if (event.event_type === "world_tick_failed") return `${event.event_type}:${eventContent(event)}`;
  if (event.event_type === "world_multiscale_update") return `${event.event_type}:${event.payload?.update_key || ""}:${event.payload?.cadence || ""}:${eventContent(event)}`;
  return "";
}

function compactEventsForDisplay(events = []) {
  const compact = [];
  events.forEach(event => {
    const previous = compact[compact.length - 1], key = compactEventKey(event);
    if (previous && key && previous.compact_key === key) {
      previous.repeat_count = Number(previous.repeat_count || 1) + 1;
      previous.created_at = event.created_at || previous.created_at;
      previous.id = event.id || previous.id;
      return;
    }
    compact.push({ ...event, compact_key: key, display_content: eventContent(event) });
  });
  return compact;
}

function latestAgentEvent(agent) { return (WorldStore.worldEvents || []).slice().reverse().find(event => Number(event.resident_id) === Number(agent.id)); }
function isResidentialLocation(location = "") {
  const text = String(location).toLowerCase();
  return ["宿舍", "公寓", "住宅", "residence"].some(token => text.includes(token));
}
function agentActivityStatus(agent) {
  const event = latestAgentEvent(agent), action = event?.payload?.action || "";
  const sleepState = WorldStore.bodyStates.get(Number(agent.id))?.sleep_state;
  const nightStates = {
    deep_sleep: ["熟睡中", "#5f6fa8"],
    light_sleep: ["浅眠 / 微觉醒", "#8a77bd"],
    night_activity: ["夜间活动", "#d99445"],
    night_shift: ["夜班中", "#b76a45"],
    insomnia_discomfort: ["失眠 / 不适", "#d45d75"],
  };
  if (nightStates[sleepState]) return nightStates[sleepState];
  const hour = currentWorldClock().getHours();
  if (hour < 6 && isResidentialLocation(agent.location)) return ["熟睡中", "#5f6fa8"];
  if (["chat", "collaborate", "club_activity", "conflict"].includes(action)) return ["交互中", "#e85d75"];
  if (["reflect", "observe"].includes(action)) return ["思考中", "#6aa7d8"];
  if (["move", "consume", "rest", "queue", "attend_class"].includes(action)) return ["探索中", "#4f8b58"];
  return ["探索中", "#4f8b58"];
}

function actionPhrase(agent, event) {
  const action = event?.payload?.action || "", location = event?.location || agent.location || "校园";
  const deferred = event?.payload?.runtime_decision?.deferred_action;
  if (action === "move") return deferred ? `前往${location}，准备${deferred === "consume" ? "吃饭" : deferred === "rest" ? "休息" : "执行计划"}` : `前往${location}`;
  if (action === "consume") return `在${location}补充食物`;
  if (action === "rest") return `在${location}休息恢复`;
  if (action === "queue") return `在${location}排队等待`;
  if (action === "attend_class") return `在${location}上课`;
  if (action === "chat") return `在${location}轻量交流`;
  if (action === "collaborate") return `在${location}协作`;
  if (action === "reflect") return `在${location}整理状态`;
  if (action === "observe") return `观察${location}`;
  return location;
}

function bodyRiskScore(agent) {
  const body = WorldStore.bodyStates.get(Number(agent.id)) || {};
  const hunger = Number(body.hunger || 0), fatigue = Number(body.fatigue || 0), health = Number(body.health ?? 100), attention = Number(body.attention ?? 100);
  return Math.max(hunger, fatigue, 100 - health, 100 - attention);
}

function bodyRiskLabel(agent) {
  const body = WorldStore.bodyStates.get(Number(agent.id)) || {};
  if (!Object.keys(body).length) return bodyAlertLabel(agent).replace(/^ · /, "") || agent.role || "校园居民";
  const risks = [];
  if (Number(body.hunger || 0) >= 75) risks.push("饥饿");
  if (Number(body.fatigue || 0) >= 75) risks.push("疲劳");
  if (Number(body.health ?? 100) < 55) risks.push("健康风险");
  if (Number(body.attention ?? 100) < 35) risks.push("注意力低");
  return risks.join("、") || "状态稳定";
}

function monitorProgress(agent) {
  const body = WorldStore.bodyStates.get(Number(agent.id)) || {};
  if (!Object.keys(body).length) return 52 + (Number(agent.id || 0) % 9);
  return Math.max(8, Math.min(100, Math.round(bodyRiskScore(agent))));
}

function renderStudentMonitor() {
  const counts = { "探索中": 0, "思考中": 0, "休息中": 0, "交互中": 0 };
  WorldStore.agents.forEach(agent => { const [label] = agentActivityStatus(agent); counts[label] = (counts[label] || 0) + 1; });
  const focusAgents = [...WorldStore.agents].sort((a, b) => bodyRiskScore(b) - bodyRiskScore(a)).slice(0, 4);
  const worldTime = formatWorldClock(true);
  const quietLabel = counts["休息中"] ? "休息中" : "思考中";
  const quietColor = counts["休息中"] ? "#8a77bd" : "#6aa7d8";
  const quietCount = counts[quietLabel] || 0;
  return `<section class="student-monitor"><div class="student-monitor-head"><h3>学生状态监控</h3><span class="monitor-time">${escapeHtml(WorldStore.observedFocus)} · ${worldTime}</span></div><div class="monitor-stats"><div class="monitor-stat" style="--stat-color:#4f8b58"><strong>${counts["探索中"] || 0}</strong><span>探索中</span></div><div class="monitor-stat" style="--stat-color:${quietColor}"><strong>${quietCount}</strong><span>${quietLabel}</span></div><div class="monitor-stat" style="--stat-color:#e85d75"><strong>${counts["交互中"] || 0}</strong><span>交互中</span></div></div><div class="monitor-list">${focusAgents.map(agent => { const [statusLabel, color] = agentActivityStatus(agent), progress = monitorProgress(agent), event = latestAgentEvent(agent), target = actionPhrase(agent, event), risk = bodyRiskLabel(agent); return `<button class="monitor-row" style="--row-color:${color}" data-agent-id="${agent.id}"><span><strong>${escapeHtml(agent.name || "Agent")}</strong><small>${escapeHtml(target)} · ${escapeHtml(risk)}</small><span class="monitor-progress" style="--value:${progress}%"><i></i></span></span><b class="monitor-badge">${statusLabel}</b></button>`; }).join("") || '<div class="empty">等待 Agent 状态。</div>'}</div></section>`;
}

function bindStudentMonitor() {
  document.querySelectorAll(".monitor-row[data-agent-id]").forEach(row => row.onclick = () => {
    const index = WorldStore.agents.findIndex(agent => Number(agent.id) === Number(row.dataset.agentId));
    if (index >= 0) openProfile(index);
  });
}

function renderObserverHud() {
  const e = WorldStore.world?.environment || {}, runtime = WorldStore.worldRuntime || {}, status = runtime.status === "running" ? "运行中" : "已暂停", worldTime = formatWorldClock(true), worldDate = currentWorldClock().toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" });
  const activeWorld = (spatialWorlds || []).find(w => w.world_key === currentWorldKey) || {};
  const boundsLabel = activeWorld.wgs84_bounds ? `WGS84 [${activeWorld.wgs84_bounds.map(v => v.toFixed(3)).join(", ")}]` : "米制仿真";
  if ($("observerCardTitle")) $("observerCardTitle").textContent = activeWorld.name || "校园真实图谱";
  if ($("observerSubtitle")) $("observerSubtitle").textContent = `${status} · ${worldDate} ${worldTime} · ${e.weather || "校园天气"} ${e.temperature ?? "--"}°C`;
  if ($("observerMetrics")) {
    $("observerMetrics").innerHTML = [
      `地图 ${activeWorld.name || "校园"}`,
      `坐标 ${boundsLabel}`,
      `时段 ${e.time_slot || "运行中"}`,
      `活跃 Agent ${WorldStore.agents.length}`,
      `拖动浏览 · 按钮或手势缩放`
    ].map(text => `<span class="hud-chip">${text}</span>`).join("");
  }
  if ($("observerEvents")) {
    $("observerEvents").innerHTML = renderStudentMonitor();
    bindStudentMonitor();
  }
  if ($("observerFocusTitle")) $("observerFocusTitle").textContent = `观察：${WorldStore.observedFocus}`;
  if ($("observerFocusText")) $("observerFocusText").textContent = "拖动浏览真实地图，使用右侧按钮或手势缩放；点击建筑或 Agent 查看局部状态。";
}

function ensureRuntimePanel() {
  let panel = $("runtimePanel");
  if (panel) return panel;
  panel = document.createElement("section");
  panel.id = "runtimePanel";
  panel.className = "runtime-panel";
  panel.innerHTML = '<div class="runtime-card"><h2>世界运行时</h2><div class="runtime-state"><i id="runtimeDot" class="runtime-dot"></i><span id="runtimeStatus">正在连接</span></div><div class="runtime-meta" id="runtimeMeta">等待 runtime 数据</div><div class="runtime-actions admin-only"><button id="worldStart">启动</button><button id="worldPause">暂停</button><button id="worldTick">推进 tick</button></div></div><div class="runtime-card"><h2>实时事件流</h2><div class="small" style="padding:0 0 8px">仅显示与当前校园生活相关的最新变化</div><div class="event-stream" id="worldEventStream"><div class="empty" style="padding:8px 0">等待世界事件...</div></div></div>';
  const pulse = $("worldPulse"), board = document.querySelector(".centerboard");
  if (pulse) pulse.insertAdjacentElement("afterend", panel); else board.insertAdjacentElement("afterbegin", panel);
  if ($("worldStart")) $("worldStart").onclick = () => adminWorldAction("/api/admin/world/start");
  if ($("worldPause")) $("worldPause").onclick = () => adminWorldAction("/api/admin/world/pause");
  if ($("worldTick")) $("worldTick").onclick = () => adminWorldAction("/api/admin/world/tick");
  return panel;
}

function renderWorldRuntime() {
  ensureRuntimePanel();
  const runtime = WorldStore.worldRuntime || {}, status = runtime.status || "paused", budget = runtime.budget || {}, worldTime = formatWorldClock(false);
  if ($("runtimeDot")) $("runtimeDot").className = `runtime-dot ${status}`;
  if ($("runtimeStatus")) $("runtimeStatus").textContent = status === "running" ? "后台运行中" : "已暂停";
  if ($("runtimeMeta")) $("runtimeMeta").textContent = `${worldTime} · tick ${runtime.latest_tick?.tick_index ?? "--"} · 自动模型 ${budget.auto_model_calls_used ?? 0}/${budget.daily_auto_model_budget ?? 100}`;
  if ($("dayLabel")) $("dayLabel").textContent = `${status === "running" ? "运行中" : "已暂停"} · ${(WorldStore.world?.environment || {}).weather || "校园"} · ${worldTime}`;
}

function renderWorldEvents() {
  ensureRuntimePanel();
  const box = $("worldEventStream");
  if (!box) return;
  const seen = new Set();
  // A completed tick resolves earlier tick failures.  Keep their full audit
  // record on the backend, but do not keep a resolved incident in the live
  // stream as though it were still affecting the current world.
  const latestCompletedTickEventId = Math.max(
    0,
    ...WorldStore.worldEvents
      .filter(event => event.event_type === "world_tick_complete")
      .map(event => Number(event.id) || 0)
  );
  const visible = compactEventsForDisplay(WorldStore.worldEvents).slice().reverse().filter(event => {
    if (event.event_type === "observer_model_detail") return false;
    if (event.event_type === "world_tick_failed" && (Number(event.id) || 0) < latestCompletedTickEventId) return false;
    const action = event.payload?.action || event.event_type || "event";
    const key = `${action}:${event.resident_id || "world"}:${event.location || ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 8);
  box.innerHTML = visible.map(event => {
    const failed = event.event_type === "world_tick_failed";
    const failureDisplay = failed ? worldTickFailureDisplay(event) : null;
    const title = failureDisplay?.title || event.title || "世界变化";
    const content = failureDisplay?.content || event.display_content || eventContent(event);
    return `<div class="event-entry ${failed ? "event-warning" : ""}"><strong>${escapeHtml(title)}${event.repeat_count > 1 ? ` · 重复 ${event.repeat_count} 次` : ""}</strong>${escapeHtml(content)}<span class="progress-meta">${event.slot || ""}${event.location ? ` · ${escapeHtml(event.location)}` : ""}${event.resident_id ? ` · Agent ${event.resident_id}` : ""}${event.display_time ? ` · ${event.display_time}` : event.created_at ? ` · ${formatWorldTimestamp(event.created_at)}` : ""}</span></div>`;
  }).join("") || '<div class="empty" style="padding:8px 0">当前没有需要关注的世界变化。</div>';
}

async function loadWorldRuntime() {
  try {
    const response = await fetch("/api/world/runtime");
    if (!response.ok) throw new Error("runtime 接口失败");
    WorldStore.worldRuntime = await response.json();
    if (WorldStore.worldRuntime.world_time) {
      runtimeClockBase = new Date(WorldStore.worldRuntime.world_time);
      runtimeClockSyncedAt = Date.now();
    }
    renderWorldRuntime();
  } catch (e) {
    ensureRuntimePanel();
    if ($("runtimeMeta")) $("runtimeMeta").textContent = `runtime 暂不可用：${e.message}`;
  }
}

async function fetchOptionalJson(url, timeoutMs = 5000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) return { ok: false, error: `接口返回 ${response.status}` };
    return { ok: true, data: await response.json() };
  } catch (error) {
    return { ok: false, error: error?.name === "AbortError" ? "读取超时" : (error?.message || "网络请求失败") };
  } finally {
    clearTimeout(timer);
  }
}

function profileEmpty(message) {
  return `<div class="empty">${escapeHtml(message)}</div>`;
}

function profileList(items, emptyMessage) {
  if (!items?.length) return profileEmpty(emptyMessage);
  return items.slice(0, 4).map(item => `<div class="profile-recent"><strong>${escapeHtml(item.title || item.name || item.action || item.location || "校园记录")}</strong>${escapeHtml(item.content || item.summary || item.reason || item.description || item.role || "已记录一条状态信息")}</div>`).join("");
}

function profileRelationList(items) {
  if (!items?.length) return profileEmpty("当前没有可展示的关系记录。");
  return items.slice(0, 4).map(item => {
    const name = item.name || item.target_name || item.to_name || "校园居民";
    const role = item.role || item.target_role || "";
    const score = item.score ?? item.relationship_score ?? item.trust ?? "—";
    const count = item.interaction_count ?? item.count;
    return `<div class="profile-card"><strong>${escapeHtml(name)}${role ? ` · ${escapeHtml(role)}` : ""}</strong><span>关系强度 ${escapeHtml(score)}${count != null ? ` · 已互动 ${escapeHtml(count)} 次` : ""}</span></div>`;
  }).join("");
}

function profileActivityList(items, emptyMessage) {
  const seen = new Set();
  const distinct = (items || []).filter(item => {
    const detail = item.content || item.summary || item.reason || item.description || item.execution?.result?.description || "";
    const key = `${item.day || ""}:${item.action || item.title || item.event_type || ""}:${detail}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  distinct.sort((a, b) => Number(b.day || 0) - Number(a.day || 0) || String(b.created_at || "").localeCompare(String(a.created_at || "")));
  if (!distinct.length) return profileEmpty(emptyMessage);
  const actionLabels = { move: "移动", consume: "用餐", rest: "休息", observe: "观察", chat: "交流", collaborate: "协作", queue: "排队", attend_class: "上课" };
  return distinct.slice(0, 4).map(item => {
    const rawAction = item.action || item.title || item.event_type || "校园行动";
    const action = actionLabels[rawAction] || rawAction;
    const detail = item.content || item.summary || item.reason || item.description || item.execution?.result?.description || "已记录一条运行时动态。";
    return `<div class="profile-recent"><strong>${escapeHtml(action)}${item.day != null ? ` · 第 ${escapeHtml(item.day)} 天` : ""}</strong>${escapeHtml(detail)}</div>`;
  }).join("");
}

function relationshipColor(metric) {
  return { affinity: "#8c6ab3", trust: "#1769aa", cooperation: "#45a878", competition: "#d39142", conflict: "#c94c4c" }[metric] || "#1769aa";
}

function relationshipType(link) {
  if (link.emergent_interpretation?.label) return link.emergent_interpretation.label;
  if (Number(link.conflict) > 45) return "冲突风险";
  if (Number(link.cooperation) > 65) return "合作伙伴";
  if (Number(link.trust) > 70) return "可信关系";
  return "弱联系/待观察";
}

function renderProfileRelationships(graph) {
  const box = $("profileRelationships");
  const links = (graph?.links || []).slice(0, 10);
  const nodes = graph?.nodes || [];
  if (!box) return;
  if (!links.length) { box.innerHTML = profileEmpty("这位 Agent 尚未建立稳定关系。"); return; }
  const active = links[0];
  const metrics = [["好感", "affinity"], ["信任", "trust"], ["合作", "cooperation"], ["竞争", "competition"], ["冲突", "conflict"]];
  const positions = links.map((link, index) => ({
    link,
    node: nodes.find(node => Number(node.id) === Number(link.to)) || {},
    x: 50 + 39 * Math.cos((Math.PI * 2 * index) / links.length - Math.PI / 2),
    y: 50 + 34 * Math.sin((Math.PI * 2 * index) / links.length - Math.PI / 2)
  }));
  const color = relationshipColor(profileRelationshipMetric);
  const lines = positions.map(item => `<line x1="50%" y1="50%" x2="${item.x}%" y2="${item.y}%" stroke="${color}" stroke-width="${Math.max(1.4, Math.min(6, Number(item.link[profileRelationshipMetric] || 0) / 18))}" opacity=".6"/>`).join("");
  const labels = positions.map(item => `<span class="relationship-node" title="${escapeHtml(item.node.name || "校园居民")}" style="left:calc(${item.x}% - 17px);top:calc(${item.y}% - 17px)">${escapeHtml(String(item.node.name || "人").slice(0, 1))}</span>`).join("");
  const activeNode = nodes.find(node => Number(node.id) === Number(active.to)) || {};
  const toolbar = metrics.map(([label, key]) => `<button type="button" data-profile-relation-metric="${key}" class="${key === profileRelationshipMetric ? "active" : ""}">${label}</button>`).join("");
  const bars = metrics.map(([label, key]) => `<div class="relationship-bar"><span>${label}</span><i style="--value:${Math.max(0, Math.min(100, Number(active[key] ?? 0)))}%;--bar:${relationshipColor(key)}"></i><b>${active[key] ?? 0}</b></div>`).join("");
  box.innerHTML = `<div class="relationship-toolbar">${toolbar}</div><div class="relationship-graph"><svg viewBox="0 0 100 100" preserveAspectRatio="none">${lines}</svg><span class="relationship-node root">我</span>${labels}</div><div class="relationship-inspector"><strong>${escapeHtml(activeNode.name || "校园居民")} · ${relationshipType(active)}</strong><span>${escapeHtml(activeNode.role || "校园居民")} · 综合关系 ${active.score ?? "—"} · 互动 ${active.interaction_count ?? 0} 次</span><div class="relationship-bars">${bars}</div></div>${links.map(link => { const node = nodes.find(item => Number(item.id) === Number(link.to)) || {}; return `<div class="relation-entry"><strong>${escapeHtml(node.name || "校园居民")} · ${relationshipType(link)}</strong><div class="relation-metrics">好感 ${link.affinity} · 信任 ${link.trust} · 合作 ${link.cooperation} · 竞争 ${link.competition} · 冲突 ${link.conflict}</div></div>`; }).join("")}`;
  box.querySelectorAll("[data-profile-relation-metric]").forEach(button => button.onclick = () => { profileRelationshipMetric = button.dataset.profileRelationMetric; renderProfileRelationships(graph); });
}

function renderProfileBase(agent) {
  const name = agent.name || "未命名居民";
  const role = agent.role || "校园居民";
  const presence = describeSpatialPresence(WorldStore.spatialAgents.get(Number(agent.id)), agent.location || "校园");
  const location = presence.location;
  if ($("detailName")) $("detailName").textContent = `${name}的人物介绍`;
  if ($("detailRole")) $("detailRole").textContent = `${role} · ${location}`;
  if ($("profileSnapshot")) $("profileSnapshot").textContent = `${name}是${role}，${presence.narrative}人物档案正在根据实时状态补全。`;
  if ($("detailState")) $("detailState").innerHTML = `<dl><div class="row"><dt>当前位置</dt><dd>${escapeHtml(location)}</dd></div><div class="row"><dt>行动状态</dt><dd>${escapeHtml(presence.status)}</dd></div><div class="row"><dt>当前目标</dt><dd>${escapeHtml(presence.target || agent.goal || agent.current_task || "自主观察与行动")}</dd></div></dl>`;
  if ($("profileCapabilities")) $("profileCapabilities").innerHTML = profileEmpty("正在读取行动条件…");
  if ($("profileRelationships")) $("profileRelationships").innerHTML = profileEmpty("正在读取关系…");
  if ($("profileRecent")) $("profileRecent").innerHTML = profileEmpty("正在读取最近动态…");
  if ($("profilePerception")) $("profilePerception").innerHTML = profileEmpty("正在读取局部观察…");
}

function renderProfileDetails(agent, payloads) {
  const [modules, body, spatial, capability, social, activity, perception, actionPlan] = payloads;
  const moduleData = modules.data?.modules || modules.data || {};
  const physical = moduleData.Physical || moduleData.physical || {};
  const mental = moduleData.Mental || moduleData.mental || {};
  const schedule = moduleData.Schedule || moduleData.schedule || {};
  const bodyData = body.data || {};
  const spatialData = spatial.data || {};
  const task = mental.current_task || mental.task || schedule.current_task || agent.current_task || "自主观察环境";
  const mood = bodyData.mood || physical.mood || agent.mood || "平稳";
  const presence = describeSpatialPresence(spatialData, agent.location || "校园");
  const location = presence.location;
  const actionStatus = describeActionPlan(actionPlan.data, spatialData);
  const energy = Number(bodyData.energy ?? physical.energy ?? 70);
  const hydration = Number(bodyData.hydration ?? 25);
  const nutrition = Number(bodyData.nutrition ?? 78);
  const activityLoad = Number(bodyData.activity_load ?? 18);
  const illnessLoad = Number(bodyData.illness_load ?? 0);
  const money = physical.money ?? agent.money ?? "—";
  const personality = agent.personality || "个性仍在行动中显现";
  if ($("profileSnapshot")) $("profileSnapshot").textContent = `${agent.name || "这位 Agent"}是${agent.role || "校园居民"}，性格底色偏向${personality}。${presence.narrative} 当前状态${mood}，行动阶段为${actionStatus}；长期牵引是“${agent.goal || "根据环境与个人资源调整目标"}”。`;
  if ($("detailState")) $("detailState").innerHTML = `<dl><div class="row"><dt>当前位置</dt><dd>${escapeHtml(location)}</dd></div>${presence.location !== presence.recent ? `<div class="row"><dt>最近经过</dt><dd>${escapeHtml(presence.recent)}</dd></div>` : ""}<div class="row"><dt>行动状态</dt><dd>${escapeHtml(actionStatus)}</dd></div><div class="row"><dt>行动目标</dt><dd>${escapeHtml(presence.target)}</dd></div><div class="row"><dt>路径进度</dt><dd>${escapeHtml(presence.progress)}</dd></div><div class="row"><dt>能量</dt><dd>${energy}/100<div class="bar"><i style="width:${Math.max(0, Math.min(100, energy))}%"></i></div></dd></div><div class="row"><dt>水分状态</dt><dd>${hydration}/100 ${hydration >= 70 ? "· 需要饮水" : "· 正常"}</dd></div><div class="row"><dt>营养储备</dt><dd>${nutrition}/100 ${nutrition <= 35 ? "· 需要均衡饮食" : "· 正常"}</dd></div><div class="row"><dt>活动负荷</dt><dd>${activityLoad}/100</dd></div>${illnessLoad > 0 ? `<div class="row"><dt>身体不适</dt><dd>${illnessLoad}/100</dd></div>` : ""}<div class="row"><dt>预算</dt><dd>${escapeHtml(money)}</dd></div><div class="row"><dt>情绪</dt><dd>${escapeHtml(mood)}</dd></div><div class="row"><dt>当前日程</dt><dd>${escapeHtml(schedule.current_task || "自主安排")}</dd></div></dl><p class="task">${escapeHtml(task)}</p>`;
  const capabilityData = capability.data || {};
  const capProfile = capabilityData.capability_profile || {};
  const spatialCap = capabilityData.spatial_capability || {};
  const capabilityLabels = { physical_endurance: "体力耐受", time_management: "时间管理", risk_tolerance: "风险承受", information_literacy: "信息识读", social_capital: "社会支持", institutional_access: "制度可达", economic_access: "经济可达" };
  const readableCapabilities = Object.entries(capProfile).filter(([key]) => key !== "resident_id").slice(0, 4).map(([key, value]) => `${capabilityLabels[key] || key} ${value}`).join(" · ");
  const ranked = Object.entries(capProfile).filter(([key]) => capabilityLabels[key]).map(([key, value]) => [capabilityLabels[key], Number(value)]).sort((a, b) => b[1] - a[1]);
  const opportunities = (capabilityData.opportunities || []).filter(item => Number(item.access_level) < 45).slice(0, 2);
  if ($("profileCapabilities")) $("profileCapabilities").innerHTML = capability.ok
    ? `<div class="profile-recent"><strong>相对优势</strong>${escapeHtml(ranked.slice(0, 3).map(([label, value]) => `${label} ${value}`).join(" · ") || readableCapabilities || "尚无细化参数")}</div>${opportunities.length ? `<div class="profile-recent"><strong>当前机会限制</strong>${escapeHtml(opportunities.map(item => `${item.opportunity_key} ${item.access_level}`).join(" · "))}</div>` : `<div class="profile-recent"><strong>当前机会条件</strong>移动速度 ${escapeHtml(spatialCap.base_speed_m_per_min ?? "—")} m/min · 感知半径 ${escapeHtml(spatialCap.perception_radius_m ?? "—")} m</div>`}`
    : profileEmpty(`行动条件暂时不可用（${capability.error || "读取失败"}）；基础档案仍可正常浏览。`);
  if ($("profileRelationships")) $("profileRelationships").innerHTML = social.ok
    ? ""
    : profileEmpty(`关系记录暂时不可用（${social.error || "读取失败"}）。`);
  if (social.ok) renderProfileRelationships(social.data);
  const activityItems = activity.data?.timeline || activity.data?.events || [];
  if ($("profileRecent")) $("profileRecent").innerHTML = activity.ok
    ? profileActivityList(activityItems.map(item => ({ ...item, action: item.decision?.action || item.action, content: item.decision?.reason || item.execution?.result?.description || item.content })), "当前没有可回溯的最近动态。")
    : profileEmpty(`最近动态暂时不可用（${activity.error || "读取失败"}）。`);
  const modality = { self: "亲历", visual: "看见", auditory: "听见", announced: "收到公告", inferred: "推断" };
  const observations = [
    ...(perception.data?.observations || []).map(item => ({ ...item, action: `${modality[item.modality] || "观察"} · ${item.origin_node_name || "当前位置"}`, content: item.summary || item.content })),
    ...(perception.data?.received_information || []).map(item => ({ ...item, action: `已接收 · ${item.channel || "消息"}`, content: item.title || item.summary || item.content }))
  ];
  if ($("profilePerception")) $("profilePerception").innerHTML = perception.ok
    ? profileActivityList(observations, "当前还没有形成可回溯的局部观察。")
    : profileEmpty(`局部认知证据暂时不可用（${perception.error || "读取失败"}）。`);
}

async function loadProfileDetails(agent, token) {
  const id = encodeURIComponent(agent.id);
  const payloads = await Promise.all([
    fetchOptionalJson(`/api/agents/${id}/modules`),
    fetchOptionalJson(`/api/agents/${id}/body-state`),
    fetchOptionalJson(`/api/agents/${id}/spatial-state`),
    fetchOptionalJson(`/api/agents/${id}/capability-profile`),
    fetchOptionalJson(`/api/agents/${id}/social-graph`),
    fetchOptionalJson(`/api/agents/${id}/profile-activity?timeline_limit=6`),
    fetchOptionalJson(`/api/agents/${id}/perception-evidence?limit=4`),
    fetchOptionalJson(`/api/agents/${id}/action-plan`)
  ]);
  if (token !== profileRequestToken || Number(selectedAgent?.id) !== Number(agent.id)) return;
  renderProfileDetails(agent, payloads);
}

function lifeEventMarkup(item) {
  const title = item.display_title || item.title || item.action || "校园经历";
  const content = item.content || item.summary || item.turning_summary || "已记录一条可回溯经历。";
  return `<article class="life-event ${escapeHtml(item.significance || "ordinary")}"><strong>第${escapeHtml(item.day ?? "—")}天 · ${escapeHtml(title)}</strong><p>${escapeHtml(content)}</p><div class="life-event-meta"><span>${escapeHtml(item.location || "校园")}</span><span>${escapeHtml(item.source || "运行记录")}</span></div></article>`;
}

function setLifeCourseView(view) {
  lifeCourseView = ["actions", "memories", "combined"].includes(view) ? view : "actions";
  document.querySelectorAll("[data-life-view]").forEach(button => button.classList.toggle("active", button.dataset.lifeView === lifeCourseView));
  document.querySelectorAll("[data-life-section]").forEach(section => section.classList.toggle("active", section.dataset.lifeSection === lifeCourseView));
}

function renderLifeCourse(payload) {
  const resident = payload.resident || selectedAgent || {};
  const current = payload.current_state || {};
  const actions = payload.action_timeline || [];
  const memories = payload.memory_timeline || [];
  const timeline = payload.timeline || [];
  if ($("lifeCourseTitle")) $("lifeCourseTitle").textContent = `${resident.name || "Agent"}的生命历程`;
  if ($("lifeCourseSubtitle")) $("lifeCourseSubtitle").textContent = `只读回顾 · 共 ${timeline.length} 条可回溯记录`;
  if ($("lifeCourseState")) $("lifeCourseState").textContent = `当前位置：${current.location || resident.location || "校园"} · 情绪：${current.mood || "—"} · 当前行动：${current.current_task || "自主行动"}`;
  if ($("lifeCourseGoal")) $("lifeCourseGoal").textContent = `长期目标：${payload.initial_goal || payload.goals?.[0]?.goal || resident.goal || "尚未记录"}`;
  if ($("lifeCourseStatus")) $("lifeCourseStatus").textContent = `已加载 ${timeline.length} 条记录；行动 ${actions.length} 条，记忆 ${memories.length} 条`;
  if ($("lifeCourseActions")) $("lifeCourseActions").innerHTML = actions.map(lifeEventMarkup).join("") || profileEmpty("暂无可回溯的行动记录。");
  if ($("lifeCourseMemories")) $("lifeCourseMemories").innerHTML = memories.map(lifeEventMarkup).join("") || profileEmpty("暂无可回溯的日记或记忆。");
  if ($("lifeCourseCombined")) $("lifeCourseCombined").innerHTML = timeline.map(lifeEventMarkup).join("") || profileEmpty("暂无可回溯的生命历程记录。");
  if ($("lifeCourseTurning")) $("lifeCourseTurning").innerHTML = (payload.turning_points || []).map(lifeEventMarkup).join("") || profileEmpty("尚未识别到关键转折。");
  if ($("lifeCourseRelationships")) $("lifeCourseRelationships").innerHTML = profileList(payload.relationships || [], "暂无关系演化记录。");
  if ($("lifeCourseGroups")) $("lifeCourseGroups").innerHTML = profileList(payload.groups || [], "暂无群体参与记录。");
  if ($("lifeCourseBoundary")) $("lifeCourseBoundary").textContent = payload.research_boundaries?.message || "生命历程只呈现已有运行记录，不补造未发生的经历。";
  setLifeCourseView(lifeCourseView);
}

async function loadLifeCourse({ older = false } = {}) {
  if (!lifeCourseResidentId) return;
  if ($("lifeCourseStatus")) $("lifeCourseStatus").textContent = older ? "正在加载更早经历…" : "正在读取生命历程…";
  const currentDay = Number(WorldStore.world?.current_day || 1);
  const toDay = older ? Math.max(1, Number(lifeCourseOldestDay || currentDay) - 1) : currentDay;
  const fromDay = older ? Math.max(1, toDay - 29) : Math.max(1, currentDay - 29);
  try {
    const payload = await ApiClient.fetchLifeCourseWindow(lifeCourseResidentId, fromDay, toDay);
    lifeCoursePayload = payload;
    lifeCourseOldestDay = fromDay;
    renderLifeCourse(payload);
  } catch (error) {
    if ($("lifeCourseStatus")) $("lifeCourseStatus").textContent = `生命历程读取失败：${error.message}`;
    ["lifeCourseActions", "lifeCourseMemories", "lifeCourseCombined"].forEach(id => { if ($(id)) $(id).innerHTML = profileEmpty("暂时无法读取生命历程，请稍后重试。"); });
  }
}

function openLifeCourse() {
  const agent = selectedAgent || WorldStore.agents[WorldStore.selected];
  if (!agent) { showToast("请先选择一名居民"); return; }
  lifeCourseResidentId = agent.id;
  lifeCoursePayload = null;
  lifeCourseOldestDay = null;
  if ($("lifeCourseOverlay")) $("lifeCourseOverlay").classList.add("open");
  loadLifeCourse();
}

function openProfile(i) {
  WorldStore.selected = i;
  const a = WorldStore.agents[i];
  if (!a) return;
  selectedAgent = a;
  const token = ++profileRequestToken;
  document.querySelectorAll(".agent").forEach((el, n) => el.classList.toggle("active", n === i));
  renderProfileBase(a);
  renderProfileCharacter(a);
  if ($("profileOverlay")) $("profileOverlay").classList.add("open");
  requestAnimationFrame(resizeProfile);
  touchObserverSession({ resident_id: a.id });
  loadProfileDetails(a, token);
}

async function load() {
  if ($("status")) $("status").textContent = "正在同步数据层...";
  try {
    const state = await ApiClient.fetchState();
    WorldStore.setWorldState(state);
    applyRandomAgentOrder(state.agents || state.residents || []);
    if ($("dayLabel")) $("dayLabel").textContent = `第 ${WorldStore.world.current_day || 1} 天 · ${(WorldStore.world.environment || {}).weather || "校园运行中"}`;
    renderEnvironment();
    renderWorldPulse();
    renderWorldRuntime();
    renderExternalInformation("正在读取外部资讯...");
    renderCampusMap();
    renderSpaces();
    renderActivities();
    renderNewspaper();
    renderList();

    const [postsResult, externalResult] = await Promise.allSettled([ApiClient.fetchNewspaper(), ApiClient.fetchExternalInformation()]);
    if (postsResult.status === "fulfilled" && postsResult.value) {
      const paperPayload = postsResult.value;
      WorldStore.newsPosts = paperPayload.posts || [];
      WorldStore.newspaperDay = paperPayload.day || WorldStore.world.current_day || 1;
      WorldStore.newspaperEdition = paperPayload.edition || {};
      WorldStore.newspaperArchive = { available_days: paperPayload.available_days || [], previous_day: paperPayload.previous_day, next_day: paperPayload.next_day, current_day: paperPayload.current_day };
    } else {
      WorldStore.newsPosts = [];
    }

    if (externalResult.status === "fulfilled") {
      WorldStore.externalInformation = externalResult.value || [];
    } else {
      WorldStore.externalInformation = [];
    }

    renderExternalInformation(externalResult.status === "rejected" ? "外部资讯暂不可用。" : "");
    renderActivities();
    renderNewspaper();
    if ($("status")) $("status").textContent = "数据层已同步";
  } catch (e) {
    if ($("status")) $("status").textContent = `连接失败：${e.message}`;
  }
}

// Global initialization
document.addEventListener("DOMContentLoaded", () => {
  WorldStore.selectWorld(currentWorldKey);
  const profileCanvas = $("profileScene");
  if (profileCanvas) {
    // The 3D renderer is retained solely for the resident profile character;
    // the primary geographic world is MapLibre 2D.
    initThreeScene(null, profileCanvas);
  }

  if ($("refresh")) $("refresh").onclick = load;
  if ($("detailRefresh")) $("detailRefresh").onclick = load;
  if ($("openNewspaper")) $("openNewspaper").onclick = () => {
    // Open first so a slow or failed edition request never makes the button
    // appear unresponsive; the overlay carries its own loading/error status.
    $("newspaperOverlay")?.classList.add("open");
    loadNewsPosts();
  };
  if ($("closeNewspaper")) $("closeNewspaper").onclick = () => $("newspaperOverlay")?.classList.remove("open");
  if ($("newspaperOverlay")) $("newspaperOverlay").onclick = event => {
    if (event.target === $("newspaperOverlay")) $("newspaperOverlay")?.classList.remove("open");
  };
  if ($("paperPrev")) $("paperPrev").onclick = () => loadNewsPosts(WorldStore.newspaperArchive.previous_day);
  if ($("paperToday")) $("paperToday").onclick = () => loadNewsPosts(WorldStore.newspaperArchive.current_day || WorldStore.world?.current_day);
  if ($("paperNext")) $("paperNext").onclick = () => loadNewsPosts(WorldStore.newspaperArchive.next_day);
  document.querySelectorAll("[data-paper-view]").forEach(button => {
    button.onclick = () => setNewspaperView(button.dataset.paperView);
  });
  if ($("closeProfile")) $("closeProfile").onclick = () => { profileRequestToken += 1; $("profileOverlay").classList.remove("open"); };
  if ($("openLifeCourse")) $("openLifeCourse").onclick = openLifeCourse;
  if ($("closeLifeCourse")) $("closeLifeCourse").onclick = () => $("lifeCourseOverlay").classList.remove("open");
  if ($("lifeCourseLoadMore")) $("lifeCourseLoadMore").onclick = () => loadLifeCourse({ older: true });
  document.querySelectorAll("[data-life-view]").forEach(button => button.onclick = () => setLifeCourseView(button.dataset.lifeView));
  if ($("lifeCourseOverlay")) $("lifeCourseOverlay").onclick = event => { if (event.target === $("lifeCourseOverlay")) $("lifeCourseOverlay").classList.remove("open"); };
  if ($("profileOverlay")) $("profileOverlay").onclick = event => { if (event.target === $("profileOverlay")) $("profileOverlay").classList.remove("open"); };
  if ($("cameraZoomIn")) $("cameraZoomIn").onclick = () => zoomActiveMap(1);
  if ($("cameraZoomOut")) $("cameraZoomOut").onclick = () => zoomActiveMap(-1);
  if ($("jumpToData")) $("jumpToData").onclick = () => $("dataDashboard")?.scrollIntoView({ behavior: "smooth", block: "start" });
  if ($("jumpToAgents")) $("jumpToAgents").onclick = () => $("agentPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });

  addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    ["profileOverlay", "lifeCourseOverlay", "newspaperOverlay"].forEach(id => $(id)?.classList.remove("open"));
  });

  // The initial world catalogue and observer state can arrive in parallel,
  // but the expensive scene is requested exactly once after its world_key is
  // resolved. Periodic observer refreshes below never reload this scene.
  Promise.all([loadObserverState(), loadSpatialWorlds()]).then(async () => {
    await loadSpatialWorld(true, false, true);
    touchObserverSession({});
    setTimeout(load, 300);
  });

  pollWorldEvents();
  setInterval(() => {
    renderObserverHud();
    renderWorldRuntime();
  }, 1000);
  setInterval(loadObserverState, 15000);
  setInterval(pollWorldEvents, 5000);
});
