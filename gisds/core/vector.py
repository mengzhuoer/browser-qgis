"""矢量查询：要素分页、属性统计、点命中。

输出统一为 GeoJSON（EPSG:4326），网页端可以直接丢给 Leaflet / Mapbox 渲染。
"""

from __future__ import annotations

import json
from typing import Any

from .errors import DataError, InvalidRequestError
from .registry import VectorLayer

_MAX_FIELD_VALUES = 50


def _transformer(layer, dst_crs: str = "EPSG:4326"):
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext

    src = layer.layer.crs()
    dst = QgsCoordinateReferenceSystem(dst_crs)
    if not src.isValid() or not dst.isValid() or src == dst:
        return None
    return QgsCoordinateTransform(src, dst, QgsCoordinateTransformContext())


def _bbox_in_layer_crs(layer, bbox):
    from qgis.core import QgsRectangle

    if not bbox:
        return None
    minx, miny, maxx, maxy = bbox
    rect = QgsRectangle(minx, miny, maxx, maxy)
    tr = _transformer(layer, layer.crs().authid() or "EPSG:4326")
    if tr is None:
        return rect
    try:
        return tr.transformBoundingBox(rect)
    except Exception as exc:
        raise InvalidRequestError(f"范围转换失败：{exc}") from exc


def _build_request(layer, bbox=None, where=None, limit=None, offset=0):
    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest()
    if bbox:
        rect = _bbox_in_layer_crs(layer, bbox)
        if rect is not None:
            request.setFilterRect(rect)
    if where:
        try:
            request.setFilterExpression(where)
        except Exception as exc:
            raise InvalidRequestError(f"属性过滤表达式无效：{exc}") from exc
    if limit:
        # QgsFeatureRequest 没有 setOffset（QGIS 3.44），偏移靠「多取 offset 个再丢掉」实现
        request.setLimit(int(limit) + int(offset or 0))
    return request


def list_features(
    layer: VectorLayer,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int = 50,
    offset: int = 0,
    where: str | None = None,
    keep_crs: bool = False,
) -> dict:
    """按条件取要素，返回 GeoJSON FeatureCollection。"""
    request = _build_request(layer, bbox, where, limit, offset)
    try:
        features = list(layer.layer.getFeatures(request))
    except Exception as exc:
        raise DataError(f"要素查询失败：{exc}") from exc

    # 偏移是手动跳的：前 offset 个不要
    if offset:
        features = features[int(offset):]

    tr = None if keep_crs else _transformer(layer, "EPSG:4326")
    field_names = [f.name() for f in layer.layer.fields()]

    out_features = []
    for feature in features:
        geometry = feature.geometry()
        if tr is not None and not geometry.isEmpty():
            geometry = Qgs_geometry_copy(geometry)
            geometry.transform(tr)
        try:
            geom_json = json.loads(geometry.asJson()) if not geometry.isEmpty() else None
        except Exception:
            geom_json = None
        attributes = feature.attributes()
        props = {}
        for name, value in zip(field_names, attributes):
            props[name] = _jsonable(value)
        out_features.append(
            {
                "type": "Feature",
                "id": feature.id(),
                "geometry": geom_json,
                "properties": props,
            }
        )

    return {
        "type": "FeatureCollection",
        "dataset_id": layer.dataset_id,
        "layer_id": layer.id,
        "crs": layer.crs if keep_crs else "EPSG:4326",
        "count": len(out_features),
        "offset": int(offset),
        "limit": int(limit),
        "total": _count_matching(layer, bbox, where),
        "features": out_features,
    }


def Qgs_geometry_copy(geometry):
    """复制一份几何（transform 会就地修改，不能动缓存里的对象）。"""
    from qgis.core import QgsGeometry

    return QgsGeometry(geometry)


def _count_matching(layer, bbox=None, where=None) -> int:
    """命中要素数。

    QgsFeatureIterator 没有 count()（QGIS 3.44），只能自己数。
    没有过滤条件时直接用图层自带的 featureCount()，那是 provider 层的快路径。
    """
    if not where and not bbox:
        try:
            return int(layer.layer.featureCount())
        except Exception:
            return int(layer.feature_count)
    request = _build_request(layer, bbox, where, None, 0)
    try:
        total = 0
        for _ in layer.layer.getFeatures(request):
            total += 1
        return total
    except Exception:
        return int(layer.feature_count)


