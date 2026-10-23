"""启动器与服务端之间的 HTTP 通道。

只用标准库（urllib），不需要 QGIS，也不装任何第三方包。
令牌存在模块级变量里，所以启动器主窗口和独立的文件浏览器窗口共享同一份登录态。
"""

from __future__ import annotations

import json
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
LOG_DIR = PROJECT_ROOT / "logs"


def load_config() -> dict:
    cfg = {
        "host": "0.0.0.0",
        "port": 8848,
        "require_login": True,
        "allow_registration": False,
        "admin_username": "admin",
        "max_upload_mb": 512,
    }
    if CONFIG_PATH.is_file():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            # 配置坏了不该让启动器起不来，但**不许裸吞** ——
            # 静默退回默认值会让人以为「配置生效了」，排查时最难查。
            # 服务端那边（gisds/config.py）也是这个做法：打印出来，不吞。
            print(f"[gisds-launcher] 配置文件解析失败，忽略：{CONFIG_PATH} ({exc})",
                  file=sys.stderr)
    return cfg


CFG = load_config()
BASE = f"http://127.0.0.1:{CFG.get('port', 8848)}"

TOKEN: str | None = None


def set_token(token: str | None) -> None:
    global TOKEN
    TOKEN = token


def _encode_url(url: str) -> str:
    """把 URL 里的非 ASCII 字符按 UTF-8 百分号编码。

    数据集名可能带中文（用户传的「寿阳土地利用」），而 urllib **只能发 ASCII 的
    URL** —— 不编码就直接抛 `UnicodeEncodeError`，文件浏览器里双击/下载中文数据集
    就崩。这里只动非 ASCII 的字节（逐字符 UTF-8 百分号编码），路径结构
    （``/ ? & =``）原样保留，所以 query 串不受影响。

    服务端那边是标准 FastAPI 路径参数，会自己把 %XX 解回来。
    """
    out = []
    for ch in url:
        if ord(ch) < 128:
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    return "".join(out)


def api(method: str, path: str, params: dict | None = None, body=None, files=None,
        timeout: float = 30):
    """调服务端接口（只用标准库）。

    返回 (状态码, 响应体 bytes)。
    """
    url = _encode_url(BASE + path)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    data = None
    if files:
        boundary = "----gisdslauncher0123456789"
        chunks: list[bytes] = []
        for field, filename, content in files:
            ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode("utf-8")
            )
            chunks.append(content)
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        data = b"".join(chunks)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def api_json(method: str, path: str, **kw):
    status, raw = api(method, path, **kw)
    if status == 401:
        # 把服务端的 detail 带出来：错密码时要说「用户名或密码不正确」，
        # 而不是笼统的「令牌已失效」—— 用户分不清自己打错了还是过期了，
        # 分不清就会一直重试。
        raise PermissionError(_detail_of(raw) or "需要登录或令牌已失效")
    if status >= 400:
        raise RuntimeError(_detail_of(raw) or f"HTTP {status}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        # 响应体不是 JSON（比如 204 空体或纯文本）→ 原样返回，不是错误
        return raw


def _detail_of(raw: bytes) -> str:
    """从错误响应里取出 detail。取不到就空着，不要拿异常盖住异常。"""
    if not raw:
        return ""
    try:
        return json.loads(raw).get("detail") or ""
    except (ValueError, TypeError, AttributeError):
        return ""


def do_login(root, log=None) -> dict | None:
    """弹窗登录，成功后把令牌存起来。返回用户信息，失败返回 None。"""
    from tkinter import simpledialog

    username = simpledialog.askstring(
        "登录", "用户名", initialvalue=CFG.get("admin_username", "admin"), parent=root
    )
    if not username:
        return None
    password = simpledialog.askstring("登录", "密码", show="*", parent=root)
    if not password:
        return None
    try:
        data = api_json("POST", "/api/auth/login",
                        body={"username": username, "password": password})
    except Exception as exc:
        if log:
            log(f"登录失败：{exc}")
        return None
    set_token(data.get("token"))
    user = data.get("user", {}) or {}
    if log:
        log(f"已登录：{username}（{user.get('role')}）")
    return user


def login_with_service_key(key: str, log=None) -> dict | None:
    """用**服务级密钥**（服务端 config.json 里的 api_key）登录。

    服务端把带这个密钥的请求按管理员算（见 api/deps.py），所以它相当于
    「服务密钥后门」：多机部署时管理员人在另一台机器上，忘了 admin 密码
    就没法远程进 —— 只能到数据机上跑 `gisds set-password`。
    这个入口就是那条远程救砖的路。

    密钥本身在**服务端那台机器**的 config.json 里，管理员要用安全渠道拿到它。
    """
    key = (key or "").strip()
    if not key:
        return None
    set_token(key)
    try:
        user = current_user()
    except Exception as exc:
        # 服务没连上 / 服务报 500 —— 这跟「密钥无效」是两回事，别说成后者
        set_token(None)
        if log:
            log(f"服务密钥登录失败：{exc}")
        return None
    if not user:
        set_token(None)
        if log:
            log("服务密钥无效")
        return None
    if not user.get("is_admin"):
        # 不是管理员就没意义，当成失败处理
        set_token(None)
        if log:
            log("服务密钥不是管理员身份")
        return None
    if log:
        log(f"已用服务密钥登录（{user.get('username')}，管理员）")
    return user


def current_user():
    """当前登录用户；没登录返回 None。

    只有「确实没登录 / 令牌失效」（401）才返回 None。网络断了、服务报 500
    这些**不是**「没登录」，要让异常冒出去 —— 否则界面会把服务故障显示成
    「未登录」，把「没报错」冒充成「对」。
    """
    try:
        data = api_json("GET", "/api/auth/me")
    except PermissionError:
        return None
    return (data or {}).get("principal")


def available_scopes():
    """当前用户能看到的数据空间列表。"""
    try:
        data = api_json("GET", "/api/auth/me")
    except PermissionError:
        return []
    return (data or {}).get("scopes") or []
