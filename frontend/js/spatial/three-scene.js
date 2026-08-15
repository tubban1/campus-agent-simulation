/**
 * Three.js 3D Scene Component Module
 * Manages 3D campus WebGL canvas, camera, lighting, 3D building meshes, agent characters, and continuous animation loop.
 */
import * as THREE from "/three/build/three.module.js";
import { $, colors, avatarFiles, defaultSpaces, WorldStore, escapeHtml } from "./world-store.js?v=20260814-png-avatars";

let renderer, profileRenderer, webglAvailable = true;
let scene, camera, sun, ground, grid, root, campusGroup, routeGroup, worldAgentGroup, bubbleGroup;
let profileScene, profileCamera, profileRoot;
let raycaster, pointer, clickableObjects = [];

let turn = -0.22, zoom = 1, mapPanX = 0, mapPanZ = 0, cameraMode = "front", autoOrbit = false;
let cameraYaw = 0;
let dragging = false, pointerTracking = false, pointerMode = "pan";
let pointerX = 0, pointerY = 0, pointerDownX = 0, pointerDownY = 0;

function canUseWebGL() {
  try {
    const probe = document.createElement("canvas");
    return Boolean(probe.getContext("webgl2") || probe.getContext("webgl"));
  } catch {
    return false;
  }
}

function fallbackRenderer(target) {
  const ctx = target.getContext("2d");
  return {
    setPixelRatio() { },
    setClearColor() { },
    setSize(w, h) { if (target) { target.width = Math.max(1, Math.floor(w)); target.height = Math.max(1, Math.floor(h)); } },
    render() { },
    ctx
  };
}

