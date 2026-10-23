/* ==========================================================================
 * 文件浏览器（网页端）
 *
 * 桌面版启动器里有个独立的文件浏览器窗口（launcher-files.bat），网页端
 * 原来没有对应的东西 —— 只有「添加图层 → 服务器共享数据」能挑已有图层，
 * 没有地方管文件本身（传了什么、哪些没被识别、怎么下、怎么删）。
 *
 * 这个面板把「文件」和「图层」拆开：文件管理归文件管理，图层归图层。
 *
 * 数据源用的是**工作区**（/api/uploads）而不是**注册中心**（/api/datasets）：
 * 前者是磁盘上真实存在的东西，后者只含 QGIS 认得出来的图层。传了但
 * QGIS 打不开的文件（比如只传了 .shp、缺 .dbf/.shx/.prj 配套件）实实在在
 * 存在磁盘上，却没进注册中心 —— 只读注册中心的话，「我上传的文件」就凭空
 * 消失了，而且它还删不掉。
 *
 * 自包含：样式、DOM、逻辑都在这个文件里，挂在 index.html 末尾一行
 * <script src="/static/files.js"></script>，不依赖 index.html 的 boot()
 * 流程（跟 panels.js 一个路子）。
 * ========================================================================== */
(function () {
  "use strict";

  const ALL = "全部";

  const CSS = `
#dlgFiles .dlg{width:920px;max-width:96vw;height:560px;max-height:90vh}
#dlgFiles .fb-bar{
  display:flex;align-items:center;gap:8px;padding:9px 13px;
  border-bottom:1px solid var(--rule);background:var(--panel-2);
}
#dlgFiles .fb-bar label{font-size:11.5px;color:var(--ink-3)}
#dlgFiles select{
  font:inherit;font-size:12px;padding:4px 7px;border:1px solid var(--rule-2);
  border-radius:3px;background:var(--panel);color:var(--ink);min-width:170px;
}
#dlgFiles .fb-count{margin-left:auto;font-size:11.5px;color:var(--ink-3)}
#dlgFiles .fb-wrap{flex:1;overflow:auto}
#dlgFiles table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px}
#dlgFiles th{
  position:sticky;top:0;z-index:2;text-align:left;font-weight:650;font-size:11px;
  color:var(--ink-2);background:var(--panel-2);
  padding:7px 10px;border-bottom:1px solid var(--rule);white-space:nowrap;
}
#dlgFiles td{
  padding:6px 10px;border-bottom:1px solid var(--rule);color:var(--ink-2);
  vertical-align:top;overflow-wrap:anywhere;
}
#dlgFiles tr:hover td{background:var(--moss-soft)}
#dlgFiles .fb-name{color:var(--ink);font-weight:600}
#dlgFiles .fb-files a{color:var(--moss);text-decoration:none;cursor:pointer}
#dlgFiles .fb-files a:hover{text-decoration:underline}
#dlgFiles .fb-files .sep{color:var(--ink-3);margin:0 5px}
#dlgFiles .fb-ok{color:var(--moss)}
#dlgFiles .fb-bad{color:#A33B24}
#dlgFiles .fb-act{white-space:nowrap}
#dlgFiles .fb-act button{
  font:inherit;font-size:11px;padding:2px 7px;margin-right:4px;cursor:pointer;
  border:1px solid var(--rule-2);border-radius:3px;background:var(--panel);color:var(--ink-2);
}
#dlgFiles .fb-act button:hover{background:var(--moss-soft);color:var(--ink)}
#dlgFiles .fb-act button.danger:hover{background:#F6E7E3;color:#A33B24}
#dlgFiles .fb-empty{padding:26px 14px;text-align:center;color:var(--ink-3);font-size:12.5px}
`;

  const HTML = `
<div class="modal" id="dlgFiles">
  <div class="dlg">
    <div class="dlg-title">文件浏览器<span class="x" id="fbClose">✕</span></div>
    <div class="dlg-body" style="padding:0;display:flex;flex-direction:column">
      <div class="fb-bar">
        <label for="fbScope">空间</label>
        <select id="fbScope"></select>
        <input type="file" id="fbPick" multiple hidden
               accept=".tif,.tiff,.img,.vrt,.geojson,.json,.gpkg,.shp,.gml,.kml">
        <button class="btn" id="fbUpload">上传文件</button>
        <button class="btn" id="fbRefresh">刷新</button>
        <span class="fb-count" id="fbCount"></span>
      </div>
      <div class="fb-wrap" id="fbWrap"></div>
    </div>
    <div class="dlg-foot">
      <span class="hint left" id="fbHint">点文件名直接下载 · 双击一行把它认得出来的图层加到地图</span>
      <button class="btn" id="fbClose2">关闭</button>
    </div>
  </div>
</div>`;

  // ---------------------------------------------------------------- 状态
  let items = [];      // /api/uploads 返回的数据集清单
  let scopes = [];     // 当前用户能看的空间
  let scope = ALL;     // 下拉当前选中
  let inited = false;

  // ---------------------------------------------------------------- 工具
  const $ = (id) => document.getElementById(id);

  function bytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
    ));
  }

  // ---------------------------------------------------------------- 初始化
  function inject() {
    if (inited) return;
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    const holder = document.createElement("div");
    holder.innerHTML = HTML;
    document.body.appendChild(holder.firstElementChild);
    inited = true;

    $("fbClose").onclick = close;
    $("fbClose2").onclick = close;
    $("fbRefresh").onclick = () => refresh(true);
    $("fbUpload").onclick = () => $("fbPick").click();
    $("fbPick").onchange = (e) => {
      const files = [...e.target.files];
      e.target.value = "";
      if (files.length) upload(files);
    };
    $("fbScope").onchange = (e) => { scope = e.target.value; render(); };

    // 点背景关闭（和「添加图层」一样：要**按下时**也在背景上才算）
    let downOnBackdrop = false;
    $("dlgFiles").addEventListener("pointerdown", (e) => {
      downOnBackdrop = (e.target === $("dlgFiles"));
    });
    $("dlgFiles").onclick = (e) => {
      if (e.target === $("dlgFiles") && downOnBackdrop) close();
    };
  }

  // ---------------------------------------------------------------- 菜单
  function mountMenu() {
    // 「文件」菜单里加一项。MENUS 是 index.html 里的全局 const（classic 脚本），
    // 这里包一层 —— 菜单是函数，每次调用都返回新数组，不能直接 push。
    if (typeof MENUS === "undefined" || !MENUS.file) return;
    if (MENUS.__fbMounted) return;
    const orig = MENUS.file;
    MENUS.file = () => {
      const list = orig();
      list.push({ sep: true });
      list.push({ label: "文件浏览器…", run: open });
      return list;
    };
    MENUS.__fbMounted = true;
  }

  // ---------------------------------------------------------------- 打开/关闭
  function open() {
    inject();
    $("dlgFiles").classList.add("show");
    refresh(true);
  }

  function close() {
    const dlg = $("dlgFiles");
    if (dlg) dlg.classList.remove("show");
  }

  // ---------------------------------------------------------------- 数据
  async function loadScopes() {
    try {
      const me = await api("/api/auth/me");
      const list = me && me.scopes ? me.scopes : [];
      scopes = list.map((s) => (typeof s === "string" ? { scope: s } : s));
    } catch (exc) {
      scopes = [];
    }
  }

  async function refresh(withScopes) {
    inject();
    if (withScopes) await loadScopes();
    const sel = $("fbScope");
    const cur = scope;
    const labels = [ALL].concat(scopes.map((s) => s.scope));
    sel.innerHTML = labels.map((v) => {
      const s = scopes.find((x) => x.scope === v);
      const text = s ? (s.label || v) : (v === ALL ? "全部空间" : v);
      return `<option value="${esc(v)}">${esc(text)}</option>`;
    }).join("");
    scope = labels.includes(cur) ? cur : ALL;
    sel.value = scope;

    try {
      const data = await api("/api/uploads");
      items = (data && data.uploads) || [];
    } catch (exc) {
      items = [];
      $("fbCount").textContent = "";
      $("fbWrap").innerHTML =
        `<div class="fb-empty">读取文件清单失败：${esc(exc.message || exc)}<br>` +
        `没登录的话请先登录 —— 登录后就能看到各个空间里已上传的文件。</div>`;
      return;
    }
    render();
  }

  function visible() {
    if (scope === ALL) return items;
    return items.filter((d) => (d.scope || "shared") === scope);
  }

  // ---------------------------------------------------------------- 渲染
  function render() {
    const rows = visible();
    $("fbCount").textContent = `${rows.length} 个数据集`;
    if (!rows.length) {
      $("fbWrap").innerHTML =
        `<div class="fb-empty">这里还没有文件。<br>` +
        `点右上角「上传文件」把数据传进来；传完的文件会立刻出现在这里。</div>`;
      return;
    }
    const head = ["数据集", "空间", "文件", "大小", "状态", ""];
    const body = rows.map((d) => {
      // 文件名做成**可点的链接** —— 点一下就下载那个文件。
      // 比在操作列堆一排按钮干净，也比「下载」按钮指向一个不存在的打包下载更靠谱。
      const links = (d.files || []).map((f) =>
        `<a data-dl="${esc(d.dataset_id)}" data-f="${esc(f)}" title="下载 ${esc(f)}">${esc(f)}</a>`
      ).join('<span class="sep">、</span>');
      const ok = d.registered !== false;
      return `<tr data-ds="${esc(d.dataset_id)}" title="${esc(d.directory || "")}">
        <td class="fb-name">${esc(d.dataset_id)}</td>
        <td>${esc(d.scope_label || (d.scope === "shared" ? "共享空间" : "个人空间"))}</td>
        <td class="fb-files">${links || "—"}</td>
        <td>${esc(bytes(d.total_bytes))}</td>
        <td class="${ok ? "fb-ok" : "fb-bad"}">${ok ? "已识别" : "未识别"}</td>
        <td class="fb-act">
          <button data-add="${esc(d.dataset_id)}" ${ok ? "" : "disabled title=\"这个数据集没有能加进地图的图层\""}>加到地图</button>
          <button class="danger" data-del="${esc(d.dataset_id)}">删除</button>
        </td>
      </tr>`;
    }).join("");
    $("fbWrap").innerHTML =
      `<table><colgroup>
         <col style="width:24%"><col style="width:8%"><col style="width:34%">
         <col style="width:8%"><col style="width:8%"><col style="width:18%">
       </colgroup><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>` +
      `<tbody>${body}</tbody></table>`;

    $("fbWrap").querySelectorAll("a[data-dl]").forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); download(b.dataset.dl, b.dataset.f); };
    });
    $("fbWrap").querySelectorAll("button[data-add]").forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); addToMap(b.dataset.add); };
    });
    $("fbWrap").querySelectorAll("button[data-del]").forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); remove(b.dataset.del); };
    });
    $("fbWrap").querySelectorAll("tr[data-ds]").forEach((tr) => {
      tr.ondblclick = () => addToMap(tr.dataset.ds);
    });
  }

  // ---------------------------------------------------------------- 操作
  async function upload(files) {
    // 「全部」只是个显示过滤器，不是能上传到的地方 —— 落到用户自己的空间
    // （先落在自己名下，要共享再单独传一份到共享空间）。
    let target = scope;
    if (target === ALL) target = "me";
    $("fbHint").textContent = "上传中…";
    try {
      const r = await api("/api/uploads?scope=" + encodeURIComponent(target), {
        method: "POST",
        body: (() => {
          const fd = new FormData();
          files.forEach((f) => fd.append("files", f, f.name));
          return fd;
        })(),
      });
      const results = (r && r.results) || [];
      const bad = results.filter((x) => !x.registered);
      if (bad.length) {
        toast(`已上传 ${results.length} 个，其中 ${bad.length} 个没能识别成图层（见「状态」列）`);
      } else {
        toast(`已上传 ${results.length} 个文件`);
      }
    } catch (exc) {
      toast("上传失败：" + (exc.message || exc));
    } finally {
      $("fbHint").textContent = "点文件名直接下载 · 双击一行把它认得出来的图层加到地图";
      await refresh(false);
    }
  }

  async function download(datasetId, filename) {
    // 下载接口要鉴权，不能直接用 <a href>（会 401）—— 取成 blob 再触发下载。
    try {
      const res = await fetch(API + "/api/uploads/" +
        encodeURIComponent(datasetId) + "/files/" + encodeURIComponent(filename), {
        headers: { Authorization: "Bearer " + sessionToken() },
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast("已下载 " + filename);
    } catch (exc) {
      toast("下载失败：" + (exc.message || exc));
    }
  }

  async function remove(datasetId) {
    if (!confirm("确定删除数据集「" + datasetId + "」？\n磁盘上的文件会一并删除。")) return;
    try {
      await api("/api/uploads/" + encodeURIComponent(datasetId), { method: "DELETE" });
      toast("已删除 " + datasetId);
    } catch (exc) {
      toast("删除失败：" + (exc.message || exc));
    }
    await refresh(false);
  }

  async function addToMap(datasetId) {
    const d = items.find((x) => x.dataset_id === datasetId);
    if (!d) return;
    const refs = [];
    ["layers", "vectors"].forEach((k) => {
      const dict = d[k] || {};
      Object.keys(dict).forEach((id) => refs.push(dict[id].ref));
    });
    if (!refs.length) {
      toast("这个数据集没有能加进地图的图层（未识别）");
      return;
    }
    let n = 0;
    for (const ref of refs) {
      const l = (S.layers || []).find((x) => x.ref === ref);
      if (!l) continue;
      await addLayer({ ...l, _type: l.kind === "vector" ? "vector" : "raster" });
      n++;
    }
    toast(n ? `已加入 ${n} 个图层` : "这个数据集的图层还没加载进来，先刷新一下");
  }

  // ---------------------------------------------------------------- 启动
  function init() {
    inject();
    mountMenu();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // 方便控制台里手动调
  window.openFilesBrowser = open;
})();
