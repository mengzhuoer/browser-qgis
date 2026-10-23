"""地图渲染：专题配色出图 + XYZ 瓦片。

所有渲染都基于 QGIS 自带的 ``QgsMapSettings`` / ``QgsMapRendererSequentialJob``，
因此坐标系重投影、图层混合、山体阴影等全部走 QGIS 原生引擎。

渲染必须在单线程内完成——见 ``core/executor.py``。
"""

from __future__ import annotations

import math
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .errors import DataError, InvalidRequestError
from .registry import RasterLayer, VectorLayer, format_value

# 定性配色（Kelly 22 色，对色觉障碍相对友好）
PALETTE: list[str] = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
    "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080", "#e6beff",
    "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000", "#ffd8b1",
    "#000075", "#808080", "#ffff00", "#0000ff",
]

# 连续型渐变（viridis 近似）
SEQUENTIAL: list[str] = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]
# 其它可选的单波段渲染模式（对应 QGIS 符号系统的拉伸方式）
GRAYSCALE: list[str] = ["#000000", "#404040", "#808080", "#c0c0c0", "#ffffff"]
SPECTRAL: list[str] = ["#9e0142", "#f46d43", "#fee08b", "#e6f598", "#5e4fa2"]
RAMPS: dict[str, list[str]] = {
    "viridis": SEQUENTIAL,
    "gray": GRAYSCALE,
    "spectral": SPECTRAL,
}

_TILE_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_TILE_CACHE_MAX = 256


def configure_cache(max_entries: int) -> None:
    """设置瓦片缓存上限（启动时从配置读一次）。

    网页端现在**按层级出瓦片**了 —— 一屏就是 25 块上下，缓存太小的话
    平移一下就全被挤掉，瓦片要重渲。配置项 `tile_cache_size` 之前根本
    没接上（这里写死 256），现在接上了。
    """
    global _TILE_CACHE_MAX
    _TILE_CACHE_MAX = max(32, int(max_entries))


# --------------------------------------------------------------------- 样式


def _ramp_item_class():
    from qgis.core import QgsColorRampShader

    item = getattr(QgsColorRampShader, "ColorRampItem", None)
    if item is not None:
        return item
    from qgis.core import QgsColorRampShaderColorRampItem  # type: ignore

    return QgsColorRampShaderColorRampItem


def _ramp_type(name: str):
    from qgis.core import QgsColorRampShader

    enum = getattr(QgsColorRampShader, "Type", None)
    if enum is not None and hasattr(enum, name):
        return getattr(enum, name)
    return getattr(QgsColorRampShader, name)


def _numeric_keys(classes: dict[str, str]) -> list[float]:
    out = []
    for key in classes:
        try:
            out.append(float(key))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _is_truecolor(rl: RasterLayer) -> bool:
    """这个栅格是不是**真彩色合成影像**。

    判据是「QGIS 自己给它配了什么渲染器」：多波段彩色渲染器
    （``QgsMultiBandColorRenderer``，红/绿/蓝 = 1/2/3 波段）就是 QGIS 认出来的
    真彩色。用 QGIS 的判据而不是我们自己数波段，是因为 QGIS 还认得出
    彩色表（palette）、带 alpha 的波段等等。
    """
    from .registry import is_truecolor_renderer

    return is_truecolor_renderer(getattr(rl, "default_renderer", None))