export function initThreeScene(canvas, profileCanvas) {
  if (canvas) {
    try {
      if (!canUseWebGL()) throw new Error("WebGL context unavailable");
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    } catch (error) {
      webglAvailable = false;
      console.warn("WebGL unavailable for optional 3D observer", error.message);
      renderer = fallbackRenderer(canvas);
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setClearColor(0xdce8e6);

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(44, 1, 0.1, 15000);
    root = new THREE.Group();
    scene.add(root);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x3f6a5c, 2.5));
    sun = new THREE.DirectionalLight(0xffffff, 2.3);
    sun.position.set(4, 7, 5);
    scene.add(sun);
    ground = new THREE.Mesh(new THREE.PlaneGeometry(54, 42), new THREE.MeshStandardMaterial({ color: 0x4f8b58, roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -2.05;
    scene.add(ground);
    grid = new THREE.GridHelper(40, 32, 0x5b917b, 0x9bc8b3);
    grid.position.y = -2.04;
    scene.add(grid);
    campusGroup = new THREE.Group();
    routeGroup = new THREE.Group();
    worldAgentGroup = new THREE.Group();
    bubbleGroup = new THREE.Group();
    root.add(campusGroup, routeGroup, worldAgentGroup, bubbleGroup);
    raycaster = new THREE.Raycaster();
    pointer = new THREE.Vector2();
    setObserverCamera();
    resize();
    bindScenePointerControls(canvas);
  }

  if (!profileCanvas) return;
  try {
    if (!canUseWebGL()) throw new Error("WebGL context unavailable");
    profileRenderer = new THREE.WebGLRenderer({ canvas: profileCanvas, antialias: true });
  } catch (error) {
    webglAvailable = false;
    console.warn("WebGL unavailable for profile character", error.message);
    profileRenderer = fallbackRenderer(profileCanvas);
  }

  profileRenderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  profileRenderer.setClearColor(0xdce8e6);

  profileScene = new THREE.Scene();
  profileCamera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
  profileRoot = new THREE.Group();
  // The profile panel is tall and narrow.  Keep enough distance to frame the
  // whole person (head to shoes), rather than cropping the head at the top.
  profileCamera.position.set(0, 0.3, 9.5);
  profileCamera.lookAt(0, 0.25, 0);
  profileScene.add(profileRoot);
  profileScene.add(new THREE.HemisphereLight(0xffffff, 0x3f6a5c, 2.5));
  const profileSun = new THREE.DirectionalLight(0xffffff, 2.1);
  profileSun.position.set(4, 6, 5);
  profileScene.add(profileSun);

  const profileGround = new THREE.Mesh(new THREE.PlaneGeometry(20, 20), new THREE.MeshStandardMaterial({ color: 0x7cb093 }));
  profileGround.rotation.x = -Math.PI / 2;
  profileGround.position.y = -1.04;
  profileScene.add(profileGround);

  resizeProfile();
  // Start the profile render loop; the optional world renderer stays dormant.
  requestAnimationFrame(animate);
}

export function spacePosition(location) {
  const layout = {
    "校务处": [-13, -7],
    "教学楼": [-4.5, -7],
    "商业街": [4, -7],
    "图书馆": [12.5, -7],
    "宿舍区": [-11.5, 6.5],
    "食堂": [-1, 6.5],
    "操场": [9.5, 6.5]
  };
  return layout[location] || [0, 0];
}

function buildingProfile(location, index = 0) {
  const profiles = {
    "校务处": { w: 4.8, h: 2.3, d: 3.4, color: 0x7a8b9e, roof: 0x485868, door: 0x2c3b4a, window: 0xb8d0d8 },
    "教学楼": { w: 6.2, h: 2.8, d: 3.8, color: 0x5b7f70, roof: 0x395347, door: 0x273b32, window: 0xd2e5dc },
    "商业街": { w: 6.8, h: 1.6, d: 3.2, color: 0xc49a6c, roof: 0x8a6239, door: 0x593d22, window: 0xf2dfc6 },
    "图书馆": { w: 5.6, h: 3.2, d: 4.2, color: 0x8f7d98, roof: 0x594862, door: 0x382d3e, window: 0xd8cee0 },
    "宿舍区": { w: 5.8, h: 2.6, d: 3.6, color: 0x6e8898, roof: 0x415664, door: 0x283843, window: 0xcfe0e8 },
    "食堂": { w: 5.2, h: 1.8, d: 3.8, color: 0xbd7c69, roof: 0x854938, door: 0x542b1f, window: 0xf5d9cf },
    "操场": { w: 6.6, h: 0.25, d: 4.6, color: 0x3b7a57, roof: 0x2d5e43, door: 0x1f422e, window: 0x8fc4a7 }
  };
  return profiles[location] || { w: 4.5 + (index % 3), h: 1.8 + (index % 2), d: 3.5, color: 0x6f8275, roof: 0x47564c, door: 0x2d3830, window: 0xcfe2dc };
}

function makeTextSprite(message, parameters = {}) {
  const fontface = parameters.fontface || "Microsoft YaHei, sans-serif";
  const fontsize = parameters.fontsize || 22;
  const borderThickness = parameters.borderThickness || 2;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  canvas.width = 256;
  canvas.height = 128;
  context.font = `Bold ${fontsize}px ${fontface}`;
  context.fillStyle = parameters.bg || "rgba(16,32,51,0.85)";
  context.strokeStyle = parameters.border || "rgba(255,255,255,0.4)";
  context.lineWidth = borderThickness;

  const textWidth = context.measureText(message).width;
  const px = (canvas.width - textWidth) / 2 - 8;
  const py = (canvas.height - fontsize) / 2 - 6;

  context.beginPath();
  context.roundRect(px, py, textWidth + 16, fontsize + 12, 6);
  context.fill();
  context.stroke();

  context.fillStyle = parameters.color || "#ffffff";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(message, canvas.width / 2, canvas.height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  const spriteMaterial = new THREE.SpriteMaterial({ map: texture, transparent: true });
  const sprite = new THREE.Sprite(spriteMaterial);
  sprite.scale.set(3.2, 1.6, 1);
  return sprite;
}

export function makeBuilding(space, index) {
  const location = space.location || space.name;
  const level = Number(space.crowd_percent ?? 0);
  const blocked = (space.effective_status || space.status) !== "开放";
  const profile = buildingProfile(location, index);
  const group = new THREE.Group();
  group.userData = { type: "space", location };
  const bodyColor = blocked ? 0xb66a35 : level > 85 ? 0xb08345 : profile.color;

  const body = new THREE.Mesh(new THREE.BoxGeometry(profile.w, profile.h, profile.d), new THREE.MeshStandardMaterial({ color: bodyColor, roughness: 0.82 }));
  body.position.y = profile.h / 2;
  body.userData = group.userData;
  group.add(body);

  const roof = new THREE.Mesh(new THREE.BoxGeometry(profile.w + 0.18, 0.18, profile.d + 0.18), new THREE.MeshStandardMaterial({ color: profile.roof, roughness: 0.78 }));
  roof.position.y = profile.h + 0.12;
  roof.userData = group.userData;
  group.add(roof);

  const label = makeTextSprite(location, { size: 13, bg: "rgba(19,36,58,.86)" });
  label.position.set(0, profile.h + 0.48, 0);
  group.add(label);

  return { group, body, profile };
}

const CORE_TSINGHUA_LANDMARKS = new Set([
  "大礼堂", "清晏楼", "紫荆学生公寓9号楼", "六教", "主楼", "图书馆", "逸夫馆", "观畴园餐厅",
  "听涛园", "紫荆学生公寓1号楼", "综合体育馆", "理科楼", "二教", "三教", "蒙民伟楼", "新清华学堂"
]);

function buildingVisualStyle(node, fallbackIndex) {
  const tags = node.properties?.osm_tags || {};
  const source = `${node.name || ""} ${tags.amenity || ""} ${tags.building || ""} ${tags.building_use || ""}`.toLowerCase();
  if (/图书馆|library/.test(source)) return { color: 0x466f9e, category: "图书馆" };
  if (/食堂|餐厅|餐饮|canteen|restaurant|cafe/.test(source)) return { color: 0xc58445, category: "餐饮" };
  if (/宿舍|公寓|dorm|residential/.test(source)) return { color: 0x8b6fa7, category: "住宿" };
  if (/体育|操场|运动|stadium|sports/.test(source)) return { color: 0x3f8b70, category: "体育" };
  if (/医院|health|clinic/.test(source)) return { color: 0xb85c68, category: "医疗" };
  if (/教学|教室|学院|实验|lecture|university|school/.test(source)) return { color: 0x4f7c68, category: "教学科研" };
  const palette = [0x71839a, 0x697a67, 0x8a7860, 0x587b80];
  return { color: palette[fallbackIndex % palette.length], category: "校园建筑" };
}

function buildingDisplayName(node) {
  const fallback = node.properties?.osm_tags?.name || node.properties?.osm_tags?.["name:zh"] || "";
  const name = String(node.name || fallback || "").trim();
  // OSM features without a human name are imported with technical IDs. They
  // are useful to the graph but make a visual map unreadable, so do not label
  // them as if they were building names.
  return /^(tsinghua|outdoor_area|main_building|poi_|building_)/i.test(name) ? "" : name;
}

function makeRealWorldBuildingMesh(node, idx, isLandmark = false) {
  const props = node.properties || {};
  const footprint = props.footprint;
  let heightM = 14.0;
  if (props.height_m) {
    heightM = Number(props.height_m);
  } else if (props.building_levels) {
    heightM = Number(props.building_levels) * 3.5;
  } else if (node.radius) {
    heightM = Math.min(Number(node.radius) * 0.8, 25);
  }

  const visualStyle = buildingVisualStyle(node, idx);
  const displayName = buildingDisplayName(node);
  let mesh;
  if (footprint && footprint.length >= 3) {
    try {
      const shape = new THREE.Shape();
      footprint.forEach((pt, i) => {
        const x = pt[0];
        const z = pt[2] !== undefined ? pt[2] : pt[1];
        if (i === 0) shape.moveTo(x, -z);
        else shape.lineTo(x, -z);
      });

      const extrudeSettings = {
        depth: heightM,
        bevelEnabled: true,
        bevelSegments: 1,
        steps: 1,
        bevelSize: 0.3,
        bevelThickness: 0.3
      };

      const geometry = new THREE.ExtrudeGeometry(shape, extrudeSettings);
      // ExtrudeGeometry grows along +Z.  With the footprint expressed as
      // (x, z), -PI/2 maps that depth onto +Y.  The previous +PI/2 rotation
      // put all real buildings below the ground plane, leaving an empty green
      // screen even though the local scene request contained building nodes.
      geometry.rotateX(-Math.PI / 2);

      const material = new THREE.MeshStandardMaterial({
        color: visualStyle.color,
        roughness: 0.6,
        metalness: 0.1
      });

      mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(0, -2.0, 0);
    } catch {
      mesh = null;
    }
  }

  if (!mesh) {
    const r = node.radius || 12;
    const geometry = new THREE.BoxGeometry(r * 1.6, heightM, r * 1.6);
    const material = new THREE.MeshStandardMaterial({
      color: visualStyle.color,
      roughness: 0.6,
      metalness: 0.1
    });
    mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(node.x || 0, heightM / 2 - 2.0, node.z || 0);
  }

  mesh.userData = { id: node.id, name: node.name, type: "building", category: visualStyle.category };

  const group = new THREE.Group();
  group.add(mesh);
  let labelSprite = null;

  if (displayName) {
    labelSprite = makeTextSprite(displayName, { fontsize: isLandmark ? 25 : 20, bg: isLandmark ? "rgba(10, 35, 25, 0.90)" : "rgba(10, 25, 20, 0.82)" });
    labelSprite.scale.set(isLandmark ? 58 : 44, isLandmark ? 29 : 22, 1);
    labelSprite.position.set(node.x || 0, heightM + 2.5, node.z || 0);
    labelSprite.visible = false;
    group.add(labelSprite);
  }

  group.userData = {
    isBuildingGroup: true,
    nodeId: node.id,
    name: displayName,
    isLandmark,
    labelSprite,
    posX: node.x || 0,
    posZ: node.z || 0
  };

  return group;
}

export function updateSceneLOD() {
  if (!camera || WorldStore.selectedWorldKey === "default" || WorldStore.scenePhase !== "ready") return;

  const isTopView = cameraMode === "top";
  const camDist = camera.position.y;

  if (camDist > 1400 || (isTopView && zoom >= 0.95)) {
    campusGroup.children.forEach(group => {
      if (group.userData?.labelSprite) {
        const isSelected = String(WorldStore.selected) === String(group.userData.nodeId);
        group.userData.labelSprite.visible = isSelected;
      }
    });
    return;
  }

  // Labels belong to the observed campus area, not the camera's physical
  // position (which is deliberately offset in front view).
  const focusX = (WorldStore.spatialScene?.bounds?.center_x || 0) + mapPanX;
  const focusZ = (WorldStore.spatialScene?.bounds?.center_z || 0) + mapPanZ;

  if (camDist > 550) {
    let activeCount = 0;
    campusGroup.children.forEach(group => {
      if (!group.userData?.isBuildingGroup) return;
      const labelSprite = group.userData.labelSprite;
      if (!labelSprite) return;

      const isSelected = String(WorldStore.selected) === String(group.userData.nodeId);
      const isLandmark = group.userData.isLandmark;

      if (isSelected || (isLandmark && activeCount < 16)) {
        labelSprite.visible = true;
        if (!isSelected) activeCount++;
      } else {
        labelSprite.visible = false;
      }
    });
    return;
  }

  const candidates = [];
  campusGroup.children.forEach(group => {
    if (!group.userData?.isBuildingGroup) return;
    const labelSprite = group.userData.labelSprite;
    if (!labelSprite) return;

    labelSprite.visible = false;
    const isSelected = String(WorldStore.selected) === String(group.userData.nodeId);
    if (isSelected) {
      labelSprite.visible = true;
      return;
    }

    const nodeX = group.userData.posX ?? 0;
    const nodeZ = group.userData.posZ ?? 0;
    const distToFocus = Math.hypot(nodeX - focusX, nodeZ - focusZ);

    candidates.push({
      group,
      dist: distToFocus,
      isLandmark: group.userData.isLandmark
    });
  });

  candidates.sort((a, b) => (a.dist - (a.isLandmark ? 150 : 0)) - (b.dist - (b.isLandmark ? 150 : 0)));

  let nearActiveCount = 0;
  for (const cand of candidates) {
    if (nearActiveCount < 24 && cand.dist < 650) {
      cand.group.userData.labelSprite.visible = true;
      nearActiveCount++;
    }
  }
}

export function disposeHierarchy(obj) {
  if (!obj) return;
  obj.traverse((child) => {
    if (child.geometry) {
      child.geometry.dispose();
    }
    if (child.material) {
      if (Array.isArray(child.material)) {
        child.material.forEach(m => {
          if (m.map) m.map.dispose();
          m.dispose();
        });
      } else {
        if (child.material.map) child.material.map.dispose();
        child.material.dispose();
      }
    }
  });
}

function clearGroupWithDisposal(group) {
  if (!group) return;
  while (group.children.length > 0) {
    const child = group.children[0];
    disposeHierarchy(child);
    group.remove(child);
  }
}

let lastBuildingWorldKey = null;
let lastBuildingNodeCount = 0;
let lastBuildingSceneVersion = null;
const agentMeshMap = new Map();

function estimatedBuildingHeight(node) {
  const props = node?.properties || {};
  if (Number(props.height_m) > 0) return Number(props.height_m);
  if (Number(props.building_levels) > 0) return Number(props.building_levels) * 3.5;
  return Math.min(Number(node?.radius || 14) * 0.8, 25);
}

function avatarFileFor(agent) {
  return avatarFiles[(Number(agent.id || 1) - 1 + avatarFiles.length) % avatarFiles.length];
}

function makeAgentPresence(agent) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const context = canvas.getContext("2d");
  const color = colors[Number(agent.id || 0) % colors.length];
  context.beginPath();
  context.arc(64, 64, 59, 0, Math.PI * 2);
  context.fillStyle = "#ffffff";
  context.fill();
  context.save();
  context.beginPath();
  context.arc(64, 64, 51, 0, Math.PI * 2);
  context.clip();
  context.fillStyle = color;
  context.fillRect(0, 0, 128, 128);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const image = new Image();
  image.onload = () => {
    context.clearRect(0, 0, 128, 128);
    context.save();
    context.beginPath();
    context.arc(64, 64, 51, 0, Math.PI * 2);
    context.clip();
    context.drawImage(image, 13, 13, 102, 102);
    context.restore();
    context.beginPath();
    context.arc(64, 64, 59, 0, Math.PI * 2);
    context.lineWidth = 8;
    context.strokeStyle = color;
    context.stroke();
    texture.needsUpdate = true;
  };
  image.src = `/avatars/${avatarFileFor(agent)}`;
  context.restore();
  context.beginPath();
  context.arc(64, 64, 59, 0, Math.PI * 2);
  context.lineWidth = 8;
  context.strokeStyle = color;
  context.stroke();
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false }));
  sprite.scale.set(18, 18, 1);
  sprite.renderOrder = 10;
  sprite.userData = { type: "agent", agent, presenceKind: "avatar" };
  return sprite;
}

