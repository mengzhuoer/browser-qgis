# -*- coding: utf-8 -*-
"""插件系统 / Python 控制台 / MCP 接口 —— 深度自检。

这一支专门补 ``check_plugins.py`` / ``check_console.py`` / ``check_mcp*.py``
**没有覆盖**的那些面：

* **插件**：zip 安装的边界（条目数 / 体积 / 解压后体积 / 嵌套 zip / metadata 合法性）、
  重复安装同名插件、坏插件的错误信息、重名 slug 的路由注销冲突、
  插件贡献的 Processing 算法、插件接口跑在哪条线程上、动作接口的鉴权、
  启停状态重启后还在不在、插件目录和生产目录的隔离。
* **控制台**：护栏能不能被绕过（``os._real`` / ``os.kill`` / ``os.__dict__``）、
  ``from os.path import ...`` 这种常见写法、命名空间 reset 的隔离范围、
  超时之后线程到底占不占着、控制台能不能改服务自己的配置。
* **MCP**：27 个工具逐个调一遍（补上 ``gis_get_dataset`` / ``gis_rescan`` 这两个
  既有自检从没调过的）、MCP 的鉴权与租户隔离、stdio 传输的握手与失败路径。

**断言按「正确行为」写**。这一支最初是按「当前实际行为」写的（凡是标了
「当前行为」的断言，说的都是**已确认的缺陷**，见交付说明）——
主会话把那批缺陷修掉之后，这些断言都改成了「修好之后该是什么样」。

**还没修、仍按现状断言的一条**（标了「当前行为·缺陷」）：
插件的接口/动作跑在事件循环线程上（不在 QGIS 工作线程上）。
这条是**线程模型**问题，改动面大、要连着插件 SDK 一起动，
本轮只把它钉住（不假装它是对的），留给下一轮处理。

**已知副作用**：MCP 那一段在**本进程内**跑，QGIS 的图层对象一直开着样例数据文件，
所以系统临时目录里会留下一个 ``gisds_selftest_mcp_*`` 目录（进程退出后才删得掉）。
``tests/_workspace`` 和项目根目录不会留下任何东西。

跑法：``run-qgis-python.bat tests/check_plugin_console_mcp.py``
"""

from __future__ import annotations

import asyncio
import gc
import io
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import httpx  # noqa: E402

from server_helper import TestServer, _free_port  # noqa: E402

#: 服务级密钥 —— 带这个头的请求按管理员算（见 api/deps.py 的 _token_from）
API_KEY = "selftest-plugin-console-key"
ADMIN_PASSWORD = "adminpass123"
USER_PASSWORD = "pass12345"

#: 启动 QGIS python 的批处理（子进程探针要用）
LAUNCHER = PROJECT_ROOT / "run-qgis-python.bat"

fails = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 钉死它：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 115 = 插件 42 + 控制台 26 + 控制台超时 8 + MCP 35 + MCP stdio 5。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 115


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


# --------------------------------------------------------------------- 工具

def call(client, method, path, **kw):
    """发一个请求，返回 (状态码, 响应体)。

    服务要是已经没了，这里**干净地失败**（状态码 None），而不是抛异常把整个
    脚本带崩 —— 「插件把服务带死了」本身就是一条要报的断言。
    """
    try:
        r = client.request(method, path, **kw)
    except Exception as exc:
        return None, {"_error": f"请求失败（服务可能已经没了）：{type(exc).__name__}: {exc}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_text": r.text}


def err_text(body) -> str:
    """把错误响应里的说明文字抠出来（不同错误类型的键不一样）。"""
    if not isinstance(body, dict):
        return str(body)
    parts = []
    for key in ("message", "detail", "error"):
        value = body.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def console_err(body) -> str:
    body = body if isinstance(body, dict) else {}
    return str(body.get("error") or "")


def console_out(body) -> str:
    body = body if isinstance(body, dict) else {}
    return str(body.get("stdout") or "")


def snapshot(root: Path) -> set:
    """目录里所有文件的相对路径（用来证明生产插件目录没被动过）。"""
    out = set()
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        try:
            out.add(str(path.relative_to(root)))
        except Exception:
            continue
    return out


def _port_alive(port: int) -> bool:
    """这个端口还接得通吗（接得通 = 进程还活着，只是可能不搭理请求）。"""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=5):
            return True
    except Exception:
        return False