def build_raster_style(
    rl: RasterLayer, colors: dict[str, str] | None = None, mode: str | None = None
) -> None:
    """给栅格图层套上配色（离散型=类别色，连续型=渐变）。

    ``colors`` 是「取值 -> #rrggbb」的覆盖表（来自网页的符号系统面板），
    ``mode`` 是连续型的渲染模式：viridis / gray / spectral。

    **真彩色合成影像不动它**（除非用户在符号系统面板里明确挑了配色）：
    遥感照片是多波段的，QGIS 桌面版给它配的是「红/绿/蓝 = 1/2/3 波段」的
    多波段彩色渲染器。原来我们一律换成「单波段 + viridis 渐变」——
    只用第 1 波段出图，绿色通道直接被压没了（实测平均 RGB 从
    [28.6, 33.0, 37.4] 变成 [50.8, 12.0, 71.0]），整张图**发紫**，
    跟 QGIS 完全不一样。渐变的第一档色 `#440154` 本身就是深紫。
    """
    from qgis.core import (
        QgsColorRampShader,
        QgsRasterShader,
        QgsSingleBandPseudoColorRenderer,
    )
    from qgis.PyQt.QtGui import QColor

    if not (colors or mode) and _is_truecolor(rl):
        # 还原 QGIS 自己的渲染方式（每次 clone 一份：setRenderer 会接管所有权）
        try:
            rl.layer.setRenderer(rl.default_renderer.clone())
        except Exception:
            pass
        return
    if (mode or "").strip().lower() == "truecolor" and _is_truecolor(rl):
        # 网页端符号系统面板里明确选了「真彩色」—— 同样走 QGIS 的渲染方式
        try:
            rl.layer.setRenderer(rl.default_renderer.clone())
        except Exception:
            pass
        return

    provider = rl.layer.dataProvider()

    if rl.kind == "categorical":
        values = list(rl.values) or _numeric_keys(rl.classes)
        if not values:
            values = [0.0, 1.0]
        item_cls = _ramp_item_class()
        items = []
        for index, value in enumerate(values):
            key = format_value(float(value))
            label = rl.classes.get(key, f"Class {key}")
            color = None
            if colors:
                color = colors.get(key) or colors.get(str(value))
            if not color:
                color = PALETTE[index % len(PALETTE)]
            items.append(item_cls(float(value), QColor(color), label))
        shader_fn = QgsColorRampShader()
        shader_fn.setColorRampType(_ramp_type("Discrete"))
        shader_fn.setColorRampItemList(items)
    else:
        minimum, maximum = 0.0, 1.0
        try:
            from qgis.core import QgsRasterBandStats

            stats = provider.bandStatistics(1, QgsRasterBandStats.All)
            minimum = float(stats.minimumValue)
            maximum = float(stats.maximumValue)
        except Exception:
            pass
        if maximum <= minimum:
            maximum = minimum + 1.0
        ramp = RAMPS.get((mode or "viridis").lower(), SEQUENTIAL)
        item_cls = _ramp_item_class()
        stops = 4
        items = []
        for index in range(stops + 1):
            ratio = index / stops
            value = minimum + (maximum - minimum) * ratio
            color = QColor(ramp[min(index, len(ramp) - 1)])
            items.append(item_cls(value, color, f"{value:.3g}"))
        shader_fn = QgsColorRampShader()
        shader_fn.setColorRampType(_ramp_type("Interpolated"))
        shader_fn.setColorRampItemList(items)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)
    rl.layer.setRenderer(QgsSingleBandPseudoColorRenderer(provider, 1, shader))


def build_vector_style(vl: VectorLayer, color: str = "#4c78a8") -> None:
    """给矢量图层套上简单填充样式。"""
    from qgis.core import QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol
    from qgis.PyQt.QtGui import QColor

    layer = vl.layer
    geom_type = (layer.geometryType() or 0)
    try:
        if geom_type == 0:  # 点
            symbol = QgsMarkerSymbol.createSimple({"name": "circle", "color": color, "size": "3"})
            renderer = layer.renderer()
            if hasattr(renderer, "setSymbol"):
                renderer.setSymbol(symbol)
        elif geom_type == 1:  # 线
            symbol = QgsLineSymbol.createSimple({"color": color, "width": "0.6"})
            renderer = layer.renderer()
            if hasattr(renderer, "setSymbol"):
                renderer.setSymbol(symbol)
        else:  # 面
            symbol = QgsFillSymbol.createSimple(
                {"color": QColor(color).lighter(150).name(), "outline_color": color, "outline_width": "0.4"}
            )
            renderer = layer.renderer()
            if hasattr(renderer, "setSymbol"):
                renderer.setSymbol(symbol)
    except Exception:
        pass


# --------------------------------------------------------------------- 渲染


def _wait_for_job(job) -> None:
    """阻塞等待渲染任务结束。

    优先用 ``waitForFinished()``；若该版本没有这个方法，退回事件循环等待。
    """
    try:
        job.waitForFinished()
        return
    except Exception:
        pass
    from qgis.PyQt.QtCore import QEventLoop

    loop = QEventLoop()
    job.finished.connect(loop.quit)
    job.start()
    loop.exec_()