function avatarClusterOffset(index, count) {
  if (count <= 1) return [0, 0];
  const ring = Math.floor(index / 6);
  const position = index % 6;
  const radius = 8 + ring * 6;
  const angle = (position / Math.min(count, 6)) * Math.PI * 2;
  return [Math.cos(angle) * radius, Math.sin(angle) * radius];
}

function updateSpatialAgentMeshes(agents, spatialScene) {
  const currentIds = new Set(agents.map(a => Number(a.id)));
  const nodesById = new Map((spatialScene?.nodes || []).map(node => [Number(node.id), node]));
  const clusters = new Map();
  agents.forEach(agent => {
    const state = WorldStore.spatialAgents.get(Number(agent.id));
    if (!state) return;
    const key = state.current_node_id != null ? `node:${state.current_node_id}` : `coord:${Math.round(Number(state.x) / 10)}:${Math.round(Number(state.z) / 10)}`;
    const items = clusters.get(key) || [];
    items.push(agent);
    clusters.set(key, items);
  });

  for (const [id, charGroup] of agentMeshMap.entries()) {
    if (!currentIds.has(id)) {
      disposeHierarchy(charGroup);
      worldAgentGroup.remove(charGroup);
      agentMeshMap.delete(id);
    }
  }

  agents.forEach((agent) => {
    const id = Number(agent.id);
    const spatialState = WorldStore.spatialAgents.get(id);
    const currentNode = spatialState ? nodesById.get(Number(spatialState.current_node_id)) : null;
    const desiredPresenceKind = "avatar";
    let posX = 0, posZ = 0;
    if (spatialState && spatialState.x !== undefined && spatialState.z !== undefined && (spatialState.x !== 0 || spatialState.z !== 0)) {
      posX = spatialState.x;
      posZ = spatialState.z;
    } else if (agent.location) {
      const matchingNode = (spatialScene?.nodes || []).find(n => n.name === agent.location || n.code === agent.location || (n.properties && n.properties.location === agent.location));
      if (matchingNode) {
        posX = matchingNode.x;
        posZ = matchingNode.z;
      } else if (spatialScene?.bounds) {
        posX = spatialScene.bounds.center_x;
        posZ = spatialScene.bounds.center_z;
      }
    }

    let charGroup = agentMeshMap.get(id);
    if (charGroup && charGroup.userData?.presenceKind !== desiredPresenceKind) {
      disposeHierarchy(charGroup);
      worldAgentGroup.remove(charGroup);
      agentMeshMap.delete(id);
      charGroup = null;
    }
    if (!charGroup) {
      charGroup = makeAgentPresence(agent);
      worldAgentGroup.add(charGroup);
      agentMeshMap.set(id, charGroup);
    }
    const clusterKey = spatialState?.current_node_id != null ? `node:${spatialState.current_node_id}` : `coord:${Math.round(Number(posX) / 10)}:${Math.round(Number(posZ) / 10)}`;
    const members = clusters.get(clusterKey) || [agent];
    const [offsetX, offsetZ] = avatarClusterOffset(members.findIndex(item => Number(item.id) === id), members.length);
    const markerHeight = currentNode?.node_type === "building" ? estimatedBuildingHeight(currentNode) + 8 : 7.5;
    charGroup.position.set(posX + offsetX, markerHeight, posZ + offsetZ);
    clickableObjects.push(charGroup);
  });
}

