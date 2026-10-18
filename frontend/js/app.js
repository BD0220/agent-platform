/* app.js — 全局状态：会话切换、SSE 事件流、发送/审批/终止 */
(() => {
  const $ = (id) => document.getElementById(id);

  const state = {
    sessionId: "",
    cursor: 0,
    es: null,           // EventSource
    terminal: false,    // 本轮执行已收尾
    pendingApprovalId: "",
    resolving: false,   // 审批请求在途，防重复点击
    workspaces: [],
  };

  // ── 状态胶囊 ──
  function setStatus(text, cls) {
    const pill = $("status-pill");
    pill.textContent = text;
    pill.className = "pill " + (cls || "");
  }

  // ── 会话列表 ──
  async function refreshSessions() {
    try {
      const data = await AB.api.sessions();
      const items = data.sessions || [];
      const box = $("session-list");
      if (!items.length) {
        box.innerHTML = `<div class="list-hint">还没有会话，点上面「新建会话」开始。</div>`;
        return;
      }
      box.innerHTML = "";
      items.forEach((s) => {
        const row = document.createElement("div");
        row.className = "sess-item" + (s.id === state.sessionId ? " active" : "");
        row.innerHTML = `<div class="sess-main">` +
          `<div class="sess-title"></div>` +
          `<div class="sess-meta">${s.message_count || 0} 条 · ${s.status_label || ""}${s.running ? " · 运行中" : ""}</div></div>` +
          `<button class="sess-del" title="删除">✕</button>`;
        row.querySelector(".sess-title").textContent = s.title || s.id;
        row.addEventListener("click", () => openSession(s.id));
        row.querySelector(".sess-del").addEventListener("click", async (e) => {
          e.stopPropagation();
          if (!confirm(`删除会话「${s.title || s.id}」？`)) return;
          await AB.api.deleteSession(s.id);
          if (s.id === state.sessionId) newSession();
          refreshSessions();
        });
        box.appendChild(row);
      });
    } catch (e) {
      $("session-list").innerHTML = `<div class="list-hint">加载失败：${e.message}</div>`;
    }
  }

  // ── 会话打开 / SSE ──
  function stopStream() {
    if (state.es) { state.es.close(); state.es = null; }
  }

  function startStream() {
    if (!state.sessionId) return;
    stopStream();
    const sid = state.sessionId;
    const base = state.cursor;
    const es = new EventSource(`/sessions/${encodeURIComponent(sid)}/events?cursor=${base}`);
    state.es = es;

    es.onmessage = (e) => {
      if (state.sessionId !== sid) return;      // 已切走，丢弃
      let ev;
      try { ev = JSON.parse(e.data); } catch (_) { return; }
      if (typeof ev.seq === "number") state.cursor = ev.seq + 1;
      AB.chat.append(ev);
      if (ev.kind === "handoff" && ev.approval_id) state.pendingApprovalId = ev.approval_id;
      if (ev.kind === "handoff_done" && ev.approval_id === state.pendingApprovalId) {
        state.pendingApprovalId = "";
        updateHandoffVisibility();
      }
    };

    es.addEventListener("end", () => {
      state.terminal = true;
      stopStream();
      updateStopVisibility();
      refreshSessions();
    });

    es.onerror = () => {
      // 网络抖动：EventSource 自带重连会用旧 cursor 造成重复，这里手动管理
      es.close();
      if (state.es === es) state.es = null;
      if (state.sessionId === sid && !state.terminal) {
        setTimeout(() => { if (state.sessionId === sid && !state.terminal) startStream(); }, 2500);
      }
    };
  }

  async function openSession(id) {
    const detail = await AB.api.sessionDetail(id);
    const meta = detail.data || {};
    stopStream();
    state.sessionId = id;
    state.cursor = meta.cursor || 0;
    state.terminal = !(meta.running || meta.pending);
    state.pendingApprovalId = (meta.pending && meta.pending.approval_id) || "";

    AB.chat.reset();
    $("ch-title").textContent = meta.title || id;
    $("ch-sub").textContent = `工作空间 · ${meta.workspace_name || "默认"}`;
    (meta.events || []).forEach((ev) => AB.chat.append(ev));
    if (state.terminal) {
      const labelMap = { done: "已完成", error: "失败", stopped: "已终止" };
      setStatus(labelMap[meta.status] || meta.status_label || "已完成",
        meta.status === "error" ? "err" : (meta.status === "stopped" ? "stop" : "ok"));
    } else {
      setStatus("进行中", "run");
    }
    updateHandoffVisibility();
    updateStopVisibility();
    startStream();
    refreshSessions();
  }

  function newSession() {
    stopStream();
    state.sessionId = "";
    state.cursor = 0;
    state.terminal = true;
    state.pendingApprovalId = "";
    AB.chat.reset();
    $("ch-title").textContent = "abundance";
    $("ch-sub").textContent = "新会话 · 每一步交接前都会先问你";
    updateHandoffVisibility();
    updateStopVisibility();
    refreshSessions();
  }

  // ── 发送 / 终止 ──
  async function send() {
    const box = $("msg-box");
    const text = box.value.trim();
    if (!text) return;
    if (state.sessionId && !state.terminal) {
      alert("该会话正在执行中，等它完成（或点 ■ 终止）再发下一条。");
      return;
    }
    box.value = "";
    try {
      if (!state.sessionId) {
        const r = await AB.api.startSession(text, $("ws-select").value || "");
        state.sessionId = r.session_id;
        state.cursor = 0;
        state.terminal = false;
        AB.chat.reset();
        $("ch-title").textContent = text.slice(0, 40);
        $("ch-sub").textContent = `工作空间 · ${r.workspace_name || "默认"}`;
        setStatus("进行中", "run");
        startStream();
        updateStopVisibility();
        refreshSessions();
      } else {
        await AB.api.sendMessage(state.sessionId, text);
        state.terminal = false;
        setStatus("进行中", "run");
        startStream();
        updateStopVisibility();
      }
    } catch (e) {
      box.value = text;   // 失败还回去，别把用户输入吞了
      alert("发送失败：" + e.message);
    }
  }

  async function stop() {
    if (!state.sessionId) return;
    try { await AB.api.stop(state.sessionId); } catch (e) { alert(e.message); }
  }

  // ── 交接审批 ──
  function updateHandoffVisibility() {
    // 从时间线里找最近一条"还没被处置"的 handoff 事件
    const bar = $("handoff");
    if (state.terminal) { bar.classList.add("hidden"); return; }
    let pending = null;
    document.querySelectorAll(".handoff-msg").forEach((n) => {
      if (n.dataset.approvalId && !n.dataset.resolved) pending = n;
    });
    if (pending) {
      const changed = state.pendingApprovalId !== pending.dataset.approvalId;
      state.pendingApprovalId = pending.dataset.approvalId;
      bar.classList.remove("hidden");
      const agents = pending.querySelectorAll(".ho-inline b");
      $("ho-flow").textContent = agents.length >= 2
        ? `${agents[0].textContent} → ${agents[1].textContent}` : "";
      const pre = pending.querySelector(".muted");
      $("ho-summary").textContent = pre ? pre.textContent : "";
      $("ho-summary").style.display = $("ho-summary").textContent ? "block" : "none";
      // 只在"换成另一项待确认"时清空意见框：同一条确认反复刷新时
      // 不能把用户正在写的打回意见抹掉
      if (changed) $("ho-task").value = "";
    } else {
      state.pendingApprovalId = "";
      bar.classList.add("hidden");
    }
  }

  async function resolve(decision) {
    if (!state.sessionId) return;
    if (state.resolving) return;          // 防重复点击：点了就立刻锁住
    state.resolving = true;
    const taskText = decision === "reject" ? ($("ho-task").value.trim() || "") : $("ho-task").value.trim();
    const approvalId = state.pendingApprovalId;
    try {
      await AB.api.approval(state.sessionId, decision, taskText, approvalId);
      markResolved(approvalId, decision);
      $("handoff").classList.add("hidden");
      if (decision !== "stop") setStatus("进行中", "run");
    } catch (e) {
      // 404 = 这项其实已经处理完（多标签页/重复点击），把陈旧确认条清掉即可
      markResolved(approvalId, decision);
      $("handoff").classList.add("hidden");
      state.pendingApprovalId = "";
      if (e.status !== 404) alert(e.message);
    } finally {
      state.resolving = false;
    }
  }

  /* 本地先标记已处置：不依赖服务端回调到达时机。
     否则在 handoff_done 到达之前，任何一条新事件都会让确认条又弹回来。 */
  function markResolved(approvalId, decision) {
    if (!approvalId) return;
    const card = document.querySelector(`.handoff-msg[data-approval-id="${approvalId}"]`);
    if (card) card.dataset.resolved = decision || "1";
    state.pendingApprovalId = "";
    updateHandoffVisibility();
  }

  function updateStopVisibility() {
    $("btn-stop").classList.toggle("hidden", !(state.sessionId && !state.terminal));
  }

  function onSessionTerminal() {
    state.terminal = true;
    updateHandoffVisibility();
    updateStopVisibility();
    refreshSessions();
  }

  // ── 工作空间下拉 ──
  async function refreshWorkspaces() {
    const data = await AB.api.workspaces();
    state.workspaces = data.workspaces || [];
    $("ws-select").innerHTML = state.workspaces.map((w) =>
      `<option value="${w.id}">${w.name}${w.exists ? "" : "（目录不存在）"}</option>`).join("");
  }

  // ── 初始化 ──
  function init() {
    $("btn-new").addEventListener("click", newSession);
    $("btn-refresh-sessions").addEventListener("click", refreshSessions);
    $("btn-send").addEventListener("click", send);
    $("btn-stop").addEventListener("click", stop);
    $("ho-ok").addEventListener("click", () => resolve("approve"));
    $("ho-back").addEventListener("click", () => resolve("reject"));
    $("ho-kill").addEventListener("click", () => resolve("stop"));
    $("msg-box").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    // 输入框自适应高度
    $("msg-box").addEventListener("input", (e) => {
      e.target.style.height = "auto";
      e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px";
    });
    AB.panels.init();
    refreshWorkspaces();
    refreshSessions();
  }

  document.addEventListener("DOMContentLoaded", init);

  // 对外暴露给 chat.js / panels.js 的钩子
  window.AB.app = {
    setStatus,
    onSessionTerminal,
    updateHandoffVisibility,
    refreshWorkspaces,
  };
})();
