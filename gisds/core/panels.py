"""QGIS 桌面版「面板」能力的内核实现。

网页版右栏的面板（属性表、统计、选择、书签、日志、处理历史）
与 QGIS Desktop 的同名 dock 面板一一对应，底层一律走 PyQGIS，
所以统计口径、表达式语义、编辑行为与桌面版完全一致 ——
不是另写一套「网页版专用」的实现。

面板能力分三类：

* **只读分析** —— 属性表、字段统计、按表达式 / 按空间关系选择；
* **编辑** —— 进入编辑模式、改属性、增删要素、字段计算器、保存 / 放弃；
* **服务级** —— 书签、日志、处理历史（在 ``core.bookmarks`` / ``core.logbuf`` 里）。

本模块里的函数全部要跑在 QGIS 专属线程上（``QgsVectorLayer`` 是 QObject，
跨线程访问会崩），所以 API 层要用 ``executor.run_qgis`` 派发。
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from .errors import DataError, InvalidRequestError, NotFoundError
from .registry import VectorLayer

# 统计时的取样上限：字段统计要全表扫，遇到超大表会卡住整个服务
_MAX_STAT_FEATURES = 200_000
# 属性表单页上限
_MAX_TABLE_ROWS = 2000

# 空间关系谓词 -> QgsGeometry 方法名
SPATIAL_PREDICATES = {
    "intersects": "intersects",
    "contains": "contains",
    "within": "within",
    "touches": "touches",
    "crosses": "crosses",
    "overlaps": "overlaps",
    "disjoint": "disjoint",
}

# 负向关系：要求「所有目标要素都满足」，而不是「任一满足」
_NEGATIVE_PREDICATES = {"disjoint"}


def _predicate(source_geom, target_geom, method: str) -> bool:
    """单个「源要素 相对 目标要素」的空间关系判定。"""
    try:
        return bool(getattr(source_geom, method)(target_geom))
    except Exception:
        return False


def _matches(source_geom, target_geoms, method: str) -> bool:
    """源要素与一组目标要素是否满足空间关系。

    关系按「源要素 相对 目标」来判（源包含目标 = contains，源位于目标内部 =
    within），与 QGIS「按位置选择」的表述一致。

    正向关系（相交 / 包含 / 位于内部 …）取「**任一**目标要素满足」；
    负向关系（分离）必须取「**所有**目标要素都满足」—— 否则一个要素只要跟
    目标里的某一个不相交就会被选中，结果几乎等于全选。
    """
    if not target_geoms:
        return False
    if method in _NEGATIVE_PREDICATES:
        return all(_predicate(source_geom, g, method) for g in target_geoms)
    return any(_predicate(source_geom, g, method) for g in target_geoms)


# --------------------------------------------------------------------- 工具


def _vlayer(layer: VectorLayer):
    """取底层 QgsVectorLayer，并确认它有效。"""
    v = getattr(layer, "layer", None)
    if v is None or not v.isValid():
        raise DataError(f"图层不可用：{layer.ref}")
    return v


def _field_index(v) -> dict[str, int]:
    return {f.name(): i for i, f in enumerate(v.fields())}


def _fid_field_indexes(v) -> set[int]:
    """provider 的**主键**字段下标 —— 也就是 FID 列。

    为什么不能按名字猜：OGR 的 FID 列就叫 ``fid``，而 QGIS 把它当**普通属性**
    一起返回（``v.fields()`` 里就有它），于是属性表里 fid 出现两次，
    而且那一列**还能编辑** —— 改完保存时 OGR 会报「未能删除要素」，
    **整个编辑会话被回滚，改动全丢**（真踩过：字段计算器选了 fid 当目标字段，
    界面提示「已更新 1420 个要素」，一保存全没了）。

    认法是 provider 自己说的：``pkAttributeIndexes()``。
    没有主键的图层（GeoJSON 之类）返回空列表。
    """
    try:
        provider = v.dataProvider()
        if provider is None:
            return set()
        return set(provider.pkAttributeIndexes())
    except Exception:
        return set()


def _cast_value(field, value):
    """按字段类型转换属性值。

    QGIS 的 ``changeAttributeValue`` 靠新值推断类型，传进来的是 JSON 里的
    字符串/数字混着来，所以这里按字段定义先规整一遍，省掉「写入后类型错乱」。
    """
    if value is None:
        return None
    type_name = (field.typeName() or "").lower()
    try:
        if type_name in ("integer", "int", "integer64", "long", "int64"):
            return int(value)
        if type_name in ("double", "real", "float", "float8", "numeric"):
            return float(value)
        if type_name in ("string", "text", "varchar", "character varying"):
            return str(value)
        if type_name in ("bool", "boolean"):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "是", "t")
            return bool(value)
    except (TypeError, ValueError):
        return value
    return value


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return float(value)
    except Exception:
        return str(value)


def geometry_from_geojson(geo: dict) -> Any:
    """GeoJSON 几何 -> QgsGeometry。

    PyQGIS 3.44 既没有 ``QgsGeometry.fromGeoJson``，也没导出 ``QgsPolygonXY`` /
    ``QgsPolylineXY``，所以这里用 ``QgsPointXY`` + 裸列表逐类型构造
    （``fromPolygonXY`` 这类方法直接吃嵌套列表）。
    只支持 OGC 的六种基本几何。
    """
    from qgis.core import QgsGeometry, QgsPointXY

    if not isinstance(geo, dict):
        raise InvalidRequestError("几何必须是 GeoJSON 对象")
    gtype = geo.get("type")
    coords = geo.get("coordinates")
    if not gtype or coords is None:
        raise InvalidRequestError("几何缺少 type 或 coordinates")

    def pt(c):
        return QgsPointXY(float(c[0]), float(c[1]))

    def line(c):
        return [pt(x) for x in c]

    try:
        if gtype == "Point":
            return QgsGeometry.fromPointXY(pt(coords))
        if gtype == "MultiPoint":
            return QgsGeometry.fromMultiPointXY([pt(c) for c in coords])
        if gtype == "LineString":
            return QgsGeometry.fromPolylineXY(line(coords))
        if gtype == "MultiLineString":
            return QgsGeometry.fromMultiPolylineXY([line(c) for c in coords])
        if gtype == "Polygon":
            return QgsGeometry.fromPolygonXY([line(r) for r in coords])
        if gtype == "MultiPolygon":
            return QgsGeometry.fromMultiPolygonXY([[line(r) for r in poly] for poly in coords])
    except InvalidRequestError:
        raise
    except Exception as exc:
        raise InvalidRequestError(f"几何无效：{exc}") from exc
    raise InvalidRequestError(f"不支持的几何类型：{gtype}")


# --------------------------------------------------------------------- 属性表


def attribute_table(
    layer: VectorLayer,
    limit: int = 100,
    offset: int = 0,
    where: str | None = None,
    selected: list[int] | None = None,
    sort: str | None = None,
    order: str = "asc",
) -> dict:
    """属性表面板的数据。

    返回字段定义 + 一页要素行 + 命中总数。行里只放属性，不放几何 ——
    几何已经在地图上了，属性表再带一份纯属浪费带宽。

    ``sort`` / ``order``：按某个字段排序（QGIS 桌面版属性表列头可点排序）。
    """
    from qgis.core import QgsExpression, QgsFeatureRequest

    v = _vlayer(layer)
    limit = max(1, min(int(limit), _MAX_TABLE_ROWS))
    offset = max(0, int(offset))

    pk = _fid_field_indexes(v)
    fields = [
        {
            "name": f.name(),
            "type": f.typeName(),
            "alias": f.alias() or f.name(),
            "length": f.length(),
            "precision": f.precision(),
            # 主键（FID）列**不能赋值** —— 前端要靠这个标记把它排除掉
            "fid": i in pk,
        }
        for i, f in enumerate(v.fields())
    ]
    index = _field_index(v)

    def _request(with_page: bool):
        request = QgsFeatureRequest()
        if where:
            # **先自己解析一遍**：QGIS 3.44 的 ``setFilterExpression`` 对语法错
            # **不抛异常**（错误要到求值才暴露，而求值失败只是「不匹配」），
            # 所以原来那个 try/except 形同虚设 —— 用户把表达式写错，
            # 界面只显示「0 个要素（已过滤）」，分不清是写错了还是真没数据。
            expr = QgsExpression(where)
            if expr.hasParserError():
                raise InvalidRequestError(f"过滤表达式无效：{expr.parserErrorString()}")
            try:
                request.setFilterExpression(where)
            except Exception as exc:
                raise InvalidRequestError(f"过滤表达式无效：{exc}") from exc
        if selected is not None:
            request.setFilterFids([int(i) for i in selected])
        if sort:
            # 排序要在**分页之前**做 —— 先排好再取第 offset+limit 个，
            # 翻页看到的顺序才是对的。
            #
            # 字段名用 ``"..."`` 包起来：字段名是中文/带空格时，不加引号
            # QGIS 会当成表达式解析而报错（中文名尤其容易踩）。
            if sort not in index:
                raise InvalidRequestError(
                    f"排序字段不存在：{sort}", {"available": list(index.keys())}
                )
            try:
                # PyQGIS 3.44 的写法是 ``addOrderBy(表达式字符串, 是否升序)``
                # （``OrderBy`` / ``OrderByExpression`` 那套 C++ 写法没暴露出来）。
                # 字段名用 ``"..."`` 包起来：中文/带空格的字段名不加引号
                # 会被当成表达式解析而报错。
                request.addOrderBy(
                    f'"{sort}"',
                    str(order).lower() != "desc",
                )
            except Exception as exc:
                raise InvalidRequestError(f"排序失败：{exc}") from exc
        if with_page:
            # QgsFeatureRequest 没有 setOffset（QGIS 3.44），
            # 偏移靠「多取 offset 个再丢掉」实现
            request.setLimit(limit + offset)
        return request

    # 总数：QgsFeatureIterator 没有 count()，没过滤时走 provider 的快路径
    if not where and selected is None:
        try:
            total = int(v.featureCount())
        except Exception:
            total = 0
    else:
        total = 0
        try:
            for _ in v.getFeatures(_request(False)):
                total += 1
        except Exception as exc:
            raise DataError(f"属性表计数失败：{exc}") from exc

    try:
        features = list(v.getFeatures(_request(True)))
    except Exception as exc:
        raise DataError(f"属性表读取失败：{exc}") from exc
    if offset:
        features = features[offset:]

    rows = []
    for feature in features:
        cells = {}
        for name, i in index.items():
            cells[name] = _jsonable(feature.attribute(i))
        rows.append({"fid": feature.id(), "cells": cells})

    return {
        "ref": layer.ref,
        "dataset_id": layer.dataset_id,
        "layer_id": layer.id,
        "title": layer.title,
        "crs": layer.crs,
        "geometry_type": layer.geometry_type,
        "fields": fields,
        "rows": rows,
        "count": len(rows),
        "total": total,
        "limit": limit,
        "offset": offset,
        "is_editing": bool(v.isEditable()),
        "source": layer.source,
    }


# --------------------------------------------------------------------- 字段统计


def field_statistics(layer: VectorLayer, field: str | None = None) -> dict:
    """统计面板的数据：逐字段的计数 / 唯一值 / 极值 / 均值 / 中位数。

    只统计**非空**值（QGIS 的「基础统计」也是这个口径）。
    """
    v = _vlayer(layer)
    index = _field_index(v)
    if field and field not in index:
        raise NotFoundError(f"字段不存在：{field}", {"available": list(index.keys())})

    targets = [field] if field else list(index.keys())
    scanned = 0
    samples: dict[str, list[Any]] = {name: [] for name in targets}
    counts: dict[str, int] = {name: 0 for name in targets}
    empties: dict[str, int] = {name: 0 for name in targets}
    uniques: dict[str, set] = {name: set() for name in targets}
    truncated = False

    try:
        for feature in v.getFeatures():
            if scanned >= _MAX_STAT_FEATURES:
                truncated = True
                break
            scanned += 1
            for name in targets:
                raw = feature.attribute(index[name])
                if raw is None or raw == "":
                    empties[name] += 1
                    continue
                counts[name] += 1
                if len(uniques[name]) <= 5000:
                    uniques[name].add(str(raw))
                samples[name].append(raw)
    except Exception as exc:
        raise DataError(f"统计失败：{exc}") from exc

    out_fields = []
    for name in targets:
        values = samples[name]
        type_name = v.fields()[index[name]].typeName()
        numeric = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in values) and values

        entry: dict[str, Any] = {
            "name": name,
            "type": type_name,
            "count": counts[name],
            "empty": empties[name],
            "unique": len(uniques[name]),
        }

        if numeric:
            nums = [float(x) for x in values]
            entry.update(
                {
                    "min": min(nums),
                    "max": max(nums),
                    "mean": sum(nums) / len(nums),
                    "median": _median(nums),
                    "sum": sum(nums),
                    "range": max(nums) - min(nums),
                    "stddev": _stddev(nums),
                }
            )
        else:
            strs = [str(x) for x in values]
            if strs:
                entry["min"] = min(strs)
                entry["max"] = max(strs)
                entry["mean"] = None
                entry["median"] = None
                entry["sum"] = None
            counter: dict[str, int] = {}
            for item in strs:
                counter[item] = counter.get(item, 0) + 1
            top = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:10]
            entry["top"] = [{"value": k, "count": n} for k, n in top]

        out_fields.append(entry)

    return {
        "ref": layer.ref,
        "feature_count": scanned,
        "truncated": truncated,
        "fields": out_fields,
    }


def _median(nums: list[float]) -> float:
    if not nums:
        return 0.0
    ordered = sorted(nums)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _stddev(nums: list[float]) -> float:
    if len(nums) < 2:
        return 0.0
    mean = sum(nums) / len(nums)
    return math.sqrt(sum((x - mean) ** 2 for x in nums) / (len(nums) - 1))


# --------------------------------------------------------------------- 选择


def select_all(layer: VectorLayer) -> dict:
    """全选：返回该图层所有要素的 fid。"""
    v = _vlayer(layer)
    fids: list[int] = []
    try:
        for feature in v.getFeatures():
            fids.append(feature.id())
    except Exception as exc:
        raise DataError(f"全选失败：{exc}") from exc
    return {
        "ref": layer.ref,
        "method": "all",
        "fids": fids,
        "count": len(fids),
    }


def select_by_layer(
    layer: VectorLayer,
    target: VectorLayer,
    predicate: str = "intersects",
) -> dict:
    """按空间关系选择：拿**另一个图层**的要素当参照物。

    对应 QGIS 的「按空间关系选择要素 → 目标图层」。
    """
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext
    from qgis.core import QgsGeometry

    v = _vlayer(layer)
    t = _vlayer(target)
    method = SPATIAL_PREDICATES.get((predicate or "intersects").lower())
    if method is None:
        raise InvalidRequestError(
            f"未知的空间关系：{predicate}",
            {"available": sorted(SPATIAL_PREDICATES.keys())},
        )

    # 参照几何统一转到目标图层的坐标系
    src = t.crs()
    dst = v.crs()
    transform = None
    if src.isValid() and dst.isValid() and src != dst:
        try:
            transform = QgsCoordinateTransform(src, dst, QgsCoordinateTransformContext())
        except Exception:
            transform = None

    refs = []
    try:
        for feature in t.getFeatures():
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            if transform is not None:
                try:
                    geom = QgsGeometry(geom)
                    geom.transform(transform)
                except Exception:
                    pass
            refs.append(geom)
    except Exception as exc:
        raise DataError(f"读取目标图层失败：{exc}") from exc

    fids: list[int] = []
    try:
        for feature in v.getFeatures():
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            if _matches(geom, refs, method):
                fids.append(feature.id())
    except Exception as exc:
        raise DataError(f"按空间关系选择失败：{exc}") from exc

    return {
        "ref": layer.ref,
        "method": "spatial-layer",
        "target_ref": target.ref,
        "predicate": method,
        "fids": fids,
        "count": len(fids),
    }


def select_by_expression(layer: VectorLayer, expression: str) -> dict:
    """按属性表达式选择（QGIS 的「按表达式选择要素」）。"""
    from qgis.core import QgsExpression, QgsFeatureRequest

    v = _vlayer(layer)
    expression = (expression or "").strip()
    if not expression:
        raise InvalidRequestError("表达式为空")

    probe = QgsExpression(expression)
    if probe.hasParserError():
        raise InvalidRequestError(f"表达式无效：{probe.parserErrorString()}")

    request = QgsFeatureRequest()
    try:
        request.setFilterExpression(expression)
    except Exception as exc:
        raise InvalidRequestError(f"表达式无效：{exc}") from exc

    fids: list[int] = []
    try:
        for feature in v.getFeatures(request):
            fids.append(feature.id())
    except Exception as exc:
        raise DataError(f"按表达式选择失败：{exc}") from exc

    return {
        "ref": layer.ref,
        "method": "expression",
        "expression": expression,
        "fids": fids,
        "count": len(fids),
    }


def select_by_spatial(
    layer: VectorLayer,
    geometry: dict,
    predicate: str = "intersects",
    mode: str = "replace",
) -> dict:
    """按空间关系选择（QGIS 的「按空间关系选择要素」）。

    ``geometry`` 是 GeoJSON 几何（经纬度）；``predicate`` 取
    intersects / contains / within / touches / crosses / overlaps / disjoint。
    """
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext

    v = _vlayer(layer)
    method = SPATIAL_PREDICATES.get((predicate or "intersects").lower())
    if method is None:
        raise InvalidRequestError(
            f"未知的空间关系：{predicate}",
            {"available": sorted(SPATIAL_PREDICATES.keys())},
        )
    if not geometry:
        raise InvalidRequestError("缺少用于选择的几何")

    target = geometry_from_geojson(geometry)
    if target.isNull() or target.isEmpty():
        raise InvalidRequestError("几何为空")

    # 目标几何是经纬度，图层可能是别的坐标系 —— 先转到图层坐标系
    src = QgsCoordinateReferenceSystem("EPSG:4326")
    dst = v.crs()
    if dst.isValid() and src != dst:
        try:
            transform = QgsCoordinateTransform(src, dst, QgsCoordinateTransformContext())
            target.transform(transform)
        except Exception:
            pass

    fids: list[int] = []
    try:
        for feature in v.getFeatures():
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            if _matches(geom, [target], method):
                fids.append(feature.id())
    except Exception as exc:
        raise DataError(f"按空间关系选择失败：{exc}") from exc

    return {
        "ref": layer.ref,
        "method": "spatial",
        "predicate": method,
        "mode": mode,
        "fids": fids,
        "count": len(fids),
    }


# --------------------------------------------------------------------- 编辑


def _require_editable(layer: VectorLayer):
    from qgis.core import QgsVectorDataProvider

    v = _vlayer(layer)
    caps = v.dataProvider().capabilities()
    missing = []
    if not caps & QgsVectorDataProvider.ChangeAttributeValues:
        missing.append("改属性")
    if not caps & QgsVectorDataProvider.AddFeatures:
        missing.append("新增要素")
    if not caps & QgsVectorDataProvider.DeleteFeatures:
        missing.append("删除要素")
    if missing:
        raise InvalidRequestError(
            f"该数据源不支持编辑（{'、'.join(missing)}）",
            {"provider": v.dataProvider().name()},
        )
    return v


def start_edit(layer: VectorLayer) -> dict:
    """进入编辑模式。"""
    v = _require_editable(layer)
    if v.isEditable():
        return {"ref": layer.ref, "is_editing": True, "message": "已经处于编辑状态"}
    if not v.startEditing():
        raise DataError(f"无法进入编辑模式：{v.lastError()}")
    return {"ref": layer.ref, "is_editing": True, "message": "已进入编辑模式"}


def commit_edit(layer: VectorLayer) -> dict:
    """保存编辑（写回数据文件）。"""
    v = _vlayer(layer)
    if not v.isEditable():
        raise InvalidRequestError("当前不在编辑状态，无需保存")
    if not v.commitChanges():
        errors = [str(e) for e in v.commitErrors()]
        v.rollBack()
        raise DataError("保存失败：" + ("；".join(errors) if errors else v.lastError()))
    return {"ref": layer.ref, "is_editing": False, "message": "已保存到数据文件"}


def rollback_edit(layer: VectorLayer) -> dict:
    """放弃编辑（丢弃所有未保存的改动）。"""
    v = _vlayer(layer)
    if not v.isEditable():
        return {"ref": layer.ref, "is_editing": False, "message": "本来就不在编辑状态"}
    if not v.rollBack():
        raise DataError(f"放弃编辑失败：{v.lastError()}")
    return {"ref": layer.ref, "is_editing": False, "message": "已放弃编辑"}


def update_feature(layer: VectorLayer, fid: int, attributes: dict) -> dict:
    """改一个要素的属性（属性表单 / 属性表里双击单元格改值）。"""
    v = _vlayer(layer)
    if not v.isEditable():
        raise InvalidRequestError("请先进入编辑模式再修改属性")
    index = _field_index(v)
    feature = v.getFeature(int(fid))
    if not feature.isValid():
        raise NotFoundError(f"要素不存在：{fid}")

    changed = []
    pk = _fid_field_indexes(v)
    for name, value in (attributes or {}).items():
        i = index.get(name)
        if i is None:
            raise InvalidRequestError(f"字段不存在：{name}", {"available": list(index.keys())})
        # 主键（FID）列**不能赋值** —— 硬改会让 provider 报错，
        # 整个编辑会话在保存时被回滚，用户之前的改动全丢。
        if i in pk:
            raise InvalidRequestError(
                f"字段「{name}」是要素 ID，不能修改",
                {"reason": "要素 ID 由数据源管理，改了会损坏图层"},
            )
        field = v.fields()[i]
        if not v.changeAttributeValue(int(fid), i, _cast_value(field, value)):
            raise DataError(f"字段「{name}」修改失败：{v.lastError()}")
        changed.append(name)

    v.updateExtents()
    return {"ref": layer.ref, "fid": int(fid), "changed": changed}


def delete_features(layer: VectorLayer, fids: Iterable[int]) -> dict:
    """删除要素。"""
    v = _vlayer(layer)
    if not v.isEditable():
        raise InvalidRequestError("请先进入编辑模式再删除要素")
    targets = [int(f) for f in (fids or [])]
    if not targets:
        raise InvalidRequestError("没有指定要删除的要素")
    if not v.deleteFeatures(targets):
        raise DataError(f"删除失败：{v.lastError()}")
    return {"ref": layer.ref, "deleted": len(targets), "fids": targets}


def add_feature(layer: VectorLayer, geometry: dict, attributes: dict | None = None) -> dict:
    """新增要素（几何是 GeoJSON）。"""
    from qgis.core import QgsFeature

    v = _vlayer(layer)
    if not v.isEditable():
        raise InvalidRequestError("请先进入编辑模式再新增要素")
    if not geometry:
        raise InvalidRequestError("缺少几何")

    geom = geometry_from_geojson(geometry)
    if geom.isNull() or geom.isEmpty():
        raise InvalidRequestError("几何为空")

    feature = QgsFeature(v.fields())
    feature.setGeometry(geom)
    index = _field_index(v)
    for name, value in (attributes or {}).items():
        i = index.get(name)
        if i is None:
            raise InvalidRequestError(f"字段不存在：{name}", {"available": list(index.keys())})
        feature.setAttribute(i, _cast_value(v.fields()[i], value))

    if not v.addFeature(feature):
        raise DataError(f"新增要素失败：{v.lastError()}")
    v.updateExtents()
    return {"ref": layer.ref, "fid": feature.id()}


def field_calculator(
    layer: VectorLayer,
    field: str,
    expression: str,
    only_selected: bool = False,
    selection: list[int] | None = None,
) -> dict:
    """字段计算器：按表达式批量给一个字段赋值。"""
    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest

    v = _vlayer(layer)
    if not v.isEditable():
        raise InvalidRequestError("请先进入编辑模式再运行字段计算器")
    index = _field_index(v)
    if field not in index:
        raise NotFoundError(f"字段不存在：{field}", {"available": list(index.keys())})
    # 主键（FID）列**不能赋值** —— 原来这里放行，界面提示「已更新 N 个要素」，
    # 一保存整个编辑会话被回滚（OGR：未能删除要素），用户之前的改动全丢。
    if index[field] in _fid_field_indexes(v):
        raise InvalidRequestError(
            f"字段「{field}」是要素 ID，不能作为计算目标",
            {"reason": "要素 ID 由数据源管理，改了会损坏图层"},
        )

    expr = QgsExpression(expression or "")
    if expr.hasParserError():
        raise InvalidRequestError(f"表达式无效：{expr.parserErrorString()}")

    context = QgsExpressionContext()
    try:
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(v))
    except Exception:
        pass

    request = QgsFeatureRequest()
    if only_selected and selection:
        request.setFilterFids([int(i) for i in selection])

    updated = 0
    try:
        for feature in v.getFeatures(request):
            context.setFeature(feature)
            value = expr.evaluate(context)
            if expr.hasEvalError():
                raise DataError(f"表达式求值失败：{expr.evalErrorString()}")
            if not v.changeAttributeValue(feature.id(), index[field], value):
                raise DataError(f"字段「{field}」写入失败：{v.lastError()}")
            updated += 1
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f"字段计算失败：{exc}") from exc

    v.updateExtents()
    return {"ref": layer.ref, "field": field, "updated": updated}


# --------------------------------------------------------------------- 概览


def layer_overview(layer: VectorLayer) -> dict:
    """「信息」面板：图层的关键信息一览。"""
    v = _vlayer(layer)
    provider = v.dataProvider()
    return {
        "ref": layer.ref,
        "title": layer.title,
        "dataset_id": layer.dataset_id,
        "source": layer.source,
        "scope": layer.scope,
        "file": layer.path,
        "provider": provider.name(),
        "crs": layer.crs,
        "geometry_type": layer.geometry_type,
        "feature_count": int(v.featureCount()),
        "field_count": len(v.fields()),
        "extent": list(layer.extent),
        "is_editing": bool(v.isEditable()),
        "capabilities": {
            "add": bool(provider.capabilities() & 0x00000008),
            "delete": bool(provider.capabilities() & 0x00000002),
            "change": bool(provider.capabilities() & 0x00000004),
        },
    }