def _image_to_png(image) -> bytes:
    from qgis.PyQt.QtCore import QBuffer, QByteArray

    buffer = QByteArray()
    qbuffer = QBuffer(buffer)
    qbuffer.open(QBuffer.ReadWrite)
    image.save(qbuffer, "PNG")
    qbuffer.close()
    data = bytes(buffer)
    if data:
        return data
    # 兜底：落临时文件
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        image.save(path, "PNG")
        return Path(path).read_bytes()
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def _union_extent(qgis_layers, dst_crs: str):
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext, QgsRectangle

    acc = QgsRectangle()
    acc.setMinimal()
    target = QgsCoordinateReferenceSystem(dst_crs)
    for layer in qgis_layers:
        ext = layer.extent()
        if ext.isEmpty():
            continue
        src = layer.crs()
        try:
            if src.isValid() and src != target:
                tr = QgsCoordinateTransform(src, target, QgsCoordinateTransformContext())
                ext = tr.transformBoundingBox(ext)
        except Exception:
            pass
        acc.combineExtentWith(ext)
    return acc


def render_png(
    layers: list[Any],
    bbox: tuple[float, float, float, float] | None = None,
    width: int = 1024,
    height: int | None = None,
    dst_crs: str = "EPSG:4326",
    background: tuple[int, int, int, int] = (255, 255, 255, 0),
    bbox_crs: str = "EPSG:4326",
) -> tuple[bytes, dict]:
    """渲染出 PNG。layers 为 QgsRasterLayer / QgsVectorLayer 列表。

    ``bbox`` 的坐标系由 ``bbox_crs`` 声明（默认 EPSG:4326）。要出 EPSG:3857
    的图但手里拿的是 3857 的范围时，**必须**同时给 ``dst_crs="EPSG:3857"``
    和 ``bbox_crs="EPSG:3857"`` —— 否则这里会再做一次 4326→3857 转换，
    范围直接飞到天边，出一张全白的图。
    """
    from qgis.core import QgsCoordinateReferenceSystem, QgsMapRendererParallelJob, QgsMapSettings
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor

    if not layers:
        raise InvalidRequestError("没有可渲染的图层")

    out_w = max(16, min(int(width), 4096))

    settings = QgsMapSettings()
    settings.setLayers(list(layers))
    settings.setBackgroundColor(QColor(*background))
    settings.setOutputDpi(96)
    settings.setDestinationCrs(QgsCoordinateReferenceSystem(dst_crs))

    if bbox:
        from qgis.core import QgsCoordinateTransform, QgsCoordinateTransformContext, QgsRectangle

        minx, miny, maxx, maxy = bbox
        rect = QgsRectangle(minx, miny, maxx, maxy)
        target = QgsCoordinateReferenceSystem(dst_crs)
        source = QgsCoordinateReferenceSystem(bbox_crs)
        if target.authid() != source.authid():
            try:
                tr = QgsCoordinateTransform(source, target, QgsCoordinateTransformContext())
                rect = tr.transformBoundingBox(rect)
            except Exception:
                pass
        settings.setExtent(rect)
        # 出图长宽比必须与范围一致，否则 QGIS 会把范围扩成画布比例，
        # 边上就留下空白。高度没给时按范围比例算。
        if height is None:
            span_x = max(rect.width(), 1e-9)
            span_y = max(rect.height(), 1e-9)
            out_h = max(16, min(int(round(out_w * span_y / span_x)), 4096))
        else:
            out_h = max(16, min(int(height), 4096))
    else:
        ext = _union_extent(layers, dst_crs)
        if ext.isEmpty():
            raise InvalidRequestError("图层范围为空，无法渲染")
        settings.setExtent(ext)
        if height is None:
            out_h = max(16, min(int(round(out_w * max(ext.height(), 1e-9) / max(ext.width(), 1e-9))), 4096))
        else:
            out_h = max(16, min(int(height), 4096))

    settings.setOutputSize(QSize(out_w, out_h))

    try:
        # 注意：QgsMapRendererSequentialJob 在 QGIS 3.44 headless 下会输出全黑图，
        # 因此使用并行渲染任务 + 阻塞等待。
        job = QgsMapRendererParallelJob(settings)
        job.start()
        _wait_for_job(job)
        image = job.renderedImage()
    except Exception as exc:
        raise DataError(f"地图渲染失败：{exc}") from exc

    data = _image_to_png(image)
    meta = {
        "width": out_w,
        "height": out_h,
        "dst_crs": dst_crs,
        "layers": len(layers),
        "bytes": len(data),
    }
    return data, meta


def render_raster_png(
    rl: RasterLayer,
    bbox: tuple[float, float, float, float] | None = None,
    width: int = 1024,
    height: int | None = None,
    dst_crs: str = "EPSG:4326",
    apply_style: bool = True,
    colors: dict[str, str] | None = None,
    mode: str | None = None,
    bbox_crs: str = "EPSG:4326",
) -> tuple[bytes, dict]:
    if apply_style:
        build_raster_style(rl, colors=colors, mode=mode)
    return render_png([rl.layer], bbox, width, height, dst_crs, bbox_crs=bbox_crs)