def vector_stats(layer: VectorLayer, bbox=None, top_n: int = 10) -> dict:
    """矢量图层统计：总数、几何类型、范围、各字段取值分布。"""
    request = _build_request(layer, bbox, None, None, 0)
    try:
        features = list(layer.layer.getFeatures(request))
    except Exception as exc:
        raise DataError(f"要素统计失败：{exc}") from exc

    field_stats: list[dict] = []
    for field in layer.layer.fields():
        name = field.name()
        values: dict[str, int] = {}
        non_null = 0
        for feature in features:
            value = feature.attribute(name)
            if value is None or value == "":
                continue
            non_null += 1
            key = _jsonable(value)
            key = key if isinstance(key, str) else json.dumps(key, ensure_ascii=False)
            values[key] = values.get(key, 0) + 1
        ranked = sorted(values.items(), key=lambda item: item[1], reverse=True)[:top_n]
        field_stats.append(
            {
                "name": name,
                "type": field.typeName(),
                "non_null": non_null,
                "unique": len(values),
                "top": [{"value": k, "count": v} for k, v in ranked],
            }
        )

    extent = None
    if features:
        from qgis.core import QgsRectangle

        acc = QgsRectangle()
        acc.setMinimal()
        for feature in features:
            geom = feature.geometry()
            if not geom.isEmpty():
                acc.combineExtentWith(geom.boundingBox())

        tr = _transformer(layer, "EPSG:4326")
        if tr is not None:
            acc = tr.transformBoundingBox(acc)
        extent = [acc.xMinimum(), acc.yMinimum(), acc.xMaximum(), acc.yMaximum()]

    return {
        "dataset_id": layer.dataset_id,
        "layer_id": layer.id,
        "feature_count": len(features),
        "total_features": int(layer.feature_count),
        "geometry_type": layer.geometry_type,
        "crs": layer.crs,
        "extent_wgs84": extent or list(layer.extent),
        "fields": field_stats,
    }


def vector_hit(layer: VectorLayer, lon: float, lat: float, limit: int = 20) -> dict:
    """点命中：返回包含该点的要素。"""
    from qgis.core import QgsGeometry, QgsPointXY, QgsSpatialIndex

    point = QgsPointXY(float(lon), float(lat))
    if layer.layer.crs().authid() != "EPSG:4326":
        tr = _transformer(layer, "EPSG:4326")
        if tr is not None:
            try:
                point = tr.transform(point)
            except Exception:
                pass

    # QgsGeometry::intersects() 没有 QgsPointXY 重载，点图层会直接抛异常，
    # 所以统一用 QgsGeometry 版本的点来判定。
    point_geom = QgsGeometry.fromPointXY(point)

    try:
        index = QgsSpatialIndex(layer.layer.getFeatures())
    except Exception:
        index = None

    hits = []
    try:
        for feature in layer.layer.getFeatures():
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            if index is not None:
                if index.intersects(geom.boundingBox().buffered(1e-9)) is False:
                    pass
            if geom.contains(point_geom) or geom.intersects(point_geom):
                props = {f.name(): _jsonable(feature.attribute(f.name())) for f in layer.layer.fields()}
                hits.append(
                    {
                        "id": feature.id(),
                        "properties": props,
                        "area_m2": _area_m2(layer, geom),
                    }
                )
                if len(hits) >= limit:
                    break
    except Exception as exc:
        raise DataError(f"点命中失败：{exc}") from exc

    return {
        "dataset_id": layer.dataset_id,
        "layer_id": layer.id,
        "lon": float(lon),
        "lat": float(lat),
        "count": len(hits),
        "features": hits,
    }


def _area_m2(layer: VectorLayer, geometry) -> float | None:
    """多边形面积（平方米）；经纬度层按纬度粗略换算。"""
    from qgis.core import QgsCoordinateReferenceSystem, QgsDistanceArea, QgsUnitTypes

    try:
        crs = layer.layer.crs()
        if crs.authid() == "EPSG:4326":
            area = geometry.area()  # 平方度
            mean_lat = (geometry.boundingBox().yMinimum() + geometry.boundingBox().yMaximum()) / 2.0
            import math

            rad = math.radians(mean_lat)
            m_lat = 111132.92 - 559.82 * math.cos(2 * rad) + 1.175 * math.cos(4 * rad)
            m_lon = 111412.84 * math.cos(rad) - 93.5 * math.cos(3 * rad)
            return abs(area * m_lat * m_lon)
        if crs.mapUnits() == QgsUnitTypes.DistanceMeters:
            return abs(geometry.area())
    except Exception:
        return None
    return None


def _jsonable(value: Any) -> Any:
    """把 QGIS 属性值转成可 JSON 序列化的类型。"""
    import datetime

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    try:
        return float(value)
    except Exception:
        return str(value)
