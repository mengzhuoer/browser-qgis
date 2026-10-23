"""网页登录流程自检（无头 Edge + CDP，真填真点）。

覆盖真人登录时会走的完整链路：
注册 → 登录 → 进控制台 → 顶栏显示当前用户 → 退出 → 再登录。

之前 check_auth.py 只测到接口层，网页上到底能不能登进去没测过；
「登不上去」这类问题恰恰出在这一层（令牌没存、401 循环跳回登录页）。

独立运行::

    run-qgis-python.bat tests/check_webui_login.py
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

from check_webui import CDP, find_browser  # noqa: E402
from server_helper import TestServer, _free_port, kill_tree  # noqa: E402

# 端口要躲开别的自检脚本（并行会话同时跑时撞端口会串台）
CDP_PORT = int(os.environ.get("GISDS_CDP_PORT_LOGIN") or _free_port(9365))
SERVER_PORT = _free_port(8865)

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 19 = 登录/进控制台/退出等主流程 19 条（逐条对过源码）。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 19

USERNAME = "webuser"
PASSWORD = "webuser123"


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
        print("  找不到 Edge / Chrome，跳过登录 UI 自检")
        # 一条断言都没跑却报「成功」是最坑的 —— 走同一道钉子，当场红。
        return summary()

    profile = tempfile.mkdtemp(prefix="gisds_cdp_login_")
    page_url = base_url + "/login"
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
            await asyncio.sleep(1.2)

            check("登录页打开了", await cdp.evaluate("location.pathname === '/login'"),
                  await cdp.evaluate("location.pathname"))
            check("登录表单存在", await cdp.evaluate("!!document.querySelector('#loginForm')"))

            # ---- 注册
            reg_visible = await cdp.evaluate("document.querySelector('#regBox').classList.contains('on')")
            check("开了自助注册时显示注册框", reg_visible)
            await cdp.type_text("#regUser", USERNAME)
            await cdp.type_text("#regPass", PASSWORD)
            await cdp.click("#regBtn")
            await asyncio.sleep(1.5)
            check("注册后跳进控制台",
                  await cdp.evaluate("location.pathname === '/'"),
                  await cdp.evaluate("location.pathname"))
            stored = await cdp.evaluate("localStorage.getItem('gisds.token')")
            check("令牌已存进 localStorage", bool(stored), str(stored)[:20])

            # ---- 控制台顶栏显示当前用户
            who = await cdp.evaluate("(document.querySelector('#userBox')||{}).textContent")
            check("顶栏显示当前用户名", USERNAME in (who or ""), str(who))

            # ---- 空地图引导：放在**下沿**、别太显眼
            # 这个临时服务的数据目录是空的 → 控制台里一个图层都没有，
            # 正好能验「还没有图层」那条提示。
            # 原来它是在地图正中间摆一张卡片（带边框和阴影）—— 空地图时它就是
            # 屏幕的主角，反而太吵。
            hint = await cdp.evaluate("""(() => {
              const h = document.querySelector('#mapHint');
              const m = document.querySelector('#map');
              if(!h || !m) return '{}';
              const hr = h.getBoundingClientRect(), mr = m.getBoundingClientRect();
              const cs = getComputedStyle(h);
              return JSON.stringify({
                shown: !h.classList.contains('hide'),
                lowerHalf: (hr.top - mr.top) > mr.height / 2,
                gapBottom: Math.round(mr.bottom - hr.bottom),
                bg: cs.backgroundColor, border: cs.borderTopWidth,
                fs: parseFloat(cs.fontSize)
              });
            })()""")
            h = json.loads(hint or "{}")
            check("空地图时给出提示", h.get("shown") is True, hint[:160])
            check("提示在地图下半部分（不在正中抢视线）", h.get("lowerHalf") is True, hint[:160])
            check("提示不是一张卡片（无背景无边框）",
                  h.get("bg") in ("rgba(0, 0, 0, 0)", "transparent")
                  and h.get("border") in ("0px", ""), hint[:160])
            check("提示是小字（不太显眼）", (h.get("fs") or 99) <= 12, str(h.get("fs")))

            # ---- 退出
            await cdp.click("#btnLogout")
            await asyncio.sleep(1.2)
            check("退出后回到登录页",
                  await cdp.evaluate("location.pathname === '/login'"),
                  await cdp.evaluate("location.pathname"))
            check("退出后令牌已清",
                  not await cdp.evaluate("localStorage.getItem('gisds.token')"))

            # ---- 再登录
            await cdp.type_text("#username", USERNAME)
            await cdp.type_text("#password", PASSWORD)
            await cdp.click("#loginForm button[type=submit]")
            await asyncio.sleep(1.5)
            check("再登录成功进控制台",
                  await cdp.evaluate("location.pathname === '/'"),
                  await cdp.evaluate("location.pathname"))
            who2 = await cdp.evaluate("(document.querySelector('#userBox')||{}).textContent")
            check("再次登录后顶栏仍是本人", USERNAME in (who2 or ""), str(who2))

            # ---- 登录态下，栅格图层必须真的画到地图上
            #
            # 之前 drawLayer() 用的是**裸 fetch**，不带 Authorization 头 →
            # 登录态下服务端回 401 → blob() 拿到的是一段 JSON 错误而不是 PNG →
            # imageOverlay 加载失败 → 地图上什么都不显示，**而且不报任何错**。
            # 免登录模式下测不出来（401 根本不会发生），所以这条断言
            # 必须跑在 require_login=True 的服务上。
            tif = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example" / "classes.tif"
            web_token = httpx.post(base_url + "/api/auth/login",
                                   json={"username": USERNAME, "password": PASSWORD},
                                   timeout=30).json().get("token")
            up = httpx.post(base_url + "/api/uploads",
                            files={"files": ("web_raster.tif", tif.read_bytes(), "image/tiff")},
                            params={"scope": "me"},
                            headers={"Authorization": "Bearer " + (web_token or "")},
                            timeout=180)
            up_res = (up.json().get("results") or [{}])[0] if up.status_code == 200 else {}
            check("上传栅格成功", up.status_code == 200, up.text[:160])
            check("上传的栅格被识别成图层", up_res.get("registered") is True,
                  str(up_res)[:160])
            ds_id = up_res.get("dataset_id")
            if ds_id:
                # 网页里走「添加图层 → 服务器共享数据 → 勾选 → 打开」
                # 那个分类读的是 S.layers（开机时加载的），所以先刷新一次 ——
                # 正常上传流程里 uploadFiles() 会做这件事，这里是直接调 API 上传的。
                await cdp.evaluate("refreshData(); true")
                await asyncio.sleep(1.0)
                await cdp.evaluate("openAddDialog('server'); true")
                await asyncio.sleep(0.8)
                await cdp.evaluate(
                    "(() => {const cb=document.querySelector('#addForm input[type=checkbox]');"
                    "if(cb){cb.checked=true;} return true;})()"
                )
                await cdp.click("#addOk")
                await asyncio.sleep(4.0)
                # 栅格图层现在按**层级出瓦片**（不是整层渲成一张大图铺上去），
                # 所以看的是 img.leaflet-tile，且瓦片是异步加载的。
                ov = ""
                for _ in range(20):
                    ov = await cdp.evaluate(
                        """(() => {
                          const imgs = Array.from(document.querySelectorAll('img.leaflet-tile[data-layer]'));
                          return JSON.stringify(imgs.map(i => ({w: i.naturalWidth, h: i.naturalHeight,
                                                            complete: i.complete})));
                        })()"""
                    )
                    if json.loads(ov or "[]"):
                        break
                    await asyncio.sleep(0.4)
                ovs = json.loads(ov or "[]")
                check("登录态下栅格图层真的显示在地图上",
                      any(i.get("w", 0) > 0 for i in ovs), ov[:200])
                httpx.delete(base_url + "/api/uploads/" + ds_id,
                             headers={"Authorization": "Bearer " + (web_token or "")},
                             timeout=60)
            else:
                # 上传没返回 dataset_id -> 这条断言**判失败**，而不是整段跳过
                # （跳过会让断言总数变少，钉子就看不出少了哪几条）
                check("登录态下栅格图层真的显示在地图上", False, "上传没返回 dataset_id")

            # ---- 密码错误要有明确提示，而不是静默失败
            await cdp.evaluate("localStorage.clear()")
            await cdp.evaluate("location.href = '/login'")
            await asyncio.sleep(1.2)
            await cdp.type_text("#username", USERNAME)
            await cdp.type_text("#password", "wrong-password")
            await cdp.click("#loginForm button[type=submit]")
            await asyncio.sleep(1.2)
            err = await cdp.evaluate("(document.querySelector('#err')||{}).textContent")
            check("密码错误时页面给出提示", bool(err and err.strip()), str(err))
            check("密码错误时留在登录页",
                  await cdp.evaluate("location.pathname === '/login'"),
                  await cdp.evaluate("location.pathname"))

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
    print("== 网页登录流程自检（自起临时服务） ==")
    tmp_data = Path(tempfile.mkdtemp(prefix="gisds_login_data_"))
    with TestServer(
        port=SERVER_PORT,
        require_login=True,
        data_root=tmp_data,
        extra_env={"GISDS_ALLOW_REGISTRATION": "1"},
    ) as _server:
        code = asyncio.run(run(_server.base))
    shutil.rmtree(tmp_data, ignore_errors=True)
    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    os._exit(code)
