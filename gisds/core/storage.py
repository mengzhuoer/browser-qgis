"""上传工作区：浏览器上传的文件落地、登记、清理。

工作区是某个数据空间的根目录，结构与共享空间一致：

    <空间根目录>/
      <dataset_id>/        <- 一次上传 = 一个数据集
        landuse.tif
        boundary.geojson

数据空间有两个（见 :mod:`gisds.core.tenancy`）：共享空间和用户私有空间。
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def migrate_legacy_layout(
    data_root: str | Path, workspace_root: str | Path | None = None
) -> list[str]:
    """把旧布局（共享库直接挂在 data 下 + 独立 workspace）迁移到新布局。

    旧布局::

        data/<数据集>/          workspace/<数据集>/

    新布局::

        data/shared/<数据集>/   data/users/<用户ID>/<数据集>/

    旧的两个目录在语义上都是「大家都能看到」的，所以一并并进共享空间。
    返回被移动的条目说明。
    """
    data_root = Path(data_root).expanduser().resolve()
    shared = data_root / "shared"
    notes: list[str] = []

    # 1) data/<数据集> -> data/shared/<数据集>
    if data_root.is_dir():
        for child in sorted(data_root.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            if child.name in ("shared", "users", "jobs"):
                continue
            target = shared / child.name
            if target.exists():
                notes.append(f"跳过 {child.name}（共享空间里已有同名数据集）")
                continue
            shared.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(target))
            notes.append(f"迁移 {child.name} -> shared/{child.name}")

    # 2) workspace/<数据集> -> data/shared/<数据集>
    if workspace_root:
        workspace_root = Path(workspace_root).expanduser().resolve()
        if workspace_root.is_dir():
            for child in sorted(workspace_root.iterdir()):
                if not child.is_dir() or child.name.startswith((".", "_")):
                    continue
                target = shared / child.name
                if target.exists():
                    notes.append(f"跳过 {child.name}（共享空间里已有同名数据集）")
                    continue
                shared.mkdir(parents=True, exist_ok=True)
                shutil.move(str(child), str(target))
                notes.append(f"迁移 workspace/{child.name} -> shared/{child.name}")

    return notes

# 支持上传的扩展名
RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".vrt", ".ntf"}
VECTOR_EXTENSIONS = {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml"}

# 压缩包（Shapefile 套件）
ARCHIVE_EXTENSIONS = {".zip"}

_SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


@dataclass
class UploadResult:
    dataset_id: str
    directory: str
    files: list[str]
    kind: str  # raster | vector | mixed | unknown
    warnings: list[str]


def safe_name(name: str) -> str:
    """把上传的文件名收拾干净（去路径、保留可读部分）。"""
    base = Path(name.replace("\\", "/")).name.strip()
    base = _SAFE_NAME.sub("_", base)
    base = base.lstrip(".") or "upload"
    return base[:120]


def detect_kind(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in RASTER_EXTENSIONS:
        return "raster"
    if suffix in VECTOR_EXTENSIONS:
        return "vector"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archive"
    return "unknown"


class Workspace:
    """上传文件的工作区管理器。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 保存

    def save(self, filename: str, data: bytes, dataset_id: str | None = None) -> UploadResult:
        """保存一个上传文件，返回数据集信息。"""
        safe = safe_name(filename)
        kind = detect_kind(safe)
        if kind == "unknown":
            raise ValueError(
                f"不支持的文件类型：{Path(filename).suffix or '(无扩展名)'}；"
                f"支持栅格 {sorted(RASTER_EXTENSIONS)}、矢量 {sorted(VECTOR_EXTENSIONS)}"
            )
        if not data:
            raise ValueError("文件内容为空")

        dataset_id = dataset_id or f"{Path(safe).stem}_{uuid.uuid4().hex[:6]}"
        directory = self.root / dataset_id
        directory.mkdir(parents=True, exist_ok=True)

        target = directory / safe
        counter = 1
        while target.exists():
            target = directory / f"{Path(safe).stem}_{counter}{Path(safe).suffix}"
            counter += 1

        target.write_bytes(data)

        warnings: list[str] = []
        if kind == "vector" and target.suffix.lower() == ".json":
            warnings.append("JSON 需含 GeoJSON 结构才会被识别为矢量图层")
        if kind == "archive":
            warnings.append("压缩包仅保存归档，未自动解压；请解压后逐个上传")

        return UploadResult(
            dataset_id=dataset_id,
            directory=str(directory),
            files=[target.name],
            kind=kind,
            warnings=warnings,
        )

    # ------------------------------------------------------------ 查询

    def list_datasets(self) -> list[dict[str, Any]]:
        """列出工作区里的数据集（按修改时间倒序）。"""
        items: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return items
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            if (directory / self.DELETE_MARKER).is_file():
                continue
            files = [
                p
                for p in directory.iterdir()
                if p.is_file() and not p.name.startswith((".", "_"))
            ]
            if not files:
                continue
            total = sum(p.stat().st_size for p in files)
            items.append(
                {
                    "dataset_id": directory.name,
                    "directory": str(directory),
                    "files": [p.name for p in files],
                    "file_count": len(files),
                    "total_bytes": total,
                    "modified_at": max(p.stat().st_mtime for p in files),
                }
            )
        items.sort(key=lambda x: x["modified_at"], reverse=True)
        return items

    def path_of(self, dataset_id: str) -> Path:
        directory = self.root / dataset_id
        if not directory.is_dir():
            raise FileNotFoundError(f"工作区数据集不存在：{dataset_id}")
        return directory

    def file_path(self, dataset_id: str, filename: str) -> Path:
        directory = self.path_of(dataset_id)
        target = directory / safe_name(filename)
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在：{filename}")
        return target

    # ------------------------------------------------------------ 删除

    DELETE_MARKER = ".deleted"

    def _trash_dir(self) -> Path:
        return self.root / ".trash"

    def mark_deleted(self, dataset_id: str) -> bool:
        """标记数据集为待删除。

        QGIS/GDAL 可能还持有文件句柄，Windows 上直接删会报 ``WinError 32``。
        所以先写一个标记文件 —— 扫描时会自动跳过被标记的目录，
        文件本身留到下次 ``purge()`` 再清理。
        """
        directory = self.root / dataset_id
        if not directory.is_dir():
            return False
        try:
            (directory / self.DELETE_MARKER).write_text("deleted", encoding="utf-8")
            return True
        except OSError:
            return False

    def is_marked_deleted(self, dataset_id: str) -> bool:
        return (self.root / dataset_id / self.DELETE_MARKER).is_file()

    def purge(self) -> int:
        """清理所有被标记删除的数据集（尽力而为）。"""
        removed = 0
        if not self.root.is_dir():
            return 0
        for directory in list(self.root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            if not (directory / self.DELETE_MARKER).is_file():
                continue
            try:
                shutil.rmtree(directory)
                removed += 1
            except OSError:
                # rmtree 可能已把标记文件删掉了，这里补回来
                self.mark_deleted(directory.name)
        trash = self._trash_dir()
        if trash.is_dir():
            for item in list(trash.iterdir()):
                try:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
                except OSError:
                    pass
        return removed

    def delete(self, dataset_id: str) -> bool:
        """删除一个上传数据集。

        文件被 QGIS/GDAL 占用时删不掉，此时留下 ``.deleted`` 标记，
        扫描会自动跳过，等文件句柄释放后由 ``purge()`` 真正清除。
        """
        directory = self.root / dataset_id
        if not directory.is_dir():
            return False
        try:
            shutil.rmtree(directory)
        except OSError:
            self.mark_deleted(dataset_id)
        return True

    def clear(self) -> int:
        count = 0
        for directory in list(self.root.iterdir()):
            if directory.is_dir() and not directory.name.startswith("."):
                try:
                    shutil.rmtree(directory)
                except OSError:
                    self.mark_deleted(directory.name)
                count += 1
        self.purge()
        return count