export function renderWorldScene(isInitialFit = false) {
  if (!scene || !campusGroup) return;
  clickableObjects = [];

  const spatialScene = WorldStore.spatialScene;
  const isRealWorldSelected = WorldStore.selectedWorldKey !== "default";
  const scenePhase = WorldStore.scenePhase || "ready";

  if (isRealWorldSelected && scenePhase === "loading") {
    if (grid) grid.visible = false;
    if (ground) ground.visible = false;
    // Keep a ready scene visible during a same-world background refresh, but
    // never show a previous world's geometry after an actual world switch.
    if (lastBuildingWorldKey !== null && lastBuildingWorldKey !== WorldStore.selectedWorldKey) {
      clearGroupWithDisposal(campusGroup);
      lastBuildingWorldKey = null;
      lastBuildingNodeCount = 0;
      lastBuildingSceneVersion = null;
    }
    if (campusGroup.children.length === 0) {
      const loadingSprite = makeTextSprite("正在加载清华真实图谱...", { size: 16, bg: "rgba(16,32,51,0.92)" });
      loadingSprite.position.set(mapPanX, 10, mapPanZ);
      campusGroup.add(loadingSprite);
    }
    return;
  }

  const isRealWorld = isRealWorldSelected && scenePhase === "ready" && spatialScene?.nodes?.length > 0;

  if (isRealWorld) {
    autoOrbit = false;
    turn = 0;
    if (root) root.rotation.y = 0;
    if (grid) grid.visible = false;
    if (ground) ground.visible = true;

    const buildingNodes = spatialScene.nodes.filter(n => n.node_type === "building" || (n.properties && n.properties.footprint));

    const box = new THREE.Box3();
    buildingNodes.forEach(node => {
      box.expandByPoint(new THREE.Vector3(node.x, 0, node.z));
    });

    let centerX = 0, centerZ = 0, spanX = 1000, spanZ = 1000;
    if (!box.isEmpty()) {
      const center = new THREE.Vector3();
      const size = new THREE.Vector3();
      box.getCenter(center);
      box.getSize(size);
      centerX = center.x;
      centerZ = center.z;
      spanX = size.x;
      spanZ = size.z;
    } else if (spatialScene.bounds) {
      centerX = spatialScene.bounds.center_x;
      centerZ = spatialScene.bounds.center_z;
      spanX = spatialScene.bounds.span_x;
      spanZ = spatialScene.bounds.span_z;
    }

    const maxSpan = Math.max(spanX, spanZ, 320);
    const currentVersion = WorldStore.sceneVersion || spatialScene.scene_version || spatialScene.version;
    const worldChanged = WorldStore.selectedWorldKey !== lastBuildingWorldKey;
    const needsRebuild = isInitialFit ||
      worldChanged ||
      currentVersion !== lastBuildingSceneVersion ||
      buildingNodes.length !== lastBuildingNodeCount ||
      campusGroup.children.length === 0;

    if (needsRebuild) {
      // The freshly requested tile is centred on the point the user panned
      // to. Preserve the physical camera position, then make that point the
      // new local origin so the next drag continues naturally.
      if (!isInitialFit && !worldChanged) {
        mapPanX = 0;
        mapPanZ = 0;
      }
      clearGroupWithDisposal(campusGroup);
      if (ground) {
        ground.geometry.dispose();
        ground.geometry = new THREE.PlaneGeometry(maxSpan * 1.6, maxSpan * 1.6);
        ground.position.set(centerX, -2.05, centerZ);
      }
      if (isInitialFit) {
        cameraMode = "front";
        mapPanX = 0;
        mapPanZ = 0;
        zoom = 1.0;
        cameraYaw = 0;
      }
      const landmarkList = Array.from(CORE_TSINGHUA_LANDMARKS);
      buildingNodes.forEach((node, idx) => {
        const isLandmark = landmarkList.includes(node.name);
        const bMesh = makeRealWorldBuildingMesh(node, idx, isLandmark);
        if (bMesh) {
          campusGroup.add(bMesh);
        }
      });
      lastBuildingWorldKey = WorldStore.selectedWorldKey;
      lastBuildingNodeCount = buildingNodes.length;
      lastBuildingSceneVersion = currentVersion;
    }

    campusGroup.children.forEach(bMesh => {
      if (bMesh.children && bMesh.children[0]) clickableObjects.push(bMesh.children[0]);
    });

    updateSpatialAgentMeshes(WorldStore.agents, spatialScene);

    // Camera updates are user-driven after the first fit. Calling this on
    // every event refresh was resetting manual pan/zoom and looked like a
    // scene flicker even when no building changed.
    if (needsRebuild && (isInitialFit || worldChanged)) {
      setObserverCamera(centerX, centerZ, maxSpan);
    }
  } else {
    if (grid) grid.visible = true;
    if (ground) {
      ground.visible = true;
      ground.geometry.dispose();
      ground.geometry = new THREE.PlaneGeometry(54, 42);
      ground.position.set(0, -2.05, 0);
    }

    const spaces = (WorldStore.world?.spaces || {}).spaces || defaultSpaces;
    spaces.forEach((space, index) => {
      const location = space.location || space.name;
      const [posx, posz] = spacePosition(location);
      const b = makeBuilding(space, index);
      b.group.position.set(posx, 0, posz);
      campusGroup.add(b.group);
      clickableObjects.push(b.body);
    });

    const locationCounts = {};
    WorldStore.agents.forEach((agent) => {
      const loc = agent.location || "校园";
      const [baseX, baseZ] = spacePosition(loc);
      const count = locationCounts[loc] || 0;
      locationCounts[loc] = count + 1;
      const offsetx = (count % 4) * 0.7 - 1.0;
      const offsetz = Math.floor(count / 4) * 0.7 + 1.2;

      const char = makeCharacter(agent);
      char.position.set(baseX + offsetx, 0.45, baseZ + offsetz);
      worldAgentGroup.add(char);
      char.children.forEach(child => clickableObjects.push(child));
    });
    setObserverCamera();
  }
}

