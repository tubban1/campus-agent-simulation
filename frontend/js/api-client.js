/**
 * API Client Module
 * Handles all HTTP communication with backend services.
 */
export const ApiClient = {
  async fetchState() {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error(`状态接口失败: ${res.status}`);
    return await res.json();
  },

  async fetchWorlds() {
    const res = await fetch("/api/spatial/worlds");
    if (!res.ok) throw new Error(`空间世界列表失败: ${res.status}`);
    return await res.json();
  },

  async fetchScene(worldKey) {
    const url = worldKey ? `/api/spatial/scene?world_key=${encodeURIComponent(worldKey)}` : "/api/spatial/scene";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`空间场景接口失败: ${res.status}`);
    return await res.json();
  },

  async fetchNewspaper() {
    const res = await fetch("/api/newspaper/agent-posts");
    if (!res.ok) return null;
    return await res.json();
  },

  async fetchExternalInformation() {
    const res = await fetch("/api/external-information");
    if (!res.ok) return [];
    const data = await res.json();
    return data.items || [];
  },

  async fetchLifeCourseWindow(residentId, fromDay, toDay) {
    const params = new URLSearchParams({ limit: "120" });
    if (Number.isFinite(Number(fromDay))) params.set("from_day", String(fromDay));
    if (Number.isFinite(Number(toDay))) params.set("to_day", String(toDay));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    let res;
    try {
      res = await fetch(`/api/agents/${encodeURIComponent(residentId)}/life-course/overview?${params}`, { signal: controller.signal });
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("读取超时，请稍后重试");
      throw error;
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) throw new Error(`生命历程接口失败: ${res.status}`);
    return await res.json();
  },

  async postSimulate(days = 1) {
    const adminToken = localStorage.getItem("ADMIN_TOKEN") || "";
    const headers = { "Content-Type": "application/json" };
    if (adminToken) headers["X-Admin-Token"] = adminToken;
    const res = await fetch(`/api/simulate?days=${days}`, { method: "POST", headers });
    if (!res.ok) throw new Error(`模拟推进失败: ${res.status}`);
    return await res.json();
  },

  async postTick(ticks = 1) {
    const adminToken = localStorage.getItem("ADMIN_TOKEN") || "";
    const headers = { "Content-Type": "application/json" };
    if (adminToken) headers["X-Admin-Token"] = adminToken;
    const res = await fetch(`/api/tick?ticks=${ticks}`, { method: "POST", headers });
    if (!res.ok) throw new Error(`tick 推进失败: ${res.status}`);
    return await res.json();
  }
};
