# -*- coding: utf-8 -*-
"""验证「口令从哪来」决定「要不要落盘、要不要打进日志」，以及改密后的同步语义。

判据只有一条：**当前有效口令还有没有别的明文副本**。

自举那一刻（api/app.py 建号）：

| 情形                                   | 备忘文件 | 日志里有明文密码 |
|----------------------------------------|----------|------------------|
| config / GISDS_ADMIN_PASSWORD 里配了    | **不写** | **不打**（明文本来就在配置里） |
| 没配 → 服务随机生成                      | 写      | 打（多机部署就靠这两处） |
| password_note=false + 随机生成          | 不写    | 仍打（否则谁也登不进去） |

改密之后（管理员改密 API / CLI ``set-password`` 两条路径）口径一致：
config / 环境变量里那一份**已经过期作废** → 备忘成了当前有效口令**唯一的明文副本**
→ **要写**（受 ``password_note`` 开关控制）。写进去的永远是当前有效口令，
绝不会留下一个失效的旧口令让人照着登一直 401。

外加一条：配置里的 admin_password 跟库里当前口令对不上时，启动要告警
（照着 config 登不进去、而账号状态和日志看上去都正常，最难查）。

跑法：``run-qgis-python.bat tests/check_password_flag.py``
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from server_helper import TestServer  # noqa: E402

fails: list[str] = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 46 = 四档 case 的条数 8（配置+关）+ 13（配置+开）+ 8（随机+关）
#            + 14（随机+开）+ 告警那条 2 + 生产备忘未被触碰 1。
# 每一档的条数由**固定的入参**（note_flag / from_config）决定，所以是确定的；
# 剩下的分支（令牌 / user_id 拿没拿到）已经改成**无条件执行** ——
# 条件不满足就让断言失败，而不是整段跳过。
EXPECTED_CHECKS = 46


def check(label: str, ok: bool, detail: str = "") -> None:
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def free_port() -> int:
    """找一个当前空闲的端口。

    不能写死端口号：TestServer 遇到端口被占会自动顺延，写死的话两个
    case 可能顺延到同一个端口上，两个服务互相把对方顶掉，状态就串了
    （表现是「第一个 case 登录失败、备忘内容对不上」）。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def server_log(port: int) -> str:
    log = PROJECT_ROOT / "logs" / f"testserver_{port}.log"
    if not log.is_file():
        return ""
    return log.read_text(encoding="utf-8", errors="replace")


def generated_password(port: int) -> str | None:
    """从启动日志里解析服务随机生成的初始密码。"""
    text = server_log(port)
    m = re.search(r"密码\s*：\s*(\S+)", text)
    return m.group(1) if m else None


def note_path(db: Path) -> Path:
    """备忘该在哪 —— 直接问生产代码，不在测试里复刻一遍命名规则。

    复刻一份的话，两边一起漂移，测试就再也钉不住任何东西了
    （命名/位置本身由 ``tests/check_password_note.py`` 负责钉）。
    """
    from gisds.core.auth import password_note_path

    return password_note_path(db)


def note_body(note: Path) -> str:
    return note.read_text(encoding="utf-8") if note.is_file() else ""


