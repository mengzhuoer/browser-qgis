# -*- coding: utf-8 -*-
"""网页端「管理员」分区自检：无头 Edge + CDP，真点真跑。

验的是用户在网页里管事的那条路：

- 登录页有一块「共享超级账号」，写着内置的那组口令（用户问「所有人怎么用」
  时，第一眼就要看得见）；
- **管理员**登录后，右栏多出「管理员」页签，里面有 用户 / 空间 / 审计 / 配置
  四块，用户表真的列出了账号；
- **普通用户**登录后，这个页签**根本不存在** —— 不是点了报错，是压根没渲染；
- 所有登录用户都能看到 Python 控制台的把手（console_access=user）。

用系统 QGIS Python 跑（要 httpx / websockets）::

    run-qgis-python.bat tests/check_webui_admin.py
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
from server_helper import TestServer, _free_port  # noqa: E402

# CDP 端口用**底座那份** ``_free_port``（主会话 21:26 修好的双探测：
# 连一下 + 不带 SO_REUSEADDR 的 bind）。本地再写一份探测是**重复实现**，
# 而且必然比底座弱 —— 底座那份两个条件都查。
#
# 端口段 9435 起：别的浏览器自检占着 9333/9364/9365/9371/9373/9375/9402，
# 而 ``_free_port`` 会从起点往上顺延最多 30 个（9402 那支能爬到 9432），
# 所以从 9435 起才不会跟它们撞。
CDP_PORT = int(os.environ.get("GISDS_CDP_PORT_ADMIN") or 0)
SUPER_USER = "admin"
SUPER_PASS = "test-super-pass-2026"
NORMAL_USER = "normaluser"
NORMAL_PASS = "normalpass123"

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 27 = 登录页共享账号 6 + 管理员会话 15 + 普通用户会话 6
EXPECTED_CHECKS = 27


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def api_json(base: str, token: str, method: str, path: str, **kw):
    headers = dict(kw.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.request(method, base + path, headers=headers, timeout=60, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


async def _open_browser(browser: str, base: str):
    """起浏览器、连上 CDP、导航到登录页。返回 (进程, websocket)。

    连不上就抛异常，由调用方换一个端口重试。**断言一条都不在这里跑** ——
    重试不能把断言跑两遍（汇总数会跟着变）。
    """
    cdp_port = CDP_PORT or _free_port(9435)
    profile = tempfile.mkdtemp(prefix="gisds_cdp_admin_")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--window-size=1600,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ws_url = None
    try:
        for _ in range(80):
            try:
                targets = httpx.get(f"http://127.0.0.1:{cdp_port}/json/list", timeout=3).json()
                for t in targets:
                    if (t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                            and t.get("url", "") in ("about:blank", "", t.get("url"))):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.4)
        if not ws_url:
            raise RuntimeError(f"端口 {cdp_port} 上没有可连的浏览器")

        ws = await websockets.connect(ws_url, max_size=32 * 1024 * 1024)
        cdp = CDP(ws)
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")
        # **在页面脚本跑之前**装上错误收集器。
        #
        # 以前那种「页面里有个 window.__errs 就查它」的做法是**空过** ——
        # 网页里压根没定义过这个变量，表达式恒为 []，断言永远绿。
        # addScriptToEvaluateOnNewDocument 是在**任何**页面脚本之前跑的，
        # 所以连加载期的报错也收得到。
        await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            (function(){
              window.__errs = [];
              window.addEventListener('error', function(e){
                window.__errs.push(String((e && (e.message || e.error)) || e));
              });
              window.addEventListener('unhandledrejection', function(e){
                window.__errs.push('unhandledrejection: ' + String(e && e.reason));
              });
            })();
        """})
        await cdp.call("Page.navigate", {"url": base + "/login"})
        await asyncio.sleep(2.0)
        # 确认真站在**我们自己的**登录页上（而不是连到了某个残留浏览器）
        where = await cdp.evaluate("location.pathname")
        if where != "/login":
            raise RuntimeError(f"页面不是登录页（pathname={where!r}）")
        return proc, ws
    except BaseException:
        _kill_browser(proc)
        raise


def _kill_browser(proc) -> None:
    """把浏览器**连同它的子进程**一起收掉。

    只 ``terminate()`` 掉父进程是不够的：Edge 的子进程还活着，
    **调试端口继续被占着** —— 下一支自检按同样的端口起浏览器就会绑不上，
    然后连到我们这个残留浏览器上，WebSocket 断在半路。
    """
    if proc is None:
        return
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, timeout=20, text=True, errors="replace")
    except Exception:
        pass
    try:
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


