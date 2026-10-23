"""面板与工具栏的 UI 自检：无头 Edge + CDP，真点真跑。

check_webui.py 覆盖的是控制台原有交互；这层补的是新加的部分——
顶部 QGIS 工具栏行、右栏六个面板、属性表读数、选择高亮、书签、日志、
处理历史，以及页面有没有脚本报错。

默认自己拉起一个临时服务实例（端口 8854），用完关掉。

用法::

    run-qgis-python.bat tests/check_webui_panels.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from check_webui import CDP, find_browser, scan_undefined, wait_for  # noqa: E402
from server_helper import TestServer, _free_port, kill_tree  # noqa: E402

# 端口要躲开别的自检脚本（尤其并行会话同时跑的时候）：
# 撞端口会串台 —— 本脚本连到别人的临时服务，页面被对方的登录鉴权拦走，
# 表现为「页面加载完成」失败，看起来像网页坏了。
CDP_PORT = int(os.environ.get("GISDS_CDP_PORT_PANELS") or _free_port(9364))
SERVER_PORT = _free_port(8864)

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 67 = 页面加载 1 + 顶部工具栏 33 + 右栏面板 13 + 属性表 5 + 统计 3
#            + 选择 4 + 空间书签 4 + 日志 1 + 处理历史 1 + 页面脚本 1
#            + undefined 1。
# 顶部工具栏那 33 里含 5 条新增：工具栏用 QGIS 官方图标 / QGIS 没有对应图标时
# 才用自绘 / 浮动条用 QGIS 官方图标 / 三个 QGIS 图标文件真的取得到。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 67


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def summary() -> int:
    """打印汇总 + 钉死断言总数。

    **每一条退出路径都得过这道钉子** —— 少了断言就当场红。
    「汇总数说谎」比「断言失败」更难查：一个断言都没跑却报「成功」是最坑的。
    """
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    return 0 if _FAILED == 0 else 1


async def run(base_url: str) -> int:
    browser = find_browser()
    if not browser:
        print("  找不到 Edge / Chrome，跳过面板 UI 自检")
        # 一条断言都没跑却报「成功」是最坑的 —— 走同一道钉子，当场红。
        return summary()
    try:
        # 等的是**就绪**（/readyz）：UI 自检要驱动一个「能干活了」的服务。
        # /healthz 是纯存活语义，进程在就 200，拦不住「还没准备好」的情况。
        r = httpx.get(base_url + "/readyz", timeout=5)
        if r.status_code != 200:
            print(f"  REST 服务未就绪（{r.status_code}），跳过面板 UI 自检")
            return summary()
    except Exception as exc:
        print(f"  REST 服务未启动（{exc}），跳过面板 UI 自检")
        return summary()

    profile = tempfile.mkdtemp(prefix="gisds_cdp_panels_")
    page_url = base_url + "/?cdp=1"
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
            page_url,
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
            print("  无法连接无头浏览器")
            return summary()

        async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Log.enable")
            await asyncio.sleep(1.0)
            ok = await wait_for(cdp, "document.querySelector('#stQgis') && document.querySelector('#stQgis').textContent !== '—'")
            check("页面加载完成", ok)
            if not ok:
                diag = await cdp.evaluate(
                    "JSON.stringify({href: location.href, title: document.title,"
                    " stQgis: (document.querySelector('#stQgis')||{}).textContent,"
                    " body: (document.body? document.body.innerText.slice(0,200):'')})"
                )
                print(f"    诊断：{diag}")
                print("    （落在 /login 说明连到了别人要登录的临时服务 —— 端口撞了）")
                return summary()

            # 收集控制台报错
            console_errors: list[str] = []

            async def drain_console():
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        return
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("method") == "Runtime.exceptionThrown":
                        det = msg.get("params", {}).get("exceptionDetails", {})
                        console_errors.append(det.get("text") or json.dumps(det, ensure_ascii=False)[:200])
                    elif msg.get("method") == "Log.entryAdded":
                        entry = msg.get("params", {}).get("entry", {})
                        if entry.get("level") == "error":
                            console_errors.append(entry.get("text", ""))

            collector = asyncio.create_task(drain_console())

            # ------------------------------------------------------------ 工具栏
            print("\n== 顶部工具栏 ==")
            check("工具栏行已插入",
                  await cdp.evaluate("!!document.querySelector('#toolbars')"))
            groups = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#toolbars .tb-group')).map(e=>e.dataset.group)"
            )
            rows = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#toolbars .tb-row')).map(r=>"
                "Array.from(r.querySelectorAll('.tb-group')).map(g=>g.dataset.group))"
            )
            check("工具栏是两排（QGIS 桌面版同款）", len(rows) == 2, str(rows))
            check("第一排是导航工具栏", rows and rows[0] == ["导航"], str(rows))
            check("第二排是 图层/编辑/属性/选择",
                  rows and len(rows) > 1 and rows[1] == ["图层", "编辑", "属性", "选择"], str(rows))
            check("分组共 5 个", len(groups) == 5, str(groups))
            acts = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#toolbars button[data-act]')).map(e=>e.dataset.act)"
            )
            for act in ("addLayer", "openTable", "openStats", "zoomLayer", "selectAll",
                        "selectExpr", "selectSpace", "toggleEdit", "saveEdit",
                        "fieldCalc", "zoomAll", "prevExtent", "toggleMapTips",
                        "toolPan", "toolZoomIn", "toolZoomOut", "zoomScale"):
                check(f"工具栏有「{act}」按钮", act in acts, str(acts))
            # 图标现在**直接用 QGIS 官方的 SVG 文件**（<img> 引用
            # /static/icons/qgis/*.svg），不再是本项目自绘的描边 svg。
            # 所以这里要同时看两种：img 的数量（QGIS 官方）与 svg 的数量（自绘兜底）。
            qgis_imgs = await cdp.evaluate(
                "document.querySelectorAll('#toolbars img.ic[src*=\"/static/icons/qgis/\"]').length"
            )
            check("工具栏用 QGIS 官方图标", qgis_imgs >= 20, str(qgis_imgs))
            own_svg = await cdp.evaluate("document.querySelectorAll('#toolbars svg.ic').length")
            check("QGIS 没有对应图标时才用自绘", own_svg <= 2, str(own_svg))
            # **同一个图标出现两次**（用户报「上面的图标有重复的」）：
            #   添加图层 vs 添加要素 都是 ＋、放弃编辑 vs 删除选中 都是 🗑、
            #   地图提示 vs 地图浮动条上的识别 都是同一个准星。
            # 按钮长相一样就分不清是哪个功能 —— 这里钉住「工具栏里图标互不相同」。
            #
            # 图标身份怎么算：QGIS 官方图标取 <img> 的 src，自绘的取 <svg> 的内容，
            # 两者混在一起比对 —— 只比其中一种会让另一种变成空过。
            raw_icons = await cdp.evaluate(
                "JSON.stringify(Array.from(document.querySelectorAll('#toolbars button'))"
                ".map(b => {const im=b.querySelector('img.ic');"
                "if(im) return 'img:'+(im.getAttribute('src')||'');"
                "const s=b.querySelector('svg');"
                "return s ? 'svg:'+(s.innerHTML || '') : '';}))"
            )
            raw_list = json.loads(raw_icons or "[]")
            seen = {}
            dup_icons = []
            for s in raw_list:
                if s in seen:
                    dup_icons.append(s[:70])
                seen[s] = True
            check("工具栏里没有重复图标", not dup_icons, " || ".join(dup_icons[:3]))
            # 地图浮动条（QGIS 的 Map Tools）也不能和第一排撞图标
            bar_icons = set(s for s in raw_list if s)
            float_icons = await cdp.evaluate(
                "JSON.stringify(Array.from(document.querySelectorAll('#mapTools button'))"
                ".map(b => {const im=b.querySelector('img.ic');"
                "if(im) return 'img:'+(im.getAttribute('src')||'');"
                "const s=b.querySelector('svg');"
                "return s ? 'svg:'+(s.innerHTML || '') : '';}))"
            )
            cross = [s[:70] for s in (json.loads(float_icons or "[]")) if s in bar_icons]
            check("地图浮动条不与顶部工具栏撞图标", not cross, " || ".join(cross[:3]))
            # 浮动条也换成 QGIS 官方图标了（识别 / 测距 / 测面积）
            float_qgis = await cdp.evaluate(
                "document.querySelectorAll('#mapTools img.ic[src*=\"/static/icons/qgis/\"]').length"
            )
            check("浮动条用 QGIS 官方图标", float_qgis == 3, str(float_qgis))
            # 图标文件**真的能取到**：404 时 <img> 不会报错，页面上就是一片空白，
            # 不看源码、不截图根本发现不了 —— 所以这里直接请求一遍静态资源。
            for fn in ("mActionPan.svg", "mActionIdentify.svg", "mActionToggleEditing.svg"):
                try:
                    rr = httpx.get(base_url + "/static/icons/qgis/" + fn, timeout=20)
                    check(f"QGIS 图标 {fn} 可取到",
                          rr.status_code == 200 and b"<svg" in rr.content,
                          str(rr.status_code))
                except Exception as exc:  # noqa: BLE001
                    check(f"QGIS 图标 {fn} 可取到", False, repr(exc))
            # 工具栏按钮的悬停提示曾经写成「undefined · 添加图层」——
            # 分组名在 TOOLBARS 的**外层**对象上，item 里没有 group 这个键。
            # 按钮本身看着完全正常，不悬停根本发现不了，所以这里单独钉一条。
            titles = await cdp.evaluate(
                "JSON.stringify(Array.from(document.querySelectorAll('#toolbars button'))"
                ".map(b => b.getAttribute('title') || ''))"
            )
            bad_titles = [t for t in json.loads(titles or "[]")
                          if "undefined" in t.lower()]
            check("工具栏按钮的提示没有 undefined", not bad_titles,
                  "; ".join(bad_titles[:6]))
            check("工具栏按钮的提示带分组名",
                  all("·" in t for t in json.loads(titles or "[]") if t),
                  "; ".join([t for t in json.loads(titles or "[]") if t and "·" not in t][:4]))
            check("工具栏在菜单栏下方、地图上方",
                  await cdp.evaluate(
                      "(() => {const t=document.querySelector('#toolbars');const m=document.querySelector('.menubar');"
                      "const mp=document.querySelector('main');"
                      "return !!(t&&m&&mp&&m.getBoundingClientRect().bottom<=t.getBoundingClientRect().top+1"
                      "&&t.getBoundingClientRect().bottom<=mp.getBoundingClientRect().top+1);})()"
                  ))

            # ------------------------------------------------------------ 面板
            print("\n== 右栏面板 ==")
            panes = await cdp.evaluate(
                "Array.from(document.querySelectorAll('.right .tabs .tab')).map(e=>e.textContent)"
            )
            for label in ("属性表", "统计", "选择", "书签", "日志", "历史"):
                check(f"右栏有「{label}」面板", label in panes, str(panes))
            for pid in ("paneTable", "paneStats", "paneSel", "paneBookmark", "paneLog", "paneHist"):
                check(f"面板 {pid} 有内容",
                      await cdp.evaluate(f"!!document.querySelector('#{pid}')"))

            # 先往地图上加一个矢量图层，面板才有东西可显示
            ref = await cdp.evaluate(
                """(async () => {
                   const r = await fetch('/api/layers');
                   const j = await r.json();
                   const l = (j.layers||[]).find(x => x.kind === 'vector');
                   if(!l) return null;
                   await addLayer({...l, _type:'vector'});
                   return l.ref;
                })()"""
            )
            check("已把矢量图层加到地图", bool(ref), str(ref))
            await asyncio.sleep(1.0)

            # ------------------------------------------------------------ 属性表
            print("\n== 属性表面板 ==")
            await cdp.click('#toolbars button[data-act="openTable"]')
            await asyncio.sleep(1.2)
            check("工具栏「打开属性表」切到属性表面板",
                  await cdp.evaluate("document.querySelector('#paneTable').classList.contains('on')"))
            check("属性表列出了图层下拉",
                  await cdp.evaluate("document.querySelectorAll('#tblLayer option').length >= 1"))
            head = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#tblEl thead th')).map(e=>e.textContent)"
            )
            check("属性表有表头（含 fid 列）", len(head) >= 3, str(head))
            body_rows = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#tblEl tbody tr')).map(e=>e.children.length)"
            )
            check("属性表有数据行", len(body_rows) >= 1, str(body_rows))
            state = await cdp.evaluate("document.querySelector('#tblState').textContent")
            check("属性表状态栏显示要素数", "个要素" in (state or ""), str(state))

            # 存一张截图，方便肉眼核对工具栏与面板的排版
            try:
                shot = await cdp.call("Page.captureScreenshot", {"format": "png"})
                import base64

                out = PROJECT_ROOT / "logs" / "panels_ui.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(shot["data"]))
                print(f"    截图：{out}")
            except Exception as exc:  # pragma: no cover - 截图失败不影响自检
                print(f"    截图失败：{exc}")

            # ------------------------------------------------------------ 统计
            print("\n== 统计面板 ==")
            await cdp.click('#toolbars button[data-act="openStats"]')
            await asyncio.sleep(1.5)
            check("工具栏「字段统计」切到统计面板",
                  await cdp.evaluate("document.querySelector('#paneStats').classList.contains('on')"))
            stat_rows = await cdp.evaluate(
                "document.querySelectorAll('#statEl tbody tr').length"
            )
            check("统计面板有统计行", stat_rows >= 1, str(stat_rows))
            check("统计字段下拉有内容",
                  await cdp.evaluate("document.querySelectorAll('#statField option').length >= 2"))

            try:
                shot = await cdp.call("Page.captureScreenshot", {"format": "png"})
                import base64

                out = PROJECT_ROOT / "logs" / "panels_stats.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(shot["data"]))
                print(f"    截图：{out}")
            except Exception as exc:  # pragma: no cover
                print(f"    截图失败：{exc}")

            # ------------------------------------------------------------ 选择
            print("\n== 选择面板 ==")
            await cdp.click('#toolbars button[data-act="selectAll"]')
            await asyncio.sleep(1.2)
            sel_state = await cdp.evaluate("document.querySelector('#selState').textContent")
            check("全选后选择面板显示已选数量", "已选" in (sel_state or ""), str(sel_state))
            sel_rows = await cdp.evaluate(
                "document.querySelectorAll('#selEl tbody tr').length"
            )
            check("选择面板列出选中要素", sel_rows >= 1, str(sel_rows))
            hl = await cdp.evaluate(
                "JSON.stringify({paths: document.querySelectorAll('#map svg path').length,"
                " strokes: Array.from(document.querySelectorAll('#map svg path')).slice(0,4)"
                ".map(p=>p.getAttribute('stroke')),"
                " selRef: (window.GISDS_PANELS||{}).sel && window.GISDS_PANELS.sel.ref,"
                " selCount: (window.GISDS_PANELS||{}).sel && window.GISDS_PANELS.sel.fids.size,"
                " sample: (window.GISDS_PANELS||{}).sel && [...window.GISDS_PANELS.sel.fids].slice(0,5),"
                " overlay: (function(){var p=window.GISDS_PANELS; if(!p) return null;"
                " return !!(S.overlays.get(p.sel.ref));})(),"
                " featId: (function(){var p=window.GISDS_PANELS; if(!p) return null;"
                " var ov=S.overlays.get(p.sel.ref); if(!ov) return null; var out=null;"
                " ov.eachLayer(function(ly){ if(out===null) out=String(ly.feature && ly.feature.id); });"
                " return out;})()})"
            )
            check("地图上选中的要素被高亮",
                  await cdp.evaluate(
                      "Array.from(document.querySelectorAll('#map svg path')).some(p => (p.getAttribute('stroke')||'') === '#e6194b')"
                  ),
                  hl)
            await cdp.click('#toolbars button[data-act="selectNone"]')
            await asyncio.sleep(0.8)
            check("取消选择后高亮消失",
                  not await cdp.evaluate(
                      "Array.from(document.querySelectorAll('#map svg path')).some(p => (p.getAttribute('stroke')||'') === '#e6194b')"
                  ))

            # ------------------------------------------------------------ 书签
            print("\n== 空间书签 ==")
            await cdp.click('.right .tabs .tab[data-pane="paneBookmark"]')
            await asyncio.sleep(1.0)
            check("书签面板已切出", await cdp.evaluate("document.querySelector('#paneBookmark').classList.contains('on')"))
            clicked = await cdp.click("#bmAdd")
            await asyncio.sleep(0.5)
            bm_diag = await cdp.evaluate(
                "(() => {const e=document.querySelector('#bmAdd');"
                " if(!e) return JSON.stringify({btn:false});"
                " const r=e.getBoundingClientRect();"
                " const at=document.elementFromPoint(r.x+r.width/2, r.y+r.height/2);"
                " const box=(sel)=>{const n=document.querySelector(sel); if(!n) return null;"
                " const b=n.getBoundingClientRect();"
                " return [Math.round(b.x),Math.round(b.y),Math.round(b.width),Math.round(b.height),"
                " n.scrollTop, n.scrollHeight, n.clientHeight];};"
                " return JSON.stringify({modals: document.querySelectorAll('.modal').length,"
                " shown: document.querySelectorAll('.modal.show').length,"
                " hasName: !!document.querySelector('.modal.show #bmName'),"
                " rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],"
                " at: at ? (at.id || at.className || at.tagName) : null,"
                " bound: typeof e.onclick === 'function',"
                " menubar: box('.menubar'), toolbars: box('#toolbars'), main: box('main'),"
                " right: box('aside.right'), tabs: box('.right .tabs'),"
                " pane: box('#paneBookmark'), innerH: window.innerHeight});})()"
            )
            check("「新建」弹出书签对话框",
                  await cdp.evaluate("!!document.querySelector('.modal.show #bmName')"),
                  f"clicked={clicked} {bm_diag}")
            await cdp.type_text(".modal.show #bmName", "UI 自检书签")
            await cdp.click(".modal.show [data-role='ok']")
            await asyncio.sleep(1.2)
            bm_names = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#bmEl tr[data-name]')).map(e=>e.dataset.name)"
            )
            check("书签已建好并列出", "UI 自检书签" in bm_names, str(bm_names))
            await cdp.click("#bmEl tr[data-name] a[data-act='zoom']")
            await asyncio.sleep(0.8)
            check("点书签能缩放", await cdp.evaluate("!!document.querySelector('#map')"))

            # ------------------------------------------------------------ 日志
            print("\n== 日志面板 ==")
            await cdp.click('.right .tabs .tab[data-pane="paneLog"]')
            await asyncio.sleep(1.2)
            log_text = await cdp.evaluate("document.querySelector('#logEl').textContent")
            check("日志面板有内容", bool(log_text and log_text.strip()), str(log_text)[:120])

            # ------------------------------------------------------------ 处理历史
            print("\n== 处理历史面板 ==")
            await cdp.click('.right .tabs .tab[data-pane="paneHist"]')
            await asyncio.sleep(1.2)
            hist_state = await cdp.evaluate("document.querySelector('#histState').textContent")
            check("历史面板能读出作业记录", "作业记录" in (hist_state or ""), str(hist_state))

            # ------------------------------------------------------------ 脚本错误
            print("\n== 页面脚本 ==")
            await asyncio.sleep(0.8)
            collector.cancel()
            check("页面没有未捕获的脚本报错", not console_errors, "; ".join(console_errors[:3]))

            # ------------------------------------------------------------ undefined
            # 面板全都填上内容之后再扫一遍：属性表/统计/选择/书签/历史里
            # 任何一处把 undefined 拼进界面（提示、标签、单元格、默认值）都在这儿露出来。
            print("\n== 界面里没有 undefined ==")
            undef = await scan_undefined(cdp)
            check("面板内容里没有 undefined", not undef, "; ".join(undef[:6]))

            # 收尾
            await cdp.evaluate("fetch('/api/processing/jobs', {method:'DELETE'}).catch(()=>{})")
            await cdp.evaluate(
                "fetch('/api/panels/bookmarks?name=' + encodeURIComponent('UI 自检书签'), {method:'DELETE'})"
                ".catch(()=>{})"
            )
            await asyncio.sleep(0.5)

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

    return summary()


if __name__ == "__main__":
    print("== 面板与工具栏 UI 自检（自起临时服务） ==")
    with TestServer(port=SERVER_PORT) as _server:
        code = asyncio.run(run(_server.base))
    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    os._exit(code)
