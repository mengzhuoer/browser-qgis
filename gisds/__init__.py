"""Browser QGIS —— 基于 PyQGIS 内核的 GIS 数据服务器。

同一份 core 内核同时对外提供两套接口：
- REST API（FastAPI）：给其他网页 / 业务系统消费
- MCP Server：给 OpenClaw（龙虾）等 Agent 当工具调用
"""

__version__ = "1.2.0"
__all__ = ["__version__"]
