/* panels.js — 弹窗：知识库/统计、工作空间、交付物查看 */
window.AB = window.AB || {};

AB.panels = (() => {
  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── 弹窗骨架 ──
  function openModal(title, bodyHtml) {
    $("modal").innerHTML =
      `<div class="modal-head"><span class="modal-title">${esc(title)}</span>` +
      `<button class="modal-close">✕</button></div><div class="modal-body">${bodyHtml}</div>`;
    $("modal-mask").classList.remove("hidden");
    $("modal").querySelector(".modal-close").addEventListener("click", closeModal);
  }
  function closeModal() { $("modal-mask").classList.add("hidden"); }

  function line(text, cls) {
    const box = $("modal").querySelector(".modal-result");
    if (!box) return;
    box.innerHTML = `<div class="msg-line ${cls || ""}">${esc(text)}</div>`;
  }

  function fileName(p) { return p.split("\\").pop().split("/").pop(); }

  // ── 交付物查看（弹窗读文件内容）──
  async function showFileContent(path) {
    openModal(fileName(path), `<pre id="file-pre" class="muted" style="white-space:pre-wrap;max-height:65vh;overflow:auto">加载中…</pre>`);
    try {
      const data = await AB.api.fileContent(path);
      const pre = $("file-pre");
      pre.textContent = data.binary
        ? "（二进制文件，不支持预览）"
        : (data.text || "（空文件）") + (data.truncated ? "\n\n…（内容过长已截断）" : "");
      pre.classList.remove("muted");
    } catch (e) {
      $("file-pre").textContent = "读取失败：" + e.message;
    }
  }

  // ── 知识库 / 统计 ──
  async function openMore() {
    openModal("知识库 / 运行统计", `
      <div class="tabs"><button class="tab active" data-tab="kb">知识库</button>
        <button class="tab" data-tab="stats">运行统计</button></div>
      <div id="tab-kb"></div><div id="tab-stats" class="hidden"></div>
      <div class="modal-result"></div>`);
    $("modal").querySelectorAll(".tab").forEach((t) => {
      t.addEventListener("click", () => {
        $("modal").querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
        t.classList.add("active");
        $("tab-kb").classList.toggle("hidden", t.dataset.tab !== "kb");
        $("tab-stats").classList.toggle("hidden", t.dataset.tab !== "stats");
        if (t.dataset.tab === "stats") renderStats();
      });
    });
    await renderKb();
  }

  async function renderKb() {
    const box = $("tab-kb");
    let cols = [];
    try { cols = (await AB.api.kbList()).collections || []; } catch (_) {}
    box.innerHTML = `
      <div class="muted" style="margin-bottom:8px">Agent 可用 search_knowledge 工具检索这里的内容；任务经验由系统自动沉淀，不在这里。</div>
      <div class="kv-grid" style="margin-bottom:12px">${cols.length ? cols.map((c) =>
        `<div class="kv"><div class="k">${esc(c.name)}</div><div class="v">${c.count}</div>` +
        `<div class="k">文本块 <button class="kb-del" data-name="${esc(c.name)}" style="border:none;background:none;color:var(--err);font-size:11px">删除</button></div></div>`).join("")
        : `<div class="muted">暂无知识库</div>`}</div>
      <div class="side-title"><span>上传文本</span></div>
      <div class="form-row"><input id="kb-name" placeholder="知识库名称，如 my-knowledge"><input id="kb-title" placeholder="文档标题（可选）"></div>
      <div class="form-row"><textarea id="kb-text" rows="4" placeholder="粘贴文本内容…"></textarea></div>
      <div class="form-row"><button class="btn-primary" id="kb-up">上传</button></div>
      <div class="side-title"><span>搜索</span></div>
      <div class="form-row"><input id="kb-q-name" placeholder="知识库名称"><input id="kb-q" placeholder="搜索关键词">
        <button class="btn-ghost" id="kb-go">搜索</button></div>
      <pre id="kb-out" class="muted" style="white-space:pre-wrap;max-height:180px;overflow:auto"></pre>`;
    box.querySelectorAll(".kb-del").forEach((b) => {
      b.addEventListener("click", async () => {
        if (!confirm(`删除知识库「${b.dataset.name}」？`)) return;
        await AB.api.kbDelete(b.dataset.name);
        renderKb();
      });
    });
    box.querySelector("#kb-up").addEventListener("click", async () => {
      try {
        const r = await AB.api.kbUpload($("kb-name").value.trim(), $("kb-title").value.trim(), $("kb-text").value);
        line(r.message || "已上传", "ok"); renderKb();
      } catch (e) { line(e.message, "err"); }
    });
    box.querySelector("#kb-go").addEventListener("click", async () => {
      try {
        const r = await AB.api.kbSearch($("kb-q-name").value.trim(), $("kb-q").value.trim(), 5);
        $("kb-out").textContent = String(r.result || "").slice(0, 4000);
      } catch (e) { $("kb-out").textContent = e.message; }
    });
  }

  async function renderStats() {
    const box = $("tab-stats");
    try {
      const s = (await AB.api.stats()).data || {};
      const u = (await AB.api.metrics()).data || {};
      const tools = (await AB.api.tools()).tools || [];
      box.innerHTML = `
        <div class="kv-grid">
          <div class="kv"><div class="k">总任务</div><div class="v">${s.total_tasks ?? "-"}</div></div>
          <div class="kv"><div class="k">已完成</div><div class="v">${s.completed_tasks ?? "-"}</div></div>
          <div class="kv"><div class="k">失败</div><div class="v">${s.failed_tasks ?? "-"}</div></div>
          <div class="kv"><div class="k">进行中</div><div class="v">${s.active_tasks ?? "-"}</div></div>
          <div class="kv"><div class="k">经验沉淀</div><div class="v">${s.total_memories ?? "-"}</div></div>
          <div class="kv"><div class="k">LLM 调用</div><div class="v">${u.total_calls ?? "-"}</div></div>
          <div class="kv"><div class="k">输入 tokens</div><div class="v">${u.total_prompt_tokens ?? "-"}</div></div>
          <div class="kv"><div class="k">输出 tokens</div><div class="v">${u.total_completion_tokens ?? "-"}</div></div>
        </div>
        <div class="side-title" style="margin-top:14px"><span>已注册工具（${tools.length}）</span></div>
        ${tools.map((t) => `<div class="tool-item"><b>${esc(t.name)}</b><div class="t-desc">${esc(t.description)}</div></div>`).join("")}`;
    } catch (e) {
      box.innerHTML = `<div class="muted">加载失败：${esc(e.message)}</div>`;
    }
  }

  // ── 工作空间 ──
  function openWorkspaceAdd() {
    openModal("添加工作空间", `
      <div class="muted" style="margin-bottom:10px">填一个真实存在的本地文件夹绝对路径，任务的产出（需求分析 / 代码 / 测试报告）会写到它下面的任务子目录里。</div>
      <div class="form-row"><input id="ws-name" placeholder="名称（留空用文件夹名）"></div>
      <div class="form-row"><input id="ws-path" placeholder="绝对路径，如 D:\\projects\\demo"></div>
      <div class="form-row"><button class="btn-primary" id="ws-ok">添加</button></div>
      <div class="modal-result"></div>`);
    $("ws-ok").addEventListener("click", async () => {
      try {
        const r = await AB.api.addWorkspace($("ws-name").value.trim(), $("ws-path").value.trim());
        line(r.message || "已添加", "ok");
        await AB.app.refreshWorkspaces();
      } catch (e) { line(e.message, "err"); }
    });
  }

  function init() {
    $("btn-open-more").addEventListener("click", () => openMore().catch((e) => alert(e.message)));
    $("btn-ws-add").addEventListener("click", openWorkspaceAdd);
    $("modal-mask").addEventListener("click", (e) => {
      if (e.target === $("modal-mask")) closeModal();
    });
  }

  return { init, showFileContent };
})();
