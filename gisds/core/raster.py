"""栅格查询：点取值、范围统计、地类面积、直方图。

坐标系约定：对外一律用经纬度（WGS84 / EPSG:4326），
内部按图层自身 CRS 换算，图层是投影坐标就自动重投影。
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from ._blocks import block_to_numpy, mask_nodata
from .errors import DataError, InvalidRequestError
from .registry import RasterLayer, format_value

# WGS84 近似换算常数
_M_PER_DEG_LAT_BASE = 111132.92
_M_PER_DEG_LON_BASE = 111412.84


# --------------------------------------------------------------------- 坐标换算


def _make_transform(src_crs: str, dst_crs: str):
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext

    src = QgsCoordinateReferenceSystem(src_crs or "EPSG:4326")
    dst = QgsCoordinateReferenceSystem(dst_crs)
    if not src.isValid() or not dst.isValid() or src == dst:
        return None
    return QgsCoordinateTransform(src, dst, QgsCoordinateTransformContext())


def transform_point(layer, lon: float, lat: float, in_crs: str = "EPSG:4326") -> tuple[float, float]:
    """经纬度 -> 图层坐标系。"""
    tr = _make_transform(in_crs, layer.crs())
    if tr is None:
        return float(lon), float(lat)
    try:
        return tr.transform(float(lon), float(lat))
    except Exception as exc:
        raise InvalidRequestError(f"坐标转换失败：{exc}") from exc


def transform_bbox(layer, bbox: tuple[float, float, float, float], in_crs: str = "EPSG:4326"):
    """经纬度范围 -> 图层坐标系范围。"""
    from qgis.core import QgsRectangle

    minx, miny, maxx, maxy = bbox
    tr = _make_transform(in_crs, layer.crs())
    rect = QgsRectangle(minx, miny, maxx, maxy)
    if tr is None:
        return rect
    try:
        return tr.transformBoundingBox(rect)
    except Exception as exc:
        raise InvalidRequestError(f"范围转换失败：{exc}") from exc


# --------------------------------------------------------------------- 面积换算


def _meters_per_degree(lat: float) -> tuple[float, float]:
    lat = max(min(lat, 89.9), -89.9)
    rad = math.radians(lat)
    m_lat = (
        _M_PER_DEG_LAT_BASE
        - 559.82 * math.cos(2 * rad)
        + 1.175 * math.cos(4 * rad)
        - 0.0023 * math.cos(6 * rad)
    )
    m_lon = (
        _M_PER_DEG_LON_BASE * math.cos(rad)
        - 93.5 * math.cos(3 * rad)
        + 0.118 * math.cos(5 * rad)
    )
    return m_lon, m_lat


def pixel_area_m2(layer: RasterLayer, bbox_crs: tuple[float, float, float, float]) -> tuple[float | None, str]:
    """估算单个像元的面积（平方米）及所用方法。

    返回 (面积平方米 or None, 方法说明)。
    """
    from qgis.core import QgsUnitTypes

    try:
        units = layer.layer.crs().mapUnits()
    except Exception:
        units = None

    if units == QgsUnitTypes.DistanceMeters:
        return abs(layer.resolution[0] * layer.resolution[1]), "投影坐标（米）直接计算"
    if units == QgsUnitTypes.DistanceDegrees:
        minx, miny, maxx, maxy = bbox_crs
        mean_lat = (miny + maxy) / 2.0
        m_lon, m_lat = _meters_per_degree(mean_lat)
        return abs(layer.resolution[0] * m_lon * layer.resolution[1] * m_lat), (
            f"地理坐标按纬度 {mean_lat:.3f}° 换算"
        )
    return None, "坐标系单位未知，未换算面积"


# --------------------------------------------------------------------- 点查询


def point_query(rl: RasterLayer, lon: float, lat: float, in_crs: str = "EPSG:4326") -> dict:
    """查询单个经纬度位置的像元值。"""
    x, y = transform_point(rl.layer, lon, lat, in_crs)
    provider = rl.layer.dataProvider()

    value: float | None = None
    ok = False
    try:
        from qgis.core import QgsPointXY, QgsRaster

        result = provider.identify(QgsPointXY(x, y), QgsRaster.IdentifyFormat.Value)
        ok = bool(result.isValid())
        if ok:
            raw = result.results().get(1)
            value = float(raw) if raw is not None else None
    except Exception:
        value, ok = _value_via_block(rl, x, y)

    if not ok and value is None:
        value, ok = _value_via_block(rl, x, y)

    result: dict[str, Any] = {
        "dataset_id": rl.dataset_id,
        "layer_id": rl.id,
        "lon": float(lon),
        "lat": float(lat),
        "x": x,
        "y": y,
        "crs": rl.crs,
        "in_crs": in_crs,
        "value": value,
        "nodata": rl.nodata,
        "kind": rl.kind,
        "in_extent": ok,
    }
    if ok and value is not None and rl.kind == "categorical":
        key = format_value(float(value))
        result["class_key"] = key
        result["class_name"] = rl.classes.get(key, f"Class {key}")
    if ok and value is not None and rl.nodata is not None and float(value) == rl.nodata:
        result["is_nodata"] = True
    return result


def _value_via_block(rl: RasterLayer, x: float, y: float) -> tuple[float | None, bool]:
    """兜底方案：按像元对齐读 1x1 块。"""
    from qgis.core import QgsRectangle

    try:
        xs = rl.layer.rasterUnitsPerPixelX()
        ys = rl.layer.rasterUnitsPerPixelY()
        if not xs or not ys:
            return None, False
        extent = rl.layer.extent()
        col = int((x - extent.xMinimum()) / xs)
        row = int((extent.yMaximum() - y) / ys)
        if col < 0 or row < 0 or col >= rl.layer.width() or row >= rl.layer.height():
            return None, False
        px0 = extent.xMinimum() + col * xs
        py1 = extent.yMaximum() - row * ys
        rect = QgsRectangle(px0, py1 - ys, px0 + xs, py1)
        block = rl.layer.dataProvider().block(1, rect, 1, 1)
        if block is None or block.isEmpty():
            return None, False
        return float(block.value(0, 0)), True
    except Exception:
        return None, False


# --------------------------------------------------------------------- 范围统计


def _read_window(rl: RasterLayer, rect, max_pixels: int):
    """按窗口读取栅格，必要时降采样。返回 (数组, 采样比例, 是否精确)。"""
    provider = rl.layer.dataProvider()
    rx = rl.resolution[0] or 1.0
    ry = rl.resolution[1] or 1.0
    native_w = max(1.0, rect.width() / abs(rx))
    native_h = max(1.0, rect.height() / abs(ry))
    total = native_w * native_h
    if total <= max_pixels:
        out_w = max(1, int(round(native_w)))
        out_h = max(1, int(round(native_h)))
        scale = 1.0
    else:
        scale = math.sqrt(max_pixels / total)
        out_w = max(1, int(native_w * scale))
        out_h = max(1, int(native_h * scale))
    block = provider.block(1, rect, out_w, out_h)
    arr = block_to_numpy(block)
    if arr is None:
        raise DataError(f"读取栅格窗口失败：{rl.id}")
    return arr, scale, scale == 1.0


def bbox_stats(
    rl: RasterLayer,
    bbox: tuple[float, float, float, float],
    in_crs: str = "EPSG:4326",
    max_pixels: int = 4_000_000,
) -> dict:
    """对给定范围做统计：分类图层给每类面积，连续图层给数值统计。"""
    minx, miny, maxx, maxy = bbox
    if not (maxx > minx and maxy > miny):
        raise InvalidRequestError("范围无效：需要 maxx > minx 且 maxy > miny")

    rect = transform_bbox(rl.layer, (minx, miny, maxx, maxy), in_crs)
    layer_rect = rl.layer.extent()
    inter = rect.intersect(layer_rect)
    if inter.isEmpty():
        raise DataError(
            "查询范围与图层范围不相交",
            {
                "layer_extent": list(rl.extent),
                "layer_extent_wgs84": list(rl.extent_wgs84),
                "query_bbox": [minx, miny, maxx, maxy],
            },
        )

    arr, scale, exact = _read_window(rl, inter, max_pixels)
    valid = mask_nodata(arr, rl.nodata)
    total_px = int(arr.shape[0] * arr.shape[1])
    valid_count = int(valid.sum())

    # 采样后每个像元覆盖的面积
    px_w_crs = inter.width() / arr.shape[1]
    px_h_crs = inter.height() / arr.shape[0]
    unit_area, method = pixel_area_m2(rl, (inter.xMinimum(), inter.yMinimum(), inter.xMaximum(), inter.yMaximum()))
    if unit_area is not None:
        block_area_m2 = unit_area * (scale**2) if not exact else unit_area
    else:
        block_area_m2 = None

    result: dict[str, Any] = {
        "dataset_id": rl.dataset_id,
        "layer_id": rl.id,
        "kind": rl.kind,
        "crs": rl.crs,
        "query_bbox": [minx, miny, maxx, maxy],
        "query_bbox_crs": [inter.xMinimum(), inter.yMinimum(), inter.xMaximum(), inter.yMaximum()],
        "sampled_pixels": total_px,
        "valid_pixels": valid_count,
        "exact": exact,
        "sample_scale": round(scale, 6),
        "area_method": method,
    }

    if valid_count == 0:
        result["classes"] = []
        result["stats"] = None
        result["area_m2"] = 0.0
        return result

    values = arr[valid]

    if rl.kind == "categorical":
        counts: dict[str, int] = {}
        uniques, uni_counts = _unique_with_counts(values)
        for val, cnt in zip(uniques, uni_counts):
            counts[format_value(float(val))] = int(cnt)
        classes = []
        for key, cnt in counts.items():
            area_m2 = block_area_m2 * cnt if block_area_m2 is not None else None
            classes.append(
                {
                    "key": key,
                    "name": rl.classes.get(key, f"Class {key}"),
                    "pixels": cnt,
                    "ratio": round(cnt / valid_count, 6),
                    "area_m2": round(area_m2, 2) if area_m2 is not None else None,
                    "area_ha": round(area_m2 / 10000.0, 4) if area_m2 is not None else None,
                    "area_km2": round(area_m2 / 1e6, 6) if area_m2 is not None else None,
                }
            )
        classes.sort(key=lambda item: item["pixels"], reverse=True)
        result["classes"] = classes
        result["area_m2"] = round(block_area_m2 * valid_count, 2) if block_area_m2 is not None else None
    else:
        result["classes"] = []
        result["stats"] = {
            "min": float(values.min()),
            "max": float(values.max()),
            "mean": float(values.mean()),
            "std": float(values.std()) if values.size > 1 else 0.0,
            "median": float(__import__("numpy").median(values)),
        }
        result["area_m2"] = round(block_area_m2 * valid_count, 2) if block_area_m2 is not None else None
    return result


def _unique_with_counts(values):
    import numpy as np

    uniques, counts = np.unique(values, return_counts=True)
    return uniques, counts


def layer_histogram(rl: RasterLayer, bin_count: int = 32) -> dict:
    """整幅图层的直方图（连续型）。

    QGIS 3.44 的 ``QgsRasterLayer.histogram()`` 已移除，这里改为读取（必要时降采样的）
    栅格块后用 numpy 统计，结果可控且不依赖版本差异。
    """
    import numpy as np

    try:
        arr, scale, exact = _read_window(rl, rl.layer.extent(), 2_000_000)
    except Exception as exc:
        raise DataError(f"直方图计算失败：{exc}") from exc

    valid = mask_nodata(arr, rl.nodata)
    values = arr[valid]
    if values.size == 0:
        return {"bins": [], "min": None, "max": None, "exact": exact, "sample_scale": round(scale, 6)}

    minimum = float(values.min())
    maximum = float(values.max())
    if maximum <= minimum:
        maximum = minimum + 1.0
    counts, edges = np.histogram(values, bins=max(1, int(bin_count)), range=(minimum, maximum))
    bins = [
        {
            "from": float(edges[i]),
            "to": float(edges[i + 1]),
            "count": int(counts[i]),
        }
        for i in range(len(counts))
    ]
    return {
        "bins": bins,
        "min": minimum,
        "max": maximum,
        "exact": exact,
        "sample_scale": round(scale, 6),
        "total_pixels": int(values.size),
    }


def layer_band_stats(rl: RasterLayer) -> dict:
    """整幅图层的像元统计。"""
    try:
        from qgis.core import QgsRasterBandStats

        stats = rl.layer.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
        return {
            "min": float(stats.minimumValue),
            "max": float(stats.maximumValue),
            "mean": float(stats.mean),
            "std": float(stats.stdDev),
        }
    except Exception as exc:
        raise DataError(f"图层统计失败：{exc}") from exc


def polygon_stats(
    rl: RasterLayer,
    polygon_wgs84: list[list[float]],
    max_pixels: int = 4_000_000,
) -> dict:
    """按多边形范围统计（先取外接矩形，再按点-in-polygon 精筛）。

    polygon_wgs84: [[lon, lat], ...] 闭合环
    """
    if not polygon_wgs84 or len(polygon_wgs84) < 3:
        raise InvalidRequestError("多边形至少需要 3 个顶点")

    xs = [p[0] for p in polygon_wgs84]
    ys = [p[1] for p in polygon_wgs84]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    stats = bbox_stats(rl, bbox, "EPSG:4326", max_pixels)

    # 精筛：只保留落在多边形内的像元中心
    from qgis.core import QgsGeometry, QgsPointXY

    ring = polygon_wgs84 + [polygon_wgs84[0]]
    geom = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in ring]])
    if not geom.isGeosValid():
        geom = geom.makeValid()
    if geom.isEmpty():
        raise InvalidRequestError("多边形几何无效")

    provider = rl.layer.dataProvider()
    rect = transform_bbox(rl.layer, bbox, "EPSG:4326")
    inter = rect.intersect(rl.layer.extent())
    if inter.isEmpty():
        raise DataError("多边形范围与图层不相交")

    arr, scale, exact = _read_window(rl, inter, max_pixels)
    valid = mask_nodata(arr, rl.nodata)
    ys_idx, xs_idx = arr.shape
    px_w = inter.width() / max(1, xs_idx)
    px_h = inter.height() / max(1, ys_idx)

    tr = _make_transform("EPSG:4326", rl.crs)
    keep = []
    for r in range(ys_idx):
        for c in range(xs_idx):
            if not valid[r, c]:
                continue
            x = inter.xMinimum() + (c + 0.5) * px_w
            y = inter.yMaximum() - (r + 0.5) * px_h
            if tr is not None:
                pt = tr.transform(x, y)
                lon, lat = pt.x(), pt.y()
            else:
                lon, lat = x, y
            if geom.contains(QgsPointXY(lon, lat)):
                keep.append(arr[r, c])

    if not keep:
        stats["classes"] = []
        stats["valid_pixels"] = 0
        stats["polygon"] = {"vertices": len(polygon_wgs84), "matched_pixels": 0}
        return stats

    import numpy as np

    kept = np.asarray(keep)
    unit_area, method = pixel_area_m2(rl, (inter.xMinimum(), inter.yMinimum(), inter.xMaximum(), inter.yMaximum()))
    block_area = (unit_area * (scale**2)) if (unit_area is not None and not exact) else unit_area

    if rl.kind == "categorical":
        uniques, counts = np.unique(kept, return_counts=True)
        classes = []
        for val, cnt in zip(uniques, counts):
            key = format_value(float(val))
            area_m2 = block_area * int(cnt) if block_area is not None else None
            classes.append(
                {
                    "key": key,
                    "name": rl.classes.get(key, f"Class {key}"),
                    "pixels": int(cnt),
                    "ratio": round(int(cnt) / kept.size, 6),
                    "area_m2": round(area_m2, 2) if area_m2 is not None else None,
                    "area_ha": round(area_m2 / 10000.0, 4) if area_m2 is not None else None,
                }
            )
        classes.sort(key=lambda item: item["pixels"], reverse=True)
        stats["classes"] = classes
        stats["valid_pixels"] = int(kept.size)
        stats["area_m2"] = round(block_area * kept.size, 2) if block_area is not None else None
    else:
        stats["classes"] = []
        stats["valid_pixels"] = int(kept.size)
        stats["stats"] = {
            "min": float(kept.min()),
            "max": float(kept.max()),
            "mean": float(kept.mean()),
            "std": float(kept.std()) if kept.size > 1 else 0.0,
        }
    stats["area_method"] = method
    stats["polygon"] = {"vertices": len(polygon_wgs84), "matched_pixels": int(kept.size)}
    return stats