export function updateCameraZoomValue() {
  if ($("cameraZoomValue")) $("cameraZoomValue").textContent = `${Math.round(100 / zoom)}%`;
}

export function setObserverCamera(centerX = null, centerZ = null, maxSpan = null) {
  if (!camera) return;
  const spatialScene = WorldStore.spatialScene;
  const isRealWorld = WorldStore.selectedWorldKey !== "default" && WorldStore.scenePhase === "ready" && spatialScene?.nodes?.length > 0;

  if (isRealWorld) {
    const cx = centerX ?? (spatialScene.bounds?.center_x || 0);
    const cz = centerZ ?? (spatialScene.bounds?.center_z || 0);
    const span = maxSpan ?? Math.max(spatialScene.bounds?.span_x || 1000, spatialScene.bounds?.span_z || 1000);

    const target = new THREE.Vector3(mapPanX + cx, -1, mapPanZ + cz);
    if (cameraMode === "top") {
      camera.position.set(mapPanX + cx, span * 0.75 * zoom, mapPanZ + cz + 0.15);
    } else {
      const distance = span * 0.68 * zoom;
      camera.position.set(
        mapPanX + cx + Math.sin(cameraYaw) * distance,
        span * 0.36 * zoom,
        mapPanZ + cz + Math.cos(cameraYaw) * distance,
      );
    }
    camera.lookAt(target);
  } else {
    const target = new THREE.Vector3(mapPanX, -1, mapPanZ);
    if (cameraMode === "top") camera.position.set(mapPanX, 18.5 * zoom, mapPanZ + 0.15);
    else camera.position.set(mapPanX, 7.15 * zoom, mapPanZ + 16.2 * zoom);
    camera.lookAt(target);
  }
  updateCameraZoomValue();
  updateSceneLOD();
}

