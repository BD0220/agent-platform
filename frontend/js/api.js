/* api.js — 后端 HTTP 封装 */
window.AB = window.AB || {};

AB.api = (() => {
  async function req(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(path, opt);
    let data = null;
    try { data = await r.json(); } catch (_) { /* 空响应 */ }
    if (!r.ok) {
      const detail = (data && data.detail) || r.statusText || ("HTTP " + r.status);
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = r.status;
      throw err;
    }
    return data;
  }

  return {
    get: (p) => req("GET", p),
    post: (p, b) => req("POST", p, b ?? {}),
    del: (p) => req("DELETE", p),

    // ── 会话 ──
    sessions: () => req("GET", "/sessions?limit=100"),
    sessionDetail: (id) => req("GET", `/sessions/${encodeURIComponent(id)}`),
    startSession: (message, workspaceId) =>
      req("POST", "/sessions/start", { message, workspace_id: workspaceId || "" }),
    sendMessage: (id, text) =>
      req("POST", `/sessions/${encodeURIComponent(id)}/message`, { message: text }),
    approval: (id, decision, task, approvalId) =>
      req("POST", `/sessions/${encodeURIComponent(id)}/approval`,
          { decision, task: task || "", approval_id: approvalId || "" }),
    stop: (id) => req("POST", `/sessions/${encodeURIComponent(id)}/stop`),
    deleteSession: (id) => req("DELETE", `/sessions/${encodeURIComponent(id)}`),

    // ── 工作空间 / 文件 ──
    workspaces: () => req("GET", "/workspaces"),
    roots: () => req("GET", "/workspaces/roots"),
    addWorkspace: (name, path) => req("POST", "/workspaces", { name, path }),
    removeWorkspace: (id) => req("DELETE", `/workspaces/${encodeURIComponent(id)}`),
    fileTree: (path) => req("GET", `/files/tree?path=${encodeURIComponent(path || "")}&depth=4`),
    fileContent: (path) => req("GET", `/files/content?path=${encodeURIComponent(path)}`),

    // ── 模型 ──
    models: () => req("GET", "/models"),
    switchModel: (modelId) => req("POST", "/model", { model_id: modelId }),
    setKey: (vendor, apiKey) => req("POST", "/models/key", { vendor, api_key: apiKey || "" }),
    setCustom: (baseUrl, model, apiKey) =>
      req("POST", "/models/custom", { base_url: baseUrl, model, api_key: apiKey || "" }),
    testModel: () => req("POST", "/models/test"),

    // ── 知识库 / 统计 ──
    kbList: () => req("GET", "/kb/collections"),
    kbUpload: (name, title, text) =>
      req("POST", `/kb/collections/${encodeURIComponent(name)}/upload`, { text, title: title || "untitled" }),
    kbSearch: (name, q, k) =>
      req("GET", `/kb/collections/${encodeURIComponent(name)}/search?q=${encodeURIComponent(q)}&top_k=${k || 5}`),
    kbDelete: (name) => req("DELETE", `/kb/collections/${encodeURIComponent(name)}`),
    stats: () => req("GET", "/stats"),
    metrics: () => req("GET", "/metrics"),
    tools: () => req("GET", "/tools"),
  };
})();
