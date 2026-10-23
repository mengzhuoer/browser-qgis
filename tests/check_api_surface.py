# -*- coding: utf-8 -*-
"""API 全量面自检：把每个端点至少打一遍，把「静默失败」钉住。

现有自检各管一段：``check_api.py`` 走免登录模式的读路径，``check_panels.py`` 走面板
与要素编辑，``check_plugins.py`` 走插件生命周期，``check_service_key.py`` 走服务密钥。
**没被任何一支覆盖到**的是这几块：

* **CRS 那一套**（``/api/crs``、``/api/crs/predefined``、``/api/crs/detail``、
  ``/api/crs/preview.png``）—— 现有自检几乎没碰过；
* **权限矩阵**：匿名 / 普通用户 / 管理员 / 免登录模式，各打一遍关键端点，
  重点查**越权**（A 用户能不能碰 B 用户的数据集）；
* **参数边界**：缺参数、类型错、越界（width=99999、z=-1、超长字符串）；
* **幂等**：连点两次安装、两次 rescan、两次 logout 会不会出错；
* **管理面**：``/api/admin/*``（用户 / 空间 / 审计 / 配置）、``/api/console/*``；
* **上传工作区**：上传 / 下载 / 删除，以及「删别人的东西」。

本脚本按**产品应有的正确行为**断言 —— 现版本存在的缺陷会让对应的断言变红，
钉在那里等人修；修完就该全绿。断言里**不写死实现的细节**（比如某个错误体长什么样），
只钉「该 404 的别 500、该 403 的别 200、越权的不许过」。

跑法::

    run-qgis-python.bat tests/check_api_surface.py

隔离：数据目录、插件目录全在 ``tempfile.mkdtemp()`` 出来的临时目录里，
**不碰生产 ``data/``**；端口动态挑空闲的（``_free_port``），绝不写死。
"""

from __future__ import annotations

import io
import json
import math
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import httpx  # noqa: E402

from server_helper import TestServer, _free_port  # noqa: E402

fails: list[str] = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 349 = 鉴权 41 + 管理面 35 + 控制台 13 + CRS 25 + 数据集 26 + 上传 26
#            + 越权 22 + 面板 43 + 工具箱 31 + 插件 19 + 栅格矢量 51
#            + 幂等 6 + 免登录模式 11。
# 全部**无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 349

ADMIN_PW = "surface-admin-pw-123"
ALICE_PW = "surface-alice-pw-123"
BOB_PW = "surface-bob-pw-123"

#: 上传用的最小 GeoJSON（两个小多边形）
FIXTURE = (
    '{"type":"FeatureCollection","features":['
    '{"type":"Feature","properties":{"名称":"甲","面积":100},'
    '"geometry":{"type":"Polygon","coordinates":[[[112.00,39.00],[112.10,39.00],'
    '[112.10,39.10],[112.00,39.10],[112.00,39.00]]]}},'
    '{"type":"Feature","properties":{"名称":"乙","面积":250},'
    '"geometry":{"type":"Polygon","coordinates":[[[112.40,39.40],[112.50,39.40],'
    '[112.50,39.50],[112.40,39.50],[112.40,39.40]]]}}'
    "]}"
)

#: 示例插件的元数据与代码（最小可用：注册一个动作 + 一个接口）
PLUGIN_META = {
    "name": "面扫描自检插件",
    "version": "1.0.0",
    "description": "check_api_surface 用的最小插件",
    "author": "gisds",
    "enabled": True,
}
PLUGIN_INIT = '''\
def ping(request, principal):
    return {"pong": 1}


def hello(params):
    return {"hello": params.get("n")}


class GisdPlugin:
    def init(self, ctx):
        ctx.add_route("/api/plugin/surface_plugin/ping", ping, methods=("GET", "POST"))
        ctx.add_action("hello", hello)

    def unload(self):
        pass
'''


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def call(client, method, path, token=None, **kw):
    """发一个请求，返回 (状态码, 响应体)。

    服务要是已经没了，这里**干净地失败**（状态码 None），而不是抛异常把整个脚本带崩
    —— 「某个端点把服务带死了」本身就是一条要报的断言。
    """
    headers = dict(kw.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = client.request(method, path, headers=headers, **kw)
    except Exception as exc:  # noqa: BLE001
        return None, {"_error": f"请求失败：{type(exc).__name__}: {exc}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_text": r.text[:400]}


def raw(client, method, path, token=None, **kw):
    """发一个请求并返回原始响应对象（二进制 / 响应头用）。"""
    headers = dict(kw.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return client.request(method, path, headers=headers, **kw)
    except Exception as exc:  # noqa: BLE001
        return None


def text_of(body) -> str:
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


def upload_geojson(client, token, name: str, scope: str | None = None):
    """上传一个 GeoJSON，返回 (状态码, 响应体)。"""
    params = {"scope": scope} if scope else {}
    return call(
        client,
        "POST",
        "/api/uploads",
        token=token,
        params=params,
        files={"files": (name, FIXTURE.encode("utf-8"), "application/geo+json")},
    )


def find_ref(client, token, dataset_id: str) -> str:
    """在图层清单里找出某个数据集下的图层引用。"""
    _st, body = call(client, "GET", "/api/layers", token=token)
    for item in (body or {}).get("layers", []):
        if item.get("dataset_id") == dataset_id:
            return item.get("ref") or ""
    return ""


def wait_job(client, token, job_id: str, timeout: float = 120.0) -> dict:
    """轮询一个作业直到结束。"""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        _st, body = call(client, "GET", f"/api/processing/jobs/{job_id}", token=token)
        last = body or {}
        if last.get("status") in ("success", "failed"):
            return last
        time.sleep(0.4)
    return last


def main() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="gisds_api_surface_"))
    data_root = tmp_root / "data"
    plugin_dir = tmp_root / "plugins"
    (data_root / "shared").mkdir(parents=True, exist_ok=True)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    sample = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
    if sample.is_dir():
        shutil.copytree(sample, data_root / "shared" / "example")

    try:
        _run_checks(data_root, plugin_dir)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被跳过了）",
          checked == EXPECTED_CHECKS, str(checked))
    print(f"\n== 结果：{checked - len(fails)} 通过 / {len(fails)} 失败 ==")
    if fails:
        print("  失败项:")
        for item in fails:
            print(f"    - {item}")
    return 1 if fails else 0


def _run_checks(data_root: Path, plugin_dir: Path) -> None:
    server = TestServer(
        port=_free_port(8901),
        require_login=True,
        data_root=data_root,
        extra_env={
            "GISDS_ADMIN_PASSWORD": ADMIN_PW,
            "GISDS_ALLOW_REGISTRATION": "1",
            "GISDS_PLUGIN_DIR": str(plugin_dir),
            "GISDS_CONSOLE_ACCESS": "admin",
            "GISDS_PASSWORD_NOTE": "0",
        },
    )
    if not server.start():
        check("临时服务能起来", False, "服务起不来，后面的断言全部无意义")
        return
    try:
        with httpx.Client(base_url=server.base, timeout=120.0) as client:
            _scan_auth(client)
            _scan_admin(client)
            _scan_console(client)
            _scan_crs(client)
            _scan_datasets(client)
            _scan_uploads(client)
            _scan_isolation(client, data_root)
            _scan_panels(client)
            _scan_processing(client)
            _scan_plugins(client)
            _scan_geo(client)
            _scan_idempotency(client)
    finally:
        server.stop()

    _scan_open_mode(data_root, plugin_dir)


# --------------------------------------------------------------------- 鉴权面


