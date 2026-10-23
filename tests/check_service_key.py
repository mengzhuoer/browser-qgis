# -*- coding: utf-8 -*-
"""验证「服务密钥登录」：多机部署下忘了 admin 密码时的远程救砖通道。

这条通道要证明的是**救砖真的通了**，不是「登录成功」就算数：
服务密钥 → 管理员身份 → **重置 admin 口令** → 用新口令正常登录。
只断言「能拉用户列表」证明不了最后那一步 —— 而那恰恰是远程进不来的真正原因。

跑法：``run-qgis-python.bat tests/check_service_key.py``
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from server_helper import TestServer, _free_port  # noqa: E402

fails: list[str] = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 9 = 错误密钥 2 + 正确密钥 3 + 拉用户列表 1 + 找得到 admin 1
#            + 重置口令 1 + 新口令能登录 1。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 9


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def login_raw(base: str, username: str, password: str):
    """绕开 api_json 直连登录接口。

    要证明的是「新口令就是个能用的口令」—— 所以**不带任何 Authorization 头**，
    免得变成「靠服务密钥令牌混过去的」，那证明不了口令本身有效。
    """
    req = urllib.request.Request(
        base + "/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def main() -> int:
    from gisds.launcher import api as lap

    key = "service-key-for-test"
    with TestServer(
        # 端口动态挑空闲的：写死的话两个会话并行跑，后一个的健康检查会连到
        # 前一个的服务上（表现是「莫名 401 / 页面跳登录页」）。
        port=_free_port(8871), require_login=True,
        extra_env={"GISDS_API_KEY": key, "GISDS_ADMIN_PASSWORD": "adminpass123"},
    ) as server:
        # 启动器连的是 api 模块里的 BASE，改那个才指到临时服务
        old_base = lap.BASE
        lap.BASE = server.base
        try:
            # 1) 错误的密钥应该被拒
            bad = lap.login_with_service_key("wrong-key", log=None)
            check("错误密钥被拒", bad is None, str(bad))
            check("错误密钥后令牌被清掉", lap.current_user() is None)

            # 2) 正确的密钥应该拿到管理员身份
            user = lap.login_with_service_key(key, log=print)
            check("正确密钥登录成功", bool(user), str(user))
            # 下面两条**无条件执行**：条件不满足就让断言**失败**，而不是整段跳过
            # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
            check("拿到的是管理员", bool(user) and user.get("is_admin") is True, str(user))
            check("身份标成服务密钥", bool(user) and user.get("username") == "api-key",
                  str(user))

            # 3) 有管理员身份就能干管理员的事：拉用户列表
            try:
                users = lap.api_json("GET", "/api/admin/users")
                check("能拉用户列表", "users" in (users or {}), str(users)[:100])
            except Exception as exc:
                users = {}
                check("能拉用户列表", False, str(exc))

            # 4) 救砖的关键一步：**能不能重置口令**。
            #    「能拉列表」证明不了远程进得来 —— 远程进不来的真正原因就是
            #    忘了 admin 口令，所以必须证明「重置口令」这条路通。
            admin_uid = ""
            for u in (users or {}).get("users", []):
                if u.get("username") == "admin":
                    admin_uid = u.get("user_id") or u.get("id") or ""
                    break
            check("用户列表里找得到 admin", bool(admin_uid), str(admin_uid))
            if admin_uid:
                new_pw = "resetbypass456"
                try:
                    lap.api_json("PATCH", f"/api/admin/users/{admin_uid}",
                                 body={"password": new_pw})
                    check("能用服务密钥重置 admin 口令", True)
                except Exception as exc:
                    check("能用服务密钥重置 admin 口令", False, str(exc))
                status, data = login_raw(server.base, "admin", new_pw)
                check("重置后的新口令能直接登录",
                      status == 200 and bool((data or {}).get("token")), str(status))
            else:
                # 用户列表里没有 admin -> 下面两条**判失败**，而不是整段跳过
                # （跳过会让断言总数变少，钉子就看不出少了哪几条）
                check("能用服务密钥重置 admin 口令", False, "用户列表里没有 admin")
                check("重置后的新口令能直接登录", False, "用户列表里没有 admin")
        finally:
            lap.BASE = old_base

    # 断言总数必须钉死：少一条就说明有断言被条件跳过了。
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
