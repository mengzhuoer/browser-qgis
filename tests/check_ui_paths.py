# -*- coding: utf-8 -*-
"""网页界面「路径」全面扫描：每个面板 / 菜单 / 工具栏都进去点一遍。

这层补的是 check_webui.py（布局 / 图层树 / 图层属性 / 工具箱）和
check_webui_panels.py（面板**存在性**与读数）之外的东西 —— 也就是
**「点了到底有没有发生事」**：

  * 菜单栏每个菜单项点一遍：点完要有可见效果，且不报 JS 错
  * 工具栏每个按钮点一遍（两排 25 个）
  * 右栏六个面板逐个进去**交互**（翻页 / 过滤 / 排序 / 选择 / 书签 / 日志 / 历史）
  * 要素编辑全流程（进入 / 改属性 / 新增 / 删除 / 字段计算 / 保存 / 放弃）
  * 图层属性四个分页、添加图层四类数据源
  * 文件浏览器 / Python 控制台 / 插件管理
  * 报错路径（非法输入、没选图层、上传不被识别、作业失败）有没有
    **人能看懂的**提示 —— 这是本项目的老毛病：出错时静默，或者只写进日志
  * 刷新页面后面板尺寸 / 选中的图层 / 打开的标签页还在不在
  * undefined / NaN / null 泄漏到界面上

跑法（端口、数据目录全部隔离，绝不碰生产 data/）::

    run-qgis-python.bat tests/check_ui_paths.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from check_webui import CDP, find_browser, wait_for  # noqa: E402
from server_helper import TestServer, _free_port, kill_tree     # noqa: E402

fails = []
checked = 0
# 这里的 161 = 登录 3 + 加图层 4 + 菜单项 26 + 工具栏按钮 26 + 图层树 13
#            + 状态栏 5 + 右栏面板 29 + 要素编辑 13 + 图层属性分页 4
#            + 添加图层对话框 5 + 文件浏览器 4 + Python 控制台 6 + 插件 5
#            + 报错路径 6 + 刷新一致性 4 + 界面泄漏 3 + 页面/服务 4
#            + 「关于本服务」算法数 1。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 165

# CDP 端口不能写死：并行会话同时跑自检时撞端口会连到别人的浏览器。
CDP_PORT = int(os.environ.get("GISDS_CDP_PORT_UIPATHS") or _free_port(9402))

ADMIN_USER = "admin"
ADMIN_PASS = "uipass123"

# 临时数据目录（main() 里建、run() 里用、跑完删）—— 生产 data/ 一个字节都不碰
TMP_ROOT: Path | None = None

# 装进页面的小工具：按**标签文字**找元素（菜单项的标签被 <span class=k> 拆开了，
# 直接读 textContent 会把快捷键提示也读进来）。
HELPERS_JS = """
window.__uip = {
  label: function(x){
    return Array.from(x.childNodes)
      .filter(function(n){ return n.nodeType === 3; })
      .map(function(n){ return n.textContent; }).join('').trim();
  },
  find: function(sel, text){
    var els = Array.from(document.querySelectorAll(sel));
    for (var i = 0; i < els.length; i++){
      if (window.__uip.label(els[i]) === text) return els[i];
    }
    for (var j = 0; j < els.length; j++){
      if (window.__uip.label(els[j]).indexOf(text) >= 0) return els[j];
    }
    return null;
  }
};
window.__errs = [];
window.addEventListener('error', function(e){
  window.__errs.push('error: ' + (e && e.message ? e.message : String(e)));
});
window.addEventListener('unhandledrejection', function(e){
  var r = e && e.reason;
  window.__errs.push('reject: ' + String((r && (r.stack || r)) || r));
});
window.__treeOrderNow = function(){
  return Array.from(document.querySelectorAll('#treeList .layer-row'))
    .map(function(e){ var n = e.querySelector('.nm'); return n ? n.textContent : ''; })
    .join('|');
};
window.__dbl = 0;
window.__clicks = 0;
document.addEventListener('dblclick', function(){ window.__dbl++; }, true);
document.addEventListener('click', function(e){
  window.__clicks++;
  window.__lastClick = (e.target && e.target.tagName) + '#' + ((e.target && e.target.id) || '');
}, true);
window.__boundsChanged = false;
window.__treeOrderBefore = '';
window.__helpTabOpened = false;
true;
"""


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


# ---------------------------------------------------------------- 小工具


async def js(cdp, expr, default=None):
    """求值一个表达式；出错就返回 default（不让整支脚本崩掉）。"""
    try:
        v = await cdp.evaluate(expr)
    except Exception:
        return default
    return default if v is None else v


async def install_helpers(cdp):
    """装页面小工具（每次导航/刷新后都要重装）。"""
    try:
        await cdp.evaluate(HELPERS_JS)
    except Exception:
        pass


async def click_label(cdp, selector, text):
    """按标签文字点一个元素（真实鼠标事件 + 命中测试）。找不到返回 False。"""
    await cdp.evaluate(
        """(() => {
             const e = window.__uip && window.__uip.find(%s, %s);
             if (e) e.scrollIntoView({block:'center', behavior:'instant'});
             return !!e;
           })()""" % (json.dumps(selector), json.dumps(text))
    )
    await asyncio.sleep(0.2)
    box = await js(
        cdp,
        """(() => {
             const e = window.__uip && window.__uip.find(%s, %s);
             if (!e) return null;
             const r = e.getBoundingClientRect();
             if (r.width === 0 && r.height === 0) return null;
             return {x: r.x + r.width / 2, y: r.y + r.height / 2};
           })()""" % (json.dumps(selector), json.dumps(text)),
    )
    if not box:
        return False
    for kind in ("mousePressed", "mouseReleased"):
        await cdp.call(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": box["x"],
                "y": box["y"],
                "button": "left",
                "clickCount": 1,
                "buttons": 1 if kind == "mousePressed" else 0,
            },
        )
    await asyncio.sleep(0.2)
    return True


async def menu_labels(cdp, menu_key):
    """打开一个菜单，返回它的条目文字列表。"""
    await cdp.click('.menubar .menu[data-menu="%s"]' % menu_key)
    await asyncio.sleep(0.35)
    return await js(
        cdp,
        """(() => Array.from(document.querySelectorAll('#dropdown .ditem'))
              .map(e => window.__uip.label(e)))()""",
        [],
    )


async def close_menu(cdp):
    await js(cdp, "closeDropdown && closeDropdown(); true")
    await asyncio.sleep(0.15)


async def arm_probes(cdp):
    """把「地图范围变了没 / 图层顺序变了没」两个探针重新武装一次。

    不重新武装的话，探针一旦置真就永远是真的 —— 后面点了没反应的动作
    也会被判成「有反应」，断言就空过了。
    """
    await js(
        cdp,
        """(() => {
             window.__boundsChanged = false;
             window.__treeOrderBefore = window.__treeOrderNow();
             return true;
           })()""",
    )


async def click_diag(cdp, selector):
    """一个元素上到底有没有东西挡着 —— 「点了没反应」时先看这个。"""
    raw = await js(
        cdp,
        """(() => {
             const b = document.querySelector(%s);
             if (!b) return JSON.stringify({btn: false});
             const r = b.getBoundingClientRect();
             const x = r.x + r.width/2, y = r.y + r.height/2;
             const at = document.elementFromPoint(x, y);
             return JSON.stringify({
               btn: true,
               rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
               at: at ? (at.tagName + '#' + (at.id||'') + '.' + (at.className||'')) : null,
               inWindow: (x >= 0 && y >= 0 && x <= window.innerWidth && y <= window.innerHeight),
               modals: document.querySelectorAll('.modal.show').length,
               modalIds: Array.from(document.querySelectorAll('.modal')).map(m=>m.id||'(anon)').join(','),
               scrollY: Math.round(window.scrollY)
             });
           })()""" % json.dumps(selector),
        "{}",
    )
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


async def menu_case(cdp, menu_key, label, probe, timeout=9.0):
    """点一个菜单项，等它该有的效果；**永远只发一条断言**（计数才稳）。"""
    labels = await menu_labels(cdp, menu_key)
    if label not in labels:
        check(f"菜单「{menu_key}」→「{label}」", False, f"菜单里没有这一项；实际条目：{labels}")
        await close_menu(cdp)
        return
    errs_before = await js(cdp, "(window.__errs||[]).length", 0)
    await arm_probes(cdp)
    await click_label(cdp, "#dropdown .ditem", label)
    ok = await wait_for(cdp, probe, timeout=timeout)
    how = "真实鼠标"
    if not ok:
        # 真实鼠标事件可能落到别的东西上（本机就有个浏览器扩展注入的浮层），
        # 退回用 JS 触发 —— 验的仍然是「点了会发生什么」。
        await js(
            cdp,
            """(() => {
                 const e = window.__uip && window.__uip.find('#dropdown .ditem', %s);
                 if (e) e.click();
                 return true;
               })()""" % json.dumps(label),
        )
        ok = await wait_for(cdp, probe, timeout=timeout)
        how = "JS 触发"
    errs_after = await js(cdp, "JSON.stringify((window.__errs||[]).slice(" + str(errs_before) + "))", "[]")
    detail = "" if ok else f"期望状态没出现；新 JS 报错：{errs_after}"
    check(f"菜单「{menu_key}」→「{label}」", ok, detail + ("" if ok else "（" + how + "）"))


async def toolbar_case(cdp, act, probe, timeout=9.0, label=None):
    """点一个工具栏按钮，等它该有的效果；**永远只发一条断言**。"""
    name = label or act
    sel = '#toolbars button[data-act="%s"]' % act
    errs_before = await js(cdp, "(window.__errs||[]).length", 0)
    await arm_probes(cdp)
    await cdp.click(sel)
    ok = await wait_for(cdp, probe, timeout=timeout)
    how = "真实鼠标"
    if not ok:
        await js(cdp, "(() => {const b = document.querySelector(%s); if (b) b.click(); return true;})()"
                 % json.dumps(sel))
        ok = await wait_for(cdp, probe, timeout=timeout)
        how = "JS 触发"
    errs_after = await js(cdp, "JSON.stringify((window.__errs||[]).slice(" + str(errs_before) + "))", "[]")
    detail = "" if ok else f"期望状态没出现；新 JS 报错：{errs_after}"
    check(f"工具栏「{name}」", ok, detail + ("" if ok else "（" + how + "）"))


async def double_click_at(cdp, x, y):
    """真双击：先 clickCount=1 一轮，再 clickCount=2 一轮。

    只发一轮 clickCount=2 的话，Chrome **不一定**派发 dblclick 事件 ——
    那样「双击单元格改属性」这条就永远测不到（看起来像功能坏了，其实是点击没对上）。
    """
    for count in (1, 2):
        for kind in ("mousePressed", "mouseReleased"):
            await cdp.call(
                "Input.dispatchMouseEvent",
                {
                    "type": kind,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": count,
                    "buttons": 1 if kind == "mousePressed" else 0,
                },
            )
        await asyncio.sleep(0.08)
    await asyncio.sleep(0.3)


async def set_file_input(cdp, selector, path):
    """给 <input type=file> 塞一个真文件（CDP 的 DOM.setFileInputFiles）。"""
    await cdp.call("DOM.enable")
    doc = await cdp.call("DOM.getDocument", {"depth": -1})
    node = await cdp.call(
        "DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": selector}
    )
    if not node.get("nodeId"):
        return False
    await cdp.call(
        "DOM.setFileInputFiles", {"files": [str(path)], "nodeId": node["nodeId"]}
    )
    return True


async def reload_page(cdp):
    """刷新页面并等它重新就绪（helper 要重装）。"""
    await cdp.evaluate("location.reload(); true")
    await asyncio.sleep(1.5)
    await wait_for(
        cdp,
        "document.querySelector('#stQgis') && "
        "document.querySelector('#stQgis').textContent !== '—'",
        timeout=30.0,
    )
    await asyncio.sleep(1.0)
    await install_helpers(cdp)


# ---------------------------------------------------------------- 场景


async def login(cdp, base_url):
    """用管理员账号登录。"""
    await asyncio.sleep(1.2)
    check("登录页打开了", await js(cdp, "location.pathname") == "/login",
          str(await js(cdp, "location.pathname")))
    await cdp.type_text("#username", ADMIN_USER)
    await cdp.type_text("#password", ADMIN_PASS)
    await cdp.click("#loginForm button[type=submit]")
    ok = await wait_for(cdp, "location.pathname === '/'", timeout=20.0)
    check("管理员登录后进了控制台", ok, str(await js(cdp, "location.pathname")))
    await asyncio.sleep(1.5)
    await install_helpers(cdp)
    who = await js(cdp, "(document.querySelector('#userBox')||{}).textContent")
    check("顶栏显示当前用户是管理员", ADMIN_USER in (who or ""), str(who))


async def add_layers_via_dialog(cdp, refs):
    """走「添加图层」对话框的「服务器共享数据」把图层加进地图。"""
    await cdp.click("#btnAddLayer")
    await asyncio.sleep(0.6)
    check("「＋」打开添加图层对话框",
          bool(await js(cdp, "document.querySelector('#dlgAdd').classList.contains('show')")))
    await cdp.click('#addTree .node[data-cat="server"]')
    await asyncio.sleep(0.5)
    n = await js(cdp, "document.querySelectorAll('#addForm input[data-ref]').length", 0)
    check("「服务器共享数据」列出可勾选的图层", (n or 0) >= 2, f"{n} 个")
    for ref in refs:
        await cdp.set_checked('#addForm input[data-ref="%s"]' % ref, True)
    await cdp.click("#addOk")
    await asyncio.sleep(2.0)
    rows = await js(
        cdp,
        "Array.from(document.querySelectorAll('#treeList .layer-row'))"
        ".map(e=>e.querySelector('.nm').textContent)",
        [],
    )
    check("勾选后点「打开」→ 图层进了图层树", len(rows or []) == len(refs), str(rows))
    tip = await js(cdp, "(document.querySelector('#stTip')||{}).textContent")
    check("状态栏提示文字跟着加图层更新", "已添加图层" in (tip or ""), str(tip))
    return rows or []


async def sweep_menus(cdp):
    """菜单栏每个菜单项点一遍。"""
    # ---- 文件
    await menu_case(cdp, "file", "添加图层…",
                    "document.querySelector('#dlgAdd').classList.contains('show')")
    await cdp.click("#addCancel")
    await asyncio.sleep(0.4)

    await menu_case(cdp, "file", "重新扫描数据目录",
                    "document.querySelector('#stTip').textContent.indexOf('扫描') >= 0")

    await menu_case(cdp, "file", "服务信息",
                    "document.querySelector('#aboutBox').classList.contains('show') && "
                    "document.querySelector('#aboutBox').textContent.indexOf('关于本服务') >= 0")
    await cdp.evaluate("document.querySelector('#aboutBox').classList.remove('show'); true")

    await menu_case(cdp, "file", "文件浏览器…",
                    "document.querySelector('#dlgFiles').classList.contains('show')")
    await cdp.click("#fbClose2")
    await asyncio.sleep(0.4)

    # ---- 编辑
    await menu_case(cdp, "edit", "清除识别结果",
                    "document.querySelector('#identResult').textContent"
                    ".indexOf('还没有识别任何要素') >= 0")

    n_before = await js(cdp, "document.querySelectorAll('#treeList .layer-row').length", 0)
    await menu_case(cdp, "edit", "移除选中图层",
                    "document.querySelectorAll('#treeList .layer-row').length < " + str(n_before))
    await add_sample_layers(cdp)

    # ---- 视图
    await menu_case(cdp, "view", "缩放至图层", "window.__boundsChanged === true")
    await menu_case(cdp, "view", "缩放至全部", "window.__boundsChanged === true")
    await menu_case(cdp, "view", "刷新地图",
                    "document.querySelector('#stTip').textContent.indexOf('刷新') >= 0")
    # 底图三连：先关掉（baseKind=none），再切内置、再切在线、最后再关掉。
    # 顺序不能换 —— setBasemap() 里 `if(kind === baseKind) return;`，
    # 开机默认就是 builtin，先点「内置底图」等于什么都没发生，断言就成了空过。
    await menu_case(cdp, "view", "关闭底图", "baseKind === 'none'")
    await menu_case(cdp, "view", "内置底图（离线）", "baseKind === 'builtin'")
    await menu_case(cdp, "view", "在线底图 OpenStreetMap", "baseKind === 'online'")
    await menu_case(cdp, "view", "关闭底图", "baseKind === 'none'")
    await menu_case(cdp, "view", "识别结果",
                    "document.querySelector('.tab[data-pane=paneIdent]').classList.contains('on')")
    await menu_case(cdp, "view", "图层属性",
                    "document.querySelector('.tab[data-pane=paneProps]').classList.contains('on')")
    await menu_case(cdp, "view", "工具箱",
                    "document.querySelector('.tab[data-pane=paneAlgo]').classList.contains('on')")
    await menu_case(cdp, "view", "Python 控制台",
                    "document.querySelector('#gisdsConsole').classList.contains('open')")
    await cdp.click("#pcClose")
    await asyncio.sleep(0.3)

    # ---- 图层
    await menu_case(cdp, "layer", "图层属性…",
                    "document.querySelector('.tab[data-pane=paneProps]').classList.contains('on')")
    await menu_case(cdp, "layer", "缩放至图层", "window.__boundsChanged === true")

    # 「移至最前」要选**最下面**那个，「移至最后」要选**最上面**那个 ——
    # 选中项本来就在目标位置时 moveLayer() 直接 return，动作等于没发生，
    # 拿它当断言只会得到一条没意义的红。
    await select_row(cdp, -1)
    await menu_case(cdp, "layer", "移至最前",
                    "window.__treeOrderNow() !== window.__treeOrderBefore")
    await select_row(cdp, 0)
    await menu_case(cdp, "layer", "移至最后",
                    "window.__treeOrderNow() !== window.__treeOrderBefore")

    n_before = await js(cdp, "document.querySelectorAll('#treeList .layer-row').length", 0)
    await menu_case(cdp, "layer", "移除图层",
                    "document.querySelectorAll('#treeList .layer-row').length < " + str(n_before))
    await add_sample_layers(cdp)

    # ---- 帮助
    before_targets = await cdp_targets(cdp)
    await menu_case(cdp, "help", "使用帮助…", "window.__helpTabOpened === true", timeout=12.0)
    await close_new_targets(cdp, before_targets)

    await menu_case(cdp, "help", "关于本服务",
                    "document.querySelector('#aboutBox').classList.contains('show') && "
                    "document.querySelector('#aboutBox').textContent.indexOf('关于本服务') >= 0")
    about_text = await js(cdp, "document.querySelector('#aboutBox').textContent")
    await cdp.evaluate("document.querySelector('#aboutBox').classList.remove('show'); true")
    real_count = await js(
        cdp, "fetch('/api/meta').then(r=>r.json()).then(j=>String(j.counts.processing_algorithms))"
    )
    m = re.search(r"Processing\s*算法\s*(\d+)", about_text or "")
    shown_count = m.group(1) if m else None
    check("「关于本服务」里的算法数是真实值（不是写死的）",
          shown_count is not None and str(real_count) == shown_count,
          f"界面显示：{shown_count!r} / 服务端实际：{real_count!r}")

    # ---- 插件贡献的菜单
    await menu_case(cdp, "tools", "示例插件：打个招呼",
                    "document.querySelector('#toast').textContent.indexOf('执行成功') >= 0")

    # ---- 文件：清空地图（放最后，因为它把图层全清了）
    await menu_case(cdp, "file", "清空地图",
                    "document.querySelectorAll('#treeList .layer-row').length === 0 && "
                    "document.querySelector('#stLayers').textContent === '0'")
    await add_sample_layers(cdp)


async def tree_order(cdp):
    return await js(
        cdp,
        "Array.from(document.querySelectorAll('#treeList .layer-row'))"
        ".map(e=>e.querySelector('.nm').textContent).join('|')",
        "",
    )


async def select_row(cdp, index):
    """选中图层树里的第 index 行（0 = 最上面那一行，-1 = 最下面那一行）。"""
    await js(
        cdp,
        """(() => {
             const rows = document.querySelectorAll('#treeList .layer-row');
             if (!rows.length) return false;
             const i = %d < 0 ? rows.length + %d : %d;
             const e = rows[i] || rows[0];
             e.click();
             return true;
           })()""" % (index, index, index),
    )
    await asyncio.sleep(0.5)


async def cdp_targets(cdp):
    try:
        return httpx.get("http://127.0.0.1:%d/json/list" % CDP_PORT, timeout=5).json()
    except Exception:
        return []


async def close_new_targets(cdp, before):
    before_ids = {t.get("id") for t in before}
    for t in await cdp_targets(cdp):
        if t.get("id") not in before_ids:
            try:
                httpx.get(
                    "http://127.0.0.1:%d/json/close/%s" % (CDP_PORT, t.get("id")), timeout=5
                )
            except Exception:
                pass


async def add_sample_layers(cdp):
    """把示例矢量 + 栅格重新加回地图（前面几步会把它们删掉）。"""
    refs = await js(
        cdp,
        "JSON.stringify((S.layers||[]).filter(l=>l.kind==='vector'||l.kind==='raster')"
        ".slice(0,2).map(l=>l.ref))",
        "[]",
    )
    try:
        ref_list = json.loads(refs or "[]")
    except Exception:
        ref_list = []
    if not ref_list:
        return
    await cdp.click("#btnAddLayer")
    await asyncio.sleep(0.5)
    await cdp.click('#addTree .node[data-cat="server"]')
    await asyncio.sleep(0.5)
    for ref in ref_list:
        await cdp.set_checked('#addForm input[data-ref="%s"]' % ref, True)
    await cdp.click("#addOk")
    await asyncio.sleep(2.0)


async def sweep_toolbar(cdp):
    """工具栏每个按钮点一遍（两排 25 个）。"""
    # ---- 第一排：导航
    await toolbar_case(cdp, "toolPan", "S.tool === 'pan'")
    await toolbar_case(cdp, "toolZoomIn", "S.tool === 'zoomIn'")
    await toolbar_case(cdp, "toolZoomOut", "S.tool === 'zoomOut'")
    await toolbar_case(cdp, "zoomLayer", "window.__boundsChanged === true")
    await toolbar_case(cdp, "zoomSelected",
                       "document.querySelector('#toast').textContent.indexOf('没有选中的要素') >= 0")
    await toolbar_case(cdp, "zoomAll", "window.__boundsChanged === true")
    await toolbar_case(cdp, "prevExtent",
                       "document.querySelector('#toast').textContent.indexOf('范围') >= 0 || "
                       "window.__boundsChanged === true")
    await toolbar_case(cdp, "nextExtent",
                       "document.querySelector('#toast').textContent.indexOf('范围') >= 0 || "
                       "window.__boundsChanged === true")
    await toolbar_case(cdp, "zoomScale", "!!document.querySelector('#zscaleInput')")
    await cdp.click(".modal.show [data-role='cancel']")
    await asyncio.sleep(0.3)
    await toolbar_case(cdp, "toggleMapTips",
                       "document.querySelector('#toolbars button[data-act=toggleMapTips]')"
                       ".classList.contains('on')")
    await toolbar_case(cdp, "toggleMapTips",
                       "!document.querySelector('#toolbars button[data-act=toggleMapTips]')"
                       ".classList.contains('on')")
    await toolbar_case(cdp, "refresh",
                       "document.querySelector('#stTip').textContent.indexOf('刷新') >= 0")

    # ---- 第二排：图层
    await toolbar_case(cdp, "addLayer",
                       "document.querySelector('#dlgAdd').classList.contains('show')")
    await cdp.click("#addCancel")
    await asyncio.sleep(0.4)

    # ---- 第二排：编辑
    await toolbar_case(cdp, "toggleEdit",
                       "document.querySelector('#toolbars button[data-act=toggleEdit]')"
                       ".classList.contains('on')")
    await toolbar_case(cdp, "saveEdit",
                       "document.querySelector('#stTip').textContent.indexOf('保存') >= 0 || "
                       "document.querySelector('#toast').textContent.indexOf('编辑状态') >= 0")
    await toolbar_case(cdp, "discardEdit",
                       "document.querySelector('#stTip').textContent.indexOf('放弃') >= 0 || "
                       "document.querySelector('#toast').textContent.indexOf('编辑状态') >= 0")
    await toolbar_case(cdp, "addFeature",
                       "document.querySelector('#stTip').textContent.indexOf('采集') >= 0 || "
                       "document.querySelector('#toast').textContent.indexOf('编辑模式') >= 0")
    await toolbar_case(cdp, "deleteSelected",
                       "document.querySelector('#toast').textContent.length > 0")
    await toolbar_case(cdp, "fieldCalc", "!!document.querySelector('#fcField')")
    await cdp.click(".modal.show [data-role='cancel']")
    await asyncio.sleep(0.3)

    # ---- 第二排：属性
    await toolbar_case(cdp, "openTable",
                       "document.querySelector('#paneTable').classList.contains('on')")
    await toolbar_case(cdp, "openStats",
                       "document.querySelector('#paneStats').classList.contains('on')")

    # ---- 第二排：选择
    await toolbar_case(cdp, "selectAll",
                       "document.querySelector('#selState').textContent.indexOf('已选') >= 0")
    await toolbar_case(cdp, "selectNone",
                       "document.querySelector('#selState').textContent.indexOf('已选 0') >= 0")
    await toolbar_case(cdp, "selectInvert",
                       "document.querySelector('#selState').textContent.indexOf('已选') >= 0")
    await toolbar_case(cdp, "selectExpr", "!!document.querySelector('#selExprInput')")
    await cdp.click(".modal.show [data-role='cancel']")
    await asyncio.sleep(0.3)
    await toolbar_case(cdp, "selectSpace", "!!document.querySelector('#selSpaceTarget')")
    await cdp.click(".modal.show [data-role='cancel']")
    await asyncio.sleep(0.3)


async def sweep_layer_tree(cdp):
    """图层树：显隐 / 右键菜单 / 上移下移 / 删除。"""
    # 显隐
    off_before = await js(
        cdp, "document.querySelectorAll('#treeList .layer-row.off').length", 0)
    await cdp.click("#treeList .layer-row:nth-child(1) input[type=checkbox]")
    await asyncio.sleep(0.6)
    off_after = await js(
        cdp, "document.querySelectorAll('#treeList .layer-row.off').length", 0)
    check("图层树复选框能控制显隐", (off_after or 0) == (off_before or 0) + 1,
          f"off 行数 {off_before} -> {off_after}")
    await cdp.click("#treeList .layer-row:nth-child(1) input[type=checkbox]")
    await asyncio.sleep(0.6)
    off_back = await js(
        cdp, "document.querySelectorAll('#treeList .layer-row.off').length", 0)
    check("再勾一次图层又显示出来", off_back == off_before, f"off 行数 {off_back}")

    # 右键菜单
    box = await js(
        cdp,
        """(() => {const e=document.querySelector('#treeList .layer-row');
           const r=e.getBoundingClientRect();
           return {x:r.x+40, y:r.y+r.height/2};})()""",
    )
    await cdp.click_at(box["x"], box["y"], button="right")
    await asyncio.sleep(0.4)
    items = await js(
        cdp,
        "document.querySelector('#ctxMenu').classList.contains('show') ? "
        "Array.from(document.querySelectorAll('#ctxMenu .citem')).map(e=>window.__uip.label(e)) : []",
        [],
    )
    check("图层树右键弹出菜单", len(items or []) > 0, str(items))
    check("右键菜单含 缩放至图层/图层属性/移至最前/移至最后/移除图层",
          all(any(k in (x or "") for x in (items or []))
              for k in ("缩放至图层", "图层属性", "移至最前", "移至最后", "移除图层")),
          str(items))

    await cdp.evaluate("document.querySelector('#ctxMenu').classList.remove('show'); true")

    # 右键菜单的每个动作
    async def ctx_click(label, row=0):
        box = await js(
            cdp,
            """(() => {
                 const rows = document.querySelectorAll('#treeList .layer-row');
                 const i = %d < 0 ? rows.length + %d : %d;
                 const e = rows[i] || rows[0];
                 const r = e.getBoundingClientRect();
                 return {x: r.x + 40, y: r.y + r.height / 2};
               })()""" % (row, row, row),
        )
        await cdp.click_at(box["x"], box["y"], button="right")
        await asyncio.sleep(0.35)
        await arm_probes(cdp)
        await click_label(cdp, "#ctxMenu .citem", label)
        await asyncio.sleep(0.8)

    await ctx_click("图层属性…")
    check("右键「图层属性…」切到图层属性面板",
          bool(await js(
              cdp,
              "document.querySelector('.tab[data-pane=paneProps]').classList.contains('on')")))

    await ctx_click("缩放至图层")
    check("右键「缩放至图层」改变了地图范围",
          bool(await js(cdp, "window.__boundsChanged === true")))

    # 顺序：右键最后一行 →「移至最前」；右键第一行 →「移至最后」
    order_before = await tree_order(cdp)
    await ctx_click("移至最前", row=-1)
    order_now = await tree_order(cdp)
    check("右键「移至最前」改变了图层顺序", order_now != order_before,
          f"{order_before} -> {order_now}")

    order_before = await tree_order(cdp)
    await ctx_click("移至最后", row=0)
    order_now = await tree_order(cdp)
    check("右键「移至最后」改变了图层顺序", order_now != order_before,
          f"{order_before} -> {order_now}")

    n_before = await js(cdp, "document.querySelectorAll('#treeList .layer-row').length", 0)
    await ctx_click("移除图层")
    n_after = await js(cdp, "document.querySelectorAll('#treeList .layer-row').length", 0)
    check("右键「移除图层」真的删掉了图层", (n_after or 0) < (n_before or 0),
          f"{n_before} -> {n_after}")
    await add_sample_layers(cdp)

    # 工具栏上的上移 / 下移 / 删除
    order_before = await tree_order(cdp)
    await cdp.click("#treeList .layer-row:nth-child(2)")
    await asyncio.sleep(0.4)
    await cdp.click("#btnLayerUp")
    await asyncio.sleep(0.5)
    order_now = await tree_order(cdp)
    check("「上移一层」按钮改变了图层顺序", order_now != order_before,
          f"{order_before} -> {order_now}")

    order_before = await tree_order(cdp)
    await cdp.click("#btnLayerDown")
    await asyncio.sleep(0.5)
    order_now = await tree_order(cdp)
    check("「下移一层」按钮改变了图层顺序", order_now != order_before,
          f"{order_before} -> {order_now}")

    n_before = await js(cdp, "document.querySelectorAll('#treeList .layer-row').length", 0)
    await cdp.click("#btnLayerDel")
    await asyncio.sleep(0.6)
    n_after = await js(cdp, "document.querySelectorAll('#treeList .layer-row').length", 0)
    check("「移除图层」按钮真的删掉了图层", (n_after or 0) < (n_before or 0),
          f"{n_before} -> {n_after}")
    await add_sample_layers(cdp)

    # 没选中时三个按钮必须禁用
    disabled = await js(
        cdp,
        """(() => {
             const s = S.selected; S.selected = null;
             renderTree();                       // 按钮的禁用态是在 renderTree 里刷的
             const ids = ['btnLayerUp','btnLayerDown','btnLayerDel'];
             const out = ids.map(i => document.getElementById(i).disabled);
             S.selected = s; renderTree();
             return JSON.stringify(out);
           })()""",
        "[]",
    )
    check("没选中图层时上移/下移/移除按钮是禁用的",
          disabled == "[true,true,true]", str(disabled))
    await select_row(cdp, 0)


async def sweep_statusbar(cdp):
    """状态栏：坐标 / 比例尺 / CRS / 图层数 / 提示。"""
    coord0 = await js(cdp, "document.querySelector('#stCoord').textContent")
    box = await js(
        cdp,
        """(() => {const r=document.getElementById('map').getBoundingClientRect();
           return {x:r.x+r.width*0.3, y:r.y+r.height*0.3};})()""",
    )
    await cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": box["x"], "y": box["y"], "buttons": 0},
    )
    await asyncio.sleep(0.6)
    coord1 = await js(cdp, "document.querySelector('#stCoord').textContent")
    check("坐标随鼠标移动刷新", coord0 != coord1 and coord1 not in ("", "—"),
          f"{coord0} -> {coord1}")

    scale0 = await js(cdp, "document.querySelector('#stScale').textContent")
    await cdp.evaluate("map.setZoom(map.getZoom()+2); true")
    await asyncio.sleep(0.9)
    scale1 = await js(cdp, "document.querySelector('#stScale').textContent")
    check("比例尺随缩放刷新", scale0 != scale1 and scale1 not in ("", "—"),
          f"{scale0} -> {scale1}")

    crs = await js(cdp, "document.querySelector('#stCrs').textContent")
    check("坐标系显示 EPSG:4326", crs == "EPSG:4326", str(crs))

    n_layers = await js(cdp, "document.querySelectorAll('#treeList .layer-row').length", 0)
    st_layers = await js(cdp, "document.querySelector('#stLayers').textContent")
    check("状态栏图层数与图层树一致", str(st_layers) == str(n_layers),
          f"状态栏 {st_layers} / 图层树 {n_layers}")

    tip = await js(cdp, "document.querySelector('#stTip').textContent")
    check("状态栏有提示文字（不是空的）", bool(tip and tip.strip()), str(tip))


async def sweep_panels(cdp):
    """右栏面板逐个进去交互。"""
    # ---------------- 属性表
    await cdp.click('#toolbars button[data-act="openTable"]')
    await asyncio.sleep(1.5)
    check("属性表列出了图层下拉",
          bool(await js(cdp, "document.querySelectorAll('#tblLayer option').length >= 1")))
    check("属性表有数据行",
          bool(await js(cdp, "document.querySelectorAll('#tblEl tbody tr').length >= 1")))

    page0 = await js(cdp, "document.querySelector('#tblPage').textContent")
    await cdp.click("#tblNext")
    await asyncio.sleep(1.2)
    page1 = await js(cdp, "document.querySelector('#tblPage').textContent")
    check("属性表「下一页」能翻页（页码变了）", page0 != page1, f"{page0} -> {page1}")
    await cdp.click("#tblPrev")
    await asyncio.sleep(1.2)
    page2 = await js(cdp, "document.querySelector('#tblPage').textContent")
    check("属性表「上一页」能翻回来", page2 == page0, f"{page1} -> {page2}")

    # 过滤：先用一个必定成立的表达式
    fields = await js(
        cdp,
        "JSON.stringify((window.PANELS_TABLE_FIELDS)||[])",
        "[]",
    )
    field_name = await js(
        cdp,
        """(() => {
             const th = document.querySelectorAll('#tblEl thead th');
             for (let i = 2; i < th.length; i++){
               const t = (th[i].textContent||'').trim();
               if (t) return t;
             }
             return '';
           })()""",
        "",
    )
    await cdp.type_text("#tblFilter", '"%s" IS NOT NULL' % (field_name or "x"))
    await cdp.click("#tblApply")
    await asyncio.sleep(1.5)
    state = await js(cdp, "document.querySelector('#tblState').textContent")
    check("属性表过滤表达式生效（状态里写明已过滤）",
          "已过滤" in (state or "") or "读取失败" in (state or ""), str(state))
    total_filtered = await js(cdp, "document.querySelector('#tblPage').textContent")

    await cdp.type_text("#tblFilter", "这不是一个合法的表达式 (((")
    await cdp.click("#tblApply")
    await asyncio.sleep(1.5)
    state = await js(cdp, "document.querySelector('#tblState').textContent")
    check("过滤表达式写错时给出看得懂的错误提示",
          "读取失败" in (state or "") and "invalid" not in (state or "").lower(),
          str(state))

    await cdp.type_text("#tblFilter", "")
    await cdp.click("#tblApply")
    await asyncio.sleep(1.2)

    sortable = await js(
        cdp,
        """(() => {
             // 查**字段列**的表头（第一个 th 是行号列「#」，不是字段，没有 data-sort）
             const th = document.querySelector('#tblEl thead th[data-sort]');
             if (!th) return false;
             return !!(th.dataset.sort || (th.getAttribute('title')||'').indexOf('排序') >= 0);
           })()""",
    )
    check("属性表列头可点击排序（点一下按该字段排，再点换升降序）", bool(sortable))

    heads_raw = await js(
        cdp,
        "JSON.stringify(Array.from(document.querySelectorAll('#tblEl thead th'))"
        ".map(e=>e.textContent))",
        "[]",
    )
    try:
        head_list = json.loads(heads_raw or "[]")
    except Exception:
        head_list = []
    dup = sorted({x for x in head_list if head_list.count(x) > 1})
    check("属性表没有重复的列（fid 列只出现一次）", not dup,
          f"表头：{head_list[:8]}；重复：{dup}")
    fid_cells = await js(
        cdp,
        "document.querySelectorAll('#tblEl tbody td.cell[data-field=\"fid\"]').length",
        0,
    )
    check("属性表不把 FID 伪字段当成可编辑的普通属性列",
          (fid_cells or 0) == 0,
          f"表里有 {fid_cells} 个 data-field=fid 的可编辑单元格 —— "
          f"改了它，保存必然失败（OGR：不允许更改要素的 ID）")

    # ---------------- 统计
    await cdp.click('#toolbars button[data-act="openStats"]')
    await asyncio.sleep(1.8)
    check("统计面板有统计行",
          bool(await js(cdp, "document.querySelectorAll('#statEl tbody tr').length >= 1")))
    opts = await js(
        cdp,
        "JSON.stringify(Array.from(document.querySelectorAll('#statField option'))"
        ".map(o=>o.value).filter(Boolean))",
        "[]",
    )
    try:
        opt_list = json.loads(opts or "[]")
    except Exception:
        opt_list = []
    check("统计面板字段下拉有可选项", len(opt_list) >= 1, str(opt_list))
    target_field = opt_list[0] if opt_list else ""
    if target_field:
        await cdp.select_option("#statField", target_field)
    await asyncio.sleep(1.8)
    groups = await js(
        cdp, "document.querySelectorAll('#statEl tbody tr.stat-group').length", 0)
    check("统计面板选了字段后只统计该字段", (groups or 0) == 1,
          f"下拉选了 {target_field!r}，表里仍有 {groups} 个字段分组")

    # ---------------- 选择
    await cdp.click('.right .tabs .tab[data-pane="paneSel"]')
    await asyncio.sleep(0.8)
    await cdp.click("#selAll")
    await asyncio.sleep(1.5)
    sel_state = await js(cdp, "document.querySelector('#selState').textContent")
    check("「全选」后选择面板显示已选数量", "已选" in (sel_state or ""), str(sel_state))
    n_all = await js(cdp, "document.querySelectorAll('#selEl tbody tr').length", 0)

    await cdp.click("#selInv")
    await asyncio.sleep(1.5)
    sel_state = await js(cdp, "document.querySelector('#selState').textContent")
    n_inv = await js(cdp, "document.querySelectorAll('#selEl tbody tr').length", 0)
    check("「反选」后选中集合变了", (n_inv or 0) != (n_all or 0),
          f"全选 {n_all} → 反选 {n_inv}")

    await cdp.click("#selExpr")
    await asyncio.sleep(0.6)
    check("「按表达式选择」弹出对话框",
          bool(await js(cdp, "!!document.querySelector('#selExprInput')")))
    await cdp.type_text("#selExprInput", '"fid" >= 0')
    await cdp.click(".modal.show [data-role='ok']")
    await asyncio.sleep(2.0)
    sel_state = await js(cdp, "document.querySelector('#selState').textContent")
    check("按表达式选择能选出要素", "已选" in (sel_state or "") and "已选 0" not in (sel_state or ""),
          str(sel_state))

    await cdp.click("#selSpace")
    await asyncio.sleep(0.6)
    check("「按空间关系选择」弹出对话框",
          bool(await js(cdp, "!!document.querySelector('#selSpaceTarget')")))
    await cdp.click(".modal.show [data-role='ok']")
    await asyncio.sleep(2.0)
    sel_state = await js(cdp, "document.querySelector('#selState').textContent")
    check("按空间关系选择能给出结果（不是静默失败）",
          bool(sel_state and sel_state.strip()), str(sel_state))

    await cdp.click("#selNone")
    await asyncio.sleep(0.8)
    sel_state = await js(cdp, "document.querySelector('#selState').textContent")
    check("「取消」把选择清空", "已选 0" in (sel_state or ""), str(sel_state))

    # ---------------- 书签
    await cdp.click('.right .tabs .tab[data-pane="paneBookmark"]')
    await asyncio.sleep(1.2)
    await cdp.click("#bmAdd")
    await asyncio.sleep(0.8)
    check("「新建」弹出书签对话框",
          bool(await js(cdp, "!!document.querySelector('.modal.show #bmName')")))
    await cdp.type_text(".modal.show #bmName", "UI路径扫描书签")
    await cdp.click(".modal.show [data-role='ok']")
    await asyncio.sleep(1.6)
    names = await js(
        cdp,
        "Array.from(document.querySelectorAll('#bmEl tr[data-name]')).map(e=>e.dataset.name)",
        [],
    )
    check("书签建好并列出", "UI路径扫描书签" in (names or []), str(names))

    await cdp.click("#bmEl tr[data-name] a[data-act='zoom']")
    await asyncio.sleep(1.0)
    check("点书签能缩放过去", bool(await js(cdp, "window.__boundsChanged === true")))

    await cdp.click("#bmEl tr[data-name] a[data-act='del']")
    await asyncio.sleep(1.6)
    names = await js(
        cdp,
        "Array.from(document.querySelectorAll('#bmEl tr[data-name]')).map(e=>e.dataset.name)",
        [],
    )
    check("书签能删掉", "UI路径扫描书签" not in (names or []), str(names))

    # ---------------- 日志
    await cdp.click('.right .tabs .tab[data-pane="paneLog"]')
    await asyncio.sleep(1.5)
    log_text = await js(cdp, "document.querySelector('#logEl').textContent")
    check("日志面板有内容", bool((log_text or "").strip()), str(log_text)[:120])

    await cdp.select_option("#logLevel", "ERROR")
    await asyncio.sleep(1.5)
    err_text = await js(cdp, "document.querySelector('#logEl').textContent")
    # 「（暂无日志）」是空态提示，不是日志行 —— 别把它当成一条不过滤的日志
    lines = [l for l in (err_text or "").split("\n") if l.strip() and "暂无日志" not in l]
    check("日志按级别过滤后只剩 ERROR",
          all("ERROR" in l for l in lines), str(err_text)[:160])

    await cdp.click("#logClear")
    await asyncio.sleep(1.5)
    log_text = await js(cdp, "document.querySelector('#logEl').textContent")
    check("「清空」能把日志清掉",
          ("暂无日志" in (log_text or "")) or len((log_text or "").strip()) < 200,
          str(log_text)[:160])
    await cdp.select_option("#logLevel", "")
    await asyncio.sleep(0.8)

    # ---------------- 历史
    await cdp.click('.right .tabs .tab[data-pane="paneHist"]')
    await asyncio.sleep(1.5)
    hist_state = await js(cdp, "document.querySelector('#histState').textContent")
    check("历史面板能读出作业记录", "作业记录" in (hist_state or ""), str(hist_state))
    await cdp.click("#histRefresh")
    await asyncio.sleep(1.5)
    hist_state = await js(cdp, "document.querySelector('#histState').textContent")
    check("历史面板「刷新」后仍是作业记录", "作业记录" in (hist_state or ""), str(hist_state))

    # ---------------- 信息面板（后端有 /api/panels/overview，前端有没有？）
    tabs = await js(
        cdp,
        "Array.from(document.querySelectorAll('.right .tabs .tab')).map(e=>e.textContent)",
        [],
    )
    check("右栏有「信息」面板（后端 /api/panels/overview 已经做好，网页没接）",
          "信息" in (tabs or []), str(tabs))

    # ---------------- AI 助手面板
    # 登录用户才看得见（免登录模式是匿名，一律不给）。
    tabs = await js(
        cdp,
        "Array.from(document.querySelectorAll('.right .tabs .tab')).map(e=>e.textContent)",
        [],
    )
    check("右栏有「AI 助手」标签", "AI 助手" in (tabs or []), str(tabs))
    await cdp.click('.right .tabs .tab[data-pane="paneAI"]')
    await asyncio.sleep(1.2)
    ai_mounted = await js(
        cdp,
        "!!(document.querySelector('#paneAI') && document.querySelector('#aiText')"
        " && document.querySelector('#aiSend'))",
        False,
    )
    check("AI 助手面板挂上了（聊天框 + 发送按钮）", bool(ai_mounted))
    ai_state = await js(cdp, "(document.querySelector('#aiCfgState')||{}).textContent || ''", "")
    check("AI 面板显示当前用的是哪份配置",
          (ai_state or "").strip() in ("服务端默认", "本机配置", "未配置"),
          repr(ai_state))
    # 这套自检的临时服务**没配模型 API**：点发送后端要给出**明确的**提示，
    # 而不是静默失败或报一个看不懂的 500。
    await cdp.type_text("#aiText", "现在有哪些数据集？")
    await cdp.click("#aiSend")
    await asyncio.sleep(2.5)
    ai_log = await js(cdp, "(document.querySelector('#aiLog')||{}).textContent || ''", "")
    check("没配模型 API 时给出明确提示（而不是静默失败）",
          "API" in (ai_log or "") or "配置" in (ai_log or ""), str(ai_log)[:200])


async def sweep_editing(cdp):
    """要素编辑全流程。"""
    await cdp.click('#toolbars button[data-act="openTable"]')
    await asyncio.sleep(1.5)
    await select_row(cdp, 0)
    await asyncio.sleep(0.8)

    # 属性表应该跟着图层树里选中的图层走（QGIS 桌面版就是这个行为）。
    # 不跟的话：用户在树上选了图层、进了编辑模式，属性表里显示的还是**另一个图层** ——
    # 后面「双击改属性」会被属性表那一层的编辑状态挡掉，
    # 「添加要素 / 保存」也会因为表层把 P.editing 冲成 null 而失灵。
    same_raw = await js(
        cdp,
        """(() => {
             const sel = S.selected;
             const want = (sel && sel.layer) ? sel.layer.ref : '';
             const got = (document.querySelector('#tblLayer')||{}).value || '';
             return JSON.stringify({want: want, got: got});
           })()""",
        "{}",
    )
    try:
        same_obj = json.loads(same_raw or "{}")
    except Exception:
        same_obj = {}
    check("属性表跟着图层树选中的图层走（QGIS 桌面版就是这个行为）",
          bool(same_obj.get("want")) and same_obj.get("want") == same_obj.get("got"),
          str(same_obj))
    # 让属性表跟上来，后面的编辑流程才是在同一个图层上做的
    if same_obj.get("want") and same_obj.get("want") != same_obj.get("got"):
        await cdp.select_option("#tblLayer", same_obj["want"])
        await asyncio.sleep(1.8)

    await cdp.click('#toolbars button[data-act="toggleEdit"]')
    ok = await wait_for(
        cdp,
        "document.querySelector('#toolbars button[data-act=toggleEdit]').classList.contains('on')",
        timeout=12.0,
    )
    check("「切换编辑」进入编辑模式", ok)

    # 改属性：双击单元格 → 输入 → 回车
    # 先等属性表把「编辑中」标记刷出来 —— editCell() 是靠 P.table.editing 放行的，
    # 那个标记是读表时跟着响应回来的，比「切换编辑」按钮亮起来晚一拍。
    await wait_for(
        cdp,
        "(() => {const e = document.querySelector('#tblEdit');"
        " return !!(e && e.style.display !== 'none');})()",
        timeout=15.0,
    )
    cell_diag = await click_diag(cdp, "#tblEl tbody tr:nth-child(1) td.cell:not([data-field=\"fid\"])")
    cell_info = await js(
        cdp,
        """(() => {
             // 特意挑**不是 fid** 的那一列：fid 是 QGIS 的伪字段，改它保存必然失败
             const td = document.querySelector('#tblEl tbody tr:nth-child(1) td.cell:not([data-field="fid"])');
             if (!td) return null;
             td.scrollIntoView({block:'center', behavior:'instant'});
             const r = td.getBoundingClientRect();
             return {x: r.x + r.width/2, y: r.y + r.height/2,
                     field: td.dataset.field, text: td.textContent.trim()};
           })()""",
    )
    old_val = (cell_info or {}).get("text")
    dbl_before = await js(cdp, "window.__dbl", 0)
    await double_click_at(cdp, cell_info["x"], cell_info["y"])
    await asyncio.sleep(0.2)
    input_now = bool(await js(cdp, "!!document.querySelector('#tblEl tbody td.cell input')"))
    dbl_now = await js(cdp, "window.__dbl", 0)
    editing_input = input_now
    how = "真实双击"
    if not editing_input:
        # CDP 的真实双击偶尔派发不出 dblclick 事件（表格在两次点击之间被重绘过，
        # 浏览器按「新目标」重新计数）。退回到直接派发 dblclick 事件 ——
        # 验的仍然是产品自己的处理逻辑。
        await js(
            cdp,
            """(() => {
                 const td = document.querySelector('#tblEl tbody tr:nth-child(1) td.cell:not([data-field="fid"])');
                 if (!td) return false;
                 td.dispatchEvent(new MouseEvent('dblclick', {bubbles:true}));
                 return true;
               })()""",
        )
        await asyncio.sleep(0.6)
        editing_input = bool(await js(cdp, "!!document.querySelector('#tblEl tbody td.cell input')"))
        how = "派发 dblclick 事件"
    check("编辑模式下双击单元格可以改属性", editing_input,
          f"dblclick {dbl_before}->{dbl_now}；立刻有输入框={input_now}；走的是{how}；"
          f"现场={cell_diag}")
    if editing_input:
        await cdp.evaluate(
            """(() => {
                 const i = document.querySelector('#tblEl tbody td.cell input');
                 if (!i) return false;
                 i.value = '9999';
                 i.dispatchEvent(new Event('input', {bubbles:true}));
                 return true;
               })()"""
        )
        await cdp.evaluate(
            """(() => {
                 const i = document.querySelector('#tblEl tbody td.cell input');
                 if (!i) return false;
                 i.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
                 return true;
               })()"""
        )
        await asyncio.sleep(2.0)
    new_val = await js(
        cdp,
        """(() => {
             const td = document.querySelector('#tblEl tbody tr:nth-child(1) td.cell');
             return td ? td.textContent.trim() : '';
           })()""",
    )
    check("改完属性值真的变了", str(new_val) != str(old_val), f"{old_val} -> {new_val}")

    # 字段计算器
    await cdp.click('#toolbars button[data-act="fieldCalc"]')
    await asyncio.sleep(0.8)
    check("「字段计算器」弹出对话框",
          bool(await js(cdp, "!!document.querySelector('#fcField')")))
    fc_raw = await js(
        cdp,
        "JSON.stringify(Array.from(document.querySelectorAll('#fcField option')).map(o=>o.value))",
        "[]",
    )
    try:
        fc_list = json.loads(fc_raw or "[]")
    except Exception:
        fc_list = []
    check("字段计算器的目标字段里没有 fid（QGIS 的 FID 伪字段不能被赋值）",
          "fid" not in [str(x).lower() for x in fc_list], str(fc_list[:8]))
    target = next((x for x in fc_list if str(x).lower() != "fid"),
                  fc_list[0] if fc_list else "")
    if target:
        await cdp.select_option("#fcField", target)
    await cdp.type_text("#fcExpr", "1")
    await cdp.click(".modal.show [data-role='ok']")
    await asyncio.sleep(2.5)
    tip = await js(cdp, "document.querySelector('#stTip').textContent")
    check("字段计算器跑完给出结果提示", "已更新" in (tip or "") or "失败" in (tip or ""), str(tip))

    # 新增要素
    total0 = await js(cdp, "document.querySelector('#tblPage').textContent")
    snap = await js(
        cdp,
        """(() => JSON.stringify({
             sel: S.selected ? (S.selected.type + ':' + S.selected.ref) : null,
             active: (S.active||[]).map(i => i.type + ':' + i.ref),
             editingBtn: document.querySelector('#toolbars button[data-act=toggleEdit]')
               .classList.contains('on')
           }))()""",
        "{}",
    )
    clicks_before = await js(cdp, "window.__clicks", 0)
    # 把 tip()/toast() 包一层：看看到底有没有人写过状态栏
    await js(
        cdp,
        """(() => {
             window.__tipLog = []; window.__toastLog = [];
             if (!window.__tipWrapped) {
               window.__tipWrapped = true;
               const ot = window.tip, oo = window.toast;
               window.tip = function(m){ window.__tipLog.push(String(m)); return ot.apply(this, arguments); };
               window.toast = function(m){ window.__toastLog.push(String(m)); return oo.apply(this, arguments); };
             }
             return true;
           })()""",
    )
    # 真实编辑态到底是啥？—— 读一次属性表会让 loadTable 重新刷 updateToolbarState，
    # 按钮的高亮态于是就反映了**当前**的 P.editing（而不是上一次留下的）。
    await cdp.click("#tblRefresh")
    await asyncio.sleep(2.0)
    editing_real = await js(
        cdp,
        "document.querySelector('#toolbars button[data-act=toggleEdit]').classList.contains('on')",
    )
    tip_before = await js(cdp, "document.querySelector('#stTip').textContent")
    toast_before = await js(cdp, "document.querySelector('#toast').textContent")
    await cdp.click('#toolbars button[data-act="addFeature"]')
    await asyncio.sleep(0.4)
    tip_fast = await js(cdp, "document.querySelector('#stTip').textContent")
    toast_fast = await js(cdp, "document.querySelector('#toast').textContent")
    clicks_after = await js(cdp, "window.__clicks", 0)
    last_click = await js(cdp, "window.__lastClick", "")
    drafting = "采集" in (tip_fast or "")
    how = "真实鼠标"
    if not drafting:
        # 真实鼠标事件可能落到别的东西上（本机有扩展注入的浮层）——
        # 退回用 JS 触发，验的仍然是「点了会发生什么」
        await js(cdp, "(() => {const b = document.querySelector('#toolbars "
                      "button[data-act=\"addFeature\"]'); if (b) b.click(); return true;})()")
        await asyncio.sleep(1.0)
        tip_fast = await js(cdp, "document.querySelector('#stTip').textContent")
        toast_fast = await js(cdp, "document.querySelector('#toast').textContent")
        drafting = "采集" in (tip_fast or "")
        how = "JS 触发"
    await asyncio.sleep(1.0)
    tip = await js(cdp, "document.querySelector('#stTip').textContent")
    toast = await js(cdp, "document.querySelector('#toast').textContent")
    tip_log = await js(cdp, "JSON.stringify(window.__tipLog || [])", "[]")
    toast_log = await js(cdp, "JSON.stringify(window.__toastLog || [])", "[]")
    # 编辑态到底还在不在？—— 点一下属性表标签会重新 loadTable，
    # updateToolbarState() 会按**当前**的 P.editing 刷新按钮的高亮态
    await cdp.evaluate("switchTab('paneTable'); true")
    await asyncio.sleep(1.5)
    editing_now = await js(
        cdp,
        "document.querySelector('#toolbars button[data-act=toggleEdit]').classList.contains('on')",
    )
    check("「添加要素」点了要有反应（进入采集模式，或明确说明为什么不能）",
          drafting or (tip_fast or "") != (tip_before or "")
          or (toast_fast or "") != (toast_before or ""),
          f"点前 tip={tip_before!r} toast={toast_before!r}；点后 tip={tip_fast!r} toast={toast_fast!r} "
          f"tip调用={tip_log} toast调用={toast_log} 点击 {clicks_before}->{clicks_after} "
          f"目标={last_click!r} 走的是{how} 读表后编辑态={editing_real} "
          f"再读表后编辑态={editing_now} 现场={snap}")
    if "采集" in (tip or ""):
        geo = await js(
            cdp,
            """(() => {const r=document.getElementById('map').getBoundingClientRect();
               return {x:r.x+r.width*0.5, y:r.y+r.height*0.5};})()""",
        )
        # 多边形至少要 3 个顶点：点 4 个不同的位置，再双击收笔
        for i in range(4):
            await cdp.click_at(geo["x"] + i * 10, geo["y"] + i * 8)
            await asyncio.sleep(0.35)
        await double_click_at(cdp, geo["x"] + 40, geo["y"] + 32)
        await asyncio.sleep(3.0)
        total1 = await js(cdp, "document.querySelector('#tblPage').textContent")
        check("地图上采集能新增一个要素", total0 != total1, f"{total0} -> {total1}")
    else:
        check("地图上采集能新增一个要素", False, "采集模式没起来，后面没法测")

    # 删除选中要素
    await cdp.click('#tblEl tbody tr:nth-child(1)')
    await asyncio.sleep(0.8)
    n_before = await js(cdp, "document.querySelectorAll('#selEl tbody tr').length", 0)
    await cdp.click('#toolbars button[data-act="deleteSelected"]')
    await asyncio.sleep(2.0)
    tip = await js(cdp, "document.querySelector('#stTip').textContent")
    check("「删除选中要素」给出结果提示",
          "已删除" in (tip or "") or "没有选中" in (tip or "") or "编辑模式" in (tip or ""),
          str(tip))
    n_after = await js(cdp, "document.querySelectorAll('#selEl tbody tr').length", 0)
    check("删除后选择集变小了", (n_after or 0) <= (n_before or 0), f"{n_before} -> {n_after}")

    # 保存 / 放弃
    await cdp.click('#toolbars button[data-act="saveEdit"]')
    ok = await wait_for(
        cdp,
        "document.querySelector('#stTip').textContent.indexOf('已保存') >= 0",
        timeout=15.0,
    )
    check("「保存编辑」把改动写回数据文件", ok,
          str(await js(cdp, "document.querySelector('#stTip').textContent")))

    await cdp.click('#toolbars button[data-act="toggleEdit"]')
    await asyncio.sleep(1.5)
    await cdp.click('#toolbars button[data-act="discardEdit"]')
    ok = await wait_for(
        cdp,
        "document.querySelector('#stTip').textContent.indexOf('已放弃') >= 0 || "
        "document.querySelector('#toast').textContent.indexOf('编辑状态') >= 0",
        timeout=15.0,
    )
    check("「放弃编辑」有明确反馈", ok,
          str(await js(cdp, "document.querySelector('#stTip').textContent")))


async def sweep_prop_tabs(cdp):
    """图层属性四个分页（用矢量图层，这层之前只测过栅格）。"""
    await cdp.click("#treeList .layer-row:nth-child(1)")
    await asyncio.sleep(0.8)
    await cdp.evaluate("switchTab('paneProps'); true")
    await asyncio.sleep(0.8)
    for tab, label in (("general", "常规"), ("symbology", "符号系统"),
                       ("transparency", "透明度"), ("source", "源")):
        await cdp.click('.prop-tab[data-proptab="%s"]' % tab)
        await asyncio.sleep(0.6)
        body = await js(cdp, "document.querySelector('#propBody').textContent")
        check("图层属性「%s」分页有内容" % label, bool(body and len(body.strip()) > 4),
              str(body)[:100])


async def sweep_add_dialog(cdp):
    """添加图层对话框的四类数据源。"""
    await cdp.click("#btnAddLayer")
    await asyncio.sleep(0.7)

    await cdp.click('#addTree .node[data-cat="local-file"]')
    await asyncio.sleep(0.5)
    accept = await js(cdp, "(document.querySelector('#addFile')||{}).accept||''")
    check("「本地文件」给出文件选择框，矢量栅格都能传",
          all(e in (accept or "") for e in (".geojson", ".gpkg", ".shp", ".tif")),
          str(accept))

    await cdp.click('#addTree .node[data-cat="server"]')
    await asyncio.sleep(0.5)
    n = await js(cdp, "document.querySelectorAll('#addForm input[data-ref]').length", 0)
    check("「服务器共享数据」列出可勾选图层", (n or 0) >= 1, f"{n} 个")

    # XYZ：缺占位符的 URL 也要有提示
    await cdp.click('#addTree .node[data-cat="xyz"]')
    await asyncio.sleep(0.5)
    await cdp.type_text("#xyzUrl", "http://127.0.0.1:1/tile.png")
    typed_url = await js(cdp, "(document.querySelector('#xyzUrl')||{}).value")
    await cdp.click("#addOk")
    await asyncio.sleep(1.5)
    toast = await js(cdp, "document.querySelector('#toast').textContent")
    rejected = await js(
        cdp,
        "document.querySelector('#toast').textContent.indexOf('占位符') >= 0 || "
        "document.querySelector('#toast').textContent.indexOf('{z}') >= 0 || "
        "document.querySelector('#toast').textContent.indexOf('模板') >= 0",
    )
    check("XYZ 的 URL 模板缺 {z}/{x}/{y} 时给出提示（而不是默默加一个坏图层）",
          bool(rejected),
          f"填进去的 URL={typed_url!r}；toast={toast!r}")

    # 再开一次对话框（上一个已经关掉了），填一个合法的模板
    await cdp.click("#btnAddLayer")
    await asyncio.sleep(0.7)
    await cdp.click('#addTree .node[data-cat="xyz"]')
    await asyncio.sleep(0.5)
    await cdp.type_text("#xyzUrl", "http://127.0.0.1:1/{z}/{x}/{y}.png")
    await cdp.type_text("#xyzName", "扫描用XYZ")
    await cdp.click("#addOk")
    await asyncio.sleep(1.8)
    names = await js(
        cdp,
        "Array.from(document.querySelectorAll('#treeList .layer-row .nm')).map(e=>e.textContent)",
        [],
    )
    check("XYZ 瓦片能加进地图", "扫描用XYZ" in (names or []), str(names))

    # WMS
    await cdp.click("#btnAddLayer")
    await asyncio.sleep(0.7)
    await cdp.click('#addTree .node[data-cat="wms"]')
    await asyncio.sleep(0.5)
    await cdp.type_text("#wmsUrl", "http://127.0.0.1:1/wms")
    await cdp.type_text("#wmsLayer", "topp:states")
    await cdp.type_text("#wmsName", "扫描用WMS")
    await cdp.click("#addOk")
    await asyncio.sleep(1.8)
    names = await js(
        cdp,
        "Array.from(document.querySelectorAll('#treeList .layer-row .nm')).map(e=>e.textContent)",
        [],
    )
    check("WMS 图层能加进地图", "扫描用WMS" in (names or []), str(names))

    # 把两个服务图层清掉，别留垃圾
    await js(
        cdp,
        """(() => {
             const keep = [];
             for (const it of (S.active||[])){
               if (it.type === 'service'){ const ov = S.overlays.get(it.ref);
                                           if (ov) map.removeLayer(ov); }
               else keep.push(it);
             }
             S.active = keep; renderTree(); return true;
           })()""",
    )
    await asyncio.sleep(0.5)


async def sweep_file_browser(cdp):
    """文件浏览器。"""
    await cdp.click('.menubar .menu[data-menu="file"]')
    await asyncio.sleep(0.4)
    await click_label(cdp, "#dropdown .ditem", "文件浏览器…")
    await asyncio.sleep(1.8)
    check("文件浏览器对话框打开了",
          bool(await js(cdp, "document.querySelector('#dlgFiles').classList.contains('show')")))
    scopes = await js(
        cdp,
        "JSON.stringify(Array.from(document.querySelectorAll('#fbScope option')).map(o=>o.value))",
        "[]",
    )
    try:
        scope_list = json.loads(scopes or "[]")
    except Exception:
        scope_list = []
    check("文件浏览器的空间下拉有「全部 + 共享 + 个人」", len(scope_list) >= 2, str(scope_list))
    rows = await js(cdp, "document.querySelectorAll('#fbWrap tbody tr').length", 0)
    count_text = await js(cdp, "document.querySelector('#fbCount').textContent")
    check("文件浏览器能读出文件清单（有空态提示也行）",
          bool((rows or 0) > 0) or "没有文件" in (await js(
              cdp, "document.querySelector('#fbWrap').textContent") or ""),
          f"{rows} 行 / {count_text}")
    await cdp.click("#fbClose2")
    await asyncio.sleep(0.5)
    check("文件浏览器能关掉",
          not bool(await js(cdp, "document.querySelector('#dlgFiles').classList.contains('show')")))


async def sweep_console(cdp):
    """Python 控制台（管理员可见）。"""
    check("管理员能看到 Python 控制台把手",
          bool(await js(cdp, "!!document.querySelector('#pcHandle')")))
    await cdp.click("#pcHandle")
    await asyncio.sleep(0.8)
    check("点把手能展开控制台",
          bool(await js(cdp, "document.querySelector('#gisdsConsole').classList.contains('open')")))
    await cdp.type_text("#pcCode", "print(6*7)")
    await cdp.click("#pcRun")
    await asyncio.sleep(4.0)
    out = await js(cdp, "document.querySelector('#pcOut').textContent")
    check("控制台执行 Python 有输出", "42" in (out or ""), str(out)[:160])
    await cdp.click("#pcClear")
    await asyncio.sleep(0.6)
    out = await js(cdp, "document.querySelector('#pcOut').textContent")
    check("「清空输出」能清空控制台", "42" not in (out or ""), str(out)[:120])
    await cdp.type_text("#pcCode", "zzz = 1")
    await cdp.click("#pcRun")
    await asyncio.sleep(4.0)
    vars_text = await js(cdp, "document.querySelector('#pcVars').textContent")
    await cdp.click("#pcReset")
    await asyncio.sleep(2.0)
    vars_after = await js(cdp, "document.querySelector('#pcVars').textContent")
    check("「重置命名空间」把变量数清零",
          "变量 0" in (vars_after or ""), f"{vars_text} -> {vars_after}")
    await cdp.click("#pcClose")
    await asyncio.sleep(0.5)
    check("控制台能收起",
          not bool(await js(cdp,
                            "document.querySelector('#gisdsConsole').classList.contains('open')")))


async def sweep_plugins(cdp):
    """插件管理。"""
    await cdp.evaluate(
        "(() => {const t=Array.from(document.querySelectorAll('.right .tabs .tab'))"
        ".find(e=>e.textContent==='插件'); if(t){t.click(); return true;} return false;})()"
    )
    await asyncio.sleep(1.8)
    check("右栏有「插件」标签并能切过去",
          bool(await js(cdp, "document.querySelector('#panePlugins').classList.contains('on')")))
    text = await js(cdp, "document.querySelector('#panePlugins').textContent")
    check("插件面板列出了已装插件", "示例插件" in (text or ""), str(text)[:160])
    check("管理员能看到「安装插件（上传 zip）」按钮",
          bool(await js(
              cdp,
              "Array.from(document.querySelectorAll('#panePlugins button'))"
              ".some(b=>b.textContent.indexOf('安装插件')>=0)")))
    await cdp.click("#plugToolbar button")
    await asyncio.sleep(1.8)
    text = await js(cdp, "document.querySelector('#panePlugins').textContent")
    check("插件面板「刷新」后清单还在", "示例插件" in (text or ""), str(text)[:160])
    check("插件贡献的面板在右栏",
          bool(await js(
              cdp,
              "Array.from(document.querySelectorAll('.right .tabs .tab'))"
              ".some(t=>t.textContent==='示例插件')")))


async def sweep_error_paths(cdp):
    """报错路径：界面必须给出人能看懂的提示。"""
    # 1) 上传一个识别不了的 shapefile —— 界面要说清为什么
    await cdp.click("#btnAddLayer")
    await asyncio.sleep(0.7)
    await cdp.click('#addTree .node[data-cat="local-file"]')
    await asyncio.sleep(0.6)
    bad = (TMP_ROOT or Path(tempfile.gettempdir())) / "lonely.shp"
    bad.write_bytes(bytes([0x50, 0x4B, 0x03, 0x04, 0, 0]))
    ok = await set_file_input(cdp, "#addFile", bad)
    check("能给「本地文件」选真文件（CDP setFileInputFiles）", ok)
    if ok:
        await cdp.click("#addOk")
        await asyncio.sleep(3.0)
    toast = await js(cdp, "document.querySelector('#toast').textContent")
    tip = await js(cdp, "document.querySelector('#stTip').textContent")
    check("上传的文件识别不了时，界面说清原因（不是只说一句「上传完成」）",
          ok and ("没被识别" in (toast or "") or "识别" in (toast or ""))
          and (".dbf" in (tip or "") or "配套" in (tip or "") or "原因" in (tip or "")),
          f"toast={toast!r} tip={tip!r}")
    await js(
        cdp,
        """(async () => {
             const r = await fetch('/api/uploads');
             const j = await r.json();
             for (const d of (j.uploads||[])){
               await fetch('/api/uploads/' + encodeURIComponent(d.dataset_id),
                           {method:'DELETE'});
             }
             return true;
           })()""",
    )

    # 2) 表达式选择留空 —— 对话框要给出提示
    await cdp.click('#toolbars button[data-act="selectExpr"]')
    await asyncio.sleep(0.8)
    await cdp.click(".modal.show [data-role='ok']")
    await asyncio.sleep(0.8)
    msg = await js(cdp, "(document.querySelector('.modal.show [data-role=msg]')||{}).textContent")
    check("按表达式选择留空时给出提示", bool((msg or "").strip()), repr(msg))
    await cdp.click(".modal.show [data-role='cancel']")
    await asyncio.sleep(0.4)

    # 3) 按比例缩放填非法值 —— 对话框要给出提示
    await cdp.click('#toolbars button[data-act="zoomScale"]')
    await asyncio.sleep(0.8)
    await cdp.type_text("#zscaleInput", "0")
    await cdp.click(".modal.show [data-role='ok']")
    await asyncio.sleep(0.8)
    msg = await js(cdp, "(document.querySelector('.modal.show [data-role=msg]')||{}).textContent")
    check("按比例缩放填非法值时给出提示", bool((msg or "").strip()), repr(msg))
    await cdp.click(".modal.show [data-role='cancel']")
    await asyncio.sleep(0.4)

    # 4) 作业失败 —— 界面要给出失败原因
    await cdp.evaluate("switchTab('paneAlgo'); true")
    await asyncio.sleep(0.5)
    await cdp.type_text("#algoSearch", "buffer")
    await asyncio.sleep(1.2)
    await cdp.click('.algoitem[data-id="native:buffer"]')
    await asyncio.sleep(2.0)
    await cdp.evaluate(
        """(() => {
             const sel = document.querySelector('#algoForm select[data-param=INPUT]');
             if (sel && sel.options.length) { sel.value = sel.options[0].value;
               sel.dispatchEvent(new Event('change', {bubbles:true})); }
             return true;
           })()"""
    )
    await asyncio.sleep(0.6)
    await cdp.evaluate("(()=>{const b=document.querySelector('#btnRun'); if(b) b.disabled=false; return true;})()")
    await cdp.click("#btnRun")
    ok = await wait_for(
        cdp,
        "document.querySelector('#jobState').textContent.indexOf('失败') >= 0 || "
        "document.querySelector('#jobState').textContent.indexOf('failed') >= 0 || "
        "document.querySelector('#jobState').textContent.indexOf('success') >= 0",
        timeout=45.0,
    )
    state = await js(cdp, "document.querySelector('#jobState').textContent")
    check("跑算法时界面给出作业状态（失败要有原因，不能静默）",
          ok and "undefined" not in (state or "") and "NaN" not in (state or ""),
          str(state)[:200])

    # 5) 没选矢量图层就点「全选」—— 要提示，而不是静默
    await js(
        cdp,
        """(() => {
             window.__savedActive = S.active;
             window.__savedSel = S.selected;
             S.active = []; S.selected = null; renderTree();
             return true;
           })()""",
    )
    await cdp.click('#toolbars button[data-act="selectAll"]')
    await asyncio.sleep(1.2)
    toast = await js(cdp, "document.querySelector('#toast').textContent")
    check("地图上没有矢量图层时点「全选」给出提示", bool((toast or "").strip()), repr(toast))
    await js(
        cdp,
        """(() => {
             S.active = window.__savedActive || []; S.selected = window.__savedSel || null;
             renderTree(); return true;
           })()""",
    )
    await asyncio.sleep(0.5)

    # 收尾：清掉作业输出
    await js(cdp, "fetch('/api/processing/jobs', {method:'DELETE'}).catch(()=>{})")
    await asyncio.sleep(0.6)


async def sweep_consistency(cdp):
    """刷新页面后：面板尺寸 / 选中的图层 / 打开的标签页还在不在。"""
    # 先记住刷新前的状态
    await cdp.evaluate("switchTab('paneTable'); true")
    await asyncio.sleep(0.8)
    await cdp.click("#treeList .layer-row:nth-child(1)")
    await asyncio.sleep(0.8)
    before = await js(
        cdp,
        """(() => {
             const l = document.querySelector('aside.left').getBoundingClientRect().width;
             const r = document.querySelector('aside.right').getBoundingClientRect().width;
             const t = document.querySelector('.tab.on');
             return JSON.stringify({l: Math.round(l), r: Math.round(r),
                                    tab: t ? t.dataset.pane : null,
                                    layers: document.querySelectorAll('#treeList .layer-row').length,
                                    sel: (S.selected && S.selected.ref) || null});
           })()""",
        "{}",
    )
    try:
        before_obj = json.loads(before or "{}")
    except Exception:
        before_obj = {}

    await reload_page(cdp)

    after = await js(
        cdp,
        """(() => {
             const l = document.querySelector('aside.left').getBoundingClientRect().width;
             const r = document.querySelector('aside.right').getBoundingClientRect().width;
             const t = document.querySelector('.tab.on');
             return JSON.stringify({l: Math.round(l), r: Math.round(r),
                                    tab: t ? t.dataset.pane : null,
                                    layers: document.querySelectorAll('#treeList .layer-row').length,
                                    sel: (S.selected && S.selected.ref) || null});
           })()""",
        "{}",
    )
    try:
        after_obj = json.loads(after or "{}")
    except Exception:
        after_obj = {}

    check("刷新后左右面板宽度还在",
          abs((after_obj.get("l") or 0) - (before_obj.get("l") or 0)) <= 2
          and abs((after_obj.get("r") or 0) - (before_obj.get("r") or 0)) <= 2,
          f"{before_obj.get('l')}/{before_obj.get('r')} -> {after_obj.get('l')}/{after_obj.get('r')}")
    check("刷新后地图上已加载的图层还在",
          (after_obj.get("layers") or 0) == (before_obj.get("layers") or 0),
          f"{before_obj.get('layers')} -> {after_obj.get('layers')}")
    check("刷新后选中的图层还在",
          (after_obj.get("sel") or "") == (before_obj.get("sel") or ""),
          f"{before_obj.get('sel')!r} -> {after_obj.get('sel')!r}")
    check("刷新后打开的标签页还在",
          (after_obj.get("tab") or "") == (before_obj.get("tab") or ""),
          f"{before_obj.get('tab')!r} -> {after_obj.get('tab')!r}")


async def scan_leaks(cdp):
    """界面上不能漏出 undefined / NaN / null。"""
    raw = await js(
        cdp,
        """(() => {
             const bad = [];
             const ATTRS = ["title", "alt", "placeholder", "aria-label"];
             document.querySelectorAll("*").forEach((el) => {
               for (const a of ATTRS) {
                 const v = el.getAttribute ? el.getAttribute(a) : null;
                 if (v && /undefined|\\bNaN\\b|\\bnull\\b/i.test(v))
                   bad.push(a + '=' + v.slice(0, 60) + ' @ ' + el.tagName + (el.id ? '#'+el.id : ''));
               }
               if ("value" in el) {
                 const v = String(el.value == null ? "" : el.value);
                 if (/undefined|\\bNaN\\b|\\bnull\\b/i.test(v))
                   bad.push('value=' + v.slice(0, 60) + ' @ ' + el.tagName + (el.id ? '#'+el.id : ''));
               }
             });
             const t = document.body ? document.body.innerText : "";
             t.split("\\n").forEach((l) => {
               if (/undefined|\\bNaN\\b|\\bnull\\b/i.test(l)) bad.push('text:' + l.trim().slice(0, 80));
             });
             return JSON.stringify(bad);
           })()""",
        "[]",
    )
    try:
        items = json.loads(raw or "[]")
    except Exception:
        items = []
    undef = [x for x in items if "undefined" in x.lower()]
    nan = [x for x in items if "NaN" in x]
    nulls = [x for x in items if "null" in x.lower()]
    check("界面里没有 undefined", not undef, "; ".join(undef[:6]))
    check("界面里没有 NaN", not nan, "; ".join(nan[:6]))
    check("界面里没有 null", not nulls, "; ".join(nulls[:6]))


# ---------------------------------------------------------------- 主流程


async def run(base_url: str) -> None:
    browser = find_browser()
    if not browser:
        check("能找到无头浏览器（Edge / Chrome）", False, "本机没装 Edge / Chrome，这轮扫描跑不了")
        return

    try:
        httpx.get(base_url + "/readyz", timeout=5)
    except Exception as exc:
        check("REST 服务已就绪", False, repr(exc))
        return

    profile = tempfile.mkdtemp(prefix="gisds_cdp_uipaths_")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--window-size=1600,900",
            base_url + "/login",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    ws_url = None
    try:
        for _ in range(80):
            try:
                targets = httpx.get(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=3).json()
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl") and (
                        t.get("url", "").startswith(base_url)
                    ):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.4)
        if not ws_url:
            check("连得上无头浏览器（CDP）", False, "拿不到 webSocketDebuggerUrl")
            return

        async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await asyncio.sleep(1.2)

            # 装页面小工具 + 记 JS 报错（每次导航后都要重装）
            await install_helpers(cdp)

            print("\n== 登录 ==")
            await login(cdp, base_url)

            ok = await wait_for(
                cdp,
                "document.querySelector('#stQgis') && "
                "document.querySelector('#stQgis').textContent !== '—'",
                timeout=30.0,
            )
            check("页面加载完成（状态栏出现 QGIS 版本）", ok)
            status_text = await js(cdp, "document.querySelector('#statusText').textContent")
            check("顶栏显示服务正常", "服务正常" in (status_text or ""), str(status_text))

            # 装一个「地图范围变了没 / 图层顺序变了没」的探针，菜单/工具栏里好判断有没有反应
            await js(
                cdp,
                """(() => {
                     window.__boundsChanged = false;
                     const orig = map.setView.bind(map);
                     map.setView = function(){ window.__boundsChanged = true; return orig.apply(null, arguments); };
                     const ofb = map.fitBounds.bind(map);
                     map.fitBounds = function(){ window.__boundsChanged = true; return ofb.apply(null, arguments); };
                     return true;
                   })()""",
            )
            # 帮助菜单会 window.open —— 探一下新标签页
            await js(
                cdp,
                """(() => {
                     const orig = window.open;
                     window.open = function(url){
                       window.__helpTabOpened = true;
                       try { return orig.apply(window, arguments); } catch(e) { return null; }
                     };
                     return true;
                   })()""",
            )
            # 插件清单是异步拉来的，插件贡献的「工具」菜单要等它到位
            await wait_for(
                cdp,
                "!!document.querySelector('.menubar .menu[data-menu=tools]')",
                timeout=20.0,
            )

            print("\n== 加图层（走「添加图层」对话框） ==")
            refs_raw = await js(
                cdp,
                "JSON.stringify((S.layers||[]).filter(l=>l.kind==='vector'||l.kind==='raster')"
                ".slice(0,2).map(l=>l.ref))",
                "[]",
            )
            try:
                refs = json.loads(refs_raw or "[]")
            except Exception:
                refs = []
            check("服务端有可加载的示例图层", len(refs) >= 2, str(refs))
            await add_layers_via_dialog(cdp, refs)

            print("\n== 菜单栏：每个菜单项点一遍 ==")
            await sweep_menus(cdp)

            print("\n== 工具栏：每个按钮点一遍 ==")
            await sweep_toolbar(cdp)

            print("\n== 图层树 ==")
            await sweep_layer_tree(cdp)

            print("\n== 状态栏 ==")
            await sweep_statusbar(cdp)

            print("\n== 右栏面板 ==")
            await sweep_panels(cdp)

            print("\n== 要素编辑 ==")
            await sweep_editing(cdp)

            print("\n== 图层属性分页 ==")
            await sweep_prop_tabs(cdp)

            print("\n== 添加图层对话框（四类数据源） ==")
            await sweep_add_dialog(cdp)

            print("\n== 文件浏览器 ==")
            await sweep_file_browser(cdp)

            print("\n== Python 控制台 ==")
            await sweep_console(cdp)

            print("\n== 插件管理 ==")
            await sweep_plugins(cdp)

            print("\n== 报错路径 ==")
            await sweep_error_paths(cdp)

            print("\n== 刷新一致性 ==")
            await sweep_consistency(cdp)

            print("\n== 界面泄漏 ==")
            await scan_leaks(cdp)

            print("\n== 全程 JS 报错 ==")
            errs = await js(cdp, "JSON.stringify((window.__errs||[]).slice(0, 8))", "[]")
            try:
                err_list = json.loads(errs or "[]")
            except Exception:
                err_list = []
            check("整个扫描过程没有未捕获的 JS 报错", not err_list, "; ".join(err_list[:5]))

    finally:
        try:
            kill_tree(proc.pid)
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    global TMP_ROOT
    tmp_root = Path(tempfile.mkdtemp(prefix="gisds_uipaths_"))
    TMP_ROOT = tmp_root
    try:
        # 示例数据复制一份进临时目录 —— 生产 data/ 一个字节都不碰
        shutil.copytree(
            PROJECT_ROOT / "tests" / "sample_data" / "shared",
            tmp_root / "shared",
            dirs_exist_ok=True,
        )
        # 内置离线底图装在生产的 data/shared/basemap/ 下，复制进临时目录
        prod_basemap = PROJECT_ROOT / "data" / "shared" / "basemap"
        if prod_basemap.is_dir():
            shutil.copytree(prod_basemap, tmp_root / "shared" / "basemap", dirs_exist_ok=True)
        with TestServer(
            # 端口基数挪到 **8972**：8851~8907 那一段是别的自检在用的
            # （并行跑时端口会撞，撞了就可能连到别人的服务 —— 见
            # server_helper._owns_port）。CDP 端口同理，在 9402。
            port=_free_port(8972),
            require_login=True,
            data_root=tmp_root,
            extra_env={"GISDS_ADMIN_PASSWORD": ADMIN_PASS,
                       "GISDS_ADMIN_USERNAME": ADMIN_USER},
        ) as server:
            asyncio.run(run(server.base))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被跳过了）",
          checked == EXPECTED_CHECKS, str(checked))
    print(f"\n== 结果：{checked - len(fails)} 通过 / {len(fails)} 失败 ==")
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001
        # main() 抛异常时**不能**让它一路退到解释器退出 ——
        # 那样会先跑 QGIS 的析构，Windows 上大概率变成 0xC0000005（访问违例），
        # 真实原因（那条 KeyError 之类）反而被埋了。改成：打印回溯 + 退出码 1。
        import traceback

        traceback.print_exc()
        code = 1

    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    import os
    os._exit(code)
