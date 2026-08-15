/**
 * World Store Module
 * Manages global spatial, agent, and simulation state.
 */

export const $ = id => document.getElementById(id);

export const escapeHtml = str => String(str ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#039;");

export const colors = [
  "#d76b6b", "#5b89b4", "#d29a48", "#5e9f8c", "#8c6ab3",
  "#4c88a8", "#b7687b", "#7a9b4c", "#bd7850", "#5f78a6"
];

export const avatarFiles = [
  "01_lin_xiaoxia.png", "02_chen_yuhang.png", "03_zhao_yiming.png", "04_su_qing.png", "05_zhou_boss.png",
  "06_li_jie.png", "07_wang_teacher.png", "08_he_admin.png", "09_zhang_chen.png", "10_logistics.png",
  "11_gu_nanxing.png", "12_xu_jiayan.png", "13_meng_yutong.png", "14_shen_yizhou.png", "15_tang_xiaotang.png",
  "16_lu_ziang.png", "17_qiao_anran.png", "18_han_mo.png", "19_bai_lu.png", "20_qin_yue.png",
  "21_confucius.svg", "22_socrates.svg", "23_buddha.svg", "24_da_vinci.svg", "25_shakespeare.svg",
  "26_newton.svg", "27_cixi.svg", "28_einstein.svg", "29_hepburn.svg", "30_steve_jobs.svg"
];

export const defaultSpaces = [
  "校务处", "教学楼", "商业街", "图书馆", "宿舍区", "食堂", "操场"
].map((location, index) => ({
  code: ["admin", "teaching", "business", "library", "dorm", "canteen", "playground"][index],
  name: location,
  location,
  status: "开放",
  effective_status: "开放",
  crowd_percent: 0,
  actual_agents: 0
}));

export const WorldStore = {
  world: {
    environment: { weather: "维度天气", temperature: "--", time_slot: "实时" },
    spaces: { spaces: defaultSpaces },
    events: []
  },
  worldRuntime: null,
  worldEvents: [],
  observerSessionId: null,
  lastWorldEventId: 0,
  observerStateLoaded: false,
  agents: [],
  agentOrderKeys: new Map(),
  spatialScene: { nodes: [], edges: [] },
  spatialAgents: new Map(),
  spatialQueue: new Map(),
  bodyStates: new Map(),
  newsPosts: [],
  newspaperDay: null,
  newspaperArchive: { available_days: [] },
  newspaperEdition: {},
  newspaperView: "edition",
  newspaperRequestId: 0,
  externalInformation: [],
  selectedWorldKey: "default",
  scenePhase: "idle",
  sceneVersion: 1,
  sceneRequestToken: 0,
  selected: 0,
  observedFocus: "World2 全局",
  relationshipMetric: "trust",

  setWorldState(nextWorld) {
    this.world = nextWorld;
    this.agents = nextWorld.agents || nextWorld.residents || [];
  },

  selectWorld(worldKey) {
    const nextKey = worldKey || "default";
    const isSameWorldReady = (nextKey === this.selectedWorldKey) &&
      this.scenePhase === "ready" &&
      (this.spatialScene?.nodes?.length || 0) > 0;

    this.selectedWorldKey = nextKey;
    if (!isSameWorldReady) {
      this.spatialScene = { nodes: [], edges: [] };
      this.scenePhase = (nextKey === "default") ? "ready" : "loading";
    }
  },

  setScenePhase(phase) {
    this.scenePhase = phase;
  },

  setSpatialScene(scene) {
    this.spatialScene = scene || { nodes: [], edges: [] };
    if (scene && scene.world_key) {
      this.selectedWorldKey = scene.world_key;
      // Keep the client-side revision when the API has no explicit scene
      // version.  Using Date.now() here made every polling response look
      // like a structural world change and forced a complete rebuild.
      const serverVersion = scene.scene_version ?? scene.version;
      if (serverVersion !== undefined && serverVersion !== null) {
        this.sceneVersion = serverVersion;
      }
      this.scenePhase = "ready";
    }
  }
};
