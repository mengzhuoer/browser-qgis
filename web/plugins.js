/* =============================================================================
   服务端插件 · 网页界面

   后端（gisds/core/plugins.py + gisds/api/routes_plugins.py）已经实现了插件
   系统：插件能往右栏加面板、往菜单栏加菜单项、注册自己的 REST 接口、注册
   Processing 算法。这个文件负责把插件的「声明」接到网页上：

     * 右栏第一个标签页「插件」—— 插件清单 + 管理操作（装 / 启停 / 重载 / 卸载）
     * 插件贡献的面板   —— 按 panels 声明追加标签页，打开时拉数据并渲染
     * 插件贡献的菜单项 —— 按 menus 声明挂到对应菜单栏

   本文件直接复用 index.html 的全局（api / esc / toast / switchTab / MENUS …）：
   同一批 classic script 共享同一个全局词法环境，不需要额外桥接。
   ============================================================================= */
(function () {
  "use strict";

  /* ----------------------------------------------------------------- 状态 */
  const ST = {
    plugins: [],   // 插件清单
    panels: [],    // 插件贡献的面板声明
    menus: [],     // 插件贡献的菜单项声明
    canManage: false,
    loading: false,
    pending: false,  // 正在拉的时候又有人点刷新，完了补一次
    /* 面板节点：键 = "插件标识/面板标识" -> {tab, pane, spec} */
    nodes: new Map(),
    /* 菜单包装：菜单名 -> 包装后的函数（包装一次，条目内容每次调用时现取） */
    menuWrapped: new Map(),
    /* 我自己插到顶栏的菜单按钮：菜单名 -> 元素 */
    menuBtns: new Map(),
  };

  /* 顶栏新菜单按钮的中文名。已有的 file/edit/view/layer/help 不需要按钮。 */
  const MENU_LABEL = {
    tools: "工具",
    plugins: "插件",
  };

  /* ----------------------------------------------------------------- 小工具 */

  /** 取错误信息，统一兜底成一句人话（界面上不能出现 undefined）。 */
  function errMsg(e) {
    if (!e) return "未知错误";
    if (typeof e === "string") return e || "未知错误";
    return e.message ? String(e.message) : "未知错误";
  }

  /** 读全局 openMenu。index.html 里它是全局 let，理论上一定在；这里兜住异常。 */
  function getOpenMenu() {
    try { return openMenu; } catch (e) { return null; }
  }
  function setOpenMenu(v) {
    try { openMenu = v; } catch (e) { /* 忽略 */ }
  }

  /* ----------------------------------------------------------------- 样式 */
  function injectStyle() {
    if (document.getElementById("plugins-style")) return;
    const style = document.createElement("style");
    style.id = "plugins-style";
    style.textContent = [
      /* 插件清单顶部的一排按钮 */
      ".plug-toolbar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:8px 12px;",
      "background:var(--panel-2);border-bottom:1px solid var(--rule);flex:0 0 auto}",
      /* 插件清单 */
      ".plug-card{padding:10px 12px;border-bottom:1px solid var(--rule)}",
      ".plug-card-top{display:flex;align-items:center;gap:7px;flex-wrap:wrap}",
      ".plug-name{font-weight:600;font-size:12.5px;color:var(--ink)}",
      ".plug-ver{font-family:var(--mono);font-size:11px;color:var(--ink-3)}",
      ".plug-badge{font-size:10.5px;line-height:1.5;padding:0 6px;border-radius:8px;",
      "border:1px solid var(--rule-2);background:var(--panel);color:var(--ink-3)}",
      ".plug-badge.ok{color:var(--moss-2);border-color:var(--moss);background:var(--moss-soft)}",
      ".plug-badge.bad{color:var(--rust);border-color:var(--rust);background:var(--rust-soft)}",
      ".plug-meta{font-size:11.5px;color:var(--ink-3);margin-top:4px}",
      ".plug-desc{font-size:12px;color:var(--ink-2);margin-top:4px;line-height:1.6;word-break:break-word}",
      ".plug-err{font-size:11.5px;color:var(--rust);margin-top:5px;line-height:1.6;word-break:break-word}",
      ".plug-actions{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}",
      ".plug-empty{padding:14px 12px;font-size:12px;color:var(--ink-3);line-height:1.75}",
      ".plug-load{padding:12px;font-size:11.5px;color:var(--ink-3)}",
      /* 插件面板里的错误：整块红字，不影响其它面板 */
      ".plug-fail{margin:10px 12px;padding:8px 10px;font-size:11.5px;line-height:1.6;color:var(--rust);",
      "background:var(--rust-soft);border:1px solid var(--rule-2);border-radius:3px;word-break:break-word}",
      /* 面板返回的 JSON 摘要 */
      ".plug-json{padding:10px 12px}",
      ".plug-json pre{margin:0;background:var(--panel-2);border:1px solid var(--rule);border-radius:3px;",
      "padding:8px;font-family:var(--mono);font-size:11px;color:var(--ink-2);",
      "white-space:pre-wrap;word-break:break-word;max-height:56vh;overflow:auto}",
      /* 插件面板里的表格：与 panels.js 的 table.tbl 同一套外观，
         这里用更具体的选择器，保证不依赖 panels.js 也能看。 */
      ".plug-tbl-wrap{overflow:auto;max-height:52vh}",
      "table.tbl.plug-tbl{border-collapse:collapse;width:100%;font-size:11.5px}",
      "table.tbl.plug-tbl th,table.tbl.plug-tbl td{border-bottom:1px solid var(--rule);",
      "border-right:1px solid var(--rule);padding:3px 6px;white-space:nowrap;text-align:left;",
      "max-width:210px;overflow:hidden;text-overflow:ellipsis}",
      "table.tbl.plug-tbl th{position:sticky;top:0;background:var(--panel-2);color:var(--ink-2);",
      "font-weight:600;z-index:1}",
      "table.tbl.plug-tbl tr:hover td{background:var(--panel-2)}",
    ].join("");
    document.head.appendChild(style);
  }

  /* ------------------------------------------------------- 右栏「插件」页 */

  /** 在右栏最前面插一个标题为「插件」的标签页。 */
  function buildPluginTab() {
    // 要选右侧那一栏，不能用 ".right" —— 菜单栏里也有一个 <div class="right">。
    const tabs = document.querySelector("aside.right .tabs");
    const aside = document.querySelector("aside.right");
    if (!tabs || !aside) return false;
    if (document.getElementById("panePlugins")) return true;

    const tab = document.createElement("div");
    tab.className = "tab";
    tab.dataset.pane = "panePlugins";
    tab.textContent = "插件";
    tab.title = "已安装的插件";
    tabs.insertBefore(tab, tabs.firstChild);

    const pane = document.createElement("div");
    pane.className = "tabpane";
    pane.id = "panePlugins";
    pane.innerHTML =
      '<div class="plug-toolbar" id="plugToolbar"></div>' +
      '<div class="plug-list" id="plugList"></div>';

    // 面板本体放在右栏第一个 tabpane 前面（tabpane 靠 .on 控制显隐，位置不影响显示）
    const firstPane = aside.querySelector(".tabpane");
    if (firstPane) aside.insertBefore(pane, firstPane);
    else aside.appendChild(pane);

    tab.onclick = () => {
      if (typeof switchTab === "function") switchTab(pane.id);
    };
    return true;
  }

  /* ------------------------------------------------------- 插件贡献的面板 */

  /** 面板的 DOM 标识：约定是 "pane" + 面板标识，重名时再加工。 */
  function makePaneId(spec) {
    const raw = String(spec.id || "panel").replace(/[^A-Za-z0-9_-]/g, "") || "panel";
    const want = "pane" + raw;
    if (!document.getElementById(want)) return want;
    const plug = String(spec.plugin || "plug").replace(/[^A-Za-z0-9_-]/g, "") || "plug";
    const alt = "pane" + plug + "_" + raw;
    if (!document.getElementById(alt)) return alt;
    let i = 2;
    while (document.getElementById(alt + "_" + i)) i++;
    return alt + "_" + i;
  }

  /** 按 panels 声明同步右栏标签页：新增、去掉失效的。 */
  function mountPluginPanels() {
    const tabs = document.querySelector("aside.right .tabs");
    const aside = document.querySelector("aside.right");
    if (!tabs || !aside) return;

    const wanted = new Set();
    ST.panels.forEach((spec) => {
      wanted.add((spec.plugin || "") + "/" + (spec.id || ""));
    });

    // 插件被卸载/停用后，它的面板要收掉
    ST.nodes.forEach((node, key) => {
      if (wanted.has(key)) return;
      if (node.tab && node.tab.parentNode) node.tab.parentNode.removeChild(node.tab);
      if (node.pane && node.pane.parentNode) node.pane.parentNode.removeChild(node.pane);
      ST.nodes.delete(key);
    });

    ST.panels.forEach((spec) => {
      const key = (spec.plugin || "") + "/" + (spec.id || "");
      const title = String(spec.title || spec.id || "插件面板");
      let node = ST.nodes.get(key);
      if (!node) {
        const tab = document.createElement("div");
        tab.className = "tab";
        const pane = document.createElement("div");
        pane.className = "tabpane";
        const pid = makePaneId(spec);
        tab.dataset.pane = pid;
        pane.id = pid;
        tabs.appendChild(tab);
        aside.appendChild(pane);
        tab.onclick = () => {
          if (typeof switchTab === "function") switchTab(pane.id);
          // 用 node.spec（每次刷新都会更新），不要用建面板时的那份旧声明
          loadPanel(node.spec, pane);
        };
        node = { tab: tab, pane: pane, spec: spec };
        ST.nodes.set(key, node);
      }
      node.spec = spec;
      node.tab.textContent = title;
      node.tab.title = "插件面板：" + title;
    });
  }

  /** 打开面板时拉一次数据。 */
  async function loadPanel(spec, pane) {
    const endpoint = String(spec.endpoint || "");
    if (!endpoint) {
      pane.innerHTML = '<div class="plug-fail">这个面板没有配置接口地址。</div>';
      return;
    }
    pane.innerHTML = '<div class="plug-load">正在加载…</div>';
    try {
      const data = await api(endpoint);
      renderPanelData(pane, data);
    } catch (e) {
      // 面板拉不到数据只影响这一个面板，不能把整个页面带崩
      pane.innerHTML = '<div class="plug-fail">加载失败：' + esc(errMsg(e)) + "</div>";
    }
  }

  /** 按返回内容选一种画法：HTML / 表格 / JSON 摘要。 */
  function renderPanelData(pane, data) {
    if (data && typeof data === "object" && !Array.isArray(data) &&
        typeof data.html === "string" && data.html.trim() !== "") {
      /* 这里的 HTML 是**插件自己生成的**：插件是管理员装到服务端上的一段代码，
         它本身就在服务端拥有完整权限，所以这段内容按「可信」处理，不做转义
         （转义会把插件自己的标签打乱，面板就没法用了）。 */
      pane.innerHTML = data.html;
      return;
    }
    if (data && typeof data === "object" &&
        Array.isArray(data.columns) && Array.isArray(data.rows)) {
      pane.innerHTML = buildTable(data.columns, data.rows);
      return;
    }
    pane.innerHTML = '<div class="plug-json"><pre>' + esc(summarize(data)) + "</pre></div>";
  }

  /** 把 columns + rows 画成一张表。 */
  function buildTable(columns, rows) {
    const cols = columns.map((c, i) => {
      if (c && typeof c === "object") {
        return String(c.name || c.title || c.label || "列" + (i + 1));
      }
      return String(c || "列" + (i + 1));
    });
    let html = '<div class="plug-tbl-wrap"><table class="tbl plug-tbl"><thead><tr>';
    cols.forEach((c) => { html += "<th>" + esc(c) + "</th>"; });
    html += "</tr></thead><tbody>";
    rows.forEach((row) => {
      const cells = Array.isArray(row) ? row : [row];
      html += "<tr>";
      for (let i = 0; i < cols.length; i++) {
        html += "<td>" + esc(cellText(cells[i])) + "</td>";
      }
      html += "</tr>";
    });
    html += "</tbody></table></div>";
    if (!rows.length) html += '<div class="empty">接口没有返回数据行。</div>';
    return html;
  }

  /** 单元格取值：空值给空串，对象给 JSON。 */
  function cellText(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "object") {
      try { return JSON.stringify(v); } catch (e) { return String(v); }
    }
    return String(v);
  }

  /** 接口既没给 html 也没给表格时，给一段 JSON 摘要。 */
  function summarize(data) {
    if (data === null || data === undefined) return "接口没有返回内容。";
    if (typeof data === "string") {
      return data.length > 4000 ? data.slice(0, 4000) + "\n…（内容过长，已截断）" : data;
    }
    if (typeof Response !== "undefined" && data instanceof Response) {
      return "接口返回的不是 JSON（HTTP " + data.status +
        (data.statusText ? " " + data.statusText : "") + "）。";
    }
    if (typeof data !== "object") return String(data);
    try {
      const s = JSON.stringify(data, null, 2);
      return s === undefined ? "接口返回了空内容。" : s;
    } catch (e) {
      return "接口返回的内容无法序列化成 JSON。";
    }
  }

  /* ------------------------------------------------------- 插件贡献的菜单 */

  /** 把菜单项挂到 MENUS 上。包装只做一次，条目内容每次展开时现取。 */
  function mountMenus() {
    if (typeof MENUS === "undefined" || !MENUS) return;
    const names = new Set();
    ST.menus.forEach((m) => {
      const n = String(m.menu || "").trim();
      if (n) names.add(n);
    });
    names.forEach((n) => ensureMenu(n));
    // 插件全被卸掉后，我自己加的菜单按钮要收起来，免得点开是空的
    ST.menuBtns.forEach((btn, name) => {
      btn.style.display = names.has(name) ? "" : "none";
    });
  }

  function ensureMenu(name) {
    if (ST.menuWrapped.has(name)) return;
    const base = typeof MENUS[name] === "function" ? MENUS[name] : null;
    const wrapped = function () {
      let baseItems = [];
      if (base) {
        try { baseItems = base() || []; } catch (e) { baseItems = []; }
      }
      if (!Array.isArray(baseItems)) baseItems = [];
      const extra = ST.menus
        .filter((m) => String(m.menu || "").trim() === name)
        .map((m) => ({
          label: String(m.label || "未命名菜单项"),
          run: () => runMenuItem(m),
        }));
      if (!extra.length) return baseItems;
      return baseItems.concat([{ sep: true }, { head: "插件" }].concat(extra));
    };
    ST.menuWrapped.set(name, wrapped);
    MENUS[name] = wrapped;
    // 目标菜单本来就有（比如 file/edit/view/layer/help），不用加顶栏按钮
    if (!base) ensureMenuButton(name);
  }

  /** 菜单本来不存在时，往顶栏插一个可见的菜单按钮。 */
  function ensureMenuButton(name) {
    if (ST.menuBtns.has(name)) return;
    const bar = document.querySelector(".menubar");
    if (!bar) return;
    const label = MENU_LABEL[name] || name;
    const btn = document.createElement("div");
    btn.className = "menu";
    btn.dataset.menu = name;
    btn.textContent = label;
    btn.title = label + " 菜单";
    // 放在顶栏右侧那一组之前，跟其它菜单排在一起
    const right = bar.querySelector(".right");
    if (right) bar.insertBefore(btn, right);
    else bar.appendChild(btn);
    // 开合逻辑跟 index.html 里那批菜单按钮完全一致
    btn.onclick = (e) => {
      e.stopPropagation();
      if (getOpenMenu() === name) {
        if (typeof closeDropdown === "function") closeDropdown();
        return;
      }
      document.querySelectorAll(".menubar .menu.on").forEach((x) => x.classList.remove("on"));
      btn.classList.add("on");
      setOpenMenu(name);
      if (typeof renderDropdown === "function") renderDropdown(name, btn);
    };
    ST.menuBtns.set(name, btn);
  }

  /** 点插件菜单项：调它自己的接口，调完提示一句。 */
  async function runMenuItem(m) {
    const label = String(m.label || "插件菜单项");
    const endpoint = String(m.endpoint || "");
    if (!endpoint) {
      toast("「" + label + "」没有配置接口，只作提示。");
      return;
    }
    const method = String(m.method || "POST").toUpperCase();
    const params = m.params && typeof m.params === "object" ? m.params : {};
    const opts = { method: method };
    if (method === "GET" || method === "HEAD") {
      // GET 没有请求体，params 拼到查询串上
      const qs = Object.keys(params)
        .filter((k) => params[k] !== null && params[k] !== undefined)
        .map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(String(params[k])))
        .join("&");
      if (qs) opts.url = endpoint + (endpoint.indexOf("?") >= 0 ? "&" : "?") + qs;
    } else {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(params);
    }
    try {
      await api(opts.url || endpoint, opts);
      toast("「" + label + "」执行成功");
    } catch (e) {
      toast("「" + label + "」执行失败：" + errMsg(e));
    }
  }

  /* ------------------------------------------------------------- 插件清单 */

  /** 顶部一排按钮：刷新人人都有，安装只有管理员看得到。 */
  function renderToolbar() {
    const bar = $("plugToolbar");
    if (!bar) return;
    bar.innerHTML = "";

    const refreshBtn = document.createElement("button");
    refreshBtn.className = "btn sm";
    refreshBtn.textContent = "刷新";
    refreshBtn.title = "重新拉取插件清单";
    refreshBtn.onclick = () => refresh();
    bar.appendChild(refreshBtn);

    if (!ST.canManage) return; // 非管理员：这一排里不放任何管理按钮

    const install = document.createElement("button");
    install.className = "btn sm primary";
    install.textContent = "安装插件（上传 zip）";
    install.title = "上传一个 zip 插件包安装";
    install.onclick = () => {
      const f = $("plugFile");
      if (f) f.click();
    };
    bar.appendChild(install);

    const file = document.createElement("input");
    file.type = "file";
    file.id = "plugFile";
    file.accept = ".zip";
    file.style.display = "none";
    file.onchange = (e) => {
      const picked = e.target.files && e.target.files[0];
      e.target.value = ""; // 允许连续选同一个文件
      if (picked) installPlugin(picked);
    };
    bar.appendChild(file);
  }

  /** 画一个插件条目。 */
  function pluginCard(p) {
    const slug = String(p.slug || "");
    const name = String(p.name || slug || "未命名插件");
    const ver = p.version ? "v" + String(p.version) : "版本未知";
    const author = p.author ? String(p.author) : "未知";
    const desc = p.description ? String(p.description) : "（插件没有写描述）";
    const enabled = p.enabled === true;
    const loaded = p.loaded === true;
    const err = p.error ? String(p.error) : "";
    const acts = Array.isArray(p.actions) ? p.actions : [];
    const nPanels = Array.isArray(p.panels) ? p.panels.length : 0;
    const nMenus = Array.isArray(p.menus) ? p.menus.length : 0;

    let h = '<div class="plug-card">';
    h += '<div class="plug-card-top">';
    h += '<span class="plug-name">' + esc(name) + "</span>";
    h += '<span class="plug-ver">' + esc(ver) + "</span>";
    h += '<span class="plug-badge ' + (enabled ? "ok" : "off") + '">' +
      (enabled ? "已启用" : "已停用") + "</span>";
    h += '<span class="plug-badge ' + (loaded ? "ok" : (err ? "bad" : "off")) + '">' +
      (loaded ? "已加载" : (err ? "加载失败" : "未加载")) + "</span>";
    h += "</div>";
    h += '<div class="plug-meta">作者：' + esc(author) + " · 贡献 " +
      nPanels + " 个面板 / " + nMenus + " 个菜单项</div>";
    h += '<div class="plug-desc">' + esc(desc) + "</div>";
    if (err) h += '<div class="plug-err">' + esc(err) + "</div>";

    if (ST.canManage) {
      h += '<div class="plug-actions">';
      h += '<button class="btn sm" data-plug-act="enable" data-plug-slug="' + esc(slug) + '"' +
        (enabled ? " disabled" : "") + ">启用</button>";
      h += '<button class="btn sm" data-plug-act="disable" data-plug-slug="' + esc(slug) + '"' +
        (enabled ? "" : " disabled") + ">停用</button>";
      h += '<button class="btn sm" data-plug-act="reload" data-plug-slug="' + esc(slug) + '">重载</button>';
      h += '<button class="btn sm" data-plug-act="uninstall" data-plug-slug="' + esc(slug) + '">卸载</button>';
      acts.forEach((a) => {
        const an = String(a || "");
        if (!an) return;
        h += '<button class="btn sm" data-plug-act="action" data-plug-slug="' + esc(slug) +
          '" data-plug-action="' + esc(an) + '">' + esc(an) + "</button>";
      });
      h += "</div>";
    }
    h += "</div>";
    return h;
  }

  /** 画插件清单。 */
  function renderPluginList() {
    const box = $("plugList");
    if (!box) return;
    renderToolbar();
    if (!ST.plugins.length) {
      box.innerHTML = '<div class="plug-empty">还没有安装插件。' +
        (ST.canManage ? "点上面的「安装插件（上传 zip）」上传一个插件包。"
                      : "需要的话请联系管理员安装。") + "</div>";
      return;
    }
    box.innerHTML = ST.plugins.map(pluginCard).join("");
    box.querySelectorAll("[data-plug-act]").forEach((btn) => {
      btn.onclick = () => handlePluginAction(
        btn.getAttribute("data-plug-act"),
        btn.getAttribute("data-plug-slug"),
        btn.getAttribute("data-plug-action"));
    });
  }

  /** 清单拉不到时的兜底。 */
  function showPluginError(e) {
    const box = $("plugList");
    if (!box) return;
    box.innerHTML = '<div class="plug-fail">插件清单加载失败：' + esc(errMsg(e)) +
      "<br>请确认服务端的插件接口可用，然后点上面的「刷新」重试。</div>";
  }

  /* ------------------------------------------------------------- 管理操作 */

  /** 启用 / 停用 / 重载 / 卸载 / 调用插件动作。 */
  async function handlePluginAction(act, slug, actionName) {
    if (!ST.canManage) {
      toast("只有管理员能管理插件。");
      return;
    }
    if (!slug) {
      toast("插件标识缺失，操作未执行。");
      return;
    }
    if (act === "action") {
      if (!actionName) {
        toast("动作名缺失，操作未执行。");
        return;
      }
      try {
        await api("/api/plugins/" + encodeURIComponent(slug) + "/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: actionName, params: {} }),
        });
        toast("动作「" + actionName + "」执行成功");
      } catch (e) {
        toast("动作执行失败：" + errMsg(e));
      }
      return;
    }
    if (act === "uninstall") {
      const p = ST.plugins.filter((x) => String(x.slug) === slug)[0];
      const nm = (p && p.name) || slug;
      if (!confirm("确定要卸载插件「" + nm + "」吗？\n卸载会删掉它的插件目录，无法撤销。")) return;
    }
    try {
      await api("/api/plugins/" + encodeURIComponent(slug) + "/" + act, { method: "POST" });
      toast("「" + act + "」操作成功");
      await refresh();
    } catch (e) {
      toast("操作失败：" + errMsg(e));
    }
  }

  /** 上传 zip 安装插件。api() 只能方便地发 JSON，上传得自己写 fetch。 */
  async function installPlugin(file) {
    if (!ST.canManage) {
      toast("只有管理员能安装插件。");
      return;
    }
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file, file.name);
    try {
      const res = await fetch(API + "/api/plugins/install", {
        method: "POST",
        headers: { Authorization: "Bearer " + sessionToken() },
        body: fd,
      });
      if (res.status === 401) {
        if (typeof goLogin === "function") goLogin();
        throw new Error("需要登录");
      }
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!res.ok) {
        let m = (data && (data.detail || data.message)) || (res.status + " " + res.statusText);
        if (typeof m !== "string") {
          try { m = JSON.stringify(m); } catch (e) { m = "安装失败"; }
        }
        throw new Error(m || "安装失败");
      }
      const nm = (data && data.plugin && data.plugin.name) ||
        String(file.name || "").replace(/\.zip$/i, "") || "新插件";
      toast("插件「" + nm + "」安装成功");
      await refresh();
    } catch (e) {
      toast("安装失败：" + errMsg(e));
    }
  }

  /* ----------------------------------------------------------------- 刷新 */

  /** 拉一次插件清单，然后重建清单、面板和菜单。 */
  async function refresh() {
    // 已经在拉了：记一笔，等这次拉完再补一次，免得漏掉刚刚的装/卸
    if (ST.loading) { ST.pending = true; return; }
    ST.loading = true;
    renderToolbar();
    const box = $("plugList");
    if (box) box.innerHTML = '<div class="plug-load">正在加载插件清单…</div>';
    try {
      const data = await api("/api/plugins");
      ST.plugins = Array.isArray(data.plugins) ? data.plugins : [];
      ST.panels = Array.isArray(data.panels) ? data.panels : [];
      ST.menus = Array.isArray(data.menus) ? data.menus : [];
      ST.canManage = data.can_manage === true;
      renderPluginList();
      mountPluginPanels();
      mountMenus();
    } catch (e) {
      showPluginError(e);
    } finally {
      ST.loading = false;
      if (ST.pending) {
        ST.pending = false;
        refresh();
      }
    }
  }

  /* ----------------------------------------------------------------- 启动 */

  function boot() {
    injectStyle();
    if (!buildPluginTab()) return;
    renderToolbar();
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
