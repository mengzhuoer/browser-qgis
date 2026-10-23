/* ==========================================================================
 * 项目属性（网页端）
 *
 * 照着 QGIS 桌面版的「项目属性」对话框做的：左边一列分区，右边是内容，
 * 底部是详情 + 按钮。**坐标参考系（CRS）** 那一页是能真用的：
 * 搜索、预定义清单、最近使用、属性/WKT、适用范围预览图、复制。
 *
 * 其余分区（常规 / 元数据 / 样式 / 变量 / 时态 …）先占位，写清楚
 * 「本节暂未接入」—— 不假装能用。
 *
 * 数据来自服务端：/api/crs（搜索）、/api/crs/predefined（预定义清单）、
 * /api/crs/detail（详情）、/api/crs/preview.png（适用范围预览图）。
 *
 * 自包含：样式、DOM、逻辑都在这个文件里，挂在 index.html 末尾一行
 * <script src="/static/settings.js"></script>。
 * ========================================================================== */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));

  // QGIS 的分区表（左边那一列）。crs 那一页是实现了的。
  const SECTIONS = [
    { id: "general",   label: "常规",         icon: "gear" },
    { id: "metadata",  label: "元数据",       icon: "info" },
    { id: "view",      label: "视图设置",     icon: "eye" },
    { id: "crs",       label: "坐标参考系",   icon: "globe", ready: true },
    { id: "transform", label: "坐标转换",     icon: "axis" },
    { id: "styles",    label: "样式",         icon: "brush" },
    { id: "colors",    label: "颜色",         icon: "palette" },
    { id: "sources",   label: "数据源",       icon: "db" },
    { id: "relations", label: "关系",         icon: "link" },
    { id: "variables", label: "变量",         icon: "var" },
    { id: "macros",    label: "宏",           icon: "macro" },
    { id: "server",    label: "QGIS Server",  icon: "server" },
    { id: "temporal",  label: "时态",         icon: "clock" },
    { id: "elevation", label: "高程",         icon: "hill" },
    { id: "sensors",   label: "传感器",       icon: "sensor" },
  ];

  // 16px 描边图标（每个分区一个）
  const ICONS = {
    gear: '<circle cx="8" cy="8" r="2.6"/><path d="M8 1.6v2M8 12.4v2M1.6 8h2M12.4 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M12.5 3.5l-1.4 1.4M4.9 11.1l-1.4 1.4"/>',
    info: '<circle cx="8" cy="8" r="6.2"/><path d="M8 7.2v4M8 4.8v.6"/>',
    eye: '<path d="M1.4 8S3.8 3.6 8 3.6 14.6 8 14.6 8 12.2 12.4 8 12.4 3.8 8 1.4 8z"/><circle cx="8" cy="8" r="2.2"/>',
    globe: '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c1.6 1.8 2.4 3.6 2.4 6S9.6 12.2 8 14 6.4 12.2 5.6 10.2 5.6 8 6.4 3.8 8 2z"/>',
    axis: '<path d="M2 14 14 2M2 14h3M2 14v-3M14 2h-3M14 2v3"/>',
    brush: '<path d="M3 13.2 9.6 6.6M11 2.4l2.6 2.6-5 5-2.6-2.6zM3 13.2c-1 .3-1.6.8-1.6 1.8 1 0 1.5-.6 1.8-1.6z"/>',
    palette: '<path d="M8 1.8a6.2 6.2 0 1 0 0 12.4c1 0 1.6-.6 1.6-1.4 0-.9-.8-1.2-.8-2 0-.7.6-1.2 1.4-1.2h1.6A2.4 2.4 0 0 0 14.2 7c0-2.9-2.8-5.2-6.2-5.2z"/><circle cx="5" cy="7" r=".9"/><circle cx="8" cy="4.6" r=".9"/><circle cx="11.4" cy="6.2" r=".9"/>',
    db: '<ellipse cx="8" cy="4" rx="5" ry="2.2"/><path d="M3 4v8c0 1.2 2.2 2.2 5 2.2s5-1 5-2.2V4M3 8c0 1.2 2.2 2.2 5 2.2s5-1 5-2.2"/>',
    link: '<path d="M6.6 9.4a2.6 2.6 0 0 0 3.8 0l2.2-2.2a2.7 2.7 0 0 0-3.8-3.8L7.7 4.4"/><path d="M9.4 6.6a2.6 2.6 0 0 0-3.8 0L3.4 8.8a2.7 2.7 0 0 0 3.8 3.8l1.1-1.1"/>',
    var: '<path d="M5.4 3.2c-1.8 0-3 1.3-3 2.9 0 1.3.8 2.4 2 2.8v4.9c0 1 .8 1.7 1.8 1.7s1.8-.7 1.8-1.7V8.9c1.2-.4 2-1.5 2-2.8 0-1.6-1.2-2.9-2.6-2.9M6.4 12.8h1.2"/>',
    macro: '<rect x="2.2" y="3.4" width="11.6" height="9.2" rx="1.4"/><path d="M5 6.4h2.4M5 9h4.2M11 6.4h.6"/>',
    server: '<rect x="2.2" y="2.4" width="11.6" height="11.2" rx="1.2"/><path d="M2.2 6h11.6M2.2 10h11.6M6 4.3v.1M6 8.3v.1M6 11.7v.1"/>',
    clock: '<circle cx="8" cy="8" r="6.2"/><path d="M8 4.4V8l2.6 1.8"/>',
    hill: '<path d="M1.6 12.4 6 7l2.6 2.8L11 5.6l3.4 6.8z"/><circle cx="11.6" cy="3.6" r="1.4"/>',
    sensor: '<circle cx="8" cy="8" r="2"/><path d="M5.2 5.2a4 4 0 0 0 0 5.6M10.8 5.2a4 4 0 0 1 0 5.6M3 3a7 7 0 0 0 0 10M13 3a7 7 0 0 1 0 10"/>',
  };

  const CSS = `
#dlgSettings{position:fixed;inset:0;z-index:9400;background:rgba(30,36,32,.34);
  display:none;align-items:center;justify-content:center}
#dlgSettings.show{display:flex}
#dlgSettings .sd{
  width:900px;max-width:95vw;height:620px;max-height:92vh;display:flex;flex-direction:column;
  background:var(--panel);border:1px solid var(--rule-2);border-radius:4px;overflow:hidden;
  box-shadow:0 20px 48px rgba(30,36,32,.26);
}
#dlgSettings .sd-title{
  padding:9px 13px;border-bottom:1px solid var(--rule);background:var(--panel-2);
  font-size:13px;font-weight:600;display:flex;align-items:center;gap:8px;
}
#dlgSettings .sd-title .x{margin-left:auto;cursor:pointer;color:var(--ink-3);font-size:15px;line-height:1;padding:0 3px}
#dlgSettings .sd-title .x:hover{color:var(--rust)}
#dlgSettings .sd-body{flex:1;display:grid;grid-template-columns:186px 1fr;min-height:0}
#dlgSettings .sd-nav{
  background:var(--panel-2);border-right:1px solid var(--rule);overflow:auto;padding:6px 0;
}
#dlgSettings .sd-nav .n{
  display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;
  font-size:12.5px;color:var(--ink-2);
}
#dlgSettings .sd-nav .n:hover{background:var(--moss-soft);color:var(--ink)}
#dlgSettings .sd-nav .n.on{background:var(--moss-soft);color:var(--ink);font-weight:600;
  box-shadow:inset 3px 0 0 var(--moss)}
#dlgSettings .sd-nav .n svg{color:var(--ink-3);flex:0 0 auto}
#dlgSettings .sd-nav .n.on svg{color:var(--moss)}
#dlgSettings .sd-nav .navsearch{padding:5px 8px 8px}
#dlgSettings .sd-nav .navsearch input{
  width:100%;font:inherit;font-size:11.5px;padding:4px 6px;
  border:1px solid var(--rule-2);border-radius:3px;background:var(--panel);color:var(--ink);
}
#dlgSettings .sd-content{overflow:auto;padding:14px 16px}
#dlgSettings .sd-foot{
  padding:9px 13px;border-top:1px solid var(--rule);background:var(--panel-2);
  display:flex;align-items:center;gap:7px;
}
#dlgSettings .sd-foot .left{margin-right:auto;font-size:11.5px;color:var(--ink-3)}
#dlgSettings h3{margin:0 0 10px;font-size:13px;font-weight:650}
#dlgSettings .row{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:9px}
#dlgSettings .row label{font-size:12px;color:var(--ink-2)}
#dlgSettings input[type=text],#dlgSettings input[type=search]{
  font:inherit;font-size:12px;padding:5px 7px;border:1px solid var(--rule-2);
  border-radius:3px;background:var(--panel);color:var(--ink);min-width:230px;
}
#dlgSettings table{width:100%;border-collapse:collapse;font-size:12px}
#dlgSettings th{
  text-align:left;font-size:11px;font-weight:650;color:var(--ink-2);background:var(--panel-2);
  padding:6px 8px;border-bottom:1px solid var(--rule);position:sticky;top:0;
}
#dlgSettings td{padding:5px 8px;border-bottom:1px solid var(--rule);color:var(--ink-2)}
#dlgSettings tr{cursor:pointer}
#dlgSettings tr:hover td{background:var(--moss-soft)}
#dlgSettings tr.on td{background:var(--moss-soft);color:var(--ink);font-weight:600}
#dlgSettings .box{
  border:1px solid var(--rule);border-radius:3px;background:var(--panel);overflow:auto;margin-bottom:12px;
}
#dlgSettings .box .hd{
  padding:5px 9px;background:var(--panel-2);border-bottom:1px solid var(--rule);
  font-size:11px;font-weight:650;color:var(--ink-2);
}
#dlgSettings .box table{font-size:12px}
#dlgSettings .box table th{position:static}
#dlgSettings .detail{display:grid;grid-template-columns:1fr 380px;gap:12px;align-items:start}
#dlgSettings .detail pre{
  margin:0;max-height:150px;overflow:auto;background:var(--panel-2);border:1px solid var(--rule);
  border-radius:3px;padding:8px;font-family:var(--mono);font-size:11px;white-space:pre-wrap;
  word-break:break-all;color:var(--ink-2);
}
#dlgSettings .detail img{
  width:100%;border:1px solid var(--rule);border-radius:3px;background:var(--panel-2);display:block;
}
#dlgSettings .kv{display:grid;grid-template-columns:auto 1fr;gap:2px 9px;font-size:12px;margin-bottom:8px}
#dlgSettings .kv dt{color:var(--ink-3);white-space:nowrap}
#dlgSettings .kv dd{margin:0;color:var(--ink);font-family:var(--mono);font-size:11.5px;word-break:break-all}
#dlgSettings .note{
  background:var(--panel-2);border:1px solid var(--rule);border-radius:3px;padding:8px 10px;
  font-size:11.5px;color:var(--ink-2);line-height:1.6;
}
#dlgSettings .note.warn{border-color:#E0D2AE;background:#F6F0E2;color:#6E5210}
`;

  const RECENT_KEY = "gisds.crs.recent";

  let section = "crs";
  let predefined = [];
  let recent = [];
  let filtered = [];
  let picked = null;      // 当前选中的 CRS（详情）
  let pickedId = null;    // 当前选中的 id（高亮）

  // ---------------------------------------------------------------- 工具

  function svg(name, size) {
    size = size || 14;
    const body = ICONS[name] || ICONS.info;
    return '<svg class="ic" viewBox="0 0 16 16" width="' + size + '" height="' + size +
      '" fill="none" stroke="currentColor" stroke-width="1.35" ' +
      'stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>";
  }

  function loadRecent() {
    try {
      const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      recent = Array.isArray(v) ? v : [];
    } catch (e) { recent = []; }
  }

  function saveRecent() {
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, 20))); } catch (e) { /* 无痕 */ }
  }

  function pushRecent(item) {
    if (!item || !item.id) return;
    recent = recent.filter((x) => x.id !== item.id);
    recent.unshift({ id: item.id, name: item.name, kind: item.kind });
    saveRecent();
  }

  // ---------------------------------------------------------------- 框架

  function build() {
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    const holder = document.createElement("div");
    holder.id = "dlgSettings";
    holder.innerHTML = `
<div class="sd">
  <div class="sd-title">项目属性<span class="x" id="sdClose">✕</span></div>
  <div class="sd-body">
    <div class="sd-nav">
      <div class="navsearch"><input type="search" id="navSearch" placeholder="搜索分区…" /></div>
      <div id="navList"></div>
    </div>
    <div class="sd-content" id="sdContent"></div>
  </div>
  <div class="sd-foot">
    <span class="left" id="sdHint"></span>
    <button class="btn" id="sdHelp">帮助</button>
    <button class="btn" id="sdCancel">取消</button>
    <button class="btn" id="sdApply">应用</button>
    <button class="btn primary" id="sdOk">确定</button>
  </div>
</div>`;
    document.body.appendChild(holder);

    $("sdClose").onclick = close;
    $("sdCancel").onclick = close;
    $("sdHelp").onclick = () => window.open("/help", "_blank");
    $("sdApply").onclick = () => applyPicked(false);
    $("sdOk").onclick = () => applyPicked(true);
    $("navSearch").oninput = (e) => renderNav(e.target.value);
    holder.addEventListener("click", (e) => { if (e.target === holder) close(); });
  }

  function renderNav(filter) {
    const f = (filter || "").trim().toLowerCase();
    const list = SECTIONS.filter((s) => !f || s.label.toLowerCase().includes(f) || s.id.includes(f));
    $("navList").innerHTML = list.map((s) =>
      `<div class="n ${s.id === section ? "on" : ""}" data-sec="${s.id}">${svg(s.icon, 14)}<span>${esc(s.label)}</span></div>`
    ).join("");
    $("navList").querySelectorAll(".n").forEach((n) => {
      n.onclick = () => { section = n.dataset.sec; renderNav($("navSearch").value); renderContent(); };
    });
  }

  // ---------------------------------------------------------------- 内容

  function renderContent() {
    if (section === "crs") return renderCrs();
    const s = SECTIONS.find((x) => x.id === section) || SECTIONS[0];
    $("sdContent").innerHTML =
      `<h3>${esc(s.label)}</h3>
       <div class="note">这一节（<b>${esc(s.label)}</b>）还没接入。
       <br>目前只有 <b>坐标参考系</b> 那一页是真能用的 —— 它是完整的坐标系查阅器：
       搜索、预定义清单、最近使用、属性与 WKT、适用范围预览图。</div>`;
    $("sdHint").textContent = "只有「坐标参考系」一节已实现";
  }

  function crsTable(rows, head) {
    if (!rows.length) return `<div style="padding:10px;color:var(--ink-3);font-size:12px">（没有条目）</div>`;
    return `<table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>` +
      rows.map((r) => `<tr data-crs="${esc(r.id)}" class="${r.id === pickedId ? "on" : ""}">
        <td>${esc(r.name || r.id)}</td><td>${esc(r.id)}</td></tr>`).join("") +
      `</tbody></table>`;
  }

  function renderCrs() {
    $("sdContent").innerHTML = `
<h3>坐标参考系（CRS）</h3>
<div class="row">
  <label><input type="checkbox" id="crsNone" disabled
         title="项目级坐标系尚未接入，这里先当查阅器用" /> 无 CRS（未知/非地球投影）</label>
</div>
<div class="row">
  <label for="crsFilter">过滤器</label>
  <input type="search" id="crsFilter" placeholder="名称 / 编码 / 别名，例如 4326、CGCS2000" />
</div>
<div class="box"><div class="hd">最近使用的坐标参考系</div><div id="crsRecentBox"></div></div>
<div class="box"><div class="hd">预定义的坐标参考系统</div><div id="crsPreBox"></div></div>
<div id="crsDetail"></div>`;

    $("crsFilter").oninput = (e) => filterCrs(e.target.value);
    $("crsPreBox").innerHTML = crsTable(predefined, ["坐标系", "标识"]);
    $("crsRecentBox").innerHTML = crsTable(recent, ["坐标系", "标识"]);
    bindCrsRows();
    filterCrs("");
    $("sdHint").textContent = "点一行看它的属性、WKT 和适用范围";
  }

  function filterCrs(text) {
    const t = (text || "").trim();
    if (!t) {
      filtered = predefined.slice();
    } else {
      const low = t.toLowerCase();
      filtered = predefined.filter((x) =>
        (x.id || "").toLowerCase().includes(low) || (x.name || "").toLowerCase().includes(low));
      // 本地没有的，去服务端搜全量 EPSG
      if (!filtered.length) {
        searchServer(t);
        return;
      }
    }
    $("crsPreBox").innerHTML = crsTable(filtered, ["坐标系", "标识"]);
    bindCrsRows();
  }

  let searchToken = 0;
  async function searchServer(text) {
    const token = ++searchToken;
    try {
      const res = await api("/api/crs?search=" + encodeURIComponent(text) + "&limit=80");
      if (token !== searchToken) return;      // 已经有更新的搜索了
      const list = (res && res.crs) || [];
      filtered = list;
      $("crsPreBox").innerHTML = crsTable(filtered, ["坐标系", "标识"]);
      bindCrsRows();
    } catch (e) {
      if (token !== searchToken) return;
      $("crsPreBox").innerHTML = `<div style="padding:10px;color:var(--rust);font-size:12px">搜索失败：${esc(e.message || e)}</div>`;
    }
  }

  function bindCrsRows() {
    document.querySelectorAll("#dlgSettings tr[data-crs]").forEach((tr) => {
      tr.onclick = () => selectCrs(tr.dataset.crs);
    });
  }

  async function selectCrs(id) {
    pickedId = id;
    document.querySelectorAll("#dlgSettings tr[data-crs]").forEach((tr) => {
      tr.classList.toggle("on", tr.dataset.crs === id);
    });
    $("crsDetail").innerHTML = `<div class="note">正在读取 ${esc(id)} …</div>`;
    try {
      const d = await api("/api/crs/detail?crs=" + encodeURIComponent(id));
      picked = d;
      const known = (predefined.concat(recent).concat(filtered)).find((x) => x.id === id);
      pushRecent(known || { id: id, name: d.name, kind: d.kind });
      $("crsRecentBox").innerHTML = crsTable(recent, ["坐标系", "标识"]);
      bindCrsRows();
      $("crsDetail").innerHTML = detailHtml(d);
      const bw = $("crsCopyWkt"), bp = $("crsCopyProj");
      if (bw) bw.onclick = () => copyText(d.wkt || "", "WKT");
      if (bp) bp.onclick = () => copyText(d.proj || "", "PROJ");
      loadPreview(d.id);
      $("sdHint").textContent = "已选中 " + (d.name || id);
    } catch (e) {
      picked = null;
      $("crsDetail").innerHTML = `<div class="note warn">读取失败：${esc(e.message || e)}</div>`;
    }
  }

  function detailHtml(d) {
    const kind = d.kind === "geographic" ? "地理坐标系（用经纬度）" : "投影坐标系";
    const props = [
      ["类型", kind],
      ["椭球", (d.ellipsoid_name || d.ellipsoid || "—")],
      ["基准", d.datum || "—"],
      ["投影方法", d.method || "—"],
      ["单位", d.unit || "—"],
      ["适用范围", d.area_of_use || "—"],
      ["已废弃", d.deprecated ? "是" : "否"],
    ];
    const html = `<div class="detail">
      <div>
        <h3 style="font-size:12.5px">${esc(d.name || d.id)}</h3>
        <dl class="kv">${props.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
        <div class="row">
          <button class="btn" id="crsCopyWkt">复制 WKT</button>
          <button class="btn" id="crsCopyProj">复制 PROJ</button>
        </div>
        <pre id="crsWkt">${esc(d.wkt || "")}</pre>
      </div>
      <div>
        <div class="box" style="margin:0">
          <div class="hd">适用范围</div>
          <img id="crsPreviewImg" alt="适用范围" />
        </div>
      </div>
    </div>`;
    return html;
  }

  /** 预览图要**带令牌**取 —— <img src> 带不了 Authorization 头，
      直接用 img 会 401（预览图接口是要登录的）。 */
  async function loadPreview(id) {
    const img = $("crsPreviewImg");
    if (!img) return;
    try {
      const res = await fetch(API + "/api/crs/preview.png?crs=" + encodeURIComponent(id), {
        headers: { Authorization: "Bearer " + sessionToken() },
        cache: "no-store",
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      img.onload = () => URL.revokeObjectURL(url);
      img.src = url;
    } catch (e) {
      img.alt = "预览图加载失败：" + (e.message || e);
    }
  }

  async function copyText(text, what) {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast(what + " 已复制到剪贴板");
    } catch (e) {
      // 剪贴板 API 在非 https 下可能不给用，退回选中+复制
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      ta.remove();
      toast(ok ? what + " 已复制到剪贴板" : "复制失败，请手动选中上面的文本");
    }
  }

  // ---------------------------------------------------------------- 开关

  function open() {
    build();
    $("dlgSettings").classList.add("show");
    loadRecent();
    section = "crs";
    renderNav("");
    renderContent();
    loadPredefined();
  }

  function close() {
    const d = $("dlgSettings");
    if (d) d.classList.remove("show");
  }

  async function loadPredefined() {
    try {
      const res = await api("/api/crs/predefined");
      predefined = (res && res.crs) || [];
      filtered = predefined.slice();
      if (section === "crs") {
        $("crsPreBox").innerHTML = crsTable(predefined, ["坐标系", "标识"]);
        bindCrsRows();
      }
    } catch (e) {
      predefined = [];
      const box = $("crsPreBox");
      if (box) {
        box.innerHTML = `<div style="padding:10px;color:var(--rust);font-size:12px">读取预定义清单失败：${esc(e.message || e)}</div>`;
      }
    }
  }

  function applyPicked(closeAfter) {
    if (!picked) { toast("先选一个坐标系"); return; }
    pushRecent(picked);
    const what = picked.kind === "geographic" ? "地理坐标系" : "投影坐标系";
    toast(`已记住 ${picked.id}（${what}）`);
    if (closeAfter) close();
  }

  // ---------------------------------------------------------------- 菜单

  function mountMenu() {
    if (typeof MENUS === "undefined" || !MENUS.file) return;
    if (MENUS.__settingsMounted) return;
    const orig = MENUS.file;
    MENUS.file = () => {
      const list = orig();
      list.push({ sep: true });
      list.push({ label: "项目属性…", run: open });
      return list;
    };
    MENUS.__settingsMounted = true;
  }

  // ---------------------------------------------------------------- 启动

  function init() {
    build();
    mountMenu();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.openProjectProperties = open;
})();
