"""坐标参考系（CRS）接口 —— 网页端「项目属性 → 坐标参考系」那一页的后端。

四个端点：
* ``GET /api/crs``                —— 搜索（名称 / 编码 / 别名）
* ``GET /api/crs/predefined``      —— 预定义坐标参考系统表
* ``GET /api/crs/detail``         —— 单个 CRS 的详情（WKT / PROJ / 适用范围…）
* ``GET /api/crs/preview.png``    —— 适用范围预览图

凡是要问 QGIS 的（详情、预览）都走 ``run_qgis`` —— 所有 QGIS 调用必须在那唯一
的工作线程上，PyQGIS 不是线程安全的。

**这四个端点要登录**（``current_principal``）
------------------------------------------------
全项目只有两类接口不鉴权：``/api/meta`` 与 ``/healthz``（登录页自己就要用），
以及内置底图瓦片 ``/api/tiles/basemap/*``（那里有专门的说明：Leaflet 用
``<img>`` 加载瓦片，**带不了 Authorization 头**，公开是有意的取舍）。

CRS 这一套不在其中：它本来就是给网页端「坐标参考系」那一页用的，
网页端请求助手（``web/index.html`` 的 ``api()``）**本来就会带上令牌**，
所以这里要求登录不会影响页面；而 ``preview.png`` 是**真在渲染**的端点
（会占 QGIS 工作线程），让它对谁都敞开，等于把渲染能力白送出去。

以前这里是漏挂依赖（不是有意公开）—— 跟别处一比就露馅了。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..core import crs as crs_core
from ..core.auth import Principal
from ..core.errors import NotFoundError
from ..core.executor import run_qgis
from .deps import current_principal, get_registry

router = APIRouter(prefix="/api/crs", tags=["crs"])


@router.get("", summary="搜索坐标参考系")
async def search(
    search: str = Query("", description="名称 / 编码 / 别名；纯数字按 EPSG 码查"),
    limit: int = Query(60, ge=1, le=300),
    principal: Principal = Depends(current_principal),
):
    items = await run_qgis(crs_core.search_crs, search, limit)
    return {"count": len(items or []), "crs": items or []}


@router.get("/predefined", summary="预定义坐标参考系统表")
async def predefined(principal: Principal = Depends(current_principal)):
    items = await run_qgis(crs_core.predefined_crs)
    return {"count": len(items or []), "crs": items or []}


@router.get("/detail", summary="某个坐标参考系的详情")
async def detail(
    crs: str = Query(..., description="EPSG:4326"),
    principal: Principal = Depends(current_principal),
):
    auth, _, code = crs.partition(":")
    info = await run_qgis(crs_core.crs_detail, auth or "EPSG", code)
    if not info:
        raise NotFoundError(f"不认识的坐标系：{crs}")
    return info


@router.get("/preview.png", summary="坐标参考系适用范围预览图")
async def preview(
    crs: str = Query(..., description="EPSG:4326"),
    width: int = Query(360, ge=64, le=1200),
    height: int = Query(220, ge=64, le=1200),
    principal: Principal = Depends(current_principal),
):
    registry = get_registry()
    land = None
    for dataset in registry.datasets(principal):
        if dataset.id == "basemap":
            vl = dataset.vectors.get("land")
            if vl is not None:
                land = vl.layer
            break
    data, _meta = await run_qgis(crs_core.crs_preview_png, crs, land, width, height)
    return Response(content=data, media_type="image/png")
