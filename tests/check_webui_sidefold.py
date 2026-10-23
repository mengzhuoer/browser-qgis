"""网页端「侧栏可收起」自检：无头 Edge + CDP，真点真量。

验的是四件事：
1. 两个面板头部都有折叠按钮；
2. 点一下 → 那一列宽度变 0、屏幕边缘出现把手、地图变宽；
3. 点边缘把手 → 面板回来，宽度还原成收起前那个值；
4. 收起状态刷新后还在（存在 localStorage）。

跑法::

    run-qgis-python.bat tests/check_webui_sidefold.py
"""

from __future__ import annotations

import asyncio
import json
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

CDP_PORT = int(os.environ.get("GISDS_CDP_PORT_SIDEFOLD") or _free_port(9373))
USERNAME = "admin"
PASSWORD = "sidefold123"

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


WIDTH_JS = """(() => {
  const main = document.querySelector('main');
  const cs = getComputedStyle(main);
  const left = document.querySelector('aside.left');
  const right = document.querySelector('aside.right');
  return JSON.stringify({
    wleft: parseFloat(cs.getPropertyValue('--w-left')) || 0,
    wright: parseFloat(cs.getPropertyValue('--w-right')) || 0,
    mapw: document.querySelector('#map') ? document.querySelector('#map').clientWidth : 0,
    leftFolded: left ? left.classList.contains('folded') : null,
    rightFolded: right ? right.classList.contains('folded') : null,
    tabLeft: !!document.querySelector('#tabLeft'),
    tabRight: !!document.querySelector('#tabRight'),
    bodyLeft: document.body.classList.contains('folded-left'),
    bodyRight: document.body.classList.contains('folded-right'),
    tabLeftShown: (() => { const e=document.querySelector('#tabLeft');
      return e ? getComputedStyle(e).display !== 'none' : false; })(),
    tabRightShown: (() => { const e=document.querySelector('#tabRight');
      return e ? getComputedStyle(e).display !== 'none' : false; })(),
  });
})()"""


async def widths(cdp) -> dict:
    raw = await cdp.evaluate(WIDTH_JS)
    return json.loads(raw or "{}")


async def run(base: str) -> int:
    browser = find_browser()
    if not browser:
        print("  找不到 Edge / Chrome，跳过侧栏收起自检")
        return 0

    profile = tempfile.mkdtemp(prefix="gisds_cdp_fold_")
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
            check("登录后进了控制台",
                  await cdp.evaluate("location.pathname === '/'"),
                  await cdp.evaluate("location.pathname"))

            # ---- 折叠按钮在不在
            check("左栏有折叠按钮", await cdp.evaluate("!!document.querySelector('#btnFoldLeft')"))
            check("右栏有折叠按钮", await cdp.evaluate("!!document.querySelector('#btnFoldRight')"))

            w0 = await widths(cdp)
            check("初始两侧栏都是展开的",
                  not w0.get("leftFolded") and not w0.get("rightFolded"), json.dumps(w0))
            check("初始边缘把手是隐藏的",
                  not w0.get("tabLeftShown") and not w0.get("tabRightShown"), json.dumps(w0))
            map_w0 = w0.get("mapw") or 0

            # ---- 收左栏
            await cdp.click("#btnFoldLeft")
            await asyncio.sleep(0.6)
            w1 = await widths(cdp)
            check("左栏收起后宽度为 0", (w1.get("wleft") or 0) < 1, json.dumps(w1))
            check("左栏收起后带上了 folded 类", w1.get("leftFolded") is True, json.dumps(w1))
            check("左栏收起后屏幕左边出现把手", w1.get("tabLeftShown") is True, json.dumps(w1))
            check("左栏收起后地图变宽了", (w1.get("mapw") or 0) > map_w0,
                  f"{map_w0} -> {w1.get('mapw')}")
            check("右栏不受影响", w1.get("rightFolded") is False, json.dumps(w1))

            # ---- 拉回左栏
            await cdp.click("#tabLeft")
            await asyncio.sleep(0.6)
            w2 = await widths(cdp)
            check("点边缘把手后左栏回来了", w2.get("leftFolded") is False, json.dumps(w2))
            check("左栏宽度还原成收起前的值",
                  abs((w2.get("wleft") or 0) - (w0.get("wleft") or 0)) < 2, json.dumps(w2))
            check("左栏回来后把手又藏起来了", w2.get("tabLeftShown") is False, json.dumps(w2))

            # ---- 收右栏
            await cdp.click("#btnFoldRight")
            await asyncio.sleep(0.6)
            w3 = await widths(cdp)
            check("右栏收起后宽度为 0", (w3.get("wright") or 0) < 1, json.dumps(w3))
            check("右栏收起后屏幕右边出现把手", w3.get("tabRightShown") is True, json.dumps(w3))
            check("右栏收起后左栏不受影响", w3.get("leftFolded") is False, json.dumps(w3))

            # ---- 两边同时收
            await cdp.click("#btnFoldLeft")
            await asyncio.sleep(0.5)
            w4 = await widths(cdp)
            check("两边可以同时收起",
                  (w4.get("wleft") or 0) < 1 and (w4.get("wright") or 0) < 1, json.dumps(w4))
            check("两边都收起后地图占满",
                  (w4.get("mapw") or 0) > (w1.get("mapw") or 0),
                  f"{w1.get('mapw')} -> {w4.get('mapw')}")

            # ---- 刷新后状态还在
            await cdp.call("Page.reload")
            await asyncio.sleep(2.2)
            w5 = await widths(cdp)
            check("刷新后两侧仍然是收起的",
                  w5.get("leftFolded") is True and w5.get("rightFolded") is True, json.dumps(w5))

            # ---- 拉回来，收拾干净
            await cdp.click("#tabLeft")
            await asyncio.sleep(0.4)
            await cdp.click("#tabRight")
            await asyncio.sleep(0.4)
            await cdp.evaluate("localStorage.removeItem('gisds.fold')")

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
    tmp = Path(tempfile.mkdtemp(prefix="gisds_fold_"))
    with TestServer(port=_free_port(8873), require_login=True, data_root=tmp,
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
