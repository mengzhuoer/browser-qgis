# -*- coding: utf-8 -*-
"""管理员分区 + 内置共享超级账号 + 全员 Python 控制台：后端自检。

验的是这一轮新增的三件事：

1. **内置共享超级账号**（``config.super_admin_password``）
   —— 账号存在、角色是管理员、用配置里那组口令能登录；
   而且这条口令是**钉住**的：运行中被人改了，下次启动会被对齐回去
   （不然「所有用户都能用的那把万能钥匙」哪天就悄悄失效了）。
2. **管理员接口只对管理员开**（``/api/admin/*``）——
   普通用户一律 403，不是「前端藏起来」那种遮羞式鉴权。
3. **所有人都能用 Python 控制台**（``console_access=user``）——
   普通用户的 ``/api/console/state`` 是 allowed，并且真能执行代码。

顺带把「口令备忘同步更新」这条口径钉上：改过口令之后备忘里记的是新口令；
重启对齐回去之后，备忘里记的**又变回**当前有效的那个 ——
文件里躺着失效的旧口令，比没有备忘更糟（照着登一直 401）。

跑法：``run-qgis-python.bat tests/check_admin_section.py``
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from server_helper import TestServer, _free_port  # noqa: E402

SUPER_USER = "admin"
SUPER_PASS = "test-super-pass-2026"
NORMAL_USER = "normaluser"
NORMAL_PASS = "normalpass123"
CHANGED_PASS = "changedpass456"

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 43 = 配置 6 + 超级账号与管理员接口 16 + 普通用户被挡 8
#      + 全员可用控制台 7 + 重启钉回与备忘同步 6
EXPECTED_CHECKS = 43

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


def req(base: str, token: str, method: str, path: str, **kw):
    """发一个请求，返回 httpx.Response（**不**抛异常 —— 403 也是要断言的结果）。"""
    headers = dict(kw.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.request(method, base + path, headers=headers, timeout=60, **kw)


def login(base: str, username: str, password: str) -> tuple[str, dict]:
    r = req(base, "", "POST", "/api/auth/login",
            json={"username": username, "password": password})
    if r.status_code != 200:
        return "", {}
    data = r.json()
    return data.get("token", ""), data.get("user", {}) or {}


def note_text(db: Path) -> str:
    """口令备忘的内容（正式库旁边那份 / 临时库旁边那份）。"""
    from gisds.core.auth import password_note_path

    path = password_note_path(db)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# --------------------------------------------------------------------- 1
def section_config() -> None:
    print("== 1. 配置：共享超级账号 + 全员可用控制台 ==")
    from gisds.config import Config

    # **这里不读开发机的 config.json** ——
    # 那个文件带明文口令、已经移出版本库（模板是 config.example.json），
    # 断言「它配了口令」在任何一次干净检出里都必然失败。
    # 这里改验**机制**：配了就开、留空 / off 就不开；默认值一律是收紧的那一档。
    check("出厂默认不开共享超级账号",
          not Config().super_admin_enabled, Config().super_admin_password)
    check("配了口令就开",
          _super_enabled_with(SUPER_PASS), SUPER_PASS)
    check("超级账号口令写得够长（register 的下限 6 位）",
          len(Config(super_admin_password=SUPER_PASS).super_admin_password_resolved) >= 6,
          SUPER_PASS)
    check("写成 off 就算没配（临时服务靠这个关掉继承来的那把钥匙）",
          not _super_enabled_with("off"), "off")
    check("控制台出厂默认只有管理员能用（收紧的一档）",
          Config().console_access == "admin", Config().console_access)
    check("控制台可以配成全员可用（user）",
          Config(console_access="user").console_access == "user")

    p = PROJECT_ROOT / "config.json"
    if p.is_file():
        try:
            from gisds.config import load_config

            cfg = load_config(p)
            print(f"   （本机有 config.json：console_access={cfg.console_access}，"
                  f"共享账号={'开' if cfg.super_admin_enabled else '关'}）")
        except Exception as exc:
            check("本机 config.json 读得动", False, str(exc))
    else:
        print("   （本机没有 config.json —— 公开仓库的正常状态，用代码默认值起）")


def _super_enabled_with(value: str) -> bool:
    from gisds.config import Config

    cfg = Config()
    cfg.super_admin_password = value
    return cfg.super_admin_enabled


# --------------------------------------------------------------------- 2
def section_admin_api(base: str) -> tuple[str, str]:
    print("\n== 2. 超级账号 + 管理员接口 ==")
    r = req(base, "", "GET", "/api/auth/config")
    body = r.json() if r.status_code == 200 else {}
    shared = body.get("shared_account") or {}
    check("登录页配置里有共享账号", bool(shared), str(body)[:160])
    check("共享账号的用户名是 admin",
          shared.get("username") == SUPER_USER, str(shared.get("username")))
    check("共享账号的口令就是配置里那组",
          shared.get("password") == SUPER_PASS, str(shared.get("password")))
    check("共享账号带着风险说明（不能只给口令不讲代价）",
          bool(str(shared.get("note") or "").strip()), str(shared.get("note")))

    token, user = login(base, SUPER_USER, SUPER_PASS)
    check("用共享口令能登录", bool(token), "401/没拿到令牌")
    check("登录回来的角色是管理员", (user or {}).get("role") == "admin", str(user))
    if not token:
        return "", ""

    r = req(base, token, "GET", "/api/admin/users")
    users = (r.json().get("users") or []) if r.status_code == 200 else []
    check("管理员能列出全部账号", r.status_code == 200, f"HTTP {r.status_code}")
    check("列表里有 admin", any(u.get("username") == SUPER_USER for u in users),
          str([u.get("username") for u in users]))
    admin_row = next((u for u in users if u.get("username") == SUPER_USER), {})
    check("admin 在列表里的角色是 admin", admin_row.get("role") == "admin",
          str(admin_row.get("role")))

    for path in ("/api/admin/spaces", "/api/admin/audit", "/api/admin/config"):
        r = req(base, token, "GET", path)
        check(f"管理员能读 {path}", r.status_code == 200, f"HTTP {r.status_code}")

    r = req(base, token, "GET", "/api/admin/config")
    cfg = r.json() if r.status_code == 200 else {}
    check("服务配置里标出「共享超级账号已启用」",
          cfg.get("super_admin_enabled") is True, str(cfg.get("super_admin_enabled")))
    check("服务配置里标出控制台是全员可用",
          cfg.get("console_access") == "user", str(cfg.get("console_access")))

    r = req(base, token, "POST", "/api/admin/users",
            json={"username": NORMAL_USER, "password": NORMAL_PASS,
                  "role": "user", "display_name": "普通用户"})
    check("管理员能新建账号", r.status_code == 201, f"HTTP {r.status_code}")
    r = req(base, token, "GET", "/api/admin/users")
    users = (r.json().get("users") or []) if r.status_code == 200 else []
    check("新建的账号出现在列表里",
          any(u.get("username") == NORMAL_USER for u in users),
          str([u.get("username") for u in users]))
    return token, ""


# --------------------------------------------------------------------- 3
def section_normal_user(base: str) -> str:
    print("\n== 3. 普通用户：管理员接口一律挡住 ==")
    token, _user = login(base, NORMAL_USER, NORMAL_PASS)
    check("普通用户能登录", bool(token), "401/没拿到令牌")
    if not token:
        return ""

    for path in ("/api/admin/users", "/api/admin/spaces",
                 "/api/admin/audit", "/api/admin/config"):
        r = req(base, token, "GET", path)
        check(f"普通用户读 {path} 被拒（403）", r.status_code == 403,
              f"HTTP {r.status_code}")

    r = req(base, token, "POST", "/api/admin/users",
            json={"username": "hacker01", "password": "hackpass123"})
    check("普通用户建账号被拒（403）", r.status_code == 403, f"HTTP {r.status_code}")

    r = req(base, token, "GET", "/api/admin/users")
    users = (r.json().get("users") or []) if r.status_code == 200 else []
    victim = next((u for u in users if u.get("username") == NORMAL_USER), {})
    vid = victim.get("user_id") or "nope"
    r = req(base, token, "PATCH", f"/api/admin/users/{vid}",
            json={"role": "admin"})
    check("普通用户改别人角色被拒（403）", r.status_code == 403, f"HTTP {r.status_code}")
    r = req(base, token, "DELETE", f"/api/admin/users/{vid}")
    check("普通用户删账号被拒（403）", r.status_code == 403, f"HTTP {r.status_code}")
    return token


# --------------------------------------------------------------------- 4
def section_console(base: str, normal_token: str, admin_token: str) -> None:
    print("\n== 4. Python 控制台：所有登录用户都能用 ==")
    r = req(base, normal_token, "GET", "/api/console/state")
    st = r.json() if r.status_code == 200 else {}
    check("普通用户拿到控制台状态", r.status_code == 200, f"HTTP {r.status_code}")
    check("普通用户的控制台是允许的", st.get("allowed") is True, str(st))
    check("控制台开放模式是 user", st.get("access") == "user", str(st.get("access")))

    r = req(base, normal_token, "POST", "/api/console/exec",
            json={"code": "print('hello-from-normal-user'); 6*7"})
    out = r.json() if r.status_code == 200 else {}
    check("普通用户真能执行 Python", r.status_code == 200, f"HTTP {r.status_code}")
    check("普通用户执行结果正确",
          "42" in str(out.get("result") or ""), str(out.get("result"))[:80])
    check("普通用户的输出能看到",
          "hello-from-normal-user" in str(out.get("stdout") or ""),
          str(out.get("stdout"))[:80])

    r = req(base, admin_token, "POST", "/api/console/exec", json={"code": "1+1"})
    out = r.json() if r.status_code == 200 else {}
    check("管理员当然也能执行", r.status_code == 200 and "2" in str(out.get("result") or ""),
          str(out)[:120])


# --------------------------------------------------------------------- 5
def section_pin_back(tmp: Path) -> None:
    print("\n== 5. 重启后：共享口令被钉回去，备忘也跟着同步 ==")
    db = tmp / "gisds.db"
    env = {"GISDS_SUPER_ADMIN_PASSWORD": SUPER_PASS}

    with TestServer(port=_free_port(8901), require_login=True, data_root=tmp,
                    extra_env=env) as server:
        token, _ = login(server.base, SUPER_USER, SUPER_PASS)
        r = req(server.base, token, "GET", "/api/admin/users")
        users = (r.json().get("users") or []) if r.status_code == 200 else []
        row = next((u for u in users if u.get("username") == SUPER_USER), {})
        uid = row.get("user_id") or ""
        r = req(server.base, token, "PATCH", f"/api/admin/users/{uid}",
                json={"password": CHANGED_PASS})
        check("改口令成功（先把那把钥匙拧掉）", r.status_code == 200, f"HTTP {r.status_code}")
        text = note_text(db)
        check("改完之后备忘里记的是新口令",
              CHANGED_PASS in text, text.replace("\n", " | ")[:100])

    # ---- 重启：口令应当被配置对齐回去
    with TestServer(port=_free_port(8902), require_login=True, data_root=tmp,
                    extra_env=env) as server:
        token, _ = login(server.base, SUPER_USER, SUPER_PASS)
        check("重启后共享口令又能登了（被钉回配置里的那组）", bool(token), "401")
        tok2, _ = login(server.base, SUPER_USER, CHANGED_PASS)
        check("重启后改掉的那组口令登不上了（钉回生效）", not tok2, "还能登")
        text = note_text(db)
        check("重启后备忘里记的是当前有效的共享口令",
              SUPER_PASS in text, text.replace("\n", " | ")[:100])
        check("重启后备忘里不再留着失效的旧口令",
              CHANGED_PASS not in text, text.replace("\n", " | ")[:100])


def main() -> int:
    section_config()

    tmp = Path(tempfile.mkdtemp(prefix="gisds_adminsect_"))
    env = {"GISDS_SUPER_ADMIN_PASSWORD": SUPER_PASS}
    with TestServer(port=_free_port(8900), require_login=True, data_root=tmp,
                    extra_env=env) as server:
        base = server.base
        admin_token, _ = section_admin_api(base)
        normal_token = section_normal_user(base) if admin_token else ""
        if admin_token and normal_token:
            section_console(base, normal_token, admin_token)
        else:
            print("（前面的步骤没过，控制台那段跳过 —— 但断言总数会因此对不上，钉子会红）")

    section_pin_back(tmp)

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
    import os as _os

    _os._exit(code)
