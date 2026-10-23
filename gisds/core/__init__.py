"""core —— GISDS 的内核。

这一层不依赖任何 Web 框架，REST 与 MCP 两套接口都调用它，保证行为一致。
"""

from __future__ import annotations

__all__ = ["errors", "executor", "registry", "raster", "vector", "render"]