def login(base: str, username: str, password: str) -> int:
    req = urllib.request.Request(
        base + "/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def admin_token(base: str, username: str, password: str) -> str | None:
    req = urllib.request.Request(
        base + "/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read()).get("token")
    except Exception:
        return None


def user_id_of(base: str, token: str) -> str | None:
    """改密接口的路径参数是 user_id，不是用户名。"""
    req = urllib.request.Request(
        base + "/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return (json.loads(r.read()).get("principal") or {}).get("user_id")
    except Exception:
        return None


def patch_password(base: str, token: str, user_id: str, password: str) -> int:
    req = urllib.request.Request(
        base + f"/api/admin/users/{user_id}",
        data=json.dumps({"password": password}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def run_cli(db: Path, username: str, password: str, note_flag: str) -> int:
    env = dict(os.environ)
    env["GISDS_DB_PATH"] = str(db)
    # CLI 是独立进程，读的是 config.json；必须显式把开关传进去，
    # 否则它会按 config.json 里的 password_note=true 来写备忘。
    env["GISDS_PASSWORD_NOTE"] = note_flag
    proc = subprocess.run(
        ["cmd.exe", "/c", str(PROJECT_ROOT / "run-qgis-python.bat"), "-m", "gisds",
         "set-password", username, password],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=180)
    return proc.returncode


def case(note_flag: str, from_config: bool) -> None:
    """from_config=True 走「配置里配了密码」那档，否则走「随机生成」那档。"""
    src = "config" if from_config else "随机生成"
    print(f"\n== 口令来源：{src} · GISDS_PASSWORD_NOTE={note_flag} ==")
    env = {"GISDS_PASSWORD_NOTE": note_flag}
    if from_config:
        env["GISDS_ADMIN_PASSWORD"] = "configpass123"
    note_on = note_flag != "0"

    with TestServer(port=free_port(), require_login=True, extra_env=env) as server:
        db = Path(server.env()["GISDS_DB_PATH"])
        note = note_path(db)
        time.sleep(0.8)
        text = server_log(server.port)

        # ---- 1. 自举那一刻：来源决定落不落盘、打不打明文 -------------------
        if from_config:
            pwd = "configpass123"
            check("配置来的口令没有打进日志", "configpass123" not in text)
            check("配置来的口令没有落盘（配置里已经是副本）", not note.exists(), str(note))
        else:
            pwd = generated_password(server.port)
            check("随机口令在启动日志里", bool(pwd), str(pwd))
            if note_on:
                check("开关开 + 随机生成 -> 落盘", note.is_file(), str(note))
                check("备忘内容就是日志里那个随机口令",
                      bool(pwd) and pwd in note_body(note))
            else:
                check("开关关 + 随机生成 -> 不落盘", not note.exists(), str(note))

        # ---- 2. 管理员改密 API：备忘要同步更新 ----------------------------
        token = admin_token(server.base, "admin", pwd or "")
        check("能登录并拿到令牌", bool(token))
        # 下面这几条**无条件执行**：条件不满足就让断言**失败**，而不是整段跳过
        # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
        uid = user_id_of(server.base, token) if token else ""
        check("拿到了 user_id", bool(uid), str(uid))
        status = patch_password(server.base, token, uid, "secondpass456") if (token and uid) else 0
        check("改密接口返回 200", status == 200, str(status))
        if note_on:
            # config 那份已过期 -> 备忘是当前口令唯一的明文副本 -> 必须写
            check("改密后备忘已同步写入", note.is_file(), str(note))
            body = note_body(note)
            check("备忘记着改密后的新口令", "secondpass456" in body)
            check("备忘里不再留着旧口令",
                  bool(pwd) and pwd not in body, f"旧口令 {pwd!r}")
            check("备忘里写明了用户名（人能看懂）", "admin" in body)
        else:
            check("开关关 -> 改密后备忘仍不落盘", not note.exists(), str(note))

        # ---- 3. CLI 重置：同一套口径 --------------------------------------
        rc = run_cli(db, "admin", "thirdpass789", note_flag)
        check("set-password 退出码 0", rc == 0, str(rc))
        if note_on:
            check("CLI 之后备忘已同步写入", note.is_file(), str(note))
            body = note_body(note)
            check("备忘记着 CLI 那个新口令", "thirdpass789" in body)
            check("备忘里不再留着上一轮的口令", "secondpass456" not in body)
        else:
            check("开关关 -> CLI 之后备忘仍不落盘", not note.exists(), str(note))


def case_note_conflict() -> None:
    """配置里的 admin_password 跟库里当前口令对不上时，启动要告警。

    这是「配置里那个过期密码同样是误导」的护栏：照着 config 登不进去，
    而账号状态、服务日志看上去全都正常，最难查。
    """
    print("\n== 配置口令与库里不一致时要告警 ==")
    import tempfile

    from gisds.core.auth import AuthService
    from gisds.core.db import Database

    tmp = Path(tempfile.mkdtemp(prefix="gisds_noteconflict_"))
    store = Database(tmp / "gisds.db")
    auth = AuthService(store)
    user = auth.register("admin", "configpass123", role="admin")
    # 管理员后来改了密码 → config 里那份已经过期
    auth.set_password(user["id"], "actualpass456")

    with TestServer(
        port=free_port(), data_root=tmp, require_login=True,
        extra_env={"GISDS_ADMIN_PASSWORD": "configpass123"},
    ) as server:
        time.sleep(1.0)
        text = server_log(server.port)
        check("启动日志里有「不一致」告警", "不一致" in text,
              text.strip().splitlines()[-1][:80] if text.strip() else "日志为空")
        check("告警说明了以数据为准", "以数据为准" in text)


def main() -> int:
    prod = PROJECT_ROOT / "data" / "admin_password.txt"
    before = prod.read_bytes() if prod.exists() else b""

    case("0", from_config=True)      # 配置里配了口令
    case("1", from_config=True)
    case("0", from_config=False)     # 随机生成
    case("1", from_config=False)
    case_note_conflict()

    after = prod.read_bytes() if prod.exists() else b""
    check("生产口令备忘未被触碰", before == after)

    # 断言总数必须钉死：少一条就说明有断言被条件跳过了。
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          checked == EXPECTED_CHECKS, str(checked))
    total = checked
    print(f"\n== 结果：{total - len(fails)} 通过 / {len(fails)} 失败 ==")
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
