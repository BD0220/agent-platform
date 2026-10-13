/* chat.js — 事件流 → 群聊 DOM（全量回放 / SSE 增量共用） */
window.AB = window.AB || {};

AB.chat = (() => {
  const AGENT_STYLE = {
    "主Agent":  { icon: "⬢", color: "#7c3aed", bg: "#f5f3ff", role: "调度中心" },
    "产品经理": { icon: "📋", color: "#2563eb", bg: "#eff6ff", role: "需求分析" },
    "程序员":   { icon: "💻", color: "#0891b2", bg: "#ecfeff", role: "编码实现" },
    "测试员":   { icon: "🔍", color: "#059669", bg: "#ecfdf5", role: "质量验证" },
  };
  const AGENT_ORDER = ["主Agent", "产品经理", "程序员", "测试员"];
  // 正在流式输出的气泡：agent 名 -> .bubble 元素（done 到达后原地替换成最终内容）
  const state = { working: new Set(), done: new Set(), resolved: new Map(), streams: {} };

  const $ = (id) => document.getElementById(id);
  const scrollInner = () => $("chat-inner");

  function esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function md(text) {
    let t = esc(text ?? "");
    const blocks = [];
    t = t.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
      return `\x00${blocks.length - 1}\x00`;
    });
    t = t.replace(/`([^`\n]+)`/g, '<code class="inline">$1</code>');
    t = t.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    let html = t.split(/\n{2,}/).map((para) =>
      `<p>${para.replace(/\n/g, "<br>")}</p>`).join("");
    html = html.replace(/\x00(\d+)\x00/g, (_, i) => blocks[+i]);
    return html;
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

  function ts(t) {
    if (!t) return "";
    const d = new Date(t * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function headTime(ev) { return `<span class="role">${ts(ev.ts)}</span>`; }

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
    card.innerHTML = avatar("主Agent") +
      `<div class="body" style="max-width:100%"><div class="head"><span class="name" style="color:${AGENT_STYLE["主Agent"].color}">主Agent</span><span class="role">交接请求</span>${headTime(ev)}</div>` +
      `<div class="bubble"><div class="ho-inline">` +
      `<div style="font-size:13px;margin-bottom:6px">等待你确认：<b>${esc(ev.from_agent || "主Agent")}</b> → <b>${esc(ev.to_agent || "")}</b></div>` +
      (ev.summary ? `<div class="muted" style="white-space:pre-wrap;max-height:120px;overflow:auto;margin-bottom:6px">${esc(ev.summary)}</div>` : "") +
      `<div style="white-space:pre-wrap">${esc(ev.task || "")}</div>` +
      `</div></div></div>`;
    return card;
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
    if (kind === "system") {
      return el("div", "sys", `<span class="chip">${esc(ev.content || "")}</span>`);
    }
    if (kind === "plan") {
      return bubble("主Agent", "执行计划", `<div><b>执行计划</b><br>${md(ev.content)}</div>`);
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
          target._text = null;
        }
        if (!target._text) {
          target._text = document.createElement("div");
          target._text.className = "stream-text";
          target.appendChild(target._text);
        }
        target._text.textContent += ev.content || "";
        return null;
      }

      if (phase === "working") {
        const hasDone = state.done.has(name);
        if (hasDone) {
          return bubble(name, "",
            `<div class="take"><b>收到任务</b><br>${md((ev.content || "").slice(0, 400))}</div>`);
        }
        // 活跃气泡：先显示接到的任务，LLM 输出到达后逐字长在下面
        const b = bubble(name, "", md(ev.content || "正在处理"));
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
        live.innerHTML = md(ev.content);
        return null;
      }
      return bubble(name, ev.status || "", md(ev.content));
    }
    if (kind === "handoff") return renderHandoff(ev);
    if (kind === "handoff_done") {
      if (ev.approval_id) state.resolved.set(ev.approval_id, ev.decision || "");
      if (ev.note) return el("div", "sys", `<span class="chip">${esc(ev.note)}</span>`);
      return null;
    }
    if (kind === "deliverable") return renderDeliverable(ev);
    if (kind === "done") {
      if (ev.stopped) return el("div", "sys", `<span class="chip">任务已终止</span>`);
      const r = ev.result || {};
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

  function append(ev) {
    pendingNode = null;
    const node = nodeFor(ev);
    if (node) scrollInner().appendChild(node);
    else if (pendingNode) scrollInner().appendChild(pendingNode);
    pendingNode = null;
    applySideEffects(ev);
    refreshMembers();
    AB.app.updateHandoffVisibility();
    const sc = $("chat-scroll");
    sc.scrollTop = sc.scrollHeight;
  }

  function refreshMembers() {
    $("members").innerHTML = AGENT_ORDER.map((name) => {
      const st = AGENT_STYLE[name];
      const cls = state.working.has(name) ? "working" : (state.done.has(name) ? "done" : "");
      return `<span class="m ${cls}"><i>${st.icon}</i>${esc(name)}</span>`;
    }).join("");
  }

  function reset() {
    state.working.clear();
    state.done.clear();
    state.resolved.clear();
    state.streams = {};
    scrollInner().innerHTML = "";
    scrollInner().appendChild(emptyState());
    AB.app.setStatus("就绪", "");
    refreshMembers();
  }

  function emptyState() {
    const box = el("div", "empty-state");
    box.innerHTML = `<div class="big">今天让他们做点什么？</div>` +
      `<div class="sub">一句话描述需求，四个 Agent 会接力完成：产品经理出需求 → 程序员写代码 → 测试员验质量，` +
      `每一步交接前都会先问你。</div>` +
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
