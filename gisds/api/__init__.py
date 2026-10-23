"""REST API 层。

这一层只做三件事：解析请求 -> 调用 core -> 组织响应。
所有涉及 QGIS 的调用都通过 ``run_qgis`` 投递到专属工作线程，保证串行安全。
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
