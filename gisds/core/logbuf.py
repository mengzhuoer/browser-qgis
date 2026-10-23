"""服务端日志缓冲（QGIS 的「日志消息」面板）。

服务本身不写日志文件，重启就没了；面板要能回看刚才发生了什么，
所以这里挂一个环形缓冲区，把最近的日志行留在内存里。

日志级别沿用 Python 的 logging 级别名（DEBUG / INFO / WARNING / ERROR）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

_CAPACITY = 800


class _RingHandler(logging.Handler):
    """把日志记录攒进定长队列。"""

    def __init__(self, capacity: int = _CAPACITY) -> None:
        super().__init__()
        self.records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            entry = {
                "time": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                entry["message"] += "\n" + self.format(record)
            with self._lock:
                self.records.append(entry)
        except Exception:
            pass  # 日志处理器绝不能把主流程带崩

    def tail(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.records)
        if level:
            wanted = level.upper()
            items = [i for i in items if i.get("level") == wanted]
        return items[-max(1, int(limit)):]

    def clear(self) -> int:
        with self._lock:
            count = len(self.records)
            self.records.clear()
        return count


HANDLER = _RingHandler()


def install() -> None:
    """把缓冲挂到根 logger 上（幂等）。

    uvicorn 启动时用 ``dictConfig`` 重置根 logger 的 handler，会把之前挂上的
    缓冲摘掉，所以这里做成「随时可重装」：应用启动时装一次，每次读日志时
    再确认一次还在不在。
    """
    root = logging.getLogger()
    if HANDLER not in root.handlers:
        HANDLER.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(HANDLER)


def tail(limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
    install()
    return HANDLER.tail(limit, level)


def clear() -> int:
    install()
    return HANDLER.clear()


def format_line(entry: dict[str, Any]) -> str:
    """把一条记录格式成一行文本（前端日志面板直接显示）。"""
    stamp = time.strftime("%H:%M:%S", time.localtime(entry.get("time", time.time())))
    return f"{stamp}  {entry.get('level', 'INFO'):<7} {entry.get('logger', '')}  {entry.get('message', '')}"


# 注意：**不要在 import 时就装 handler**。
# CLI 里是「先 import 应用模块，再调 logging.basicConfig()」，
# 根 logger 提前挂了 handler 的话 basicConfig 会变成空操作，
# 根级别会停在 WARNING，服务自己的 INFO 日志就全被吞了。
# 所以这里只提供 install()，由应用启动时和读日志时各自调用。
