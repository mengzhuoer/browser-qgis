"""插件接口。

前缀 ``/api/plugins``。清单（面板 / 菜单声明）对所有登录用户可见 ——
网页要靠它把插件贡献的面板画到右栏、把菜单项挂到菜单栏。

**装 / 启停 / 卸载只有管理员能做**：插件是服务端代码，装上去等于
在数据服务器上放了一段任意代码。
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile

from ..core import plugins as plugins_core
from ..core.auth import Principal
from ..core.errors import InvalidRequestError, NotFoundError
from ..core.executor import run_qgis
from .deps import current_principal, get_config

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _manager() -> plugins_core.PluginManager:
    # 插件目录由配置给（核心层不读配置）——
    # 传进去还能让管理器跟着目录走：测试里每个临时服务的插件目录都不同。
    return plugins_core.get_manager(get_config().plugin_dir_path)


def _require_admin(principal: Principal | None) -> None:
    if principal is None or not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员能管理插件")


@router.get("")
def list_plugins(
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """插件清单 + 它们贡献的面板和菜单项。"""
    manager = _manager()
    manager.discover()
    return {
        "plugins": manager.list(),
        "panels": manager.panels(),
        "menus": manager.menus(),
        "can_manage": bool(principal and principal.is_admin),
    }


@router.post("/install")
async def install_plugin(
    file: UploadFile = File(...),
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """上传一个 zip 装上插件。"""
    _require_admin(principal)
    data = await file.read()
    try:
        record = await run_qgis(_install, data, file.filename or "")
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"安装失败：{exc}") from exc
    return {"ok": True, "plugin": record.to_dict()}


def _install(data: bytes, filename: str) -> plugins_core.PluginRecord:
    manager = _manager()
    record = manager.install_zip(data, filename)
    # 装完就加载（加载要在 QGIS 线程上）
    if record.enabled:
        manager.load(record)
    return record


@router.post("/{slug}/enable")
async def enable_plugin(
    slug: str,
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """启用插件。"""
    _require_admin(principal)
    try:
        record = await run_qgis(_set_enabled, slug, True)
    except KeyError as exc:
        raise NotFoundError(f"没有这个插件：{slug}") from exc
    return {"ok": True, "plugin": record.to_dict()}


@router.post("/{slug}/disable")
async def disable_plugin(
    slug: str,
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """停用插件（代码还留着，只是不加载）。"""
    _require_admin(principal)
    try:
        record = await run_qgis(_set_enabled, slug, False)
    except KeyError as exc:
        raise NotFoundError(f"没有这个插件：{slug}") from exc
    return {"ok": True, "plugin": record.to_dict()}


@router.post("/{slug}/reload")
async def reload_plugin(
    slug: str,
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """重载插件（改完代码不用重启服务）。"""
    _require_admin(principal)
    try:
        record = await run_qgis(_reload, slug)
    except KeyError as exc:
        raise NotFoundError(f"没有这个插件：{slug}") from exc
    return {"ok": True, "plugin": record.to_dict()}


@router.post("/{slug}/uninstall")
async def uninstall_plugin(
    slug: str,
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """卸载插件（删掉它的目录）。"""
    _require_admin(principal)
    try:
        await run_qgis(_uninstall, slug)
    except KeyError as exc:
        raise NotFoundError(f"没有这个插件：{slug}") from exc
    return {"ok": True}


@router.post("/{slug}/action")
async def plugin_action(
    slug: str,
    body: dict = Body(default_factory=dict),
    request: Request = None,  # type: ignore[assignment]
    principal: Principal | None = Depends(current_principal),
) -> Any:
    """调用插件注册的动作。"""
    action = str(body.get("action") or "").strip()
    if not action:
        raise InvalidRequestError("要指定 action")
    params = body.get("params")
    if not isinstance(params, dict):
        params = {}
    manager = _manager()
    record = manager.records.get(slug)
    if record is None:
        raise NotFoundError(f"没有这个插件：{slug}")
    fn = record.actions.get(action)
    if fn is None:
        raise NotFoundError(
            f"插件 {slug} 没有这个动作：{action}",
            {"available": sorted(record.actions.keys())},
        )
    try:
        result = _call_action(fn, params, principal)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"动作执行失败：{exc}") from exc
    return result


def _call_action(fn: Any, params: dict, principal: Principal | None) -> Any:
    """调插件的动作，把**调用者身份**也传过去。

    动作回调原来只收 ``params``，插件根本不知道是谁在调它 —— 想自己做权限判断
    也没法做（插件自己注册的接口是收 ``principal`` 的，只有动作这一处不一致）。

    按签名决定传不传第二个参数：**只收一个参数的旧插件照常能用**，
    写成 ``def action(self, params, principal)`` 的就能拿到调用者。
    """
    try:
        signature = inspect.signature(fn)
        positional = [
            p
            for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):
        positional = []
    if len(positional) >= 2:
        return fn(params, principal)
    return fn(params)


# --------------------------------------------------------------------- 内部


class _RouteRegistrar:
    """把插件的接口挂到 FastAPI 上，插件停用时再摘掉。

    放在 API 层而不是核心层，是为了不让 ``core/plugins.py`` 认识 FastAPI
    （核心层只管插件，HTTP 的事归这里）。
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.counter = 0
        #: slug -> 它注册过的路由名。**精确认领**用，不靠名字猜。
        self._by_slug: dict[str, list[str]] = {}

    def register(self, path: str, handler: Any, methods: Any, slug: str) -> None:
        async def _endpoint(
            request: Request,
            principal: Principal = Depends(current_principal),
        ) -> Any:
            result = handler(request, principal)
            if inspect.isawaitable(result):
                result = await result
            return result

        self.counter += 1
        name = f"gisds_plugin_{slug}_{self.counter}"
        self.app.router.add_api_route(
            path,
            _endpoint,
            methods=list(methods),
            name=name,
        )
        self._by_slug.setdefault(slug, []).append(name)

    def unregister(self, slug: str) -> int:
        """把这个插件注册过的接口全摘掉，返回摘了几个。

        **按登记时记下的名字精确摘**，不是按前缀猜。
        原来用 ``gisds_plugin_<slug>_`` 前缀匹配，而 slug 本身可以含下划线：
        停用插件 ``a`` 时，插件 ``a_b`` 的路由名 ``gisds_plugin_a_b_2``
        也以 ``gisds_plugin_a_`` 开头 → 被连坐摘掉（``a_b`` 的接口变 404，
        而清单里它还显示 loaded=true —— 状态与实际不一致）。
        """
        names = set(self._by_slug.pop(slug, []))
        if not names:
            return 0
        keep = []
        removed = 0
        for route in list(self.app.router.routes):
            if str(getattr(route, "name", "") or "") in names:
                removed += 1
                continue
            keep.append(route)
        if removed:
            self.app.router.routes[:] = keep
        return removed


def make_route_registrar(app: Any) -> _RouteRegistrar:
    """造一个插件路由登记器。"""
    return _RouteRegistrar(app)


def _set_enabled(slug: str, enabled: bool) -> plugins_core.PluginRecord:
    return _manager().set_enabled(slug, enabled)


def _reload(slug: str) -> plugins_core.PluginRecord:
    manager = _manager()
    manager.discover()
    record = manager.records.get(slug)
    if record is None:
        raise KeyError(slug)
    return manager.reload(record)


def _uninstall(slug: str) -> None:
    manager = _manager()
    manager.discover()
    if slug not in manager.records:
        raise KeyError(slug)
    manager.uninstall(slug)
