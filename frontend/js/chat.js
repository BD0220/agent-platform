/* chat.js — 事件流 → 群聊 DOM（全量回放 / SSE 增量共用）
 *
 * 2026-10-19 输出可读性重做：
 *   1. Markdown 交给 AB.md（标题/列表/表格/代码块/复制按钮），不再用
 *      原来的 15 行正则版 —— 老版本把 `## 标题` 和 `- 列表` 原样吐出来，
 *      整篇代码被压成一坨 <br>，这是"输出很难受"的主因之一。
 *   2. 工具调用升级成一等公民：收到 kind=tool 画一张紧凑卡片
 *      （工具名 / 目标 / 状态 / 耗时 / 结果大小），点开看结果预览。
 *      老版本只在系统徽章里塞一句"🔧 调用了 xxx"，看不到任何结果。
 *   3. 超长消息默认折叠，长代码块自带"展开全部"；避免一屏塞进 3 万字符。
 *   4. 自动滚动只在用户本来就贴着底部时生效，不再抢滚动条。
 */
window.AB = window.AB || {};

AB.chat = (() => {
  const AGENT_STYLE = {
    "主Agent":  { icon: "⬢", color: "#7c3aed", bg: "#f5f3ff", role: "调度中心" },
    "产品经理": { icon: "📋", color: "#2563eb", bg: "#eff6ff", role: "需求分析" },
    "程序员":   { icon: "💻", color: "#0891b2", bg: "#ecfeff", role: "编码实现" },
    "测试员":   { icon: "🔍", color: "#059669", bg: "#ecfdf5", role: "质量验证" },
    "审查员":   { icon: "🛡️", color: "#b45309", bg: "#fffbeb", role: "独立验收" },
  };
  const AGENT_ORDER = ["主Agent", "产品经理", "程序员", "测试员", "审查员"];

  // 正在流式输出的气泡：agent 名 -> .bubble 元素（done 到达后原地替换成最终内容）
  const state = { working: new Set(), done: new Set(), resolved: new Map(), streams: {}, tools: new Map() };

  const COLLAPSE_CHARS = 1600;   // 超过这个长度的气泡默认折叠
  const COLLAPSE_PX = 460;       // 折叠后的可视高度上限

  const $ = (id) => document.getElementById(id);
  const scrollInner = () => $("chat-inner");

  const esc = (s) => AB.md.esc(s);

  function md(text) {
    return AB.md.render(AB.md.stripProtocol(text));
  }

  function el(tag, cls, html) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  function avatar(name) {
    const st = AGENT_STYLE[name];
    return `<div class="av" style="background:${st ? st.bg : "#fff"};color:${st ? st.color : "inherit"}">${st ? st.icon : "我"}</div>`;
  }

  function bubble(name, role, innerHtml) {
    const st = AGENT_STYLE[name] || {};
    const wrap = el("div", "msg");
    wrap.innerHTML = avatar(name) +
      `<div class="body"><div class="head"><span class="name" style="color:${st.color || "inherit"}">${esc(name)}</span>` +
      `${role ? `<span class="role">${esc(role)}</span>` : ""}</div>` +
      `<div class="bubble">${innerHtml}</div></div>`;
    return wrap;
  }

  /* 把文本画进气泡；过长则折叠 + "展开全文" */
  function paint(bubbleEl, text) {
    const clean = AB.md.stripProtocol(text);
    bubbleEl.innerHTML = `<div class="bubble-inner">${AB.md.render(clean)}</div>`;
    const inner = bubbleEl.querySelector(".bubble-inner");
    const tooLong = clean.length > COLLAPSE_CHARS ||
      (inner.scrollHeight > COLLAPSE_PX && clean.length > 400);
    if (!tooLong) return bubbleEl;
    inner.classList.add("is-collapsed");
    inner.style.maxHeight = COLLAPSE_PX + "px";
    const more = el("button", "msg-more", `展开全文（${clean.length} 字）`);
    more.addEventListener("click", () => {
      const collapsed = inner.classList.toggle("is-collapsed");
      inner.style.maxHeight = collapsed ? COLLAPSE_PX + "px" : "none";
      more.textContent = collapsed ? `展开全文（${clean.length} 字）` : "收起";
    });
    bubbleEl.appendChild(more);
    return bubbleEl;
  }

  function ts(t) {
    if (!t) return "";
    const d = new Date(t * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function headTime(ev) { return `<span class="role">${ts(ev.ts)}</span>`; }

  function fmtSize(n) {
    const v = Number(n) || 0;
    if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + " MB";
    if (v >= 1024) return (v / 1024).toFixed(1) + " KB";
    return v + " B";
  }

  function renderDeliverable(ev) {
    const dir = ev.delivery_dir || "";
    const files = ev.files || [];
    const wrap = el("div", "msg");
    wrap.innerHTML = avatar("主Agent") +
      `<div class="body" style="max-width:100%"><div class="head"><span class="name" style="color:${AGENT_STYLE["主Agent"].color}">主Agent</span><span class="role">交付物</span>${headTime(ev)}</div>` +
      `<div class="deliv"><div class="deliv-head"><span>本次交付</span>` +
      `<span class="deliv-dir" title="${esc(dir)}">${esc(dir.split("\\").pop() || dir)}</span></div>` +
      `<div class="deliv-files">${files.map((f) =>
        `<div class="deliv-file"><span>${esc(f.name)}</span>` +
        `<button class="open" data-path="${esc(dir ? dir + "\\" + f.name : "")}">查看</button></div>`).join("")}` +
      `</div></div></div>`;
    wrap.querySelectorAll(".open").forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = btn.dataset.path;
        if (p && AB.panels) AB.panels.showFileContent(p);
      });
    });
    return wrap;
  }

  function renderHandoff(ev) {
    const card = el("div");
    card.className = "msg handoff-msg";
    card.dataset.approvalId = ev.approval_id || "";
    // 两级闸门：计划确认（主Agent → 执行团队）要有别于普通交接请求，
    // 否则用户会把它当成日常交接顺手点掉（2026-10-18 实测反馈）
    const isPlan = (ev.to_agent || "") === "执行团队";
    const role = isPlan ? "执行计划确认" : "交接请求";
    const intro = isPlan
      ? `<b>这是开场的执行计划确认</b>：主Agent 打算按下面的计划开工。确认后开始执行；不满意可直接在意见框改计划再拒绝`
      : `等待你确认：<b>${esc(ev.from_agent || "主Agent")}</b> → <b>${esc(ev.to_agent || "")}</b>`;
    card.innerHTML = avatar("主Agent") +
      `<div class="body" style="max-width:100%"><div class="head"><span class="name" style="color:${AGENT_STYLE["主Agent"].color}">主Agent</span><span class="role">${role}</span>${headTime(ev)}</div>` +
      `<div class="bubble"><div class="ho-inline">` +
      `<div style="font-size:13px;margin-bottom:6px">${intro}</div>` +
      (ev.summary ? `<div class="muted" style="white-space:pre-wrap;max-height:120px;overflow:auto;margin-bottom:6px">${esc(ev.summary)}</div>` : "") +
      `<div class="ho-task">${md(ev.task || "")}</div>` +
      `</div></div></div>`;
    return card;
  }

  /* 工具调用卡片：一行一个工具，点开看结果预览 */
  function renderTool(ev) {
    const status = ev.status || "ok";
    const failed = status === "error";
    const dotCls = failed ? "failed" : (status === "start" ? "running" : "ok");
    const target = ev.target ? `<span class="tool-target">${esc(ev.target)}</span>` : "";
    const meta = [];
    if (ev.duration_ms) meta.push(`${(Number(ev.duration_ms) / 1000).toFixed(1)}s`);
    if (ev.chars) meta.push(fmtSize(ev.chars));
    const btn = el("button", "tool-card" + (failed ? " is-error" : ""));
    btn.innerHTML =
      `<span class="tool-dot ${dotCls}"></span>` +
      `<span class="tool-name">${esc(ev.tool || "tool")}</span>` +
      target +
      `<span class="tool-meta">${esc(meta.join(" · "))}</span>` +
      `<span class="tool-caret">›</span>`;
    const row = el("div", "tool-row");
    row.appendChild(btn);
    const preview = ev.preview || "";
    if (preview) {
      const box = el("div", "tool-preview hidden");
      box.innerHTML = `<pre>${esc(preview)}</pre>` +
        (ev.truncated ? `<div class="tool-preview-foot">结果已截断，完整内容见交付目录或日志</div>` : "");
      btn.addEventListener("click", () => {
        box.classList.toggle("hidden");
        btn.classList.toggle("open");
      });
      row.appendChild(box);
    }
    return row;
  }

  // tool 事件按 tool_id 原地更新（start → ok），时间线里不会一行变两行
  function upsertTool(ev) {
    const key = String(ev.tool_id || `${ev.agent || ""}:${ev.tool || ""}:${ev.seq || ""}`);
    const existing = state.tools.get(key);
    const row = renderTool(ev);
    if (existing && existing.isConnected) {
      existing.replaceWith(row);
    }
    state.tools.set(key, row);
    return existing && existing.isConnected ? null : row;
  }

  function nodeFor(ev) {
    const kind = ev.kind;
    if (kind === "user") {
      const wrap = el("div", "msg me");
      wrap.innerHTML = `<div class="av">我</div><div class="body">` +
        `<div class="head"><span class="name">${esc(ev.agent || "我")}</span>${headTime(ev)}</div>` +
        `<div class="bubble">${md(ev.content)}</div></div>`;
      return wrap;
    }
    if (kind === "tool") return upsertTool(ev);
    if (kind === "system") {
      return el("div", "sys", `<span class="chip">${esc(ev.content || "")}</span>`);
    }
    if (kind === "plan") {
      return bubble("主Agent", "执行计划", md(ev.content));
    }
    if (kind === "agent") {
      const name = ev.agent || "主Agent";
      const phase = ev.phase || "done";

      // 流式增量：往该 agent 的活跃气泡里追加文字（节点已挂载，返回 null）
      if (phase === "delta") {
        let target = state.streams[name];
        if (!target) {
          const b = bubble(name, "", "");
          state.streams[name] = b.querySelector(".bubble");
          b.dataset.streaming = "1";
          pendingNode = b;
          target = state.streams[name];
          target._raw = "";
        }
        target._raw = (target._raw || "") + (ev.content || "");
        target.innerHTML = `<div class="stream-text">${AB.md.render(AB.md.stripProtocol(target._raw))}</div>`;
        return null;
      }

      if (phase === "working") {
        const hasDone = state.done.has(name);
        const task = AB.md.stripProtocol(ev.content || "");
        if (hasDone) {
          return bubble(name, "任务简报", md(task.slice(0, 600) + (task.length > 600 ? "…" : "")));
        }
        // 活跃气泡：接到的任务只展示开头（全文已经在交接确认卡里），
        // 整段灌进来会把气泡顶成一堵墙
        const b = bubble(name, "", md((task || "正在处理").slice(0, 300)));
        state.streams[name] = b.querySelector(".bubble");
        return b;
      }

      if (phase === "note") {
        return el("div", "sys", `<span class="chip">${esc(name)}：${esc(ev.content || "")}</span>`);
      }

      // 最终产出：若该 agent 有流式气泡，原地替换，时间线上不留两条
      const live = state.streams[name];
      if (live) {
        delete state.streams[name];
        paint(live, ev.content || "");
        return null;
      }
      const b = bubble(name, ev.status || "", "");
      paint(b.querySelector(".bubble"), ev.content || "");
      return b;
    }
    if (kind === "handoff") return renderHandoff(ev);
    if (kind === "handoff_done") {
      if (ev.approval_id) {
        state.resolved.set(ev.approval_id, ev.decision || "");
        // 把时间线上对应的确认卡片标记为已处置，前端不再把它当"待确认"
        const card = scrollInner().querySelector(
          `.handoff-msg[data-approval-id="${ev.approval_id}"]`);
        if (card) card.dataset.resolved = ev.decision || "1";
      }
      if (ev.note) return el("div", "sys", `<span class="chip">${esc(ev.note)}</span>`);
      return null;
    }
    if (kind === "deliverable") return renderDeliverable(ev);
    if (kind === "done") {
      if (ev.stopped) return el("div", "sys", `<span class="chip">任务已终止</span>`);
      const r = ev.result || {};
      if (r["测试状态"] === "未通过") {
        return el("div", "sys",
          `<span class="chip" style="background:var(--warn-bg);color:var(--warn)">⚠ 任务结束：测试未通过（可继续提修改要求）</span>`);
      }
      const bits = [];
      if (r["测试状态"]) bits.push("测试：" + r["测试状态"]);
      if (r["主Agent调度轮数"]) bits.push("调度 " + r["主Agent调度轮数"] + " 轮");
      return el("div", "sys",
        `<span class="chip">✅ 全部完成${bits.length ? " · " + esc(bits.join(" · ")) : ""}</span>`);
    }
    if (kind === "error") {
      return el("div", "sys", `<span class="chip" style="background:var(--err-bg);color:var(--err)">❌ ${esc(ev.message || "")}</span>`);
    }
    return null;
  }

  function applySideEffects(ev) {
    const kind = ev.kind;
    if (kind === "agent") {
      const name = ev.agent || "主Agent";
      if (ev.phase === "working") state.working.add(name);
      if (ev.phase === "done") { state.working.delete(name); state.done.add(name); }
      if (ev.phase === "done") AB.app.setStatus("进行中", "run");
    }
    if (kind === "tool") {
      const name = ev.agent || "主Agent";
      if (ev.status === "start") state.working.add(name);
    }
    if (kind === "handoff") AB.app.setStatus("待你确认", "wait");
    if (kind === "done") {
      AB.app.setStatus(ev.stopped ? "已终止" : "已完成", ev.stopped ? "stop" : "ok");
      state.working.clear();
      AB.app.onSessionTerminal();
    }
    if (kind === "error") {
      AB.app.setStatus("失败", "err");
      state.working.clear();
      AB.app.onSessionTerminal();
    }
    if (kind === "system" && ev.stopping) AB.app.setStatus("停止中", "stop");
  }

  let pendingNode = null;

  // ── 长任务进度条 ───────────────────────────────────────
  // 痛点："他老是卡住不动" —— 模型一次性生成几千字时界面一粒字都不动，
  // 用户以为挂了。这里把后端的 progress 心跳渲染成一条常驻状态行，
  // 按 agent 原地更新（"产品经理 · 已 12s · 834 字"），任务结束自动消失。
  const progressState = new Map();

  function updateProgress(ev) {
    const name = ev.agent || "Agent";
    if (ev.done) progressState.delete(name);
    else progressState.set(name, ev.content || "");
    let strip = $("prog-strip");
    if (!progressState.size) {
      if (strip) strip.remove();
      return;
    }
    if (!strip) {
      strip = el("div", "prog-strip");
      strip.id = "prog-strip";
      scrollInner().appendChild(strip);
    }
    strip.innerHTML = [...progressState.entries()]
      .map(([n, t]) => `<span class="prog-item"><i class="prog-dot"></i>` +
        `<b>${esc(n)}</b><span>${esc(t)}</span></span>`)
      .join("");
  }

  // 只有用户本来就贴在底部时才自动滚动，否则往回翻历史会被不断拽回底部
  function nearBottom() {
    const sc = $("chat-scroll");
    return sc.scrollHeight - sc.scrollTop - sc.clientHeight < 120;
  }

  function append(ev) {
    // 进度心跳不产消息，只更新顶部那条状态行
    if (ev.kind === "progress") {
      updateProgress(ev);
      return;
    }
    const stick = nearBottom();
    pendingNode = null;
    const node = nodeFor(ev);
    if (node) scrollInner().appendChild(node);
    else if (pendingNode) scrollInner().appendChild(pendingNode);
    pendingNode = null;
    applySideEffects(ev);
    refreshMembers();
    AB.app.updateHandoffVisibility();
    if (stick) {
      const sc = $("chat-scroll");
      sc.scrollTop = sc.scrollHeight;
    }
  }

  function refreshMembers() {
    $("members").innerHTML = AGENT_ORDER.map((name) => {
      const st = AGENT_STYLE[name];
      const cls = state.working.has(name) ? "working" : (state.done.has(name) ? "done" : "");
      return `<span class="m ${cls}"><i>${st.icon}</i>${esc(name)}</span>`;
    }).join("");
  }

  function reset() {
    progressState.clear();
    const strip = $("prog-strip");
    if (strip) strip.remove();
    state.working.clear();
    state.done.clear();
    state.resolved.clear();
    state.streams = {};
    state.tools = new Map();
    scrollInner().innerHTML = "";
    scrollInner().appendChild(emptyState());
    AB.app.setStatus("就绪", "");
    refreshMembers();
  }

  function emptyState() {
    const box = el("div", "empty-state");
    box.innerHTML = `<div class="big">今天让他们做点什么？</div>` +
      `<div class="sub">一句话描述需求，五个角色会接力完成：产品经理出需求 → 程序员写代码 → 测试员验质量，` +
      `收尾前独立审查员再验收一遍；计划与每次交接前都会先问你。</div>` +
      `<div class="hints">` +
      `<span>写一个判断回文的 Python 函数并运行</span>` +
      `<span>实现一个 LRU 缓存并写测试</span>` +
      `<span>把这段逻辑重构成带类型注解的函数</span></div>`;
    box.querySelectorAll(".hints span").forEach((s) => {
      s.addEventListener("click", () => { $("msg-box").value = s.textContent; $("msg-box").focus(); });
    });
    return box;
  }

  return { append, reset, refreshMembers, AGENT_STYLE };
})();