def make_zip(entries: dict[str, bytes]) -> bytes:
    """按「条目名 -> 内容」造一个 zip（内存里，不落盘）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


#: 示例插件的元数据
META = {
    "name": "自检插件",
    "version": "1.0.0",
    "description": "自检用的示例插件",
    "author": "gisds",
    "enabled": True,
}

#: 普通插件：面板 + 菜单项 + 自己的接口 + 自己的动作
PLUGIN_TEMPLATE = '''\
import threading

VERSION = "{version}"


def ping(request, principal):
    """插件自己注册的接口。"""
    return {{"pong": VERSION, "thread": threading.current_thread().name}}


def hello(params):
    """插件自己注册的动作（老写法：只收 params）。"""
    return {{"hello": params.get("n"), "version": VERSION,
             "thread": threading.current_thread().name}}


def whoami(params, principal):
    """收下**调用者身份**的动作（新写法：多收一个 principal）。"""
    return {{"user": getattr(principal, "username", "?"),
             "is_admin": bool(getattr(principal, "is_admin", False))}}


class GisdPlugin:
    """自检用的示例插件。"""

    def init(self, ctx):
        ctx.add_panel(
            id="{slug}_panel",
            title="自检面板",
            endpoint="/api/plugin/{slug}/ping",
        )
        ctx.add_menu_item(
            menu="tools",
            label="自检菜单项",
            endpoint="/api/plugin/{slug}/ping",
            method="POST",
        )
        ctx.add_route("/api/plugin/{slug}/ping", ping, methods=("GET", "POST"))
        ctx.add_action("hello", hello)
        ctx.add_action("whoami", whoami)

    def unload(self):
        pass
'''

#: 往 Processing 工具箱里加一个算法的插件
ALGO_PLUGIN = '''\
from qgis.core import QgsProcessingAlgorithm, QgsProcessingProvider


class CountAlgorithm(QgsProcessingAlgorithm):
    """自检用的算法（什么都不做，只证明注册上了）。"""

    def createInstance(self):
        return CountAlgorithm()

    def name(self):
        return "count"

    def displayName(self):
        return "自检计数"

    def group(self):
        return "自检插件分组"

    def groupId(self):
        return "gisds_selftest"

    def shortHelpString(self):
        return "自检用算法。"

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        return {}


class SelfTestProvider(QgsProcessingProvider):
    """自检插件的 provider。"""

    def id(self):
        return "gisds_selftest"

    def name(self):
        return "自检插件"

    def longName(self):
        return "自检插件（自检用）"

    def loadAlgorithms(self):
        self.addAlgorithm(CountAlgorithm())


class GisdPlugin:
    def init(self, ctx):
        ctx.register_processing_provider(SelfTestProvider())
'''

#: init() 直接抛异常的插件
BROKEN_PLUGIN = '''\
class GisdPlugin:
    def init(self, ctx):
        raise RuntimeError("自检：故意炸掉")
'''

#: 往 register_processing_provider 里塞了一个不是 provider 的东西
BAD_PROVIDER_PLUGIN = '''\
class GisdPlugin:
    def init(self, ctx):
        ctx.register_processing_provider(object())
'''


def plugin_zip(slug: str, source: str, version: str = "1.0.0", meta: dict | None = None) -> bytes:
    """造一个「正常形状」的插件 zip。"""
    payload = {**META, **(meta or {}), "version": version}
    return make_zip({
        f"{slug}/metadata.json": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        f"{slug}/__init__.py": source.encode("utf-8"),
    })


def install(client, headers, data: bytes, filename="plugin.zip"):
    return call(client, "POST", "/api/plugins/install", headers=headers,
                files={"file": (filename, data, "application/zip")})


# --------------------------------------------------------------------- 插件

def check_plugin_section() -> None:
    """插件系统：安装 / 启停 / 卸载 / 权限 / 坏插件 / zip 安全 / 状态持久化。"""
    print("\n== 1. 插件系统 ==")
    plugin_dir = Path(tempfile.mkdtemp(prefix="gisds_selftest_plugins_"))
    data_root = Path(tempfile.mkdtemp(prefix="gisds_selftest_data_"))
    prod_plugins = PROJECT_ROOT / "plugins"
    prod_before = snapshot(prod_plugins)
    try:
        with TestServer(
            port=_free_port(8903),
            require_login=True,
            data_root=data_root,
            extra_env={
                "GISDS_API_KEY": API_KEY,
                "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
                "GISDS_ALLOW_REGISTRATION": "1",
                # 插件目录必须隔离：生产目录里有 gisds_hello，碰了会影响线上
                "GISDS_PLUGIN_DIR": str(plugin_dir),
            },
        ) as server:
            with httpx.Client(base_url=server.base, timeout=90.0) as client:
                admin = {"X-API-Key": API_KEY}

                # ---------------------------------------------- 装一个插件
                print("\n-- 1.1 装插件 --")
                data = plugin_zip("demo", PLUGIN_TEMPLATE.format(slug="demo", version="v1"))
                status, body = install(client, admin, data)
                plugin = (body or {}).get("plugin") or {}
                check("装插件成功：ok=true、slug 对、loaded=true、error 为空",
                      status == 200 and (body or {}).get("ok") is True
                      and plugin.get("slug") == "demo"
                      and plugin.get("loaded") is True and not plugin.get("error"),
                      f"HTTP {status} {str(body)[:200]}")

                check("插件真的落在隔离目录里（生产 plugins/ 没被碰）",
                      (plugin_dir / "demo" / "metadata.json").is_file()
                      and (plugin_dir / "demo" / "__init__.py").is_file(),
                      f"{plugin_dir / 'demo'}")

                status, body = call(client, "GET", "/api/plugins", headers=admin)
                listed = next((p for p in ((body or {}).get("plugins") or [])
                               if p.get("slug") == "demo"), {})
                check("清单里有它，面板 / 菜单项 / 动作 / 接口都报了",
                      listed.get("loaded") is True
                      and any(p.get("id") == "demo_panel" for p in ((body or {}).get("panels") or []))
                      and any(m.get("label") == "自检菜单项" for m in ((body or {}).get("menus") or []))
                      and "hello" in (listed.get("actions") or [])
                      and "/api/plugin/demo/ping" in (listed.get("routes") or []),
                      f"listed={listed}")

                check("生产插件目录原封不动（gisds_hello 还在，且没多出文件）",
                      snapshot(prod_plugins) == prod_before
                      and (prod_plugins / "gisds_hello" / "__init__.py").is_file(),
                      f"{sorted(prod_before)}")

                # ---------------------------------------------- 接口 / 动作
                print("\n-- 1.2 插件的接口与动作 --")
                status, body = call(client, "GET", "/api/plugin/demo/ping", headers=admin)
                check("插件自己注册的接口能调通",
                      status == 200 and (body or {}).get("pong") == "v1",
                      f"HTTP {status} {str(body)[:160]}")

                status, body = call(client, "POST", "/api/plugins/demo/action",
                                    headers=admin, json={"action": "hello", "params": {"n": 3}})
                check("插件自己注册的动作能调通",
                      status == 200 and (body or {}).get("hello") == 3,
                      f"HTTP {status} {str(body)[:160]}")

                # 插件接口跑在哪条线程上？（项目自己的规矩：所有 QGIS 调用都得
                # 排到唯一那条 QGIS 工作线程上）
                status, body = call(client, "GET", "/api/plugin/demo/ping", headers=admin)
                route_thread = str((body or {}).get("thread") or "")
                status, body = call(client, "POST", "/api/console/exec", headers=admin,
                                    json={"code": "import threading; print(threading.current_thread().name)"})
                console_thread = console_out(body).strip()
                check("【当前行为·缺陷】插件的接口不在 QGIS 工作线程上跑（在事件循环线程）",
                      route_thread and "gisds-qgis" not in route_thread,
                      f"插件接口线程={route_thread!r}")
                check("对照：控制台的代码确实跑在 QGIS 工作线程上",
                      "gisds-qgis" in console_thread,
                      f"控制台线程={console_thread!r}")

                # ---------------------------------------------- Processing 算法
                print("\n-- 1.3 插件贡献的 Processing 算法 --")
                data = plugin_zip("algop", ALGO_PLUGIN, version="1.0.0")
                status, body = install(client, admin, data)
                status, body = call(client, "GET", "/api/processing/algorithms?provider=gisds_selftest",
                                    headers=admin)
                algos = (body or {}).get("algorithms") or []
                check("插件注册的算法出现在算法清单里（provider 名字对）",
                      len(algos) == 1 and algos[0].get("id") == "gisds_selftest:count"
                      and algos[0].get("provider") == "gisds_selftest",
                      f"{algos}")

                status, body = call(client, "POST", "/api/plugins/algop/disable", headers=admin)
                status, body = call(client, "GET", "/api/processing/algorithms?provider=gisds_selftest",
                                    headers=admin)
                check("插件停用后它的算法从清单里消失",
                      not ((body or {}).get("algorithms") or []),
                      f"{str(body)[:200]}")

                status, body = call(client, "POST", "/api/plugins/algop/enable", headers=admin)
                status, body = call(client, "GET", "/api/processing/algorithms?provider=gisds_selftest",
                                    headers=admin)
                check("插件重新启用后算法又回来",
                      len((body or {}).get("algorithms") or []) == 1,
                      f"{str(body)[:200]}")

                # ---------------------------------------------- 权限
                print("\n-- 1.4 权限 --")
                status, body = call(client, "POST", "/api/auth/register",
                                    json={"username": "pluguser", "password": USER_PASSWORD})
                user_token = str((body or {}).get("token") or "")
                check("能注册一个普通用户（下面的权限断言才有意义）",
                      status == 201 and bool(user_token), f"HTTP {status}")
                user = {"Authorization": f"Bearer {user_token}"}

                status, body = install(client, user, data)
                check("普通用户装插件 -> 403", status == 403, f"HTTP {status} {str(body)[:120]}")
                status, body = call(client, "POST", "/api/plugins/demo/disable", headers=user)
                check("普通用户停用插件 -> 403", status == 403, f"HTTP {status} {str(body)[:120]}")

                status, body = call(client, "POST", "/api/plugins/demo/action", headers=user,
                                    json={"action": "hello", "params": {"n": 1}})
                check("【当前行为】普通用户能调用插件的动作（动作只要求登录，权限由插件自己定）",
                      status == 200 and (body or {}).get("hello") == 1,
                      f"HTTP {status} {str(body)[:160]}")

                # 动作回调现在**能拿到调用者身份**了（多收一个 principal 即可）
                status, body = call(client, "POST", "/api/plugins/demo/action", headers=user,
                                    json={"action": "whoami", "params": {}})
                who = body if isinstance(body, dict) else {}
                check("动作回调能拿到调用者身份（插件自己做权限判断）",
                      status == 200 and who.get("user") not in (None, ""),
                      f"HTTP {status} {str(body)[:160]}")

                status, body = call(client, "GET", "/api/plugin/demo/ping", headers=user)
                check("【当前行为】普通用户也能调插件自己注册的接口",
                      status == 200 and (body or {}).get("pong") == "v1",
                      f"HTTP {status} {str(body)[:160]}")

                # ---------------------------------------------- 重装同名插件
                print("\n-- 1.5 重装同名插件 --")
                data2 = plugin_zip("demo", PLUGIN_TEMPLATE.format(slug="demo", version="v2"),
                                   version="2.0.0")
                status, body = install(client, admin, data2)
                status, body = call(client, "GET", "/api/plugin/demo/ping", headers=admin)
                check("重装同名插件后，接口返回的是**新版本**（热替换生效）",
                      status == 200 and (body or {}).get("pong") == "v2",
                      f"HTTP {status} {str(body)[:160]}")
                status, body = call(client, "POST", "/api/plugins/demo/action", headers=admin,
                                    json={"action": "hello", "params": {"n": 1}})
                check("同一个插件，接口与动作都是新版（没有两份代码并存）",
                      status == 200 and (body or {}).get("version") == "v2",
                      f"HTTP {status} {str(body)[:160]}")

                # ---------------------------------------------- 坏插件
                print("\n-- 1.6 坏插件不许拖垮服务 --")
                data = plugin_zip("badplug", BROKEN_PLUGIN, version="0.0.1")
                status, body = install(client, admin, data)
                plugin = (body or {}).get("plugin") or {}
                check("init() 抛异常的插件：loaded=false、error 说清原因",
                      status == 200 and plugin.get("loaded") is False
                      and "RuntimeError" in str(plugin.get("error") or ""),
                      f"HTTP {status} loaded={plugin.get('loaded')!r} "
                      f"error={plugin.get('error')!r}")

                status, body = call(client, "GET", "/api/plugins", headers=admin)
                bad = next((p for p in ((body or {}).get("plugins") or [])
                            if p.get("slug") == "badplug"), {})
                check("再拉一次清单，坏插件的 error 仍然在（不会被抹掉）",
                      bad.get("loaded") is False and "RuntimeError" in str(bad.get("error") or ""),
                      f"error={bad.get('error')!r}")

                status, body = call(client, "GET", "/api/plugin/demo/ping", headers=admin)
                check("坏插件没拖垮服务：别的插件照常能调",
                      status == 200 and (body or {}).get("pong") in ("v1", "v2"),
                      f"HTTP {status} {str(body)[:160]}")
                status, body = call(client, "GET", "/healthz")
                check("坏插件没拖垮服务：/healthz 200", status == 200, f"HTTP {status}")

                data = plugin_zip("badprov", BAD_PROVIDER_PLUGIN, version="0.0.1")
                status, body = install(client, admin, data)
                plugin = (body or {}).get("plugin") or {}
                check("register_processing_provider 传非 provider 对象：插件加载失败但服务活着",
                      status == 200 and plugin.get("loaded") is False
                      and bool(plugin.get("error")),
                      f"HTTP {status} loaded={plugin.get('loaded')!r} "
                      f"error={plugin.get('error')!r}")
                status, body = call(client, "GET", "/healthz")
                check("传非 provider 对象后 /healthz 仍然 200", status == 200, f"HTTP {status}")

                # ---------------------------------------------- slug 前缀冲突
                print("\n-- 1.7 slug 前缀冲突 --")
                install(client, admin, plugin_zip("a", PLUGIN_TEMPLATE.format(slug="a", version="v1")))
                install(client, admin, plugin_zip("a_b", PLUGIN_TEMPLATE.format(slug="a_b", version="v1")))
                status, body = call(client, "GET", "/api/plugin/a_b/ping", headers=admin)
                check("两个 slug 都在时，a_b 的接口能调通",
                      status == 200 and (body or {}).get("pong") == "v1",
                      f"HTTP {status} {str(body)[:160]}")
                status, body = call(client, "POST", "/api/plugins/a/disable", headers=admin)
                status, body = call(client, "GET", "/api/plugin/a_b/ping", headers=admin)
                check("停用插件 a 之后，插件 a_b 的接口照常能调（不该被连坐）",
                      status == 200 and (body or {}).get("pong") == "v1",
                      f"HTTP {status} {str(body)[:160]}")
                status, body = call(client, "GET", "/api/plugins", headers=admin)
                listed = next((p for p in ((body or {}).get("plugins") or [])
                               if p.get("slug") == "a_b"), {})
                check("清单里 a_b 仍是 loaded=true，且与实际一致（接口调得通）",
                      listed.get("loaded") is True,
                      f"listed={listed}")

                # ---------------------------------------------- zip 安全（补边角）
                print("\n-- 1.8 zip 安全（补既有自检没覆盖的边角）--")
                # 条目数超限（上限 2000）
                many = {f"demo/extra_{i}.txt": b"x" for i in range(2100)}
                many["demo/metadata.json"] = json.dumps(META).encode("utf-8")
                many["demo/__init__.py"] = b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n"
                status, body = install(client, admin, make_zip(many))
                check("条目数超 2000 的 zip 装不上（报错点明「条目太多」）",
                      status >= 400 and "条目太多" in err_text(body),
                      f"HTTP {status} {err_text(body)[:160]}")

                # 压缩包本身超 20MB（用随机数据，保证压不下去）
                big = os.urandom(21 * 1024 * 1024)
                status, body = install(client, admin, make_zip({
                    "demo/metadata.json": json.dumps(META).encode("utf-8"),
                    "demo/__init__.py": b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n",
                    "demo/blob.bin": big,
                }))
                check("压缩包本身超 20MB 的装不上（报错点明「太大」）",
                      status >= 400 and "太大" in err_text(body),
                      f"HTTP {status} {err_text(body)[:160]}")
                del big

                # zip 炸弹：解压后超 200MB（全是 0，压得极小）
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr("demo/metadata.json", json.dumps(META).encode("utf-8"))
                    zf.writestr("demo/__init__.py",
                                b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n")
                    # 注意：ZipInfo 默认是「不压缩」的，必须显式给 DEFLATED，
                    # 否则这个包本身就是 201MB，测的就不是「解压后太大」那条路了。
                    info = zipfile.ZipInfo("demo/bomb.bin")
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with zf.open(info, "w") as fh:
                        for _ in range(201):
                            fh.write(b"\0" * (1024 * 1024))
                bomb = buf.getvalue()
                status, body = install(client, admin, bomb)
                check("解压后超 200MB 的 zip 炸弹装不上（报错点明「解压后太大」）",
                      status >= 400 and "解压后太大" in err_text(body)
                      and len(bomb) < 20 * 1024 * 1024,
                      f"HTTP {status} {err_text(body)[:160]} 包本身={len(bomb) / 1024 / 1024:.1f}MB")
                del bomb

                # 嵌套 zip：里面的 zip 不该被解开
                inner = make_zip({"secret.txt": b"inner"})
                status, body = install(client, admin, make_zip({
                    "nest/metadata.json": json.dumps(META).encode("utf-8"),
                    "nest/__init__.py": b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n",
                    "nest/inner.zip": inner,
                }))
                nest_dir = plugin_dir / "nest"
                check("嵌套 zip 不会被解开（内层还是原样的 .zip 文件）",
                      status == 200
                      and (nest_dir / "inner.zip").is_file()
                      and not (nest_dir / "secret.txt").exists()
                      and not (nest_dir / "inner").exists(),
                      f"{sorted(p.name for p in nest_dir.iterdir()) if nest_dir.is_dir() else nest_dir}")
                if nest_dir.is_dir():
                    shutil.rmtree(nest_dir, ignore_errors=True)

                # metadata.json 非法
                status, body = install(client, admin, make_zip({
                    "badmeta/metadata.json": b"{not json",
                    "badmeta/__init__.py": b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n",
                }))
                check("metadata.json 是坏 JSON 的装不上（报错点明「解析不了」）",
                      status >= 400 and "解析不了" in err_text(body),
                      f"HTTP {status} {err_text(body)[:160]}")

                # metadata.json 不是对象
                status, body = install(client, admin, make_zip({
                    "arrmeta/metadata.json": b"[1, 2, 3]",
                    "arrmeta/__init__.py": b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n",
                }))
                check("metadata.json 不是 JSON 对象的装不上",
                      status >= 400 and "JSON 对象" in err_text(body),
                      f"HTTP {status} {err_text(body)[:160]}")

                # slug 与目录名不符
                status, body = install(client, admin, make_zip({
                    "dirname/metadata.json": json.dumps({**META, "slug": "othername"}).encode("utf-8"),
                    "dirname/__init__.py": b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n",
                }))
                check("metadata.json 里的 slug 与目录名不符的装不上（且不留半个目录）",
                      status >= 400 and "不一致" in err_text(body)
                      and not (plugin_dir / "dirname").exists(),
                      f"HTTP {status} {err_text(body)[:160]}")

                # slug 里带路径分隔符
                status, body = install(client, admin, make_zip({
                    "dirname2/metadata.json": json.dumps({**META, "slug": "a/b"}).encode("utf-8"),
                    "dirname2/__init__.py": b"class GisdPlugin:\n    def init(self, ctx):\n        pass\n",
                }))
                check("slug 里带路径分隔符的装不上（报错点明「不合法」）",
                      status >= 400 and "不合法" in err_text(body),
                      f"HTTP {status} {err_text(body)[:160]}")

                # 装完之后：插件目录里不该有越界文件
                check("失败的安装没在插件目录里写出越界文件",
                      not any(p.name == "evil.py" for p in plugin_dir.rglob("evil.py"))
                      and not (plugin_dir.parent / "evil.py").exists(),
                      f"{plugin_dir}")

                # ---------------------------------------------- 卸载
                print("\n-- 1.9 卸载 --")
                status, body = call(client, "POST", "/api/plugins/a_b/uninstall", headers=admin)
                check("卸载成功：ok=true", status == 200 and (body or {}).get("ok") is True,
                      f"HTTP {status} {str(body)[:160]}")
                check("卸载后目录真的没了，状态文件里也没它了",
                      not (plugin_dir / "a_b").exists()
                      and "a_b" not in (plugin_dir / ".state.json").read_text(encoding="utf-8"),
                      f"{plugin_dir / 'a_b'}")

                # ---------------------------------------------- 状态持久化
                print("\n-- 1.10 启停状态重启后还在不在 --")
                status, body = call(client, "POST", "/api/plugins/demo/disable", headers=admin)
                check("停用 demo 成功", status == 200
                      and ((body or {}).get("plugin") or {}).get("enabled") is False,
                      f"HTTP {status} {str(body)[:160]}")
                check("状态写进了插件目录的 .state.json",
                      (plugin_dir / ".state.json").is_file()
                      and "demo" in (plugin_dir / ".state.json").read_text(encoding="utf-8"),
                      f"{plugin_dir / '.state.json'}")

            # ---- 重启服务：状态还在不在
            print("\n-- 1.11 重启服务后状态还在不在 --")
        with TestServer(
            port=_free_port(8904),
            require_login=True,
            data_root=data_root,
            extra_env={
                "GISDS_API_KEY": API_KEY,
                "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
                "GISDS_ALLOW_REGISTRATION": "1",
                "GISDS_PLUGIN_DIR": str(plugin_dir),
            },
        ) as server:
            with httpx.Client(base_url=server.base, timeout=90.0) as client:
                admin = {"X-API-Key": API_KEY}
                status, body = call(client, "GET", "/api/plugins", headers=admin)
                listed = next((p for p in ((body or {}).get("plugins") or [])
                               if p.get("slug") == "demo"), {})
                check("重启服务后，被停用的插件仍然是停用的（.state.json 生效）",
                      listed.get("enabled") is False and listed.get("loaded") is False,
                      f"listed={listed}")
    finally:
        shutil.rmtree(plugin_dir, ignore_errors=True)
        shutil.rmtree(data_root, ignore_errors=True)


# --------------------------------------------------------------------- 控制台

CHILD_SCRIPT = '''\
# -*- coding: utf-8 -*-
"""子进程探针：跑一段控制台代码，把「护栏有没有挡住」打印出来。"""
import sys
from pathlib import Path

sys.path.insert(0, {root!r})

from gisds.core import pyconsole

code = Path(sys.argv[1]).read_text(encoding="utf-8")
res = pyconsole.run_in_session(pyconsole.session_for("child"), code)
print("GUARD_BLOCKED:" + str(res.error))
'''


def run_child_console(code_text: str, work_dir: Path) -> subprocess.CompletedProcess:
    """在**子进程**里跑一段控制台代码（护栏要是没挡住，服务当场就没了）。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    script = work_dir / "child_console.py"
    script.write_text(CHILD_SCRIPT.format(root=str(PROJECT_ROOT)), encoding="utf-8")
    code_file = work_dir / "code.py"
    code_file.write_text(code_text, encoding="utf-8")
    return subprocess.run(
        ["cmd.exe", "/c", str(LAUNCHER), str(script), str(code_file)],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, errors="replace", timeout=300,
    )