def render_vector_png(
    vl: VectorLayer,
    bbox: tuple[float, float, float, float] | None = None,
    width: int = 1024,
    height: int | None = None,
    dst_crs: str = "EPSG:4326",
    bbox_crs: str = "EPSG:4326",
) -> tuple[bytes, dict]:
    build_vector_style(vl)
    return render_png([vl.layer], bbox, width, height, dst_crs, bbox_crs=bbox_crs)


def render_combined_png(
    rasters: list[RasterLayer],
    vectors: list[VectorLayer],
    bbox: tuple[float, float, float, float] | None = None,
    width: int = 1024,
    height: int | None = None,
    dst_crs: str = "EPSG:4326",
    bbox_crs: str = "EPSG:4326",
) -> tuple[bytes, dict]:
    for rl in rasters:
        build_raster_style(rl)
    for vl in vectors:
        build_vector_style(vl)
    layers = [rl.layer for rl in rasters] + [vl.layer for vl in vectors]
    return render_png(layers, bbox, width, height, dst_crs, bbox_crs=bbox_crs)


# --------------------------------------------------------------------- 底图

# 内置离线底图的配色。走的是「纸面制图」的路子：
# 海洋是淡蓝灰底、陆地是纸白、国界是细灰线、河流淡蓝。
BASEMAP_OCEAN = "#DBE5EA"
BASEMAP_STYLES: dict[str, dict] = {
    "land": {"fill": "#F8F6F0", "stroke": "#C9C6B7", "width": 0.25},
    "boundaries": {"fill": "none", "stroke": "#8D968D", "width": 0.4},
    "lakes": {"fill": BASEMAP_OCEAN, "stroke": "#B4C6D0", "width": 0.15},
    "rivers": {"fill": "none", "stroke": "#A9C7D7", "width": 0.45},
}
# 底图各图层的叠放次序（越靠后越在上面）
BASEMAP_ORDER = ("land", "boundaries", "lakes", "rivers")


def build_basemap_style(vl: VectorLayer, role: str) -> None:
    """给底图矢量图层套上专用配色。"""
    from qgis.core import QgsFillSymbol, QgsLineSymbol
    from qgis.PyQt.QtGui import QColor

    style = BASEMAP_STYLES.get(role) or BASEMAP_STYLES["land"]
    layer = vl.layer
    geom_type = layer.geometryType() or 0
    try:
        if geom_type == 0:  # 点（底图基本不用）
            symbol = QgsMarkerSymbol.createSimple(
                {"name": "circle", "color": style["stroke"], "size": "1.6"}
            )
            renderer = layer.renderer()
            if hasattr(renderer, "setSymbol"):
                renderer.setSymbol(symbol)
            return
        if style["fill"] == "none":
            symbol = QgsLineSymbol.createSimple(
                {"color": style["stroke"], "width": str(style["width"])}
            )
        else:
            symbol = QgsFillSymbol.createSimple(
                {
                    "color": QColor(style["fill"]).name(),
                    "outline_color": style["stroke"],
                    "outline_width": str(style["width"]),
                }
            )
        renderer = layer.renderer()
        if hasattr(renderer, "setSymbol"):
            renderer.setSymbol(symbol)
    except Exception:
        pass


def render_basemap_tile(
    vectors: list[VectorLayer],
    roles: list[str],
    z: int,
    x: int,
    y: int,
    use_cache: bool = True,
) -> tuple[bytes, dict]:
    """渲染一张底图瓦片（多个矢量要素叠在一张 PNG 上）。

    ``vectors`` 与 ``roles`` 一一对应，roles 决定配色与叠放次序。
    """
    key = f"basemap/{z}/{x}/{y}"
    if use_cache and key in _TILE_CACHE:
        _TILE_CACHE.move_to_end(key)
        return _TILE_CACHE[key], {"cached": True, "z": z, "x": x, "y": y}

    layers = []
    pairs = sorted(zip(roles, vectors), key=lambda p: BASEMAP_ORDER.index(p[0]) if p[0] in BASEMAP_ORDER else 99)
    for role, vl in pairs:
        build_basemap_style(vl, role)
        layers.append(vl.layer)

    rect = tile_bbox_3857(z, x, y)
    bbox = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
    data, meta = render_png(
        layers, bbox, 256, 256, "EPSG:3857", background=_bg(BASEMAP_OCEAN), bbox_crs="EPSG:3857"
    )
    meta.update({"z": z, "x": x, "y": y, "cached": False, "kind": "basemap"})

    if use_cache:
        _TILE_CACHE[key] = data
        while len(_TILE_CACHE) > _TILE_CACHE_MAX:
            _TILE_CACHE.popitem(last=False)
    return data, meta