export function getObserverZoom() {
  return zoom;
}

export function zoomIn() {
  setObserverZoom(zoom - 0.12);
}

export function zoomOut() {
  setObserverZoom(zoom + 0.12);
}

export function setObserverZoom(nextZoom) {
  zoom = Math.max(0.52, Math.min(2.25, nextZoom));
  setObserverCamera();
}

export function setCameraMode(mode) {
  cameraMode = mode === "top" ? "top" : "front";
  autoOrbit = false;
  setObserverCamera();
  return cameraMode;
}

export function toggleCameraMode() {
  return setCameraMode(cameraMode === "front" ? "top" : "front");
}

export function panObserver(dx, dy) {
  const isRealWorld = WorldStore.selectedWorldKey !== "default" && WorldStore.scenePhase === "ready";
  const scale = zoom * (isRealWorld ? 0.55 : 0.018);
  const extent = isRealWorld ? Math.max(WorldStore.spatialScene?.bounds?.span_x || 0, WorldStore.spatialScene?.bounds?.span_z || 0, 350) : 8;
  mapPanX = Math.max(-extent, Math.min(extent, mapPanX - dx * scale));
  mapPanZ = Math.max(-extent, Math.min(extent, mapPanZ - dy * scale));
  setObserverCamera();
  if (isRealWorld && typeof window.requestSpatialViewportAtMetric === "function") {
    const bounds = WorldStore.spatialScene?.bounds;
    if (bounds) window.requestSpatialViewportAtMetric(bounds.center_x + mapPanX, bounds.center_z + mapPanZ);
  }
}