async def run(base: str) -> None:
    browser = find_browser()
    if not browser:
        print("  找不到 Edge / Chrome，跳过管理员分区自检")
        return

    proc = None
    ws = None
    for attempt in range(3):
        try:
            proc, ws = await _open_browser(browser, base)
            break
        except Exception as exc:
            print(f"  第 {attempt + 1} 次没连上浏览器（{exc}），换端口重试")
            await asyncio.sleep(1.0)
    if ws is None:
        print("  连不上无头浏览器")
        return

    try:
        async with ws:
            cdp = CDP(ws)

            # ================================================== 登录页
            print("\n== 登录页：共享超级账号 ==")
            box = await cdp.evaluate(
                "!!document.querySelector('#sharedBox.on')")
            check("登录页显示「共享超级账号」", bool(box),
                  await cdp.evaluate("!!document.querySelector('#sharedBox')"))
            check("共享账号的用户名是 admin",
                  await cdp.evaluate("document.querySelector('#shUser').textContent.trim()")
                  == SUPER_USER,
                  str(await cdp.evaluate("document.querySelector('#shUser').textContent")))
            check("共享账号的口令就是内置那组",
                  await cdp.evaluate("document.querySelector('#shPass').textContent.trim()")
                  == SUPER_PASS,
                  str(await cdp.evaluate("document.querySelector('#shPass').textContent")))
            check("共享账号带着风险说明",
                  bool((await cdp.evaluate(
                      "document.querySelector('#shNote').textContent.trim()") or "")),
                  str(await cdp.evaluate("document.querySelector('#shNote').textContent"))[:100])

            await cdp.click("#shFill")
            await asyncio.sleep(0.4)
            check("点「填入表单」后用户名被填上",
                  await cdp.evaluate("document.querySelector('#username').value")
                  == SUPER_USER,
                  str(await cdp.evaluate("document.querySelector('#username').value")))
            check("点「填入表单」后口令被填上",
                  await cdp.evaluate("document.querySelector('#password').value")
                  == SUPER_PASS,
                  str(await cdp.evaluate("document.querySelector('#password').value")))

            # ---- 登录
            await cdp.click("#loginForm button[type=submit]")
            await asyncio.sleep(1.8)
            check("用共享口令登录后进了控制台",
                  await cdp.evaluate("location.pathname === '/'"),
                  await cdp.evaluate("location.pathname"))

            # ================================================== 管理员会话
            print("\n== 管理员：右栏多出「管理员」页签 ==")
            tabs = await cdp.evaluate(
                "JSON.stringify([...document.querySelectorAll('aside.right .tab')]"
                ".map(t=>t.textContent.trim()))")
            check("右栏有「管理员」页签", "管理员" in (tabs or ""), str(tabs))

            # 登录后管理员面板**默认是收着的** ——
            # 页面里 `.tabpane{display:none}` 管着「没选中的页签藏起来」，
            # 而 `#paneAdmin` 的 ID 优先级更高 —— 一条 `#paneAdmin{display:flex}`
            # 就会把隐藏整个盖掉，这个面板会一直占着右栏一小块。
            check("管理员面板默认收着（没选中的页签不占地方）",
                  await cdp.evaluate(
                      "getComputedStyle(document.querySelector('#paneAdmin')).display === 'none'"),
                  await cdp.evaluate(
                      "getComputedStyle(document.querySelector('#paneAdmin')).display"))

            await cdp.click('aside.right .tab[data-pane="paneAdmin"]')
            await asyncio.sleep(0.8)
            check("点了页签面板就打开了",
                  await cdp.evaluate("!!document.querySelector('#paneAdmin.on')"),
                  await cdp.evaluate("!!document.querySelector('#paneAdmin')"))

            # 切到别的页签 → 管理员面板要收回去；再切回来又要开
            await cdp.evaluate(
                "document.querySelector('aside.right .tab[data-pane=paneIdent]').click()")
            await asyncio.sleep(0.6)
            check("切到别的页签，管理员面板收回去",
                  await cdp.evaluate(
                      "getComputedStyle(document.querySelector('#paneAdmin')).display === 'none'"),
                  await cdp.evaluate(
                      "getComputedStyle(document.querySelector('#paneAdmin')).display"))
            await cdp.click('aside.right .tab[data-pane="paneAdmin"]')
            await asyncio.sleep(0.7)
            check("切回来管理员面板又开了",
                  await cdp.evaluate(
                      "getComputedStyle(document.querySelector('#paneAdmin')).display !== 'none'"),
                  await cdp.evaluate(
                      "getComputedStyle(document.querySelector('#paneAdmin')).display"))

            nav = await cdp.evaluate(
                "JSON.stringify([...document.querySelectorAll('#paneAdmin .ad-navitem')]"
                ".map(x=>x.textContent.trim()))")
            check("面板里有 用户 / 空间 / 审计 / 配置 四个分区",
                  all(k in (nav or "") for k in ("用户", "空间", "审计", "配置")), str(nav))

            rows = await cdp.evaluate(
                "JSON.stringify([...document.querySelectorAll('#adUserRows tr')]"
                ".map(tr=>tr.textContent.trim()))")
            check("用户表里列出了 admin", SUPER_USER in (rows or ""), str(rows)[:200])
            check("用户表里列出了普通用户", NORMAL_USER in (rows or ""), str(rows)[:200])

            # ---- 切到空间
            await cdp.evaluate(
                "document.querySelector('#paneAdmin .ad-navitem[data-view=spaces]').click()")
            await asyncio.sleep(0.8)
            check("「空间」分区有内容",
                  await cdp.evaluate("!!document.querySelector('#paneAdmin #adViewSpaces.on')"),
                  await cdp.evaluate(
                      "!!document.querySelector('#paneAdmin #adViewSpaces.on')"))

            # ---- 切到审计
            await cdp.evaluate(
                "document.querySelector('#paneAdmin .ad-navitem[data-view=audit]').click()")
            await asyncio.sleep(0.8)
            audit_rows = await cdp.evaluate(
                "document.querySelectorAll('#adAuditRows tr').length")
            check("「审计」分区列出了审计记录", int(audit_rows or 0) > 0, str(audit_rows))

            # ---- 切到配置
            await cdp.evaluate(
                "document.querySelector('#paneAdmin .ad-navitem[data-view=config]').click()")
            await asyncio.sleep(0.8)
            cfg_text = await cdp.evaluate(
                "document.querySelector('#adCfgBody').textContent")
            check("「配置」里写明控制台对所有登录用户开放",
                  "所有登录用户" in (cfg_text or ""), str(cfg_text)[:160])
            warn_text = await cdp.evaluate(
                "document.querySelector('#adCfgWarn').textContent")
            check("「配置」里说明了共享超级账号的风险",
                  "共享超级账号" in (warn_text or ""), str(warn_text)[:160])
            check("管理员能看到 Python 控制台的把手",
                  await cdp.evaluate("!!document.querySelector('#gisdsConsole .pc-handle')"),
                  await cdp.evaluate("!!document.querySelector('#gisdsConsole')"))

            errs_admin = await cdp.evaluate("JSON.stringify(window.__errs||[])")
            check("管理员会话没有脚本报错", (errs_admin or "[]") in ("[]", ""),
                  str(errs_admin)[:200])

            # ================================================== 普通用户会话
            print("\n== 普通用户：管理员分区整个不见 ==")
            await cdp.evaluate("document.querySelector('#btnLogout').click()")
            await asyncio.sleep(1.6)
            check("登出后回到登录页",
                  await cdp.evaluate("location.pathname === '/login'"),
                  await cdp.evaluate("location.pathname"))

            await cdp.evaluate("""(() => {
              document.querySelector('#username').value = %s;
              document.querySelector('#password').value = %s;
              document.querySelector('#loginForm button[type=submit]').click();
              return true;
            })()""" % (repr(NORMAL_USER), repr(NORMAL_PASS)))
            await asyncio.sleep(1.8)
            check("普通用户登录后进了控制台",
                  await cdp.evaluate("location.pathname === '/'"),
                  await cdp.evaluate("location.pathname"))

            tabs2 = await cdp.evaluate(
                "JSON.stringify([...document.querySelectorAll('aside.right .tab')]"
                ".map(t=>t.textContent.trim()))")
            check("普通用户看不到「管理员」页签", "管理员" not in (tabs2 or ""), str(tabs2))
            check("普通用户看不到管理员面板",
                  await cdp.evaluate("!document.querySelector('#paneAdmin')"),
                  await cdp.evaluate("!!document.querySelector('#paneAdmin')"))
            check("普通用户能看到 Python 控制台的把手（全员可用）",
                  await cdp.evaluate("!!document.querySelector('#gisdsConsole .pc-handle')"),
                  await cdp.evaluate("!!document.querySelector('#gisdsConsole')"))

            errs = await cdp.evaluate("JSON.stringify(window.__errs||[])")
            check("页面没有脚本报错", (errs or "[]") in ("[]", ""), str(errs)[:200])
    finally:
        # 浏览器连同子进程一起收掉 —— 只 terminate 父进程的话，
        # Edge 的子进程还活着、调试端口继续被占着（下一支自检会中招）。
        _kill_browser(proc)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gisds_wbadmin_"))
    env = {
        # 共享超级账号：临时服务**显式打开**它（server_helper 默认给关掉了）
        "GISDS_SUPER_ADMIN_PASSWORD": SUPER_PASS,
    }
    with TestServer(port=_free_port(8893), require_login=True, data_root=tmp,
                    extra_env=env) as server:
        token = api_json(server.base, "", "POST", "/api/auth/login",
                         json={"username": SUPER_USER, "password": SUPER_PASS})["token"]
        api_json(server.base, token, "POST", "/api/admin/users",
                 json={"username": NORMAL_USER, "password": NORMAL_PASS,
                       "role": "user", "display_name": "普通用户"})
        asyncio.run(run(server.base))
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    if _FAILED:
        print("  失败项见上")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        code = 1

    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    import os as _os

    _os._exit(code)
