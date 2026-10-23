"""内置离线底图：下载 Natural Earth 世界矢量数据并装进共享空间。

底图不走在线瓦片服务，而是用本服务自己的渲染管线把矢量数据画成
XYZ 瓦片（``/api/tiles/basemap/{z}/{x}/{y}.png``），因此：

- 装好之后完全离线可用
- 任意缩放级别都是矢量渲染，放大不糊
- 不引入任何新的依赖

数据源是 Natural Earth（公共领域，naturalearthdata.com）。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NE_BASE = "https://naciscdn.org/naturalearth/{scale}/{category}/{name}.zip"

# 底图要素：(下载目录, 分类, 原始名, 图层名, 中文名)
BASEMAP_LAYERS: list[tuple[str, str, str, str, str]] = [
    ("50m", "physical", "ne_50m_land", "land", "陆地"),
    ("50m", "cultural", "ne_50m_admin_0_boundary_lines_land", "boundaries", "国界"),
    ("110m", "physical", "ne_110m_lakes", "lakes", "湖泊"),
    ("110m", "physical", "ne_110m_rivers_lake_centerlines", "rivers", "河流"),
]

GPKG_NAME = "basemap.gpkg"
METADATA_NAME = "metadata.json"
DATASET_ID = "basemap"


def ogr2ogr_path() -> str | None:
    """找 ogr2ogr 可执行文件。"""
    for candidate in ("ogr2ogr.exe", "ogr2ogr"):
        found = shutil.which(candidate)
        if found:
            return found
    osgeo = os.environ.get("OSGEO4W_ROOT")
    if osgeo:
        guess = Path(osgeo) / "bin" / "ogr2ogr.exe"
        if guess.is_file():
            return str(guess)
    return None


def _download(scale: str, category: str, name: str, timeout: int = 90) -> bytes:
    url = NE_BASE.format(scale=scale, category=category, name=name)
    request = urllib.request.Request(url, headers={"User-Agent": "BrowserQGIS/1.0 (offline basemap installer)"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _extract(blob: bytes, workdir: Path) -> Path:
    """解压 zip，返回里面的 .shp 路径。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        zf.extractall(workdir)
    shapes = sorted(workdir.rglob("*.shp"))
    if not shapes:
        raise RuntimeError("压缩包里没有 .shp 文件")
    return shapes[0]


def install_basemap(
    target_dir: str | Path,
    force: bool = False,
    timeout: int = 90,
    log=None,
) -> dict:
    """下载 Natural Earth 并装成一个数据集目录。

    目录结构::

        <target_dir>/
            basemap.gpkg     所有底图要素打包成一个 GeoPackage
            metadata.json    数据集说明（列出各个矢量图层）

    返回安装结果摘要。
    """
    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    gpkg = target / GPKG_NAME
    meta_path = target / METADATA_NAME

    def say(msg: str) -> None:
        if log:
            log(msg)

    if gpkg.is_file() and meta_path.is_file() and not force:
        say(f"底图已存在，跳过下载：{target}")
        return {"installed": False, "path": str(target), "layers": _read_layer_names(meta_path)}

    ogr = ogr2ogr_path()
    if not ogr:
        raise RuntimeError(
            "找不到 ogr2ogr（GDAL 的命令行工具）。请确认 QGIS/OSGeo4W 安装完整。"
        )

    if force:
        for stale in (gpkg, meta_path):
            if stale.exists():
                stale.unlink()

    specs: dict[str, dict] = {}
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="gisds_ne_") as tmp:
        work = Path(tmp)
        for scale, category, name, layer_id, title in BASEMAP_LAYERS:
            say(f"下载 {name} …")
            blob = _download(scale, category, name, timeout=timeout)
            total_bytes += len(blob)
            shp = _extract(blob, work / name)
            say(f"  转换到 GeoPackage：{layer_id}")
            cmd = [
                ogr, "-f", "GPKG", str(gpkg),
                "-nln", layer_id,
                "-overwrite",
                "-lco", "SPATIAL_INDEX=YES",
                str(shp),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"ogr2ogr 转换 {name} 失败：{proc.stderr.strip()[:400]}")
            specs[layer_id] = {
                "file": GPKG_NAME,
                "sub_layer": layer_id,
                "title": title,
                "source": f"Natural Earth {scale}",
            }

    metadata = {
        "name": "世界底图（内置离线）",
        "description": "Natural Earth 世界矢量数据，由本服务渲染成 XYZ 瓦片，完全离线可用。",
        "crs": "EPSG:4326",
        "vectors": specs,
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    say(f"完成：{target}")
    return {
        "installed": True,
        "path": str(target),
        "download_bytes": total_bytes,
        "size_bytes": gpkg.stat().st_size if gpkg.is_file() else 0,
        "layers": list(specs.keys()),
    }


def _read_layer_names(meta_path: Path) -> list[str]:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return list((data.get("vectors") or {}).keys())
    except Exception:
        return []


def basemap_available(shared_root: str | Path) -> bool:
    """共享空间里是否已经装好底图。"""
    root = Path(shared_root).expanduser().resolve()
    return (root / DATASET_ID / GPKG_NAME).is_file()
