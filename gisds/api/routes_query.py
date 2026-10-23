"""综合查询：一次请求同时返回该点的栅格值与矢量命中。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Depends

from ..core.auth import Principal

from ..core import raster as raster_core
from ..core import vector as vector_core
from ..core.executor import run_qgis
from .deps import get_registry, current_principal

router = APIRouter(prefix="/api/query", tags=["query"])


@router.get("/point")
async def query_point(
    lon: float = Query(..., description="经度"),
    lat: float = Query(..., description="纬度"),
    dataset: str | None = Query(None, description="限定数据集；缺省则查全部"), principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()

    def _work() -> dict:
        rasters = []
        for layer in registry.all_rasters(principal):
            if dataset and layer.dataset_id != dataset:
                continue
            try:
                rasters.append(raster_core.point_query(layer, lon, lat))
            except Exception as exc:
                rasters.append(
                    {"dataset_id": layer.dataset_id, "layer_id": layer.id, "error": str(exc)}
                )

        vectors = []
        for layer in registry.all_vectors(principal):
            if dataset and layer.dataset_id != dataset:
                continue
            try:
                vectors.append(vector_core.vector_hit(layer, lon, lat))
            except Exception as exc:
                vectors.append(
                    {"dataset_id": layer.dataset_id, "layer_id": layer.id, "error": str(exc)}
                )

        return {
            "lon": lon,
            "lat": lat,
            "dataset": dataset,
            "rasters": rasters,
            "vectors": vectors,
        }

    return await run_qgis(_work)
