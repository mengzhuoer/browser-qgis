"""网页端「项目属性」对话框自检：无头 Edge + CDP，真点真量。

验的是：
1. 左边那套分区导航在（QGIS 桌面版那一套）；
2. 坐标参考系页：过滤器、预定义清单、最近使用；
3. 点一行 → 详情（名称 / 属性 / WKT / 适用范围）出来；
4. **适用范围预览图真的加载出来了**（naturalWidth > 0）——
   这个接口要登录，所以必须带令牌取 blob，用 <img src> 直接指会 401；
5. 搜索能打到服务端的全量 EPSG 目录（本地清单里没有的名字也搜得到）。

跑法::

    run-qgis-python.bat tests/check_webui_settings.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from check_webui import CDP, find_browser  # noqa: E402
from server_helper import TestServer, _free_port, kill_tree  # noqa: E402

CDP_PORT = int(os.environ.get("GISDS_CDP_PORT_SETTINGS") or _free_port(9375))
USERNAME = "admin"
PASSWORD = "settings123"

_PASSED = 0
_FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


async def run(base: str) -> int:
    browser = find_browser()
    if not browser:
        print("  找不到 Edge / Chrome，跳过项目属性自检")
        return 0

    profile = tempfile.mkdtemp(prefix="gisds_cdp_set_")
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
            base + "/login",
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
                    if (t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                            and t.get("url", "").startswith(base)):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.4)
        if not ws_url:
            print("  无法连接无头浏览器")
            return 1

        async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await asyncio.sleep(1.2)

            await cdp.type_text("#username", USERNAME)
            await cdp.type_text("#password", PASSWORD)
            await cdp.click("#loginForm button[type=submit]")
            await asyncio.sleep(1.8)

            # ---- 打开对话框
            await cdp.evaluate("window.openProjectProperties()")
            await asyncio.sleep(1.2)
            check("项目属性对话框打开了",
                  await cdp.evaluate("!!document.querySelector('#dlgSettings.show')"))

            # ---- 左边那套分区导航
            nav = await cdp.evaluate(
                "JSON.stringify([...document.querySelectorAll('#dlgSettings .sd-nav .n span')]"
                ".map(s=>s.textContent))")
            import json as _json
            items = _json.loads(nav or "[]")
            check("左侧分区导航是 QGIS 那一套（15 项）", len(items) == 15, str(items))
            for want in ("常规", "元数据", "视图设置", "坐标参考系", "样式", "颜色",
                         "数据源", "变量", "QGIS Server", "时态", "高程"):
                check(f"分区里有「{want}」", want in items, str(items))

            # ---- 坐标参考系页
            check("默认落在「坐标参考系」页",
                  await cdp.evaluate(
                      "!!document.querySelector('#dlgSettings h3') && "
                      "document.querySelector('#dlgSettings h3').textContent.indexOf('坐标参考系')>=0"))
            check("有过滤器输入框", await cdp.evaluate("!!document.querySelector('#crsFilter')"))
            check("有「无 CRS」复选框", await cdp.evaluate("!!document.querySelector('#crsNone')"))
            check("有「预定义的坐标参考系统」表",
                  await cdp.evaluate(
                      "!!document.querySelector('#dlgSettings .box .hd') && "
                      "[...document.querySelectorAll('#dlgSettings .box .hd')]"
                      ".some(h=>h.textContent.indexOf('预定义')>=0)"))
            rows = await cdp.evaluate(
                "document.querySelectorAll('#dlgSettings tr[data-crs]').length")
            check("预定义清单里有条目", rows > 3, f"{rows} 行")

            # ---- 点一行看详情
            await cdp.evaluate(
                "document.querySelector('#dlgSettings tr[data-crs]').click()")
            await asyncio.sleep(1.6)
            check("详情里有 CRS 名称",
                  await cdp.evaluate(
                      "!!document.querySelector('#dlgSettings .detail h3') && "
                      "document.querySelector('#dlgSettings .detail h3').textContent.length>1"))
            check("详情里有 WKT",
                  await cdp.evaluate(
                      "!!document.querySelector('#crsWkt') && "
                      "document.querySelector('#crsWkt').textContent.length>20"))
            check("详情里有属性表（类型/椭球/适用范围）",
                  await cdp.evaluate(
                      "document.querySelectorAll('#dlgSettings .detail .kv dt').length>=4"))
            # 预览图：接口要登录，所以是带令牌取的 blob
            img = await cdp.evaluate("""(() => {
              const i = document.querySelector('#crsPreviewImg');
              if(!i) return JSON.stringify({exists:false});
              return JSON.stringify({exists:true, w:i.naturalWidth, h:i.naturalHeight,
                                    alt:i.alt||''});
            })()""")
            info = _json.loads(img or "{}")
            check("适用范围预览图真的加载出来了（naturalWidth>0）",
                  info.get("w", 0) > 0, img)

            # ---- 搜索：本地清单里没有的名字也要搜得到
            await cdp.type_text("#crsFilter", "CGCS2000")
            await asyncio.sleep(2.0)
            rows2 = await cdp.evaluate(
                "document.querySelectorAll('#dlgSettings tr[data-crs]').length")
            check("按名字能搜到服务端目录里的 CRS", rows2 > 0, f"{rows2} 行")

            # ---- 别的分区：老实说「暂未接入」，不假装能用
            await cdp.evaluate(
                "[...document.querySelectorAll('#dlgSettings .sd-nav .n')]"
                ".find(n=>n.textContent.indexOf('样式')>=0).click()")
            await asyncio.sleep(0.6)
            check("没接入的分区会明说「还没接入」",
                  await cdp.evaluate(
                      "!!document.querySelector('#dlgSettings .note') && "
                      "document.querySelector('#dlgSettings .note').textContent.indexOf('还没接入')>=0"))

            # ---- 菜单里有这一项
            menu = await cdp.evaluate("""(() => {
              try { return (MENUS.file()||[]).map(x=>x.label||'').join('|'); }
              catch(e){ return 'ERR:'+e.message; }
            })()""")
            check("「文件」菜单里有「项目属性…」", "项目属性" in (menu or ""), str(menu))

            errs = await cdp.evaluate("JSON.stringify(window.__errs||[])")
            check("页面没有脚本报错", (errs or "[]") in ("[]", ""), str(errs)[:200])
    finally:
        try:
            kill_tree(proc.pid)
            proc.wait(timeout=8)
        except Exception:
            pass
    return 0


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gisds_set_"))
    with TestServer(port=_free_port(8875), require_login=True, data_root=tmp,
                    extra_env={"GISDS_ADMIN_PASSWORD": PASSWORD}) as server:
        code = asyncio.run(run(server.base))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    if _FAILED:
        print("  失败项见上")
    return code or (1 if _FAILED else 0)


if __name__ == "__main__":
    code = main()
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    import os as _os

    _os._exit(code)