export function rotateObserver(dx, dy = 0) {
  cameraYaw += dx * 0.008;
  // Keep the front-view horizon comfortable while allowing Shift-drag to
  // rotate around the focused local area.
  if (cameraMode === "front" && Math.abs(dy) > 0) {
    zoom = Math.max(0.52, Math.min(2.25, zoom + dy * 0.002));
  }
  autoOrbit = false;
  setObserverCamera();
}

export function resetObserverCamera() {
  zoom = 1;
  turn = -0.22;
  mapPanX = 0;
  mapPanZ = 0;
  cameraMode = "front";
  cameraYaw = 0;
  autoOrbit = false;
  setObserverCamera();
}

function bindScenePointerControls(canvas) {
  if (!canvas || canvas.dataset.spatialControlsBound) return;
  canvas.dataset.spatialControlsBound = "true";
  canvas.style.touchAction = "none";
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    dragging = true;
    pointerTracking = true;
    pointerDownX = pointerX = event.clientX;
    pointerDownY = pointerY = event.clientY;
    canvas.setPointerCapture?.(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!pointerTracking) return;
    const dx = event.clientX - pointerX;
    const dy = event.clientY - pointerY;
    pointerX = event.clientX;
    pointerY = event.clientY;
    if (event.shiftKey) rotateObserver(dx, dy);
    else panObserver(dx, dy);
  });
  const stopTracking = (event) => {
    dragging = false;
    pointerTracking = false;
    canvas.releasePointerCapture?.(event.pointerId);
  };
  canvas.addEventListener("pointerup", stopTracking);
  canvas.addEventListener("pointercancel", stopTracking);
  // Wheel input belongs to the document scroll container: the map is one
  // section of the page, and zoom remains an explicit + / − control.
}

