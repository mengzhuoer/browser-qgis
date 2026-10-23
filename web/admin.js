/* ==========================================================================
 * 管理员分区（网页端）
 *
 * 右栏多一个「管理员」页签，里面四块：用户 / 空间 / 审计 / 配置。
 *
 * **普通用户看不到这一页签** —— 不是「点了报错」，而是**页签根本不渲染**：
 * 页面加载时先问一次 GET /api/auth/me，principal.is_admin 为 false 就到此为止，
 * 连 DOM 都不建。后端 /api/admin/* 每一路也都单独鉴权（要求管理员），
 * 所以前端藏起来只是体面，后端才是真门槛。
 *
 * 自包含：样式、DOM、逻辑都在这个文件里，挂在 index.html 末尾一行
 * <script src="/static/admin.js"></script>，跟 files.js / settings.js 一个路子。
 * ========================================================================== */
(function () {
  "use strict";

  const PANE_ID = "paneAdmin";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));

  // ---------------------------------------------------------------- 状态
  let isAdmin = false;
  let mounted = false;
  let view = "users";        // 当前看哪一块：users / spaces / audit / config
  let me = null;             // 当前登录者
  let users = [];
  let spaces = [];
  let audit = [];
  let config = null;
  let busy = false;

  // ---------------------------------------------------------------- 工具
  function toast(msg) {
    if (typeof window.toast === "function") window.toast(msg);
    else if (typeof tip === "function") tip(msg);
  }

  /** 把 unix 秒变成本地时间的短字符串。 */
  function fmtTime(ts) {
    const n = Number(ts);
    if (!n || !isFinite(n)) return "—";
    const d = new Date(n * 1000);
    const p = (x) => String(x).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate())
      + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  /** 只有月日时分 —— 右栏窄，日期列写成全量会把「操作」列挤出屏幕。 */
  function fmtDay(ts) {
    const n = Number(ts);
    if (!n || !isFinite(n)) return "—";
    const d = new Date(n * 1000);
    const p = (x) => String(x).padStart(2, "0");
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function fmtRole(role) {
    return role === "admin" ? "管理员" : "普通用户";
  }

  function fmtStatus(st) {
    return st === "disabled" ? "已停用" : "正常";
  }

  function fmtConsoleAccess(mode) {
    if (mode === "user") return "所有登录用户";
    if (mode === "off") return "已关闭";
    return "仅管理员";
  }

  // ---------------------------------------------------------------- 样式
  const CSS = `
/* 这里必须用「#paneAdmin」/「#paneAdmin.on」两条，而不是一条「#paneAdmin{display:flex}」：
   页面里「.tabpane{display:none}」管着「没选中的页签藏起来」，而
   「#paneAdmin」的 **ID 选择器优先级更高**（1,0,0 > 0,1,0）—— 一条
   「#paneAdmin{display:flex}」会把「.tabpane」的隐藏**整个盖掉**，
   于是这个面板一直占着右栏一小块，切到「识别结果」它也不收。
   所以显式写两条：默认 none，被选中时 flex。

   ⚠️ 这段注释原来用的是**反引号**包选择器（就是那个撇号字符）——
   它在下面这个 CSS 模板字符串里，反引号会把模板串**提前闭合**，
   整个 admin.js 语法错误、不执行，管理员页签就永远不出现
   （而且控制台只有一条 SyntaxError，页面上什么现象都没有）。
   所以这个模板串里**一律不许出现反引号** —— 连注释里也不行。
   （我就是在这儿又踩了一次：第一次改的时候把示例写成了带反引号的样子，
     结果模板串照样被切断。**这种事只能靠机器数，不能靠眼睛看。**） */
#paneAdmin{display:none;flex-direction:column;min-height:0}
#paneAdmin.on{display:flex}
#paneAdmin .ad-head{
  display:flex;align-items:center;gap:8px;padding:7px 12px;
  background:var(--panel-2);border-bottom:1px solid var(--rule);
}
#paneAdmin .ad-title{font-size:12.5px;font-weight:650;color:var(--ink)}
#paneAdmin .ad-who{font-size:11px;color:var(--ink-3)}
#paneAdmin .ad-spacer{flex:1}
#paneAdmin .ad-nav{
  display:flex;flex:0 0 auto;border-bottom:1px solid var(--rule);background:var(--panel);
}
#paneAdmin .ad-navitem{
  padding:6px 11px;cursor:pointer;font-size:11.5px;color:var(--ink-3);
  border-bottom:2px solid transparent;
}
#paneAdmin .ad-navitem:hover{color:var(--ink-2);background:var(--panel-2)}
#paneAdmin .ad-navitem.on{color:var(--ink);border-bottom-color:var(--moss);font-weight:600}
#paneAdmin .ad-body{flex:1;min-height:0;overflow:auto}
#paneAdmin .ad-view{display:none;padding:10px 12px;overflow-x:auto}
#paneAdmin .ad-view.on{display:block}
/* 表格在窄右栏里会被挤变形：「管理员」徽标竖着排成一列、操作按钮折成三行。
   给一个最小宽度 + 横向滚动，宁可让用户横着拖，也不要压变形。 */
#paneAdmin table{width:100%;min-width:430px;border-collapse:collapse;font-size:11.5px}
#paneAdmin th{
  text-align:left;font-size:10.5px;font-weight:650;color:var(--ink-2);background:var(--panel-2);
  padding:5px 7px;border-bottom:1px solid var(--rule);white-space:nowrap;
}
#paneAdmin td{padding:4px 7px;border-bottom:1px solid var(--rule);color:var(--ink-2);vertical-align:top}
#paneAdmin tr:hover td{background:var(--moss-soft)}
/* 「操作」列钉在右边：右栏窄的时候它是最靠右的一列，
   不钉住就容易被横向滚动条推到屏幕外 —— 而那正是最该点得到的一列。 */
#paneAdmin th:last-child,#paneAdmin td:last-child{
  position:sticky;right:0;background:var(--panel);box-shadow:-1px 0 0 var(--rule);
}
#paneAdmin tr:hover td:last-child{background:var(--moss-soft)}
#paneAdmin .ad-badge{
  display:inline-block;padding:0 5px;border-radius:8px;font-size:10.5px;white-space:nowrap;
  border:1px solid var(--rule-2);background:var(--panel);color:var(--ink-2);
}
#paneAdmin .ad-badge.admin{border-color:var(--moss);color:var(--moss-2);background:var(--moss-soft)}
#paneAdmin .ad-badge.off{border-color:var(--rule-2);color:var(--ink-3)}
#paneAdmin .ad-badge.bad{border-color:var(--rust);color:var(--rust);background:var(--rust-soft)}
#paneAdmin .ad-acts{display:flex;gap:4px;flex-wrap:wrap}
#paneAdmin .ad-passbox{display:flex;gap:4px;align-items:center;margin-top:4px;flex-wrap:wrap}
#paneAdmin button.danger{border-color:var(--rust);color:var(--rust);font-weight:600}
#paneAdmin button.danger:hover{background:var(--rust-soft)}
#paneAdmin .ad-form{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:9px}
#paneAdmin .ad-form label{font-size:11px;color:var(--ink-3);display:block;margin-bottom:2px}
#paneAdmin .ad-form input,#paneAdmin .ad-form select{
  width:100%;font:inherit;font-size:12px;padding:4px 6px;
  border:1px solid var(--rule-2);border-radius:3px;background:var(--panel);color:var(--ink);
}
#paneAdmin .ad-form .full{grid-column:1/-1}
#paneAdmin .ad-warn{
  border:1px solid var(--rule-2);border-left:3px solid var(--rust);background:var(--rust-soft);
  color:var(--ink);font-size:11.5px;line-height:1.6;padding:8px 10px;border-radius:3px;margin-bottom:10px;
}
#paneAdmin .ad-warn b{color:var(--rust)}
#paneAdmin .ad-note{
  border:1px solid var(--rule-2);border-left:3px solid var(--moss);background:var(--moss-soft);
  font-size:11.5px;line-height:1.6;padding:8px 10px;border-radius:3px;margin-bottom:10px;color:var(--ink-2);
}
#paneAdmin .ad-kv{display:grid;grid-template-columns:118px 1fr;font-size:11.5px}
#paneAdmin .ad-kv dt{color:var(--ink-3);padding:3px 0}
#paneAdmin .ad-kv dd{margin:0;padding:3px 0;color:var(--ink-2);word-break:break-all}
#paneAdmin .ad-empty{font-size:11.5px;color:var(--ink-3);padding:6px 0}
`;

  // ---------------------------------------------------------------- 骨架
  const HTML = `
<div class="ad-head">
  <span class="ad-title">管理员</span>
  <span class="ad-who" id="adWho">—</span>
  <span class="ad-spacer"></span>
  <button class="btn sm" id="adRefresh" type="button">刷新</button>
</div>
<div class="ad-nav">
  <div class="ad-navitem on" data-view="users">用户</div>
  <div class="ad-navitem" data-view="spaces">空间</div>
  <div class="ad-navitem" data-view="audit">审计</div>
  <div class="ad-navitem" data-view="config">配置</div>
</div>
<div class="ad-body">
  <div class="ad-view on" id="adViewUsers">
    <div id="adUserWarn"></div>
    <div class="row" style="margin-bottom:9px">
      <button class="btn sm primary" id="adNewToggle" type="button">新建账号</button>
      <span class="hint" id="adUserCount">—</span>
    </div>
    <div id="adNewForm" style="display:none">
      <div class="ad-form">
        <div><label for="adNuName">用户名</label><input type="text" id="adNuName" placeholder="至少 3 个字符" /></div>
        <div><label for="adNuPass">口令</label><input type="text" id="adNuPass" placeholder="至少 6 位" /></div>
        <div><label for="adNuRole">角色</label>
          <select id="adNuRole"><option value="user">普通用户</option><option value="admin">管理员</option></select></div>
        <div><label for="adNuDisp">显示名</label><input type="text" id="adNuDisp" placeholder="留空就用用户名" /></div>
        <div class="full row">
          <button class="btn primary" id="adNuOk" type="button">创建</button>
          <button class="btn" id="adNuCancel" type="button">取消</button>
          <span class="hint" id="adNuHint"></span>
        </div>
      </div>
    </div>
    <table>
      <thead><tr>
        <th>用户名</th><th>显示名</th><th>角色</th><th>状态</th>
        <th>创建</th><th>最后登录</th><th>操作</th>
      </tr></thead>
      <tbody id="adUserRows"></tbody>
    </table>
  </div>

  <div class="ad-view" id="adViewSpaces">
    <div class="panel-note" style="border-radius:3px;border:1px solid var(--rule);margin-bottom:9px">
      共享空间人人可写；个人空间只有本人能写。下面是当前登记在册的数据集。
    </div>
    <table>
      <thead><tr>
        <th>空间</th><th>数据集</th><th>图层</th><th>路径</th>
      </tr></thead>
      <tbody id="adSpaceRows"></tbody>
    </table>
  </div>

  <div class="ad-view" id="adViewAudit">
    <div class="panel-note" style="border-radius:3px;border:1px solid var(--rule);margin-bottom:9px">
      审计日志（最近 200 条）。
    </div>
    <table>
      <thead><tr><th>时间</th><th>用户</th><th>动作</th><th>对象</th></tr></thead>
      <tbody id="adAuditRows"></tbody>
    </table>
  </div>

  <div class="ad-view" id="adViewConfig">
    <div id="adCfgWarn"></div>
    <dl class="ad-kv" id="adCfgBody"></dl>
  </div>
</div>`;

  // ---------------------------------------------------------------- 挂载
  function mount() {
    if (mounted) return true;
    const tabs = document.querySelector("aside.right .tabs");
    const aside = document.querySelector("aside.right");
    if (!tabs || !aside) return false;
    if (document.getElementById(PANE_ID)) { mounted = true; return true; }

    const style = document.createElement("style");
    style.id = "admin-style";
    style.textContent = CSS;
    document.head.appendChild(style);

    const tab = document.createElement("div");
    tab.className = "tab";
    tab.dataset.pane = PANE_ID;
    tab.textContent = "管理员";
    tab.title = "账号、空间、审计与服务配置";
    tabs.appendChild(tab);

    const pane = document.createElement("div");
    pane.className = "tabpane";
    pane.id = PANE_ID;
    pane.innerHTML = HTML;
    aside.appendChild(pane);

    tab.onclick = () => {
      if (typeof switchTab === "function") switchTab(PANE_ID);
    };

    bind();
    mounted = true;
    return true;
  }

  // ---------------------------------------------------------------- 绑定
  function bind() {
    const who = $("adWho");
    if (who && me) who.textContent = (me.display_name || me.username || "") + "（管理员）";

    const refresh = $("adRefresh");
    if (refresh) refresh.onclick = () => refreshAll();

    document.querySelectorAll("#paneAdmin .ad-navitem").forEach((item) => {
      item.onclick = () => {
        view = item.dataset.view;
        document.querySelectorAll("#paneAdmin .ad-navitem").forEach((x) => {
          x.classList.toggle("on", x === item);
        });
        document.querySelectorAll("#paneAdmin .ad-view").forEach((p) => {
          p.classList.toggle("on", p.id === "adView" + view.charAt(0).toUpperCase() + view.slice(1));
        });
        refreshAll();
      };
    });

    const toggle = $("adNewToggle");
    if (toggle) {
      toggle.onclick = () => {
        const box = $("adNewForm");
        if (!box) return;
        const open = box.style.display !== "none";
        box.style.display = open ? "none" : "block";
        toggle.textContent = open ? "新建账号" : "收起";
        if (!open) {
          const n = $("adNuName");
          if (n) n.focus();
        }
      };
    }
    const cancel = $("adNuCancel");
    if (cancel) cancel.onclick = () => { const b = $("adNewForm"); if (b) b.style.display = "none"; };
    const ok = $("adNuOk");
    if (ok) ok.onclick = createUser;

    // 行内操作按钮（表格是 innerHTML 渲染的，事件委托更省事）
    const rows = $("adUserRows");
    if (rows) {
      rows.addEventListener("click", (e) => {
        const btn = e.target && e.target.closest ? e.target.closest("button[data-act]") : null;
        if (!btn) return;
        const id = btn.dataset.id;
        const act = btn.dataset.act;
        if (act === "role") toggleRole(id);
        else if (act === "status") toggleStatus(id);
        else if (act === "pass") askPassword(id);
        else if (act === "del") removeUser(id, btn.dataset.name, btn);
      });
    }
  }

  // ---------------------------------------------------------------- 取数
  async function loadUsers() {
    const r = await api("/api/admin/users");
    users = (r && Array.isArray(r.users)) ? r.users : [];
    renderUsers();
  }

  async function loadSpaces() {
    const r = await api("/api/admin/spaces");
    spaces = (r && Array.isArray(r.spaces)) ? r.spaces : [];
    renderSpaces();
  }

  async function loadAudit() {
    const r = await api("/api/admin/audit?limit=200");
    audit = (r && Array.isArray(r.entries)) ? r.entries : [];
    renderAudit();
  }

  async function loadConfig() {
    config = await api("/api/admin/config");
    renderConfig();
  }

  async function refreshAll() {
    if (!isAdmin || busy) return;
    busy = true;
    try {
      if (view === "users") await loadUsers();
      else if (view === "spaces") await loadSpaces();
      else if (view === "audit") await loadAudit();
      else if (view === "config") await loadConfig();
    } catch (e) {
      toast("读取失败：" + (e && e.message ? e.message : String(e)));
    } finally {
      busy = false;
    }
  }

  // ---------------------------------------------------------------- 渲染
  function renderUsers() {
    const body = $("adUserRows");
    if (!body) return;
    const cnt = $("adUserCount");
    if (cnt) cnt.textContent = "共 " + users.length + " 个账号";

    // 共享超级账号的提示 —— 摆在最上面，让人一眼看见「这把钥匙是公开的」
    const warn = $("adUserWarn");
    if (warn) {
      const superOn = !!(config && config.super_admin_enabled);
      if (superOn) {
        warn.innerHTML =
          '<div class="ad-warn"><b>共享超级账号</b>：内置的「'
          + esc(config.super_admin_username || "admin")
          + '」口令钉在配置里，同网段任何人拿它登录都是管理员，'
          + '能管账号、能删数据、能跑 Python。要收紧就把配置里的 '
          + '<code>super_admin_password</code> 清空。</div>';
      } else {
        warn.innerHTML = "";
      }
    }

    if (!users.length) {
      body.innerHTML = '<tr><td colspan="7" class="ad-empty">还没有账号。</td></tr>';
      return;
    }
    body.innerHTML = users.map((u) => {
      const isSelf = me && u.username === me.username;
      const roleBadge = u.role === "admin"
        ? '<span class="ad-badge admin">管理员</span>'
        : '<span class="ad-badge">普通用户</span>';
      const stBadge = u.status === "disabled"
        ? '<span class="ad-badge bad">已停用</span>'
        : '<span class="ad-badge off">正常</span>';
      const roleBtn = u.role === "admin"
        ? '<button class="btn sm" data-act="role" data-id="' + esc(u.user_id) + '" title="降为普通用户">降为用户</button>'
        : '<button class="btn sm" data-act="role" data-id="' + esc(u.user_id) + '" title="设为管理员">设为管理员</button>';
      const stBtn = u.status === "disabled"
        ? '<button class="btn sm" data-act="status" data-id="' + esc(u.user_id) + '">启用</button>'
        : '<button class="btn sm" data-act="status" data-id="' + esc(u.user_id) + '">停用</button>';
      const delBtn = isSelf
        ? '<button class="btn sm" data-act="del" data-id="' + esc(u.user_id)
          + '" data-name="' + esc(u.username) + '" disabled title="不能删除自己">删除</button>'
        : '<button class="btn sm" data-act="del" data-id="' + esc(u.user_id)
          + '" data-name="' + esc(u.username) + '">删除</button>';
      return "<tr data-uid=\"" + esc(u.user_id) + "\">"
        + "<td><b>" + esc(u.username) + "</b>" + (isSelf ? "（我）" : "") + "</td>"
        + "<td>" + esc(u.display_name) + "</td>"
        + "<td>" + roleBadge + "</td>"
        + "<td>" + stBadge + "</td>"
        + "<td>" + esc(fmtDay(u.created_at)) + "</td>"
        + "<td>" + esc(fmtDay(u.last_login_at)) + "</td>"
        + '<td><div class="ad-acts">' + roleBtn + stBtn
        + '<button class="btn sm" data-act="pass" data-id="' + esc(u.user_id) + '">改密码</button>'
        + delBtn + "</div></td>"
        + "</tr>";
    }).join("");
  }

  function renderSpaces() {
    const body = $("adSpaceRows");
    if (!body) return;
    if (!spaces.length) {
      body.innerHTML = '<tr><td colspan="4" class="ad-empty">还没有登记的数据集。</td></tr>';
      return;
    }
    body.innerHTML = spaces.map((s) => (
      "<tr>"
      + "<td>" + esc(s.scope_label || s.scope) + "</td>"
      + "<td>" + esc(s.dataset_name) + "</td>"
      + "<td>" + (Number(s.raster_count) || 0) + " 栅格 / " + (Number(s.vector_count) || 0) + " 矢量</td>"
      + "<td>" + esc(s.path || "") + "</td>"
      + "</tr>"
    )).join("");
  }

  function renderAudit() {
    const body = $("adAuditRows");
    if (!body) return;
    if (!audit.length) {
      body.innerHTML = '<tr><td colspan="4" class="ad-empty">还没有审计记录。</td></tr>';
      return;
    }
    body.innerHTML = audit.map((a) => (
      "<tr>"
      + "<td>" + esc(fmtTime(a.at)) + "</td>"
      + "<td>" + esc(a.username || "—") + "</td>"
      + "<td>" + esc(a.action || "") + "</td>"
      + "<td>" + esc(a.target || "") + "</td>"
      + "</tr>"
    )).join("");
  }

  function renderConfig() {
    const body = $("adCfgBody");
    if (!body || !config) return;

    const warn = $("adCfgWarn");
    if (warn) {
      const parts = [];
      if (config.super_admin_enabled) {
        parts.push("内置共享超级账号 <b>" + esc(config.super_admin_username || "admin")
          + "</b> 已启用 —— 同网段任何人都能用它拿到管理员权限。");
      }
      if (config.console_access === "user") {
        parts.push("Python 控制台对<b>所有登录用户</b>开放 —— 任何登录的人都能在这台机器上执行 Python 代码。");
      } else if (config.console_access === "off") {
        parts.push("Python 控制台已关闭。");
      } else {
        parts.push("Python 控制台<b>仅管理员</b>可用。");
      }
      warn.innerHTML = parts.length
        ? '<div class="ad-warn">' + parts.join("<br>") + "</div>"
        : "";
    }

    const rows = [
      ["管理员账号", config.admin_username || "—"],
      ["共享超级账号", config.super_admin_enabled
        ? (config.super_admin_username || "admin") + "（已启用）"
        : "未启用"],
      ["Python 控制台", fmtConsoleAccess(config.console_access)],
      ["要求登录", config.require_login ? "是" : "否（免登录模式）"],
      ["自助注册", config.allow_registration ? "开放" : "关闭"],
      ["监听地址", (config.host || "0.0.0.0") + ":" + (config.port || 8848)],
      ["数据根目录", config.data_root || "—"],
      ["共享空间", config.shared_root || "—"],
      ["用户空间", config.users_root || "—"],
      ["单次上传上限", (Number(config.max_upload_mb) || 0) + " MB"],
    ];
    body.innerHTML = rows.map((r) => (
      "<dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd>"
    )).join("");
  }

  // ---------------------------------------------------------------- 动作
  async function createUser() {
    const name = ($("adNuName") && $("adNuName").value.trim()) || "";
    const pass = ($("adNuPass") && $("adNuPass").value) || "";
    const role = ($("adNuRole") && $("adNuRole").value) || "user";
    const disp = ($("adNuDisp") && $("adNuDisp").value.trim()) || "";
    const hint = $("adNuHint");
    if (name.length < 3) { if (hint) hint.textContent = "用户名至少 3 个字符"; return; }
    if (pass.length < 6) { if (hint) hint.textContent = "口令至少 6 位"; return; }
    try {
      await api("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: name, password: pass, role: role, display_name: disp }),
      });
      toast("已创建账号 " + name);
      ["adNuName", "adNuPass", "adNuDisp"].forEach((id) => { if ($(id)) $(id).value = ""; });
      const box = $("adNewForm");
      if (box) box.style.display = "none";
      const tg = $("adNewToggle");
      if (tg) tg.textContent = "新建账号";
      await loadUsers();
    } catch (e) {
      if (hint) hint.textContent = e && e.message ? e.message : String(e);
    }
  }

  async function patchUser(id, patch, okMsg) {
    try {
      await api("/api/admin/users/" + encodeURIComponent(id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      toast(okMsg);
      await loadUsers();
    } catch (e) {
      toast("操作失败：" + (e && e.message ? e.message : String(e)));
    }
  }

  function toggleRole(id) {
    const u = users.find((x) => x.user_id === id);
    if (!u) return;
    const next = u.role === "admin" ? "user" : "admin";
    patchUser(id, { role: next },
      next === "admin" ? ("已把 " + u.username + " 设为管理员") : ("已把 " + u.username + " 降为普通用户"));
  }

  function toggleStatus(id) {
    const u = users.find((x) => x.user_id === id);
    if (!u) return;
    const next = u.status === "disabled" ? "active" : "disabled";
    patchUser(id, { status: next },
      next === "disabled" ? ("已停用 " + u.username) : ("已启用 " + u.username));
  }

  /** 改密码：就地展开一个输入框，不用 window.prompt（无头浏览器里 prompt 是死的）。 */
  function askPassword(id) {
    const row = document.querySelector('#adUserRows tr[data-uid="' + id + '"]');
    const u = users.find((x) => x.user_id === id);
    if (!row || !u) return;
    if (row.querySelector(".ad-passbox")) return;   // 已经展开了就别再来一次
    const cell = row.querySelector("td:last-child");
    if (!cell) return;
    const box = document.createElement("div");
    box.className = "ad-passbox";
    box.innerHTML =
      '<input type="text" placeholder="新口令（至少 6 位）" style="font:inherit;font-size:11.5px;'
      + 'padding:3px 5px;border:1px solid var(--rule-2);border-radius:3px;width:130px" />'
      + '<button class="btn sm primary" data-act="passok" data-id="' + esc(id) + '">确定</button>'
      + '<button class="btn sm" data-act="passcancel">取消</button>';
    cell.appendChild(box);
    const input = box.querySelector("input");
    if (input) input.focus();
    box.querySelector("[data-act=passok]").onclick = () => {
      const val = (input && input.value) || "";
      if (val.length < 6) { toast("口令至少 6 位"); return; }
      patchUser(id, { password: val }, "已修改 " + u.username + " 的口令");
    };
    box.querySelector("[data-act=passcancel]").onclick = () => box.remove();
  }

  /** 删账号：两步确认，不 window.confirm。
   *
   *  一是 window.confirm 在无头浏览器里是死的（自动返回 false，测试没法验，
   *   某些嵌入环境干脆没有这个函数）；二是删除账号不可逆，值得让人多想一秒。
   */
  async function removeUser(id, name, btn) {
    if (btn && !btn.dataset.armed) {
      btn.dataset.armed = "1";
      btn.textContent = "确认删除？";
      btn.classList.add("danger");
      setTimeout(() => {
        if (!btn.dataset.armed) return;
        btn.dataset.armed = "";
        btn.textContent = "删除";
        btn.classList.remove("danger");
      }, 6000);
      return;
    }
    if (btn) { btn.dataset.armed = ""; btn.classList.remove("danger"); }
    try {
      await api("/api/admin/users/" + encodeURIComponent(id), { method: "DELETE" });
      toast("已删除 " + (name || ""));
      await loadUsers();
    } catch (e) {
      toast("删除失败：" + (e && e.message ? e.message : String(e)));
    }
  }

  // ---------------------------------------------------------------- 启动
  async function boot() {
    if (typeof api !== "function") {
      // index.html 的内联脚本还没跑完，等页面加载完再问
      window.addEventListener("load", boot, { once: true });
      return;
    }
    let data = null;
    try {
      data = await api("/api/auth/me");
    } catch (e) {
      return;   // 没登录 / 免登录模式 → 安静地不显示这个分区
    }
    const p = (data && data.principal) || null;
    if (!p || !p.is_admin) return;   // 普通用户：页签根本不出现
    isAdmin = true;
    me = p;
    if (!mount()) return;
    // 配置和用户一起拉：用户表里要显示「共享超级账号」的提示，
    // 所以先把 config 拿到手，再渲染用户表。
    try { config = await api("/api/admin/config"); } catch (e) { config = null; }
    await refreshAll();
  }

  // 自检（tests/check_webui_admin.py）和浏览器控制台里要能读状态，
  // 顺带也方便排查「普通用户为什么看不到」。
  window.GISDS_ADMIN = {
    state: () => ({
      isAdmin: isAdmin,
      mounted: mounted,
      view: view,
      userCount: users.length,
      spaceCount: spaces.length,
      auditCount: audit.length,
      hasConfig: !!config,
    }),
    open: () => {
      if (typeof switchTab === "function") switchTab(PANE_ID);
    },
    refresh: refreshAll,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
