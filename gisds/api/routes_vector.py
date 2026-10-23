"""矢量查询接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Depends

from ..core.auth import Principal

from ..core import vector as vector_core
from ..core.executor import run_qgis
from .deps import get_registry, parse_bbox, current_principal

router = APIRouter(prefix="/api/vectors", tags=["vector"])


@router.get("/{dataset_id}/{vector_id}")
async def vector_info(dataset_id: str, vector_id: str, principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_vector(dataset_id, vector_id, principal)
    return layer.to_dict(detailed=True)


@router.get("/{dataset_id}/{vector_id}/features")
async def features(
    dataset_id: str,
    vector_id: str,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy（经纬度）"),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    where: str | None = Query(None, description="QGIS 属性过滤表达式"),
    keep_crs: bool = Query(False), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_vector(dataset_id, vector_id, principal)
    box = parse_bbox(bbox)
    return await run_qgis(vector_core.list_features, layer, box, limit, offset, where, keep_crs)


@router.get("/{dataset_id}/{vector_id}/stats")
async def stats(
    dataset_id: str,
    vector_id: str,
    bbox: str | None = Query(None),
    top_n: int = Query(10, ge=1, le=100), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_vector(dataset_id, vector_id, principal)
    box = parse_bbox(bbox)
    return await run_qgis(vector_core.vector_stats, layer, box, top_n)


@router.get("/{dataset_id}/{vector_id}/hit")
async def hit(
    dataset_id: str,
    vector_id: str,
    lon: float = Query(...),
    lat: float = Query(...),
    limit: int = Query(20, ge=1, le=200), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_vector(dataset_id, vector_id, principal)
    return await run_qgis(vector_core.vector_hit, layer, lon, lat, limit)
