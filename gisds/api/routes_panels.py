"""面板接口：QGIS 桌面版各 dock 面板对应的 REST 端点。

前缀 ``/api/panels``，与既有的 ``/api/vectors`` / ``/api/processing`` 分开，
方便网页端按面板取数。读接口只要求能看见图层；写接口（编辑要素、书签）
按数据空间判权限。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..core import bookmarks as bookmarks_core
from ..core import logbuf
from ..core import panels as panels_core
from ..core.auth import Principal
from ..core.errors import InvalidRequestError, NotFoundError
from ..core.executor import run_qgis
from ..core.tenancy import SHARED, scope_label
from .deps import current_principal, get_config, get_registry

router = APIRouter(prefix="/api/panels", tags=["panels"])


# --------------------------------------------------------------------- 工具


def _vector_by_ref(ref: str, principal: Principal | None):
    """按 ``数据集:图层`` 取矢量图层（含空间信息，用于写权限判断）。"""
    registry = get_registry()
    if not ref or ":" not in ref:
        raise InvalidRequestError("ref 需要是「数据集:图层」形式")
    dataset_id, _, layer_id = ref.partition(":")
    dataset = registry.get_dataset(dataset_id, principal)
    layer = dataset.vectors.get(layer_id)
    if layer is None:
        if layer_id in dataset.layers:
            raise InvalidRequestError(
                f"「{ref}」是栅格图层，不能用矢量面板",
                {"kind": "raster"},
            )
        raise NotFoundError(
            f"找不到矢量图层：{ref}",
            {"available": sorted(dataset.vectors.keys())},
        )
    return layer


def _require_writable(layer, principal: Principal | None) -> None:
    """编辑会写回数据文件，所以要检查该空间的写权限。"""
    if principal is None or not principal.can_write(layer.scope):
        raise HTTPException(
            status_code=403,
            detail=f"这个空间（{scope_label(layer.scope)}）你没有写入权限",
        )


def _scope_for(principal: Principal | None, requested: str | None) -> str:
    """书签面板的默认空间：自己的空间；免登录模式只有共享空间。"""
    if requested:
        scope = requested
    elif principal is None or principal.shared_only:
        scope = SHARED
    else:
        scope = principal.own_scope()
    if principal is not None and not principal.can_see(scope):
        raise HTTPException(status_code=403, detail="这个空间你看不到")
    return scope


# --------------------------------------------------------------------- 读


@router.get("/table")
async def table(
    ref: str = Query(..., description="数据集:图层"),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    where: str | None = Query(None, description="QGIS 属性过滤表达式"),
    sort: str | None = Query(None, description="按这个字段排序"),
    order: str = Query("asc", description="asc | desc"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """属性表面板。"""
    layer = _vector_by_ref(ref, principal)
    return await run_qgis(
        panels_core.attribute_table, layer, limit, offset, where, None, sort, order
    )


@router.get("/statistics")
async def statistics(
    ref: str = Query(..., description="数据集:图层"),
    field: str | None = Query(None, description="只统计某一个字段；缺省统计全部字段"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """统计面板。"""
    layer = _vector_by_ref(ref, principal)
    return await run_qgis(panels_core.field_statistics, layer, field)


@router.get("/overview")
async def overview(
    ref: str = Query(..., description="数据集:图层"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """「信息」面板：图层关键信息一览。"""
    layer = _vector_by_ref(ref, principal)
    return await run_qgis(panels_core.layer_overview, layer)


@router.get("/logs")
async def logs(
    limit: int = Query(200, ge=1, le=800),
    level: str | None = Query(None, description="只看某个级别：DEBUG / INFO / WARNING / ERROR"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """日志消息面板。

    **要登录**：日志里有绝对路径、用户名、内部报错 —— 匿名读到这些
    等于把服务端内部信息摊开。
    """
    items = logbuf.tail(limit, level)
    return {
        "count": len(items),
        "entries": [
            {**item, "text": logbuf.format_line(item)} for item in items
        ],
    }


@router.delete("/logs")
async def clear_logs(principal: Principal = Depends(current_principal)) -> dict:
    """清空日志缓冲。"""
    return {"cleared": logbuf.clear()}


# --------------------------------------------------------------------- 选择


@router.post("/select")
async def select(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """按表达式 / 按空间关系选择要素。

    body::

        {"ref": "数据集:图层", "expression": "面积 > 100"}
        {"ref": "数据集:图层", "geometry": {...GeoJSON...}, "predicate": "intersects"}

    ``predicate`` 可选 intersects / contains / within / touches / crosses /
    overlaps / disjoint。
    """
    ref = str(body.get("ref") or "")
    layer = _vector_by_ref(ref, principal)

    if str(body.get("mode") or "").lower() == "all":
        return await run_qgis(panels_core.select_all, layer)

    expression = body.get("expression")
    if expression:
        return await run_qgis(panels_core.select_by_expression, layer, str(expression))

    target_ref = body.get("target_ref")
    if target_ref:
        target = _vector_by_ref(str(target_ref), principal)
        predicate = str(body.get("predicate") or "intersects")
        return await run_qgis(panels_core.select_by_layer, layer, target, predicate)

    geometry = body.get("geometry")
    if geometry:
        predicate = str(body.get("predicate") or "intersects")
        mode = str(body.get("mode") or "replace")
        return await run_qgis(panels_core.select_by_spatial, layer, geometry, predicate, mode)

    raise InvalidRequestError("要么给 expression，要么给 geometry，要么给 target_ref，要么 mode=all")


# --------------------------------------------------------------------- 书签


@router.get("/bookmarks")
async def list_bookmarks(
    scope: str | None = Query(None, description="数据空间；缺省是自己的空间"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """空间书签面板。"""
    target = _scope_for(principal, scope)
    items = bookmarks_core.list_bookmarks(get_config().data_root_path, target)
    return {
        "scope": target,
        "scope_label": scope_label(target),
        "count": len(items),
        "bookmarks": items,
    }


@router.post("/bookmarks")
async def create_bookmark(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """新建空间书签。"""
    target = _scope_for(principal, body.get("scope"))
    try:
        entry = bookmarks_core.add_bookmark(
            get_config().data_root_path,
            target,
            str(body.get("name") or ""),
            body.get("extent") or [],
            str(body.get("crs") or "EPSG:4326"),
            str(body.get("description") or ""),
        )
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return {"scope": target, "bookmark": entry}


@router.delete("/bookmarks")
async def delete_bookmark(
    name: str = Query(...),
    scope: str | None = Query(None),
    principal: Principal = Depends(current_principal),
) -> dict:
    """删除空间书签。"""
    target = _scope_for(principal, scope)
    removed = bookmarks_core.remove_bookmark(get_config().data_root_path, target, name)
    if not removed:
        raise NotFoundError(f"书签不存在：{name}")
    return {"scope": target, "name": name, "removed": True}


# --------------------------------------------------------------------- 编辑


@router.post("/edit/start")
async def edit_start(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """进入编辑模式。"""
    layer = _vector_by_ref(str(body.get("ref") or ""), principal)
    _require_writable(layer, principal)
    return await run_qgis(panels_core.start_edit, layer)


@router.post("/edit/commit")
async def edit_commit(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """保存编辑（写回数据文件）。"""
    layer = _vector_by_ref(str(body.get("ref") or ""), principal)
    _require_writable(layer, principal)
    return await run_qgis(panels_core.commit_edit, layer)


@router.post("/edit/rollback")
async def edit_rollback(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """放弃编辑。"""
    layer = _vector_by_ref(str(body.get("ref") or ""), principal)
    _require_writable(layer, principal)
    return await run_qgis(panels_core.rollback_edit, layer)


@router.patch("/edit/feature")
async def edit_feature(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """改要素属性。"""
    layer = _vector_by_ref(str(body.get("ref") or ""), principal)
    _require_writable(layer, principal)
    fid = body.get("fid")
    if fid is None:
        raise InvalidRequestError("缺少 fid")
    attributes = body.get("attributes")
    if not isinstance(attributes, dict) or not attributes:
        raise InvalidRequestError("attributes 不能为空")
    return await run_qgis(panels_core.update_feature, layer, int(fid), attributes)


@router.post("/edit/feature")
async def add_feature(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """新增要素（几何是 GeoJSON）。"""
    layer = _vector_by_ref(str(body.get("ref") or ""), principal)
    _require_writable(layer, principal)
    return await run_qgis(
        panels_core.add_feature,
        layer,
        body.get("geometry"),
        body.get("attributes") or {},
    )


@router.delete("/edit/feature")
async def delete_feature(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """删除要素。"""
    layer = _vector_by_ref(str(body.get("ref") or ""), principal)
    _require_writable(layer, principal)
    fids = body.get("fids")
    if fids is None and body.get("fid") is not None:
        fids = [body.get("fid")]
    return await run_qgis(panels_core.delete_features, layer, fids or [])


@router.post("/edit/calculator")
async def edit_calculator(
    body: dict = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """字段计算器：按表达式批量赋值。"""
    layer = _vector_by_ref(str(body.get("ref") or ""), principal)
    _require_writable(layer, principal)
    return await run_qgis(
        panels_core.field_calculator,
        layer,
        str(body.get("field") or ""),
        str(body.get("expression") or ""),
        bool(body.get("only_selected")),
        body.get("selection") or [],
    )


# --------------------------------------------------------------------- 能力


@router.get("/capabilities")
async def capabilities(principal: Principal = Depends(current_principal)) -> dict:
    """网页端启动时拉一次：知道有哪些面板可用。"""
    registry = get_registry()
    layers = []
    for dataset in registry.datasets(principal):
        for layer in dataset.vectors.values():
            layers.append(
                {
                    "ref": layer.ref,
                    "title": layer.title,
                    "scope": layer.scope,
                    "feature_count": layer.feature_count,
                }
            )
    return {
        "panels": [
            "table",
            "statistics",
            "selections",
            "bookmarks",
            "logs",
            "history",
            "overview",
        ],
        "vector_layers": layers,
    }
