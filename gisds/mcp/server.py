"""MCP Server —— 让 OpenClaw（龙虾）等 Agent 把这套 GIS 当工具用。

与 REST API **共用同一套 core**（``gisds.core``），因此两边的查询结果、
数值口径、渲染效果完全一致；差别只在外壳：REST 是 HTTP，MCP 是工具调用。

工具定义在 ``gisds.core.gis_tools`` —— **网页里的 AI 助手也用同一份**
（那边按登录用户身份过滤空间，这边是服务级）。别在这里另写一份工具。

启动方式::

    python -m gisds mcp                       # stdio（OpenClaw 挂载最常用）
    python -m gisds mcp --transport streamable-http --port 8849
"""

from __future__ import annotations

import functools
import io
import json
import logging
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.errors import GisdsError, InvalidRequestError

LOGGER = logging.getLogger("gisds.mcp")

#: 给模型看的工具用法说明。**与网页 AI 助手共用同一段**
#: （``gisds.core.gis_tools.TOOL_INSTRUCTIONS``）—— 两边给模型的说明
#: 必须一致，不然同一个工具在两条路上表现不一样。
INSTRUCTIONS = """
Browser QGIS 是一个基于 PyQGIS 内核的 GIS 数据服务器。

- 先用 gis_list_datasets 看有哪些数据集，再用 gis_get_dataset 看具体有哪些图层。
- 栅格图层：gis_query_point 查单点值，gis_query_bbox 查范围内统计，
  gis_layer_area 查地类面积，gis_render_map 出图。
- 矢量图层：gis_list_features 取要素（返回 GeoJSON），gis_vector_stats 看统计。
- 属性表 / 统计：gis_attribute_table 读字段与要素，gis_field_stats 逐字段算
  计数、唯一值、极值、均值、中位数。
- 选择：gis_select_features 支持按表达式、按 GeoJSON 几何、按另一个图层
  （target_ref）三种选法，也能 mode="all" 全选。
- 编辑：先 gis_edit_start 进编辑模式，再 gis_edit_feature / gis_add_feature /
  gis_delete_features / gis_field_calculator 改数据，最后 gis_edit_commit
  写回文件（或 gis_edit_rollback 放弃）。
- 所有经纬度参数一律使用 WGS84 (EPSG:4326)，服务内部会自动换算到图层自身坐标系。
- 分类图层（kind=categorical）的取值会附带中文类别名，统计接口给出各类面积（公顷）。
"""


@contextmanager
def _stdout_to_stderr():
    """MCP stdio 传输时 stdout 是协议通道，任何往 stdout 打印都会破坏协议。"""
    original = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _safe_tool(fn):
    """把工具内部异常转成结构化错误返回。

    MCP 客户端（尤其是 Agent）拿到一段明确的错误说明，比收到一个未捕获异常
    有用得多——Agent 可以据此自行纠正参数再重试。
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except GisdsError as exc:
            return _json({"error": exc.code, "message": exc.message, "detail": exc.detail})
        except Exception as exc:  # pragma: no cover - 兜底
            LOGGER.exception("MCP 工具执行失败")
            return _json({"error": "tool_error", "message": f"{type(exc).__name__}: {exc}"})

    return wrapper


def _image_result(data: bytes, caption: str):
    """把 PNG 包装成 MCP 的图片内容块（不可用时退化为临时文件路径）。"""
    try:
        from mcp.server.mcpserver import Image

        return Image(data=data, format="png")
    except Exception:
        import tempfile
        from pathlib import Path

        path = Path(tempfile.gettempdir()) / f"gisds_{abs(hash(caption)) % 10**8}.png"
        path.write_bytes(data)
        return f"已渲染并保存到：{path}\n（{len(data)} 字节）"


def _mcp_tool(spec, ctx):
    """把共享工具定义包成一个 MCP 工具。

    两件事在这里做：

    * 异常 → 结构化错误（Agent 能据此改参数重试）
    * 渲染结果 → MCP 图片内容块

    参数签名由 ``gisds.core.gis_tools.bind`` 保留（SDK 靠 ``inspect.signature``
    推 schema），所以这里只包一层薄壳。
    """
    from ..core.gis_tools import RenderResult, bind

    fn = _safe_tool(bind(spec, ctx))

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = await fn(*args, **kwargs)
        if isinstance(result, RenderResult):
            return _image_result(result.data, result.caption)
        return result

    return wrapper


def build_server(data_root: str | None = None):
    """构造 MCP Server 实例。"""
    from mcp.server.mcpserver import MCPServer

    from .. import qgis_env
    from ..config import load_config
    from ..core.executor import run_qgis_sync
    from ..core.gis_tools import TOOLS, ToolContext
    from ..core.registry import Registry

    config = load_config()
    if data_root:
        config.data_root = str(data_root)

    with _stdout_to_stderr():
        # QGIS 对象必须在统一的工作线程上创建（与 REST 服务同一套约束）
        run_qgis_sync(qgis_env.init_qgis)
        run_qgis_sync(qgis_env.ensure_processing)

    registry = Registry(
        config.data_root_path, config.shared_root_path, config.users_root_path
    )
    run_qgis_sync(registry.scan)

    server = MCPServer(
        name="gis-data-server",
        title="GIS 数据服务器（PyQGIS 内核）",
        instructions=INSTRUCTIONS,
        version="1.0.0",
    )

    # 服务级身份：能看到所有空间（与重构前的行为一致）。
    # 网页 AI 助手走的是另一套身份（按登录用户过滤空间）—— 见 core/ai.py。
    ctx = ToolContext(registry=registry, config=config, principal=None)

    for spec in TOOLS:
        server.add_tool(
            _mcp_tool(spec, ctx),
            name=spec.name,
            description=spec.description,
        )

    return server


def serve_mcp(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8849,
    data_root: str | None = None,
) -> int:
    """启动 MCP Server。

    注意：stdio 传输时 **stdout 就是 MCP 协议通道**，不能重定向。
    因此 QGIS 初始化阶段（可能打印告警）单独包了一层 stdout->stderr 保护，
    之后的协议输出保持原样。日志统一走 stderr。

    **传输方式决定了暴露面**（这是本项目写明的取舍，见 README「MCP Server」一节）：
    MCP 是**服务级工具**，不走 HTTP 鉴权、能看到所有空间。
    * ``stdio``（默认，``mcp.bat`` 用的就是这个）：只有本机能起这个进程，
      暴露面 = 本机用户权限，可以接受。
    * ``streamable-http`` / ``sse``：**整个网络都能调**。既然没有鉴权，
      就等于把「能看到所有空间、能改所有数据」的能力开给了局域网 ——
      绑到 0.0.0.0 之前请先想清楚。
    """
    if transport != "stdio":
        LOGGER.warning(
            "MCP 正以 %s 传输启动：这套工具**不走鉴权、能看到所有空间**。"
            "只在本机用（stdio）没问题；对外暴露前请确认网络边界。",
            transport,
        )
        print(
            f"[gisds] 警告：MCP 以 {transport} 传输启动 —— 无鉴权、可见所有数据空间。",
            file=sys.stderr,
        )
    server = build_server(data_root)

    if transport == "stdio":
        server.run(transport="stdio")
        return 0

    try:
        server.run(transport=transport, host=host, port=port)
    except TypeError:
        # 老版本 API：host/port 通过 settings 配置
        try:
            server.settings.host = host
            server.settings.port = port
        except Exception:
            pass
        server.run(transport=transport)
    return 0
