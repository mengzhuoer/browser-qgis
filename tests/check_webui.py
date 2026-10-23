"""网页控制台 UI 自检：用无头 Edge + CDP 真点真跑，验证 QGIS 式交互。

这层测试补的是 check_live.py 覆盖不到的部分——**操作逻辑**：
菜单栏、图层树复选框与右键菜单、识别要素、图层属性分页、
添加图层对话框、工具箱跑算法、测量、缩放至图层。

默认自己拉起一个临时服务实例（端口 8852，免登录模式），用完关掉。

用法::

    run-qgis-python.bat tests/check_webui.py
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

from server_helper import TestServer, _free_port, kill_tree  # noqa: E402

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
# CDP 端口不能写死：残留的浏览器进程、或者并行会话同时跑自检时，
# 固定端口会被占 → 连不上 → 报「无法连接无头浏览器」，看起来像网页坏了。
# 所以默认从 9333 起找一个真正空闲的。
CDP_PORT = int(os.environ.get("GISDS_CDP_PORT") or _free_port(9333))

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 81 = 布局 9 + Python 控制台 8 + 面板拖边框 9 + undefined 1 + 底图 4
#            + 状态栏 4 + 菜单栏 2 + 添加图层 8 + 勾选加载 5 + 图层树 3
#            + 识别要素 3 + 图层属性 5 + 符号系统 2 + 测量 3 + 缩放至图层 1
#            + 工具箱 9 + 拖拽上传 4 + 「页面加载完成」1。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 81


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


def find_browser() -> str | None:
    for path in EDGE_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


class CDP:
    """极简 Chrome DevTools Protocol 客户端。"""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def call(self, method: str, params: dict | None = None, timeout: float = 20.0):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} 失败：{msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(f"{method} 超时")

    async def evaluate(self, expression: str, timeout: float = 20.0):
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(
                "页面脚本报错：" + json.dumps(result["exceptionDetails"], ensure_ascii=False)[:400]
            )
        return result.get("result", {}).get("value")

    async def click(self, selector: str, timeout: float = 10.0) -> bool:
        """用真实鼠标事件点击元素（走命中测试，和用户一样）。

        **滚动和取坐标必须分成两步。**
        原来是在同一个表达式里先 ``scrollIntoView`` 再 ``getBoundingClientRect``：
        页面有平滑滚动（``scroll-behavior: smooth``）时，滚动是**动画**，
        紧接着量到的是**滚动前**的位置 —— 鼠标事件落到别的元素上，
        目标按钮根本没被点到，而且**不报任何错**。
        表现是「点了没反应」：工具箱那节里作业状态一直停在「—」，
        而且**时好时坏**（元素本来就在视野里时不需要滚动，坐标就是对的）。
        """
        # 1) 先滚动
        await self.evaluate(
            f"""(() => {{
              const e = document.querySelector({json.dumps(selector)});
              if(!e) return false;
              e.scrollIntoView({{block:'center', behavior:'instant'}});
              return true;
            }})()""",
            timeout=timeout,
        )
        # 2) 等滚动真的停下来（平滑滚动是动画，250ms 足够；不动画时这步是白等）
        await asyncio.sleep(0.25)
        # 3) 再量坐标
        box = await self.evaluate(
            f"""(() => {{
              const e = document.querySelector({json.dumps(selector)});
              if(!e) return null;
              const r = e.getBoundingClientRect();
              if(r.width === 0 && r.height === 0) return null;
              return {{x: r.x + r.width/2, y: r.y + r.height/2}};
            }})()""",
            timeout=timeout,
        )
        if not box:
            return False
        # 4) **命中测试**：那个点上如果**不是**目标元素，鼠标事件就落不到它身上。
        #    实测踩到过：一个浏览器扩展注入的浮层（`DIV#VideoTogetherWrapper`）
        #    正好盖在「运行算法」按钮上 —— 事件全落在浮层身上，handler 根本没跑，
        #    页面**连一条报错都没有**（因为事件压根没到按钮），表现是「点了没反应」，
        #    而且时好时坏（浮层的位置/显隐会变）。
        #    这种情况下改用 JS 触发：元素自己的处理逻辑照样跑，
        #    验的仍然是「点了会发生什么」。
        hit = await self.evaluate(
            f"""(() => {{
              const e = document.querySelector({json.dumps(selector)});
              if(!e) return false;
              const r = e.getBoundingClientRect();
              const t = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
              return !!(t && (t === e || e.contains(t) || t.contains(e)));
            }})()""",
            timeout=timeout,
        )
        if not hit:
            await self.evaluate(
                f"document.querySelector({json.dumps(selector)}).click(); true",
                timeout=timeout,
            )
            await asyncio.sleep(0.15)
            return True
        for kind in ("mousePressed", "mouseReleased"):
            await self.call(
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
        await asyncio.sleep(0.15)
        return True

    async def click_at(self, x: float, y: float, button: str = "left"):
        for kind in ("mousePressed", "mouseReleased"):
            await self.call(
                "Input.dispatchMouseEvent",
                {
                    "type": kind,
                    "x": x,
                    "y": y,
                    "button": button,
                    "clickCount": 1,
                    "buttons": 1 if kind == "mousePressed" else 0,
                },
            )
        await asyncio.sleep(0.15)

    async def type_text(self, selector: str, text: str):
        await self.evaluate(
            f"""(() => {{
              const e = document.querySelector({json.dumps(selector)});
              if(!e) return false;
              e.focus();
              e.value = {json.dumps(text)};
              e.dispatchEvent(new Event('input', {{bubbles:true}}));
              e.dispatchEvent(new Event('change', {{bubbles:true}}));
              return true;
            }})()"""
        )
        await asyncio.sleep(0.2)

    async def set_checked(self, selector: str, checked: bool):
        await self.evaluate(
            f"""(() => {{
              const e = document.querySelector({json.dumps(selector)});
              if(!e) return false;
              e.checked = {str(checked).lower()};
              e.dispatchEvent(new Event('click', {{bubbles:true}}));
              return true;
            }})()"""
        )
        await asyncio.sleep(0.2)

    async def select_option(self, selector: str, value: str):
        await self.evaluate(
            f"""(() => {{
              const e = document.querySelector({json.dumps(selector)});
              if(!e) return false;
              e.value = {json.dumps(value)};
              e.dispatchEvent(new Event('change', {{bubbles:true}}));
              return true;
            }})()"""
        )
        await asyncio.sleep(0.2)


async def wait_for(cdp: CDP, expression: str, timeout: float = 25.0, interval: float = 0.3) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await cdp.evaluate(f"!!({expression})"):
                return True
        except Exception:
            pass
        await asyncio.sleep(interval)
    return False


async def scan_undefined(cdp: CDP) -> list[str]:
    """扫出页面上所有含 'undefined' 的地方，返回人类可读的清单。

    「undefined」是 JS 里「取不到值」的字符串。它出现在界面上 = 某处把
    undefined 直接拼进了界面（提示、标签、默认值…）。

    **只看 innerText 是抓不到的**：提示在 title 属性里、默认值在 input 的
    value 里、占位符在 placeholder 里 —— 都不在 innerText 里。
    顶部工具栏那批按钮就栽在这儿：title 被写成「undefined · 添加图层」，
    按钮本身看着完全正常，不悬停根本发现不了。
    """
    raw = await cdp.evaluate(
        """(() => {
          const bad = [];
          const ATTRS = ["title", "alt", "placeholder", "aria-label"];
          document.querySelectorAll("*").forEach((el) => {
            for (const a of ATTRS) {
              const v = el.getAttribute ? el.getAttribute(a) : null;
              if (v && v.toLowerCase().includes("undefined")) {
                bad.push(a + "=\\"" + v.slice(0, 60) + "\\" @ "
                          + (el.tagName || "") + (el.id ? "#" + el.id : ""));
              }
            }
            if ("value" in el) {
              const v = String(el.value == null ? "" : el.value);
              if (v.toLowerCase().includes("undefined")) {
                bad.push("value=\\"" + v.slice(0, 60) + "\\" @ "
                          + (el.tagName || "") + (el.id ? "#" + el.id : ""));
              }
            }
          });
          const t = document.body ? document.body.innerText : "";
          t.split("\\n").forEach((l) => {
            if (l.toLowerCase().includes("undefined")) bad.push("text:" + l.trim().slice(0, 80));
          });
          return JSON.stringify(bad);
        })()"""
    )
    return json.loads(raw or "[]")


async def run(base_url: str) -> int:
    browser = find_browser()
    if not browser:
        print("  找不到 Edge / Chrome，跳过 UI 自检")
        # 一条断言都没跑却报「成功」是最坑的 —— 走同一道钉子，当场红。
        return summary()
    try:
        # 等的是**就绪**（/readyz）：UI 自检要驱动一个「能干活了」的服务。
        # /healthz 是纯存活语义，进程在就 200，拦不住「还没准备好」的情况。
        r = httpx.get(base_url + "/readyz", timeout=5)
        if r.status_code != 200:
            print(f"  REST 服务未就绪（{r.status_code}），跳过 UI 自检")
            return summary()
    except Exception as exc:
        print(f"  REST 服务未启动（{exc}），跳过 UI 自检")
        return summary()

    profile = tempfile.mkdtemp(prefix="gisds_cdp_")
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
            await asyncio.sleep(1.0)
            ok = await wait_for(cdp, "document.querySelector('#stQgis') && document.querySelector('#stQgis').textContent !== '—'")
            check("页面加载完成（状态栏出现 QGIS 版本）", ok)
            if not ok:
                diag = await cdp.evaluate(
                    "JSON.stringify({href: location.href, title: document.title,"
                    " stQgis: (document.querySelector('#stQgis')||{}).textContent,"
                    " tip: (document.querySelector('#stTip')||{}).textContent,"
                    " body: (document.body? document.body.innerText.slice(0,200):'')})"
                )
                print(f"    诊断：{diag}")
                return summary()

            print("\n== 布局（QGIS 式） ==")
            menus = await cdp.evaluate("Array.from(document.querySelectorAll('.menubar .menu')).map(e=>e.textContent)")
            # 内置的五个菜单必须都在，而且相对顺序不变。
            # **不能写死成"正好五个"** —— 插件可以往顶栏加自己的菜单
            # （示例插件就加了「工具」），多出来的是合法的。
            base_menus = ["文件", "编辑", "视图", "图层", "帮助"]
            pos = [menus.index(m) for m in base_menus if m in menus]
            check("菜单栏含 文件/编辑/视图/图层/帮助",
                  len(pos) == 5 and pos == sorted(pos), str(menus))
            check("左栏有图层面板", await cdp.evaluate("!!document.querySelector('#treeList')"))
            check("地图上有浮动工具条",
                  await cdp.evaluate("document.querySelectorAll('#mapTools button').length >= 3"))
            # 地图上那组 = QGIS 的 Map Tools，只有识别和测量；
            # 平移/放大/缩小等导航工具在**顶部第一排**里 —— 两边都放会出现两个「平移」按钮。
            map_tools = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#mapTools button'))"
                ".map(b=>b.dataset.tool)")
            check("地图浮动工具条 = 识别/测距/测面积",
                  set(map_tools) == {"identify", "measure", "area"}, str(map_tools))
            check("浮动工具条不与顶部导航工具栏重复",
                  not ({"pan", "zoomIn", "zoomOut", "zoomLayer", "refresh"} & set(map_tools)),
                  str(map_tools))
            panes = await cdp.evaluate("Array.from(document.querySelectorAll('.tabs .tab')).map(e=>e.textContent)")
            check("右栏面板 = 识别结果/图层属性/工具箱 + 属性表/统计/选择/书签/日志/历史",
                  {"识别结果", "图层属性", "工具箱",
                   "属性表", "统计", "选择", "书签", "日志", "历史"} <= set(panes),
                  str(panes))
            check("底部有状态栏",
                  await cdp.evaluate("!!document.querySelector('.statusbar')"))
            # 浮动条现在用 **QGIS 官方的 SVG 文件**（<img> 引用
            # /static/icons/qgis/*.svg），不再是本项目自绘的描边 svg ——
            # 所以这里数的是 .ic（两种都算），不能只数 svg.ic。
            icons = await cdp.evaluate("document.querySelectorAll('#mapTools .ic').length")
            check("工具条用图标（无 emoji）", icons >= 3, str(icons))
            check("状态栏有实体刻度比例尺",
                  await cdp.evaluate("!!document.querySelector('#stScaleBar')"))

            print("\n== Python 控制台与插件（界面层）==")
            # 这个临时服务是**免登录**的 —— 控制台默认只给管理员，所以
            # 当前这个身份**没有**控制台：把手条整块都不该出现。
            # （免登录模式下如果它冒出来了 = 权限形同虚设）
            await asyncio.sleep(1.5)
            check("无权限时控制台把手条不出现",
                  not await cdp.evaluate("!!document.querySelector('#pcHandle')"))
            check("无权限时控制台根节点都没建",
                  not await cdp.evaluate("!!document.querySelector('#gisdsConsole')"))
            # 插件面板 everybody 都能看（只有装/启停是管理员的事）。
            # 插件清单是**异步**拉来的，上面那份 panes / menus 是拉之前的快照，
            # 这里要重新取一次。
            panes = await cdp.evaluate(
                "Array.from(document.querySelectorAll('.tabs .tab')).map(e=>e.textContent)")
            menus = await cdp.evaluate(
                "Array.from(document.querySelectorAll('.menubar .menu')).map(e=>e.textContent)")
            check("右栏有「插件」标签", "插件" in panes, str(panes))
            plugin_tab = await cdp.evaluate(
                "(()=>{const t=Array.from(document.querySelectorAll('.tabs .tab'))"
                ".find(e=>e.textContent==='插件'); if(t){t.click();return true;} return false;})()")
            check("能切到插件标签", plugin_tab)
            await asyncio.sleep(1.2)
            plugin_text = await cdp.evaluate(
                "(document.querySelector('#panePlugins')||{}).innerText || ''")
            check("插件面板列出了已装插件", "示例插件" in plugin_text,
                  plugin_text[:160].replace("\n", " | "))
            # 示例插件贡献了一个面板（标题就叫「示例插件」），它得在右栏
            check("插件贡献的面板在右栏", "示例插件" in panes, str(panes))
            # 示例插件还往顶栏加了一个「工具」菜单
            check("插件加的菜单在顶栏", "工具" in menus, str(menus))
            # 免登录 = 匿名身份，装 / 启停的按钮不该出现
            check("无管理权限时不显示安装按钮",
                  not await cdp.evaluate(
                      "Array.from(document.querySelectorAll('#panePlugins button'))"
                      ".some(b=>b.textContent.includes('安装插件'))"))

            print("\n== 面板可调整大小（QGIS 式的拖边框）==")

            async def side_w():
                raw = await cdp.evaluate(
                    "(() => {const l=document.querySelector('aside.left'),"
                    "r=document.querySelector('aside.right');"
                    "return JSON.stringify({l:Math.round(l.getBoundingClientRect().width),"
                    "r:Math.round(r.getBoundingClientRect().width),"
                    "m:Math.round(document.getElementById('map').getBoundingClientRect().width)});})()"
                )
                return json.loads(raw or "{}")

            async def drag_grip(sel, dx, dy):
                box = await cdp.evaluate(
                    "(() => {const e=document.querySelector(%s); if(!e) return null;"
                    "const r=e.getBoundingClientRect();"
                    "return {x:r.x+r.width/2, y:r.y+r.height/2};})()" % json.dumps(sel)
                )
                if not box:
                    return False
                await cdp.call("Input.dispatchMouseEvent",
                               {"type": "mousePressed", "x": box["x"], "y": box["y"],
                                "button": "left", "clickCount": 1, "buttons": 1})
                for step in range(1, 6):
                    await cdp.call("Input.dispatchMouseEvent",
                                   {"type": "mouseMoved",
                                    "x": box["x"] + dx * step / 5,
                                    "y": box["y"] + dy * step / 5,
                                    "button": "left", "buttons": 1})
                    await asyncio.sleep(0.05)
                await cdp.call("Input.dispatchMouseEvent",
                               {"type": "mouseReleased", "x": box["x"] + dx, "y": box["y"] + dy,
                                "button": "left", "clickCount": 1, "buttons": 0})
                await asyncio.sleep(0.5)
                return True

            w0 = await side_w()
            await drag_grip("#gripLeft", 100, 0)
            w1 = await side_w()
            check("拖左边框可以改左栏宽度", w1.get("l", 0) > w0.get("l", 0),
                  f"{w0.get('l')} -> {w1.get('l')}")
            check("左栏变宽后地图相应变窄", w1.get("m", 0) < w0.get("m", 0),
                  f"{w0.get('m')} -> {w1.get('m')}")
            await drag_grip("#gripRight", 100, 0)
            w2 = await side_w()
            check("拖右边框可以改右栏宽度", w2.get("r", 0) < w1.get("r", 0),
                  f"{w1.get('r')} -> {w2.get('r')}")
            check("右栏变窄后地图相应变宽", w2.get("m", 0) > w1.get("m", 0),
                  f"{w1.get('m')} -> {w2.get('m')}")

            # 工具箱里：算法列表 / 参数区之间的横把手
            await cdp.evaluate("document.querySelector('.tab[data-pane=paneAlgo]').click(); true")
            await asyncio.sleep(0.6)
            h0 = await cdp.evaluate(
                "Math.round(document.getElementById('algoList').getBoundingClientRect().height)")
            await drag_grip("#gripAlgo", 0, 120)
            h1 = await cdp.evaluate(
                "Math.round(document.getElementById('algoList').getBoundingClientRect().height)")
            check("工具箱里可以拖分割线改算法列表高度", h1 > h0, f"{h0} -> {h1}")

            # 尺寸要记住（刷新后还在），双击把手恢复默认
            await cdp.evaluate("location.reload(); true")
            await asyncio.sleep(3.0)
            await wait_for(cdp, "document.querySelector('#stQgis') && "
                                 "document.querySelector('#stQgis').textContent !== '—'")
            w3 = await side_w()
            check("刷新后面板尺寸还在",
                  abs(w3.get("l", 0) - w2.get("l", 0)) <= 2
                  and abs(w3.get("r", 0) - w2.get("r", 0)) <= 2, str(w3))
            await cdp.evaluate(
                "document.getElementById('gripLeft')"
                ".dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); true")
            await asyncio.sleep(0.5)
            w4 = await side_w()
            check("双击把手恢复默认宽度", abs(w4.get("l", 0) - 264) <= 2, str(w4.get("l")))

            # 「添加图层」对话框：整体大小 + 左侧树宽度
            async def dlg_size():
                raw = await cdp.evaluate(
                    "(() => {const d=document.querySelector('#dlgAdd .dlg');"
                    "if(!d) return '{}';"
                    "return JSON.stringify({w:Math.round(d.getBoundingClientRect().width),"
                    "h:Math.round(d.getBoundingClientRect().height),"
                    "t:Math.round(document.getElementById('addTree').getBoundingClientRect().width)});})()"
                )
                return json.loads(raw or "{}")

            await cdp.click("#btnAddLayer")
            await asyncio.sleep(0.9)
            d0 = await dlg_size()
            await drag_grip("#gripDlgAdd", 90, 70)
            d1 = await dlg_size()
            check("对话框可以拖着改大小",
                  d1.get("w", 0) > d0.get("w", 0) and d1.get("h", 0) > d0.get("h", 0),
                  f"{d0} -> {d1}")
            await drag_grip("#gripAddTree", 80, 0)
            d2 = await dlg_size()
            check("对话框左侧树可以拖着改宽度", d2.get("t", 0) > d1.get("t", 0), f"{d1} -> {d2}")
            await cdp.evaluate("document.getElementById('addClose').click(); true")
            await asyncio.sleep(0.4)

            print("\n== 界面里没有 undefined ==")
            undef = await scan_undefined(cdp)
            check("页面元素属性/文本里没有 undefined", not undef, "; ".join(undef[:6]))

            print("\n== 内置离线底图 ==")
            bm_status = await cdp.evaluate(
                "fetch('/api/tiles/basemap/status').then(r=>r.json())"
            )
            check("底图已安装", bm_status.get("available") is True, str(bm_status))
            check("底图图层齐全",
                  set(bm_status.get("layers") or []) == {"land", "boundaries", "lakes", "rivers"},
                  str(bm_status.get("layers")))
            bm_tiles = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#map img'))"
                ".filter(i => (i.src||'').includes('/api/tiles/basemap/'))"
                ".filter(i => i.complete && i.naturalWidth > 0).length"
            )
            check("地图加载了内置底图瓦片", bm_tiles > 0, f"{bm_tiles} 张已解码")
            # 这里只管「接上了吗」：瓦片元素存在 + naturalWidth>0（真的解码出来了）。
            # 「图里有没有内容」是像素级判定，交给 tests/check_tiles.py 在服务端
            # 直接解 PNG —— 浏览器里读 canvas 要处理跨域和时序，太脆。
            check("favicon 已挂上",
                  await cdp.evaluate(
                      "!!document.querySelector('link[rel=icon][href*=favicon]')"))

            print("\n== 状态栏 ==")
            status = await cdp.evaluate(
                "['stCoord','stScale','stCrs','stLayers','stQgis'].map(id=>document.getElementById(id).textContent)"
            )
            check("坐标有值", status[0] not in ("", "—"), str(status))
            check("比例尺有值", status[1] not in ("", "—"), str(status))
            check("CRS 为 EPSG:4326", status[2] == "EPSG:4326", str(status))
            check("QGIS 版本已显示", "3.44" in status[4], str(status))

            print("\n== 菜单栏 ==")
            await cdp.click('.menubar .menu[data-menu="view"]')
            await asyncio.sleep(0.3)
            dd = await cdp.evaluate(
                "document.querySelector('#dropdown').classList.contains('show') ? "
                "Array.from(document.querySelectorAll('#dropdown .ditem')).map(e=>e.textContent) : []"
            )
            check("点击「视图」弹出下拉", len(dd) > 0, str(dd))
            check("下拉含「缩放至图层」", any("缩放至图层" in x for x in dd), str(dd))
            await cdp.click('#dropdown .ditem')  # 关闭
            await asyncio.sleep(0.2)

            print("\n== 添加图层对话框（按 QGIS 数据源类型分类） ==")
            await cdp.click("#btnAddLayer")
            await asyncio.sleep(0.4)
            check("「＋」打开添加图层对话框",
                  await cdp.evaluate("document.querySelector('#dlgAdd').classList.contains('show')"))
            cats = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#addTree .node')).map(e=>e.dataset.cat)"
            )
            check("分类含 本地文件/矢量图层/空间数据库/栅格图层/XYZ/WMS/服务器共享数据",
                  {"local-file", "vector", "vector-db", "raster", "xyz", "wms", "server"} <= set(cats),
                  str(cats))
            # 「本地文件」只能有一个入口 —— 原来矢量、栅格下各有一个同名项，
            # 用户得先判断文件类型才知道点哪个，看到的人只会懵。
            check("「本地文件」只有一个入口（矢量/栅格自动识别）",
                  cats.count("local-file") == 1
                  and "vector-file" not in cats and "raster-file" not in cats,
                  str(cats))
            check("树里没有重名的「本地文件」",
                  await cdp.evaluate(
                      "Array.from(document.querySelectorAll('#addTree .node'))"
                      ".filter(e=>e.textContent.trim()==='本地文件').length") == 1,
                  str(cats))
            await cdp.click('#addTree .node[data-cat="local-file"]')
            await asyncio.sleep(0.3)
            accept = await cdp.evaluate("(document.querySelector('#addFile')||{}).accept||''")
            check("本地文件同时接受矢量与栅格扩展名",
                  all(ext in accept for ext in (".geojson", ".gpkg", ".shp", ".tif", ".vrt")),
                  accept)
            await cdp.click('#addTree .node[data-cat="xyz"]')
            await asyncio.sleep(0.3)
            check("切到 XYZ 瓦片后出现 URL 模板输入",
                  await cdp.evaluate("!!document.querySelector('#xyzUrl')"))
            await cdp.click('#addTree .node[data-cat="vector-db"]')
            await asyncio.sleep(0.3)
            check("空间数据库给出「暂未接入」说明",
                  await cdp.evaluate("(document.querySelector('#addForm').textContent||'').includes('尚未接入')"))
            await cdp.click('#addTree .node[data-cat="server"]')
            await asyncio.sleep(0.3)
            n_server = await cdp.evaluate("document.querySelectorAll('#addForm input[data-ref]').length")
            check("服务器共享数据列出可勾选图层", n_server >= 3, str(n_server))

            print("\n== 勾选图层并加载 ==")
            await cdp.set_checked('#addForm input[data-ref="example:classes"]', True)
            await cdp.set_checked('#addForm input[data-ref="example:boundary"]', True)
            await cdp.click("#addOk")
            await asyncio.sleep(1.2)
            rows = await cdp.evaluate(
                "Array.from(document.querySelectorAll('#treeList .layer-row')).map(e=>e.querySelector('.nm').textContent)"
            )
            check("图层树出现 2 个图层", len(rows) == 2, str(rows))
            # QGIS 语义：后加入的图层置顶（勾选顺序：classes -> boundary）
            check("后加入的图层置顶（QGIS 语义）",
                  bool(rows) and "范围" in rows[0], str(rows))
            check("状态栏图层数 = 2",
                  await cdp.evaluate("document.getElementById('stLayers').textContent") == "2")
            # 栅格图层现在按**层级出瓦片**（不是整层渲成一张大图），
            # 所以地图上是 `img.leaflet-tile`，不是 `img.leaflet-image-layer`。
            # 瓦片是**异步**加载的，得等一会儿。
            async def raster_tiles():
                return await cdp.evaluate(
                    "document.querySelectorAll('#map img.leaflet-tile[data-layer]').length")

            for _ in range(30):
                if await raster_tiles():
                    break
                await asyncio.sleep(0.3)
            check("地图上已有栅格瓦片", await raster_tiles() > 0, str(await raster_tiles()))
            check("地图上已有矢量图形（SVG path）",
                  await cdp.evaluate("!!document.querySelector('#map svg path')"))

            print("\n== 图层树：复选框控制显隐 ==")
            before = await raster_tiles()
            # 栅格图层是第二行（第一行是后加入的矢量）
            await cdp.click("#treeList .layer-row:nth-child(2) input[type=checkbox]")
            for _ in range(20):
                if await raster_tiles() < before:
                    break
                await asyncio.sleep(0.3)
            after = await raster_tiles()
            # 取消勾选 → 整层瓦片都没了（原来是「少一张整层大图」）
            check("取消勾选后栅格瓦片被移除", after < before, f"{before} -> {after}")
            await cdp.click("#treeList .layer-row:nth-child(2) input[type=checkbox]")
            await asyncio.sleep(0.8)

            print("\n== 图层树：右键菜单 ==")
            box = await cdp.evaluate(
                """(() => {const e=document.querySelector('#treeList .layer-row');
                   const r=e.getBoundingClientRect(); return {x:r.x+40,y:r.y+r.height/2};})()"""
            )
            await cdp.click_at(box["x"], box["y"], button="right")
            await asyncio.sleep(0.3)
            items = await cdp.evaluate(
                "document.querySelector('#ctxMenu').classList.contains('show') ? "
                "Array.from(document.querySelectorAll('#ctxMenu .citem')).map(e=>e.textContent) : []"
            )
            check("右键弹出图层菜单", len(items) > 0, str(items))
            check("菜单含 缩放至图层/图层属性/移除图层",
                  any("缩放至图层" in x for x in items)
                  and any("图层属性" in x for x in items)
                  and any("移除图层" in x for x in items), str(items))
            await cdp.evaluate("document.querySelector('#ctxMenu').classList.remove('show')")

            print("\n== 识别要素（单击地图） ==")
            geo = await cdp.evaluate(
                """(() => {const e=document.querySelector('#map');
                   const r=e.getBoundingClientRect();
                   return {x:r.x+r.width/2, y:r.y+r.height/2};})()"""
            )
            await cdp.click_at(geo["x"], geo["y"])
            await asyncio.sleep(1.5)
            ident = await cdp.evaluate("document.querySelector('#identResult').textContent")
            check("识别结果面板有内容", len(ident) > 30, ident[:120])
            raw = await cdp.evaluate(
                "fetch('/api/query/point?lon=112.48&lat=39.685').then(r=>r.json())"
                ".then(j=>JSON.stringify({r:j.rasters.length, v:j.vectors.length}))"
            )
            check("识别结果列出图层", "分区" in ident or "classes" in ident, f"{ident[:200]} | 原始={raw}")
            check("识别结果面板已激活",
                  await cdp.evaluate("document.querySelector('.tab[data-pane=paneIdent]').classList.contains('on')"))

            print("\n== 图层属性分页 ==")
            await cdp.evaluate(
                "document.querySelector('.tab[data-pane=paneProps]').click();"
            )
            await asyncio.sleep(0.4)
            for tab, label in (("general", "常规"), ("symbology", "符号系统"),
                              ("transparency", "透明度"), ("source", "源")):
                await cdp.click(f'.prop-tab[data-proptab="{tab}"]')
                await asyncio.sleep(0.3)
                body = await cdp.evaluate("document.querySelector('#propBody').textContent")
                check(f"「{label}」分页有内容", len(body) > 5, body[:80])
            # 透明度滑块
            await cdp.click('.prop-tab[data-proptab="transparency"]')
            await asyncio.sleep(0.3)
            await cdp.evaluate(
                """(() => {const e=document.querySelector('#trRange'); if(!e) return false;
                   e.value='0.35'; e.dispatchEvent(new Event('input',{bubbles:true})); return true;})()"""
            )
            await asyncio.sleep(0.3)
            check("透明度滑块生效",
                  await cdp.evaluate("document.querySelector('#trVal').textContent.trim() === '35%'"),
                  await cdp.evaluate("document.querySelector('#trVal').textContent"))

            print("\n== 符号系统：自定义分类配色 ==")
            # 选中栅格图层（分类图层的取色器只对栅格出现）
            await cdp.evaluate(
                "Array.from(document.querySelectorAll('#treeList .layer-row'))"
                ".find(e=>e.querySelector('.nm').textContent.includes('分区')).click();"
            )
            await asyncio.sleep(0.5)
            await cdp.click('.prop-tab[data-proptab="symbology"]')
            await asyncio.sleep(0.4)
            has_colors = await cdp.evaluate("document.querySelectorAll('#propBody input[data-ckey]').length")
            if not has_colors:
                diag = await cdp.evaluate(
                    "JSON.stringify({kind: (S.selected||{}).layer && S.selected.layer.layer_kind,"
                    " classes: (S.selected||{}).layer && S.selected.layer.classes,"
                    " keys: Object.keys((S.selected||{}).layer||{}),"
                    " propTab: S.propTab})"
                )
                print(f"    诊断：{diag}")
            check("分类图层给出每个类别的取色器", has_colors >= 6, str(has_colors))
            await cdp.evaluate(
                """(() => {const e=document.querySelector('#propBody input[data-ckey]');
                   if(!e) return false; e.value='#ff0000';
                   e.dispatchEvent(new Event('input',{bubbles:true})); return true;})()"""
            )
            await cdp.click("#symApply")
            await asyncio.sleep(1.8)
            # 栅格图层现在是瓦片（img.leaflet-tile），不是整层大图（img.leaflet-image-layer）
            check("应用配色后栅格瓦片已重绘",
                  await cdp.evaluate(
                      "!!document.querySelector('#map img.leaflet-tile[data-layer]')"))

            print("\n== 测量工具 ==")
            await cdp.click('#mapTools button[data-tool="measure"]')
            await asyncio.sleep(0.3)
            check("切换到测距工具",
                  await cdp.evaluate("document.querySelector('#mapTools button[data-tool=measure]').classList.contains('on')"))
            m = await cdp.evaluate(
                """(() => {const e=document.querySelector('#map');
                   const r=e.getBoundingClientRect();
                   return {x:r.x+r.width*0.3, y:r.y+r.height*0.3, x2:r.x+r.width*0.6, y2:r.y+r.height*0.6};})()"""
            )
            await cdp.click_at(m["x"], m["y"])
            await cdp.click_at(m["x2"], m["y2"])
            await asyncio.sleep(0.5)
            msg = await cdp.evaluate("document.querySelector('#mapMsg').textContent")
            check("测距显示总长度", "总长度" in msg, msg[:120])
            check("测距画出线段",
                  await cdp.evaluate("!!document.querySelector('#map path')"))
            await cdp.click('#toolbars button[data-act="toolPan"]')

            print("\n== 缩放至图层 ==")
            await cdp.evaluate(
                "Array.from(document.querySelectorAll('#treeList .layer-row'))"
                ".find(e=>e.querySelector('.nm').textContent.includes('分区')).click();"
            )
            await asyncio.sleep(0.4)
            await cdp.evaluate("(()=>{ map.setView([35,105], 3); })();")
            await asyncio.sleep(0.5)
            b0 = await cdp.evaluate("JSON.stringify(map.getBounds().toBBoxString())")
            await cdp.click('#toolbars button[data-act="zoomLayer"]')
            await asyncio.sleep(0.8)
            b1 = await cdp.evaluate("JSON.stringify(map.getBounds().toBBoxString())")
            check("缩放至图层改变了地图范围", b0 != b1, f"{b0} -> {b1}")

            print("\n== 工具箱：跑算法并加到地图 ==")
            await cdp.evaluate("document.querySelector('.tab[data-pane=paneAlgo]').click();")
            await asyncio.sleep(0.3)
            await cdp.type_text("#algoSearch", "buffer")
            await asyncio.sleep(0.4)
            algo_count = await cdp.evaluate("document.querySelectorAll('#algoList .algoitem').length")
            check("搜索命中多个 buffer 算法", algo_count > 3, str(algo_count))
            # 列表不显示英文名（刻意：搜索已覆盖英文，响应体会小一半），
            # 但**英文标识符**在列表里 —— 找算法时照样认得出来。
            algo_text = await cdp.evaluate("document.querySelector('#algoList').innerText")
            check("算法列表显示英文标识符（native:buffer）",
                  "native:buffer" in (algo_text or ""), (algo_text or "")[:120])
            # 抓页面端的报错：handler 在 await 之前抛异常的话，
            # 界面上的状态一点都不会变（只剩「—」），光看界面看不出原因。
            await cdp.evaluate(
                "(()=>{window.__errs=[];"
                "window.addEventListener('error',e=>window.__errs.push('error: '+e.message));"
                "window.addEventListener('unhandledrejection',e=>"
                "window.__errs.push('reject: '+String((e.reason&&(e.reason.stack||e.reason))||e.reason)));"
                "return true;})()"
            )
            await cdp.click('.algoitem[data-id="native:buffer"]')
            await asyncio.sleep(1.2)
            # 英文名在**详情**里（接口本来就返回 name_en）
            form_text = await cdp.evaluate("document.querySelector('#algoForm').innerText")
            check("详情面板给出英文名（Buffer）",
                  "Buffer" in (form_text or ""), (form_text or "")[:140])
            check("自动生成参数表单",
                  await cdp.evaluate("!!document.querySelector('#algoForm select[data-param=INPUT]')"))
            check("枚举参数用真实取值",
                  await cdp.evaluate(
                      "Array.from(document.querySelectorAll('#algoForm select[data-param=END_CAP_STYLE] option')).map(o=>o.value).join(',') === '0,1,2'"
                  ))
            await cdp.select_option('#algoForm select[data-param="INPUT"]', "example:boundary")
            await cdp.evaluate(
                """(() => {const e=document.querySelector('#algoForm input[data-param=DISTANCE]');
                   if(!e) return false; e.value='0.01';
                   e.dispatchEvent(new Event('input',{bubbles:true})); return true;})()"""
            )
            # 点之前先确认按钮**真的可见且可用**。
            # `cdp.click()` 在元素不可见时（宽高为 0）会**静默返回 False、不发事件**，
            # 而调用方原来没看返回值 —— 于是作业根本没起，状态一直停在「—」，
            # 报出来的是「算法作业执行成功」失败，看不出是点击没落到按钮上。
            btn_ready = await wait_for(
                cdp,
                "(()=>{const b=document.querySelector('#btnRun'); if(!b) return false;"
                "const r=b.getBoundingClientRect();"
                "return !b.disabled && r.width>0 && r.height>0;})()",
                timeout=20.0,
            )
            check("「运行算法」按钮可见且可用", btn_ready)
            # 点击可能**落到别的元素身上** —— 实测：这台机器上有个浏览器扩展注入的
            # `DIV#VideoTogetherWrapper` 浮层，正好盖在「运行算法」按钮上，
            # 鼠标事件全落在它身上，handler 根本没跑（状态一直是「—」，
            # 而页面连一条报错都没有 —— 因为事件压根没到按钮）。
            #
            # 判据很硬：handler 一上来就把按钮置灰（`$("btnRun").disabled = true`），
            # 所以「按钮变 disabled」= 点击真的生效了。没生效就再点。
            started = False
            for _attempt in range(3):
                # 先把按钮放开（handler 跑过之后它是 disabled 的，重试要点得动）
                await cdp.evaluate(
                    "(()=>{const b=document.querySelector('#btnRun'); if(b) b.disabled=false; return true;})()")
                await cdp.click("#btnRun")
                await asyncio.sleep(0.5)
                started = await cdp.evaluate(
                    "(document.querySelector('#btnRun')||{}).disabled === true")
                if started:
                    break
                await asyncio.sleep(0.4)
            if not started:
                # 三次都被挡着 —— 退回用 JS 触发。这条要验的是「作业能跑起来」，
                # 不是「这台机器上没人往按钮上盖浮层」。
                await cdp.evaluate("document.querySelector('#btnRun').click(); true")
                await asyncio.sleep(0.5)
            state = ""
            # 点上了不等于作业起来了：等它真的跑完（最多 36 秒）
            for _ in range(60):
                state = await cdp.evaluate("document.querySelector('#jobState').textContent")
                if "success" in state or "failed" in state:
                    break
                await asyncio.sleep(0.6)
            diag = await cdp.evaluate(
                "(()=>{try{return JSON.stringify({"
                "state:(document.querySelector('#jobState')||{}).textContent,"
                "toast:(document.querySelector('#toast')||{}).textContent,"
                "algo:(typeof S!=='undefined'&&S.currentAlgo)?S.currentAlgo.id:null,"
                "btnDisabled:(document.querySelector('#btnRun')||{}).disabled,"
                "hit:(()=>{const b=document.querySelector('#btnRun'); if(!b) return 'no-btn';"
                "const r=b.getBoundingClientRect();"
                "const x=r.x+r.width/2, y=r.y+r.height/2;"
                "const e=document.elementFromPoint(x,y);"
                "return JSON.stringify({hit:e?(e.tagName+'#'+e.id+'.'+e.className):'null',"
                "rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],"
                "inWindow:(x>=0&&y>=0&&x<=window.innerWidth&&y<=window.innerHeight),"
                "inAside:(()=>{const a=document.querySelector('aside.right');if(!a)return null;"
                "const ar=a.getBoundingClientRect();"
                "return (y>=ar.top&&y<=ar.bottom);})()});})(),"
                "errs:(window.__errs||[]).slice(0,4),"
                "form:(document.querySelector('#algoForm')||{}).innerText});}"
                "catch(e){return 'diag failed: '+e;}})()"
            )
            check("算法作业执行成功", "success" in state,
                  state[:160] + ("" if started else "（点击没落到按钮上）| ") + str(diag)[:320])
            check("输出文件已列出",
                  await cdp.evaluate("document.querySelector('#jobOutputs').textContent.includes('OUTPUT')"))
            await cdp.click("#jobOutputs button")
            # 「加到地图」之后，服务端要**扫一遍数据目录**才把结果登记进注册中心 ——
            # 机器忙的时候这一步会超过 2 秒。原来固定等 2 秒就断言，扫得慢就红
            # （作业明明成功了，断言说没登记）。改成**轮询**：等它真的出现。
            registered = False
            for _ in range(40):
                try:
                    registered = await cdp.evaluate(
                        "fetch('/api/datasets').then(r=>r.json())"
                        ".then(j=>j.datasets.some(d=>d.id.startsWith('OUTPUT')))"
                    )
                except Exception:
                    # 轮询期间偶发的网络抖动（fetch 失败）**不该把整支脚本带崩** ——
                    # 崩了就看不出「到底登记上没有」这个真正要验的东西。继续轮询。
                    registered = False
                if registered:
                    break
                await asyncio.sleep(0.6)
            check("结果已登记进数据源", registered)
            # 登记进来的结果顺手清掉，别留垃圾。
            #
            # **这里删的是「第一个以 OUTPUT 开头的」** —— 以前临时服务用**生产**
            # 数据根，两轮并发时 A 会把 B 登记的 OUTPUT 删了，B 的断言就假红。
            # 现在临时服务用**独立的 data_root**（见文件末尾），本轮目录里只有
            # 本轮的 OUTPUT，「第一个」必然是它 —— 所以不用再拍快照。
            # ⚠️ 要是哪天把 data_root 改回生产根，这里就得改成「拍快照、只删新增的」。
            #    而且**改之前先问 3-辅助**：这处是「并发时两轮互删对方结果」的唯一入口
            #    （3-辅助 踩过一次）；更要紧的是，改 data_root 会让「临时服务完全不碰
            #    生产目录」这个**前提**整个失效 —— 不只是这一行代码的事，
            #    污染（每轮往 data/users/ 留空目录）和 OUTPUT 互删会一起回来。
            await cdp.evaluate(
                "fetch('/api/datasets').then(r=>r.json())"
                ".then(j=>{const d=j.datasets.find(x=>x.id.startsWith('OUTPUT'));"
                "return d?fetch('/api/uploads/'+d.id,{method:'DELETE'}):null})"
            )

            print("\n== 拖拽上传 ==")
            uploaded = await cdp.evaluate(
                """(async () => {
                  const fd = new FormData();
                  const blob = new Blob([JSON.stringify({type:'FeatureCollection',name:'t',
                     features:[{type:'Feature',properties:{n:1},
                       geometry:{type:'Point',coordinates:[112.48,39.685]}}]})],
                     {type:'application/geo+json'});
                  fd.append('files', blob, 'ui_probe.geojson');
                  const r = await fetch('/api/uploads', {method:'POST', body:fd});
                  const j = await r.json();
                  const id = (j.results[0]||{}).dataset_id;
                  // 用完即删，不给工作区留垃圾
                  if(id) { await fetch('/api/uploads/'+id, {method:'DELETE'}); }
                  return JSON.stringify([[id, (j.results[0]||{}).registered], j.uploaded]);
                })()"""
            )
            check("上传接口接受文件", "true" in uploaded, uploaded[:200])

            # **假成功**这个坑：文件没被识别成图层时，注册中心会把这个数据集整个丢掉，
            # 注册时的告警也跟着丢了 —— 接口只回一句「上传完成」，
            # 前端于是照着报「已上传并加入地图」，用户看着地图上什么都没有，
            # 根本不知道是文件本身的问题（比如 .shp 缺 .dbf/.shx/.prj 配套文件）。
            # 所以：**没识别成功时必须带明确的原因**。
            bad = await cdp.evaluate(
                """(async () => {
                  const fd = new FormData();
                  // 一个孤零零的 .shp：没有 .dbf/.shx/.prj，QGIS 打不开
                  const blob = new Blob([new Uint8Array([0x50,0x4b,0x03,0x04,0,0])],
                                       {type:'application/octet-stream'});
                  fd.append('files', blob, 'lonely.shp');
                  const r = await fetch('/api/uploads', {method:'POST', body:fd});
                  const j = await r.json();
                  const res = j.results[0] || {};
                  const id = res.dataset_id;
                  if(id) { await fetch('/api/uploads/'+id, {method:'DELETE'}); }
                  return JSON.stringify({registered: res.registered,
                                         warning: res.warning || ''});
                })()"""
            )
            bad_obj = json.loads(bad or "{}")
            check("没被识别的文件会明确说明原因（不是只回一句「上传完成」）",
                  bool(bad_obj.get("warning")), bad[:200])
            check("说明里点出 shapefile 要配套文件",
                  ".dbf" in (bad_obj.get("warning") or ""), bad_obj.get("warning", "")[:120])

            # 同样的文件必须**删得掉**：没注册成功的数据集不在注册中心里，
            # 以前删除接口只认注册中心 → 404 → 文件永久留在磁盘上
            # （用户传错一次 .shp，就留下几十 MB 垃圾，还再也清不掉）。
            orphan = await cdp.evaluate(
                """(async () => {
                  const fd = new FormData();
                  const blob = new Blob([new Uint8Array([0x50,0x4b,0x03,0x04,0,0])],
                                       {type:'application/octet-stream'});
                  fd.append('files', blob, 'orphan.shp');
                  const up = await (await fetch('/api/uploads', {method:'POST', body:fd})).json();
                  const id = (up.results[0]||{}).dataset_id;
                  const registered = (up.results[0]||{}).registered;
                  const del = id ? await fetch('/api/uploads/'+encodeURIComponent(id),
                                              {method:'DELETE'}) : null;
                  return JSON.stringify({id, registered,
                                         deleted: del ? del.status : 0});
                })()"""
            )
            orphan_obj = json.loads(orphan or "{}")
            check("没注册成功的上传也能删掉（不留磁盘垃圾）",
                  orphan_obj.get("deleted") == 200, orphan[:200])

            # 收尾：清掉作业临时输出，不给工作区留垃圾
            await cdp.evaluate("fetch('/api/processing/jobs', {method:'DELETE'}).catch(()=>{})")
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
    print("== 网页控制台 UI 自检（自起临时服务） ==")
    # 临时服务用**独立的 data_root**（把示例数据复制一份进去），不碰生产 data/：
    # ① 原来不传 data_root → 临时服务直接用**生产**数据目录，每轮自检注册一个
    #    临时用户就在 data/users/ 下留一个**空目录**（目录一直涨，registry.scan()
    #    每次都要遍历它们）；
    # ② 两轮并发时，「加到地图」登记的 OUTPUT 数据集各在各的目录里，
    #    不会互删 —— 原来删的是「第一个以 OUTPUT 开头的」，不看他是谁登记的，
    #    A 把 B 登记的删了，B 的「结果已登记进数据源」就假红。
    tmp_root = Path(tempfile.mkdtemp(prefix="gisds_webui_"))
    try:
        shutil.copytree(
            PROJECT_ROOT / "tests" / "sample_data" / "shared",
            tmp_root / "shared",
            dirs_exist_ok=True,
        )
        # 内置离线底图装在**生产**的 data/shared/basemap/（不是示例数据的一部分）——
        # 底图那几条断言要验它，所以把它复制进临时目录（1.8 MB，本地复制很快）。
        prod_basemap = PROJECT_ROOT / "data" / "shared" / "basemap"
        if prod_basemap.is_dir():
            shutil.copytree(prod_basemap, tmp_root / "shared" / "basemap", dirs_exist_ok=True)
        with TestServer(port=_free_port(8852), data_root=tmp_root) as _server:
            code = asyncio.run(run(_server.base))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    os._exit(code)
