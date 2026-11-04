"""免口令模式（passwordless）自检。

覆盖：
- 开关能被前端读到（登录页据此隐藏密码框）
- 只要一个用户名就能进；账号不存在当场建
- 同名即同人：同一个用户名在任何设备上是同一个空间
- 不同用户名 = 不同空间，数据互相看不见
- 管理员账号不能被免口令顶替（仍然走密码登录）
- 免口令模式下不用注册
- 免口令模式下 Python 控制台对普通账号可用（console_access=user）
- 关掉开关后，空密码登录照旧被拒（回归）

独立运行::

    run-qgis-python.bat tests/check_passwordless.py
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
_TMP = Path(tempfile.mkdtemp(prefix="gisds_open_"))
os.environ["GISDS_DATA_ROOT"] = str(_TMP / "data")
os.environ["GISDS_SHARED_ROOT"] = str(_TMP / "data" / "shared")
os.environ["GISDS_USERS_ROOT"] = str(_TMP / "data" / "users")
os.environ["GISDS_DB_PATH"] = str(_TMP / "data" / "gisds.db")
os.environ["GISDS_REQUIRE_LOGIN"] = "1"
os.environ["GISDS_PASSWORDLESS_LOGIN"] = "1"
os.environ["GISDS_ALLOW_REGISTRATION"] = "1"
os.environ["GISDS_CONSOLE_ACCESS"] = "user"

from fastapi.testclient import TestClient  # noqa: E402

from gisds.api.app import create_app  # noqa: E402
from gisds.api.deps import get_config  # noqa: E402

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。全部无条件执行。
EXPECTED_CHECKS = 17


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


GEOJSON = (
    '{"type":"FeatureCollection","features":[{"type":"Feature",'
    '"properties":{"n":1},"geometry":{"type":"Point","coordinates":[112.48,39.685]}}]}'
)


def main() -> int:
    print("== 免口令模式 ==")
    app = create_app()
    with TestClient(app) as client:
        # ---- 开关能被前端读到
        r = client.get("/api/auth/config")
        check("登录页能读到免口令开关",
              r.status_code == 200 and r.json().get("passwordless_login") is True,
              r.text[:200])
        r = client.get("/api/meta")
        check("元信息里也带上了免口令开关",
              r.status_code == 200 and r.json()["config"].get("passwordless_login") is True,
              r.text[:200])

        # ---- 只要用户名就能进
        r = client.post("/api/auth/claim", json={"username": "dora"})
        check("凭用户名直接进入", r.status_code == 200, r.text[:200])
        dora = r.json()
        dora_headers = {"Authorization": f"Bearer {dora['token']}"}
        dora_id = dora["user"]["user_id"]

        r = client.post("/api/auth/claim", json={"username": "dora"})
        check("同名再进还是同一个账号（同名即同人）",
              r.status_code == 200 and r.json()["user"]["user_id"] == dora_id,
              r.text[:200])

        r = client.post("/api/auth/claim", json={"username": "evan"})
        check("换个用户名是另一个账号",
              r.status_code == 200 and r.json()["user"]["user_id"] != dora_id,
              r.text[:200])
        evan_headers = {"Authorization": f"Bearer {r.json()['token']}"}

        # ---- login 接口也接受空密码
        r = client.post("/api/auth/login", json={"username": "dora"})
        check("登录接口空密码也能进（免口令）",
              r.status_code == 200 and r.json()["user"]["user_id"] == dora_id,
              r.text[:200])

        # ---- 管理员账号不能被免口令顶替
        r = client.post("/api/auth/claim", json={"username": "admin"})
        check("管理员账号不能被免口令顶替", r.status_code == 403, str(r.status_code))

        # ---- 用户名太短：给中文提示
        r = client.post("/api/auth/claim", json={"username": "li"})
        check("用户名太短时给中文提示",
              r.status_code == 400 and "至少 3 个字符" in r.text,
              f"{r.status_code} {r.text[:160]}")

        # ---- 不用注册
        r = client.post("/api/auth/register",
                        json={"username": "frank", "password": "frank123"})
        check("免口令模式下不用注册", r.status_code == 400, str(r.status_code))

        # ---- 空间隔离
        r = client.post("/api/uploads?scope=me",
                        files={"files": ("dora_pt.geojson", GEOJSON.encode(), "application/geo+json")},
                        headers=dora_headers)
        check("免口令用户能上传到自己的空间", r.status_code == 200, r.text[:200])

        r = client.get("/api/layers", headers=dora_headers)
        check("上传的数据自己看得见",
              r.status_code == 200 and any("dora_pt" in x["ref"] for x in r.json()["layers"]),
              r.text[:300])

        r = client.get("/api/layers", headers=evan_headers)
        check("别人的数据看不见（各自独立空间）",
              r.status_code == 200 and not any("dora_pt" in x["ref"] for x in r.json()["layers"]),
              r.text[:300])

        r = client.get("/api/uploads", headers=evan_headers)
        check("上传列表也按人隔离",
              r.status_code == 200 and not any("dora_pt" in x["dataset_id"] for x in r.json()["uploads"]),
              r.text[:300])

        r = client.get("/api/auth/me", headers=dora_headers)
        check("免口令用户有自己的个人空间",
              r.status_code == 200 and any(s["scope"] != "shared" for s in r.json()["scopes"]),
              r.text[:300])

        # ---- 控制台（console_access=user）
        r = client.get("/api/console/state", headers=dora_headers)
        check("免口令模式下普通账号能用 Python 控制台",
              r.status_code == 200 and r.json().get("allowed") is True,
              r.text[:200])

        # ---- 回归：关掉开关后，空密码登录要被拒
        cfg = get_config()
        cfg.passwordless_login = False
        try:
            r = client.post("/api/auth/login", json={"username": "dora"})
            check("关掉开关后空密码登录被拒（回归）", r.status_code == 401, str(r.status_code))
            r = client.post("/api/auth/claim", json={"username": "dora"})
            check("关掉开关后免口令入口被拒（回归）", r.status_code == 403, str(r.status_code))
        finally:
            cfg.passwordless_login = True

    print()
    print(f"== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    check(f"断言总数为 {EXPECTED_CHECKS}", _PASSED == EXPECTED_CHECKS,
          f"实际 {_PASSED}（断言被跳过会让总数对不上）")
    shutil.rmtree(_TMP, ignore_errors=True)
    return 1 if _FAILED else 0


if __name__ == "__main__":
    code = main()
    import os as _os

    # os._exit 不刷缓冲，stdout 里的 PASS/FAIL 会丢 —— 先手动刷
    sys.stdout.flush()
    sys.stderr.flush()
    _os._exit(code)
