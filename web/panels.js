/* =============================================================================
   QGIS 桌面版「工具栏 + 面板」集成到网页版

   QGIS Desktop 顶部是菜单栏 + 一排工具栏，右侧是一列可停靠的面板。
   网页版没有停靠概念，所以对应地做成两样东西：

   * **顶部工具栏行**（#toolbars）—— 对应桌面版的图层 / 选择 / 编辑 / 导航 /
     视图五条工具栏，分组、图标、提示文案都照抄 QGIS。
   * **右栏面板** —— 在原有「识别结果 / 图层属性 / 工具箱」之外补上
     属性表、统计、选择、空间书签、日志消息、处理历史。

   所有能力都走 REST（``/api/panels/*``），底层是 PyQGIS，所以统计口径、
   表达式语义、编辑行为与 QGIS Desktop 完全一致 —— 不是另写一套网页版逻辑。

   本文件直接复用 index.html 的全局状态（``S`` / ``api`` / ``map`` / ``svg`` …）：
   同一批 classic script 共享同一个全局词法环境，不需要额外桥接。
   ============================================================================= */
(function () {
  "use strict";

  /* --------------------------------------------------------------- 图标补充 */
  /* 沿用 index.html 的 ICON 字典（16px 描边、跟随 currentColor），
     这样新工具栏和原有图标是同一套视觉语言。 */
  Object.assign(ICON, {
    table: '<rect x="2" y="2.6" width="12" height="10.8"/><path d="M2 6.2h12M2 9.6h12M6.4 6.2v7.2M10.2 6.2v7.2"/>',
    chart: '<path d="M2.4 13.6h11.2"/><rect x="3.6" y="8" width="2.4" height="4"/><rect x="7.4" y="4.6" width="2.4" height="7.4"/><rect x="11.2" y="9.8" width="2.4" height="2.2"/>',
    edit: '<path d="M11.2 2.2 13.8 4.8 5.8 12.8 2.6 13.4l.6-3.2z"/>',
    save: '<path d="M2.6 2.6h8.2l2.6 2.6v8.2H2.6z"/><path d="M5 2.6v3.6h5.2V2.6M5 13.4V9.6h6v3.8"/>',
    trash: '<path d="M3 4.2h10M6.4 4.2V2.8h3.2v1.4M4.3 4.2l.7 9h6l.7-9"/>',
    calc: '<rect x="3.2" y="1.8" width="9.6" height="12.4"/><path d="M5.4 5.2h5.2M5.4 8.2h5.2M5.4 11.2h5.2"/>',
    filter: '<path d="M2.2 3.2h11.6L9.4 8.4v4.4l-2.8 1.4V8.4z"/>',
    selAll: '<rect x="2.2" y="2.2" width="11.6" height="11.6"/><path d="M4.6 8.2 6.9 10.5 11.4 5.6"/>',
    selNone: '<rect x="2.2" y="2.2" width="11.6" height="11.6"/><path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2"/>',
    selInv: '<rect x="2.2" y="2.2" width="11.6" height="11.6"/><path d="M2.2 8h11.6"/>',
    left: '<path d="M9.6 3.4 5.2 8l4.4 4.6"/>',
    right: '<path d="M6.4 3.4 10.8 8l-4.4 4.6"/>',
    angle: '<path d="M2.4 13.4h11.2"/><path d="M2.4 13.4 8.4 2.8"/><path d="M8.4 13.4a7 7 0 0 0-1.5-4.3"/>',
    bookmark: '<path d="M4 2.2h8v11.6L8 10.6l-4 3.2z"/>',
    log: '<rect x="3.2" y="2.2" width="9.6" height="11.6"/><path d="M5.4 5.4h5.2M5.4 8h5.2M5.4 10.6h3"/>',
    history: '<circle cx="8" cy="8" r="5.6"/><path d="M8 4.8V8l2.4 1.6"/>',
    zoomSel: '<circle cx="7" cy="7" r="4.4"/><path d="M10.3 10.3 14 14"/><path d="M5.2 7h3.6M7 5.2v3.6"/>',
    scale: '<path d="M1.8 11.4h12.4"/><path d="M4 11.4v2.6M7 11.4v1.6M10 11.4v2.6M13 11.4v1.6"/><path d="M4 6.6h8"/>',
    /* 下面三个是为了消掉「同一个图标出现两次」：
       addFeature（添加要素）原来跟「添加图层」共用 plus；
       discard（放弃编辑）原来跟「删除选中」共用 trash；
       tip（地图提示）原来跟地图浮动条上的「识别」共用 identify。 */
    addFeature: '<path d="M5.6 3 10 7.4 5.6 11.8 1.2 7.4z"/><path d="M12.4 2.4v3M10.9 3.9h3"/>',
    discard: '<path d="M3.4 8.4a5 5 0 1 1 1.5 4.9"/><path d="M3.4 3.4v5h5"/>',
    tip: '<path d="M2.4 3.4h11.2v7.2H7.2L4.4 13.2v-2.6H2.4z"/>',
  });

  /* --------------------------------------------------------------- QGIS 图标 */
  /* 工具栏与浮动条一律用 **QGIS 官方的 SVG 原文件**（QGIS 3.44 的
     images/themes/default/），不重画、不近似。

     为什么走 <img> 而不是内联：QGIS 的图标是 24×24 的**填充式多灰阶**
     （#ccc / #999 / #000 / #fff），而本项目其它图标是 16px 描边式 ——
     svg() 硬编码了 viewBox="0 0 16 16" + fill="none" + stroke="currentColor"，
     把 QGIS 的 path 内联进来会被压成线框轮廓、彻底变形。
     所以这里用 <img> 引用原文件，让浏览器按原样渲染。

     许可：QGIS 是 GPL v2（仓库根 COPYING）。这些 SVG 是 QGIS 作品的
     一部分，随附说明见 web/icons/qgis/LICENSE-QGIS.txt。 */

  const QGIS_ICON = {
    /* 地图导航 —— 对应 QGIS 的 Map Navigation 工具栏 */
    pan: "mActionPan.svg",
    zoomIn: "mActionZoomIn.svg",
    zoomOut: "mActionZoomOut.svg",
    zoomLayer: "mActionZoomToLayer.svg",
    zoomSelected: "mActionZoomToSelected.svg",
    zoomAll: "mActionZoomFullExtent.svg",
    prevExtent: "mActionZoomLast.svg",
    nextExtent: "mActionZoomNext.svg",
    tip: "mActionMapTips.svg",
    refresh: "mActionRefresh.svg",

    /* 图层 —— QGIS 的图层工具栏是「新建图层 / 加数据库图层 / 加 WMS」几个下拉，
       语义上对应我们的「添加图层」对话框（数据源管理器） */
    addLayer: "mActionDataSourceManager.svg",

    /* 属性 —— QGIS 的 Attributes 工具栏 */
    openTable: "mActionOpenTable.svg",

    /* 编辑 / 数字化 —— QGIS 的 Digitizing 工具栏 */
    toggleEdit: "mActionToggleEditing.svg",
    saveEdit: "mActionSaveEdits.svg",
    discardEdit: "mActionCancelEdits.svg",
    addFeature: "mActionNewTableRow.svg",
    deleteSelected: "mActionDeleteSelectedFeatures.svg",
    fieldCalc: "mActionCalculateField.svg",

    /* 选择 —— QGIS 的 Selection 工具栏 */
    selectAll: "mActionSelectAll.svg",
    selectNone: "mActionDeselectAll.svg",
    selectInvert: "mActionInvertSelection.svg",
    selectExpr: "mIconExpressionSelect.svg",
    selectSpace: "mActionSelectRadius.svg",

    /* 地图浮动条 —— QGIS 的 Map Tools */
    identify: "mActionIdentify.svg",
    measure: "mActionMeasure.svg",
    area: "mActionMeasureArea.svg",
  };

  /* QGIS 图标是 24×24 灰阶，18px 显示最接近桌面版的观感。
     尺寸用**内联 style**：index.html 里的 `.ic{width:16px;height:16px}`
     是 CSS，优先级高于 HTML 的 width/height 属性，写属性会被它盖掉。 */
  function qgisIcon(name, size) {
    const file = QGIS_ICON[name];
    if (!file) return "";
    const s = size || 18;
    return '<img class="ic" style="width:' + s + 'px;height:' + s + 'px" ' +
      'src="/static/icons/qgis/' + file + '" alt="" draggable="false">';
  }

  /* 地图左上角的浮动条（QGIS 的 Map Tools）在 index.html 里，
     用的是本项目的描边图标。这里把它换成 QGIS 官方图标 ——
     不改 index.html（那是别人的文件），只在运行时替换。

     index.html 的 boot 会往 <span data-ic> 里填 svg，填充时机不固定，
     所以除了立即替换，还挂一个 MutationObserver 兜底：
     谁后填的 svg，都会被换回 QGIS 图标。 */
  function applyQgisFloatIcons() {
    document.querySelectorAll("#mapTools button[data-tool]").forEach((b) => {
      const html = qgisIcon(b.dataset.tool, 18);
      if (!html || b.querySelector("img.ic")) return;
      b.innerHTML = html;
    });
  }

  function watchFloatIcons() {
    const bar = document.getElementById("mapTools");
    if (!bar || typeof MutationObserver === "undefined") return;
    const mo = new MutationObserver(() => applyQgisFloatIcons());
    mo.observe(bar, { childList: true, subtree: true });
  }

  /* ----------------------------------------------------------------- 状态 */
  const P = {
    info: { ref: null, data: null },
    table: { ref: null, offset: 0, limit: 100, total: 0, rows: [], fields: [], where: "", editing: false, sort: "", order: "asc" },
    stats: { ref: null, field: "", data: null },
    sel: { ref: null, fids: new Set() },
    bookmark: { scope: null, items: [] },
    logs: { timer: null, level: "" },
    hist: { timer: null },
    extents: [],
    mapTips: false,
    tipTimer: null,
    draft: null, // 采集新要素时的草稿
    editing: null, // 正在编辑的图层 ref
  };

  const $ = (id) => document.getElementById(id);

  /* index.html 的 api() 把 opts 原样透传给 fetch，body 必须是**字符串**。
     面板这边到处要发 JSON，顺手包一层，省得每处都写 JSON.stringify。 */
  const call = (path, opts) => {
    if (opts && opts.body !== undefined && typeof opts.body !== "string") {
      opts = {
        ...opts,
        body: JSON.stringify(opts.body),
        headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      };
    }
    return api(path, opts);
  };

  /* ------------------------------------------------------------ 通用小工具 */

  function vectorItems() {
    return (S.active || []).filter((it) => it && it.type === "vector" && it.layer && it.layer.ref);
  }

  function currentVector() {
    const sel = S.selected;
    if (sel && sel.type === "vector" && sel.layer && sel.layer.ref) return sel;
    const list = vectorItems();
    return list.length ? list[0] : null;
  }

  function refOf(item) {
    return item && item.layer ? item.layer.ref : null;
  }

  function layerTitle(ref) {
    const item = (S.active || []).find((it) => it.layer && it.layer.ref === ref);
    if (item) return item.layer.title || ref;
    return ref;
  }

  function setBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = busy;
    btn.classList.toggle("busy", !!busy);
  }

  /* 轻量对话框：复用控制台自带的 .modal / .dlg 样式 */
  function dialog(title, bodyHtml, onOk, okText) {
    const wrap = document.createElement("div");
    wrap.className = "modal show";
    wrap.innerHTML =
      '<div class="dlg" style="width:470px;max-width:94vw">' +
      '<div class="dlg-title">' + esc(title) + '<span class="x">&times;</span></div>' +
      '<div class="dlg-body" style="display:block;padding:13px 15px">' + bodyHtml + "</div>" +
      '<div class="dlg-foot"><span class="hint left" data-role="msg"></span>' +
      '<button class="btn" data-role="cancel">取消</button>' +
      '<button class="btn primary" data-role="ok">' + esc(okText || "确定") + "</button></div></div>";
    document.body.appendChild(wrap);

    const close = () => {
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    };
    const msg = wrap.querySelector('[data-role="msg"]');
    wrap.querySelector(".x").onclick = close;
    wrap.querySelector('[data-role="cancel"]').onclick = close;
    wrap.onclick = (e) => {
      if (e.target === wrap) close();
    };
    wrap.querySelector('[data-role="ok"]').onclick = async () => {
      const btn = wrap.querySelector('[data-role="ok"]');
      btn.disabled = true;
      try {
        const keep = await onOk(wrap, msg);
        if (keep !== false) close();
      } catch (err) {
        if (msg) msg.textContent = err && err.message ? err.message : String(err);
      } finally {
        btn.disabled = false;
      }
    };
    const first = wrap.querySelector("input,select,textarea");
    if (first) first.focus();
    return wrap;
  }

  /* ======================================================================== */
  /* 1. 顶部工具栏                                                          */
  /* ======================================================================== */

  /* QGIS 桌面版是**两排**工具栏，这里按同样的分工铺：
     第一排 =「地图导航工具栏」，第二排 =「编辑/数字化 + 属性工具栏」。
     识别与测量在 QGIS 里是另一个独立的浮动工具条 —— 我们地图左上角那组就是它。

     布局写成「排 → 组 → 按钮」三层：加一排就在 TOOLBAR_ROWS 里多一个数组。 */
  /* 分组与顺序照 QGIS 3.44 的顶部工具栏语义：
       QGIS 桌面版从左到右是 文件 → 图层 → 数字化(编辑) → 地图导航 → 属性 → 选择，
       另有「文件 / 插件 / 栅格 / 数据库 / Web / 标注 / 捕捉 / 网格 / 标注项」
       这些本项目没有对应能力的工具栏就不搬。

     两排的切法沿用 QGIS 的**浮动 Map Tools**：识别与测量在 QGIS 里属于地图上
     那个独立的浮动工具条（我们地图左上角那组就是它），所以不放进顶部两排。

     顺序 = 照 QGIS；icon 键 = QGIS_ICON 的键（拿不到官方图标的才用自绘，
     「按比例缩放」和「字段统计」QGIS 桌面版没有对应的工具栏按钮 ——
      后者在 QGIS 里是 Processing 的「基础统计」算法，不在工具栏上）。

     「按空间关系选择要素」同理：QGIS 桌面版没有这个工具栏按钮（「按位置选择」
      是 Processing 的算法），这里借 QGIS 官方**按半径选择**的图标
      （mActionSelectRadius.svg）—— 是空间选择里最接近的一个，但严格说是近似。 */
  const TOOLBAR_ROWS = [
    [
      {
        group: "导航",
        items: [
          { act: "toolPan", icon: "pan", title: "平移地图（P）" },
          { act: "toolZoomIn", icon: "zoomIn", title: "放大（+）" },
          { act: "toolZoomOut", icon: "zoomOut", title: "缩小（−）" },
          { act: "zoomLayer", icon: "zoomLayer", title: "缩放到图层" },
          { act: "zoomSelected", icon: "zoomSelected", title: "缩放到选择" },
          { act: "zoomAll", icon: "zoomAll", title: "全图" },
          { act: "prevExtent", icon: "prevExtent", title: "上一个范围" },
          { act: "nextExtent", icon: "nextExtent", title: "下一个范围" },
          { act: "zoomScale", icon: "scale", title: "按比例缩放（输入 1:N）" },
          { act: "toggleMapTips", icon: "tip", title: "地图提示（悬停显示要素信息）" },
          { act: "refresh", icon: "refresh", title: "刷新地图" },
        ],
      },
    ],
    [
      {
        group: "图层",
        items: [
          { act: "addLayer", icon: "addLayer", title: "添加图层" },
        ],
      },
      {
        group: "编辑",
        items: [
          { act: "toggleEdit", icon: "toggleEdit", title: "切换编辑模式" },
          { act: "saveEdit", icon: "saveEdit", title: "保存编辑" },
          { act: "discardEdit", icon: "discardEdit", title: "放弃编辑" },
          { act: "addFeature", icon: "addFeature", title: "添加要素" },
          { act: "deleteSelected", icon: "deleteSelected", title: "删除选中要素" },
          { act: "fieldCalc", icon: "fieldCalc", title: "字段计算器" },
        ],
      },
      {
        group: "属性",
        items: [
          { act: "openTable", icon: "openTable", title: "打开属性表" },
          { act: "openStats", icon: "chart", title: "字段统计" },
        ],
      },
      {
        group: "选择",
        items: [
          { act: "selectAll", icon: "selectAll", title: "全选要素" },
          { act: "selectNone", icon: "selectNone", title: "取消选择" },
          { act: "selectInvert", icon: "selectInvert", title: "反选" },
          { act: "selectExpr", icon: "selectExpr", title: "按表达式选择要素" },
          { act: "selectSpace", icon: "selectSpace", title: "按空间关系选择要素" },
        ],
      },
    ],
  ];

  function buildToolbars() {
    if ($("toolbars")) return;
    const bar = document.createElement("div");
    bar.id = "toolbars";
    bar.className = "toolbars";

    TOOLBAR_ROWS.forEach((row) => {
      const rowEl = document.createElement("div");
      rowEl.className = "tb-row";
      row.forEach((grp, gi) => {
        const g = document.createElement("div");
        g.className = "tb-group";
        g.dataset.group = grp.group;
        grp.items.forEach((it) => {
          const b = document.createElement("button");
          b.dataset.act = it.act;
          // 分组名在**外层**的 grp 上（item 里没有 group 这个键）——
          // 写成 it.group 的话提示就成了「undefined · 添加图层」。
          b.title = (grp.group || "") + " · " + (it.title || "");
          // 有 QGIS 官方图标的用原文件；没有的（按比例缩放、字段统计）沿用本项目自绘
          b.innerHTML = qgisIcon(it.icon, 18) || svg(it.icon, 16);
          g.appendChild(b);
        });
        rowEl.appendChild(g);
        if (gi < row.length - 1) {
          const sep = document.createElement("div");
          sep.className = "tb-sep";
          rowEl.appendChild(sep);
        }
      });
      bar.appendChild(rowEl);
    });

    const main = document.querySelector("main");
    if (main && main.parentNode) main.parentNode.insertBefore(bar, main);

    bar.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-act]");
      if (!btn) return;
      onToolbarAction(btn.dataset.act, btn);
    });
  }

  /* ======================================================================== */
  /* 2. 右栏面板                                                            */
  /* ======================================================================== */

  const PANELS = [
    {
      id: "Info",
      label: "信息",
      html:
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<button class="btn" id="infoRefresh" title="重新读取">刷新</button>' +
        '<span class="hint" id="infoState">—</span></div></div>' +
        '<div class="sec" style="padding:0"><dl class="kv" id="infoEl"></dl></div>',
    },
    {
      id: "Table",
      label: "属性表",
      html:
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<select id="tblLayer" style="flex:1;min-width:0"></select>' +
        '<button class="btn" id="tblRefresh" title="重新读取">刷新</button></div>' +
        '<div class="row" style="gap:6px;margin-top:7px">' +
        '<input id="tblFilter" placeholder="过滤表达式，例如 &quot;面积&quot; &gt; 100" ' +
        'style="flex:1;min-width:0" />' +
        '<button class="btn" id="tblApply">应用</button></div>' +
        '<div class="row" style="gap:6px;margin-top:6px">' +
        '<span class="hint" id="tblState">—</span>' +
        '<span class="spacer"></span>' +
        '<span class="hint" id="tblEdit" style="display:none;color:var(--rust)">编辑中</span></div></div>' +
        '<div class="sec" style="padding:0"><div class="tbl-wrap"><table class="tbl" id="tblEl"></table></div></div>' +
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<button class="btn" id="tblPrev">上一页</button>' +
        '<button class="btn" id="tblNext">下一页</button>' +
        '<span class="hint" id="tblPage">—</span></div></div>',
    },
    {
      id: "Stats",
      label: "统计",
      html:
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<select id="statField" style="flex:1;min-width:0"></select>' +
        '<button class="btn" id="statRefresh">统计</button></div>' +
        '<div class="hint" id="statState" style="margin-top:6px">—</div></div>' +
        '<div class="sec" style="padding:0"><div class="tbl-wrap"><table class="tbl" id="statEl"></table></div></div>',
    },
    {
      id: "Sel",
      label: "选择",
      html:
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<button class="btn" id="selAll">全选</button>' +
        '<button class="btn" id="selNone">取消</button>' +
        '<button class="btn" id="selInv">反选</button>' +
        '<button class="btn" id="selExpr">表达式</button>' +
        '<button class="btn" id="selSpace">空间关系</button></div>' +
        '<div class="hint" id="selState" style="margin-top:6px">—</div></div>' +
        '<div class="sec" style="padding:0"><div class="tbl-wrap"><table class="tbl" id="selEl"></table></div></div>',
    },
    {
      id: "Bookmark",
      label: "书签",
      html:
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<button class="btn primary" id="bmAdd" title="用当前地图范围新建书签">新建</button>' +
        '<button class="btn" id="bmRefresh">刷新</button>' +
        '<span class="hint" id="bmState">—</span></div></div>' +
        '<div class="sec" style="padding:0"><div class="tbl-wrap"><table class="tbl" id="bmEl"></table></div></div>',
    },
    {
      id: "Log",
      label: "日志",
      html:
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<select id="logLevel" style="flex:1;min-width:0">' +
        '<option value="">全部级别</option><option>INFO</option><option>WARNING</option><option>ERROR</option>' +
        "</select>" +
        '<button class="btn" id="logRefresh">刷新</button>' +
        '<button class="btn" id="logClear">清空</button></div></div>' +
        '<div class="sec" style="padding:0"><pre class="logbox" id="logEl"></pre></div>',
    },
    {
      id: "Hist",
      label: "历史",
      html:
        '<div class="sec"><div class="row" style="gap:6px">' +
        '<button class="btn" id="histRefresh">刷新</button>' +
        '<span class="hint" id="histState">—</span></div></div>' +
        '<div class="sec" style="padding:0"><div class="tbl-wrap"><table class="tbl" id="histEl"></table></div></div>',
    },
  ];

  function buildPanels() {
    // 注意：要选右侧那一栏，不能用 ".right" ——
    // 菜单栏里也有一个 <div class="right">（放登录状态那几项），会选错。
    const tabs = document.querySelector("aside.right .tabs");
    const aside = document.querySelector("aside.right");
    if (!tabs || !aside) return;

    PANELS.forEach((p) => {
      const tab = document.createElement("div");
      tab.className = "tab";
      tab.dataset.pane = "pane" + p.id;
      tab.textContent = p.label;
      tabs.appendChild(tab);

      const pane = document.createElement("div");
      pane.className = "tabpane";
      pane.id = "pane" + p.id;
      pane.innerHTML = p.html;
      aside.appendChild(pane);

      tab.onclick = () => {
        switchTab(pane.id);
        onPanelShown(p.id);
      };
    });
  }

  /* ======================================================================== */
  /* 3. 信息面板                                                            */
  /* ======================================================================== */

  async function loadInfo() {
    const item = currentVector();
    const ref = refOf(item);
    const el = $("infoEl");
    if (!el) return;
    if (!ref) {
      el.innerHTML = "";
      $("infoState").textContent = "地图上还没有矢量图层";
      return;
    }
    P.info.ref = ref;
    try {
      const d = await call("/api/panels/overview?ref=" + encodeURIComponent(ref));
      P.info.data = d;
      const ext = d.extent || [];
      const rows = [
        ["图层", d.title || ref],
        ["数据集", d.dataset_id || "—"],
        ["空间", d.scope === "shared" ? "共享空间" : "个人空间"],
        ["几何类型", wkbName(d.geometry_type)],
        ["要素数", d.feature_count],
        ["字段数", d.field_count],
        ["坐标系", d.crs || "—"],
        ["数据提供程序", d.provider || "—"],
        [
          "范围",
          ext.length === 4
            ? ext.map((v) => Number(v).toFixed(4)).join(" , ")
            : "—",
        ],
        ["编辑状态", d.is_editing ? "编辑中" : "未编辑"],
      ];
      el.innerHTML = rows
        .map(([k, v]) => "<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>")
        .join("");
      $("infoState").textContent = "";
    } catch (err) {
      el.innerHTML = "";
      $("infoState").textContent = "读取失败：" + err.message;
    }
  }

  /* ======================================================================== */
  /* 4. 属性表面板                                                          */
  /* ======================================================================== */

  function refreshLayerOptions() {
    const sel = $("tblLayer");
    if (!sel) return;
    const items = vectorItems();
    const cur = P.table.ref;
    sel.innerHTML = items.length
      ? items
          .map(
            (it) =>
              '<option value="' +
              esc(it.layer.ref) +
              '">' +
              esc(it.layer.title || it.layer.ref) +
              "</option>"
          )
          .join("")
      : '<option value="">（地图上还没有矢量图层）</option>';
    if (cur && items.some((it) => it.layer.ref === cur)) sel.value = cur;
  }

  async function loadTable() {
    const sel = $("tblLayer");
    if (!sel) return;
    const ref = sel.value || P.table.ref;
    if (!ref) {
      $("tblEl").innerHTML = "";
      $("tblState").textContent = "地图上还没有矢量图层";
      return;
    }
    if (ref !== P.table.ref) {
      P.table.ref = ref;
      P.table.offset = 0;
      P.sel.ref = ref;
      P.sel.fids.clear();
    }
    const q = new URLSearchParams({ ref: ref, limit: String(P.table.limit), offset: String(P.table.offset) });
    if (P.table.where) q.set("where", P.table.where);
    // 排序：点列头切换（QGIS 桌面版属性表列头可点排序）
    if (P.table.sort) {
      q.set("sort", P.table.sort);
      q.set("order", P.table.order || "asc");
    }
    $("tblState").textContent = "读取中…";
    try {
      const data = await call("/api/panels/table?" + q.toString());
      P.table.fields = data.fields || [];
      P.table.rows = data.rows || [];
      P.table.total = data.total || 0;
      P.table.editing = !!data.is_editing;
      P.editing = data.is_editing ? ref : null;
      renderTable();
      updateToolbarState();
    } catch (err) {
      $("tblState").textContent = "读取失败：" + err.message;
    }
  }

  function renderTable() {
    const el = $("tblEl");
    if (!el) return;
    // 主键（FID）列**不单独显示**：表头已经有一列 fid（来自 feature.id()），
    // 再显示一遍就成了两列同名；而它**不能编辑**（服务端会挡），
    // 改了保存时 provider 会报错、整个编辑会话被回滚。
    const fields = (P.table.fields || []).filter((f) => !f.fid);
    const rows = P.table.rows || [];
    if (!fields.length) {
      el.innerHTML = "";
      $("tblState").textContent = "该图层没有字段";
      return;
    }
    let html = "<thead><tr><th>#</th><th>fid</th>";
    fields.forEach((f) => {
      const on = P.table.sort === f.name;
      const arrow = on ? (P.table.order === "desc" ? " ▼" : " ▲") : "";
      html +=
        '<th class="sortable' +
        (on ? " on" : "") +
        '" data-sort="' +
        esc(f.name) +
        '" title="' +
        esc(f.type) +
        '">' +
        esc(f.alias || f.name) +
        arrow +
        "</th>";
    });
    html += "</tr></thead><tbody>";

    rows.forEach((row, i) => {
      const selected = P.sel.ref === P.table.ref && P.sel.fids.has(row.fid);
      html += '<tr data-fid="' + row.fid + '"' + (selected ? ' class="sel"' : "") + ">";
      html += "<td>" + (P.table.offset + i + 1) + "</td>";
      html += '<td class="num">' + row.fid + "</td>";
      fields.forEach((f) => {
        const v = row.cells[f.name];
        const isNum = typeof v === "number";
        html +=
          '<td class="cell' +
          (isNum ? " num" : "") +
          '" data-field="' +
          esc(f.name) +
          '">' +
          (v === null || v === undefined || v === "" ? '<span class="muted">—</span>' : esc(v)) +
          "</td>";
      });
      html += "</tr>";
    });
    html += "</tbody>";
    el.innerHTML = html;

    $("tblState").textContent =
      P.table.total + " 个要素" + (P.table.where ? "（已过滤）" : "") + " · " + fields.length + " 个字段";
    $("tblEdit").style.display = P.table.editing ? "" : "none";

    const from = P.table.total ? P.table.offset + 1 : 0;
    const to = P.table.offset + rows.length;
    $("tblPage").textContent = from + "–" + to + " / " + P.table.total;
    $("tblPrev").disabled = P.table.offset <= 0;
    $("tblNext").disabled = to >= P.table.total;
  }

  function bindTableEvents() {
    const el = $("tblEl");
    if (!el) return;

    $("tblRefresh").onclick = () => loadTable();
    $("tblApply").onclick = () => {
      P.table.where = ($("tblFilter").value || "").trim();
      P.table.offset = 0;
      loadTable();
    };
    $("tblPrev").onclick = () => {
      P.table.offset = Math.max(0, P.table.offset - P.table.limit);
      loadTable();
    };
    $("tblNext").onclick = () => {
      P.table.offset += P.table.limit;
      loadTable();
    };
    $("tblLayer").onchange = () => {
      P.table.ref = $("tblLayer").value;
      P.table.offset = 0;
      loadTable();
    };

    // 点行 = 选中要素（地图上高亮）；双击单元格 = 改属性（编辑模式下）
    el.addEventListener("click", (e) => {
      // 点列头 = 排序（QGIS 桌面版属性表列头可点排序）
      const th = e.target.closest("th[data-sort]");
      if (th) {
        const name = th.dataset.sort;
        if (P.table.sort === name) {
          P.table.order = P.table.order === "asc" ? "desc" : "asc";
        } else {
          P.table.sort = name;
          P.table.order = "asc";
        }
        P.table.offset = 0;
        loadTable();
        return;
      }
      const tr = e.target.closest("tr[data-fid]");
      if (!tr) return;
      const fid = Number(tr.dataset.fid);
      if (e.ctrlKey || e.metaKey) {
        toggleSelect(fid);
      } else {
        P.sel.fids = new Set([fid]);
        P.sel.ref = P.table.ref;
        highlightSelection();
        renderTable();
        renderSelectionPanel();
      }
    });
    el.addEventListener("dblclick", (e) => {
      const td = e.target.closest("td.cell");
      if (!td) return;
      editCell(td);
    });
  }

  async function editCell(td) {
    if (!P.table.editing) {
      toast("先点工具栏上的「切换编辑」再改属性");
      return;
    }
    const field = td.dataset.field;
    const fid = Number(td.closest("tr").dataset.fid);
    const old = td.textContent.trim();
    td.innerHTML = '<input style="width:100%;min-width:60px" />';
    const input = td.querySelector("input");
    input.value = old === "—" ? "" : old;
    input.focus();
    input.select();

    const finish = async (save) => {
      const value = input.value;
      input.removeEventListener("blur", onBlur);
      if (!save) {
        renderTable();
        return;
      }
      try {
        await call("/api/panels/edit/feature", {
          method: "PATCH",
          body: { ref: P.table.ref, fid: fid, attributes: { [field]: value } },
        });
        tip("已修改 " + field);
        await loadTable();
        await refreshMapLayer(P.table.ref);
      } catch (err) {
        toast("修改失败：" + err.message);
        renderTable();
      }
    };
    const onBlur = () => finish(false);
    input.addEventListener("blur", onBlur);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        input.removeEventListener("blur", onBlur);
        finish(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        input.removeEventListener("blur", onBlur);
        finish(false);
      }
    });
  }

  /* ======================================================================== */
  /* 4. 统计面板                                                            */
  /* ======================================================================== */

  async function loadStats() {
    const ref = P.stats.ref || (currentVector() ? refOf(currentVector()) : null);
    if (!ref) {
      $("statState").textContent = "地图上还没有矢量图层";
      return;
    }
    P.stats.ref = ref;
    $("statState").textContent = "统计中…";
    try {
      // **把下拉里选中的字段传过去** —— 原来没传，接口统计的是**全部字段**，
      // 下拉框成了摆设（选了也白选）。
      let q = "/api/panels/statistics?ref=" + encodeURIComponent(ref);
      if (P.stats.field) q += "&field=" + encodeURIComponent(P.stats.field);
      const data = await call(q);
      P.stats.data = data;
      const sel = $("statField");
      if (sel) {
        const cur = P.stats.field;
        // 下拉里列**全部字段**（用属性表那份清单），别用统计结果 ——
        // 统计结果在选了单个字段时只有那一个，下拉会越缩越窄。
        const all = (P.table.fields || []).filter((f) => !f.fid);
        const opts = all.length ? all : data.fields || [];
        sel.innerHTML =
          '<option value="">全部字段</option>' +
          opts
            .map((f) => '<option value="' + esc(f.name) + '">' + esc(f.name) + "</option>")
            .join("");
        if (cur) sel.value = cur;
      }
      renderStats();
    } catch (err) {
      $("statState").textContent = "统计失败：" + err.message;
    }
  }

  function renderStats() {
    const el = $("statEl");
    if (!el || !P.stats.data) return;
    const fields = P.stats.data.fields || [];
    const labels = [
      ["count", "计数"],
      ["unique", "唯一值"],
      ["empty", "空值"],
      ["min", "最小值"],
      ["max", "最大值"],
      ["mean", "平均值"],
      ["median", "中位数"],
      ["sum", "求和"],
      ["range", "极差"],
      ["stddev", "标准差"],
    ];
    el.innerHTML =
      "<thead><tr><th>字段</th><th>指标</th><th>值</th></tr></thead><tbody>" +
      fields
        .map((f) => {
          let body = "";
          labels.forEach(([k, label]) => {
            const v = f[k];
            if (v === undefined || v === null) return;
            const show = typeof v === "number" ? Math.round(v * 10000) / 10000 : v;
            body += "<tr><td></td><td>" + label + '</td><td class="num">' + esc(show) + "</td></tr>";
          });
          (f.top || []).forEach((t) => {
            body +=
              '<tr><td></td><td class="muted">高频值</td><td class="num">' +
              esc(t.value) +
              " × " +
              esc(t.count) +
              "</td></tr>";
          });
          return (
            '<tr class="stat-group"><td colspan="3"><b>' +
            esc(f.name) +
            '</b> <span class="muted">' +
            esc(f.type) +
            "</span></td></tr>" +
            body
          );
        })
        .join("") +
      "</tbody>";
    $("statState").textContent =
      "共 " + (P.stats.data.feature_count || 0) + " 个要素" + (P.stats.data.truncated ? "（已截断）" : "");
  }

  /* ======================================================================== */
  /* 5. 选择                                                                */
  /* ======================================================================== */

  function toggleSelect(fid) {
    if (P.sel.fids.has(fid)) P.sel.fids.delete(fid);
    else P.sel.fids.add(fid);
    highlightSelection();
    renderSelectionPanel();
    renderTable();
  }

  function setSelection(fids) {
    P.sel.fids = new Set(fids);
    highlightSelection();
    renderSelectionPanel();
    renderTable();
  }

  function highlightSelection() {
    const ref = P.sel.ref;
    if (!ref) return;
    const ov = S.overlays.get(ref);
    if (!ov || !ov.eachLayer) return;
    const item = (S.active || []).find((it) => it.layer && it.layer.ref === ref);
    if (!item) return;
    const st = S.styles && S.styles.get(ref) ? S.styles.get(ref) : { color: "#2f6f9e", width: 1.6, fillOpacity: 0.2 };
    const color = st.color || "#2f6f9e";
    ov.eachLayer((ly) => {
      const fid = ly.feature && ly.feature.id;
      const on = fid !== undefined && P.sel.fids.has(fid);
      if (on) {
        ly.setStyle({ color: "#e6194b", weight: (st.width || 1.6) + 1.6, fillColor: "#e6194b", fillOpacity: 0.42 });
      } else {
        ly.setStyle({ color: color, weight: st.width || 1.6, fillColor: color, fillOpacity: st.fillOpacity || 0.2 });
      }
    });
  }

  function renderSelectionPanel() {
    const el = $("selEl");
    if (!el) return;
    const fids = [...P.sel.fids];
    $("selState").textContent = P.sel.ref
      ? (P.sel.ref.split(":").pop() + " · 已选 " + fids.length + " 个要素")
      : "未选择图层";
    if (!fids.length) {
      el.innerHTML = '<tbody><tr><td class="muted" style="padding:10px">没有选中的要素</td></tr></tbody>';
      return;
    }
    el.innerHTML =
      "<thead><tr><th>fid</th><th>属性</th></tr></thead><tbody>" +
      fids
        .map((fid) => {
          const row = (P.table.rows || []).find((r) => r.fid === fid);
          let summary = "";
          if (row) {
            summary = Object.keys(row.cells)
              .slice(0, 3)
              .map((k) => k + "=" + row.cells[k])
              .join("  ");
          }
          return '<tr data-fid="' + fid + '"><td class="num">' + fid + "</td><td>" + esc(summary) + "</td></tr>";
        })
        .join("") +
      "</tbody>";
  }

  async function selectAllFeatures() {
    const item = currentVector();
    if (!item) {
      toast("先选一个矢量图层");
      return;
    }
    const ref = refOf(item);
    try {
      const data = await call("/api/panels/select", { method: "POST", body: { ref: ref, mode: "all" } });
      P.sel.ref = ref;
      setSelection(data.fids || []);
      tip("已选中 " + (data.fids || []).length + " 个要素");
    } catch (err) {
      toast("全选失败：" + err.message);
    }
  }

  async function selectNoneFeatures() {
    setSelection([]);
    tip("已取消选择");
  }

  async function selectInvertFeatures() {
    const item = currentVector();
    if (!item) {
      toast("先选一个矢量图层");
      return;
    }
    const ref = refOf(item);
    try {
      const data = await call("/api/panels/select", { method: "POST", body: { ref: ref, mode: "all" } });
      const all = data.fids || [];
      const inverted = all.filter((f) => !P.sel.fids.has(f));
      P.sel.ref = ref;
      setSelection(inverted);
      tip("已反选 " + inverted.length + " 个要素");
    } catch (err) {
      toast("反选失败：" + err.message);
    }
  }

  function dialogSelectByExpression() {
    const item = currentVector();
    if (!item) {
      toast("先选一个矢量图层");
      return;
    }
    const ref = refOf(item);
    dialog(
      "按表达式选择要素",
      '<div class="fld"><label>表达式（QGIS 语法）</label>' +
        '<input type="text" id="selExprInput" placeholder="&quot;面积&quot; &gt; 100" /></div>' +
        '<div class="note info">支持 QGIS 表达式，比如 <code>"名称" = &apos;甲&apos;</code>、<code>"面积" &gt; 100</code>。</div>',
      async (wrap) => {
        const expr = wrap.querySelector("#selExprInput").value.trim();
        if (!expr) throw new Error("表达式不能为空");
        const data = await call("/api/panels/select", { method: "POST", body: { ref: ref, expression: expr } });
        P.sel.ref = ref;
        setSelection(data.fids || []);
        tip("按表达式选中 " + (data.fids || []).length + " 个要素");
      },
      "选择"
    );
  }

  function dialogSelectBySpace() {
    const item = currentVector();
    if (!item) {
      toast("先选一个矢量图层");
      return;
    }
    const ref = refOf(item);
    const targets = vectorItems().filter((it) => it.layer.ref !== ref);
    const options =
      '<option value="">（用当前地图范围）</option>' +
      targets.map((it) => '<option value="' + esc(it.layer.ref) + '">' + esc(it.layer.title || it.layer.ref) + "</option>").join("");
    dialog(
      "按空间关系选择要素",
      '<div class="fld"><label>目标图层</label><select id="selSpaceTarget" style="width:100%">' + options + "</select></div>" +
        '<div class="fld"><label>空间关系</label><select id="selSpacePred" style="width:100%">' +
        '<option value="intersects">相交（intersects）</option>' +
        '<option value="contains">包含（contains）</option>' +
        '<option value="within">位于内部（within）</option>' +
        '<option value="touches">相接（touches）</option>' +
        '<option value="crosses">穿过（crosses）</option>' +
        '<option value="overlaps">重叠（overlaps）</option>' +
        '<option value="disjoint">分离（disjoint）</option>' +
        "</select></div>" +
        '<div class="note info">选「当前地图范围」时按你框出的范围选；选具体图层时按该图层的要素选。</div>',
      async (wrap) => {
        const target = wrap.querySelector("#selSpaceTarget").value;
        const predicate = wrap.querySelector("#selSpacePred").value;
        let body;
        if (target) {
          body = { ref: ref, target_ref: target, predicate: predicate };
        } else {
          const b = map.getBounds();
          body = {
            ref: ref,
            predicate: predicate,
            geometry: {
              type: "Polygon",
              coordinates: [
                [
                  [b.getWest(), b.getSouth()],
                  [b.getEast(), b.getSouth()],
                  [b.getEast(), b.getNorth()],
                  [b.getWest(), b.getNorth()],
                  [b.getWest(), b.getSouth()],
                ],
              ],
            },
          };
        }
        const data = await call("/api/panels/select", { method: "POST", body: body });
        P.sel.ref = ref;
        setSelection(data.fids || []);
        tip("按空间关系选中 " + (data.fids || []).length + " 个要素");
      },
      "选择"
    );
  }

  function zoomToSelection() {
    if (!P.sel.fids.size) {
      toast("没有选中的要素");
      return;
    }
    const ov = S.overlays.get(P.sel.ref);
    if (!ov || !ov.eachLayer) {
      toast("该图层不在地图上");
      return;
    }
    let bounds = null;
    ov.eachLayer((ly) => {
      const fid = ly.feature && ly.feature.id;
      if (fid === undefined || !P.sel.fids.has(fid)) return;
      const b = ly.getBounds();
      if (!b || !b.isValid()) return;
      bounds = bounds ? bounds.extend(b) : L.latLngBounds(b.getSouth(), b.getWest(), b.getNorth(), b.getEast());
    });
    if (!bounds || !bounds.isValid()) {
      toast("选中的要素没有范围");
      return;
    }
    map.fitBounds(bounds, { padding: [30, 30] });
  }

  /* ======================================================================== */
  /* 6. 空间书签                                                            */
  /* ======================================================================== */

  async function loadBookmarks() {
    try {
      const data = await call("/api/panels/bookmarks");
      P.bookmark.items = data.bookmarks || [];
      P.bookmark.scope = data.scope;
      renderBookmarks();
    } catch (err) {
      $("bmState").textContent = "读取失败：" + err.message;
    }
  }

  function renderBookmarks() {
    const el = $("bmEl");
    if (!el) return;
    const items = P.bookmark.items || [];
    $("bmState").textContent = (P.bookmark.scope || "") + " · " + items.length + " 个书签";
    if (!items.length) {
      el.innerHTML = '<tbody><tr><td class="muted" style="padding:10px">还没有书签</td></tr></tbody>';
      return;
    }
    el.innerHTML =
      "<thead><tr><th>名称</th><th>范围</th><th></th></tr></thead><tbody>" +
      items
        .map(
          (b) =>
            '<tr data-name="' +
            esc(b.name) +
            '"><td>' +
            esc(b.name) +
            '</td><td class="muted mono" style="font-size:10.5px">' +
            b.extent.map((v) => Math.round(v * 1000) / 1000).join(", ") +
            '</td><td><a href="#" data-act="zoom">缩放</a> · <a href="#" data-act="del">删</a></td></tr>'
        )
        .join("") +
      "</tbody>";
  }

  function addBookmark() {
    const b = map.getBounds();
    dialog(
      "新建空间书签",
      '<div class="fld"><label>名称</label><input type="text" id="bmName" placeholder="例如：示例范围" /></div>' +
        '<div class="hint">范围取当前地图视野：' +
        esc(
          [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
            .map((v) => Math.round(v * 10000) / 10000)
            .join(", ")
        ) +
        "</div>",
      async (wrap) => {
        const name = wrap.querySelector("#bmName").value.trim();
        if (!name) throw new Error("书签名不能为空");
        await call("/api/panels/bookmarks", {
          method: "POST",
          body: { name: name, extent: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], crs: "EPSG:4326" },
        });
        await loadBookmarks();
        tip("书签已保存");
      },
      "保存"
    );
  }

  /* ======================================================================== */
  /* 7. 日志 / 处理历史                                                     */
  /* ======================================================================== */

  async function loadLogs() {
    const level = ($("logLevel") || {}).value || "";
    try {
      const q = level ? "?limit=300&level=" + encodeURIComponent(level) : "?limit=300";
      const data = await call("/api/panels/logs" + q);
      const el = $("logEl");
      if (el) el.textContent = (data.entries || []).map((e) => e.text).join("\n") || "（暂无日志）";
    } catch (err) {
      const el = $("logEl");
      if (el) el.textContent = "读取日志失败：" + err.message;
    }
  }

  async function loadHistory() {
    try {
      const data = await api("/api/processing/jobs?limit=30");
      const jobs = data.jobs || [];
      const el = $("histEl");
      if (!el) return;
      $("histState").textContent = jobs.length + " 条作业记录";
      if (!jobs.length) {
        el.innerHTML = '<tbody><tr><td class="muted" style="padding:10px">还没有跑过算法</td></tr></tbody>';
        return;
      }
      el.innerHTML =
        "<thead><tr><th>作业</th><th>状态</th><th>耗时</th></tr></thead><tbody>" +
        jobs
          .map((j) => {
            const dur = j.finished_at ? Math.round((j.finished_at - j.created_at) * 100) / 100 : "—";
            const cls = j.status === "success" ? "ok" : j.status === "failed" ? "bad" : "";
            return (
              '<tr data-job="' +
              esc(j.id) +
              '"><td>' +
              esc(j.title || j.id) +
              '</td><td class="' +
              cls +
              '">' +
              esc(j.status) +
              '</td><td class="num">' +
              dur +
              "</td></tr>"
            );
          })
          .join("") +
        "</tbody>";
    } catch (err) {
      const el = $("histEl");
      if (el) el.textContent = "读取作业历史失败：" + err.message;
    }
  }

  /* ======================================================================== */
  /* 8. 编辑                                                                */
  /* ======================================================================== */

  async function toggleEdit() {
    const item = currentVector();
    if (!item) {
      toast("先选一个矢量图层");
      return;
    }
    const ref = refOf(item);
    if (P.editing === ref) {
      await commitEdit();
      return;
    }
    if (P.editing) {
      toast("请先保存或放弃当前图层的编辑");
      return;
    }
    try {
      const data = await call("/api/panels/edit/start", { method: "POST", body: { ref: ref } });
      P.editing = data.is_editing ? ref : null;
      tip("已进入编辑模式：" + layerTitle(ref));
      updateToolbarState();
      if ($("paneTable").classList.contains("on")) loadTable();
    } catch (err) {
      toast("进入编辑模式失败：" + err.message);
    }
  }

  async function commitEdit() {
    if (!P.editing) {
      toast("当前不在编辑状态");
      return;
    }
    const ref = P.editing;
    try {
      await call("/api/panels/edit/commit", { method: "POST", body: { ref: ref } });
      P.editing = null;
      tip("已保存到数据文件");
      updateToolbarState();
      await refreshMapLayer(ref);
      if ($("paneTable").classList.contains("on")) loadTable();
    } catch (err) {
      toast("保存失败：" + err.message);
    }
  }

  async function discardEdit() {
    if (!P.editing) {
      toast("当前不在编辑状态");
      return;
    }
    const ref = P.editing;
    try {
      await call("/api/panels/edit/rollback", { method: "POST", body: { ref: ref } });
      P.editing = null;
      tip("已放弃编辑");
      updateToolbarState();
      await refreshMapLayer(ref);
      if ($("paneTable").classList.contains("on")) loadTable();
    } catch (err) {
      toast("放弃编辑失败：" + err.message);
    }
  }

  async function deleteSelected() {
    if (!P.editing) {
      toast("先进入编辑模式再删除要素");
      return;
    }
    if (!P.sel.fids.size) {
      toast("没有选中的要素");
      return;
    }
    const fids = [...P.sel.fids];
    try {
      await call("/api/panels/edit/feature", {
        method: "DELETE",
        body: { ref: P.editing, fids: fids },
      });
      tip("已删除 " + fids.length + " 个要素（记得保存）");
      P.sel.fids.clear();
      highlightSelection();
      if ($("paneTable").classList.contains("on")) loadTable();
    } catch (err) {
      toast("删除失败：" + err.message);
    }
  }

  function dialogFieldCalculator() {
    const item = currentVector();
    if (!item) {
      toast("先选一个矢量图层");
      return;
    }
    const ref = refOf(item);
    // 主键（FID）列不能作为计算目标 —— 硬改会让 provider 报错、
    // 整个编辑会话在保存时被回滚（之前的改动全丢）。
    const fields = (P.table.fields || []).filter((f) => !f.fid);
    const options = fields
      .map((f) => '<option value="' + esc(f.name) + '">' + esc(f.alias || f.name) + "</option>")
      .join("");
    dialog(
      "字段计算器",
      '<div class="fld"><label>目标字段</label><select id="fcField" style="width:100%">' + options + "</select></div>" +
        '<div class="fld"><label>表达式</label><input type="text" id="fcExpr" placeholder="&quot;面积&quot; * 2" /></div>' +
        '<div class="note info">用 QGIS 表达式给字段批量赋值。字段名如果是中文，记得用双引号括起来。</div>',
      async (wrap) => {
        const field = wrap.querySelector("#fcField").value;
        const expr = wrap.querySelector("#fcExpr").value.trim();
        if (!field) throw new Error("请选择目标字段");
        if (!expr) throw new Error("表达式不能为空");
        const data = await call("/api/panels/edit/calculator", {
          method: "POST",
          body: { ref: ref, field: field, expression: expr },
        });
        tip("已更新 " + data.updated + " 个要素（记得保存）");
        if ($("paneTable").classList.contains("on")) loadTable();
      },
      "运行"
    );
  }

  /* 采集新要素：地图上点顶点，双击结束 */
  function startDraft() {
    const item = currentVector();
    if (!item) {
      toast("先选一个矢量图层");
      return;
    }
    if (!P.editing) {
      toast("先进入编辑模式再添加要素");
      return;
    }
    const ref = refOf(item);
    const gtype = (item.layer.geometry_type || "").toLowerCase();
    P.draft = { ref: ref, gtype: gtype, points: [], shape: null };
    setTool("pan");
    tip("采集：" + (gtype.indexOf("point") >= 0 ? "单击落点" : "单击加顶点，双击结束"));
    map.on("click", onDraftClick);
    map.on("dblclick", onDraftDblClick);
  }

  function onDraftClick(e) {
    if (!P.draft) return;
    P.draft.points.push([e.latlng.lng, e.latlng.lat]);
    drawDraft();
  }

  function onDraftDblClick(e) {
    if (!P.draft) return;
    L.DomEvent.stop(e);
    finishDraft();
  }

  function drawDraft() {
    if (!P.draft) return;
    if (P.draft.shape) {
      map.removeLayer(P.draft.shape);
      P.draft.shape = null;
    }
    const pts = P.draft.points.map((p) => [p[1], p[0]]);
    if (!pts.length) return;
    if (P.draft.gtype.indexOf("point") >= 0) {
      P.draft.shape = L.circleMarker(pts[0], { radius: 5, color: "#e6194b", fillOpacity: 0.5 }).addTo(map);
    } else {
      P.draft.shape = L.polyline(pts, { color: "#e6194b", weight: 2, dashArray: "4 3" }).addTo(map);
    }
  }

  async function finishDraft() {
    if (!P.draft) return;
    const d = P.draft;
    const min = d.gtype.indexOf("point") >= 0 ? 1 : d.gtype.indexOf("line") >= 0 ? 2 : 3;
    if (d.points.length < min) {
      toast("顶点不够");
      return;
    }
    stopDraft();
    let geometry;
    if (d.gtype.indexOf("point") >= 0) {
      geometry = { type: "Point", coordinates: d.points[0] };
    } else if (d.gtype.indexOf("line") >= 0) {
      geometry = { type: "LineString", coordinates: d.points };
    } else {
      const ring = d.points.slice();
      ring.push(d.points[0]);
      geometry = { type: "Polygon", coordinates: [ring] };
    }
    try {
      await call("/api/panels/edit/feature", {
        method: "POST",
        body: { ref: d.ref, geometry: geometry, attributes: {} },
      });
      tip("已添加要素（记得保存）");
      if ($("paneTable").classList.contains("on")) loadTable();
      await refreshMapLayer(d.ref);
    } catch (err) {
      toast("添加要素失败：" + err.message);
    }
  }

  function stopDraft() {
    if (P.draft && P.draft.shape) map.removeLayer(P.draft.shape);
    P.draft = null;
    map.off("click", onDraftClick);
    map.off("dblclick", onDraftDblClick);
  }

  /* ======================================================================== */
  /* 9. 地图提示                                                            */
  /* ======================================================================== */

  function toggleMapTips() {
    P.mapTips = !P.mapTips;
    if (P.mapTips) {
      tip("地图提示：鼠标悬停显示要素信息");
      map.on("mousemove", onMapMove);
    } else {
      map.off("mousemove", onMapMove);
      hideTip();
      tip("地图提示已关闭");
    }
    updateToolbarState();
  }

  let tipBox = null;
  function showTip(html, x, y) {
    if (!tipBox) {
      tipBox = document.createElement("div");
      tipBox.className = "maptip";
      document.body.appendChild(tipBox);
    }
    tipBox.innerHTML = html;
    tipBox.style.display = "block";
    const w = tipBox.offsetWidth;
    const h = tipBox.offsetHeight;
    tipBox.style.left = Math.min(x + 14, window.innerWidth - w - 8) + "px";
    tipBox.style.top = Math.min(y + 14, window.innerHeight - h - 8) + "px";
  }
  function hideTip() {
    if (tipBox) tipBox.style.display = "none";
  }

  function onMapMove(e) {
    if (P.tipTimer) return;
    P.tipTimer = setTimeout(async () => {
      P.tipTimer = null;
      const item = currentVector();
      if (!item) return;
      const l = item.layer;
      try {
        const data = await api(
          "/api/vectors/" +
            encodeURIComponent(l.dataset_id) +
            "/" +
            encodeURIComponent(layerIdOf(l)) +
            "/hit?lon=" +
            e.latlng.lng +
            "&lat=" +
            e.latlng.lat +
            "&limit=1"
        );
        if (!data || !data.count) {
          hideTip();
          return;
        }
        const f = data.features[0];
        const rows = Object.entries(f.properties || {})
          .slice(0, 8)
          .map(([k, v]) => "<div><b>" + esc(k) + "</b>: " + esc(v) + "</div>")
          .join("");
        showTip(rows || "（无属性）", e.originalEvent.clientX, e.originalEvent.clientY);
      } catch (err) {
        hideTip();
      }
    }, 260);
  }

  /* ======================================================================== */
  /* 10. 范围历史（上一个 / 下一个范围）                                     */
  /* ======================================================================== */

  function trackExtents() {
    map.on("moveend", () => {
      const b = map.getBounds();
      const cur = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
      const last = P.extents[P.extents.length - 1];
      if (last && last.every((v, i) => Math.abs(v - cur[i]) < 1e-9)) return;
      P.extents.push(cur);
      if (P.extents.length > 50) P.extents.shift();
      P.extentIndex = P.extents.length - 1;
    });
  }

  function gotoExtent(delta) {
    if (P.extents.length < 2) {
      toast("还没有可回退的范围");
      return;
    }
    let i = (P.extentIndex === undefined ? P.extents.length - 1 : P.extentIndex) + delta;
    if (i < 0 || i >= P.extents.length) {
      toast(delta < 0 ? "已经是最早的范围" : "已经是最新的范围");
      return;
    }
    P.extentIndex = i;
    const e = P.extents[i];
    map.fitBounds([[e[1], e[0]], [e[3], e[2]]], { padding: [26, 26] });
  }

  /* 比例尺：和状态栏那套算法一致（每像素米数 × 96/25.4，96dpi），
     这样「按比例缩放」缩过去之后，状态栏显示的比例尺就是你要的那个数。 */
  function metersPerPixel() {
    const size = map.getSize();
    const a = map.containerPointToLatLng([size.x / 2, size.y / 2]);
    const b = map.containerPointToLatLng([size.x / 2 + 100, size.y / 2]);
    return map.distance(a, b) / 100;
  }

  function currentScaleDenominator() {
    return metersPerPixel() * (96 / 25.4);
  }

  function zoomToScale(denominator) {
    const mpp = metersPerPixel();
    const targetMpp = denominator / (96 / 25.4);
    if (!mpp || !targetMpp) return;
    const z = map.getZoom() + Math.log2(mpp / targetMpp);
    map.setZoom(Math.max(0, Math.min(20, z)));
  }

  function dialogZoomToScale() {
    dialog(
      "按比例缩放",
      '<div class="fld"><label>比例尺（1:N）</label>' +
        '<input type="number" id="zscaleInput" min="1" step="1" value="10000" /></div>' +
        '<div class="hint">当前比例尺约 1:' +
        Math.round(currentScaleDenominator()).toLocaleString("en-US") +
        "。输入目标分母后，地图会以当前中心缩放到那个比例尺。</div>",
      async (wrap) => {
        const v = Number(wrap.querySelector("#zscaleInput").value);
        if (!v || v <= 0) throw new Error("请输入大于 0 的比例尺分母");
        zoomToScale(v);
        tip("已缩放到 1:" + v);
      },
      "缩放"
    );
  }

  /* ======================================================================== */
  /* 11. 工具栏动作                                                        */
  /* ======================================================================== */

  function updateToolbarState() {
    const bar = $("toolbars");
    if (!bar) return;
    const editing = !!P.editing;
    const set = (act, on) => {
      const b = bar.querySelector('button[data-act="' + act + '"]');
      if (b) b.classList.toggle("on", !!on);
    };
    set("toggleEdit", editing);
    set("saveEdit", editing);
    set("discardEdit", editing);
    set("addFeature", editing);
    set("deleteSelected", editing);
    set("toggleMapTips", P.mapTips);
  }

  async function refreshMapLayer(ref) {
    const item = (S.active || []).find((it) => it.layer && it.layer.ref === ref);
    if (!item) return;
    try {
      await drawLayer(item);
    } catch (err) {
      /* drawLayer 内部已经提示过了 */
    }
  }

  function onToolbarAction(act, btn) {
    switch (act) {
      case "addLayer":
        openAddDialog();
        break;
      case "openTable":
        switchTab("paneTable");
        refreshLayerOptions();
        loadTable();
        break;
      case "openStats":
        switchTab("paneStats");
        loadStats();
        break;
      case "zoomLayer":
        zoomToLayer(S.selected);
        break;
      case "zoomSelected":
        zoomToSelection();
        break;
      case "selectAll":
        selectAllFeatures();
        break;
      case "selectNone":
        selectNoneFeatures();
        break;
      case "selectInvert":
        selectInvertFeatures();
        break;
      case "selectExpr":
        dialogSelectByExpression();
        break;
      case "selectSpace":
        dialogSelectBySpace();
        break;
      case "toggleEdit":
        toggleEdit();
        break;
      case "saveEdit":
        commitEdit();
        break;
      case "discardEdit":
        discardEdit();
        break;
      case "addFeature":
        startDraft();
        break;
      case "deleteSelected":
        deleteSelected();
        break;
      case "fieldCalc":
        dialogFieldCalculator();
        break;
      case "toolPan":
        setTool("pan");
        break;
      case "toolZoomIn":
        setTool("zoomIn");
        break;
      case "toolZoomOut":
        setTool("zoomOut");
        break;
      case "zoomScale":
        dialogZoomToScale();
        break;
      case "zoomAll":
        zoomAll();
        break;
      case "prevExtent":
        gotoExtent(-1);
        break;
      case "nextExtent":
        gotoExtent(1);
        break;
      case "toggleMapTips":
        toggleMapTips();
        break;
      case "refresh":
        if (typeof refreshMap === "function") refreshMap();
        else (S.active || []).forEach((it) => drawLayer(it));
        break;
      default:
        break;
    }
  }

  /* ======================================================================== */
  /* 12. 面板事件绑定 + 启动                                                */
  /* ======================================================================== */

  function bindPanelEvents() {
    const selAll = $("selAll");
    if (selAll) selAll.onclick = selectAllFeatures;
    const selNone = $("selNone");
    if (selNone) selNone.onclick = selectNoneFeatures;
    const selInv = $("selInv");
    if (selInv) selInv.onclick = selectInvertFeatures;
    const selExpr = $("selExpr");
    if (selExpr) selExpr.onclick = dialogSelectByExpression;
    const selSpace = $("selSpace");
    if (selSpace) selSpace.onclick = dialogSelectBySpace;

    const bmAdd = $("bmAdd");
    if (bmAdd) bmAdd.onclick = addBookmark;
    const bmRefresh = $("bmRefresh");
    if (bmRefresh) bmRefresh.onclick = loadBookmarks;

    const logRefresh = $("logRefresh");
    if (logRefresh) logRefresh.onclick = loadLogs;
    const logClear = $("logClear");
    if (logClear)
      logClear.onclick = async () => {
        try {
          await call("/api/panels/logs", { method: "DELETE" });
          loadLogs();
        } catch (err) {
          toast("清空日志失败：" + err.message);
        }
      };
    const logLevel = $("logLevel");
    if (logLevel) logLevel.onchange = loadLogs;

    const histRefresh = $("histRefresh");
    if (histRefresh) histRefresh.onclick = loadHistory;

    const statRefresh = $("statRefresh");
    if (statRefresh) statRefresh.onclick = loadStats;
    const infoRefresh = $("infoRefresh");
    if (infoRefresh) infoRefresh.onclick = loadInfo;
    const statField = $("statField");
    if (statField)
      statField.onchange = () => {
        P.stats.field = statField.value;
        loadStats();
      };

    // 书签表：缩放 / 删除
    const bmEl = $("bmEl");
    if (bmEl)
      bmEl.addEventListener("click", async (e) => {
        const a = e.target.closest("a[data-act]");
        const tr = e.target.closest("tr[data-name]");
        if (!a || !tr) return;
        e.preventDefault();
        const name = tr.dataset.name;
        const bm = (P.bookmark.items || []).find((b) => b.name === name);
        if (a.dataset.act === "zoom") {
          if (!bm) return;
          map.fitBounds([[bm.extent[1], bm.extent[0]], [bm.extent[3], bm.extent[2]]], { padding: [26, 26] });
        } else {
          try {
            await call("/api/panels/bookmarks?name=" + encodeURIComponent(name), { method: "DELETE" });
            await loadBookmarks();
            tip("书签已删除");
          } catch (err) {
            toast("删除书签失败：" + err.message);
          }
        }
      });

    // 选择面板：点条目缩放过去
    const selEl = $("selEl");
    if (selEl)
      selEl.addEventListener("click", (e) => {
        const tr = e.target.closest("tr[data-fid]");
        if (!tr) return;
        const fid = Number(tr.dataset.fid);
        P.sel.fids = new Set([fid]);
        highlightSelection();
        renderSelectionPanel();
        renderTable();
      });
  }

  function onPanelShown(id) {
    switch (id) {
      case "Info":
        loadInfo();
        break;
      case "Table":
        refreshLayerOptions();
        loadTable();
        break;
      case "Stats":
        loadStats();
        break;
      case "Sel":
        renderSelectionPanel();
        break;
      case "Bookmark":
        loadBookmarks();
        break;
      case "Log":
        loadLogs();
        if (!P.logs.timer) P.logs.timer = setInterval(() => {
          if ($("paneLog") && $("paneLog").classList.contains("on")) loadLogs();
        }, 3000);
        break;
      case "Hist":
        loadHistory();
        if (!P.hist.timer) P.hist.timer = setInterval(() => {
          if ($("paneHist") && $("paneHist").classList.contains("on")) loadHistory();
        }, 4000);
        break;
      default:
        break;
    }
  }

  function injectStyle() {
    const style = document.createElement("style");
    style.id = "panels-style";
    style.textContent = [
      /* 工具栏：QGIS Desktop 那种「一排 = 一组按钮 + 分隔线」，一共两排 */
      ".toolbars{display:flex;flex-direction:column;flex:0 0 auto;",
      "background:var(--panel);border-bottom:1px solid var(--rule)}",
      ".tb-row{display:flex;align-items:center;flex:0 0 auto;height:29px;padding:0 6px;overflow-x:auto}",
      ".tb-row::-webkit-scrollbar{height:0}",
      ".tb-row + .tb-row{border-top:1px solid var(--rule)}",
      ".tb-group{display:flex;align-items:center;gap:1px;padding:0 5px;flex:0 0 auto}",
      ".tb-sep{width:1px;height:16px;background:var(--rule);flex:0 0 auto;margin:0 4px}",
      ".toolbars button{display:flex;align-items:center;justify-content:center;width:24px;height:23px;",
      "border:1px solid transparent;border-radius:2px;background:none;color:var(--ink-2);cursor:pointer;padding:0}",
      ".toolbars button:hover{background:var(--panel-2);border-color:var(--rule);color:var(--ink)}",
      ".toolbars button.on{background:var(--moss-soft);border-color:var(--moss);color:var(--moss-2)}",
      ".toolbars button:disabled{opacity:.38;cursor:default}",
      /* 右栏标签多了一个，允许多行 */
      "aside.right .tabs{flex-wrap:wrap}",
      "aside.right .tab{flex:0 0 auto;padding:6px 9px;font-size:12px}",
      /* 表格 */
      ".tbl-wrap{overflow:auto;max-height:52vh}",
      "table.tbl{border-collapse:collapse;width:100%;font-size:11.5px}",
      "table.tbl th,table.tbl td{border-bottom:1px solid var(--rule);border-right:1px solid var(--rule);",
      "padding:3px 6px;white-space:nowrap;text-align:left;max-width:210px;overflow:hidden;text-overflow:ellipsis}",
      "table.tbl th{position:sticky;top:0;background:var(--panel-2);color:var(--ink-2);font-weight:600;z-index:1}",
      "table.tbl th.sortable{cursor:pointer;user-select:none}",
      "table.tbl th.sortable:hover{color:var(--ink)}",
      "table.tbl th.sortable.on{color:var(--moss)}",
      "table.tbl td.num{text-align:right;font-family:var(--mono)}",
      "table.tbl tr:hover td{background:var(--panel-2)}",
      "table.tbl tr.sel td{background:var(--moss-soft)}",
      "table.tbl tr.stat-group td{background:var(--panel-2);border-top:1px solid var(--rule-2)}",
      "table.tbl td.ok{color:var(--moss-2)}",
      "table.tbl td.bad{color:var(--rust)}",
      ".logbox{margin:0;padding:8px 10px;font-family:var(--mono);font-size:11px;line-height:1.6;",
      "white-space:pre-wrap;word-break:break-all;max-height:52vh;overflow:auto;color:var(--ink-2)}",
      /* 地图提示 */
      ".maptip{position:fixed;z-index:9700;display:none;max-width:260px;pointer-events:none;",
      "background:var(--panel);border:1px solid var(--rule-2);border-radius:3px;padding:6px 8px;",
      "font-size:11.5px;line-height:1.55;box-shadow:0 6px 18px rgba(30,36,32,.16)}",
      ".maptip b{color:var(--ink)}",
    ].join("");
    document.head.appendChild(style);
  }

  function boot() {
    injectStyle();
    buildToolbars();
    /* 浮动条换成 QGIS 官方图标：index.html 的 boot 也会往那三个按钮里填 svg，
       填充时机不固定，所以立即换一次 + 挂 MutationObserver 兜底。 */
    applyQgisFloatIcons();
    watchFloatIcons();
    buildPanels();
    bindTableEvents();
    bindPanelEvents();
    trackExtents();
    updateToolbarState();
    renderSelectionPanel();

    // 自检（tests/check_webui_panels.py）要能读面板内部状态，
    // 顺带也方便在浏览器控制台里排查，所以挂一个句柄。
    window.GISDS_PANELS = P;

    // 图层树里换图层时，属性表跟着换
    if (typeof selectLayer === "function") {
      const original = selectLayer;
      selectLayer = function (item) {
        const out = original.apply(this, arguments);
        try {
          if (item && item.type === "vector" && item.layer && item.layer.ref) {
            P.table.ref = item.layer.ref;
            P.stats.ref = item.layer.ref;
            P.sel.ref = item.layer.ref;
            P.info.ref = item.layer.ref;
            P.sel.fids.clear();
            refreshLayerOptions();
            if ($("paneTable") && $("paneTable").classList.contains("on")) loadTable();
            if ($("paneStats") && $("paneStats").classList.contains("on")) loadStats();
            if ($("paneInfo") && $("paneInfo").classList.contains("on")) loadInfo();
            renderSelectionPanel();
          }
        } catch (err) {
          /* 面板刷新失败不该影响图层切换 */
        }
        return out;
      };
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
