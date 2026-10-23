"""PNG 像素级判定（给自检脚本用）。

「返回 200」和「字节数 > 200」都**不能**说明瓦片里有东西 ——
一张全白 PNG 同样满足这两条，这类 bug 就是从这种缝里溜过去的
（渲染范围错了、坐标换算错了，最后都得到一张合法的空图）。

所以判断「有没有内容」必须解像素。

判据用**颜色簇数**，不用「非白像素占比」：底图的海洋是淡蓝灰 #DBE5EA，
陆地对它才算「非白」，占比会随海陆分布乱跳；颜色簇数则稳定 ——
空白瓦片只有一种颜色（或全透明），有内容的至少两种。

依赖 QGIS 自带的 PyQt5 + numpy，不用装 Pillow。
"""

from __future__ import annotations

import numpy as np
from PyQt5.QtGui import QImage

# 颜色量化步长：24/255 的灰阶足够区分「海 / 陆 / 边界」这类大面积色块，
# 又不至于把抗锯齿边缘算成一堆新颜色。
_STEP = 24


def pixels(png: bytes):
    """解 PNG 成 (h, w, 4) 的 numpy 数组。"""
    img = QImage.fromData(png, "PNG").convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    buf = img.constBits()
    buf.setsize(bpl * h)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl // 4, 4)
    return np.array(arr[:, :w, :], copy=True)


def color_clusters(px) -> int:
    """粗略数一下有多少种显著不同的颜色。"""
    rgb = px[:, :, :3].reshape(-1, 3).astype(np.int16)
    alpha = px[:, :, 3].reshape(-1)
    rgb = rgb[alpha > 8]
    if not len(rgb):
        return 0
    q = (rgb // _STEP).astype(np.int32)
    keys = q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]
    _, counts = np.unique(keys, return_counts=True)
    keep = counts[counts > len(keys) * 0.01]
    return int(len(keep))


def has_content(png: bytes) -> bool:
    """这张 PNG 里有没有实际内容（至少两种显著颜色）。"""
    return color_clusters(pixels(png)) >= 2
