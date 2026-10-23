# -*- coding: utf-8 -*-
"""Python 控制台自检。

控制台 = 在数据服务器上执行 Python。三件事必须钉住：

1. **能干活**：管理员执行代码，stdout 里有输出，末行表达式像 REPL 一样回显，
   出错时带**回溯**。
2. **隔离**：每个用户一份命名空间 —— A 定义的变量不能漏到 B 那儿。
3. **别把服务搞死**：``os._exit()`` / ``os.abort()`` / ``os.exec*()`` /
   ``exit()`` / ``sys.exit()`` 都被护栏挡住，**而且服务必须还活着**。

权限有几种配置，各起一个临时服务分别验（同一个服务只能有一种配置）：

* ``admin``（默认）：只有管理员能用，普通用户 403
* ``user``：所有登录用户都能用 —— 命名空间隔离在这里验（两个普通用户）
* ``off``：整个关掉，管理员也 403
* 免登录模式（``require_login=False``）：匿名访客一律不许跑代码

跑法：``run-qgis-python.bat tests/check_console.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from server_helper import TestServer, _free_port  # noqa: E402

#: 服务级密钥 —— 带这个头的请求按管理员算（见 api/deps.py 的 _token_from）
API_KEY = "test-console-key"
ADMIN_PASSWORD = "adminpass123"
USER_PASSWORD = "pass12345"

fails: list[str] = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 钉死它：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
EXPECTED_CHECKS = 31


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def call(client, method, path, **kw):
    """发一个请求，返回 (状态码, 响应体)。

    服务要是已经没了，这里**干净地失败**（状态码 None），而不是抛异常把整个
    脚本带崩 —— 「护栏没挡住、服务被打死了」本身就是一条要报的断言。
    """
    try:
        r = client.request(method, path, **kw)
    except Exception as exc:
        return None, {"_error": f"请求失败（服务可能已经没了）：{type(exc).__name__}: {exc}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_text": r.text}


def run_code(client, headers, code):
    """在控制台里执行一段代码。"""
    return call(client, "POST", "/api/console/exec", headers=headers, json={"code": code})


def _err(body) -> str:
    body = body if isinstance(body, dict) else {}
    return str(body.get("error") or "")


def _stdout(body) -> str:
    body = body if isinstance(body, dict) else {}
    return str(body.get("stdout") or "")


# --------------------------------------------------------------------- 管理员模式

def check_admin_mode() -> None:
    """console_access=admin：管理员能干活，普通用户吃闭门羹。"""
    print("\n== 1. 管理员模式（console_access=admin）==")
    with TestServer(
        # 端口动态挑空闲的：写死的话两个会话并行跑，后一个的健康检查会连到
        # 前一个的服务上（表现是「莫名 401 / 页面跳登录页」）。
        port=_free_port(8893),
        require_login=True,
        extra_env={
            "GISDS_API_KEY": API_KEY,
            "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "GISDS_ALLOW_REGISTRATION": "1",
            "GISDS_CONSOLE_ACCESS": "admin",
            "GISDS_CONSOLE_TIMEOUT": "20",
        },
    ) as server:
        with httpx.Client(base_url=server.base, timeout=60.0) as client:
            admin = {"X-API-Key": API_KEY}

            # ---- 状态
            status, body = call(client, "GET", "/api/console/state", headers=admin)
            check(
                "管理员的状态：allowed=true、access=admin",
                status == 200
                and (body or {}).get("allowed") is True
                and (body or {}).get("access") == "admin",
                f"HTTP {status} {str(body)[:160]}",
            )

            # ---- 执行代码
            status, body = run_code(client, admin, 'print("hi")')
            check("执行 print('hi') 没有报错", status == 200 and _err(body) == "",
                  f"HTTP {status} error={_err(body)[:120]}")
            check("stdout 里有 hi", "hi" in _stdout(body), f"stdout={_stdout(body)[:120]!r}")

            status, body = run_code(client, admin, "1+1")
            check("末行表达式会回显（1+1 -> 2）",
                  status == 200 and (body or {}).get("result") == "2",
                  f"result={(body or {}).get('result')!r}")

            status, body = run_code(client, admin, "1/0")
            check("1/0 报错并点明 ZeroDivisionError",
                  status == 200 and "ZeroDivisionError" in _err(body),
                  f"error={_err(body)[:160]}")
            check("1/0 带得上回溯",
                  bool(str((body or {}).get("traceback") or "").strip()),
                  f"traceback={str((body or {}).get('traceback') or '')[:120]!r}")

            # ---- 命名空间是持久的
            run_code(client, admin, "x = 42")
            status, body = run_code(client, admin, "print(x)")
            check("命名空间是持久的：print(x) 打出 42", "42" in _stdout(body),
                  f"stdout={_stdout(body)[:120]!r} error={_err(body)[:120]}")

            status, body = call(client, "GET", "/api/console/state", headers=admin)
            variables = {v.get("name"): v for v in ((body or {}).get("variables") or [])}
            check("state 的 variables 里有 x，且值是 42",
                  str((variables.get("x") or {}).get("value")) == "42",
                  f"x={(variables.get('x') or {}).get('value')!r}")

            # ---- 命名空间按人隔离
            # 同一个 admin 账号，用「服务密钥」和「登录令牌」两种身份进来 ——
            # 这是两个不同的 user_id，命名空间必须是两份。
            status, body = call(client, "POST", "/api/auth/login",
                                json={"username": "admin", "password": ADMIN_PASSWORD})
            token = str((body or {}).get("token") or "")
            check("能用 admin 账号登录（拿到第二个管理员身份）",
                  status == 200 and bool(token), f"HTTP {status}")
            admin2 = {"Authorization": f"Bearer {token}"}
            status, body = run_code(client, admin2, "print(x)")
            check("命名空间按人隔离：另一个身份 print(x) 报 NameError",
                  "NameError" in _err(body), f"error={_err(body)[:160]}")

            # ---- reset
            status, body = call(client, "POST", "/api/console/reset", headers=admin)
            check("reset 返回 ok=true", status == 200 and (body or {}).get("ok") is True,
                  f"HTTP {status} {str(body)[:120]}")
            status, body = run_code(client, admin, "print(x)")
            check("reset 之后 x 没了（print(x) 报 NameError）",
                  "NameError" in _err(body), f"error={_err(body)[:160]}")

            # ---- 权限：普通用户
            status, body = call(client, "POST", "/api/auth/register",
                                json={"username": "consoleuser", "password": USER_PASSWORD})
            user_token = str((body or {}).get("token") or "")
            check("能注册一个普通用户（下面的权限断言才有意义）",
                  status == 201 and bool(user_token), f"HTTP {status}")
            user = {"Authorization": f"Bearer {user_token}"}

            status, body = run_code(client, user, "print(1)")
            check("普通用户执行代码 -> 403", status == 403, f"HTTP {status} {str(body)[:120]}")
            status, body = call(client, "GET", "/api/console/state", headers=user)
            check("普通用户看状态：allowed=false",
                  status == 200 and (body or {}).get("allowed") is False,
                  f"HTTP {status} {str(body)[:160]}")

            # ---- 安全护栏
            # 这些代码**真的**能把服务进程干掉。断言要两件事一起成立：
            # 接口报错（护栏生效）**且**服务还活着（护栏没漏）。
            guards = [
                ("os._exit(0)", "import os; os._exit(0)", "AttributeError"),
                ("os.abort(0)", "import os; os.abort(0)", "AttributeError"),
                ("os.execv(...)", "import os; os.execv('cmd', ['cmd'])", "AttributeError"),
                ("exit()", "exit()", "RuntimeError"),
                ("sys.exit()", "import sys; sys.exit()", "退出"),
            ]
            for label, code, needle in guards:
                status, body = run_code(client, admin, code)
                check(f"护栏挡住 {label}",
                      status == 200 and needle in _err(body),
                      f"HTTP {status} error={_err(body)[:140]}")

            status, body = run_code(client, admin, "import os; print(hasattr(os, '_exit'))")
            check("控制台里的 os 是被拦过的那份（连 _exit 属性都没有）",
                  status == 200 and "False" in _stdout(body),
                  f"HTTP {status} stdout={_stdout(body)[:120]!r}")

            status, body = call(client, "GET", "/healthz")
            check("一轮护栏打下来服务还活着（/healthz 200）", status == 200, f"HTTP {status}")
            status, body = run_code(client, admin, "print('still alive')")
            check("服务还活着（还能正常执行代码）",
                  status == 200 and "still alive" in _stdout(body),
                  f"HTTP {status} stdout={_stdout(body)[:120]!r} error={_err(body)[:120]}")


# --------------------------------------------------------------------- 用户模式

def check_user_mode() -> None:
    """console_access=user：普通用户能用控制台，但**各用各的**命名空间。"""
    print("\n== 2. 用户模式（console_access=user）：命名空间按用户隔离 ==")
    with TestServer(
        port=_free_port(8894),
        require_login=True,
        extra_env={
            "GISDS_API_KEY": API_KEY,
            "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "GISDS_ALLOW_REGISTRATION": "1",
            "GISDS_CONSOLE_ACCESS": "user",
            "GISDS_CONSOLE_TIMEOUT": "20",
        },
    ) as server:
        with httpx.Client(base_url=server.base, timeout=60.0) as client:
            tokens = []
            for name in ("conuser_a", "conuser_b"):
                status, body = call(client, "POST", "/api/auth/register",
                                    json={"username": name, "password": USER_PASSWORD})
                tokens.append(str((body or {}).get("token") or ""))
            check("能注册两个普通用户（conuser_a / conuser_b）",
                  all(tokens) and status == 201, f"HTTP {status}")
            ha = {"Authorization": f"Bearer {tokens[0]}"}
            hb = {"Authorization": f"Bearer {tokens[1]}"}

            status, body = run_code(client, ha, "x = 42")
            check("u_a 执行 x = 42 成功", status == 200 and _err(body) == "",
                  f"HTTP {status} error={_err(body)[:120]}")

            status, body = run_code(client, hb, "print(x)")
            check("u_b 执行 print(x) 报 NameError（u_a 的变量不外泄）",
                  status == 200 and "NameError" in _err(body),
                  f"HTTP {status} error={_err(body)[:160]}")

            status, body = call(client, "GET", "/api/console/state", headers=hb)
            names = [v.get("name") for v in ((body or {}).get("variables") or [])]
            check("u_b 的变量清单里没有 x", "x" not in names, f"variables={names}")


# --------------------------------------------------------------------- 关掉

def check_off_mode() -> None:
    """console_access=off：整个控制台关掉，管理员也不行。"""
    print("\n== 3. 控制台关掉（console_access=off）==")
    with TestServer(
        port=_free_port(8895),
        require_login=True,
        extra_env={
            "GISDS_API_KEY": API_KEY,
            "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "GISDS_CONSOLE_ACCESS": "off",
            "GISDS_CONSOLE_TIMEOUT": "20",
        },
    ) as server:
        with httpx.Client(base_url=server.base, timeout=60.0) as client:
            admin = {"X-API-Key": API_KEY}
            status, body = run_code(client, admin, "print(1)")
            check("控制台关掉后管理员执行代码 -> 403", status == 403,
                  f"HTTP {status} {str(body)[:120]}")
            status, body = call(client, "GET", "/api/console/state", headers=admin)
            check("控制台关掉后状态：allowed=false、access=off",
                  status == 200
                  and (body or {}).get("allowed") is False
                  and (body or {}).get("access") == "off",
                  f"HTTP {status} {str(body)[:160]}")


# --------------------------------------------------------------------- 免登录

def check_anonymous_mode() -> None:
    """免登录模式（require_login=False）：匿名访客不许在服务器上跑代码。

    免登录 = 「能访问网页」就等于「能在数据服务器上执行任意代码」，
    这种模式下一律要拒。
    """
    print("\n== 4. 免登录模式：匿名访客不许用控制台 ==")
    with TestServer(
        port=_free_port(8897),
        require_login=False,
        extra_env={
            "GISDS_API_KEY": API_KEY,
            "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "GISDS_CONSOLE_ACCESS": "admin",
        },
    ) as server:
        with httpx.Client(base_url=server.base, timeout=60.0) as client:
            status, body = run_code(client, {}, "print(1)")
            check("免登录模式下匿名访客执行代码 -> 403", status == 403,
                  f"HTTP {status} {str(body)[:120]}")
            status, body = call(client, "GET", "/api/console/state")
            check("免登录模式下状态：allowed=false",
                  status == 200 and (body or {}).get("allowed") is False,
                  f"HTTP {status} {str(body)[:160]}")


def main() -> int:
    check_admin_mode()
    check_user_mode()
    check_off_mode()
    check_anonymous_mode()

    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
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

    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    import os

    os._exit(code)
