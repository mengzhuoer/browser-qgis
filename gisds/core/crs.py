"""坐标参考系（CRS）目录。

网页端「项目属性 → 坐标参考系」那一页的后端：
搜 CRS、看它的详情和适用范围、给它画一张范围预览图。

数据来自两处，各司其职：

* **proj.db**（PROJ 自带的 SQLite 库，QGIS 也用它）：CRS 的**名录** ——
  有哪些坐标系、叫什么、是地理还是投影、是否废弃。按名字搜索靠它。
* **QgsCoordinateReferenceSystem**（PyQGIS）：单个 CRS 的**权威详情** ——
  WKT、PROJ 字符串、椭球、适用范围（bounds）都能直接问它，比自己解析 WKT 可靠。

QGIS 的 ``QgsCoordinateReferenceSystem`` 没有「枚举全部 CRS」的接口（它自己也是查
proj.db 的），所以名录走 SQL、详情走 QGIS。
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

# 常用坐标系：网页端默认先显示这些，其余靠搜索。
# 全量 EPSG 有五千多条，一次全塞给浏览器没意义。
PREDEFINED: list[tuple[str, str]] = [
    ("EPSG", "4326"),    # WGS 84
    ("EPSG", "3857"),    # WGS 84 / Pseudo-Mercator（网页地图用的就是它）
    ("EPSG", "4490"),    # CGCS2000
    ("EPSG", "4547"),    # CGCS2000 / 3-degree Gauss-Kruger CM 117E
    ("EPSG", "4269"),    # NAD83
    ("EPSG", "4230"),    # ED50
    ("EPSG", "4979"),    # WGS 84 (3D)
    ("EPSG", "32650"),   # WGS 84 / UTM zone 50N
    ("EPSG", "32649"),   # WGS 84 / UTM zone 49N
    ("EPSG", "32651"),   # WGS 84 / UTM zone 51N
    ("EPSG", "32750"),   # WGS 84 / UTM zone 50S
    ("EPSG", "25848"),   # ETRS89 / UTM zone 48N
    ("EPSG", "25849"),
    ("EPSG", "25850"),
    ("EPSG", "26901"),   # NAD83 / UTM zone 1N
    ("EPSG", "2154"),    # RGF93 / Lambert-93
    ("EPSG", "3035"),    # ETRS89-extended / LAEA Europe
    ("EPSG", "5070"),    # NAD83 / Conus Albers
    ("EPSG", "3006"),    # SWEREF99 TM
    ("EPSG", "5514"),    # WGS 84 / 3-degree Gauss-Kruger zone 38
    ("EPSG", "3395"),    # WGS 84 / World Mercator
    ("EPSG", "4087"),    # WGS 84 / World Equidistant Cylindrical
    ("EPSG", "3413"),    # SVY21 / Singapore TM
    ("EPSG", "23750"),   # S-JTSK / Krovak East North
    ("EPSG", "2440"),    # S-JTSK / Ferro
]

_lock = threading.Lock()
_db_path: Path | None = None
_db_checked = False
_predefined_cache: list[tuple[str, str]] | None = None


def proj_db_path() -> Path | None:
    """找到 PROJ 的 proj.db（QGIS / OSGeo4W 自带）。"""
    global _db_path, _db_checked
    with _lock:
        if _db_checked:
            return _db_path
        _db_checked = True
        roots: list[Path] = []
        pkg = os.environ.get("QGIS_PREFIX_PATH") or ""
        if pkg:
            p = Path(pkg)
            roots += [p, p.parent, p.parent.parent]
        roots += [Path("C:/PROGRA~1/QGIS34~1.14"), Path("C:/OSGeo4W")]
        for root in roots:
            for rel in ("share/proj/proj.db", "apps/proj/proj.db", "share/proj/proj.db"):
                cand = root / rel
                if cand.is_file():
                    _db_path = cand
                    return _db_path
        return None


def _connect() -> sqlite3.Connection | None:
    path = proj_db_path()
    if path is None:
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _kind(row_type: str) -> str:
    t = (row_type or "").lower()
    if "geographic" in t:
        return "geographic"
    if "projected" in t:
        return "projected"
    if "geocentric" in t:
        return "geocentric"
    if "vertical" in t:
        return "vertical"
    if "compound" in t:
        return "compound"
    if "engineering" in t:
        return "engineering"
    if "derived" in t:
        return "derived"
    return t or "other"


def search_crs(text: str = "", limit: int = 60) -> list[dict]:
    """按关键字搜坐标参考系。

    纯数字 → 当作 EPSG 码；否则按名称 / 别名 / 编码模糊匹配。
    """
    text = (text or "").strip()
    limit = max(1, min(int(limit or 60), 300))
    con = _connect()
    if con is None:
        return []
    try:
        cur = con.cursor()
        if not text:
            cur.execute(
                "SELECT auth_name, code, name, type, deprecated FROM crs_view "
                "WHERE auth_name = 'EPSG' ORDER BY CAST(code AS INTEGER) LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            return [_row_to_item(r) for r in rows]

        if text.isdigit():
            cur.execute(
                "SELECT auth_name, code, name, type, deprecated FROM crs_view "
                "WHERE auth_name = 'EPSG' AND code = ?",
                (text,),
            )
            rows = cur.fetchall()
            if rows:
                return [_row_to_item(r) for r in rows]

        like = f"%{text}%"
        cur.execute(
            "SELECT DISTINCT c.auth_name, c.code, c.name, c.type, c.deprecated "
            "FROM crs_view c "
            "LEFT JOIN alias_name a ON a.table_name = c.table_name "
            "    AND a.auth_name = c.auth_name AND a.code = c.code "
            "WHERE (c.name LIKE ? OR a.alt_name LIKE ? OR c.code LIKE ? OR c.description LIKE ?) "
            "ORDER BY (c.code = ?) DESC, c.auth_name, c.code LIMIT ?",
            (like, like, like, like, text, limit),
        )
        return [_row_to_item(r) for r in cur.fetchall()]
    finally:
        con.close()


def _row_to_item(row) -> dict:
    return {
        "id": f"{row['auth_name']}:{row['code']}",
        "name": row["name"],
        "kind": _kind(row["type"]),
        "deprecated": bool(row["deprecated"]),
    }


def predefined_crs() -> list[dict]:
    """默认列表：常用坐标系（权威详情由 crs_detail 按需补）。"""
    out: list[dict] = []
    for auth, code in _valid_predefined():
        item = {"id": f"{auth}:{code}", "name": None, "kind": None, "deprecated": False}
        detail = crs_detail(auth, code)
        if detail:
            item.update({k: detail.get(k) for k in ("name", "kind", "deprecated")})
        out.append(item)
    return out


def _valid_predefined() -> list[tuple[str, str]]:
    """只留下 proj.db 里真有的那些。

    不先查一下就构造 CRS，PROJ 会往 stderr 打「crs not found」——
    自检输出里突然冒出一堆这种噪音，比没有还糟。
    """
    global _predefined_cache
    with _lock:
        if _predefined_cache is not None:
            return _predefined_cache
    con = _connect()
    if con is None:
        _predefined_cache = list(PREDEFINED)
        return _predefined_cache
    try:
        cur = con.cursor()
        good: list[tuple[str, str]] = []
        for auth, code in PREDEFINED:
            cur.execute(
                "SELECT 1 FROM crs_view WHERE auth_name = ? AND code = ? LIMIT 1",
                (auth, code),
            )
            if cur.fetchone():
                good.append((auth, code))
        _predefined_cache = good
        return good
    finally:
        con.close()


def crs_detail(auth: str, code: str) -> dict | None:
    """单个 CRS 的详情（名称、类型、WKT、适用范围……）。

    详情问 QGIS（权威），名录信息问 proj.db（权威）。

    注意 QGIS 3.44 的 ``QgsCoordinateReferenceSystem`` **没有** ``datum()`` /
    ``projectionMethod()`` / ``datumDescription()`` —— 基准和投影方法只能从
    WKT 里取（QGIS 自己的 CRS 对话框也是解析 WKT 树的）。
    """
    crs = _build_crs(auth, code)
    if crs is None or not crs.isValid():
        return None
    bbox = crs.bounds()
    extent = [bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()]
    try:
        proj = crs.toProj()
    except Exception:
        proj = ""
    wkt = ""
    try:
        wkt = crs.toWkt()
    except Exception:
        pass
    ellipsoid = crs.ellipsoidAcronym() or ""
    return {
        "id": f"{auth}:{code}",
        "name": crs.description(),
        "kind": "geographic" if crs.isGeographic() else "projected",
        "deprecated": bool(crs.isDeprecated()),
        "ellipsoid": ellipsoid,
        "ellipsoid_name": _ellipsoid_name(ellipsoid),
        "datum": _wkt_part(wkt, "DATUM"),
        "method": _wkt_part(wkt, "PROJECTION") or ("Geographic" if crs.isGeographic() else ""),
        "unit": _unit_of(crs),
        "wkt": wkt,
        "proj": proj,
        "extent": extent,
        "area_of_use": _area_text(extent),
    }


def _wkt_part(wkt: str, key: str) -> str:
    """从 WKT 里抠出某个节点的名称（``DATUM["WGS_1984", ...]`` → ``WGS_1984``）。"""
    m = re.search(rf'{key}\["([^"]+)"', wkt or "")
    return m.group(1) if m else ""


def _ellipsoid_name(authid: str) -> str:
    """椭球的完整名称（``EPSG:7030`` → ``WGS 84``）。"""
    if not authid or ":" not in authid:
        return ""
    auth, _, code = authid.partition(":")
    con = _connect()
    if con is None:
        return ""
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT name, description FROM ellipsoid WHERE auth_name = ? AND code = ? LIMIT 1",
            (auth, code),
        )
        row = cur.fetchone()
        if row is None:
            return ""
        return row[1] or row[0] or ""
    except Exception:
        return ""
    finally:
        con.close()


def _build_crs(auth: str, code: str):
    from qgis.core import QgsCoordinateReferenceSystem

    try:
        auth = (auth or "EPSG").strip().upper()
        code = str(code or "").strip()
        if auth.upper() == "EPSG" and code.isdigit():
            crs = QgsCoordinateReferenceSystem.fromEpsgId(int(code))
            if crs.isValid():
                return crs
        crs = QgsCoordinateReferenceSystem(f"{auth}:{code}")
        return crs if crs.isValid() else None
    except Exception:
        return None


def _unit_of(crs) -> str:
    """坐标单位（° / m …）。"""
    try:
        unit = crs.mapUnits()
        name = getattr(unit, "name", "") or ""
        return {"Degrees": "°", "Meters": "m", "Feet": "ft"}.get(name, name)
    except Exception:
        return ""


def _area_text(extent: list[float]) -> str:
    if not extent or len(extent) != 4:
        return ""
    minx, miny, maxx, maxy = extent
    return (f"经度 {minx:.4f}° ~ {maxx:.4f}°，纬度 {miny:.4f}° ~ {maxy:.4f}°"
            f"（{maxx - minx:.4f}° × {maxy - miny:.4f}°）")


# --------------------------------------------------------------------- 预览图

# 预览图的配色：对着 QGIS 那个 CRS 预览框的样子做的 ——
# 适用范围铺一层浅玫瑰，陆地是稍深一点的玫瑰，边界是更深的玫瑰线。
PREVIEW_BG = "#F6DCDC"
PREVIEW_LAND = "#EFC7C7"
PREVIEW_LINE = "#C97C7C"
PREVIEW_EDGE = "#A33B24"


def crs_preview_png(
    crs_id: str,
    land_layer=None,
    width: int = 360,
    height: int = 220,
) -> tuple[bytes, dict]:
    """画一张「这个 CRS 管哪块地方」的预览图。

    ``land_layer`` 是底图里的陆地矢量（可空 —— 没有底图时也能画，只是没有陆地轮廓）。
    画的是**地理坐标**（EPSG:4326）下的适用范围矩形：投影坐标系的适用范围
    （比如 UTM 50N）本身就是一小块经纬度区间，落在世界地图上正好是个小框。

    渲染本身交给 ``core.render.render_png`` —— 那份已经踩过坑
    （QGIS 3.44 headless 下顺序渲染 job 会出全黑图），别在这里另写一套。
    """
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFillSymbol,
        QgsGeometry,
        QgsMapRendererParallelJob,
        QgsMapSettings,
        QgsRectangle,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor

    from . import render as render_core

    auth, _, code = (crs_id or "").partition(":")
    crs = _build_crs(auth or "EPSG", code)
    if crs is None or not crs.isValid():
        raise ValueError(f"无法识别的坐标系：{crs_id}")
    box = crs.bounds()
    extent = [box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()]
    if not extent or extent[0] >= extent[2] or extent[1] >= extent[3]:
        extent = [-180.0, -90.0, 180.0, 90.0]

    layers = []
    # 陆地（画在「适用范围底色」之上）
    if land_layer is not None:
        try:
            symbol = QgsFillSymbol.createSimple({
                "color": QColor(PREVIEW_LAND).name(),
                "outline_color": QColor(PREVIEW_LINE).name(),
                "outline_width": "0.4",
            })
            renderer = land_layer.renderer()
            if hasattr(renderer, "setSymbol"):
                renderer.setSymbol(symbol)
            layers.append(land_layer)
        except Exception:
            pass

    # 适用范围矩形 —— 当底色，**放最后**。
    # QgsMapSettings 的图层顺序是「第一个画在最上面」（QGIS 约定），所以
    # 底色要排在最后；放前面的话它会把陆地整个盖住（全球范围时尤其明显：
    # 整张图变成一块平色，什么都看不出来）。
    rect_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "aou", "memory")
    if rect_layer.isValid():
        feat = QgsFeature(rect_layer.fields())
        feat.setGeometry(QgsGeometry.fromRect(QgsRectangle(*extent)))
        rect_layer.dataProvider().addFeature(feat)
        rect_layer.updateExtents()
        symbol = QgsFillSymbol.createSimple({
            "color": QColor(PREVIEW_BG).name(),
            "outline_color": QColor(PREVIEW_EDGE).name(),
            "outline_width": "0.8",
        })
        renderer = rect_layer.renderer()
        if hasattr(renderer, "setSymbol"):
            renderer.setSymbol(symbol)
        layers.append(rect_layer)

    if not layers:
        raise ValueError("没有可渲染的内容")

    out_w = max(64, min(int(width), 1200))
    out_h = max(64, min(int(height), 1200))
    # 范围按画布比例**适配**（短的那边撑开），保证适用范围完整可见。
    # 不适配的话 QGIS 会自己按画布比例把范围撑开（见 core/render.py 的注释），
    # 细长的适用范围（比如 UTM 的 6°×84°）会被拉成一个奇怪的大范围。
    span_x = max(extent[2] - extent[0], 1e-9)
    span_y = max(extent[3] - extent[1], 1e-9)
    target = out_w / out_h
    if span_x / span_y < target:
        grow = (span_y * target - span_x) / 2
        extent[0] -= grow
        extent[2] += grow
    elif span_x / span_y > target:
        grow = (span_x / target - span_y) / 2
        extent[1] -= grow
        extent[3] += grow

    # 自己定 extent：**不要**用 render.render_png —— 它会把传入范围和图层自身
    # 范围取并集，而陆地图层是全球 → 视图被撑成全球，就看不到「这个 CRS 管哪块」了。
    #
    # 顺序也要跟 core/render.py 一致：**先 setExtent 再 setOutputSize**。
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setBackgroundColor(QColor(PREVIEW_BG))
    settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    settings.setExtent(QgsRectangle(*extent))
    settings.setOutputSize(QSize(out_w, out_h))
    # 注意：QGIS 3.44 headless 下顺序渲染 job 会出全黑图，必须用并行 job。
    job = QgsMapRendererParallelJob(settings)
    job.start()
    render_core._wait_for_job(job)
    data = render_core._image_to_png(job.renderedImage())
    return data, {"crs": f"{auth}:{code}", "extent": extent, "width": out_w, "height": out_h}