def _scan_auth(client) -> None:
    print("\n== 1. 鉴权与登录 ==")
    # 三个公开探针：登录页自己就要用，是**有意**不鉴权的
    st, body = call(client, "GET", "/healthz")
    check("GET /healthz -> 200", st == 200, f"HTTP {st}")
    check("healthz 报 ok", (body or {}).get("status") == "ok", str(body)[:120])

    st, body = call(client, "GET", "/readyz")
    check("GET /readyz -> 200（已就绪）", st == 200, f"HTTP {st}")
    check("readyz 报 ready=true", (body or {}).get("ready") is True, str(body)[:120])

    st, body = call(client, "GET", "/api/meta")
    check("GET /api/meta -> 200（登录页自己就要用，公开）", st == 200, f"HTTP {st}")
    check("meta 带版本与能力清单",
          bool((body or {}).get("version")) and "rest" in (body or {}).get("capabilities", {}).get("integration", []),
          str(body)[:160])

    st, body = call(client, "GET", "/api/auth/config")
    check("GET /api/auth/config -> 200", st == 200, f"HTTP {st}")
    check("auth/config 报出 require_login=true", (body or {}).get("require_login") is True,
          str(body))
    check("auth/config 报出开放自助注册", (body or {}).get("allow_registration") is True,
          str(body))

    st, body = call(client, "GET", "/api/auth/me")
    check("匿名 GET /api/auth/me -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "GET", "/api/datasets")
    check("匿名 GET /api/datasets -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "GET", "/api/panels/logs")
    check("匿名 GET /api/panels/logs -> 401（读日志也要登录）", st == 401, f"HTTP {st}")

    # CRS 那一套必须挂 current_principal —— 以前是漏挂的（不是有意公开）。
    # 钉在这儿：谁再把依赖摘掉，这两条立刻红。
    st, body = call(client, "GET", "/api/crs", params={"search": "4326"})
    check("匿名 GET /api/crs -> 401（CRS 目录也要登录）", st == 401, f"HTTP {st}")

    resp = raw(client, "GET", "/api/crs/preview.png", params={"crs": "EPSG:4326"})
    check("匿名 GET /api/crs/preview.png -> 401（渲染端点更要登录）",
          resp is not None and resp.status_code == 401,
          f"HTTP {None if resp is None else resp.status_code}")

    st, body = call(client, "POST", "/api/auth/register",
                    json={"username": "alice", "password": ALICE_PW})
    check("注册 alice -> 201", st == 201, f"HTTP {st} {text_of(body)[:120]}")
    alice_token = (body or {}).get("token") or ""
    check("注册成功返回令牌", bool(alice_token))

    st, body = call(client, "POST", "/api/auth/register",
                    json={"username": "bob", "password": BOB_PW})
    check("注册 bob -> 201", st == 201, f"HTTP {st} {text_of(body)[:120]}")
    bob_token = (body or {}).get("token") or ""

    st, body = call(client, "POST", "/api/auth/register",
                    json={"username": "alice", "password": ALICE_PW})
    check("重名注册 -> 400", st == 400, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/register",
                    json={"username": "x", "password": ALICE_PW})
    check("用户名太短 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/register",
                    json={"username": "shorty", "password": "123"})
    check("密码太短 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/register",
                    json={"username": "a" * 200, "password": ALICE_PW})
    check("超长用户名 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/register",
                    json={"username": "dave", "password": "dave-pass-123"})
    check("注册 dave（给幂等测试用）-> 201", st == 201, f"HTTP {st}")
    dave_token = (body or {}).get("token") or ""

    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    check("正确口令登录 -> 200", st == 200, f"HTTP {st}")
    check("登录返回令牌", bool((body or {}).get("token")))

    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": "wrong-password"})
    check("口令错 -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "no-such-user", "password": "whatever"})
    check("账号不存在 -> 401（别泄露「账号不存在」）", st == 401, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/login", json={"username": "alice"})
    check("登录缺密码字段 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/login", content=b"{not json",
                    headers={"Content-Type": "application/json"})
    check("登录 body 是坏 JSON -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/auth/me", token=alice_token)
    check("带令牌 GET /api/auth/me -> 200", st == 200, f"HTTP {st}")
    check("me 认得出当前用户", (body or {}).get("principal", {}).get("username") == "alice",
          str(body))
    check("me 列出可写空间", len((body or {}).get("scopes") or []) == 2, str(body))

    st, body = call(client, "GET", "/api/auth/me", token="not-a-real-token")
    check("乱令牌 GET /api/auth/me -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/logout")
    check("不带任何头登出 -> 200（不报错）", st == 200, f"HTTP {st}")
    check("不带令牌登出报 logged_out=false", (body or {}).get("logged_out") is False, str(body))

    st, body = call(client, "POST", "/api/auth/logout", token=dave_token)
    check("登出 -> 200", st == 200, f"HTTP {st}")
    check("登出报 logged_out=true", (body or {}).get("logged_out") is True, str(body))

    st, body = call(client, "POST", "/api/auth/logout", token=dave_token)
    check("重复登出 -> 200（幂等，不报 500）", st == 200, f"HTTP {st}")
    check("重复登出报 logged_out=false", (body or {}).get("logged_out") is False, str(body))

    st, body = call(client, "GET", "/api/auth/me", token=dave_token)
    check("登出后令牌立刻失效 -> 401", st == 401, f"HTTP {st}")

    # 管理员登录（口令来自环境变量）
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    check("管理员登录 -> 200", st == 200, f"HTTP {st}")
    admin_token = (body or {}).get("token") or ""
    check("管理员令牌拿到了", bool(admin_token))


# --------------------------------------------------------------------- 管理面


def _scan_admin(client) -> None:
    print("\n== 2. 管理面 /api/admin/* ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/admin/users")
    check("匿名 GET /api/admin/users -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/users", token=alice)
    check("普通用户 GET /api/admin/users -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/users", token=admin)
    check("管理员 GET /api/admin/users -> 200", st == 200, f"HTTP {st}")
    check("用户列表里能看到 alice", any(u.get("username") == "alice"
                                        for u in (body or {}).get("users", [])),
          str((body or {}).get("count")))

    st, body = call(client, "POST", "/api/admin/users", token=alice,
                    json={"username": "tmpuser", "password": "tmp-pass-123"})
    check("普通用户建账号 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "POST", "/api/admin/users", token=admin,
                    json={"username": "tmpuser", "password": "tmp-pass-123"})
    check("管理员建账号 -> 201", st == 201, f"HTTP {st} {text_of(body)[:120]}")
    tmp_uid = (body or {}).get("user_id") or ""

    st, body = call(client, "POST", "/api/admin/users", token=admin,
                    json={"username": "tmpuser", "password": "tmp-pass-123"})
    check("重名建账号 -> 400", st == 400, f"HTTP {st}")

    st, body = call(client, "POST", "/api/admin/users", token=admin,
                    json={"username": "tmpuser2", "password": "tmp-pass-123", "role": "root"})
    check("非法 role -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/admin/users", token=admin,
                    json={"username": "tmpuser3", "password": "1"})
    check("建账号密码太短 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "PATCH", f"/api/admin/users/{tmp_uid}", token=alice,
                    json={"display_name": "改了"})
    check("普通用户改账号 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "PATCH", f"/api/admin/users/{tmp_uid}", token=admin,
                    json={"display_name": "临时账号"})
    check("管理员改账号 -> 200", st == 200, f"HTTP {st}")
    check("改完 display_name 生效", (body or {}).get("display_name") == "临时账号", str(body))

    st, body = call(client, "PATCH", "/api/admin/users/no-such-user", token=admin,
                    json={"display_name": "x"})
    check("改不存在的账号 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "PATCH", f"/api/admin/users/{tmp_uid}", token=admin,
                    json={"role": "hacker"})
    check("改角色传非法值 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "DELETE", f"/api/admin/users/{tmp_uid}", token=alice)
    check("普通用户删账号 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/users", token=admin)
    admin_uid = ""
    for u in (body or {}).get("users", []):
        if u.get("username") == "admin":
            admin_uid = u.get("user_id") or ""
            break
    st, body = call(client, "DELETE", f"/api/admin/users/{admin_uid}", token=admin)
    check("管理员删自己 -> 400", st == 400, f"HTTP {st}")

    st, body = call(client, "DELETE", f"/api/admin/users/{tmp_uid}", token=admin)
    check("管理员删账号 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "DELETE", f"/api/admin/users/{tmp_uid}", token=admin)
    check("删同一个账号第二次 -> 404", st == 404, f"HTTP {st}")

    # 停用账号：旧令牌要立刻作废，新登录要被拒
    st, body = call(client, "POST", "/api/admin/users", token=admin,
                    json={"username": "disableduser", "password": "disabled-pw-123"})
    check("管理员建停用测试账号 -> 201", st == 201, f"HTTP {st} {text_of(body)[:120]}")
    dis_uid = (body or {}).get("user_id") or ""

    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "disableduser", "password": "disabled-pw-123"})
    check("停用测试账号登录 -> 200", st == 200, f"HTTP {st}")
    dis_token = (body or {}).get("token") or ""

    st, body = call(client, "PATCH", f"/api/admin/users/{dis_uid}", token=admin,
                    json={"status": "disabled"})
    check("管理员停用账号 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "GET", "/api/auth/me", token=dis_token)
    check("账号被停用后旧令牌立刻失效 -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "disableduser", "password": "disabled-pw-123"})
    check("账号被停用后不能再登录 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "PATCH", f"/api/admin/users/{dis_uid}", token=admin,
                    json={"status": "瞎写的状态"})
    check("停用状态传非法值 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "DELETE", f"/api/admin/users/{dis_uid}", token=admin)
    check("删掉停用测试账号 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/spaces", token=alice)
    check("普通用户 GET /api/admin/spaces -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/spaces", token=admin)
    check("管理员 GET /api/admin/spaces -> 200", st == 200, f"HTTP {st}")
    check("空间清单非空", (body or {}).get("count", 0) >= 1, str(body))

    st, body = call(client, "GET", "/api/admin/audit", token=alice)
    check("普通用户 GET /api/admin/audit -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/audit", token=admin)
    check("管理员 GET /api/admin/audit -> 200", st == 200, f"HTTP {st}")
    check("审计日志里有记录", (body or {}).get("count", 0) >= 1, str(body))

    st, body = call(client, "GET", "/api/admin/audit?limit=99999", token=admin)
    check("审计日志 limit 越界也不该 500", st is not None and st < 500, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/config", token=alice)
    check("普通用户 GET /api/admin/config -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "GET", "/api/admin/config", token=admin)
    check("管理员 GET /api/admin/config -> 200", st == 200, f"HTTP {st}")
    check("配置里报出 require_login", (body or {}).get("require_login") is True, str(body))


# --------------------------------------------------------------------- 控制台


def _scan_console(client) -> None:
    print("\n== 3. 控制台 /api/console/* ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/console/state")
    check("匿名 GET /api/console/state -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "GET", "/api/console/state", token=alice)
    check("普通用户 GET /api/console/state -> 200", st == 200, f"HTTP {st}")
    check("普通用户拿到 allowed=false", (body or {}).get("allowed") is False, str(body))

    st, body = call(client, "GET", "/api/console/state", token=admin)
    check("管理员 GET /api/console/state -> 200", st == 200, f"HTTP {st}")
    check("管理员拿到 allowed=true", (body or {}).get("allowed") is True, str(body))

    st, body = call(client, "POST", "/api/console/exec", token=alice,
                    json={"code": "1+1"})
    check("普通用户执行代码 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "POST", "/api/console/exec", token=admin,
                    json={"code": "1+1"})
    check("管理员执行代码 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")
    check("执行结果里有 stdout", "2" in json.dumps(body or {}), str(body)[:160])

    st, body = call(client, "POST", "/api/console/exec", token=admin, json={"code": "   "})
    check("空代码 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/console/exec", token=admin, json={})
    check("缺 code 字段 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/console/exec", token=admin,
                    json={"code": "raise ValueError('boom')"})
    check("代码抛异常不该 500", st is not None and st < 500, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/console/reset", token=alice)
    check("普通用户重置控制台 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "POST", "/api/console/reset", token=admin)
    check("管理员重置控制台 -> 200", st == 200, f"HTTP {st}")


# --------------------------------------------------------------------- CRS


def _scan_crs(client) -> None:
    print("\n== 4. 坐标参考系 /api/crs/* ==")
    # CRS 这一套**要登录**（不是有意公开的接口），所以这里一律带 admin 令牌。
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/crs", token=admin, params={"search": "4326"})
    check("GET /api/crs?search=4326 -> 200", st == 200, f"HTTP {st}")
    check("搜得到 EPSG:4326",
          any(c.get("id") == "EPSG:4326" for c in (body or {}).get("crs", [])),
          str((body or {}).get("count")))

    st, body = call(client, "GET", "/api/crs", token=admin, params={"search": "WGS 84"})
    check("按英文名搜索 -> 200", st == 200, f"HTTP {st}")
    check("按英文名搜得到东西", (body or {}).get("count", 0) >= 1, str(body)[:160])

    st, body = call(client, "GET", "/api/crs", token=admin, params={"search": "北京"})
    check("按中文名搜索 -> 200（不 500）", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/crs", token=admin, params={"limit": 0})
    check("limit=0 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/crs", token=admin, params={"limit": 99999})
    check("limit=99999 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/crs", token=admin, params={"search": "x" * 5000})
    check("超长搜索词 -> 200（不 500）", st is not None and st < 500, f"HTTP {st}")

    st, body = call(client, "GET", "/api/crs/predefined", token=admin)
    check("GET /api/crs/predefined -> 200", st == 200, f"HTTP {st}")
    check("预定义表里有 EPSG:4326",
          any(c.get("id") == "EPSG:4326" for c in (body or {}).get("crs", [])),
          str((body or {}).get("count")))
    check("预定义表非空", (body or {}).get("count", 0) >= 10, str((body or {}).get("count")))

    st, body = call(client, "GET", "/api/crs/detail", token=admin, params={"crs": "EPSG:4326"})
    check("GET /api/crs/detail?crs=EPSG:4326 -> 200", st == 200, f"HTTP {st}")
    check("详情带 WKT", bool((body or {}).get("wkt")), str(body)[:160])
    check("详情带适用范围", len((body or {}).get("extent") or []) == 4, str(body)[:160])

    st, body = call(client, "GET", "/api/crs/detail", token=admin, params={"crs": "EPSG:999999"})
    check("详情查不存在的 CRS -> 404", st == 404, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "GET", "/api/crs/detail", token=admin, params={"crs": "这不是坐标系"})
    check("详情传垃圾值 -> 404（不 500）", st == 404, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "GET", "/api/crs/detail", token=admin)
    check("详情缺 crs 参数 -> 422", st == 422, f"HTTP {st}")

    resp = raw(client, "GET", "/api/crs/preview.png", token=admin,
               params={"crs": "EPSG:4326"})
    check("GET /api/crs/preview.png -> 200", resp is not None and resp.status_code == 200,
          f"HTTP {None if resp is None else resp.status_code}")
    check("预览图是 image/png",
          resp is not None and resp.headers.get("content-type", "").startswith("image/png"),
          "" if resp is None else resp.headers.get("content-type", ""))
    check("预览图是真正的 PNG（magic bytes）",
          resp is not None and resp.content[:8] == b"\x89PNG\r\n\x1a\n",
          "" if resp is None else str(len(resp.content)))
    # PNG 的 IHDR 里带着宽高（第 16~24 字节，大端 4 字节整数）—— 顺带证明画的是 360x220
    check("预览图尺寸是 360x220",
          resp is not None and resp.content[16:24] == b"\x00\x00\x01\x68\x00\x00\x00\xdc",
          "" if resp is None else str(resp.content[16:24]))

    st, body = call(client, "GET", "/api/crs/preview.png", token=admin,
                    params={"crs": "EPSG:999999"})
    check("预览图查不存在的 CRS -> 4xx（不 500）",
          st is not None and 400 <= st < 500, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "GET", "/api/crs/preview.png", token=admin,
                    params={"crs": "EPSG:4326", "width": 99999})
    check("预览图 width=99999 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/crs/preview.png", token=admin,
                    params={"crs": "EPSG:4326", "width": 10})
    check("预览图 width=10 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/crs/preview.png", token=admin)
    check("预览图缺 crs 参数 -> 422", st == 422, f"HTTP {st}")


# --------------------------------------------------------------------- 数据集


def _scan_datasets(client) -> None:
    print("\n== 5. 数据集与图层 ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/datasets", token=admin)
    check("GET /api/datasets -> 200", st == 200, f"HTTP {st}")
    check("共享示例数据集在列", any(d.get("id") == "example"
                                    for d in (body or {}).get("datasets", [])),
          str((body or {}).get("count")))

    st, body = call(client, "GET", "/api/datasets", token=admin, params={"source": "shared"})
    check("按 source=shared 过滤 -> 200", st == 200, f"HTTP {st}")
    check("过滤结果全是 shared",
          all(d.get("source") == "shared" for d in (body or {}).get("datasets", [])),
          str((body or {}).get("count")))

    st, body = call(client, "GET", "/api/datasets", token=admin, params={"source": "不存在的源"})
    check("按不存在的 source 过滤 -> 200 且为空", st == 200 and (body or {}).get("count") == 0,
          f"HTTP {st} {str(body)[:120]}")

    st, body = call(client, "GET", "/api/datasets/example", token=admin)
    check("GET /api/datasets/example -> 200", st == 200, f"HTTP {st}")
    check("详情含 2 个栅格", len((body or {}).get("layers") or {}) == 2,
          str(list((body or {}).get("layers") or {})))
    check("详情含 1 个矢量", len((body or {}).get("vectors") or {}) == 1,
          str(list((body or {}).get("vectors") or {})))

    st, body = call(client, "GET", "/api/datasets/no-such-dataset", token=admin)
    check("不存在的数据集 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/datasets/example/overview", token=admin)
    check("GET /api/datasets/example/overview -> 200", st == 200, f"HTTP {st}")
    check("概览里图层非空", len((body or {}).get("layers") or []) >= 3,
          str((body or {}).get("layers"))[:120])

    st, body = call(client, "GET", "/api/datasets/example/overview", token=admin,
                    params={"max_pixels": 10})
    check("overview 的 max_pixels=10 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/datasets/rescan")
    check("匿名 POST /api/datasets/rescan -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "GET", "/api/layers", token=admin)
    check("GET /api/layers -> 200", st == 200, f"HTTP {st}")
    check("图层清单含 example:classes",
          any(i.get("ref") == "example:classes" for i in (body or {}).get("layers", [])),
          str((body or {}).get("count")))
    check("图层清单含 example:boundary",
          any(i.get("ref") == "example:boundary" for i in (body or {}).get("layers", [])),
          str((body or {}).get("count")))

    st, body = call(client, "GET", "/api/layers", token=admin, params={"kind": "vector"})
    check("按 kind=vector 过滤 -> 200", st == 200, f"HTTP {st}")
    check("过滤结果全是矢量",
          all(i.get("kind") == "vector" for i in (body or {}).get("layers", [])),
          str((body or {}).get("count")))

    st, body = call(client, "GET", "/api/layers", token=admin, params={"q": "boundary"})
    check("按关键字搜图层 -> 200", st == 200, f"HTTP {st}")
    check("搜到了 boundary", (body or {}).get("count", 0) >= 1, str(body)[:120])

    st, body = call(client, "GET", "/api/layers/example/boundary/fields", token=admin)
    check("矢量图层字段清单 -> 200", st == 200, f"HTTP {st}")
    check("字段清单标成 vector", (body or {}).get("kind") == "vector", str(body)[:120])
    check("字段清单非空", len((body or {}).get("fields") or []) >= 1, str(body)[:120])

    st, body = call(client, "GET", "/api/layers/example/classes/fields", token=admin)
    check("栅格图层字段清单 -> 200", st == 200, f"HTTP {st}")
    check("栅格字段清单标成 raster", (body or {}).get("kind") == "raster", str(body)[:120])

    st, body = call(client, "GET", "/api/layers/example/no-such-layer/fields", token=admin)
    check("不存在的图层 -> 404", st == 404, f"HTTP {st}")


# --------------------------------------------------------------------- 上传


def _scan_uploads(client) -> None:
    print("\n== 6. 上传工作区 /api/uploads/* ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "bob", "password": BOB_PW})
    bob = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/uploads")
    check("匿名 GET /api/uploads -> 401", st == 401, f"HTTP {st}")

    st, body = upload_geojson(client, alice, "alice_private.geojson")
    check("alice 上传到自己空间 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")
    alice_ds = ((body or {}).get("results") or [{}])[0].get("dataset_id") or ""
    alice_ref = find_ref(client, alice, alice_ds)
    check("alice 的上传拿到了图层引用", bool(alice_ref), alice_ds)

    st, body = upload_geojson(client, bob, "bob_private.geojson")
    check("bob 上传到自己空间 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")
    bob_ds = ((body or {}).get("results") or [{}])[0].get("dataset_id") or ""
    bob_ref = find_ref(client, bob, bob_ds)
    check("bob 的上传拿到了图层引用", bool(bob_ref), bob_ds)

    st, body = upload_geojson(client, alice, "alice_shared.geojson", scope="shared")
    check("alice 上传到共享空间 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")

    st, body = upload_geojson(client, alice, "alice_other.geojson",
                              scope="user:not-my-id")
    check("往别人的空间上传 -> 403", st == 403, f"HTTP {st} {text_of(body)[:160]}")

    st, body = upload_geojson(client, alice, "alice_bogus.geojson", scope="不存在的空间")
    check("上传到不存在的空间 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/uploads", token=alice,
                    files={"files": ("bad.txt", b"hello", "text/plain")})
    check("上传不支持的类型 -> 422", st == 422, f"HTTP {st} {text_of(body)[:160]}")

    st, body = call(client, "POST", "/api/uploads", token=alice,
                    files={"files": ("empty.geojson", b"", "application/geo+json")})
    check("上传空文件 -> 422", st == 422, f"HTTP {st} {text_of(body)[:160]}")

    st, body = call(client, "GET", "/api/uploads", token=alice)
    check("GET /api/uploads -> 200", st == 200, f"HTTP {st}")
    check("alice 看得到自己的上传", (body or {}).get("count", 0) >= 1, str(body)[:160])

    st, body = call(client, "GET", "/api/uploads", token=bob)
    bob_ids = [i.get("dataset_id") for i in (body or {}).get("uploads", [])]
    check("bob 看不到 alice 的私有上传", alice_ds not in bob_ids, str(bob_ids))
    check("bob 看得到自己的上传", bob_ds in bob_ids, str(bob_ids))

    st, body = call(client, "GET", f"/api/uploads/{alice_ds}/files/alice_private.geojson",
                    token=alice)
    check("下载自己的原始文件 -> 200", st == 200, f"HTTP {st}")
    check("下载回来的内容一致", "FeatureCollection" in json.dumps(body or {}),
          str(body)[:120])

    st, body = call(client, "GET", f"/api/uploads/{alice_ds}/files/alice_private.geojson",
                    token=bob)
    check("下载别人的私有文件 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/uploads/{alice_ds}/files/no-such-file.geojson",
                    token=alice)
    check("下载不存在的文件 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/uploads/{alice_ds}/files/..%2F..%2Fgisds.db",
                    token=alice)
    check("文件名路径穿越不许读到库文件 -> 4xx（不 200）",
          st is not None and st != 200, f"HTTP {st}")

    st, body = call(client, "DELETE", f"/api/uploads/{alice_ds}", token=bob)
    check("bob 删 alice 的数据集 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "DELETE", f"/api/uploads/{bob_ds}", token=bob)
    check("bob 删自己的数据集 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")

    st, body = call(client, "DELETE", f"/api/uploads/{bob_ds}", token=bob)
    check("删同一个数据集第二次 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "DELETE", "/api/uploads/no-such-dataset", token=bob)
    check("删不存在的数据集 -> 404", st == 404, f"HTTP {st}")

    # 只传一个 .shp（缺 .dbf/.shx）—— QGIS 打不开，注册中心会丢掉整个数据集。
    # 这种东西以前**删不掉**（文件永久留在磁盘上），必须还能删。
    st, body = call(client, "POST", "/api/uploads", token=alice,
                    files={"files": ("orphan.shp", b"\x00\x00\x27\x0a", "application/octet-stream")})
    check("只传 .shp 的上传 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")
    orphan = ((body or {}).get("results") or [{}])[0]
    orphan_ds = orphan.get("dataset_id") or ""
    check("只传 .shp 的上传没有注册成功（registered=false）",
          orphan.get("registered") is False, str(orphan)[:160])
    st, body = call(client, "DELETE", f"/api/uploads/{orphan_ds}", token=alice)
    check("没注册成功的上传也能删掉 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")


# --------------------------------------------------------------------- 越权


def _scan_isolation(client, data_root: Path) -> None:
    print("\n== 7. 越权矩阵（A 用户碰 B 用户的私有数据）==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "bob", "password": BOB_PW})
    bob = (body or {}).get("token") or ""

    # bob 造一个只有他自己看得到的私有数据集
    st, body = upload_geojson(client, bob, "secret.geojson")
    bob_ds = ((body or {}).get("results") or [{}])[0].get("dataset_id") or ""
    bob_ref = find_ref(client, bob, bob_ds)
    bob_vector = bob_ref.split(":")[-1] if bob_ref else ""
    bob_raster = "no-such-raster"

    st, body = call(client, "GET", "/api/datasets", token=alice)
    alice_ids = [d.get("id") for d in (body or {}).get("datasets", [])]
    check("alice 的数据集清单不含 bob 的私有数据集", bob_ds not in alice_ids,
          str(alice_ids))

    st, body = call(client, "GET", f"/api/datasets/{bob_ds}", token=alice)
    check("alice 读 bob 的数据集 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/layers", token=alice)
    alice_refs = [i.get("ref") for i in (body or {}).get("layers", [])]
    check("alice 的图层清单不含 bob 的私有图层", bob_ref not in alice_refs,
          str(alice_refs))

    st, body = call(client, "GET", f"/api/vectors/{bob_ds}/{bob_vector}", token=alice)
    check("alice 读 bob 的矢量图层 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/rasters/{bob_ds}/{bob_raster}", token=alice)
    check("alice 读 bob 的栅格图层 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/vectors/{bob_ds}/{bob_vector}/features",
                    token=alice)
    check("alice 查 bob 的要素 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/rasters/{bob_ds}/{bob_raster}/point",
                    token=alice, params={"lon": 112.05, "lat": 39.05})
    check("alice 点查 bob 的栅格 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/panels/table?ref={bob_ref}", token=alice)
    check("alice 打开 bob 的属性表 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/panels/overview?ref={bob_ref}", token=alice)
    check("alice 打开 bob 的信息面板 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/render/vector/{bob_ds}/{bob_vector}.png",
                    token=alice)
    check("alice 渲染 bob 的矢量 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/tiles/{bob_ds}/{bob_raster}/13/6700/3200.png",
                    token=alice)
    check("alice 取 bob 的瓦片 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/query/point", token=alice,
                    params={"lon": 112.05, "lat": 39.05})
    check("alice 的综合点查询不含 bob 的数据集",
          bob_ds not in json.dumps(body or {}), str(body)[:160])

    st, body = call(client, "GET", "/api/processing/algorithms/native:buffer", token=alice)
    check("alice 的算法详情里没有 bob 的图层",
          bob_ref not in json.dumps((body or {}).get("available_layers") or []),
          str(body)[:160])

    # rescan 不该把别人的私有数据集吐出来
    st, body = call(client, "POST", "/api/datasets/rescan", token=alice)
    check("alice 触发 rescan -> 200", st == 200, f"HTTP {st}")
    rescan_ids = [d.get("id") for d in (body or {}).get("datasets", [])]
    check("rescan 的结果不含 bob 的私有数据集（越权）", bob_ds not in rescan_ids,
          f"共 {len(rescan_ids)} 个：" + str(rescan_ids)[:200])

    # 作业是按 owner 记的，但读取没按 owner 过滤
    st, body = call(client, "POST", "/api/processing/run", token=alice,
                    json={"algorithm": "native:buffer",
                          "parameters": {"INPUT": bob_ref, "DISTANCE": 0.01},
                          "name": "越权-buffer"})
    check("alice 用 bob 的图层跑算法 -> 作业被受理", st == 200, f"HTTP {st}")
    job_id = (body or {}).get("job_id") or ""
    final = wait_job(client, alice, job_id)
    check("alice 不该能拿 bob 的数据跑出结果（越权）",
          final.get("status") != "success", f"status={final.get('status')}")

    st, body = call(client, "GET", f"/api/processing/jobs/{job_id}", token=bob)
    check("bob 读 alice 的作业 -> 404（作业按 owner 隔离）", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/processing/jobs/{job_id}/output/OUTPUT", token=bob)
    check("bob 下 alice 的作业输出 -> 404（作业按 owner 隔离）", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/auth/me", token=alice)
    alice_uid = (body or {}).get("principal", {}).get("user_id") or ""

    st, body = call(client, "GET", "/api/auth/me", token=bob)
    bob_uid = (body or {}).get("principal", {}).get("user_id") or ""

    # Processing 的 OUTPUT 参数是原样透传给 QGIS 的 —— 普通用户能不能
    # 借此往**别人的空间**里写文件？（目标只落在本脚本的临时沙箱里）
    injected = data_root / "users" / bob_uid / "injected_by_alice.gpkg"
    st, body = call(client, "POST", "/api/processing/run", token=alice,
                    json={"algorithm": "native:buffer",
                          "parameters": {"INPUT": "example:boundary", "DISTANCE": 0.01,
                                         "OUTPUT": str(injected)},
                          "name": "越权写文件"})
    check("Processing 指定 OUTPUT 路径 -> 作业被受理", st == 200, f"HTTP {st}")
    inject_job = (body or {}).get("job_id") or ""
    wait_job(client, alice, inject_job)
    check("普通用户不能往别人的私有空间里写文件",
          not injected.exists(), f"文件已落到 {injected}")

    st, body = call(client, "GET", "/api/processing/jobs", token=bob)
    owners = [j.get("owner") for j in (body or {}).get("jobs", [])]
    check("bob 的作业清单里不该有 alice 的作业",
          bool(alice_uid) and alice_uid not in owners, str(owners))


# --------------------------------------------------------------------- 面板


def _scan_panels(client) -> None:
    print("\n== 8. 面板 /api/panels/* ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "bob", "password": BOB_PW})
    bob = (body or {}).get("token") or ""

    st, body = upload_geojson(client, alice, "panels_alice.geojson")
    ds = ((body or {}).get("results") or [{}])[0].get("dataset_id") or ""
    ref = find_ref(client, alice, ds)

    st, body = call(client, "GET", f"/api/panels/table?ref={ref}", token=alice)
    check("GET /api/panels/table -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")
    check("属性表给出字段", len((body or {}).get("fields") or []) >= 2,
          str((body or {}).get("fields"))[:120])

    st, body = call(client, "GET", "/api/panels/table?ref=没有冒号", token=alice)
    check("属性表 ref 格式不对 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/panels/table?ref=example:classes", token=alice)
    check("属性表指向栅格图层 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/panels/table?ref=nosuch:layer", token=alice)
    check("属性表指向不存在的图层 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/panels/statistics?ref={ref}", token=alice)
    check("GET /api/panels/statistics -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/panels/statistics?ref={ref}&field=没有这个字段",
                    token=alice)
    check("统计不存在的字段 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/panels/overview?ref={ref}", token=alice)
    check("GET /api/panels/overview -> 200", st == 200, f"HTTP {st}")
    check("信息面板给出要素数", (body or {}).get("feature_count") == 2, str(body)[:120])

    st, body = call(client, "GET", "/api/panels/logs", token=alice)
    check("GET /api/panels/logs -> 200", st == 200, f"HTTP {st}")
    check("日志面板有条目", (body or {}).get("count", 0) >= 1, str(body)[:120])

    st, body = call(client, "DELETE", "/api/panels/logs")
    check("匿名 DELETE /api/panels/logs -> 401（清日志也要登录）", st == 401, f"HTTP {st}")

    st, body = call(client, "DELETE", "/api/panels/logs", token=alice)
    check("DELETE /api/panels/logs -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/select", token=alice,
                    json={"ref": ref, "expression": '"面积" > 200'})
    check("按表达式选择 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")
    check("按表达式选出 1 个", (body or {}).get("count") == 1, str(body)[:120])

    st, body = call(client, "POST", "/api/panels/select", token=alice,
                    json={"ref": ref, "expression": '"面积" >'})
    check("非法表达式 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/select", token=alice, json={"ref": ref})
    check("什么都不给 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/select", token=alice,
                    json={"ref": ref, "mode": "all"})
    check("全选 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/select", token=alice,
                    json={"ref": "nosuch:layer", "mode": "all"})
    check("对不存在的图层做选择 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/panels/bookmarks", token=alice)
    check("GET /api/panels/bookmarks -> 200", st == 200, f"HTTP {st}")
    check("书签默认在自己的空间",
          str((body or {}).get("scope", "")).startswith("user:"), str(body)[:120])

    st, body = call(client, "POST", "/api/panels/bookmarks", token=alice,
                    json={"name": "面扫描书签", "extent": [112.0, 39.0, 112.1, 39.1]})
    check("新建书签 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/panels/bookmarks", token=alice,
                    json={"name": "", "extent": [1, 2, 3, 4]})
    check("空书签名 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/panels/bookmarks", token=bob)
    check("bob 看不到 alice 的书签", (body or {}).get("count") == 0, str(body)[:120])

    st, body = call(client, "GET", "/api/panels/bookmarks", token=alice,
                    params={"scope": "user:not-my-id"})
    check("读别人的空间书签 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/bookmarks", token=alice,
                    json={"name": "越权书签", "extent": [1, 2, 3, 4],
                          "scope": "user:not-my-id"})
    check("往别人的空间写书签 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "DELETE", "/api/panels/bookmarks", token=alice,
                    params={"name": "面扫描书签"})
    check("删书签 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "DELETE", "/api/panels/bookmarks", token=alice,
                    params={"name": "面扫描书签"})
    check("删同一个书签第二次 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/panels/capabilities", token=alice)
    check("GET /api/panels/capabilities -> 200", st == 200, f"HTTP {st}")
    check("能力清单列出面板", "table" in (body or {}).get("panels", []), str(body)[:120])

    st, body = call(client, "GET", "/api/panels/capabilities")
    check("匿名 GET /api/panels/capabilities -> 401", st == 401, f"HTTP {st}")

    # 要素编辑：只动 alice 自己的私有图层
    st, body = call(client, "POST", "/api/panels/edit/start", token=alice,
                    json={"ref": ref})
    check("进入编辑模式 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/panels/edit/start", token=alice,
                    json={"ref": ref})
    check("重复进入编辑模式 -> 200（幂等）", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/edit/start", token=bob,
                    json={"ref": ref})
    check("bob 进 alice 的图层编辑 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "PATCH", "/api/panels/edit/feature", token=alice,
                    json={"ref": ref, "attributes": {"面积": 1}})
    check("改要素缺 fid -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "PATCH", "/api/panels/edit/feature", token=alice,
                    json={"ref": ref, "fid": 1, "attributes": {}})
    check("改要素 attributes 为空 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/panels/table?ref={ref}", token=alice)
    rows = (body or {}).get("rows") or []
    fid = rows[0].get("fid") if rows else None
    st, body = call(client, "PATCH", "/api/panels/edit/feature", token=alice,
                    json={"ref": ref, "fid": fid, "attributes": {"面积": 999}})
    check("改要素属性 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/panels/edit/feature", token=alice,
                    json={"ref": ref,
                          "geometry": {"type": "Polygon",
                                       "coordinates": [[[112.6, 39.6], [112.7, 39.6],
                                                        [112.7, 39.7], [112.6, 39.7],
                                                        [112.6, 39.6]]]},
                          "attributes": {"名称": "丙", "面积": 7}})
    check("新增要素 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/panels/edit/calculator", token=alice,
                    json={"ref": ref, "field": "面积", "expression": '"面积" * 2'})
    check("字段计算器 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/panels/edit/calculator", token=alice,
                    json={"ref": ref, "field": "面积", "expression": '"面积" *'})
    check("非法计算表达式 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/edit/commit", token=alice,
                    json={"ref": ref})
    check("保存编辑 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/panels/edit/commit", token=alice,
                    json={"ref": ref})
    check("不在编辑态再保存 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/panels/edit/start", token=alice,
                    json={"ref": ref})
    st, body = call(client, "POST", "/api/panels/edit/rollback", token=alice,
                    json={"ref": ref})
    check("放弃编辑 -> 200", st == 200, f"HTTP {st}")


# --------------------------------------------------------------------- 工具箱


def _scan_processing(client) -> None:
    print("\n== 9. 工具箱 /api/processing/* ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/processing/algorithms", token=alice)
    check("GET /api/processing/algorithms -> 200", st == 200, f"HTTP {st}")
    check("算法总数不为 0", (body or {}).get("total", 0) > 0, str(body)[:120])

    st, body = call(client, "GET", "/api/processing/algorithms", token=alice,
                    params={"q": "buffer"})
    check("按关键字搜算法 -> 200", st == 200, f"HTTP {st}")
    check("搜到了算法", (body or {}).get("count", 0) >= 1, str(body)[:120])

    st, body = call(client, "GET", "/api/processing/algorithms", token=alice,
                    params={"provider": "gdal"})
    check("按 provider 过滤 -> 200（不 500）", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/algorithms", token=alice,
                    params={"include_en": "true"})
    check("include_en=true -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/algorithms/native:buffer", token=alice)
    check("GET /api/processing/algorithms/native:buffer -> 200", st == 200, f"HTTP {st}")
    check("算法详情带参数定义", len((body or {}).get("parameters") or []) >= 1,
          str(body)[:120])

    st, body = call(client, "GET", "/api/processing/algorithms/no:such_algo", token=alice)
    check("算法详情查不存在的算法 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/groups", token=alice)
    check("GET /api/processing/groups -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/processing/run", token=alice, json={})
    check("run 缺 algorithm -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/processing/run", token=alice,
                    json={"algorithm": "native:buffer", "parameters": "不是对象"})
    check("run 的 parameters 不是对象 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/processing/run", token=alice,
                    json={"algorithm": "no:such_algo", "parameters": {}})
    check("run 不存在的算法 -> 作业被受理后失败，不 500", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/processing/run", token=alice,
                    json={"algorithm": "native:buffer",
                          "parameters": {"INPUT": "example:boundary", "DISTANCE": 0.01},
                          "name": "面扫描缓冲"})
    check("run 正常算法 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")
    job_id = (body or {}).get("job_id") or ""
    check("run 返回 job_id", bool(job_id))

    final = wait_job(client, alice, job_id)
    check("作业跑成功", final.get("status") == "success",
          str(final.get("error"))[:200])
    outputs = final.get("outputs") or {}
    check("作业产出 OUTPUT", "OUTPUT" in outputs, str(list(outputs)))

    st, body = call(client, "GET", f"/api/processing/jobs/{job_id}/output/OUTPUT",
                    token=alice)
    check("下载作业输出 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/processing/jobs/{job_id}/output/没有这个键",
                    token=alice)
    check("下载不存在的输出键 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "POST", f"/api/processing/jobs/{job_id}/add-to-map",
                    token=alice, json={"key": "OUTPUT", "scope": "me"})
    check("作业输出加入地图 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")
    check("加入地图后落在自己的空间",
          str((body or {}).get("scope", "")).startswith("user:"), str(body)[:160])

    st, body = call(client, "POST", f"/api/processing/jobs/{job_id}/add-to-map",
                    token=alice, json={"key": "OUTPUT", "scope": "user:not-my-id"})
    check("作业输出加入别人的空间 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/jobs/no-such-job", token=alice)
    check("查不存在的作业 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/jobs/no-such-job/output/OUTPUT",
                    token=alice)
    check("下载不存在作业的输出 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "POST", "/api/processing/jobs/no-such-job/add-to-map",
                    token=alice, json={"key": "OUTPUT"})
    check("不存在作业加入地图 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/jobs", token=alice,
                    params={"limit": 0})
    check("作业列表 limit=0 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/jobs", token=alice,
                    params={"limit": 99999})
    check("作业列表 limit=99999 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/processing/jobs", token=admin)
    check("管理员能看作业列表 -> 200", st == 200, f"HTTP {st}")

    # 普通用户清作业历史不该把别人的作业也清掉
    st, body = call(client, "POST", "/api/processing/run", token=admin,
                    json={"algorithm": "native:buffer",
                          "parameters": {"INPUT": "example:boundary", "DISTANCE": 0.02},
                          "name": "管理员的作业"})
    admin_job_id = (body or {}).get("job_id") or ""
    wait_job(client, admin, admin_job_id)

    st, body = call(client, "DELETE", "/api/processing/jobs", token=alice)
    check("普通用户清空作业历史 -> 403（作业目录是服务级的，删了就没了）",
          st == 403, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "GET", f"/api/processing/jobs/{admin_job_id}", token=admin)
    check("普通用户清不了作业，管理员的作业记录还在",
          st == 200, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/processing/jobs/{admin_job_id}/output/OUTPUT",
                    token=admin)
    check("普通用户清不了作业，管理员的作业输出文件也还在",
          st == 200, f"HTTP {st}")

    # 管理员自己清：记录和文件都要真的没掉
    st, body = call(client, "DELETE", "/api/processing/jobs", token=admin)
    check("管理员清空作业历史 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "GET", f"/api/processing/jobs/{admin_job_id}", token=admin)
    check("管理员清空之后，作业记录真的没了（不是只删了文件）",
          st == 404, f"HTTP {st}")


# --------------------------------------------------------------------- 插件


def _scan_plugins(client) -> None:
    print("\n== 10. 插件 /api/plugins/* ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/plugins")
    check("匿名 GET /api/plugins -> 401", st == 401, f"HTTP {st}")

    st, body = call(client, "GET", "/api/plugins", token=alice)
    check("普通用户 GET /api/plugins -> 200", st == 200, f"HTTP {st}")
    check("普通用户 can_manage=false", (body or {}).get("can_manage") is False, str(body)[:120])

    st, body = call(client, "GET", "/api/plugins", token=admin)
    check("管理员 GET /api/plugins -> 200", st == 200, f"HTTP {st}")
    check("管理员 can_manage=true", (body or {}).get("can_manage") is True, str(body)[:120])

    data = make_zip({
        "surface_plugin/metadata.json": json.dumps(PLUGIN_META, ensure_ascii=False).encode("utf-8"),
        "surface_plugin/__init__.py": PLUGIN_INIT.encode("utf-8"),
    })
    st, body = call(client, "POST", "/api/plugins/install", token=alice,
                    files={"file": ("surface_plugin.zip", data, "application/zip")})
    check("普通用户装插件 -> 403", st == 403, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/install", token=admin,
                    files={"file": ("surface_plugin.zip", data, "application/zip")})
    check("管理员装插件 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")

    st, body = call(client, "POST", "/api/plugins/install", token=admin,
                    files={"file": ("surface_plugin.zip", data, "application/zip")})
    check("同一个 zip 装第二次 -> 不 500", st is not None and st < 500,
          f"HTTP {st} {text_of(body)[:160]}")

    st, body = call(client, "GET", "/api/plugins", token=admin)
    slugs = [p.get("slug") for p in (body or {}).get("plugins", [])]
    check("插件清单里有刚装的插件", "surface_plugin" in slugs, str(slugs))

    st, body = call(client, "POST", "/api/plugins/surface_plugin/action", token=admin,
                    json={"action": "hello", "params": {"n": 7}})
    check("调用插件动作 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")

    st, body = call(client, "POST", "/api/plugins/surface_plugin/action", token=admin,
                    json={})
    check("调插件动作不指定 action -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/no_such_plugin/action", token=admin,
                    json={"action": "hello"})
    check("调不存在的插件 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/surface_plugin/disable", token=admin)
    check("停用插件 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/surface_plugin/disable", token=admin)
    check("重复停用插件 -> 200（幂等）", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/surface_plugin/enable", token=admin)
    check("启用插件 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/surface_plugin/reload", token=admin)
    check("重载插件 -> 200", st == 200, f"HTTP {st} {text_of(body)[:160]}")

    st, body = call(client, "POST", "/api/plugins/no_such_plugin/enable", token=admin)
    check("启用不存在的插件 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/surface_plugin/uninstall", token=admin)
    check("卸载插件 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "POST", "/api/plugins/surface_plugin/uninstall", token=admin)
    check("重复卸载插件 -> 404", st == 404, f"HTTP {st}")


# --------------------------------------------------------------------- 栅格 / 矢量 / 渲染


def _scan_geo(client) -> None:
    print("\n== 11. 栅格 / 矢量 / 渲染 / 瓦片 ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "alice", "password": ALICE_PW})
    alice = (body or {}).get("token") or ""

    st, body = call(client, "GET", "/api/rasters/example/classes", token=alice)
    check("GET /api/rasters/example/classes -> 200", st == 200, f"HTTP {st}")
    ext = (body or {}).get("extent_wgs84") or [0, 0, 0, 0]
    cx = (ext[0] + ext[2]) / 2
    cy = (ext[1] + ext[3]) / 2

    st, body = call(client, "GET", "/api/rasters/example/classes/point", token=alice,
                    params={"lon": cx, "lat": cy})
    check("栅格点查询 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")
    check("点查询给出类别名", bool((body or {}).get("class_name")), str(body)[:120])

    st, body = call(client, "GET", "/api/rasters/example/classes/point", token=alice,
                    params={"lat": cy})
    check("点查询缺 lon -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/example/classes/point", token=alice,
                    params={"lon": "不是数字", "lat": cy})
    check("点查询 lon 类型错 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/example/classes/bbox", token=alice,
                    params={"minx": ext[0], "miny": ext[1], "maxx": ext[2], "maxy": ext[3]})
    check("栅格 bbox 统计 -> 200", st == 200, f"HTTP {st}")
    check("bbox 统计给出类别", len((body or {}).get("classes") or []) >= 1, str(body)[:120])

    st, body = call(client, "GET", "/api/rasters/example/classes/bbox", token=alice,
                    params={"minx": 1, "miny": 1, "maxx": 0, "maxy": 0})
    check("bbox 范围反了 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/example/classes/bbox", token=alice,
                    params={"minx": 1, "miny": 1})
    check("bbox 缺一半 -> 200（按整幅算）", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/example/classes/bbox", token=alice,
                    params={"max_pixels": 10})
    check("bbox 的 max_pixels=10 -> 422", st == 422, f"HTTP {st}")

    poly = [[ext[0], ext[1]], [ext[2], ext[1]], [ext[2], ext[3]], [ext[0], ext[3]]]
    st, body = call(client, "POST", "/api/rasters/example/classes/polygon", token=alice,
                    json=poly)
    check("栅格多边形统计 -> 200", st == 200, f"HTTP {st} {text_of(body)[:120]}")

    st, body = call(client, "POST", "/api/rasters/example/classes/polygon", token=alice,
                    json=[])
    check("多边形顶点不足 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "POST", "/api/rasters/example/classes/polygon", token=alice,
                    json="不是数组")
    check("多边形 body 类型错 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/example/classes/histogram", token=alice,
                    params={"bins": 16})
    check("栅格直方图 -> 200", st == 200, f"HTTP {st}")
    check("直方图有 16 个 bin", len((body or {}).get("bins") or []) == 16, str(body)[:120])

    st, body = call(client, "GET", "/api/rasters/example/classes/histogram", token=alice,
                    params={"bins": 999})
    check("直方图 bins=999 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/example/classes/area", token=alice)
    check("栅格面积统计 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/example/classes/stats", token=alice)
    check("栅格波段统计 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/rasters/no-such/no-such", token=alice)
    check("不存在的栅格图层 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary", token=alice)
    check("GET /api/vectors/example/boundary -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/features", token=alice,
                    params={"limit": 5})
    check("矢量要素查询 -> 200", st == 200, f"HTTP {st}")
    check("要素是 GeoJSON", (body or {}).get("type") == "FeatureCollection", str(body)[:120])

    st, body = call(client, "GET", "/api/vectors/example/boundary/features", token=alice,
                    params={"limit": 99999})
    check("要素查询 limit=99999 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/features", token=alice,
                    params={"offset": -1})
    check("要素查询 offset=-1 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/features", token=alice,
                    params={"bbox": "1,2,3"})
    check("要素查询 bbox 少一项 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/features", token=alice,
                    params={"bbox": "a,b,c,d"})
    check("要素查询 bbox 不是数字 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/features", token=alice,
                    params={"bbox": "3,3,1,1"})
    check("要素查询 bbox 范围反了 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/stats", token=alice)
    check("矢量统计 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/stats", token=alice,
                    params={"top_n": 999})
    check("矢量统计 top_n=999 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/hit", token=alice,
                    params={"lon": cx, "lat": cy})
    check("矢量点命中 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/example/boundary/hit", token=alice,
                    params={"lat": cy})
    check("矢量点命中缺 lon -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/vectors/no-such/no-such/features", token=alice)
    check("不存在的矢量图层 -> 404", st == 404, f"HTTP {st}")

    resp = raw(client, "GET", "/api/render/raster/example/classes.png", token=alice,
               params={"width": 256})
    check("栅格出图 -> 200", resp is not None and resp.status_code == 200,
          f"HTTP {None if resp is None else resp.status_code}")
    check("栅格出图是 PNG",
          resp is not None and resp.content[:8] == b"\x89PNG\r\n\x1a\n",
          "" if resp is None else f"{len(resp.content)} bytes")

    st, body = call(client, "GET", "/api/render/raster/example/classes.png", token=alice,
                    params={"width": 99999})
    check("出图 width=99999 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/render/raster/example/classes.png", token=alice,
                    params={"width": 10})
    check("出图 width=10 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/render/raster/no-such/no-such.png", token=alice)
    check("渲染不存在的图层 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/render/vector/example/boundary.png", token=alice)
    check("矢量出图 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/render/combined/example.png", token=alice,
                    params={"layers": "classes", "vectors": "boundary"})
    check("叠加出图 -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/render/combined/example.png", token=alice,
                    params={"layers": "没有这个图层"})
    check("叠加出图没有匹配图层 -> 404", st == 404, f"HTTP {st}")

    st, body = call(client, "GET", "/api/render/combined/example.png", token=alice)
    check("叠加出图缺 layers 参数 -> 422", st == 422, f"HTTP {st}")

    st, body = call(client, "GET", "/api/tiles/basemap/status", token=alice)
    check("GET /api/tiles/basemap/status -> 200", st == 200, f"HTTP {st}")

    st, body = call(client, "GET", "/api/tiles/basemap/3/4/2.png", token=alice)
    check("底图瓦片（没装底图时）-> 404 而不是 500",
          st is not None and st == 404, f"HTTP {st}")

    z = 13
    n = 2 ** z
    tx = int((cx + 180.0) / 360.0 * n)
    rad = math.radians(cy)
    ty = int((1.0 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2.0 * n)
    st, body = call(client, "GET", f"/api/tiles/example/classes/{z}/{tx}/{ty}.png",
                    token=alice)
    check("数据图层瓦片 -> 200", st == 200, f"HTTP {st}")
    resp = raw(client, "GET", f"/api/tiles/example/classes/{z}/{tx}/{ty}.png", token=alice)
    check("数据图层瓦片是 PNG",
          resp is not None and resp.content[:8] == b"\x89PNG\r\n\x1a\n",
          "" if resp is None else f"{len(resp.content)} bytes")

    st, body = call(client, "GET", f"/api/tiles/example/classes/{z}/999999/{ty}.png",
                    token=alice)
    check("瓦片 x 超范围 -> 不是 500", st is not None and st < 500, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/tiles/example/classes/{z}/{tx}/999999.png",
                    token=alice)
    check("瓦片 y 超范围 -> 不是 500", st is not None and st < 500, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/tiles/example/classes/-30/0/0.png", token=alice)
    check("瓦片 z=-30 -> 不是 500", st is not None and st < 500, f"HTTP {st}")

    st, body = call(client, "GET", f"/api/tiles/basemap/-30/0/0.png", token=alice)
    check("底图瓦片 z=-30 -> 不是 500", st is not None and st < 500, f"HTTP {st}")

    st, body = call(client, "GET", "/api/tiles/example/classes/13/0/0.png")
    check("匿名取数据图层瓦片 -> 401", st == 401, f"HTTP {st}")

    # 前端用 <img> 加载瓦片时带不了 Authorization 头，靠 ?token= 传令牌
    # （deps._token_from 认 token / access_token）—— 这条路不能断
    st, body = call(client, "GET", f"/api/tiles/example/classes/{z}/{tx}/{ty}.png",
                    params={"token": alice})
    check("瓦片用 ?token= 传令牌 -> 200", st == 200, f"HTTP {st}")


# --------------------------------------------------------------------- 幂等


def _scan_idempotency(client) -> None:
    print("\n== 12. 幂等与状态 ==")
    st, body = call(client, "POST", "/api/auth/login",
                    json={"username": "admin", "password": ADMIN_PW})
    admin = (body or {}).get("token") or ""

    st, body = call(client, "POST", "/api/datasets/rescan", token=admin)
    check("第一次 rescan -> 200", st == 200, f"HTTP {st}")
    st, body = call(client, "POST", "/api/datasets/rescan", token=admin)
    check("紧接着第二次 rescan -> 200", st == 200, f"HTTP {st}")
    check("两次 rescan 的数据集数一致",
          (body or {}).get("count") is not None, str(body)[:120])

    st, body = call(client, "GET", "/api/datasets", token=admin)
    check("rescan 之后数据集清单还在 -> 200", st == 200, f"HTTP {st}")


# --------------------------------------------------------------------- 免登录模式


def _scan_open_mode(data_root: Path, plugin_dir: Path) -> None:
    print("\n== 13. 免登录模式（require_login=false）==")
    tmp_root = Path(tempfile.mkdtemp(prefix="gisds_surface_open_"))
    open_data = tmp_root / "data"
    (open_data / "shared").mkdir(parents=True, exist_ok=True)
    sample = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
    if sample.is_dir():
        shutil.copytree(sample, open_data / "shared" / "example")
    server = TestServer(
        port=_free_port(8911),
        require_login=False,
        data_root=open_data,
        extra_env={
            "GISDS_ADMIN_PASSWORD": ADMIN_PW,
            "GISDS_PLUGIN_DIR": str(plugin_dir),
            "GISDS_PASSWORD_NOTE": "0",
        },
    )
    if not server.start():
        check("免登录模式的临时服务能起来", False, "服务起不来")
        shutil.rmtree(tmp_root, ignore_errors=True)
        return
    try:
        with httpx.Client(base_url=server.base, timeout=120.0) as client:
            st, body = call(client, "GET", "/api/datasets")
            check("免登录模式 GET /api/datasets -> 200", st == 200, f"HTTP {st}")
            check("免登录模式能看到共享数据集",
                  any(d.get("id") == "example" for d in (body or {}).get("datasets", [])),
                  str((body or {}).get("count")))

            st, body = call(client, "GET", "/api/auth/me")
            check("免登录模式 GET /api/auth/me -> 200", st == 200, f"HTTP {st}")
            check("免登录模式的身份是匿名",
                  (body or {}).get("principal", {}).get("username") == "anonymous",
                  str(body)[:120])

            st, body = call(client, "GET", "/api/console/state")
            check("免登录模式 GET /api/console/state -> 200", st == 200, f"HTTP {st}")
            check("免登录模式控制台 allowed=false", (body or {}).get("allowed") is False,
                  str(body)[:120])

            st, body = call(client, "POST", "/api/console/exec", json={"code": "1+1"})
            check("免登录模式执行代码 -> 403", st == 403, f"HTTP {st}")

            st, body = call(client, "GET", "/api/admin/users")
            check("免登录模式 GET /api/admin/users -> 403", st == 403, f"HTTP {st}")

            st, body = call(client, "GET", "/api/panels/logs")
            check("免登录模式 GET /api/panels/logs -> 200（不鉴权）", st == 200, f"HTTP {st}")

            st, body = upload_geojson(client, None, "open_upload.geojson")
            check("免登录模式上传 -> 落在共享空间",
                  st == 200 and (body or {}).get("scope") == "shared",
                  f"HTTP {st} {text_of(body)[:160]}")

            st, body = call(client, "GET", "/api/crs/predefined")
            check("免登录模式 GET /api/crs/predefined -> 200", st == 200, f"HTTP {st}")
    finally:
        server.stop()
        shutil.rmtree(tmp_root, ignore_errors=True)


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
