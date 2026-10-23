"""栅格块读取的底层工具。

``QgsRasterBlock`` 是 QGIS 提供的内存块，但拿 numpy 数组要靠一层转换。
这里统一封装，并提供一个逐像元兜底方案（极端情况下 ``block.data()`` 不可用）。
"""

from __future__ import annotations

import numpy as np

# QgsRasterDataType -> numpy dtype
_DTYPE_MAP: dict[int, np.dtype] = {
    1: np.dtype("uint8"),  # Byte
    2: np.dtype("uint16"),  # UInt16
    3: np.dtype("int16"),  # Int16
    4: np.dtype("uint32"),  # UInt32
    5: np.dtype("int32"),  # Int32
    6: np.dtype("float32"),  # Float32
    7: np.dtype("float64"),  # Float64
    14: np.dtype("int8"),  # Int8
    15: np.dtype("uint8"),  # UInt8
}


def numpy_dtype_for(data_type: int) -> np.dtype:
    return _DTYPE_MAP.get(int(data_type), np.dtype("float64"))


def block_to_numpy(block) -> np.ndarray | None:
    """把 QgsRasterBlock 转成二维 numpy 数组（形状为 [row, col]）。"""
    if block is None:
        return None
    try:
        if block.isEmpty():
            return None
    except Exception:
        return None

    width = int(block.width())
    height = int(block.height())
    if width <= 0 or height <= 0:
        return None

    dtype = numpy_dtype_for(int(block.dataType()))
    try:
        raw = bytes(block.data())
        need = width * height * dtype.itemsize
        if len(raw) >= need:
            return np.frombuffer(raw[:need], dtype=dtype).reshape(height, width)
    except Exception:
        pass

    # 兜底：逐像元取值（慢，但保证正确）
    out = np.zeros((height, width), dtype=np.float64)
    for r in range(height):
        for c in range(width):
            out[r, c] = block.value(r, c)
    return out


def mask_nodata(arr: np.ndarray, nodata: float | None) -> np.ndarray:
    """返回布尔掩膜：True 表示有效像元。"""
    if arr is None:
        return np.zeros((0, 0), dtype=bool)
    valid = np.ones(arr.shape, dtype=bool)
    if arr.dtype.kind == "f":
        valid &= ~np.isnan(arr)
    if nodata is not None:
        valid &= arr != nodata
    return valid


def unique_values(arr: np.ndarray, nodata: float | None, limit: int = 256) -> list:
    """取有限个不同的取值（离散型图层用来自动生成类别表）。"""
    if arr is None or arr.size == 0:
        return []
    valid = mask_nodata(arr, nodata)
    if not valid.any():
        return []
    vals = np.unique(arr[valid])
    if vals.size > limit:
        # 太多不同取值 -> 不是离散型，返回空
        return []
    return [float(v) for v in vals]
