/* ==========================================================================
 * AI 助手（网页端）
 *
 * 后端 /api/ai/* 已经把「模型 + 工具 + 权限」都串好了，这里只管界面：
 * 一个贴在右栏的聊天面板 —— 配置模型、发问题、看模型调了哪些工具。
 *
 * 自包含：样式、DOM、逻辑都在这个文件里，挂在 index.html 末尾一行
 * <script src="/static/ai.js"></script>，跟 panels.js / console.js 一个路子。
 *
 * 版面按「野外记录本」来排：
 *   顶上一条页眉（名字 + 当前用的哪份配置 + 设置开关）
 *   下面是设置页（一行一个项，标签在左、控件在右）
 *   再下面是记录区：我问的、它答的、它查了什么
 *   最底下是落笔的地方
 *
 * 权限：所有**登录用户**都能用（产品定死，不可配置）。页面加载时先问一次
 * GET /api/ai/config，403 就**把标签页整个撤掉** —— 安静地当作没有这个
 * 功能，不弹窗不报错（跟 console.js 一个做法）。
 *
 * API Key：只存在这台电脑的 localStorage 里，随请求发给服务端。
 * 服务端存的那份（管理员配的默认）**永远不会下发到浏览器**，
 * 这里只拿得到 has_key。
 * ========================================================================== */
