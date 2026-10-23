/* ==========================================================================
 * 侧栏可收起（左栏「图层」/ 右栏「面板」）
 *
 * QGIS 桌面版的停靠窗（dock widget）都能收起来：点标题栏上的小箭头把窗体
 * 收掉，屏幕上只留下一条窄把手，点它再拉回来。网页端原来只有「拖着边框改
 * 宽度」，不能收 —— 屏幕小的时候两侧栏把地图挤得只剩一条。
 *
 * 这里补上「收起」：
 *   * 面板头部各加一个小箭头按钮（收起）；
 *   * 收起后，屏幕对应边缘留一条窄把手（点它拉回来）；
 *   * 两边**互不影响**，可以只收一边；
 *   * 收起状态记在 localStorage（和面板尺寸分开存），刷新后还在；
 *   * 快捷键 [ 收/放左栏，] 收/放右栏。
 *
 * 收起时宽度置 0（`--w-left` / `--w-right`），地图要重算尺寸，
 * 否则瓦片会错位、留白。
 *
 * 自包含：样式、DOM、逻辑都在这个文件里，挂在 index.html 末尾一行
 * <script src="/static/sidefold.js"></script>（跟 panels.js / files.js 一个路子）。
 * ========================================================================== */
(function () {
  "use strict";

  const KEY = "gisds.fold";
  const $ = (id) => document.getElementById(id);
  const folded = { left: false, right: false };   // true = 已收起
  const lastWidth = { left: null, right: null };  // 收起前的宽度（拉回来时用）

  const CSS = `
/* 收起后那一列宽度为 0，边框也得去掉，否则还剩 1px */
aside.left.folded{border-right:none}
aside.right.folded{border-left:none}

/* 面板头部里的折叠按钮 */
.foldbtn{
  display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;
  width:21px;height:17px;padding:0;margin-left:6px;cursor:pointer;
  background:transparent;border:1px solid transparent;border-radius:3px;color:var(--ink-3);
}
.foldbtn:hover{background:var(--moss-soft);color:var(--ink)}
.foldbtn:focus-visible{outline:2px solid var(--moss);outline-offset:1px}
/* 右栏的折叠按钮贴在页签行右端（绝对定位，免得把页签挤窄） */
aside.right .tabs{position:relative;padding-right:26px}
aside.right .tabs > .foldbtn{position:absolute;right:0;top:0;bottom:0;margin:0;width:24px}

/* 收起后贴在屏幕边缘的把手 —— QGIS 里隐藏的停靠窗就是这个样。
   z-index 要**压过 Leaflet**：Leaflet 的控件是 1000、瓦片面板 400~700，
   用默认的 70 会被地图盖住（把手就看不见，也点不着）。 */
.side-tab{
  position:fixed;top:50%;transform:translateY(-50%);z-index:1200;
  width:20px;height:74px;padding:0;cursor:pointer;display:none;
  align-items:center;justify-content:center;
  background:var(--panel);color:var(--ink-2);border:1px solid var(--rule-2);
  box-shadow:0 2px 10px rgba(30,36,32,.13);
}
.side-tab.left{left:0;border-left:none;border-radius:0 3px 3px 0}
.side-tab.right{right:0;border-right:none;border-radius:3px 0 0 3px}
.side-tab:hover{background:var(--moss-soft);color:var(--ink)}
.side-tab:focus-visible{outline:2px solid var(--moss);outline-offset:-2px}
body.folded-left .side-tab.left{display:flex}
body.folded-right .side-tab.right{display:flex}
`;

  // 16px 描边箭头（左边收起用 «，右边收起用 »）
  const ARROW_L = '<path d="M10 3.5 5.5 8l4.5 4.5"/>';
  const ARROW_R = '<path d="M6 3.5 10.5 8 6 12.5"/>';

  function svg(body, size) {
    size = size || 14;
    return '<svg class="ic" viewBox="0 0 16 16" width="' + size + '" height="' + size +
      '" fill="none" stroke="currentColor" stroke-width="1.35" ' +
      'stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>";
  }

  // ---------------------------------------------------------------- 状态

  function load() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || "{}") || {};
      folded.left = !!v.left;
      folded.right = !!v.right;
    } catch (e) { /* 存坏了就当没收起 */ }
  }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(folded)); } catch (e) { /* 无痕模式等 */ }
  }

  function currentWidth(side) {
    const main = document.querySelector("main");
    if (!main) return null;
    const v = getComputedStyle(main)
      .getPropertyValue(side === "left" ? "--w-left" : "--w-right");
    return (v || "").trim() || null;
  }

  function mapInvalidate() {
    try {
      if (typeof map !== "undefined" && map && map.invalidateSize) map.invalidateSize();
    } catch (e) { /* map 还没建好 */ }
  }

  function apply() {
    const main = document.querySelector("main");
    if (!main) return;
    ["left", "right"].forEach((side) => {
      const varName = side === "left" ? "--w-left" : "--w-right";
      const aside = document.querySelector(side === "left" ? "aside.left" : "aside.right");
      if (folded[side]) {
        // 收起前把当前宽度记下来（用户可能拖过，拉回来要还原）
        if (lastWidth[side] == null) lastWidth[side] = currentWidth(side);
        main.style.setProperty(varName, "0px");
        if (aside) aside.classList.add("folded");
        document.body.classList.add("folded-" + side);
      } else {
        const w = lastWidth[side];
        if (w) main.style.setProperty(varName, w);
        lastWidth[side] = null;
        if (aside) aside.classList.remove("folded");
        document.body.classList.remove("folded-" + side);
      }
    });
    mapInvalidate();
  }

  function toggle(side) {
    folded[side] = !folded[side];
    save();
    apply();
  }

  // ---------------------------------------------------------------- 界面

  function foldButton(side, title, arrow) {
    const b = document.createElement("button");
    b.className = "foldbtn";
    b.id = side === "left" ? "btnFoldLeft" : "btnFoldRight";
    b.title = title;
    b.setAttribute("aria-label", title);
    b.innerHTML = svg(arrow, 14);
    b.onclick = () => toggle(side);
    return b;
  }

  function build() {
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    // 左栏：放进「图层」面板的头部
    const head = document.querySelector("aside.left .panel-head");
    if (head) head.appendChild(foldButton("left", "收起图层面板", ARROW_L));

    // 右栏：放进页签行右端
    const tabs = document.querySelector("aside.right .tabs");
    if (tabs) tabs.appendChild(foldButton("right", "收起面板", ARROW_R));

    // 屏幕边缘的把手（收起后才出现）
    ["left", "right"].forEach((side) => {
      const t = document.createElement("button");
      t.className = "side-tab " + side;
      t.id = side === "left" ? "tabLeft" : "tabRight";
      t.title = side === "left" ? "展开图层面板" : "展开面板";
      t.setAttribute("aria-label", t.title);
      t.innerHTML = svg(side === "left" ? ARROW_R : ARROW_L, 14);
      t.onclick = () => toggle(side);
      document.body.appendChild(t);
    });
  }

  // ---------------------------------------------------------------- 启动

  function init() {
    build();
    load();
    apply();
    document.addEventListener("keydown", (e) => {
      if (e.target && e.target.matches && e.target.matches("input,textarea,select")) return;
      if (e.key === "[") { e.preventDefault(); toggle("left"); }
      else if (e.key === "]") { e.preventDefault(); toggle("right"); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // 方便控制台里手动调
  window.toggleSide = toggle;
})();
