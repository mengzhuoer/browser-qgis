"""数据集注册与概览接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Depends

from ..core.auth import Principal

from ..core import raster as raster_core
from ..core.executor import run_qgis
from .deps import get_registry, current_principal

router = APIRouter(prefix="/api", tags=["datasets"])


@router.get("/datasets")
async def list_datasets(source: str | None = Query(None, description="shared | workspace"), principal: Principal = Depends(current_principal)) -> dict:
    """列出全部数据集（服务器共享库 + 我的上传）。"""
    registry = get_registry()
    datasets = registry.datasets(principal)
    if source:
        datasets = [d for d in datasets if d.source == source]
    return {
        "count": len(datasets),
        "data_root": str(registry.data_root),
        "shared_root": str(registry.shared_root),
        "users_root": str(registry.users_root),
        "datasets": [d.to_dict() for d in datasets],
        "scan_errors": registry.scan_errors,
    }


@router.post("/datasets/rescan")
async def rescan(principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    await run_qgis(registry.scan)
    # **按调用者过滤**。``registry.scan()`` 返回的是**全部空间**的数据集
    # （含别人的私有数据集，以及服务器上的绝对路径）—— 直接返回等于
    # 任何登录用户点一下「重新扫描」就能看到所有人的数据。
    # 对比同文件的 GET /api/datasets：那边是过滤过的。
    datasets = [
        d for d in registry.datasets() if principal is None or principal.can_see(d.scope)
    ]
    return {
        "rescanned": True,
        "count": len(datasets),
        "datasets": [d.to_dict() for d in datasets],
        "scan_errors": registry.scan_errors,
    }


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    dataset = registry.get_dataset(dataset_id, principal)
    return dataset.to_dict(detailed=True)


@router.get("/datasets/{dataset_id}/overview")
async def dataset_overview(
    dataset_id: str, max_pixels: int = Query(2_000_000, ge=10_000), principal: Principal = Depends(current_principal)) -> dict:
    """每个分类图层的地类面积概览。"""
    registry = get_registry()
    dataset = registry.get_dataset(dataset_id, principal)

    def _work() -> dict:
        result = {
            "dataset_id": dataset.id,
            "name": dataset.name,
            "source": dataset.source,
            "extent_wgs84": list(dataset.extent_wgs84),
            "layers": [],
        }
        for layer in dataset.layers.values():
            entry = {
                "layer_id": layer.id,
                "ref": layer.ref,
                "title": layer.title,
                "kind": layer.kind,
                "year": layer.year,
            }
            if layer.kind == "categorical":
                minx, miny, maxx, maxy = layer.extent_wgs84
                try:
                    stats = raster_core.bbox_stats(
                        layer, (minx, miny, maxx, maxy), max_pixels=max_pixels
                    )
                    entry["classes"] = stats["classes"]
                    entry["area_m2"] = stats.get("area_m2")
                    entry["exact"] = stats.get("exact")
                except Exception as exc:
                    entry["error"] = str(exc)
            else:
                try:
                    entry["stats"] = raster_core.layer_band_stats(layer)
                except Exception as exc:
                    entry["error"] = str(exc)
            result["layers"].append(entry)

        for vector in dataset.vectors.values():
            result["layers"].append(
                {
                    "layer_id": vector.id,
                    "ref": vector.ref,
                    "title": vector.title,
                    "kind": "vector",
                    "feature_count": vector.feature_count,
                    "geometry_type": vector.geometry_type,
                }
            )
        return result

    return await run_qgis(_work)


@router.get("/layers")
async def list_layers(
    dataset: str | None = None,
    kind: str | None = Query(None, description="raster | vector"),
    source: str | None = Query(None, description="shared | workspace"),
    q: str | None = Query(None, description="按 id/标题模糊搜索"), principal: Principal = Depends(current_principal)) -> dict:
    """跨数据集检索图层（算法参数表单用的就是这份清单）。"""
    registry = get_registry()
    items = registry.layer_refs(principal)

    if dataset:
        items = [i for i in items if i["dataset_id"] == dataset]
    if source:
        items = [i for i in items if i["source"] == source]
    if kind:
        wanted = kind.lower()
        if wanted == "raster":
            items = [i for i in items if i["kind"] == "raster"]
        elif wanted == "vector":
            items = [i for i in items if i["kind"] == "vector"]
    if q:
        needle = q.lower()
        items = [
            i
            for i in items
            if needle in i["ref"].lower()
            or needle in (i["title"] or "").lower()
            or needle in (i["dataset_name"] or "").lower()
        ]

    return {"count": len(items), "layers": items}


@router.get("/layers/{dataset_id}/{layer_id}/fields")
async def layer_fields(dataset_id: str, layer_id: str, principal: Principal = Depends(current_principal)) -> dict:
    """矢量图层的字段清单（算法里选字段时要用）。"""
    registry = get_registry()
    try:
        layer = registry.get_vector(dataset_id, layer_id, principal)
    except Exception:
        # 也可能是栅格图层
        raster = registry.get_raster(dataset_id, layer_id, principal)
        return {
            "ref": raster.ref,
            "kind": "raster",
            "fields": [],
            "bands": raster.band_count,
        }
    return {
        "ref": layer.ref,
        "kind": "vector",
        "fields": layer.fields,
        "feature_count": layer.feature_count,
    }
