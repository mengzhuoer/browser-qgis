"""QGIS 调用的线程调度。

PyQGIS 不是线程安全的：图层渲染会触碰 Qt 图形栈，多个 HTTP 请求并发进来时
直接渲染会偶发崩溃甚至段错误。

这里的做法是：所有 QGIS 调用都投递到**唯一一个**工作线程串行执行。
HTTP 侧（FastAPI）用 ``await run_qgis(...)`` 提交任务，既保证串行，
又不会阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gisds-qgis")


async def run_qgis(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """把一次 QGIS 调用投递到专属工作线程并等待结果。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, functools.partial(fn, *args, **kwargs))


def run_qgis_sync(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """同步版本（给 CLI / 测试用）。"""
    return _EXECUTOR.submit(functools.partial(fn, *args, **kwargs)).result()
