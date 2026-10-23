# -*- coding: utf-8 -*-
"""插件系统自检。

插件 = 服务端 Python 插件：装上去就是一段在数据服务器上跑的代码。
要钉住的是四件事：

1. **装得上、用得了**：zip 装进去，面板 / 菜单项出现在清单里，
   插件自己注册的接口和动作都能调通。
2. **能管**：停用 / 启用 / 重载 / 卸载都按预期改变清单。插件自己注册的接口
   也要跟着插件一起摘掉 —— 停了调不到，再启用又调得到。
3. **权限**：装 / 启停 / 卸载**只有管理员能做**；清单所有人都能看，
   但 ``can_manage`` 对普通用户是 false。
4. **zip 是不可信输入**：路径穿越、绝对路径、符号链接、缺 metadata.json、
   坏 zip —— 一样都不许装上，更不许在插件目录外写出文件。
   插件自己炸了也不许把服务带死。

跑法：``run-qgis-python.bat tests/check_plugins.py``
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from server_helper import TestServer, _free_port  # noqa: E402

#: 服务级密钥 —— 带这个头的请求按管理员算（见 api/deps.py 的 _token_from）
API_KEY = "test-plugin-key"
ADMIN_PASSWORD = "adminpass123"
USER_PASSWORD = "pass12345"

#: 插件的 slug = 插件目录名（zip 里那一层）
SLUG = "demo_plugin"

fails: list[str] = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 钉死它：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
EXPECTED_CHECKS = 38

#: 示例插件的元数据
META = {
    "name": "自检插件",
    "version": "1.0.0",
    "description": "自检用的示例插件",
    "author": "gisds",
    "enabled": True,
}
META_BYTES = json.dumps(META, ensure_ascii=False).encode("utf-8")

#: 示例插件的代码。面板、菜单项、接口、动作各贡献一样，
#: 网页就是靠清单里的面板 / 菜单项把它们画出来的。
PLUGIN_INIT = '''\
def ping(request, principal):
    """插件自己注册的接口。"""
    return {"pong": 1}


def hello(params):
    """插件自己注册的动作。"""
    return {"hello": params.get("n")}


class GisdPlugin:
    """自检用的示例插件。"""

    def init(self, ctx):
        ctx.add_panel(
            id="demo_panel",
            title="自检面板",
            endpoint="/api/plugin/demo_plugin/ping",
        )
        ctx.add_menu_item(
            menu="tools",
            label="自检菜单项",
            endpoint="/api/plugin/demo_plugin/ping",
            method="POST",
        )
        ctx.add_route("/api/plugin/demo_plugin/ping", ping, methods=("GET", "POST"))
        ctx.add_action("hello", hello)
        ctx.log("自检插件已加载")

    def unload(self):
        pass
'''
PLUGIN_INIT_BYTES = PLUGIN_INIT.encode("utf-8")

#: 故意在 init 里**先注册接口、再炸掉**的插件。
#:
#: 这种插件加载失败时，它注册过的接口必须一并摘掉 ——
#: 否则「插件已经炸了，它的接口却还能被调」。
BROKEN_INIT = '''\
def bad_ping(request, principal):
    """坏插件注册的接口。"""
    return {"bad_pong": 1}


class GisdPlugin:
    """先注册接口、再炸掉的坏插件。"""

    def init(self, ctx):
        ctx.add_route("/api/plugin/bad_plugin/ping", bad_ping, methods=("GET", "POST"))
        raise RuntimeError("炸了")
'''
BROKEN_INIT_BYTES = BROKEN_INIT.encode("utf-8")


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


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


def make_zip(entries: dict[str, bytes]) -> bytes:
    """按「条目名 -> 内容」造一个 zip（内存里，不落盘）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_zip_with_symlink(entries: dict[str, bytes], link_name: str, target: str) -> bytes:
    """造一个带**符号链接**条目的 zip。

    符号链接在 zip 里靠 unix 权限位 0o120000 标记，``zipfile`` 不会自己造，
    只能手写 ``external_attr``。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        info = zipfile.ZipInfo(link_name)
        info.external_attr = 0o120777 << 16
        zf.writestr(info, target)
    return buf.getvalue()


def install(client, headers, data: bytes, filename="plugin.zip"):
    """上传一个 zip 装插件。"""
    return call(client, "POST", "/api/plugins/install", headers=headers,
                files={"file": (filename, data, "application/zip")})


def find_files(root: Path, name: str) -> list[Path]:
    """递归找叫这个名字的文件。"""
    if not root.is_dir():
        return []
    return [p for p in root.rglob(name) if p.is_file()]


def handler_alive(client, headers, path) -> tuple[bool, str]:
    """插件的接口还挂着：200，而且拿得到 handler 的返回值。"""
    status, body = call(client, "GET", path, headers=headers)
    ok = status == 200 and isinstance(body, dict) and body.get("pong") == 1
    return ok, f"HTTP {status} {str(body)[:120]}"


def handler_gone(client, headers, path, marker: str) -> tuple[bool, str]:
    """插件的接口已经摘掉：不是 200，而且拿不到 handler 的返回值。

    光看状态码不够 —— 万一某个兜底路由回了 200，接口其实还在。
    所以连 handler 的返回值一起查。
    """
    status, body = call(client, "GET", path, headers=headers)
    text = str(body)
    ok = status != 200 and marker not in text
    return ok, f"HTTP {status} {text[:120]}"


def main() -> int:
    # 插件目录指向临时目录：别污染正式数据目录，脚本跑完还得清掉。
    plugin_dir = Path(tempfile.mkdtemp(prefix="gisds_plugins_"))
    try:
        with TestServer(
            # 端口动态挑空闲的：写死的话两个会话并行跑，后一个的健康检查会连到
            # 前一个的服务上（表现是「莫名 401 / 页面跳登录页」）。
            port=_free_port(8896),
            require_login=True,
            extra_env={
                "GISDS_API_KEY": API_KEY,
                "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
                "GISDS_ALLOW_REGISTRATION": "1",
                "GISDS_PLUGIN_DIR": str(plugin_dir),
            },
        ) as server:
            with httpx.Client(base_url=server.base, timeout=60.0) as client:
                admin = {"X-API-Key": API_KEY}

                # ------------------------------------------------ 清单（还没装插件）
                print("== 1. 清单（还没装插件）==")
                status, body = call(client, "GET", "/api/plugins", headers=admin)
                check("管理员看清单：还没有插件，can_manage=true",
                      status == 200
                      and list((body or {}).get("plugins") or []) == []
                      and (body or {}).get("can_manage") is True,
                      f"HTTP {status} {str(body)[:160]}")

                # ------------------------------------------------ 安装
                print("\n== 2. 装一个 zip 插件 ==")
                data = make_zip({
                    f"{SLUG}/metadata.json": META_BYTES,
                    f"{SLUG}/__init__.py": PLUGIN_INIT_BYTES,
                })
                status, body = install(client, admin, data)
                plugin = (body or {}).get("plugin") or {}
                check("安装成功：ok=true、slug / name / version 都对",
                      status == 200
                      and (body or {}).get("ok") is True
                      and plugin.get("slug") == SLUG
                      and plugin.get("name") == META["name"]
                      and plugin.get("version") == META["version"],
                      f"HTTP {status} {str(body)[:200]}")
                check("装完就加载上了（loaded=true、error 为空）",
                      plugin.get("loaded") is True and not plugin.get("error"),
                      f"loaded={plugin.get('loaded')!r} error={plugin.get('error')!r}")

                status, body = call(client, "GET", "/api/plugins", headers=admin)
                plugins = (body or {}).get("plugins") or []
                listed = next((p for p in plugins if p.get("slug") == SLUG), {})
                check("清单里能看到刚装的插件",
                      listed.get("slug") == SLUG and listed.get("loaded") is True,
                      f"plugins={[p.get('slug') for p in plugins]}")
                check("插件真的落盘了（目录里有 metadata.json 和 __init__.py）",
                      (plugin_dir / SLUG / "metadata.json").is_file()
                      and (plugin_dir / SLUG / "__init__.py").is_file(),
                      f"{plugin_dir / SLUG}")

                # ------------------------------------------------ 面板 / 菜单
                print("\n== 3. 插件贡献的面板和菜单项 ==")
                panels = (body or {}).get("panels") or []
                panel = next((p for p in panels if p.get("id") == "demo_panel"), {})
                check("面板进了清单（id / title / endpoint 都对）",
                      panel.get("id") == "demo_panel"
                      and panel.get("title") == "自检面板"
                      and panel.get("endpoint") == "/api/plugin/demo_plugin/ping",
                      f"panels={panels}")
                menus = (body or {}).get("menus") or []
                menu = next((m for m in menus if m.get("label") == "自检菜单项"), {})
                check("菜单项进了清单（menu / label / endpoint 都对）",
                      menu.get("menu") == "tools"
                      and menu.get("label") == "自检菜单项"
                      and menu.get("endpoint") == "/api/plugin/demo_plugin/ping",
                      f"menus={menus}")
                check("清单里报出了插件的动作和接口",
                      "hello" in (listed.get("actions") or [])
                      and "/api/plugin/demo_plugin/ping" in (listed.get("routes") or []),
                      f"actions={listed.get('actions')} routes={listed.get('routes')}")

                # ------------------------------------------------ 插件自己的接口 / 动作
                print("\n== 4. 插件注册的接口和动作 ==")
                status, body = call(client, "GET", "/api/plugin/demo_plugin/ping",
                                    headers=admin)
                check("插件注册的接口能调通（返回 pong=1）",
                      status == 200 and (body or {}).get("pong") == 1,
                      f"HTTP {status} {str(body)[:160]}")
                status, body = call(client, "POST", f"/api/plugins/{SLUG}/action",
                                    headers=admin,
                                    json={"action": "hello", "params": {"n": 3}})
                check("插件注册的动作能调通（params 传到了插件里）",
                      status == 200 and (body or {}).get("hello") == 3,
                      f"HTTP {status} {str(body)[:160]}")

                # ------------------------------------------------ 停用 / 启用
                print("\n== 5. 停用 / 启用 ==")
                status, body = call(client, "POST", f"/api/plugins/{SLUG}/disable",
                                    headers=admin)
                plugin = (body or {}).get("plugin") or {}
                check("停用成功：enabled=false、loaded=false",
                      status == 200 and (body or {}).get("ok") is True
                      and plugin.get("enabled") is False
                      and plugin.get("loaded") is False,
                      f"HTTP {status} {str(body)[:200]}")

                status, body = call(client, "GET", "/api/plugins", headers=admin)
                panels = (body or {}).get("panels") or []
                menus = (body or {}).get("menus") or []
                check("停用后面板从清单里消失",
                      not any(p.get("id") == "demo_panel" for p in panels),
                      f"panels={panels}")
                check("停用后菜单项从清单里消失",
                      not any(m.get("label") == "自检菜单项" for m in menus),
                      f"menus={menus}")

                ok, detail = handler_gone(client, admin, "/api/plugin/demo_plugin/ping", "pong")
                check("停用后插件自己的接口调不到了（路由要一并摘掉）", ok, detail)

                status, body = call(client, "POST", f"/api/plugins/{SLUG}/enable",
                                    headers=admin)
                plugin = (body or {}).get("plugin") or {}
                check("启用成功：enabled=true、loaded=true",
                      status == 200 and (body or {}).get("ok") is True
                      and plugin.get("enabled") is True
                      and plugin.get("loaded") is True,
                      f"HTTP {status} {str(body)[:200]}")

                status, body = call(client, "GET", "/api/plugins", headers=admin)
                panels = (body or {}).get("panels") or []
                check("启用后面板又回到清单里",
                      any(p.get("id") == "demo_panel" for p in panels),
                      f"panels={panels}")

                ok, detail = handler_alive(client, admin, "/api/plugin/demo_plugin/ping")
                check("启用后插件自己的接口又能调了（重新注册上了）", ok, detail)

                # ------------------------------------------------ 重载
                print("\n== 6. 重载 / 卸载 ==")
                status, body = call(client, "POST", f"/api/plugins/{SLUG}/reload",
                                    headers=admin)
                plugin = (body or {}).get("plugin") or {}
                check("重载成功：ok=true、loaded=true",
                      status == 200 and (body or {}).get("ok") is True
                      and plugin.get("loaded") is True,
                      f"HTTP {status} {str(body)[:200]}")
                ok, detail = handler_alive(client, admin, "/api/plugin/demo_plugin/ping")
                check("重载后插件自己的接口还能调（重载会重新注册）", ok, detail)

                status, body = call(client, "POST", f"/api/plugins/{SLUG}/uninstall",
                                    headers=admin)
                check("卸载成功：ok=true", status == 200 and (body or {}).get("ok") is True,
                      f"HTTP {status} {str(body)[:160]}")
                status, body = call(client, "GET", "/api/plugins", headers=admin)
                plugins = (body or {}).get("plugins") or []
                check("卸载后清单里没有它了，目录也删干净了",
                      not any(p.get("slug") == SLUG for p in plugins)
                      and not (plugin_dir / SLUG).exists(),
                      f"plugins={[p.get('slug') for p in plugins]}")
                ok, detail = handler_gone(client, admin, "/api/plugin/demo_plugin/ping", "pong")
                check("卸载后插件自己的接口也调不到了", ok, detail)

                # ------------------------------------------------ 权限
                print("\n== 7. 权限 ==")
                status, body = call(client, "POST", "/api/auth/register",
                                    json={"username": "pluginuser", "password": USER_PASSWORD})
                token = str((body or {}).get("token") or "")
                check("能注册一个普通用户（下面的权限断言才有意义）",
                      status == 201 and bool(token), f"HTTP {status}")
                user = {"Authorization": f"Bearer {token}"}

                status, body = install(client, user, data)
                check("普通用户装插件 -> 403", status == 403, f"HTTP {status} {str(body)[:120]}")
                status, body = call(client, "POST", f"/api/plugins/{SLUG}/enable",
                                    headers=user)
                check("普通用户启用插件 -> 403", status == 403, f"HTTP {status} {str(body)[:120]}")
                status, body = call(client, "GET", "/api/plugins", headers=user)
                check("普通用户看清单：能看，但 can_manage=false",
                      status == 200 and (body or {}).get("can_manage") is False,
                      f"HTTP {status} {str(body)[:160]}")

                # ------------------------------------------------ zip 安全
                print("\n== 8. zip 安全（zip 是不可信输入）==")
                # 路径穿越：两个位置都试 —— 跳出插件自己的目录、跳出插件目录
                evil = b"print('pwned')\n"
                data = make_zip({
                    f"{SLUG}/metadata.json": META_BYTES,
                    f"{SLUG}/__init__.py": PLUGIN_INIT_BYTES,
                    f"{SLUG}/../evil.py": evil,
                    "../evil.py": evil,
                })
                status, body = install(client, admin, data)
                msg = err_text(body)
                check("路径穿越的 zip 装不上（报错点明「穿越」）",
                      status >= 400 and "穿越" in msg,
                      f"HTTP {status} {msg[:160]}")

                # 绝对路径：unix 的 /etc/... 和 windows 的 C:\...
                data = make_zip({
                    "metadata.json": json.dumps({**META, "slug": "abs_plugin"}).encode("utf-8"),
                    "__init__.py": PLUGIN_INIT_BYTES,
                    "/etc/evil.py": evil,
                })
                status, body = install(client, admin, data)
                msg = err_text(body)
                check("绝对路径的 zip 装不上（报错点明「绝对路径」）",
                      status >= 400 and "绝对路径" in msg,
                      f"HTTP {status} {msg[:160]}")

                data = make_zip({
                    "metadata.json": json.dumps({**META, "slug": "drive_plugin"}).encode("utf-8"),
                    "__init__.py": PLUGIN_INIT_BYTES,
                    "C:\\evil.py": evil,
                })
                status, body = install(client, admin, data)
                msg = err_text(body)
                check("带盘符的绝对路径也装不上",
                      status >= 400 and "绝对路径" in msg,
                      f"HTTP {status} {msg[:160]}")

                # 符号链接
                data = make_zip_with_symlink(
                    {
                        f"{SLUG}/metadata.json": META_BYTES,
                        f"{SLUG}/__init__.py": PLUGIN_INIT_BYTES,
                    },
                    f"{SLUG}/link.py",
                    "../../evil.py",
                )
                status, body = install(client, admin, data)
                msg = err_text(body)
                check("带符号链接的 zip 装不上（报错点明「符号链接」）",
                      status >= 400 and "符号链接" in msg,
                      f"HTTP {status} {msg[:160]}")

                # 没有 metadata.json
                data = make_zip({f"{SLUG}/__init__.py": PLUGIN_INIT_BYTES})
                status, body = install(client, admin, data)
                msg = err_text(body)
                check("没有 metadata.json 的 zip 装不上",
                      status >= 400 and "metadata.json" in msg,
                      f"HTTP {status} {msg[:160]}")

                # 根本不是 zip
                status, body = install(client, admin, b"this is not a zip at all")
                msg = err_text(body)
                check("坏 zip 装不上", status >= 400 and "zip" in msg.lower(),
                      f"HTTP {status} {msg[:160]}")

                # 装完之后：插件目录里不能有越界文件
                check("失败的安装没在插件目录里写出越界文件（也没有半个插件目录）",
                      not find_files(plugin_dir, "evil.py")
                      and not (plugin_dir / SLUG).exists(),
                      f"evil.py={find_files(plugin_dir, 'evil.py')} "
                      f"{SLUG}={(plugin_dir / SLUG).exists()}")
                check("失败的安装没在插件目录外面写出文件",
                      not (plugin_dir.parent / "evil.py").exists(),
                      f"{plugin_dir.parent / 'evil.py'}")

                # ------------------------------------------------ 坏插件不许拖垮服务
                print("\n== 9. 坏插件不许拖垮服务 ==")
                data = make_zip({
                    "bad_plugin/metadata.json": json.dumps(
                        {**META, "name": "坏插件", "version": "0.0.1"},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    "bad_plugin/__init__.py": BROKEN_INIT_BYTES,
                })
                status, body = install(client, admin, data)
                plugin = (body or {}).get("plugin") or {}
                check("装一个 import 就抛异常的插件：loaded=false、error 里说清原因",
                      plugin.get("loaded") is False
                      and "RuntimeError" in str(plugin.get("error") or ""),
                      f"HTTP {status} loaded={plugin.get('loaded')!r} "
                      f"error={plugin.get('error')!r}")
                ok, detail = handler_gone(client, admin, "/api/plugin/bad_plugin/ping",
                                           "bad_pong")
                check("坏插件注册过的接口也调不到（加载失败要摘掉它）", ok, detail)
                status, body = call(client, "GET", "/api/plugins", headers=admin)
                check("坏插件没拖垮服务：清单照常拿得到",
                      status == 200
                      and any(p.get("slug") == "bad_plugin" for p in
                              ((body or {}).get("plugins") or [])),
                      f"HTTP {status} {str(body)[:160]}")
                status, body = call(client, "GET", "/healthz")
                check("坏插件没拖垮服务：/healthz 200", status == 200, f"HTTP {status}")

        check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
              checked == EXPECTED_CHECKS, str(checked))
    finally:
        # 临时插件目录用完就清：别在系统临时目录里堆垃圾
        shutil.rmtree(plugin_dir, ignore_errors=True)

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

    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    import os

    os._exit(code)