function material(c) { return new THREE.MeshStandardMaterial({ color: c, roughness: 0.72 }); }
function part(group, geometry, c, x, y, z, scale) {
  const m = new THREE.Mesh(geometry, material(c));
  m.position.set(x, y, z);
  if (scale) m.scale.set(...scale);
  group.add(m);
  return m;
}

export function makeCharacter(agent) {
  const g = new THREE.Group();
  const n = Number(agent.id || WorldStore.selected + 1);
  const female = String(agent.gender || "").includes("女") || n % 2 === 0;
  const shirt = colors[n % colors.length];
  const skin = female ? 0xf1c5a4 : 0xd9a27e;
  const hair = female ? 0x4b2c24 : 0x30231e;

  part(g, new THREE.CylinderGeometry(0.22, 0.27, 0.7, 12), shirt, 0, 0.18, 0);
  part(g, new THREE.SphereGeometry(0.31, 16, 12), skin, 0, 0.82, 0);
  part(g, new THREE.SphereGeometry(0.325, 16, 12), hair, 0, 1.0, -0.02, [1, female ? 1.06 : 0.72, 1]);
  if (female) {
    part(g, new THREE.SphereGeometry(0.14, 12, 10), hair, -0.29, 0.72, 0.03);
    part(g, new THREE.SphereGeometry(0.14, 12, 10), hair, 0.29, 0.72, 0.03);
  }
  const armGeo = new THREE.CylinderGeometry(0.08, 0.09, 0.55, 10);
  const legGeo = new THREE.CylinderGeometry(0.105, 0.12, 0.72, 10);
  part(g, armGeo, skin, -0.34, 0.28, 0, [1, 1, 1]).rotation.z = -0.18;
  part(g, armGeo, skin, 0.34, 0.28, 0, [1, 1, 1]).rotation.z = 0.18;
  part(g, legGeo, 0x33465d, -0.14, -0.58, 0);
  part(g, legGeo, 0x33465d, 0.14, -0.58, 0);
  part(g, new THREE.BoxGeometry(0.23, 0.11, 0.39), 0x27333e, -0.14, -0.99, 0.08);
  part(g, new THREE.BoxGeometry(0.23, 0.11, 0.39), 0x27333e, 0.14, -0.99, 0.08);
  const eyeGeo = new THREE.SphereGeometry(0.025, 8, 6);
  part(g, eyeGeo, 0x172033, -0.1, 0.84, 0.29);
  part(g, eyeGeo, 0x172033, 0.1, 0.84, 0.29);
  g.userData = { agent };
  return g;
}

export function renderProfileCharacter(agent) {
  if (!profileRoot) return;
  while (profileRoot.children.length) {
    disposeHierarchy(profileRoot.children[0]);
    profileRoot.remove(profileRoot.children[0]);
  }
  const character = makeCharacter(agent);
  character.scale.setScalar(1.42);
  character.position.set(0, 0.12, 0);
  profileRoot.add(character);
  const nameplate = makeTextSprite(agent.name || "校园居民", {
    fontsize: 18,
    bg: "rgba(18, 49, 61, 0.84)",
    color: "#f4fffb"
  });
  nameplate.scale.set(3.1, 1.12, 1);
  nameplate.position.set(0, 2.55, 0);
  profileRoot.add(nameplate);
  profileRoot.rotation.set(0, -0.28, 0);
}

export function resize() {
  const canvas = $("scene");
  if (!canvas || !renderer) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

export function resizeProfile() {
  const profileCanvas = $("profileScene");
  if (!profileCanvas || !profileRenderer) return;
  const w = profileCanvas.clientWidth, h = profileCanvas.clientHeight;
  if (!w || !h) return;
  profileRenderer.setSize(w, h, false);
  profileCamera.aspect = w / h;
  profileCamera.updateProjectionMatrix();
}

function animate(t) {
  requestAnimationFrame(animate);
  if (renderer && scene && camera) {
    const isRealWorld = WorldStore.selectedWorldKey !== "default" && WorldStore.scenePhase === "ready";
    if (autoOrbit && !dragging && !isRealWorld) turn += 0.00035;
    if (root) root.rotation.y = isRealWorld ? 0 : turn;
    if (campusGroup && (!campusGroup.children || campusGroup.children.length === 0)) {
      renderWorldScene();
    }
    renderer.render(scene, camera);
  }
  if (profileRenderer && profileScene && profileCamera && $("profileOverlay")?.classList.contains("open")) {
    profileRoot.rotation.y = -t * 0.00028;
    profileRenderer.render(profileScene, profileCamera);
  }
}

export function renderThreeLoop() {
  requestAnimationFrame(animate);
}
