"""地图渲染与瓦片接口。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Depends, HTTPException

from ..core.auth import Principal
from fastapi.responses import Response

from ..core import render as render_core
from ..core.errors import NotFoundError
from ..core.executor import run_qgis
from .deps import get_registry, parse_bbox, current_principal

router = APIRouter(prefix="/api", tags=["render"])

_PNG_HEADERS = {"Cache-Control": "public, max-age=300"}


def _png(data: bytes) -> Response:
    return Response(content=data, media_type="image/png", headers=_PNG_HEADERS)


def _parse_colors(colors: str | None) -> dict[str, str] | None:
    """解析 ``colors`` 查询参数（分类图层的自定义配色 JSON）。"""
    if not colors:
        return None
    try:
        parsed = json.loads(colors)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        return {str(k): str(v) for k, v in parsed.items()}
    return None


@router.get("/render/raster/{dataset_id}/{layer_id}.png")
async def render_raster(
    dataset_id: str,
    layer_id: str,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy（经纬度）"),
    width: int = Query(1024, ge=64, le=4096),
    height: int | None = Query(None, ge=64, le=4096),
    dst_crs: str = Query("EPSG:4326"),
    colors: str | None = Query(
        None, description='自定义配色 JSON，如 {"1":"#ff0000"}（离散型分类图层）'
    ),
    mode: str | None = Query(None, description="连续型渲染模式：viridis / gray / spectral"), principal: Principal = Depends(current_principal)) -> Response:
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    box = parse_bbox(bbox)
    color_map = _parse_colors(colors)
    data, _meta = await run_qgis(
        render_core.render_raster_png,
        layer,
        box,
        width,
        height,
        dst_crs,
        True,
        color_map,
        mode,
    )
    return _png(data)


@router.get("/render/vector/{dataset_id}/{vector_id}.png")
async def render_vector(
    dataset_id: str,
    vector_id: str,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy（经纬度）"),
    width: int = Query(1024, ge=64, le=4096),
    height: int | None = Query(None, ge=64, le=4096),
    dst_crs: str = Query("EPSG:4326"), principal: Principal = Depends(current_principal)) -> Response:
    registry = get_registry()
    layer = registry.get_vector(dataset_id, vector_id, principal)
    box = parse_bbox(bbox)
    data, _meta = await run_qgis(
        render_core.render_vector_png, layer, box, width, height, dst_crs
    )
    return _png(data)


@router.get("/render/combined/{dataset_id}.png")
async def render_combined(
    dataset_id: str,
    layers: str = Query(..., description="栅格图层 id，逗号分隔"),
    vectors: str | None = Query(None, description="矢量图层 id，逗号分隔"),
    bbox: str | None = Query(None),
    width: int = Query(1024, ge=64, le=4096),
    height: int | None = Query(None, ge=64, le=4096),
    dst_crs: str = Query("EPSG:4326"), principal: Principal = Depends(current_principal)) -> Response:
    registry = get_registry()
    dataset = registry.get_dataset(dataset_id, principal)
    raster_layers = []
    for layer_id in [x for x in layers.split(",") if x.strip()]:
        candidate = dataset.layers.get(layer_id.strip())
        if candidate is not None:
            raster_layers.append(candidate)

    vector_layers = []
    for layer_id in [x for x in (vectors or "").split(",") if x.strip()]:
        candidate = dataset.vectors.get(layer_id.strip())
        if candidate is not None:
            vector_layers.append(candidate)

    if not raster_layers and not vector_layers:
        from ..core.errors import NotFoundError

        raise NotFoundError(
            f"数据集 {dataset_id} 下没有匹配的图层",
            {"layers": layers, "vectors": vectors},
        )

    box = parse_bbox(bbox)
    data, _meta = await run_qgis(
        render_core.render_combined_png,
        raster_layers,
        vector_layers,
        box,
        width,
        height,
        dst_crs,
    )
    return _png(data)


def _basemap_vectors(registry) -> tuple[list, list[str]]:
    """找共享空间里的内置底图图层，按叠放次序返回。"""
    for dataset in registry.datasets():
        if dataset.id != "basemap":
            continue
        vectors, roles = [], []
        for role in render_core.BASEMAP_ORDER:
            vl = dataset.vectors.get(role)
            if vl is not None:
                vectors.append(vl)
                roles.append(role)
        if vectors:
            return vectors, roles
    return [], []


@router.get("/tiles/basemap/status")
async def basemap_status() -> dict:
    """内置底图是否已经装好（前端据此决定要不要默认开底图）。"""
    registry = get_registry()
    vectors, roles = _basemap_vectors(registry)
    return {
        "available": bool(vectors),
        "layers": roles,
        "max_native_zoom": 9,
        "source": "Natural Earth（公共领域）",
    }


def _check_tile_range(z: int, x: int, y: int) -> None:
    """校验瓦片坐标 —— 越界要报 422，不能让服务端 500。

    原来 z / y 是**无约束**的路径参数，越界时 ``core.render.tile_bbox_3857``
    里的 ``math.sinh`` 直接 OverflowError → 500，traceback 还很难看懂
    （真出过：``/api/tiles/example/classes/-30/0/0.png`` → 500）。
    """
    if not (0 <= z <= 22):
        raise HTTPException(status_code=422, detail=f"瓦片层级超出范围：z={z}（允许 0~22）")
    limit = 2**z
    if not (0 <= x < limit) or not (0 <= y < limit):
        raise HTTPException(
            status_code=422,
            detail=f"瓦片行列超出范围：z={z} 时 x / y 必须在 0 ~ {limit - 1} 之间",
        )


@router.get("/tiles/basemap/{z}/{x}/{y}.png")
async def basemap_tile(z: int, x: int, y: int) -> Response:
    """内置离线底图瓦片：由本服务把世界矢量数据渲染成 XYZ 瓦片。

    **这个端点刻意不做鉴权**，别顺手加上去。

    前端是用 Leaflet 的 ``L.tileLayer("/api/tiles/basemap/{z}/{x}/{y}.png")`` 加载的，
    它发的是普通 ``<img>`` 请求 —— **带不了 Authorization 头**。所以这个端点一旦
    要求登录，登录态下底图会「401 → 拿到一段 JSON → 静默白屏」，
    跟栅格图层那次（裸 fetch 不带令牌）是同一类故障，而且同样**不报任何错**。

    底图是 Natural Earth 公共领域数据，瓦片里没有任何用户数据，所以这里公开是
    有意的取舍。``tests/check_tiles.py`` 里有断言钉住这个性质
    （登录态下不带 token 也要能取到瓦片），改了这里会立刻红。
    """
    _check_tile_range(z, x, y)
    registry = get_registry()
    vectors, roles = _basemap_vectors(registry)
    if not vectors:
        raise NotFoundError(
            "还没有安装内置底图",
            {"hint": "在服务端执行 gisds basemap 下载 Natural Earth 底图数据"},
        )
    data, _meta = await run_qgis(
        render_core.render_basemap_tile, vectors, roles, z, x, y, True
    )
    return _png(data)


@router.get("/tiles/{dataset_id}/{layer_id}/{z}/{x}/{y}.png")
async def tile(
    dataset_id: str,
    layer_id: str,
    z: int,
    x: int,
    y: int,
    cache: bool = Query(True),
    colors: str | None = Query(
        None, description='自定义配色 JSON，如 {"1":"#ff0000"}（离散型分类图层）'
    ),
    mode: str | None = Query(None, description="连续型渲染模式：viridis / gray / spectral"),
    principal: Principal = Depends(current_principal),
) -> Response:
    """渲染一张 Web 墨卡托瓦片（按层级出图，放大才看得清）。

    网页端用它铺栅格图层 —— 整层渲成一张大图再拉伸的话，放大必然糊。
    """
    _check_tile_range(z, x, y)
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    color_map = _parse_colors(colors)
    data, _meta = await run_qgis(
        render_core.render_tile, layer, z, x, y, cache, color_map, mode
    )
    return _png(data)
