"""用户系统与空间隔离自检。

覆盖：
- 注册 / 登录 / 登出 / 当前用户
- 管理员建号、改密码、停用、改角色、删除
- 空间隔离：用户看不见别人的私有空间，但都能看共享空间
- 共享空间人人可写
- 未登录访问业务接口 -> 401
- 免登录模式（require_login=false）-> 只看得到共享空间

独立运行::

    run-qgis-python.bat tests/check_auth.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 用临时数据目录，别碰真实数据
_TMP = Path(tempfile.mkdtemp(prefix="gisds_auth_"))
os.environ["GISDS_DATA_ROOT"] = str(_TMP / "data")
os.environ["GISDS_SHARED_ROOT"] = str(_TMP / "data" / "shared")
os.environ["GISDS_USERS_ROOT"] = str(_TMP / "data" / "users")
os.environ["GISDS_DB_PATH"] = str(_TMP / "data" / "gisds.db")
os.environ["GISDS_REQUIRE_LOGIN"] = "1"
os.environ["GISDS_ALLOW_REGISTRATION"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from gisds.api.app import create_app  # noqa: E402
from gisds.core.auth import AuthService, hash_password, verify_password  # noqa: E402
from gisds.core.db import Database  # noqa: E402

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 53 = 密码与会话（纯单元）11 + HTTP 接口 13 + 空间隔离 13 + 管理员 14
#            + 免登录模式 2。
# 唯一带分支的是「重名注册被拒」那条：try/except 两个分支各跑**同一条**断言，
# 所以条数不变。**全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，
# 钉子当场变红。
EXPECTED_CHECKS = 53


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


# --------------------------------------------------------------------- 纯单元

def unit_tests() -> None:
    print("== 密码与会话（纯单元） ==")
    stored = hash_password("hello123")
    check("密码摘要不是明文", "hello123" not in stored)
    check("正确密码能校验通过", verify_password("hello123", stored))
    check("错误密码校验失败", not verify_password("hello124", stored))
    check("两个用户同密码摘要不同", hash_password("hello123") != hash_password("hello123"))

    db = Database(_TMP / "unit.db")
    auth = AuthService(db)
    user = auth.register("alice", "alice123")
    check("注册成功", user["username"] == "alice")
    check("默认角色是 user", user["role"] == "user", user["role"])
    try:
        auth.register("alice", "alice123")
        check("重名注册被拒", False)
    except Exception:
        check("重名注册被拒", True)

    token, _ = auth.login("alice", "alice123")
    check("登录拿到令牌", len(token) > 20)
    principal = auth.principal_from_token(token)
    check("令牌能换回身份", principal is not None and principal.username == "alice")
    check("错误密码登录失败", _login_fails(auth, "alice", "nope"))
    check("登出后令牌失效", auth.logout(token) and auth.principal_from_token(token) is None)


def _login_fails(auth: AuthService, username: str, password: str) -> bool:
    try:
        auth.login(username, password)
        return False
    except Exception:
        return True


# --------------------------------------------------------------------- HTTP

def http_tests() -> None:
    print("\n== HTTP 接口 ==")
    app = create_app()
    with TestClient(app) as client:
        # 首个管理员
        r = client.get("/api/admin/users")
        check("未登录不能管理用户", r.status_code in (401, 403), str(r.status_code))

        r = client.get("/api/datasets")
        check("未登录不能列数据集", r.status_code == 401, str(r.status_code))

        r = client.get("/healthz")
        check("健康检查公开可访问", r.status_code == 200)
        r = client.get("/api/meta")
        check("元信息公开可访问", r.status_code == 200)
        check("元信息里能看到是否要登录",
              r.json()["config"].get("require_login") is True)

        # 注册
        r = client.post("/api/auth/register",
                        json={"username": "alice", "password": "alice123"})
        check("可以自助注册", r.status_code == 201, r.text[:200])
        alice = r.json()
        alice_headers = {"Authorization": f"Bearer {alice['token']}"}

        r = client.post("/api/auth/register",
                        json={"username": "bob", "password": "bob12345"})
        check("可以注册第二个账号", r.status_code == 201, r.text[:200])
        bob_headers = {"Authorization": f"Bearer {r.json()['token']}"}

        r = client.get("/api/auth/me", headers=alice_headers)
        check("取当前用户", r.status_code == 200 and r.json()["principal"]["username"] == "alice")
        check("当前用户能看到两个空间",
              len(r.json()["scopes"]) == 2, r.text[:200])

        r = client.post("/api/auth/logout", headers=alice_headers)
        check("登出成功", r.status_code == 200)
        r = client.get("/api/auth/me", headers=alice_headers)
        check("登出后令牌失效", r.status_code == 401, str(r.status_code))

        # 重新登录
        r = client.post("/api/auth/login", json={"username": "alice", "password": "alice123"})
        check("登录成功", r.status_code == 200, r.text[:200])
        alice_headers = {"Authorization": f"Bearer {r.json()['token']}"}

        r = client.post("/api/auth/login", json={"username": "alice", "password": "bad"})
        check("密码不对登录失败", r.status_code == 401, str(r.status_code))

        # ---- 空间隔离
        print("\n== 空间隔离 ==")
        geojson = (
            '{"type":"FeatureCollection","features":[{"type":"Feature",'
            '"properties":{"n":1},"geometry":{"type":"Point","coordinates":[112.48,39.685]}}]}'
        )

        r = client.post("/api/uploads?scope=me",
                        files={"files": ("alice_pt.geojson", geojson.encode(), "application/geo+json")},
                        headers=alice_headers)
        check("上传到自己的空间", r.status_code == 200, r.text[:200])
        alice_ds = r.json()["results"][0]["dataset_id"]
        check("上传结果落在个人空间", r.json()["scope"].startswith("user:"), r.json().get("scope", ""))

        r = client.post("/api/uploads?scope=shared",
                        files={"files": ("shared_pt.geojson", geojson.encode(), "application/geo+json")},
                        headers=bob_headers)
        check("也能上传到共享空间", r.status_code == 200, r.text[:200])
        shared_ds = r.json()["results"][0]["dataset_id"]
        check("共享空间标识正确", r.json()["scope"] == "shared", r.json().get("scope", ""))

        r = client.get("/api/datasets", headers=alice_headers)
        ids = [d["id"] for d in r.json()["datasets"]]
        check("alice 看得见自己的数据集", alice_ds in ids, str(ids))
        check("alice 看得见共享空间的数据集", shared_ds in ids, str(ids))

        r = client.get("/api/datasets", headers=bob_headers)
        ids = [d["id"] for d in r.json()["datasets"]]
        check("bob 看不见 alice 的私有空间", alice_ds not in ids, str(ids))
        check("bob 看得见共享空间的数据集", shared_ds in ids, str(ids))

        r = client.get(f"/api/datasets/{alice_ds}", headers=bob_headers)
        check("bob 直接按 id 取也拿不到 alice 的数据", r.status_code == 404, str(r.status_code))

        r = client.get("/api/layers", headers=bob_headers)
        check("bob 的图层清单里没有 alice 的图层",
              not any(i["ref"].startswith(f"{alice_ds}:") for i in r.json()["layers"]))

        # bob 往共享空间写
        r = client.post("/api/uploads?scope=shared",
                        files={"files": ("bob_shared.geojson", geojson.encode(), "application/geo+json")},
                        headers=bob_headers)
        check("bob 也能写共享空间（人人可写）", r.status_code == 200, r.text[:200])

        # bob 删 alice 的数据 -> 403
        r = client.delete(f"/api/uploads/{alice_ds}", headers=bob_headers)
        check("bob 删不了 alice 的数据", r.status_code in (403, 404), str(r.status_code))

        # alice 自己能删
        r = client.delete(f"/api/uploads/{alice_ds}", headers=alice_headers)
        check("alice 能删自己的数据", r.status_code == 200, r.text[:200])

        # ---- 管理员
        print("\n== 管理员 ==")
        # 首个管理员是启动时自动建的
        r = client.get("/api/admin/users")
        check("未登录仍然不能列用户", r.status_code in (401, 403), str(r.status_code))

        db = Database(Path(os.environ["GISDS_DB_PATH"]))
        auth = AuthService(db)
        admin, password = auth.ensure_admin("boss", "boss12345")
        check("能建管理员", admin["role"] == "admin", admin.get("role", ""))
        r = client.post("/api/auth/login", json={"username": "boss", "password": "boss12345"})
        check("管理员能登录", r.status_code == 200, r.text[:200])
        admin_headers = {"Authorization": f"Bearer {r.json()['token']}"}

        r = client.get("/api/admin/users", headers=admin_headers)
        check("管理员能列用户", r.status_code == 200 and r.json()["count"] >= 3, r.text[:200])

        r = client.post("/api/admin/users", headers=admin_headers,
                        json={"username": "carol", "password": "carol123"})
        check("管理员能建号", r.status_code == 201, r.text[:200])
        carol_id = r.json()["user_id"]

        r = client.get("/api/datasets", headers=admin_headers)
        ids = [d["id"] for d in r.json()["datasets"]]
        check("管理员看得见所有人的空间", len(ids) >= 2, str(ids))

        r = client.patch(f"/api/admin/users/{carol_id}", headers=admin_headers,
                        json={"status": "disabled"})
        check("管理员能停用账号", r.status_code == 200 and r.json()["status"] == "disabled", r.text[:200])

        r = client.post("/api/auth/login", json={"username": "carol", "password": "carol123"})
        check("停用后不能登录", r.status_code == 403, str(r.status_code))

        r = client.patch(f"/api/admin/users/{carol_id}", headers=admin_headers,
                        json={"password": "newpass123"})
        check("管理员能改密码", r.status_code == 200, r.text[:200])
        r = client.patch(f"/api/admin/users/{carol_id}", headers=admin_headers,
                        json={"status": "active"})
        check("管理员能重新启用账号", r.status_code == 200, r.text[:200])
        r = client.post("/api/auth/login", json={"username": "carol", "password": "newpass123"})
        check("启用后新密码能登录", r.status_code == 200, r.text[:200])

        r = client.delete(f"/api/admin/users/{carol_id}", headers=admin_headers)
        check("管理员能删号", r.status_code == 200, r.text[:200])
        r = client.get("/api/admin/users", headers=admin_headers)
        check("删完之后列表里没有了",
              not any(u["user_id"] == carol_id for u in r.json()["users"]))

        # 普通用户不能碰管理接口
        r = client.get("/api/admin/users", headers=alice_headers)
        check("普通用户不能管理用户", r.status_code in (401, 403), str(r.status_code))

        # ---- 免登录模式
        print("\n== 免登录模式 ==")
        os.environ["GISDS_REQUIRE_LOGIN"] = "0"
        from gisds.api import deps as deps_mod

        deps_mod.set_config(None)  # type: ignore[arg-type]
        app2 = create_app()
        with TestClient(app2) as client2:
            r = client2.get("/api/datasets")
            check("免登录模式能列数据集", r.status_code == 200, r.text[:200])
            scopes = {d.get("scope") for d in r.json()["datasets"]}
            check("免登录模式只看得到共享空间", scopes <= {"shared"}, str(scopes))
        os.environ["GISDS_REQUIRE_LOGIN"] = "1"
        deps_mod.set_config(None)  # type: ignore[arg-type]


def main() -> int:
    try:
        unit_tests()
        http_tests()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    return 0 if _FAILED == 0 else 1


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
    os._exit(code)
