"""空间书签（QGIS 的「空间书签」面板 / 空间书签工具栏）。

书签就是一组范围 + 一个名字。这里按数据空间分开存：
共享空间的书签所有人可见，个人空间的书签只有本人可见。
落盘成一个 JSON 文件（``<数据根目录>/bookmarks.json``），服务重启不丢。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _bookmarks_path(data_root: Path) -> Path:
    return Path(data_root) / "bookmarks.json"


def _read(data_root: Path) -> dict[str, list[dict[str, Any]]]:
    path = _bookmarks_path(data_root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): list(v) for k, v in raw.items() if isinstance(v, list)}


def _write(data_root: Path, data: dict[str, list[dict[str, Any]]]) -> None:
    path = _bookmarks_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_bookmarks(data_root: Path, scope: str) -> list[dict[str, Any]]:
    """取某个空间的书签。"""
    with _LOCK:
        data = _read(data_root)
    return list(data.get(scope, []))


def add_bookmark(
    data_root: Path,
    scope: str,
    name: str,
    extent: list[float],
    crs: str = "EPSG:4326",
    description: str = "",
) -> dict[str, Any]:
    """新建书签；同名则覆盖。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("书签名不能为空")
    if not extent or len(extent) != 4:
        raise ValueError("范围必须是 minx,miny,maxx,maxy 四个数")
    try:
        box = [float(v) for v in extent]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"范围含非法数值：{exc}") from exc

    entry = {
        "name": name,
        "extent": box,
        "crs": crs or "EPSG:4326",
        "description": description or "",
        "scope": scope,
        "created_at": time.time(),
    }
    with _LOCK:
        data = _read(data_root)
        items = [b for b in data.get(scope, []) if b.get("name") != name]
        items.append(entry)
        data[scope] = items
        _write(data_root, data)
    return entry


def remove_bookmark(data_root: Path, scope: str, name: str) -> bool:
    """删除书签，返回是否真的删掉了。"""
    with _LOCK:
        data = _read(data_root)
        items = data.get(scope, [])
        keep = [b for b in items if b.get("name") != name]
        if len(keep) == len(items):
            return False
        data[scope] = keep
        _write(data_root, data)
    return True
