"""API 层的共享依赖：配置、注册中心、账号会话、上传工作区。"""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request

from ..config import Config, load_config
from ..core.auth import AuthService, Principal
from ..core.db import Database
from ..core.registry import Registry
from ..core.storage import Workspace
from ..core.tenancy import SHARED, user_scope

_CONFIG: Config | None = None
_REGISTRY: Registry | None = None
_AUTH: AuthService | None = None
_DB: Database | None = None


def get_config() -> Config:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def set_config(config: Config) -> None:
    global _CONFIG, _REGISTRY, _AUTH, _DB
    _CONFIG = config
    _REGISTRY = None
    _AUTH = None
    _DB = None


# --------------------------------------------------------------------- 账号

def get_db() -> Database:
    global _DB
    if _DB is None:
        _DB = Database(get_config().db_path_resolved)
    return _DB


def get_auth() -> AuthService:
    global _AUTH
    if _AUTH is None:
        _AUTH = AuthService(get_db())
    return _AUTH


def get_registry() -> Registry:
    """取注册中心。

    首次扫描会创建 QgsRasterLayer / QgsVectorLayer，这些是 QObject，
    必须在 QGIS 工作线程上创建，所以这里把扫描派到那条线程。
    """
    global _REGISTRY
    if _REGISTRY is None:
        config = get_config()
        _REGISTRY = Registry(
            config.data_root_path, config.shared_root_path, config.users_root_path
        )
        _scan_registry(_REGISTRY)
    return _REGISTRY


def _scan_registry(registry: Registry) -> None:
    from .. import qgis_env
    from ..core.executor import run_qgis_sync

    if qgis_env.in_qgis_thread():
        registry.scan()
    else:
        run_qgis_sync(registry.scan)


# --------------------------------------------------------------------- 空间

def get_workspace(scope: str = SHARED) -> Workspace:
    """按空间取上传工作区。"""
    config = get_config()
    if scope == SHARED:
        root = config.shared_root_path
    elif scope.startswith("user:"):
        root = config.users_root_path / scope.split(":", 1)[1]
    else:
        raise HTTPException(status_code=422, detail=f"未知的数据空间：{scope}")
    root.mkdir(parents=True, exist_ok=True)
    return Workspace(root)


def write_scope_for(principal: Principal | None, requested: str | None) -> str:
    """决定写到哪个空间。

    * 免登录模式：只能写共享空间
    * 普通用户：默认写自己的空间，也可以写共享空间（人人可写）
    * 管理员：可以写任意用户的空间
    """
    config = get_config()
    if principal is None or principal.shared_only:
        # 免登录模式：只有一个共享空间
        return SHARED
    target = requested or "me"
    if target in ("me", "personal", "user"):
        return user_scope(principal.user_id)
    if target == SHARED:
        return SHARED
    if target.startswith("user:"):
        if principal.is_admin or target == user_scope(principal.user_id):
            return target
        raise HTTPException(status_code=403, detail="不能写入他人的空间")
    raise HTTPException(status_code=422, detail=f"未知的数据空间：{target}")


# --------------------------------------------------------------------- 鉴权

def _token_from(request: Request, authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    if x_api_key:
        return x_api_key
    return request.query_params.get("token") or request.query_params.get("access_token")


def current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Principal | None:
    """解析当前调用者。

    - 带服务级 API Key 的按管理员算
    - 带会话令牌的按登录用户算
    - 开了 ``require_login`` 却没有身份 -> 401
    - 关掉 ``require_login``（免登录模式）-> 一个只能看共享空间的匿名身份
    """
    config = get_config()
    token = _token_from(request, authorization, x_api_key)

    if config.api_key and token and token == config.api_key:
        return Principal(
            user_id="__api_key__",
            username="api-key",
            role="admin",
            display_name="服务密钥",
            api_key=True,
        )

    if token:
        principal = get_auth().principal_from_token(token)
        if principal:
            return principal

    if config.require_login:
        raise HTTPException(
            status_code=401,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 免登录模式：只看得到共享空间
    return Principal(
        user_id="anonymous",
        username="anonymous",
        role="user",
        display_name="匿名访客",
        shared_only=True,
    )


def require_admin(principal: Principal | None = Header(default=None)) -> Principal:
    """管理员才能用的接口。"""
    if principal is None or not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal


# --------------------------------------------------------------------- 其它

async def check_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """可选的 API Key 校验（config.api_key 为空时不做任何限制）。"""
    expected = get_config().api_key
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="缺少或错误的 X-API-Key")


def bbox_params(
    minx: float | None = None,
    miny: float | None = None,
    maxx: float | None = None,
    maxy: float | None = None,
) -> tuple[float, float, float, float] | None:
    """把四个范围参数合成一个 bbox；缺任意一项则返回 None（表示不限范围）。"""
    values = (minx, miny, maxx, maxy)
    if any(v is None for v in values):
        return None
    minx, miny, maxx, maxy = values  # type: ignore[misc]
    if maxx <= minx or maxy <= miny:
        raise HTTPException(status_code=422, detail="bbox 无效：需要 maxx > minx 且 maxy > miny")
    return (float(minx), float(miny), float(maxx), float(maxy))


def parse_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    """解析 "minx,miny,maxx,maxy" 形式的范围字符串。"""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != 4:
        raise HTTPException(status_code=422, detail="bbox 格式应为 minx,miny,maxx,maxy")
    try:
        minx, miny, maxx, maxy = (float(p) for p in parts)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"bbox 含非法数值：{exc}") from exc
    if maxx <= minx or maxy <= miny:
        raise HTTPException(status_code=422, detail="bbox 无效：需要 maxx > minx 且 maxy > miny")
    return (minx, miny, maxx, maxy)


def public_dict(data: dict[str, Any]) -> dict[str, Any]:
    return data