def _bg(color: str) -> tuple[int, int, int, int]:
    """#rrggbb -> (r,g,b,255)。"""
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255)


# --------------------------------------------------------------------- 瓦片


def tile_bbox_3857(z: int, x: int, y: int):
    """XYZ 瓦片 -> EPSG:3857 范围。

    **先校验坐标**：z 或 y 越界时下面那个 ``math.sinh`` 会直接 OverflowError
    （真出过：``/api/tiles/example/classes/-30/0/0.png`` → 500）。
    这里抛 ValueError，由应用的异常处理器转成 422。
    """
    from qgis.core import QgsRectangle

    if not (0 <= z <= 22):
        raise ValueError(f"瓦片层级超出范围：z={z}（允许 0~22）")
    limit = 2**z
    if not (0 <= x < limit) or not (0 <= y < limit):
        raise ValueError(f"瓦片行列超出范围：z={z} 时 x / y 必须在 0 ~ {limit - 1} 之间")

    n = 2.0**z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0

    def merc_y(lat_deg: float) -> float:
        """EPSG:3857 的 y = R·ln(tan(π/4 + φ/2))，R 取 WGS84 长半轴。

        **不能用 R·radians(φ)** —— 那是等距圆柱近似，在中纬度能差出好几百公里：
        瓦片范围会整体偏移，同一张地图在不同层级之间对不齐，瓦片还会渲成空白。
        """
        lat = max(min(lat_deg, 85.05112878), -85.05112878)
        phi = math.radians(lat)
        return 6378137.0 * math.log(math.tan(math.pi / 4.0 + phi / 2.0))

    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    x_min = math.radians(lon_min) * 6378137.0
    x_max = math.radians(lon_max) * 6378137.0
    return QgsRectangle(x_min, merc_y(lat_min), x_max, merc_y(lat_max))


def render_tile(
    rl: RasterLayer,
    z: int,
    x: int,
    y: int,
    use_cache: bool = True,
    colors: dict[str, str] | None = None,
    mode: str | None = None,
) -> tuple[bytes, dict]:
    """渲染一张 Web 墨卡托瓦片。

    按**层级**逐块出图（而不是把整层渲成一张大图铺上去）——
    那样放大时才看得清细节：整层渲成 1400px 的话，1 个图像像素就对应
    6 个源像素，放大必然糊。

    缓存键**必须带上样式**：否则自定义配色的请求会命中默认样式的缓存，
    界面上改了配色却还是老样子。
    """
    style_key = _style_cache_key(colors, mode)
    key = f"{rl.dataset_id}/{rl.id}/{z}/{x}/{y}/{rl.width}x{rl.height}/{style_key}"
    if use_cache and key in _TILE_CACHE:
        _TILE_CACHE.move_to_end(key)
        return _TILE_CACHE[key], {"cached": True, "z": z, "x": x, "y": y}

    build_raster_style(rl, colors=colors, mode=mode)
    rect = tile_bbox_3857(z, x, y)
    bbox = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
    # tile_bbox_3857 给的已经是 3857 的范围，别再让 render_png 二次转换
    data, meta = render_png([rl.layer], bbox, 256, 256, "EPSG:3857", bbox_crs="EPSG:3857")
    meta.update({"z": z, "x": x, "y": y, "cached": False})

    if use_cache:
        _TILE_CACHE[key] = data
        while len(_TILE_CACHE) > _TILE_CACHE_MAX:
            _TILE_CACHE.popitem(last=False)
    return data, meta


def _style_cache_key(colors: dict[str, str] | None, mode: str | None) -> str:
    """样式进缓存键：配色或渲染模式一变，出图就变了，缓存必须分开。"""
    if not colors and not mode:
        return "default"
    parts: list[str] = []
    if mode:
        parts.append("m=" + str(mode))
    if colors:
        parts.append("c=" + ",".join(f"{k}:{v}" for k, v in sorted(colors.items())))
    return "|".join(parts)


def clear_tile_cache() -> int:
    count = len(_TILE_CACHE)
    _TILE_CACHE.clear()
    return count