(function () {
  "use strict";

  const KEY_STORE = "gisds.ai";          // 浏览器侧的临时配置
  const CHAT_TIMEOUT = 180000;           // 模型可能很慢，超时给足 3 分钟
  const PANE_ID = "paneAI";

  //: 空态里的三个例句。点一下填进输入框（不自动发 —— 发一次就是一份钱）。
  const EXAMPLES = [
    "现在有哪些数据集？",
    "示例数据里的分类图层，各类各占多少面积？",
    "列一下可以用的 Processing 算法",
  ];

  // ---------------------------------------------------------------- 状态

  let cfg = null;        // 服务端默认配置（没有 key 明文）
  let allowed = false;   // 当前用户能不能用
  let mounted = false;   // 标签页建好没有
  let busy = false;      // 有没有正在问
  const history = [];    // 对话历史（只留文本）

  // ---------------------------------------------------------------- 工具

  const byId = (id) => document.getElementById(id);

  const ENT = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
  };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ENT[c]);
  }

  /** 调后端。AI 请求自己写一份，不复用全局 api()：
   *  全局那份 20 秒超时 + 自动重试 —— 对聊天都不合适
   *  （模型慢是常态；重试一次就是多花一份钱）。 */
  async function aiFetch(path, body, timeoutMs) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs || CHAT_TIMEOUT);
    try {
      const res = await fetch(API + path, {
        method: body === undefined ? "GET" : "POST",
        signal: ctl.signal,
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + sessionToken(),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let data = null;
      try {
        data = await res.json();
      } catch (e) {
        data = null;
      }
      if (!res.ok) {
        let msg = res.status + " " + res.statusText;
        if (data) {
          if (typeof data.message === "string") msg = data.message;
          else if (typeof data.detail === "string") msg = data.detail;
          else if (data.detail) msg = JSON.stringify(data.detail);
        }
        throw new Error(msg);
      }
      return data;
    } catch (err) {
      if (err && err.name === "AbortError") {
        throw new Error("请求超时：模型太久没回，或网络不通");
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  // ---------------------------------------------------------------- 样式

  const CSS = `
#paneAI{display:none;flex-direction:column;overflow:hidden;background:var(--panel)}
#paneAI.on{display:flex}

/* ---- 页眉 ---- */
#paneAI .ai-head{
  display:flex;align-items:center;gap:8px;flex:0 0 auto;
  padding:0 11px;height:31px;background:var(--panel-2);
  border-bottom:1px solid var(--rule);
}
#paneAI .ai-head .ai-name{
  font-size:11px;font-weight:600;letter-spacing:.6px;color:var(--ink-2);
}
#paneAI .ai-head .ai-state{
  margin-left:auto;font-family:var(--mono);font-size:10px;letter-spacing:.02em;
  color:var(--ink-3);border:1px solid var(--rule);background:var(--panel);
  border-radius:8px;padding:0 6px;line-height:15px;
}
#paneAI .ai-head .ai-state.on{color:var(--moss-2);border-color:#B9D3C4;background:var(--moss-soft)}
#paneAI .ai-head .ai-state.bad{color:var(--rust);border-color:#E0C4B8;background:var(--rust-soft)}
#paneAI .ai-cfgbtn{
  font:inherit;font-size:11px;line-height:1;padding:3px 8px;cursor:pointer;
  background:var(--panel);border:1px solid var(--rule-2);border-top-color:#C6CEC2;
  border-radius:2px;color:var(--ink-2);
}
#paneAI .ai-cfgbtn:hover{background:var(--panel-3);color:var(--ink)}

/* ---- 设置页 ---- */
#paneAI .ai-sheet{
  flex:0 0 auto;display:none;padding:10px 11px 9px;
  background:var(--panel);border-bottom:1px solid var(--rule);
}
#paneAI .ai-sheet.show{display:block}
#paneAI .ai-frow{
  display:grid;grid-template-columns:56px 1fr;gap:8px;align-items:center;margin-bottom:6px;
}
#paneAI .ai-frow > label{
  font-size:10.5px;letter-spacing:.4px;color:var(--ink-3);text-align:right;
}
#paneAI .ai-frow select,#paneAI .ai-frow input{
  width:100%;min-width:0;box-sizing:border-box;font:inherit;font-size:12px;
  background:var(--panel-2);border:1px solid var(--rule);border-bottom-color:var(--rule-2);
  color:var(--ink);border-radius:2px;padding:4px 6px;outline:none;
}
#paneAI .ai-frow select:focus,#paneAI .ai-frow input:focus{
  background:var(--panel);border-color:var(--rule-2);border-bottom-color:var(--moss);
}
#paneAI .ai-frow input::placeholder{color:#9AA39A}
#paneAI .ai-frow input:disabled,#paneAI .ai-frow select:disabled{color:var(--ink-3)}
#paneAI .ai-use{
  display:flex;align-items:flex-start;gap:6px;margin:9px 0 0;cursor:pointer;
  font-size:11.5px;color:var(--ink-2);line-height:1.5;
}
#paneAI .ai-use input{width:13px;height:13px;margin:1px 0 0;accent-color:var(--moss);cursor:pointer;flex:0 0 auto}
#paneAI .ai-note{
  margin-top:7px;font-size:10.5px;line-height:1.6;color:var(--ink-3);
  padding-left:9px;border-left:1px solid var(--rule);
}

/* ---- 记录区 ---- */
#paneAI .ai-log{flex:1 1 auto;min-height:0;overflow:auto;padding:0}
#paneAI .ai-msg{padding:9px 11px;border-bottom:1px solid var(--rule-3)}
#paneAI .ai-role{
  font-size:9.5px;letter-spacing:.9px;color:var(--ink-3);margin-bottom:3px;
  text-transform:uppercase;
}
#paneAI .ai-msg.user{background:var(--panel-2);border-left:2px solid var(--moss)}
#paneAI .ai-body{font-size:12.5px;line-height:1.68;color:var(--ink);white-space:pre-wrap;word-break:break-word}
#paneAI .ai-body.err{color:var(--rust)}
#paneAI .ai-body.busy{color:var(--ink-3)}

/* 工具调用：一条野外记录 */
#paneAI .ai-step{border-bottom:1px solid var(--rule-3);background:var(--panel)}
#paneAI .ai-step > summary{
  list-style:none;cursor:pointer;padding:6px 11px;display:flex;align-items:center;gap:7px;
  font-size:11.5px;color:var(--ink-2);
}
#paneAI .ai-step > summary::-webkit-details-marker{display:none}
#paneAI .ai-step > summary:hover{background:var(--panel-2)}
#paneAI .ai-step .ai-tick{
  width:1px;height:11px;background:var(--tick);flex:0 0 auto;
}
#paneAI .ai-step .ai-tool{
  font-family:var(--mono);font-size:11px;color:var(--moss-2);letter-spacing:.01em;
}
#paneAI .ai-step .ai-badge{
  margin-left:auto;font-size:9.5px;letter-spacing:.4px;color:var(--ink-3);
  border:1px solid var(--rule);border-radius:8px;padding:0 5px;line-height:14px;
}
#paneAI .ai-step .ai-badge.bad{color:var(--rust);border-color:#E0C4B8;background:var(--rust-soft)}
#paneAI .ai-step .ai-detail{padding:2px 11px 9px 19px}
#paneAI .ai-args{
  font-family:var(--mono);font-size:10.5px;line-height:1.75;color:var(--ink-3);
  font-variant-numeric:tabular-nums;
}
#paneAI .ai-args div{display:flex;gap:8px}
#paneAI .ai-args .k{color:var(--ink-3);flex:0 0 auto}
#paneAI .ai-args .v{color:var(--ink-2);word-break:break-all;min-width:0}
#paneAI .ai-out{
  margin-top:5px;padding:6px 8px;background:var(--panel-2);
  border:1px solid var(--rule-3);border-radius:2px;
  font-family:var(--mono);font-size:10.5px;line-height:1.6;color:var(--ink-2);
  white-space:pre-wrap;word-break:break-word;max-height:210px;overflow:auto;
  font-variant-numeric:tabular-nums;
}
#paneAI .ai-step img{
  display:block;max-width:100%;margin:6px 0 0;border:1px solid var(--rule);
}

/* ---- 空态 ---- */
#paneAI .ai-empty{
  padding:20px 14px 18px;
  background-image:
    linear-gradient(rgba(120,132,118,.07) 1px,transparent 1px),
    linear-gradient(90deg,rgba(120,132,118,.07) 1px,transparent 1px);
  background-size:16px 16px;
}
#paneAI .ai-empty .ai-lead{font-size:12.5px;line-height:1.7;color:var(--ink-2)}
#paneAI .ai-empty .ai-lead b{color:var(--ink);font-weight:600}
#paneAI .ai-ex{
  margin-top:11px;display:flex;flex-direction:column;gap:5px;align-items:flex-start;
}
#paneAI .ai-ex button{
  font:inherit;font-size:11.5px;line-height:1.5;text-align:left;cursor:pointer;
  background:var(--panel);border:1px solid var(--rule);border-left:2px solid var(--moss);
  color:var(--ink-2);border-radius:0 2px 2px 0;padding:5px 9px;max-width:100%;
}
#paneAI .ai-ex button:hover{background:var(--panel-2);color:var(--ink)}

/* ---- 落笔处 ---- */
#paneAI .ai-compose{
  flex:0 0 auto;padding:8px 11px 10px;background:var(--panel-2);
  border-top:1px solid var(--rule);
}
#paneAI .ai-compose textarea{
  width:100%;box-sizing:border-box;resize:vertical;min-height:52px;
  background:var(--panel);border:1px solid var(--rule);border-bottom-color:var(--rule-2);
  color:var(--ink);border-radius:2px;padding:6px 7px;font:inherit;font-size:12.5px;
  line-height:1.55;outline:none;display:block;
}
#paneAI .ai-compose textarea:focus{border-color:var(--rule-2);border-bottom-color:var(--moss)}
#paneAI .ai-compose textarea::placeholder{color:#9AA39A}
#paneAI .ai-bar{display:flex;align-items:center;gap:6px;margin-top:6px}
#paneAI .ai-bar .spacer{flex:1}
#paneAI .ai-state{
  font-size:10.5px;color:var(--ink-3);font-family:var(--mono);
}
`;

  function injectStyle() {
    const style = document.createElement("style");
    style.id = "ai-style";
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  // ---------------------------------------------------------------- 浏览器侧配置

  function loadLocal() {
    try {
      const raw = localStorage.getItem(KEY_STORE);
      if (!raw) return {};
      const obj = JSON.parse(raw);
      return obj && typeof obj === "object" ? obj : {};
    } catch (e) {
      return {};
    }
  }

  function saveLocal(patch) {
    const next = Object.assign(loadLocal(), patch);
    try {
      localStorage.setItem(KEY_STORE, JSON.stringify(next));
    } catch (e) {
      /* 隐私模式下写不了，忽略 —— 只是这次不记住 */
    }
    return next;
  }

  function clearLocal() {
    try {
      localStorage.removeItem(KEY_STORE);
    } catch (e) {
      /* 同上 */
    }
  }

  // ---------------------------------------------------------------- 界面

  function mount() {
    if (mounted) return true;
    const tabs = document.querySelector("aside.right .tabs");
    const aside = document.querySelector("aside.right");
    if (!tabs || !aside) return false;
    if (document.getElementById(PANE_ID)) return true;

    const tab = document.createElement("div");
    tab.className = "tab";
    tab.dataset.pane = PANE_ID;
    tab.textContent = "AI 助手";
    tab.title = "用大模型驱动这套 GIS 能力";
    tabs.appendChild(tab);

    const pane = document.createElement("div");
    pane.className = "tabpane";
    pane.id = PANE_ID;
    pane.innerHTML =
      '<div class="ai-head">' +
      '<span class="ai-name">AI 助手</span>' +
      '<span class="ai-state" id="aiCfgState">—</span>' +
      '<button class="ai-cfgbtn" id="aiCfgToggle" title="模型设置">设置</button>' +
      "</div>" +
      '<div class="ai-sheet" id="aiCfgBody">' +
      '<div class="ai-frow"><label for="aiProvider">协议</label>' +
      '<select id="aiProvider"><option value="openai">OpenAI 兼容</option>' +
      '<option value="anthropic">Anthropic 原生</option></select></div>' +
      '<div class="ai-frow"><label for="aiBaseUrl">接口</label>' +
      '<input type="text" id="aiBaseUrl" placeholder="留空用默认" /></div>' +
      '<div class="ai-frow"><label for="aiModel">模型</label>' +
      '<input type="text" id="aiModel" placeholder="留空用默认" /></div>' +
      '<div class="ai-frow"><label for="aiKey">API Key</label>' +
      '<input type="password" id="aiKey" placeholder="只存在这台电脑" /></div>' +
      '<label class="ai-use"><input type="checkbox" id="aiUseServer" checked />' +
      "<span>用服务端默认（管理员配好的那份）</span></label>" +
      '<div class="ai-note" id="aiCfgNote"></div>' +
      "</div>" +
      '<div class="ai-log" id="aiLog"></div>' +
      '<div class="ai-compose">' +
      '<textarea id="aiText" rows="3" placeholder="问一句…（Enter 发送，Shift+Enter 换行）"></textarea>' +
      '<div class="ai-bar">' +
      '<button class="btn primary" id="aiSend">发送</button>' +
      '<button class="btn" id="aiClear">清空</button>' +
      '<span class="spacer"></span>' +
      '<span class="ai-state" id="aiState">—</span>' +
      "</div></div>";

    aside.appendChild(pane);
    tab.onclick = () => {
      if (typeof switchTab === "function") switchTab(PANE_ID);
    };

    bindEvents();
    renderLocal();
    mounted = true;
    return true;
  }

  function unmount() {
    const tab = document.querySelector('aside.right .tab[data-pane="' + PANE_ID + '"]');
    const pane = document.getElementById(PANE_ID);
    if (tab && tab.parentNode) tab.parentNode.removeChild(tab);
    if (pane && pane.parentNode) pane.parentNode.removeChild(pane);
    mounted = false;
  }

  function bindEvents() {
    const toggle = byId("aiCfgToggle");
    if (toggle) {
      toggle.onclick = () => {
        const body = byId("aiCfgBody");
        if (!body) return;
        body.classList.toggle("show");
        toggle.textContent = body.classList.contains("show") ? "收起" : "设置";
      };
    }

    const useServer = byId("aiUseServer");
    if (useServer) {
      useServer.onchange = () => {
        const local = !useServer.checked;
        ["aiProvider", "aiBaseUrl", "aiModel", "aiKey"].forEach((id) => {
          const el = byId(id);
          if (el) el.disabled = !local;
        });
        renderCfgNote();
      };
    }

    ["aiProvider", "aiBaseUrl", "aiModel"].forEach((id) => {
      const el = byId(id);
      if (!el) return;
      el.oninput = () => {
        saveLocal({
          provider: byId("aiProvider").value,
          base_url: byId("aiBaseUrl").value.trim(),
          model: byId("aiModel").value.trim(),
        });
        renderCfgNote();
      };
    });

    const key = byId("aiKey");
    if (key) {
      key.oninput = () => {
        saveLocal({ api_key: key.value });
        renderCfgNote();
      };
    }

    const send = byId("aiSend");
    if (send) send.onclick = () => submit();

    const clear = byId("aiClear");
    if (clear) {
      clear.onclick = () => {
        history.length = 0;
        const log = byId("aiLog");
        if (log) log.innerHTML = emptyHtml();
        const txt = byId("aiText");
        if (txt) txt.value = "";
        setState("—");
      };
    }

    const text = byId("aiText");
    if (text) {
      text.onkeydown = (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          submit();
        }
      };
    }
  }

  function setState(msg) {
    const el = byId("aiState");
    if (el) el.textContent = msg;
  }

  function renderLocal() {
    const local = loadLocal();
    const useServer = byId("aiUseServer");
    const hasLocal = !!(local.api_key || local.base_url || local.model);
    if (useServer) useServer.checked = !hasLocal;
    const provider = byId("aiProvider");
    if (provider) provider.value = local.provider || "openai";
    const base = byId("aiBaseUrl");
    if (base) base.value = local.base_url || "";
    const model = byId("aiModel");
    if (model) model.value = local.model || "";
    const key = byId("aiKey");
    if (key) key.value = local.api_key || "";
    ["aiProvider", "aiBaseUrl", "aiModel", "aiKey"].forEach((id) => {
      const el = byId(id);
      // 用服务端默认时，这几项就用不上了（禁掉）
      if (el) el.disabled = !!(useServer && useServer.checked);
    });
    renderCfgNote();
  }

  function renderCfgNote() {
    const note = byId("aiCfgNote");
    if (!note) return;
    const useServer = byId("aiUseServer");
    if (useServer && useServer.checked) {
      const ready = cfg && cfg.has_key;
      note.textContent = ready
        ? "正在用服务端默认配置。管理员改了那边的默认设置，这边立即生效。"
        : "服务端还没配默认 API Key —— 取消勾选，自己填一个 Key 就能用。";
      return;
    }
    const local = loadLocal();
    note.textContent = local.api_key
      ? "Key 只存在这台电脑的浏览器里，不会写进服务端。"
      : "还没填 Key。填了才能用。";
  }

  function renderCfgState() {
    const el = byId("aiCfgState");
    if (!el || !cfg) return;
    const local = loadLocal();
    const usingLocal = !!(local.api_key || local.base_url || local.model);
    el.classList.remove("on", "bad");
    if (usingLocal) {
      el.textContent = "本机配置";
      el.classList.add("on");
    } else if (cfg.has_key) {
      el.textContent = "服务端默认";
      el.classList.add("on");
    } else {
      el.textContent = "未配置";
      el.classList.add("bad");
    }
  }

  // ---------------------------------------------------------------- 消息

  function logEl() {
    return byId("aiLog");
  }

  function clearEmpty() {
    const empty = byId("aiEmpty");
    if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
  }

  function emptyHtml() {
    return (
      '<div class="ai-empty" id="aiEmpty">' +
      '<div class="ai-lead">用大白话问就行 —— <b>它会自己去查数据、算数、跑算法</b>，' +
      "查了什么过程会摊开给你看。</div>" +
      '<div class="ai-ex">' +
      EXAMPLES.map(
        (t) => '<button type="button" data-ex="' + esc(t) + '">' + esc(t) + "</button>"
      ).join("") +
      "</div></div>"
    );
  }

  function addUser(text) {
    const log = logEl();
    if (!log) return;
    clearEmpty();
    const div = document.createElement("div");
    div.className = "ai-msg user";
    div.innerHTML =
      '<div class="ai-role">我</div><div class="ai-body">' + esc(text) + "</div>";
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function addAssistant(text, isError) {
    const log = logEl();
    if (!log) return;
    clearEmpty();
    const div = document.createElement("div");
    div.className = "ai-msg";
    div.innerHTML =
      '<div class="ai-role">助手</div><div class="ai-body' +
      (isError ? " err" : "") +
      '">' +
      esc(text) +
      "</div>";
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function addThinking() {
    const log = logEl();
    if (!log) return null;
    clearEmpty();
    const div = document.createElement("div");
    div.className = "ai-msg";
    div.innerHTML =
      '<div class="ai-role">助手</div><div class="ai-body busy">正在想…</div>';
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  /** 参数按「键 值」一行一行排开 —— 比一坨 JSON 好读多了。 */
  function argsHtml(args) {
    const keys = args && typeof args === "object" ? Object.keys(args) : [];
    if (!keys.length) return "";
    return (
      '<div class="ai-args">' +
      keys
        .map(
          (k) =>
            '<div><span class="k">' +
            esc(k) +
            '</span><span class="v">' +
            esc(fmtValue(args[k])) +
            "</span></div>"
        )
        .join("") +
      "</div>"
    );
  }

  function fmtValue(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "object") return JSON.stringify(v);
    if (typeof v === "boolean") return v ? "是" : "否";
    return String(v);
  }

  function addSteps(steps) {
    const log = logEl();
    if (!log || !steps || !steps.length) return;
    clearEmpty();
    steps.forEach((step) => {
      const box = document.createElement("details");
      box.className = "ai-step";
      const bad = step.ok === false;
      box.innerHTML =
        "<summary>" +
        '<span class="ai-tick"></span>' +
        '<span class="ai-tool">' +
        esc(step.name) +
        "</span>" +
        '<span class="ai-badge' +
        (bad ? " bad" : "") +
        '">' +
        (bad ? "失败" : "已查") +
        "</span></summary>" +
        '<div class="ai-detail">' +
        argsHtml(step.arguments) +
        '<div class="ai-out">' +
        esc(String(step.result == null ? "" : step.result)) +
        "</div>" +
        (step.image ? '<img alt="渲染结果" src="' + esc(step.image) + '" />' : "") +
        "</div>";
      log.appendChild(box);
    });
    log.scrollTop = log.scrollHeight;
  }

  // ---------------------------------------------------------------- 发送

  function buildBody(text) {
    const body = { message: text, history: history.slice() };
    const useServer = byId("aiUseServer");
    if (useServer && !useServer.checked) {
      const local = loadLocal();
      if (local.provider) body.provider = local.provider;
      if (local.base_url) body.base_url = local.base_url;
      if (local.model) body.model = local.model;
      if (local.api_key) body.api_key = local.api_key;
    }
    return body;
  }

  async function submit() {
    if (busy) return;
    const text = byId("aiText");
    if (!text) return;
    const message = text.value.trim();
    if (!message) return;

    busy = true;
    const send = byId("aiSend");
    if (send) send.disabled = true;
    text.value = "";
    setState("请求中…");
    addUser(message);
    const pending = addThinking();

    try {
      const data = await aiFetch("/api/ai/chat", buildBody(message));
      if (pending && pending.parentNode) pending.parentNode.removeChild(pending);
      addSteps(data.steps || []);
      const answer = (data.answer || "").trim() || "（模型没给文字）";
      addAssistant(answer, !data.ok);
      history.push({ role: "user", content: message });
      history.push({ role: "assistant", content: answer });
      setState(data.rounds > 1 ? "第 " + data.rounds + " 轮" : "完成");
    } catch (err) {
      if (pending && pending.parentNode) pending.parentNode.removeChild(pending);
      addAssistant("出错了：" + (err && err.message ? err.message : String(err)), true);
      setState("失败");
    } finally {
      busy = false;
      if (send) send.disabled = false;
      const box = byId("aiText");
      if (box) box.focus();
    }
  }

  // ---------------------------------------------------------------- 启动

  async function boot() {
    injectStyle();
    let data = null;
    try {
      data = await aiFetch("/api/ai/config");
    } catch (e) {
      // 没登录 / 免登录模式 → 安静地不显示这个功能
      return;
    }
    cfg = data || null;
    allowed = true;
    mount();
    const log = logEl();
    if (log && !log.innerHTML.trim()) log.innerHTML = emptyHtml();
    renderCfgState();
    renderCfgNote();
  }

  // ---------------------------------------------------------------- 例句

  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest("#aiEmpty .ai-ex button") : null;
    if (!btn) return;
    const box = byId("aiText");
    if (!box) return;
    box.value = btn.getAttribute("data-ex") || "";
    box.focus();
  });

  // 自检（tests/check_ai.py / check_webui*.py）要能读面板内部状态，
  // 顺带也方便在浏览器控制台里排查，所以挂一个句柄。
  window.GISDS_AI = {
    state: () => ({
      allowed: allowed,
      mounted: mounted,
      busy: busy,
      history: history.length,
      provider: cfg ? cfg.provider : null,
      hasServerKey: cfg ? !!cfg.has_key : false,
    }),
    open: () => {
      if (typeof switchTab === "function") switchTab(PANE_ID);
    },
    ask: (text) => {
      const box = byId("aiText");
      if (!box) return Promise.reject(new Error("面板没挂上"));
      box.value = String(text || "");
      return submit();
    },
    clearLocal: clearLocal,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
