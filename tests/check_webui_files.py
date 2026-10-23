"""网页端「文件浏览器」自检：无头 Edge + CDP，真点真跑。

验的是用户在网页里管文件的那条路：
- 「文件」菜单里有「文件浏览器…」这一项；
- 打开后能看到**各个空间**里已上传的文件（含**没被 QGIS 识别**的那个）；
- 表格里有「文件」列（看得见上传的原文件名）和「状态」列（已识别 / 未识别）；
- 空间下拉是「全部 + 共享 + 本人的个人空间」。

用系统 QGIS Python 跑（要 httpx / websockets）::

    run-qgis-python.bat tests/check_webui_files.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from check_webui import CDP, find_browser  # noqa: E402
from server_helper import TestServer, _free_port, kill_tree  # noqa: E402

CDP_PORT = int(os.environ.get("GISDS_CDP_PORT_FILES") or _free_port(9371))
USERNAME = "admin"
PASSWORD = "filetest123"

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 19 = 登录 1 + 菜单/脚本/对话框 3 + 空间下拉 2 + 表格内容 9
#            + 切空间 2 + 关闭 1 + 页面无报错 1。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
# （找不到浏览器时 run() 会提前返回，那时一条断言都没跑 —— 钉子照样红，
#   不会再「什么都没验却报成功」。）
EXPECTED_CHECKS = 19


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def api_json(base: str, token: str, method: str, path: str, **kw):
    h = dict(kw.pop("headers", {}) or {})
    if token:
        h["Authorization"] = f"Bearer {token}"   # 空令牌会变成 "Bearer "，httpx 直接拒
    r = httpx.request(method, base + path, headers=h, timeout=60, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def seed_uploads(base: str, token: str) -> None:
    """先造三种文件：一个正常的、一个中文名的、一个**没被 QGIS 认出来**的。"""
    geo = json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"n": 1},
                      "geometry": {"type": "Point", "coordinates": [112.48, 39.685]}}],
    }).encode()
    api_json(base, token, "POST", "/api/uploads?scope=me",
             files=[("files", ("probe.geojson", geo, "application/json"))])
    api_json(base, token, "POST", "/api/uploads?scope=me",
             files=[("files", ("寿阳测试.geojson", geo, "application/json"))])
    # 只有 .shp、缺 .dbf/.shx/.prj 配套件 → QGIS 打不开 → registered=false
    api_json(base, token, "POST", "/api/uploads?scope=me",
             files=[("files", ("lonely.shp", b"\x00\x01\x02dummy", "application/octet-stream"))])


async def run(base: str) -> int:
    browser = find_browser()
    if not browser:
        print("  找不到 Edge / Chrome，跳过文件浏览器自检")
        return 0

    profile = tempfile.mkdtemp(prefix="gisds_cdp_files_")
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

            # ---- 登录
            await cdp.type_text("#username", USERNAME)
            await cdp.type_text("#password", PASSWORD)
            await cdp.click("#loginForm button[type=submit]")
            await asyncio.sleep(1.6)
            check("登录后进了控制台",
                  await cdp.evaluate("location.pathname === '/'"),
                  await cdp.evaluate("location.pathname"))

            # ---- 「文件」菜单里有这一项
            menu = await cdp.evaluate("""(() => {
              try { return (MENUS.file()||[]).map(x=>x.label||'').join('|'); }
              catch(e){ return 'ERR:'+e.message; }
            })()""")
            check("「文件」菜单里有「文件浏览器…」", "文件浏览器" in (menu or ""), str(menu))

            # ---- 脚本挂上了
            check("文件浏览器脚本已加载",
                  await cdp.evaluate("typeof window.openFilesBrowser === 'function'"),
                  await cdp.evaluate("typeof window.openFilesBrowser"))

            # ---- 打开对话框
            await cdp.evaluate("window.openFilesBrowser()")
            await asyncio.sleep(1.4)
            check("文件浏览器对话框打开了",
                  await cdp.evaluate("!!document.querySelector('#dlgFiles.show')"),
                  await cdp.evaluate("!!document.querySelector('#dlgFiles')"))

            # ---- 空间下拉
            scopes = await cdp.evaluate("""(() => {
              const s = document.querySelector('#fbScope');
              if(!s) return null;
              return JSON.stringify([...s.options].map(o=>o.value));
            })()""")
            sc = json.loads(scopes or "[]")
            check("空间下拉是「全部 + 共享 + 本人的个人空间」",
                  bool(sc) and sc[0] == "全部" and "shared" in sc
                  and any(str(v).startswith("user:") for v in sc), str(sc))
            check("默认选中「全部」",
                  await cdp.evaluate("document.querySelector('#fbScope').value") == "全部",
                  await cdp.evaluate("document.querySelector('#fbScope').value"))

            # ---- 表格内容
            table = await cdp.evaluate("""(() => {
              const trs = [...document.querySelectorAll('#fbTable')].length
                ? [...document.querySelectorAll('#fbTable tbody tr')]
                : [...document.querySelectorAll('#dlgFiles tbody tr')];
              return JSON.stringify(trs.map(tr=>[...tr.cells].map(td=>td.textContent.trim())));
            })()""")
            rows = json.loads(table or "[]")
            check("列表里能看到上传的文件（3 个）", len(rows) == 3, f"{len(rows)} 行 {table[:200]}")
            flat = " ".join(" ".join(r) for r in rows)
            check("「文件」列显示的是上传的原文件名",
                  "probe.geojson" in flat and "lonely.shp" in flat, flat[:200])
            check("中文文件名也在", "寿阳测试.geojson" in flat, flat[:200])
            check("「状态」列标出未识别",
                  any("未识别" in r for r in rows), flat[:200])
            check("已识别的也标出来了",
                  any("已识别" in r for r in rows), flat[:200])
            check("每个文件名都是可点的下载链接",
                  await cdp.evaluate("document.querySelectorAll('#dlgFiles a[data-dl]').length") >= 3,
                  await cdp.evaluate("document.querySelectorAll('#dlgFiles a[data-dl]').length"))
            check("每行都有「加到地图」按钮",
                  await cdp.evaluate("document.querySelectorAll('#dlgFiles button[data-add]').length") >= 3,
                  await cdp.evaluate("document.querySelectorAll('#dlgFiles button[data-add]').length"))
            check("每行都有删除按钮",
                  await cdp.evaluate("document.querySelectorAll('#dlgFiles button[data-del]').length") >= 3,
                  await cdp.evaluate("document.querySelectorAll('#dlgFiles button[data-del]').length"))
            check("没被识别的那个「加到地图」是禁用的",
                  await cdp.evaluate("""(() => {
                    const b=[...document.querySelectorAll('#dlgFiles button[data-add]')]
                      .find(x=>x.disabled);
                    return !!b;
                  })()"""))

            # ---- 切到「共享空间」应该为空（刚才只传到个人空间）
            await cdp.evaluate("""(() => {
              const s = document.querySelector('#fbScope');
              s.value = 'shared';
              s.dispatchEvent(new Event('change', {bubbles:true}));
            })()""")
            await asyncio.sleep(0.6)
            shared_rows = await cdp.evaluate(
                "document.querySelectorAll('#dlgFiles tbody tr').length")
            check("切到「共享空间」后列表为空（文件传到个人空间去了）",
                  shared_rows == 0, f"{shared_rows} 行")

            # ---- 切回「全部」
            await cdp.evaluate("""(() => {
              const s = document.querySelector('#fbScope');
              s.value = '全部';
              s.dispatchEvent(new Event('change', {bubbles:true}));
            })()""")
            await asyncio.sleep(0.6)
            back_rows = await cdp.evaluate(
                "document.querySelectorAll('#dlgFiles tbody tr').length")
            check("切回「全部」又都回来了", back_rows == 3, f"{back_rows} 行")

            # ---- 关闭
            await cdp.click("#fbClose2")
            await asyncio.sleep(0.4)
            check("能关掉对话框",
                  not await cdp.evaluate("!!document.querySelector('#dlgFiles.show')"))

            # ---- 页面没有报错
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
    tmp = Path(tempfile.mkdtemp(prefix="gisds_wbfiles_"))
    with TestServer(port=_free_port(8877), require_login=True, data_root=tmp,
                    extra_env={"GISDS_ADMIN_PASSWORD": PASSWORD}) as server:
        token = api_json(server.base, "", "POST", "/api/auth/login",
                         json={"username": USERNAME, "password": PASSWORD})["token"]
        seed_uploads(server.base, token)
        code = asyncio.run(run(server.base))
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    if _FAILED:
        print("  失败项见上")
    return code or (1 if _FAILED else 0)


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
    import os as _os

    _os._exit(code)