def check_console_section() -> None:
    """Python 控制台：护栏 / 绕过姿势 / 命名空间隔离 / 能不能改服务配置。"""
    print("\n== 2. Python 控制台 ==")
    data_root = Path(tempfile.mkdtemp(prefix="gisds_selftest_console_"))
    work_dir = Path(tempfile.mkdtemp(prefix="gisds_selftest_child_"))
    # 插件目录也要隔离：默认会指向生产 plugins/（里面有 gisds_hello）
    plugin_dir = Path(tempfile.mkdtemp(prefix="gisds_selftest_consoleplug_"))
    try:
        with TestServer(
            port=_free_port(8905),
            require_login=True,
            data_root=data_root,
            extra_env={
                "GISDS_API_KEY": API_KEY,
                "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
                "GISDS_ALLOW_REGISTRATION": "1",
                "GISDS_CONSOLE_ACCESS": "admin",
                "GISDS_CONSOLE_TIMEOUT": "20",
                # 插件目录也隔离：默认指向生产 plugins/（里面有 gisds_hello）
                "GISDS_PLUGIN_DIR": str(plugin_dir),
            },
        ) as server:
            with httpx.Client(base_url=server.base, timeout=90.0) as client:
                admin = {"X-API-Key": API_KEY}

                def run(code: str):
                    return call(client, "POST", "/api/console/exec", headers=admin,
                                json={"code": code})

                # ---------------------------------------------- 护栏
                print("\n-- 2.1 护栏 --")
                guards = [
                    ("os._exit(0)", "import os; os._exit(0)", "AttributeError"),
                    ("os.abort()", "import os; os.abort()", "AttributeError"),
                    ("os.execv()", "import os; os.execv('cmd', ['cmd'])", "AttributeError"),
                    ("exit()", "exit()", "RuntimeError"),
                    ("quit()", "quit()", "RuntimeError"),
                    ("sys.exit()", "import sys; sys.exit()", "退出"),
                ]
                for label, code, needle in guards:
                    status, body = run(code)
                    check(f"护栏挡住 {label}",
                          status == 200 and needle in console_err(body),
                          f"HTTP {status} error={console_err(body)[:140]}")

                status, body = call(client, "GET", "/healthz")
                check("一轮护栏打下来服务还活着（/healthz 200）", status == 200, f"HTTP {status}")

                # ---------------------------------------------- os 的其它写法
                print("\n-- 2.2 os 的各种写法 --")
                status, body = run("from os.path import join; print(join('a', 'b'))")
                check("from os.path import join 能用（护栏不该误伤 os.path）",
                      status == 200 and not console_err(body) and "a" in console_out(body),
                      f"error={console_err(body)[:160]} stdout={console_out(body)[:80]!r}")

                status, body = run("from os import getcwd; print('ok', getcwd()[:1])")
                check("对照：from os import getcwd 能用",
                      status == 200 and not console_err(body) and "ok" in console_out(body),
                      f"error={console_err(body)[:120]} stdout={console_out(body)[:80]!r}")

                status, body = run("import os.path; print(os.path.join('a', 'b'))")
                check("对照：import os.path 也能用",
                      status == 200 and not console_err(body) and "a" in console_out(body),
                      f"error={console_err(body)[:120]} stdout={console_out(body)[:80]!r}")

                victim = work_dir / "victim.txt"
                victim.write_text("x", encoding="utf-8")
                status, body = run(f"import os; os.remove({str(victim)!r}); print('removed')")
                check("【当前行为】os.remove 没被护栏挡住（控制台能删文件）",
                      status == 200 and not console_err(body) and not victim.exists(),
                      f"error={console_err(body)[:120]} 还在={victim.exists()}")

                # ---------------------------------------------- 绕过姿势（子进程里试）
                print("\n-- 2.3 护栏能不能被绕过（在子进程里试）--")
                proc = run_child_console("import os; os._real._exit(0)", work_dir)
                check("os._real._exit(0) 被挡住了（真 os 摸不到）",
                      "GUARD_BLOCKED" in (proc.stdout or ""),
                      f"rc={proc.returncode} out={(proc.stdout or '')[:120]!r}")

                proc = run_child_console("import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
                                         work_dir)
                check("os.kill 被挡住了（子进程不能把自己杀掉）",
                      "GUARD_BLOCKED" in (proc.stdout or ""),
                      f"rc={proc.returncode} out={(proc.stdout or '')[:120]!r}")

                proc = run_child_console("import os; os.__dict__['_exit'](0)", work_dir)
                check("os.__dict__['_exit'](0) 被挡住了（护栏的 __dict__ 里没有 _exit）",
                      "GUARD_BLOCKED" in (proc.stdout or "")
                      and "KeyError" in (proc.stdout or ""),
                      f"rc={proc.returncode} out={(proc.stdout or '')[:160]!r}")

                # ---------------------------------------------- 控制台改服务自己的配置
                print("\n-- 2.4 控制台能不能改服务自己的配置 --")
                status, body = call(client, "POST", "/api/console/exec", headers=admin,
                                    json={"code": "from gisds.api.deps import get_config; "
                                                 "c = get_config(); c.console_access = 'user'; "
                                                 "print(c.console_access)"})
                check("控制台能改服务自己的配置（console_access 被改成 user）",
                      status == 200 and "user" in console_out(body),
                      f"stdout={console_out(body)[:80]!r} error={console_err(body)[:120]}")

                status, body = call(client, "POST", "/api/auth/register",
                                    json={"username": "conuser_a", "password": USER_PASSWORD})
                token_a = str((body or {}).get("token") or "")
                status, body = call(client, "POST", "/api/auth/register",
                                    json={"username": "conuser_b", "password": USER_PASSWORD})
                token_b = str((body or {}).get("token") or "")
                user_a = {"Authorization": f"Bearer {token_a}"}
                user_b = {"Authorization": f"Bearer {token_b}"}
                check("能注册两个普通用户（下面的断言才有意义）",
                      bool(token_a) and bool(token_b), f"{status}")

                status, body = call(client, "POST", "/api/console/exec", headers=user_a,
                                    json={"code": "print('now allowed')"})
                check("【当前行为·缺陷】改完之后普通用户就能用控制台了（访问控制被代码改掉了）",
                      status == 200 and "now allowed" in console_out(body),
                      f"HTTP {status} stdout={console_out(body)[:80]!r} "
                      f"error={console_err(body)[:120]}")

                # ---------------------------------------------- 命名空间隔离
                # 上面刚把 console_access 改成了 user，普通用户现在能进控制台了
                print("\n-- 2.5 命名空间隔离（两个普通用户各用各的）--")
                status, body = call(client, "POST", "/api/console/exec", headers=user_a,
                                    json={"code": "shared_var = 'A'"})
                check("A 执行 shared_var = 'A' 成功",
                      status == 200 and not console_err(body),
                      f"HTTP {status} error={console_err(body)[:120]}")
                status, body = call(client, "POST", "/api/console/exec", headers=user_b,
                                    json={"code": "shared_var = 'B'"})
                check("B 执行 shared_var = 'B' 成功",
                      status == 200 and not console_err(body),
                      f"HTTP {status} error={console_err(body)[:120]}")

                status, body = call(client, "POST", "/api/console/exec", headers=user_b,
                                    json={"code": "print(shared_var)"})
                check("B 看到的是自己的变量（B），看不到 A 的",
                      status == 200 and "B" in console_out(body)
                      and "A" not in console_out(body),
                      f"stdout={console_out(body)[:80]!r} error={console_err(body)[:120]}")

                status, body = call(client, "POST", "/api/console/reset", headers=user_a)
                check("A 自己的 reset 成功", status == 200 and (body or {}).get("ok") is True,
                      f"HTTP {status} {str(body)[:120]}")
                status, body = call(client, "POST", "/api/console/exec", headers=user_b,
                                    json={"code": "print(shared_var)"})
                check("A reset 之后，B 的变量还在（reset 只清自己那份）",
                      status == 200 and "B" in console_out(body),
                      f"stdout={console_out(body)[:80]!r} error={console_err(body)[:120]}")
                status, body = call(client, "POST", "/api/console/exec", headers=user_a,
                                    json={"code": "print(shared_var)"})
                check("A reset 之后，A 自己的变量没了（报 NameError）",
                      status == 200 and "NameError" in console_err(body),
                      f"error={console_err(body)[:120]}")

                # ---------------------------------------------- 把配置改回去
                print("\n-- 2.6 把配置改回去 --")
                status, body = call(client, "POST", "/api/console/exec", headers=admin,
                                    json={"code": "from gisds.api.deps import get_config; "
                                                 "c = get_config(); c.console_access = 'admin'; "
                                                 "print(c.console_access)"})
                check("控制台也能把配置改回去（服务没被卡死）",
                      status == 200 and "admin" in console_out(body),
                      f"stdout={console_out(body)[:80]!r} error={console_err(body)[:120]}")
                status, body = call(client, "POST", "/api/console/exec", headers=user_a,
                                    json={"code": "print('blocked again')"})
                check("改回去之后普通用户又被挡在门外",
                      status == 403, f"HTTP {status} {str(body)[:120]}")

                status, body = call(client, "POST", "/api/console/exec", headers=admin,
                                    json={"code": "from gisds.api.deps import get_config; "
                                                 "print(get_config().api_key)"})
                check("【当前行为】控制台能直接读到服务级密钥（没有沙箱）",
                      status == 200 and API_KEY in console_out(body),
                      f"stdout={console_out(body)[:120]!r}")
    finally:
        shutil.rmtree(data_root, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(plugin_dir, ignore_errors=True)


def check_console_timeout_section() -> None:
    """控制台超时：提示说清了吗？线程还占着吗？服务还能用吗？

    分两种数据目录各起一个服务：
    * 数据目录里**有**数据集 -> 注册中心非空，/healthz 不用碰 QGIS 线程，
      超时之后服务还能应答，只是 QGIS 相关的请求全卡住。
    * 数据目录**空** -> 注册中心是空的，/healthz 每次都会去扫目录，
      而扫目录要排到 QGIS 线程上 —— 那条线程正被死循环占着，
      于是 /healthz 把**事件循环**也堵死了，整个服务一个请求都不回。
    """
    print("\n== 3. 控制台超时（死循环）==")
    sample = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
    base_data = Path(tempfile.mkdtemp(prefix="gisds_selftest_timeout_"))
    base_plug = Path(tempfile.mkdtemp(prefix="gisds_selftest_timeoutplug_"))
    try:
        # ---------------------------------------------- 场景 A：数据目录里有数据集
        print("\n-- 3.1 数据目录里有数据集 --")
        data_a = base_data / "with_data"
        (data_a / "shared").mkdir(parents=True, exist_ok=True)
        if sample.is_dir():
            shutil.copytree(sample, data_a / "shared" / "example")
        with TestServer(
            port=_free_port(8906),
            require_login=True,
            data_root=data_a,
            extra_env={
                "GISDS_API_KEY": API_KEY,
                "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
                "GISDS_CONSOLE_TIMEOUT": "2",
                # 插件目录也隔离：默认指向生产 plugins/
                "GISDS_PLUGIN_DIR": str(base_plug),
            },
        ) as server:
            admin = {"X-API-Key": API_KEY}
            with httpx.Client(base_url=server.base, timeout=90.0) as client:
                status, body = call(
                    client, "POST", "/api/console/exec", headers=admin,
                    json={"code": "import time\nwhile True:\n    time.sleep(0.05)"},
                )
                err = console_err(body)
                check("死循环会超时返回（提示点明「超时」）",
                      status == 200 and "超时" in err,
                      f"HTTP {status} error={err[:200]}")
                check("超时提示说清了「线程停不下来、还占着 QGIS 线程」",
                      "线程" in err and ("停不下来" in err or "中断" in err or "占着" in err),
                      f"error={err[:220]}")

            # 端口还在监听 = 进程还活着（只是不搭理请求了）
            check("超时之后服务进程还活着（端口还在监听）",
                  _port_alive(server.port), f"port={server.port}")

            with httpx.Client(base_url=server.base, timeout=15.0) as fresh:
                status, body = call(fresh, "GET", "/healthz")
            check("超时之后 /healthz 还能应答（注册中心非空，探针不用碰 QGIS 线程）",
                  status == 200, f"HTTP {status} {str(body)[:120]}")

            try:
                with httpx.Client(base_url=server.base, timeout=5.0) as probe:
                    probe.get("/api/processing/algorithms?provider=native", headers=admin)
                answered = True
            except Exception:
                answered = False
            check("【当前行为】超时之后 QGIS 线程确实还占着（后续 QGIS 请求 5 秒内没返回）",
                  answered is False,
                  f"answered={answered}")

        # ---------------------------------------------- 场景 B：数据目录是空的
        print("\n-- 3.2 数据目录是空的（注册中心为空，探针也会去扫目录）--")
        data_b = base_data / "empty"
        data_b.mkdir(parents=True, exist_ok=True)
        with TestServer(
            port=_free_port(8907),
            require_login=True,
            data_root=data_b,
            extra_env={
                "GISDS_API_KEY": API_KEY,
                "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
                "GISDS_CONSOLE_TIMEOUT": "2",
                "GISDS_PLUGIN_DIR": str(base_plug),
            },
        ) as server:
            admin = {"X-API-Key": API_KEY}
            with httpx.Client(base_url=server.base, timeout=90.0) as client:
                status, body = call(
                    client, "POST", "/api/console/exec", headers=admin,
                    json={"code": "import time\nwhile True:\n    time.sleep(0.05)"},
                )
                check("空数据目录下死循环同样会超时返回",
                      status == 200 and "超时" in console_err(body),
                      f"HTTP {status} error={console_err(body)[:200]}")

            check("超时之后服务进程还活着（端口还在监听）",
                  _port_alive(server.port), f"port={server.port}")

            try:
                with httpx.Client(base_url=server.base, timeout=8.0) as probe:
                    probe.get("/healthz")
                answered = True
            except Exception:
                answered = False
            check("超时之后连 /healthz 仍然应答（探针不碰 QGIS 线程，不会被焊死）",
                  answered is True,
                  f"answered={answered}")
    finally:
        shutil.rmtree(base_data, ignore_errors=True)
        shutil.rmtree(base_plug, ignore_errors=True)


# --------------------------------------------------------------------- MCP

#: 27 个工具的调用顺序（编辑类要按「先进编辑模式、再改、最后保存」的顺序）
MCP_TOOL_ORDER = (
    "gis_list_datasets",
    "gis_get_dataset",
    "gis_list_layers",
    "gis_rescan",
    "gis_query_point",
    "gis_query_bbox",
    "gis_layer_area",
    "gis_layer_histogram",
    "gis_list_features",
    "gis_vector_stats",
    "gis_point_hit",
    "gis_render_map",
    "gis_list_algorithms",
    "gis_describe_algorithm",
    "gis_run_algorithm",
    "gis_attribute_table",
    "gis_field_stats",
    "gis_select_features",
    "gis_edit_start",
    "gis_edit_feature",
    "gis_add_feature",
    "gis_delete_features",
    "gis_field_calculator",
    "gis_edit_commit",
    "gis_edit_rollback",
    "gis_list_bookmarks",
    "gis_add_bookmark",
)


async def mcp_call(server, tool: str, **kwargs) -> dict:
    """调一个 MCP 工具，返回 {ok, text, images, detail}。"""
    try:
        result = await server.call_tool(tool, kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": "", "images": 0,
                "detail": f"抛异常：{type(exc).__name__}: {exc}"}
    content = getattr(result, "content", None) or []
    texts = [getattr(c, "text", "") for c in content if getattr(c, "text", None)]
    images = [c for c in content if getattr(c, "type", "") == "image"]
    text = "\n".join(texts)
    bad = bool(getattr(result, "isError", False))
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("error"):
            bad = True
    except Exception:
        pass
    return {"ok": not bad, "text": text, "images": len(images), "detail": text[:200]}


def mcp_env(data_root: Path) -> dict:
    env = dict(os.environ)
    env["GISDS_DATA_ROOT"] = str(data_root)
    env["GISDS_SHARED_ROOT"] = str(data_root / "shared")
    env["GISDS_USERS_ROOT"] = str(data_root / "users")
    env["GISDS_DB_PATH"] = str(data_root / "gisds.db")
    # 明明要求登录、明明配了密钥，MCP 也不该因此就能免鉴权 —— 下面会验这一点
    env["GISDS_REQUIRE_LOGIN"] = "1"
    env["GISDS_API_KEY"] = API_KEY
    return env


def check_mcp_section() -> None:
    """MCP：27 个工具逐个调 / 鉴权 / 租户隔离。"""
    print("\n== 4. MCP 接口 ==")
    data_root = Path(tempfile.mkdtemp(prefix="gisds_selftest_mcp_"))
    sample = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
    try:
        if sample.is_dir():
            shutil.copytree(sample, data_root / "shared" / "example")
        # 造一个「别的用户的私有空间」数据集
        private = data_root / "users" / "someone" / "private_ds"
        private.mkdir(parents=True, exist_ok=True)
        shutil.copy(sample / "boundary.geojson", private / "private.geojson")

        saved_env = {k: os.environ.get(k) for k in
                     ("GISDS_DATA_ROOT", "GISDS_SHARED_ROOT", "GISDS_USERS_ROOT",
                      "GISDS_DB_PATH", "GISDS_REQUIRE_LOGIN", "GISDS_API_KEY")}
        os.environ.update(mcp_env(data_root))
        try:
            asyncio.run(_mcp_checks(data_root))
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            # QGIS 的图层对象还开着这些数据文件，得先让它们被回收，
            # 否则删目录会静默失败（Windows 上文件被占着删不掉）。
            gc.collect()
            for _ in range(10):
                shutil.rmtree(data_root, ignore_errors=True)
                if not data_root.exists():
                    break
                time.sleep(0.3)
    finally:
        shutil.rmtree(data_root, ignore_errors=True)


async def _mcp_checks(data_root: Path) -> None:
    from gisds.mcp.server import build_server

    server = build_server()
    check("MCP Server 造得起来", server is not None)

    tools = await server.list_tools()
    names = [t.name for t in tools]
    check("工具是 27 个", len(names) == 27, f"实际 {len(names)}")
    check("每个工具都有说明",
          all((t.description or "").strip() for t in tools),
          f"共 {len(tools)} 个")

    # ---- 27 个工具逐个调一遍
    print("\n-- 4.1 27 个工具逐个调一遍 --")
    results: dict[str, dict] = {}

    results["gis_list_datasets"] = await mcp_call(server, "gis_list_datasets")
    results["gis_get_dataset"] = await mcp_call(server, "gis_get_dataset", dataset_id="example")
    results["gis_list_layers"] = await mcp_call(server, "gis_list_layers")
    results["gis_rescan"] = await mcp_call(server, "gis_rescan")
    results["gis_query_point"] = await mcp_call(
        server, "gis_query_point", dataset_id="example", layer_id="classes",
        lon=112.48, lat=39.685)
    results["gis_query_bbox"] = await mcp_call(
        server, "gis_query_bbox", dataset_id="example", layer_id="classes",
        minx=112.40, miny=39.62, maxx=112.56, maxy=39.75)
    results["gis_layer_area"] = await mcp_call(
        server, "gis_layer_area", dataset_id="example", layer_id="classes")
    results["gis_layer_histogram"] = await mcp_call(
        server, "gis_layer_histogram", dataset_id="example", layer_id="surface", bins=8)
    results["gis_list_features"] = await mcp_call(
        server, "gis_list_features", dataset_id="example", vector_id="boundary")
    results["gis_vector_stats"] = await mcp_call(
        server, "gis_vector_stats", dataset_id="example", vector_id="boundary")
    results["gis_point_hit"] = await mcp_call(
        server, "gis_point_hit", dataset_id="example", vector_id="boundary",
        lon=112.48, lat=39.685)
    results["gis_render_map"] = await mcp_call(
        server, "gis_render_map", dataset_id="example", layer_id="classes", width=320)
    results["gis_list_algorithms"] = await mcp_call(server, "gis_list_algorithms", query="buffer")
    results["gis_describe_algorithm"] = await mcp_call(
        server, "gis_describe_algorithm", algorithm_id="native:buffer")
    results["gis_run_algorithm"] = await mcp_call(
        server, "gis_run_algorithm", algorithm="native:buffer",
        parameters={"INPUT": "example:boundary", "DISTANCE": 0.01,
                    "END_CAP_STYLE": 1, "JOIN_STYLE": 1})
    results["gis_attribute_table"] = await mcp_call(
        server, "gis_attribute_table", ref="example:boundary", limit=10)
    results["gis_field_stats"] = await mcp_call(server, "gis_field_stats", ref="example:boundary")
    results["gis_select_features"] = await mcp_call(
        server, "gis_select_features", ref="example:boundary", mode="all")
    results["gis_edit_start"] = await mcp_call(server, "gis_edit_start", ref="example:boundary")

    # 下面几个要用到上一步查出来的 fid / 字段名
    field_name = ""
    try:
        table = json.loads(results["gis_attribute_table"]["text"])
        fields = table.get("fields") or []
        if fields:
            field_name = str(fields[0].get("name") or "")
        else:
            rows = table.get("rows") or []
            if rows:
                field_name = str(next(iter(rows[0].keys())))
    except Exception:
        field_name = ""
    try:
        selection = json.loads(results["gis_select_features"]["text"])
        fid = (selection.get("fids") or [0])[0]
    except Exception:
        fid = 0

    results["gis_edit_feature"] = await mcp_call(
        server, "gis_edit_feature", ref="example:boundary", fid=int(fid),
        attributes={field_name: "自检写入"} if field_name else {})
    results["gis_add_feature"] = await mcp_call(
        server, "gis_add_feature", ref="example:boundary",
        geometry={"type": "Polygon",
                  "coordinates": [[[113.0, 40.0], [113.1, 40.0], [113.1, 40.1],
                                   [113.0, 40.1], [113.0, 40.0]]]},
        attributes={})
    try:
        added = json.loads(results["gis_add_feature"]["text"])
        added_fid = added.get("fid")
    except Exception:
        added_fid = None
    results["gis_delete_features"] = await mcp_call(
        server, "gis_delete_features", ref="example:boundary",
        fids=[added_fid] if added_fid is not None else [])
    results["gis_field_calculator"] = await mcp_call(
        server, "gis_field_calculator", ref="example:boundary",
        field=field_name, expression="1")
    results["gis_edit_commit"] = await mcp_call(server, "gis_edit_commit", ref="example:boundary")
    results["gis_edit_rollback"] = await mcp_call(server, "gis_edit_rollback", ref="example:boundary")
    results["gis_list_bookmarks"] = await mcp_call(server, "gis_list_bookmarks")
    results["gis_add_bookmark"] = await mcp_call(
        server, "gis_add_bookmark", name="MCP 自检书签", extent=[112.0, 39.0, 112.1, 39.1])

    for name in MCP_TOOL_ORDER:
        r = results.get(name) or {"ok": False, "text": "", "images": 0, "detail": "没调用"}
        has_payload = bool(r.get("text")) or bool(r.get("images"))
        check(f"MCP 工具 {name} 调得通、且有内容",
                      bool(r.get("ok")) and has_payload,
                      str(r.get("detail"))[:160])

    # 顺手把**本次**算法输出目录清掉（别在系统临时目录里堆垃圾；
    # 只删自己那几个，别把别的会话的输出也清了）
    try:
        algo_result = json.loads(results["gis_run_algorithm"]["text"])
        for entry in (algo_result.get("outputs") or {}).values():
            out_path = Path(str(entry.get("path") or ""))
            if out_path.name and out_path.parent.name and out_path.parent.parent.name == "gisds_algo":
                shutil.rmtree(out_path.parent, ignore_errors=True)
    except Exception:
        pass

    # ---- 鉴权 / 租户隔离
    #
    # **这是写进 README 的设计决定，不是缺陷**：README「MCP Server」一节明写
    # 「**MCP 是服务级工具**，不走 HTTP 鉴权，能看到所有空间」。
    # MCP 是运维 / Agent 侧的服务级工具，与「网页上的多租户隔离」是两条线。
    # 这里把**当前的设计**钉住 —— 免得后人以为它是有租户隔离的，
    # 或者反过来以为「看不到别人的空间」才是应该的。
    print("\n-- 4.2 MCP 的权限模型（服务级工具，按设计：能看到所有空间）--")
    layers = json.loads(results["gis_list_layers"]["text"])
    refs = [i.get("ref") for i in layers.get("layers", [])]
    check("MCP 能看到别的用户私有空间的数据集（服务级工具，按设计如此）",
          "private_ds:private" in refs,
          f"refs={refs}")

    private_table = await mcp_call(server, "gis_attribute_table", ref="private_ds:private", limit=5)
    check("MCP 能读别的用户私有空间的数据（服务级工具，按设计如此）",
          bool(private_table.get("ok")) and '"rows"' in private_table.get("text", ""),
          str(private_table.get("detail"))[:160])

    started = await mcp_call(server, "gis_edit_start", ref="private_ds:private")
    check("MCP 能进别的用户私有空间的数据的编辑模式（服务级工具，按设计如此）",
          bool(started.get("ok")) and '"is_editing": true' in started.get("text", ""),
          str(started.get("detail"))[:160])
    await mcp_call(server, "gis_edit_rollback", ref="private_ds:private")

    check("MCP 不需要任何凭据（服务级工具；改用 HTTP 传输时要注意暴露面）",
          bool(results["gis_list_datasets"].get("ok")),
          "MCP Server 里没有鉴权这一层 —— 这是设计，不是漏掉")


# --------------------------------------------------------------------- MCP stdio

def check_mcp_stdio_section() -> None:
    """MCP stdio 传输：进程起得来吗、握手成不成功、stdout 干不干净。"""
    print("\n== 5. MCP stdio 传输 ==")
    data_root = Path(tempfile.mkdtemp(prefix="gisds_selftest_stdio_"))
    sample = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
    try:
        if sample.is_dir():
            shutil.copytree(sample, data_root / "shared" / "example")
        env = mcp_env(data_root)
        # stderr 一定要落到文件里：用 PIPE 的话，QGIS 初始化那一堆告警
        # 会把管道写满，服务进程就被卡住不动了（自检自己先挂）。
        err_file = open(data_root / "stdio_stderr.txt", "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(LAUNCHER), "-m", "gisds", "mcp"],
            cwd=str(PROJECT_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err_file,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        lines: "queue.Queue[str | None]" = queue.Queue()

        def _reader():
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    lines.put(line)
            except Exception:
                pass
            lines.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        def send(payload: dict) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        def read_json(want_id: int, timeout: float = 240.0):
            """读到一条 id 匹配的消息；顺便统计非 JSON 的行数。"""
            deadline = time.time() + timeout
            junk = 0
            while time.time() < deadline:
                try:
                    line = lines.get(timeout=max(1.0, deadline - time.time()))
                except queue.Empty:
                    return None, junk
                if line is None:
                    return None, junk
                text = line.strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except Exception:
                    junk += 1
                    continue
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg, junk
            return None, junk

        try:
            from mcp.types import LATEST_PROTOCOL_VERSION

            send({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "gisds-selftest", "version": "1.0"},
                },
            })
            msg, junk = read_json(1)
            ok_init = bool(msg and msg.get("result"))
            check("stdio 传输：握手成功（initialize 有回应）", ok_init,
                  f"msg={str(msg)[:200]}")
            server_info = ((msg or {}).get("result") or {}).get("serverInfo") or {}
            check("stdio 传输：服务端自报家门（name=gis-data-server）",
                  server_info.get("name") == "gis-data-server",
                  f"serverInfo={server_info}")

            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            msg2, junk2 = read_json(2)
            tool_list = ((msg2 or {}).get("result") or {}).get("tools") or []
            check("stdio 传输：tools/list 返回 27 个工具", len(tool_list) == 27,
                  f"实际 {len(tool_list)}")
            check("stdio 传输：stdout 上除了协议消息没有别的输出（协议通道干净）",
                  junk + junk2 == 0, f"非 JSON 行数={junk + junk2}")
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, timeout=30)
            except Exception:
                pass
            try:
                proc.wait(timeout=20)
            except Exception:
                pass
            try:
                err_file.close()
            except Exception:
                pass

        # ---- 起不来的情况：参数不合法要**响亮地失败**，而不是干等
        broken = subprocess.run(
            ["cmd.exe", "/c", str(LAUNCHER), "-m", "gisds", "mcp", "--transport", "bogus"],
            cwd=str(PROJECT_ROOT), env=env,
            capture_output=True, text=True, errors="replace", timeout=300,
        )
        check("MCP 启动参数不合法时进程非零退出（不是干等着）",
              broken.returncode != 0 and not (broken.stdout or "").strip(),
              f"rc={broken.returncode} out={(broken.stdout or '')[:120]!r}")
    finally:
        shutil.rmtree(data_root, ignore_errors=True)


# --------------------------------------------------------------------- 入口

def main() -> int:
    check_plugin_section()
    check_console_section()
    check_console_timeout_section()
    check_mcp_section()
    check_mcp_stdio_section()

    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被跳过了）",
          checked == EXPECTED_CHECKS, str(checked))
    print(f"\n== 结果：{checked - len(fails)} 通过 / {len(fails)} 失败 ==")
    if fails:
        print("  失败项:", fails)
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
