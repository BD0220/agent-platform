/* md.js — 轻量 Markdown 渲染器（零依赖，离线可用）
 *
 * 为什么自己写：项目没有打包步骤，且不能引外部 CDN。
 * 设计约束：
 *   1. 先转义、再套用 Markdown 规则 —— 所有用户/模型文本都不可信，
 *      拼出来的标签只能来自本文件，杜绝 XSS；
 *   2. 支持"未闭合代码块" —— 流式输出时 ``` 还没闭合也要先渲染成代码块，
 *      否则用户看到的是满屏裸反引号（老版本 `md()` 的实际表现）；
 *   3. 覆盖模型实际会用的语法：标题/列表(可嵌套)/表格/引用/分隔线/
 *      围栏代码/inline code/加粗/斜体/删除线/链接。
 */
window.AB = window.AB || {};

AB.md = (() => {
  const FENCE_LONG_LINES = 18;   // 超过这个行数的代码块默认折叠

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── 行内元素（输入必须是已转义文本）──
  function inline(text) {
    let t = text;
    // inline code 先抽出来，避免其中被当成粗体/链接
    const codes = [];
    t = t.replace(/`([^`\n]+)`/g, (_, code) => {
      codes.push(`<code class="inline">${code}</code>`);
      return `\u0001${codes.length - 1}\u0001`;
    });
    t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    t = t.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    t = t.replace(/__([^_\n]+)__/g, "<b>$1</b>");
    t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>");
    t = t.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    t = t.replace(/\u0001(\d+)\u0001/g, (_, i) => codes[+i]);
    return t;
  }

  function codeBlock(code, lang, open) {
    const lines = code.split("\n");
    const long = lines.length > FENCE_LONG_LINES;
    const cls = "code-block" + (long ? " is-long" : "") + (open ? " is-streaming" : "");
    const label = lang ? esc(lang) : "text";
    const head = `<div class="code-head"><span class="code-lang">${label}</span>` +
      (long ? `<span class="code-hint">${lines.length} 行</span>` : "") +
      `<button type="button" class="code-copy" data-copy="1">复制</button></div>`;
    // code 在进入 render 时已经整体转义过，这里不能再 esc 一次（会双重转义）
    const body = `<pre class="code-body"><code>${code.replace(/\n$/, "")}</code></pre>`;
    return `<div class="${cls}">${head}${body}` +
      (long ? `<button type="button" class="code-more" data-more="1">展开全部 ${lines.length} 行</button>` : "") +
      `</div>`;
  }

  const RX = {
    fence: /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/,
    fenceEnd: (ch) => new RegExp("^\\s*" + ch + "{3,}\\s*$"),
    heading: /^\s*(#{1,6})\s+(.*?)\s*#*\s*$/,
    hr: /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/,
    // 整篇文本在 render 入口已转义，所以引用符号此时是 &gt;
    quote: /^\s*(?:&gt;|>)\s?/,
    ul: /^(\s*)([-*+])\s+(.*)$/,
    ol: /^(\s*)(\d{1,9})[.)]\s+(.*)$/,
    tableSep: /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/,
  };

  function render(src) {
    // 先整体转义再解析：模型输出和用户输入都不可信，
    // 拼进 HTML 的标签只能由本文件产生（这条规则不能有例外）。
    const text = esc(String(src ?? "")).replace(/\r\n?/g, "\n");
    const lines = text.split("\n");
    const out = [];
    let i = 0;
    let para = [];

    const flush = () => {
      if (!para.length) return;
      const joined = para.join("\n");
      out.push(`<p>${inline(joined).replace(/\n/g, "<br>")}</p>`);
      para = [];
    };

    while (i < lines.length) {
      const line = lines[i];

      // 围栏代码
      const f = line.match(RX.fence);
      if (f) {
        flush();
        const tick = f[1][0];
        const lang = f[2] || "";
        const endRx = RX.fenceEnd(tick);
        const buf = [];
        i += 1;
        let closed = false;
        while (i < lines.length) {
          if (endRx.test(lines[i])) { closed = true; i += 1; break; }
          buf.push(lines[i]);
          i += 1;
        }
        out.push(codeBlock(buf.join("\n"), lang, !closed));
        continue;
      }

      // 空行
      if (!line.trim()) { flush(); i += 1; continue; }

      // 标题
      const h = line.match(RX.heading);
      if (h) {
        flush();
        const lvl = Math.min(h[1].length, 4);
        out.push(`<h${lvl + 2} class="md-h">${inline(h[2])}</h${lvl + 2}>`);
        i += 1;
        continue;
      }

      // 分隔线
      if (RX.hr.test(line)) { flush(); out.push('<hr class="md-hr">'); i += 1; continue; }

      // 引用（收集连续行，内部递归渲染）
      if (RX.quote.test(line)) {
        flush();
        const buf = [];
        while (i < lines.length && RX.quote.test(lines[i])) {
          buf.push(lines[i].replace(RX.quote, ""));
          i += 1;
        }
        out.push(`<blockquote class="md-quote">${render(buf.join("\n"))}</blockquote>`);
        continue;
      }

      // 表格：当前行有 | 且下一行是分隔行
      if (line.includes("|") && i + 1 < lines.length && RX.tableSep.test(lines[i + 1])) {
        flush();
        const cells = (row) => row.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
        const head = cells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          rows.push(cells(lines[i]));
          i += 1;
        }
        out.push('<div class="md-table-wrap"><table class="md-table"><thead><tr>' +
          head.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
          rows.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
          "</tbody></table></div>");
        continue;
      }

      // 列表（支持缩进嵌套）
      const ul = line.match(RX.ul);
      const ol = line.match(RX.ol);
      if (ul || ol) {
        flush();
        const ordered = Boolean(ol);
        const baseIndent = (ul || ol)[1].length;
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(ordered ? RX.ol : RX.ul);
          if (!m) {
            // 列表项的续行（缩进更深且非空）
            if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
              items[items.length - 1].text += "\n" + lines[i].trim();
              i += 1;
              continue;
            }
            break;
          }
          const indent = m[1].length;
          if (indent > baseIndent) {
            // 收集更深缩进的整段，递归渲染成嵌套列表
            const nested = [];
            while (i < lines.length) {
              const mm = lines[i].match(ordered ? RX.ol : RX.ul);
              if (!mm || mm[1].length <= baseIndent) break;
              nested.push(lines[i]);
              i += 1;
            }
            if (items.length) items[items.length - 1].children = nested.join("\n");
            continue;
          }
          items.push({ text: m[3], children: "" });
          i += 1;
        }
        const tag = ordered ? "ol" : "ul";
        out.push(`<${tag} class="md-list">` + items.map((it) => {
          const body = inline(it.text).replace(/\n/g, "<br>");
          return `<li>${body}${it.children ? render(it.children) : ""}</li>`;
        }).join("") + `</${tag}>`);
        continue;
      }

      para.push(line);
      i += 1;
    }
    flush();
    return out.join("");
  }

  /* 剥掉不该让人看见的协议痕迹：
   *   [TOOL_CALL: x: y] ... [/TOOL_CALL]   —— 平台指令 + 工具参数正文，不是给人读的
   *   [RESULT]{...}[/RESULT]              —— 子 Agent 给平台的信封，属元数据
   * 注意要连块内正文一起删：create_file 的参数就是整篇文件内容，
   * 老版本只删了标记行，于是 29KB 代码照样糊在聊天区。
   */
  function stripProtocol(text) {
    let t = String(text ?? "");
    t = t.replace(/\[TOOL_CALL:[\s\S]*?\[\/TOOL_CALL\]/g, "");
    t = t.replace(/\[TOOL_CALL:[\s\S]*$/g, "");
    t = t.replace(/\[\/TOOL_CALL\]/g, "");
    t = t.replace(/\[RESULT\][\s\S]*$/g, "");
    return t.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  // 代码块复制 / 展开：用事件委托，动态插入的节点也能用
  document.addEventListener("click", (e) => {
    const copyBtn = e.target.closest && e.target.closest(".code-copy");
    if (copyBtn) {
      const block = copyBtn.closest(".code-block");
      const code = block && block.querySelector(".code-body code");
      const text = code ? code.textContent : "";
      const done = () => { copyBtn.textContent = "已复制"; setTimeout(() => { copyBtn.textContent = "复制"; }, 1200); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
      } else {
        fallbackCopy(text, done);
      }
      return;
    }
    const moreBtn = e.target.closest && e.target.closest(".code-more");
    if (moreBtn) {
      const block = moreBtn.closest(".code-block");
      if (block) {
        const long = block.classList.toggle("is-long");
        moreBtn.textContent = long ? "收起" : "展开全部";
      }
    }
  });

  function fallbackCopy(text, done) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (_) { /* 忽略：复制失败不该打断阅读 */ }
    document.body.removeChild(ta);
  }

  return { render, stripProtocol, esc };
})();
