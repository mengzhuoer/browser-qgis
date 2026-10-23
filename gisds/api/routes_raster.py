"""栅格查询接口。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Depends

from ..core.auth import Principal

from ..core import raster as raster_core
from ..core.executor import run_qgis
from .deps import bbox_params, get_registry, current_principal

router = APIRouter(prefix="/api/rasters", tags=["raster"])


@router.get("/{dataset_id}/{layer_id}")
async def layer_info(dataset_id: str, layer_id: str, principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    return layer.to_dict(detailed=True)


@router.get("/{dataset_id}/{layer_id}/point")
async def point_query(
    dataset_id: str,
    layer_id: str,
    lon: float = Query(..., description="经度"),
    lat: float = Query(..., description="纬度"),
    in_crs: str = Query("EPSG:4326", description="输入坐标所在坐标系"), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    return await run_qgis(raster_core.point_query, layer, lon, lat, in_crs)


@router.get("/{dataset_id}/{layer_id}/bbox")
async def bbox_stats(
    dataset_id: str,
    layer_id: str,
    minx: float | None = Query(None),
    miny: float | None = Query(None),
    maxx: float | None = Query(None),
    maxy: float | None = Query(None),
    in_crs: str = Query("EPSG:4326"),
    max_pixels: int = Query(4_000_000, ge=1_000), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    bbox = bbox_params(minx, miny, maxx, maxy)
    if bbox is None:
        ex = layer.extent_wgs84
        bbox = (ex[0], ex[1], ex[2], ex[3])
    return await run_qgis(raster_core.bbox_stats, layer, bbox, in_crs, max_pixels)


@router.post("/{dataset_id}/{layer_id}/polygon")
async def polygon_stats(
    dataset_id: str,
    layer_id: str,
    polygon: list[list[float]] = Body(..., description="[[lon, lat], ...] 顶点环"),
    max_pixels: int = Query(4_000_000, ge=1_000), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    return await run_qgis(raster_core.polygon_stats, layer, polygon, max_pixels)


@router.get("/{dataset_id}/{layer_id}/histogram")
async def histogram(
    dataset_id: str,
    layer_id: str,
    bins: int = Query(32, ge=2, le=256), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    return await run_qgis(raster_core.layer_histogram, layer, bins)


@router.get("/{dataset_id}/{layer_id}/area")
async def area(
    dataset_id: str,
    layer_id: str,
    minx: float | None = Query(None),
    miny: float | None = Query(None),
    maxx: float | None = Query(None),
    maxy: float | None = Query(None),
    in_crs: str = Query("EPSG:4326"),
    max_pixels: int = Query(4_000_000, ge=1_000), principal: Principal = Depends(current_principal)) -> dict:
    """地类面积统计（分类图层的核心指标）。"""
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    bbox = bbox_params(minx, miny, maxx, maxy)
    if bbox is None:
        ex = layer.extent_wgs84
        bbox = (ex[0], ex[1], ex[2], ex[3])
    stats = await run_qgis(raster_core.bbox_stats, layer, bbox, in_crs, max_pixels)
    return {
        "dataset_id": dataset_id,
        "layer_id": layer_id,
        "kind": layer.kind,
        "classes": stats.get("classes", []),
        "area_m2": stats.get("area_m2"),
        "valid_pixels": stats.get("valid_pixels"),
        "exact": stats.get("exact"),
        "area_method": stats.get("area_method"),
    }


@router.get("/{dataset_id}/{layer_id}/stats")
async def band_stats(dataset_id: str, layer_id: str, principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    layer = registry.get_raster(dataset_id, layer_id, principal)
    return await run_qgis(raster_core.layer_band_stats, layer)
