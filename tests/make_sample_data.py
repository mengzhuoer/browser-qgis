"""生成一份中性的合成示例数据，用于验证服务链路。

产出（默认写到 tests/sample_data/shared/example/，即**共享空间**下）::

    example/
      metadata.json
      surface.tif      # 连续型：起伏曲面
      classes.tif      # 离散型：6 类分区
      boundary.geojson # 矢量：一个范围面

这是**合成数据**，只为跑通链路用，不代表任何真实调查结果。

用法::

    run-qgis-python.bat tests/make_sample_data.py [目标目录]
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

from osgeo import gdal, ogr, osr

gdal.UseExceptions()

# 示例区域（经纬度）
MINX, MAXY = 112.40, 39.75
RES = 0.0005
WIDTH = 320
HEIGHT = 260
MAXX = MINX + WIDTH * RES
MINY = MAXY - HEIGHT * RES

CLASS_LABELS = {
    1: "类别一",
    2: "类别二",
    3: "类别三",
    4: "类别四",
    5: "类别五",
    6: "类别六",
}


def _grids():
    """分类分区 + 连续曲面 + 分类掩膜。"""
    cx, cy = WIDTH / 2, HEIGHT / 2
    classes, surface = [], []
    for r in range(HEIGHT):
        row_cls, row_surf = [], []
        for c in range(WIDTH):
            dx = (c - cx) / cx
            dy = (r - cy) / cy
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 0.18:
                value = 6
            elif dist < 0.34:
                value = 5
            elif dist < 0.55:
                value = 3
            elif dist < 0.78:
                value = 2
            elif r > HEIGHT * 0.82:
                value = 4
            else:
                value = 1
            row_cls.append(value)
            row_surf.append(
                round(900 + 120 * (1 - r / HEIGHT) + 60 * math.sin(c / 40.0) + 40 * math.cos(r / 25.0), 2)
            )
        classes.append(row_cls)
        surface.append(row_surf)
    return classes, surface


def _write_raster(path: Path, rows, dtype, nodata=None):
    driver = gdal.GetDriverByName("GTiff")
    height, width = len(rows), len(rows[0])
    dataset = driver.Create(str(path), width, height, 1, dtype)
    band = dataset.GetRasterBand(1)

    import numpy as np

    if dtype == gdal.GDT_Byte:
        array = np.array(rows, dtype=np.uint8)
    elif dtype == gdal.GDT_Int16:
        array = np.array(rows, dtype=np.int16)
    else:
        array = np.array(rows, dtype=np.float32)
    band.WriteArray(array)
    if nodata is not None:
        band.SetNoDataValue(nodata)

    dataset.SetGeoTransform((MINX, RES, 0.0, MAXY, 0.0, -RES))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    dataset.FlushCache()
    dataset = None


def _write_geojson(path: Path):
    driver = ogr.GetDriverByName("GeoJSON")
    vector = driver.CreateDataSource(str(path))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    layer = vector.CreateLayer("region", srs, ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("name", ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("area_km2", ogr.OFTReal))

    cx, cy = (MINX + MAXX) / 2, (MAXY + MINY) / 2
    radius_x = (MAXX - MINX) * 0.42
    radius_y = (MAXY - MINY) * 0.42
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for i in range(37):
        angle = 2 * math.pi * i / 36
        ring.AddPoint_2D(cx + radius_x * math.cos(angle), cy + radius_y * math.sin(angle))
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)

    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetField("name", "示例范围")
    feature.SetField("area_km2", round(polygon.GetArea() * 111.0 * 111.0 / 100.0, 3))
    feature.SetGeometry(polygon)
    layer.CreateFeature(feature)
    feature = None
    layer = None
    vector = None


def main() -> int:
    default_target = Path(__file__).resolve().parent / "sample_data" / "shared" / "example"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default_target
    target.mkdir(parents=True, exist_ok=True)

    classes, surface = _grids()
    _write_raster(target / "classes.tif", classes, gdal.GDT_Byte, nodata=255)
    _write_raster(target / "surface.tif", surface, gdal.GDT_Float32)
    _write_geojson(target / "boundary.geojson")

    metadata = {
        "name": "示例数据（合成）",
        "description": "用于验证服务链路的合成样例，不代表真实调查结果。",
        "crs": "EPSG:4326",
        "layers": {
            "classes": {
                "file": "classes.tif",
                "kind": "categorical",
                "title": "分区（离散）",
                "classes": {str(k): v for k, v in CLASS_LABELS.items()},
            },
            "surface": {
                "file": "surface.tif",
                "kind": "continuous",
                "title": "曲面（连续）",
            },
        },
        "vectors": {"boundary": {"file": "boundary.geojson", "title": "示例范围"}},
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"示例数据已生成：{target}")
    for item in sorted(target.iterdir()):
        print(f"  - {item.name}  ({item.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
