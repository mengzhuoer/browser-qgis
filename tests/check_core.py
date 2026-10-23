"""内核自检：注册 -> 点查询 -> 范围统计 -> 矢量 -> 渲染。

针对中性合成示例数据（tests/sample_data/shared/example）：
一张 6 类分区栅格、一张连续曲面、一个范围面。

独立运行（不需要 pytest）::

    run-qgis-python.bat tests/check_core.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gisds import qgis_env  # noqa: E402
from gisds.core import raster as raster_core  # noqa: E402
from gisds.core import render as render_core  # noqa: E402
from gisds.core import vector as vector_core  # noqa: E402
from gisds.core.registry import Registry  # noqa: E402
from pngutil import color_clusters, pixels  # noqa: E402

SAMPLE_ROOT = PROJECT_ROOT / "tests" / "sample_data"

# 示例数据的已知属性（与 tests/make_sample_data.py 一致）
EXPECTED_WIDTH = 320
EXPECTED_HEIGHT = 260
EXPECTED_CLASSES = 6

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 48 = 扫描数据目录 10 + 点查询 4 + 范围统计 7 + 连续型 2 + 直方图 1
#            + 多边形 2 + 矢量查询 8 + 渲染 5 + 无 CRS 栅格 4 + 真彩色栅格 5。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 48


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    print("== 初始化 QGIS ==")
    qgis_env.init_qgis()
    info = qgis_env.runtime_info()
    print(f"  QGIS {info['qgis_version']} | Python {info['python']} | prefix={info['prefix_path']}")

    print("\n== 扫描数据目录 ==")
    registry = Registry(SAMPLE_ROOT)
    datasets = registry.scan()
    check("发现 1 个数据集", len(datasets) == 1, f"实际 {len(datasets)}")
    if not datasets:
        print("  没有数据，无法继续")
        return 1
    dataset = datasets[0]
    print(f"  数据集: {dataset.id} / {dataset.name}")
    check("数据集 id 为 example", dataset.id == "example", f"实际 {dataset.id}")
    check("识别出 2 个栅格图层", len(dataset.layers) == 2, f"实际 {len(dataset.layers)}")
    check("识别出 1 个矢量图层", len(dataset.vectors) == 1, f"实际 {len(dataset.vectors)}")

    classes = registry.get_raster("example", "classes")
    check("classes 判定为离散型", classes.kind == "categorical", f"实际 {classes.kind}")
    check("classes 类别表来自 metadata", len(classes.classes) == EXPECTED_CLASSES, f"实际 {len(classes.classes)}")
    check("classes 抽到 6 个取值", len(classes.values) == EXPECTED_CLASSES, f"实际 {len(classes.values)}")
    check("classes 尺寸 320x260",
          classes.width == EXPECTED_WIDTH and classes.height == EXPECTED_HEIGHT,
          f"实际 {classes.width}x{classes.height}")
    check("classes 标题来自 metadata", classes.title == "分区（离散）", f"实际 {classes.title}")
    surface = registry.get_raster("example", "surface")
    check("surface 判定为连续型", surface.kind == "continuous", f"实际 {surface.kind}")

    print("\n== 点查询 ==")
    minx, miny, maxx, maxy = classes.extent_wgs84
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    hit = raster_core.point_query(classes, cx, cy)
    print(f"  中心点 ({cx:.4f}, {cy:.4f}) -> value={hit['value']} class={hit.get('class_name')}")
    check("点查询返回数值", hit["value"] is not None)
    check("点查询带类别名", bool(hit.get("class_name")))
    check("中心点落在 '类别六'（同心圆中心）",
          hit.get("class_name") == "类别六", f"实际 {hit.get('class_name')}")

    edge = raster_core.point_query(classes, 0.0, 0.0)
    check("范围外点返回 None", edge["value"] is None)

    print("\n== 范围统计（整幅） ==")
    stats = raster_core.bbox_stats(classes, (minx, miny, maxx, maxy))
    total = sum(item["pixels"] for item in stats["classes"])
    check("类别覆盖全部有效像元", total == stats["valid_pixels"], f"{total} vs {stats['valid_pixels']}")
    check("有效像元数 = 320*260",
          stats["valid_pixels"] == EXPECTED_WIDTH * EXPECTED_HEIGHT,
          f"实际 {stats['valid_pixels']}")
    check("面积换算成功", stats["area_m2"] and stats["area_m2"] > 0, f"实际 {stats['area_m2']}")
    check("统计精确（未降采样）", stats["exact"] is True)
    for item in stats["classes"][:3]:
        print(f"    {item['name']:<8} {item['pixels']:>7} px  {item['area_ha']:>9.2f} ha")
    check("类别表覆盖 6 类", len(stats["classes"]) == EXPECTED_CLASSES, f"实际 {len(stats['classes'])}")
    top = stats["classes"][0]
    check("最大类别是 '类别一'（背景分区）", top["name"] == "类别一", f"实际 {top['name']}")
    check("类别表含 '类别六'", any(c["name"] == "类别六" for c in stats["classes"]))

    print("\n== 连续型统计 ==")
    dstats = raster_core.bbox_stats(surface, (minx, miny, maxx, maxy))
    check("连续型给出 min/max/mean", dstats["stats"] and "mean" in dstats["stats"])
    print(f"    曲面 mean={dstats['stats']['mean']:.1f} "
          f"min={dstats['stats']['min']:.1f} max={dstats['stats']['max']:.1f}")
    check("曲面取值范围合理（780~1080）",
          700 < dstats["stats"]["min"] and dstats["stats"]["max"] < 1200,
          f"实际 {dstats['stats']['min']}~{dstats['stats']['max']}")

    print("\n== 直方图 ==")
    hist = raster_core.layer_histogram(surface)
    check("直方图有 bin", len(hist["bins"]) > 0, f"实际 {len(hist['bins'])}")

    print("\n== 多边形统计 ==")
    poly = [
        [minx + (maxx - minx) * 0.2, miny + (maxy - miny) * 0.2],
        [maxx - (maxx - minx) * 0.2, miny + (maxy - miny) * 0.2],
        [maxx - (maxx - minx) * 0.2, maxy - (maxy - miny) * 0.2],
        [minx + (maxx - minx) * 0.2, maxy - (maxy - miny) * 0.2],
    ]
    pstats = raster_core.polygon_stats(classes, poly)
    check("多边形统计命中像元", pstats["valid_pixels"] > 0, f"实际 {pstats['valid_pixels']}")
    check("多边形像元数少于整幅", pstats["valid_pixels"] < stats["valid_pixels"])
    print(f"    多边形内 {pstats['valid_pixels']} 像元，类别 {len(pstats['classes'])} 个")

    print("\n== 矢量查询 ==")
    boundary = registry.get_vector("example", "boundary")
    feats = vector_core.list_features(boundary, limit=10)
    check("取到 1 个要素", feats["count"] == 1, f"实际 {feats['count']}")
    feature = feats["features"][0]
    check("要素有几何", feature["geometry"] is not None)
    check("几何类型为 Polygon", feature["geometry"]["type"] == "Polygon", f"实际 {feature['geometry']}")
    check("属性带名称", feature["properties"].get("name") == "示例范围", f"实际 {feature['properties']}")
    check("属性带面积字段", "area_km2" in feature["properties"], f"实际 {feature['properties']}")

    vstats = vector_core.vector_stats(boundary)
    check("矢量统计给出字段", len(vstats["fields"]) >= 1)
    check("矢量范围有效", vstats["extent_wgs84"][2] > vstats["extent_wgs84"][0])

    vhit = vector_core.vector_hit(boundary, cx, cy)
    check("点命中范围面", vhit["count"] == 1, f"实际 {vhit['count']}")

    print("\n== 渲染 ==")
    png, meta = render_core.render_raster_png(classes, (minx, miny, maxx, maxy), width=600)
    check("渲染出 PNG", png[:8] == b"\x89PNG\r\n\x1a\n", f"头部 {png[:8]!r}")
    check("PNG 体积合理", len(png) > 2000, f"实际 {len(png)}")
    print(f"    PNG {meta['width']}x{meta['height']}, {len(png) / 1024:.1f} KB")
    out_png = PROJECT_ROOT / "tests" / "out_render.png"
    out_png.write_bytes(png)
    print(f"    已保存 {out_png}")

    # 瓦片坐标按图层范围动态推算，避免手算错
    import math

    def lon_to_tile_x(lon, z):
        return int((lon + 180.0) / 360.0 * (2**z))

    def lat_to_tile_y(lat, z):
        rad = math.radians(max(min(lat, 85.05112878), -85.05112878))
        return int((1.0 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2.0 * (2**z))

    z = 13
    tx = lon_to_tile_x(cx, z)
    ty = lat_to_tile_y(cy, z)
    tile, tmeta = render_core.render_tile(classes, z, tx, ty)
    check("瓦片渲染成功", tile[:8] == b"\x89PNG\r\n\x1a\n")
    print(f"    tile {z}/{tx}/{ty} -> {len(tile)} bytes")

    _, tmeta2 = render_core.render_tile(classes, z, tx, ty)
    check("瓦片命中缓存", tmeta2.get("cached") is True)

    # 瓦片是透明背景的 PNG（叠加在地图上不遮挡），所以体积比不透明时小得多。
    # 但「体积 > 200」证明不了有内容 —— 一张全白 PNG 同样是 200、体积也不小，
    # 所以这里解像素数颜色簇：空白瓦片只有一种颜色（或全透明），有内容至少两种。
    clusters = color_clusters(pixels(tile))
    check("瓦片有实际内容（像素级）", clusters >= 2, f"{clusters} 种颜色")

    print("\n== 没有坐标系信息的 GeoTIFF（导入后显示不出来的那个坑）==")
    check_nocrs_raster()

    print("\n== 真彩色遥感影像不能被渲成一片紫 ==")
    check_truecolor_raster()

    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    return 0 if _FAILED == 0 else 1


def check_truecolor_raster() -> None:
    """**真彩色遥感影像之前整张图发紫。**

    原因：``build_raster_style()`` 无条件把栅格渲染器换成「单波段 + viridis 渐变」，
    只用第 1 波段出图 —— 绿色通道直接被压没了（实测平均 RGB 从
    [28.6, 33.0, 37.4] 变成 [50.8, 12.0, 71.0]），整片**发紫**，跟 QGIS 桌面版
    完全不一样。渐变的第一档色 ``#440154`` 本身就是深紫。

    QGIS 桌面版给多波段影像配的是「红/绿/蓝 = 1/2/3 波段」的多波段彩色渲染器；
    修法是**别用单波段假彩色盖掉它**。
    """
    import shutil
    import tempfile

    import numpy as np
    from osgeo import gdal
    from qgis.core import QgsMultiBandColorRenderer

    tmp = Path(tempfile.mkdtemp(prefix="gisds_truecolor_"))
    try:
        ds_dir = tmp / "shared" / "rgb"
        ds_dir.mkdir(parents=True, exist_ok=True)
        tif = ds_dir / "rgb.tif"
        # 3 波段真彩色：内容是**灰阶**（R=G=B）。
        # 真彩色渲染出来 R/G/B 应该基本相等；被假彩色渲成渐变就会变成 R/B 高、G 低的紫。
        gds = gdal.GetDriverByName("GTiff").Create(str(tif), 64, 48, 3, gdal.GDT_Byte)
        grad = np.fromfunction(
            lambda y, x: (x * 255) // 63, (48, 64), dtype=int
        ).astype("uint8")
        for band in (1, 2, 3):
            gds.GetRasterBand(band).WriteArray(grad)
        gds.GetRasterBand(1).SetColorInterpretation(gdal.GCI_RedBand)
        gds.GetRasterBand(2).SetColorInterpretation(gdal.GCI_GreenBand)
        gds.GetRasterBand(3).SetColorInterpretation(gdal.GCI_BlueBand)
        gds.SetGeoTransform([-112.56, 0.0005, 0, 39.75, 0, -0.0005])
        gds.FlushCache()
        gds = None

        reg = Registry(tmp)
        reg.scan()
        layer = reg.datasets()[0].layers["rgb"]
        check("真彩色影像被认出来", layer.to_dict().get("truecolor") is True,
              str(layer.to_dict().get("truecolor")))

        render_core.build_raster_style(layer)
        check("套配色后仍是多波段彩色渲染器",
              isinstance(layer.layer.renderer(), QgsMultiBandColorRenderer),
              type(layer.layer.renderer()).__name__)

        png, _ = render_core.render_png([layer.layer], None, 96, 96, "EPSG:4326")
        px = pixels(png)[:, :, :3].reshape(-1, 3).astype(float)
        mean = px.mean(axis=0)
        check("真彩色出图不是紫色的（绿通道没被压没）",
              abs(mean[0] - mean[1]) < 30 and abs(mean[2] - mean[1]) < 30,
              f"平均 RGB = {mean.round(1)}")

        # 显式要求单波段渐变时，假彩色**照旧**能用（那是用户主动选的）
        render_core.build_raster_style(layer, mode="viridis")
        check("显式 mode=viridis 仍走渐变",
              not isinstance(layer.layer.renderer(), QgsMultiBandColorRenderer),
              type(layer.layer.renderer()).__name__)

        # 对照组：单波段栅格不算真彩色
        one = ds_dir / "one.tif"
        gds = gdal.GetDriverByName("GTiff").Create(str(one), 32, 32, 1, gdal.GDT_Byte)
        gds.GetRasterBand(1).WriteArray(grad[:32, :32])
        gds.SetGeoTransform([-112.56, 0.0005, 0, 39.75, 0, -0.0005])
        gds.FlushCache()
        gds = None
        reg2 = Registry(tmp)
        reg2.scan()
        one_layer = reg2.datasets()[0].layers["one"]
        check("单波段栅格不算真彩色",
              one_layer.to_dict().get("truecolor") is False,
              str(one_layer.to_dict().get("truecolor")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_nocrs_raster() -> None:
    """**没有 CRS 定义的 GeoTIFF 之前渲染出来是全白的。**

    原因：QGIS 渲染时要把图层变换到目标 CRS（3857/4326），而图层自己的 CRS
    无效 → 变换做不了 → 整层被跳过 → 出来一张合法的空图。
    表现是「上传成功、图层面板里也有、地图上什么都看不见」，最容易被误判成
    「文件坏了」。

    修法（core/registry.py 的 `_ensure_crs`）：注册时发现 CRS 无效就按
    EPSG:4326 解释（QGIS 桌面版对未知 CRS 图层也是按工程 CRS 处理的），
    并留一条告警让人知道这是猜的。
    """
    import shutil
    import tempfile

    import numpy as np
    from osgeo import gdal

    tmp = Path(tempfile.mkdtemp(prefix="gisds_nocrs_"))
    try:
        # 目录结构：<数据根>/shared/<数据集>/<文件>（一个目录 = 一个数据集）
        ds_dir = tmp / "shared" / "nocrs"
        ds_dir.mkdir(parents=True, exist_ok=True)
        tif = ds_dir / "nocrs.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(tif), 64, 48, 1, gdal.GDT_Byte)
        arr = np.fromfunction(
            lambda y, x: (x + y) % 6 + 1, (48, 64), dtype=int
        ).astype("uint8")
        ds.GetRasterBand(1).WriteArray(arr)
        # 只给地理变换，**不给**投影定义
        ds.SetGeoTransform([-112.56, 0.0005, 0, 39.75, 0, -0.0005])
        ds.FlushCache()
        ds = None

        reg = Registry(tmp)
        datasets = reg.scan()
        check("无 CRS 的栅格被识别为图层", len(datasets) == 1, f"实际 {len(datasets)}")
        # 下面三条断言**无条件执行**：条件不满足就让断言**失败**，而不是整段跳过
        # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
        dataset = datasets[0] if datasets else None
        layer = next(iter(dataset.layers.values()), None) if dataset else None
        check("无 CRS 的图层被按 EPSG:4326 解释",
              layer is not None and layer.crs == "EPSG:4326",
              layer.crs if layer else "没有图层")
        check("无 CRS 时给出告警（让人知道这是猜的）",
              dataset is not None and any("坐标系" in w for w in dataset.warnings),
              "; ".join(dataset.warnings) if dataset else "没有数据集")
        if layer is not None:
            # 关键：出图必须有内容 —— 之前这里是全白（0 种颜色）
            png, _ = render_core.render_raster_png(
                layer, layer.extent_wgs84, width=400, dst_crs="EPSG:3857"
            )
            n = color_clusters(pixels(png))
            check("无 CRS 的栅格出图有实际内容（像素级）", n >= 2, f"{n} 种颜色")
        else:
            check("无 CRS 的栅格出图有实际内容（像素级）", False, "没有图层，出不了图")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        try:
            code = main()
        except BaseException:  # noqa: BLE001
            # main() 抛异常时**不能**让它一路退到解释器退出 ——
            # 那样会先跑 QGIS 的析构，Windows 上大概率变成 0xC0000005（访问违例），
            # 真实原因（那条 KeyError 之类）反而被埋了。改成：打印回溯 + 退出码 1。
            import traceback

            traceback.print_exc()
            code = 1

    except Exception:
        traceback.print_exc()
        code = 2
    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    # PyQGIS 在 Windows 上退出时可能触发 0xC0000005，这里直接结束进程
    import os

    os._exit(code)
