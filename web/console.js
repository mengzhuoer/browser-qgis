/* ==========================================================================
 * Python 控制台（网页端）
 *
 * 后端 /api/console/* 已经在 PyQGIS 内核里跑 Python 了，这里只管一件事：
 * 给它一个像 QGIS 桌面版那样贴在底部的抽屉面板 —— 收起时是一条窄把手条，
 * 展开时从底部往上盖住地图区域。
 *
 * 覆盖而不是重排：用 position:fixed 浮在页面上，**不动**其它元素的高度，
 * 所以地图、侧栏、状态栏的布局一点不变。
 *
 * 自包含：样式、DOM、逻辑都在这个文件里，挂在 index.html 末尾一行
 * <script src="/static/console.js"></script>，跟 panels.js / files.js 一个路子。
 *
 * 权限：控制台等于在服务器上执行任意代码，后端默认只给管理员开。页面加载时
 * 先问一次 GET /api/console/state，allowed 为 false 就**整个不渲染**（连把手
 * 条都不出现），也不发任何后续请求 —— 安静地当作没有这个功能，不弹窗不报错。
 * ========================================================================== */
(function () {
  "use strict";

  const H_KEY = "gisds.console.h";  // 抽屉高度记在 localStorage 里
  const MIN_H = 160;                // 最小高度
  const DEF_H = 320;                // 默认高度
  const MAX_HIST = 200;             // 历史命令最多留多少条

  // ---------------------------------------------------------------- 状态
  let allowed = false;   // 当前用户能不能用控制台
  let mounted = false;   // DOM 建好了没有
  let opened = false;    // 抽屉是不是展开着
  let height = DEF_H;    // 抽屉高度（px）
  let varCount = 0;      // 命名空间里的变量数
  let seq = 0;           // 执行序号（输出里的 In [n]）
  let busy = false;      // 有没有正在执行
  const hist = [];       // 命令历史（只在内存里，刷新就清空）
  let hIdx = -1;         // 历史游标（-1 = 正在写新命令）
  let hDraft = "";       // 翻历史之前暂存当前输入
  let perm = null;       // 权限检查的 promise（只查一次）

  // ---------------------------------------------------------------- 工具
  const byId = (id) => document.getElementById(id);

  // 后端返回的都是纯文本，插进 DOM 前一律转义
  // （实体表放在最前面，改起来只有一个地方）
  const ENT = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
  };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ENT[c]);
  }

  // ---------------------------------------------------------------- 样式
  const CSS = `
#gisdsConsole{
  --pc-handle-h:22px;
  --pc-bottom:0px;
  --pc-bar-h:26px;
  position:fixed;left:0;right:0;bottom:var(--pc-bottom);z-index:8000;pointer-events:none;
  font-family:var(--ui);
}
/* 把手条是**底部读数条左端的一个签**，不是浮在地图上的一条横杠。
   原来它是通栏 22px，压在地图最下面一横 —— 地图自己的
   「还没有图层，点左侧 ＋ 添加图层」提示正好在那一横，于是被盖掉一半；
   状态栏的坐标 / 比例尺读数也被压在下面看不见。
   现在它跟状态栏同高、同排，读数条给它让出左边一段，
   地图底部和状态栏都干干净净。 */
#gisdsConsole .pc-handle{
  pointer-events:auto;position:fixed;left:0;bottom:var(--pc-bottom);
  display:flex;align-items:center;gap:6px;
  height:var(--pc-bar-h);padding:0 12px;
  background:var(--panel);border-right:1px solid var(--rule);
  color:var(--ink-2);font-size:11.5px;cursor:pointer;user-select:none;
}
#gisdsConsole .pc-handle:hover{background:var(--panel-3);color:var(--ink)}
#gisdsConsole .pc-handle .pc-caret{color:var(--ink-3);font-size:9px;line-height:1}
#gisdsConsole .pc-drawer{
  pointer-events:auto;position:fixed;left:0;right:0;
  bottom:calc(var(--pc-bottom) + var(--pc-bar-h));
  height:320px;max-height:calc(100vh - 60px);display:none;
  flex-direction:column;background:var(--panel);
  border:1px solid var(--rule-2);border-bottom:0;border-radius:3px 3px 0 0;
}
#gisdsConsole.open .pc-drawer{display:flex}
#gisdsConsole .pc-grip{
  height:4px;flex:0 0 auto;cursor:row-resize;
  background:var(--panel-3);border-bottom:1px solid var(--rule);
}
#gisdsConsole .pc-grip:hover,#gisdsConsole .pc-grip.dragging{background:var(--moss-soft)}
#gisdsConsole .pc-head{
  display:flex;align-items:center;gap:8px;height:28px;flex:0 0 auto;padding:0 9px;
  background:var(--panel-2);border-bottom:1px solid var(--rule);
}
#gisdsConsole .pc-title{font-size:11.5px;font-weight:600;letter-spacing:.4px;color:var(--ink-2)}
#gisdsConsole .pc-vars{
  font-size:10.5px;color:var(--ink-3);background:var(--panel);
  border:1px solid var(--rule);border-radius:8px;padding:0 6px;
}
#gisdsConsole .pc-spacer{flex:1}
#gisdsConsole .pc-btn{
  font:inherit;font-size:11px;line-height:1.5;padding:2px 8px;
  background:var(--panel);border:1px solid var(--rule-2);border-radius:3px;
  color:var(--ink-2);cursor:pointer;
}
#gisdsConsole .pc-btn:hover{background:var(--panel-3);color:var(--ink)}
#gisdsConsole .pc-btn:disabled{opacity:.42;cursor:default}
#gisdsConsole .pc-btn:disabled:hover{background:var(--panel);color:var(--ink-2)}
#gisdsConsole .pc-btn.pc-run{background:var(--moss);border-color:var(--moss);color:#fff;font-weight:600}
#gisdsConsole .pc-btn.pc-run:hover{background:var(--moss-2);border-color:var(--moss-2)}
#gisdsConsole .pc-out{
  flex:1 1 auto;min-height:0;overflow:auto;padding:8px 11px;
  background:var(--panel);color:var(--ink-2);
  font-family:var(--mono);font-size:11.5px;line-height:1.62;
  white-space:pre-wrap;word-break:break-word;
}
#gisdsConsole .pc-out .pc-tag{color:var(--ink-3);margin-right:7px}
#gisdsConsole .pc-out .pc-echo{color:var(--ink)}
#gisdsConsole .pc-out .pc-res{color:var(--moss-2)}
#gisdsConsole .pc-out .pc-stderr{color:var(--amber)}
#gisdsConsole .pc-out .pc-err{color:var(--rust)}
#gisdsConsole .pc-out .pc-trace{color:var(--rust);opacity:.76}
#gisdsConsole .pc-out .pc-meta{color:var(--ink-3)}
#gisdsConsole .pc-out .pc-cost{margin-left:9px;color:var(--ink-3)}
#gisdsConsole .pc-input{
  display:flex;align-items:flex-start;gap:7px;padding:6px 9px;flex:0 0 auto;
  background:var(--panel-2);border-top:1px solid var(--rule);
}
#gisdsConsole .pc-prompt{
  font-family:var(--mono);font-size:12px;color:var(--moss);line-height:1.7;flex:0 0 auto;
}
#gisdsConsole .pc-code{
  flex:1 1 auto;min-width:0;resize:none;overflow:auto;
  background:var(--panel);border:1px solid var(--rule-2);border-radius:2px;
  padding:4px 7px;font-family:var(--mono);font-size:12px;line-height:1.55;
  color:var(--ink);outline:none;
}
#gisdsConsole .pc-code:focus{border-color:var(--moss)}
`;

  // ---------------------------------------------------------------- 骨架
  const HTML = `
<div id="gisdsConsole">
  <div class="pc-drawer" id="pcDrawer">
    <div class="pc-grip" id="pcGrip" title="拖动调整高度"></div>
    <div class="pc-head">
      <span class="pc-title">Python 控制台</span>
      <span class="pc-vars" id="pcVars" title="当前命名空间里的变量数">变量 0</span>
      <span class="pc-spacer"></span>
      <button class="pc-btn" id="pcClear" type="button">清空输出</button>
      <button class="pc-btn" id="pcReset" type="button">重置命名空间</button>
      <button class="pc-btn" id="pcClose" type="button">关闭</button>
    </div>
    <div class="pc-out" id="pcOut"></div>
    <div class="pc-input">
      <span class="pc-prompt">>>></span>
      <textarea class="pc-code" id="pcCode" rows="2" spellcheck="false"
                placeholder="写 Python 代码，Enter 执行、Shift+Enter 换行"></textarea>
      <button class="pc-btn pc-run" id="pcRun" type="button">执行</button>
    </div>
  </div>
  <div class="pc-handle" id="pcHandle" role="button" tabindex="0"
       title="展开或收起 Python 控制台（Ctrl+\`）">
    <span class="pc-caret" id="pcCaret">▲</span>
    <span>Python 控制台</span>
  </div>
</div>`;

  // ---------------------------------------------------------------- 挂载
  function inject() {
    if (mounted) return;
    const style = document.createElement("style");
    style.id = "console-style";
    style.textContent = CSS;
    document.head.appendChild(style);

    const holder = document.createElement("div");
    holder.innerHTML = HTML;
    document.body.appendChild(holder.firstElementChild);
    mounted = true;

    applyHeight();
    syncBottom();
    // 状态栏的高度是布局算出来的，等一帧再让一次位更稳
    setTimeout(syncBottom, 80);
    bind();
    showHint();
  }

  function showHint() {
    const out = byId("pcOut");
    if (!out) return;
    out.innerHTML = '<div class="pc-meta">控制台已就绪。'
      + "在下面写 Python，Enter 执行、Shift+Enter 换行；Ctrl+` 开合这个面板。</div>";
  }

  function bind() {
    byId("pcHandle").onclick = toggle;
    byId("pcHandle").onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggle();
      }
    };
    byId("pcClose").onclick = close;
    byId("pcClear").onclick = clearOut;
    byId("pcReset").onclick = resetNs;
    byId("pcRun").onclick = () => run();
    byId("pcCode").addEventListener("keydown", onCodeKey);
    byId("pcCode").addEventListener("input", autoGrow);
    bindGrip();
    document.addEventListener("keydown", onGlobalKey);
    window.addEventListener("resize", applyHeight);
    window.addEventListener("resize", syncBottom);
  }

  // ---------------------------------------------------------------- 开合
  function open() {
    if (!allowed) return;
    inject();
    const root = byId("gisdsConsole");
    if (!root) return;
    root.classList.add("open");
    opened = true;
    const caret = byId("pcCaret");
    if (caret) caret.textContent = "▼";
    const ta = byId("pcCode");
    if (ta) {
      ta.focus();
      autoGrow();
    }
    scrollOut();
  }

  function close() {
    const root = byId("gisdsConsole");
    if (!root) return;
    root.classList.remove("open");
    opened = false;
    const caret = byId("pcCaret");
    if (caret) caret.textContent = "▲";
  }

  function toggle() {
    if (opened) close();
    else open();
  }

  // ---------------------------------------------------------------- 高度
  function clampH(h) {
    const max = Math.max(MIN_H, window.innerHeight - 90);
    return Math.min(Math.max(h, MIN_H), max);
  }

  function applyHeight() {
    const d = byId("pcDrawer");
    if (!d) return;
    height = clampH(height);
    d.style.height = Math.round(height) + "px";
  }

  /**
   * 把手条别压在状态栏上。
   *
   * 状态栏（坐标 / 比例尺 / CRS）就在页面最底下 25px，控制台是
   * position:fixed 浮上来的 —— 不让位的话把手条会盖住它，看不到坐标。
   * 这里按状态栏**实际**高度让位（量出来，不写死），状态栏没了就贴底。
   */
  function syncBottom() {
    const root = byId("gisdsConsole");
    if (!root) return;
    let offset = 0;
    let barH = 26;
    const bar = document.querySelector(".statusbar");
    if (bar) {
      const r = bar.getBoundingClientRect();
      if (r && r.height > 0) {
        // 把手条要**待在状态栏旁边**（同一排、同样高），不能压在状态栏上，
        // 也不能压在地图最下面那一横上 —— 那里是地图自己的「加图层」提示。
        //
        // 这里原来算的是「状态栏下边沿离视口底还有多远」—— 可状态栏本来就是
        // body 里最后一个元素，那个距离恒等于 0，于是把手条正好盖在它身上。
        // 该问的是「状态栏有多高」。
        offset = Math.round(r.height);
        barH = offset;
      }
    }
    root.style.setProperty("--pc-bottom", offset + "px");
    root.style.setProperty("--pc-bar-h", barH + "px");
    // 读数条给把手条让出左边一段（把手条的宽度是内容决定的，量一下）
    const handle = byId("pcHandle");
    if (handle) {
      const w = Math.round(handle.getBoundingClientRect().width);
      if (w > 0) {
        document.body.style.setProperty("--pc-left-pad", w + "px");
      }
    }
  }

  function loadHeight() {
    try {
      const v = parseInt(localStorage.getItem(H_KEY) || "", 10);
      if (isFinite(v) && v > 0) height = clampH(v);
    } catch (e) {
      /* 隐私模式下 localStorage 可能不让用，忽略 */
    }
  }

  function saveHeight() {
    try {
      localStorage.setItem(H_KEY, String(Math.round(height)));
    } catch (e) {
      /* 同上 */
    }
  }

  function bindGrip() {
    const grip = byId("pcGrip");
    if (!grip) return;
    let dragging = false;

    const start = (e) => {
      dragging = true;
      grip.classList.add("dragging");
      // 借用页面现成的「正在改大小」样式：拖的时候别选中文本
      document.body.classList.add("resizing", "row");
      if (e && e.preventDefault) e.preventDefault();
    };
    const move = (e) => {
      if (!dragging) return;
      const y = e.touches && e.touches.length ? e.touches[0].clientY : e.clientY;
      height = clampH(window.innerHeight - y);
      applyHeight();
      if (e.cancelable) e.preventDefault();
    };
    const end = () => {
      if (!dragging) return;
      dragging = false;
      grip.classList.remove("dragging");
      document.body.classList.remove("resizing", "row");
      saveHeight();
    };

    grip.addEventListener("mousedown", start);
    grip.addEventListener("touchstart", start, { passive: false });
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    window.addEventListener("touchmove", move, { passive: false });
    window.addEventListener("touchend", end);
  }

  // ---------------------------------------------------------------- 输出
  function push(html) {
    const out = byId("pcOut");
    if (!out) return;
    out.insertAdjacentHTML("beforeend", html);
    scrollOut();
  }

  function scrollOut() {
    const out = byId("pcOut");
    if (out) out.scrollTop = out.scrollHeight;
  }

  // 一段输出。tag 是来源标记（In [n]: / Out [n]:），text 是纯文本。
  function line(cls, n, text, tagWord) {
    if (text == null) return;
    const s = String(text);
    if (!s) return;
    const tag = (tagWord || "In") + " [" + n + "]:";
    push('<div class="' + cls + '"><span class="pc-tag">' + esc(tag) + "</span>" + esc(s) + "</div>");
  }

  function clearOut() {
    const out = byId("pcOut");
    if (!out) return;
    out.innerHTML = "";
    showHint();
  }

  // ---------------------------------------------------------------- 变量
  function setVars() {
    const el = byId("pcVars");
    if (el) el.textContent = "变量 " + (Number(varCount) || 0);
  }

  async function refreshVars() {
    if (!allowed) return;
    try {
      const st = await api("/api/console/state");
      varCount = st && Array.isArray(st.variables) ? st.variables.length : 0;
    } catch (e) {
      /* 拿不到就保持原样，不打扰用户 */
    }
    setVars();
  }

  async function resetNs() {
    const btn = byId("pcReset");
    if (btn) btn.disabled = true;
    try {
      const r = await api("/api/console/reset", { method: "POST" });
      varCount = Number(r && r.variables) || 0;
      setVars();
      push('<div class="pc-meta">命名空间已重置，变量已清空。</div>');
    } catch (e) {
      push('<div class="pc-err">重置命名空间失败：'
        + esc(e && e.message ? e.message : String(e)) + "</div>");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- 执行
  async function run(code) {
    if (busy) return;
    const ta = byId("pcCode");
    const src = String(code == null ? (ta ? ta.value : "") : code);
    if (!src.trim()) return;

    busy = true;
    const btn = byId("pcRun");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "执行中…";
    }
    if (ta) {
      ta.value = "";
      autoGrow();
    }

    seq += 1;
    const n = seq;
    push('<div class="pc-echo"><span class="pc-tag">In [' + n + "]:</span>" + esc(src)
      + '<span class="pc-cost" data-cost="' + n + '"></span></div>');
    pushHist(src);

    try {
      const r = await api("/api/console/exec", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: src }),
      });
      showResult(n, r);
      setCost(n, r && r.duration_ms);
    } catch (e) {
      // api() 失败会抛 Error，message 是服务端的中文提示 —— 原样显示，不弹窗
      push('<div class="pc-err"><span class="pc-tag">In [' + n + "]:</span>"
        + esc(e && e.message ? e.message : String(e)) + "</div>");
    } finally {
      busy = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = "执行";
      }
      refreshVars();
    }
  }

  function showResult(n, r) {
    const d = r || {};
    line("pc-stdout", n, d.stdout);       // 标准输出：普通墨色
    line("pc-stderr", n, d.stderr);       // 标准错误/日志：琥珀色
    line("pc-res", n, d.result, "Out");   // 最后一个表达式的 repr
    line("pc-err", n, d.error);           // 错误摘要：锈红
    line("pc-trace", n, d.traceback);     // 完整回溯：浅一点的锈红
  }

  function setCost(n, ms) {
    const el = document.querySelector(
      '#gisdsConsole .pc-cost[data-cost="' + n + '"]'
    );
    if (el) el.textContent = "耗时 " + (Number(ms) || 0) + " 毫秒";
  }

  // ---------------------------------------------------------------- 输入
  function autoGrow() {
    const ta = byId("pcCode");
    if (!ta) return;
    ta.style.height = "auto";
    const max = Math.round(window.innerHeight * 0.3);
    const h = Math.max(Math.min(ta.scrollHeight, max), 34);
    ta.style.height = h + "px";
  }

  function onCodeKey(e) {
    // Enter 执行、Shift+Enter 换行（多行代码靠 Shift+Enter 敲出来）
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      run();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      walk(-1);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      walk(1);
    }
  }

  // ↑ 往更老的历史走，↓ 往更新的走；走到头恢复原来写的那行
  function walk(dir) {
    if (!hist.length) return;
    const ta = byId("pcCode");
    if (!ta) return;
    if (hIdx === -1) {
      if (dir > 0) return;
      hDraft = ta.value;
      hIdx = hist.length - 1;
    } else {
      const next = hIdx + dir;
      if (next < 0) return;
      if (next >= hist.length) {
        hIdx = -1;
        ta.value = hDraft;
        autoGrow();
        return;
      }
      hIdx = next;
    }
    ta.value = hist[hIdx];
    autoGrow();
    ta.selectionStart = ta.selectionEnd = ta.value.length;
  }

  function pushHist(code) {
    if (hist[hist.length - 1] !== code) hist.push(code);
    if (hist.length > MAX_HIST) hist.shift();
    hIdx = -1;
    hDraft = "";
  }

  // Ctrl + ` 开合。反引号在别的键盘上键名可能不一样，所以两种都认。
  function onGlobalKey(e) {
    if (!allowed || !mounted) return;
    if (!e.ctrlKey || e.altKey || e.metaKey) return;
    const isTick = e.key === "`"
      || ((e.key === "Dead" || e.key === "Unidentified") && e.code === "Backquote");
    if (!isTick) return;

    // 正在输入框里打字就别抢这个键 —— 用户要的是打出反引号本身
    const t = e.target;
    const tag = t && t.tagName ? t.tagName.toUpperCase() : "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (t && t.isContentEditable) return;

    e.preventDefault();
    toggle();
  }

  // ---------------------------------------------------------------- 权限
  async function checkAccess() {
    let st = null;
    try {
      st = await api("/api/console/state");
    } catch (e) {
      st = null;
    }
    allowed = !!(st && st.allowed);
    if (!allowed) {
      // 没权限：连把手条都不渲染，也不再发任何请求，安静地当作没这功能
      return false;
    }
    varCount = st && Array.isArray(st.variables) ? st.variables.length : 0;
    inject();
    setVars();
    return true;
  }

  function ensureAccess() {
    if (!perm) perm = checkAccess();
    return perm;
  }

  function start() {
    if (typeof api === "function") {
      ensureAccess();
      return;
    }
    // index.html 的内联脚本还没跑完，等页面加载完再问
    window.addEventListener("load", () => { ensureAccess(); }, { once: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  // ---------------------------------------------------------------- 启动
  loadHeight();

  // 菜单项调它：打开控制台。没权限就什么都不做
  window.openConsole = function () {
    ensureAccess().then((ok) => {
      if (ok) open();
    });
  };
})();
