"""GIS 工具注册表 —— **MCP Server 与网页 AI 助手共用同一份定义**。

为什么抽出来：网页里的 AI 助手要能调 QGIS 的能力（查数据、跑算法、编辑要素），
而这些能力本来就在 MCP Server 里（27 个工具）。如果各写一份，工具名、参数、
说明会**慢慢漂移**（MCP 改了一个参数，网页 AI 还按老的调）——
所以这里把工具定义收成一份，两边都从它取。

* ``build_server()``（MCP）→ 按服务级身份登记（能看到所有空间，与原行为一致）
* 网页 AI 助手 → 按**登录用户**身份登记（只能看到自己有权看的空间）

**同一份定义、两套身份**，是这个模块存在的全部意义。

三条「不许漂移」的硬规矩：

1. 工具说明 = 处理函数的 docstring 原文（不再另抄一份）
2. 参数 JSON Schema **从函数签名自动派生**（参数名、类型、必填项不会跟实现脱节），
   只额外叠加一句中文参数说明
3. 工具实现在这里只有一份，MCP 与网页 AI 调的是同一个函数

这个模块**不依赖 mcp 包**：渲染结果是自己的 :class:`RenderResult`，
由各自的「外壳」（MCP Server / 网页 AI）翻译成自己那套协议。
"""

from __future__ import annotations

import inspect
import json
import tempfile
import types
import typing
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .errors import InvalidRequestError, NotFoundError

#: 工具用法说明。**MCP Server 与网页 AI 助手共用这一段**（见
#: ``gisds.mcp.server.INSTRUCTIONS``）—— 两边给模型的说明必须一致，
#: 不然同一个工具在两条路上表现不一样。
TOOL_INSTRUCTIONS = """
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


# --------------------------------------------------------------------- 上下文


@dataclass
class ToolContext:
    """一次工具调用的上下文。

    ``principal`` 决定能看到哪些空间：
    * ``None`` —— **服务级**（MCP 的既有行为：能看到所有空间）
    * 某个 ``Principal`` —— 按空间可见性过滤（网页 AI 助手走这条）
    """

    registry: Any
    config: Any = None
    principal: Any = None

    def get_dataset(self, dataset_id: str):
        return self.registry.get_dataset(dataset_id, self.principal)

    def get_raster(self, dataset_id: str, layer_id: str):
        return self.registry.get_raster(dataset_id, layer_id, self.principal)

    def get_vector(self, dataset_id: str, vector_id: str):
        return self.registry.get_vector(dataset_id, vector_id, self.principal)

    def layer_refs(self) -> list[dict]:
        return self.registry.layer_refs(self.principal)

    def datasets(self) -> list:
        """当前身份能看到的数据集。"""
        return self.registry.datasets(self.principal)

    def resolve_ref(self, ref: str):
        """把「数据集:图层」引用解析成图层对象（**按身份过滤**）。

        算法参数里可以塞任意引用，不带身份过滤的话，
        普通用户能让 QGIS 去读别人的私有图层。
        """
        return self.registry.resolve_ref(ref, self.principal)

    def check_scope(self, scope: str | None, write: bool = False) -> str:
        """校验要操作的数据空间，非法就报错。

        ``principal`` 为 None（服务级 / MCP）时不过滤 —— 那是既有行为；
        网页 AI 助手走这条时按登录用户过滤。
        """
        target = scope or "shared"
        if self.principal is None:
            return target
        allowed = (
            self.principal.can_write(target)
            if write
            else self.principal.can_see(target)
        )
        if not allowed:
            raise InvalidRequestError(f"不能操作这个数据空间：{target}")
        return target

    def vector(self, ref: str):
        """按「数据集:图层」取矢量图层。"""
        dataset_id, _, layer_id = ref.partition(":")
        dataset = self.get_dataset(dataset_id)
        layer = dataset.vectors.get(layer_id)
        if layer is None:
            raise NotFoundError(
                f"找不到矢量图层：{ref}",
                {"available": sorted(dataset.vectors.keys())},
            )
        return layer


@dataclass
class RenderResult:
    """渲染出来的图片。

    core 不认识 MCP 的 ``Image``，也不该认识 —— 各自的外壳自己翻译：
    * MCP Server → 图片内容块
    * 网页 AI 助手 → 给用户看的 data URL + 给模型看的一句话
    """

    data: bytes
    caption: str
    width: int = 0
    height: int = 0


@dataclass
class ToolSpec:
    """一个工具的完整定义。

    说明与参数 schema 都是**算出来的**，不是抄出来的：
    * ``description`` ← 处理函数的 docstring
    * ``parameters`` ← 处理函数的签名（参数名、类型、必填项）+ ``param_docs`` 里的中文说明
    """

    name: str
    handler: Callable[..., Awaitable[Any]]
    #: 只读工具（不改数据）。网页 AI 助手默认只给只读工具，
    #: 写操作要在配置里显式开。
    readonly: bool = True
    #: 参数的中文说明：``{"dataset_id": "数据集 id"}``。
    #: 只影响给模型看的说明，**不影响**参数名与类型（那些从签名来）。
    param_docs: dict = field(default_factory=dict)

    @property
    def description(self) -> str:
        """工具说明 = 处理函数的 docstring 原文。"""
        return (self.handler.__doc__ or "").strip()

    @property
    def parameters(self) -> dict:
        """给模型看的 JSON Schema（OpenAI 兼容格式）。"""
        return build_parameters(self)

    def schema(self) -> dict:
        """OpenAI 兼容的 tool 定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def anthropic_schema(self) -> dict:
        """Anthropic 原生的 tool 定义。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# --------------------------------------------------------------------- 小工具


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def resolved_signature(handler: Callable[..., Any]) -> inspect.Signature:
    """取处理函数的签名，**注解解析成真正的类型**。

    本模块开了 ``from __future__ import annotations``，不解析的话拿到的
    注解是字符串 —— MCP SDK 拿字符串去建 pydantic 模型会炸。
    """
    try:
        return inspect.signature(handler, eval_str=True)
    except (NameError, TypeError):  # pragma: no cover - 注解写坏了才走到
        return inspect.signature(handler)


def _json_type(annotation: Any) -> dict:
    """把 Python 注解翻译成 JSON Schema 的类型片段。"""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _json_type(args[0])
        return {}                       # 多类型联合：老实说猜不准
    if origin in (list, tuple, set, frozenset):
        args = typing.get_args(annotation)
        return {"type": "array", "items": _json_type(args[0]) if args else {}}
    if origin is dict:
        return {"type": "object"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is str:
        return {"type": "string"}
    if annotation is dict:
        return {"type": "object"}
    if annotation is list:
        return {"type": "array"}
    return {}


def build_parameters(spec: ToolSpec) -> dict:
    """从签名派生工具的 JSON Schema。

    参数名、类型、哪些必填 —— 全部跟实现走；``param_docs`` 只负责补一句
    中文说明。改函数签名，这张表自动跟着变，不会漂。
    """
    sig = resolved_signature(spec.handler)
    props: dict[str, dict] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name == "ctx":
            continue
        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            annotation = str
        schema = _json_type(annotation)
        doc = spec.param_docs.get(name)
        if doc:
            schema["description"] = doc
        props[name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(name)
    out: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


# --------------------------------------------------------------------- 工具实现
#
# 每个工具都是「模块级 async 函数 + 第一个参数 ctx」，
# 这样：既能直接调用（网页 AI），也能闭包绑定到 MCP Server 上（签名会被保留）。


async def _gis_list_datasets(ctx: ToolContext) -> str:
    """列出服务中所有可用的 GIS 数据集。"""

    def _work() -> str:
        datasets = ctx.datasets()
        lines = [f"共 {len(datasets)} 个数据集："]
        for dataset in datasets:
            lines.append(f"\n[{dataset.id}] {dataset.name}")
            if dataset.description:
                lines.append(f"  {dataset.description}")
            for layer in dataset.layers.values():
                year = f"，年份 {layer.year}" if layer.year else ""
                classes = f"，{len(layer.classes)} 个类别" if layer.classes else ""
                lines.append(
                    f"  - 栅格 {layer.id}（{layer.kind}，{layer.width}x{layer.height}，"
                    f"{layer.crs}{year}{classes}）"
                )
            for vector in dataset.vectors.values():
                lines.append(
                    f"  - 矢量 {vector.id}（{vector.geometry_type}，{vector.feature_count} 个要素）"
                )
        if ctx.registry.scan_errors:
            lines.append("\n扫描告警：")
            lines += [f"  ! {err}" for err in ctx.registry.scan_errors]
        return "\n".join(lines)

    from .executor import run_qgis

    return await run_qgis(_work)


async def _gis_get_dataset(ctx: ToolContext, dataset_id: str) -> str:
    """查看某个数据集的详细信息（全部图层、类别表、坐标系、范围）。"""
    dataset = ctx.get_dataset(dataset_id)
    return _json(dataset.to_dict(detailed=True))


async def _gis_list_layers(
    ctx: ToolContext,
    dataset_id: str | None = None,
    kind: str | None = None,
    keyword: str | None = None,
) -> str:
    """检索图层。kind 可取 raster / vector；keyword 按名称模糊匹配。

    返回的 ref 形如「数据集:图层」，可直接用于 gis_run_algorithm 的输入参数。
    """
    items = ctx.layer_refs()
    if dataset_id:
        items = [i for i in items if i["dataset_id"] == dataset_id]
    if kind:
        wanted = kind.lower()
        if wanted == "raster":
            items = [i for i in items if i["kind"] == "raster"]
        elif wanted == "vector":
            items = [i for i in items if i["kind"] == "vector"]
    if keyword:
        needle = keyword.lower()
        items = [
            i
            for i in items
            if needle in i["ref"].lower()
            or needle in (i["title"] or "").lower()
            or needle in (i["dataset_name"] or "").lower()
        ]
    return _json({"count": len(items), "layers": items})


async def _gis_rescan(ctx: ToolContext) -> str:
    """重新扫描数据根目录（新增或修改数据后调用）。"""

    def _work() -> str:
        datasets = ctx.registry.scan()
        return f"重新扫描完成，当前 {len(datasets)} 个数据集。"

    from .executor import run_qgis

    return await run_qgis(_work)


# --------------------------------------------------------------------- 栅格


async def _gis_query_point(
    ctx: ToolContext, dataset_id: str, layer_id: str, lon: float, lat: float
) -> str:
    """查询某个经纬度位置在指定栅格图层上的取值。

    lon/lat 为 WGS84 经纬度。分类图层会同时返回类别名。
    """
    from . import raster as raster_core
    from .executor import run_qgis

    layer = ctx.get_raster(dataset_id, layer_id)
    result = await run_qgis(raster_core.point_query, layer, lon, lat, "EPSG:4326")
    return _json(result)


async def _gis_query_bbox(
    ctx: ToolContext,
    dataset_id: str,
    layer_id: str,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
) -> str:
    """查询指定经纬度范围内的栅格统计。

    分类图层返回各类像元数与面积（公顷），连续图层返回 min/max/mean/std。
    """
    from . import raster as raster_core
    from .executor import run_qgis

    layer = ctx.get_raster(dataset_id, layer_id)
    result = await run_qgis(
        raster_core.bbox_stats, layer, (minx, miny, maxx, maxy), "EPSG:4326", 4_000_000
    )
    return _json(result)


async def _gis_layer_area(ctx: ToolContext, dataset_id: str, layer_id: str) -> str:
    """统计整个图层的地类面积（分类图层的核心指标）。"""
    from . import raster as raster_core
    from .executor import run_qgis

    layer = ctx.get_raster(dataset_id, layer_id)
    minx, miny, maxx, maxy = layer.extent_wgs84
    result = await run_qgis(
        raster_core.bbox_stats, layer, (minx, miny, maxx, maxy), "EPSG:4326", 4_000_000
    )
    return _json(
        {
            "dataset_id": dataset_id,
            "layer_id": layer_id,
            "kind": layer.kind,
            "classes": result.get("classes", []),
            "area_m2": result.get("area_m2"),
            "area_method": result.get("area_method"),
        }
    )


async def _gis_layer_histogram(
    ctx: ToolContext, dataset_id: str, layer_id: str, bins: int = 32
) -> str:
    """计算指定栅格图层的像元值直方图。"""
    from . import raster as raster_core
    from .executor import run_qgis

    layer = ctx.get_raster(dataset_id, layer_id)
    result = await run_qgis(raster_core.layer_histogram, layer, bins)
    return _json(result)


# --------------------------------------------------------------------- 矢量


async def _gis_list_features(
    ctx: ToolContext,
    dataset_id: str,
    vector_id: str,
    bbox: str | None = None,
    limit: int = 50,
    where: str | None = None,
) -> str:
    """取矢量要素，返回 GeoJSON。

    bbox 形如 "minx,miny,maxx,maxy"（经纬度）；where 为 QGIS 属性过滤表达式。
    """
    from . import vector as vector_core
    from .executor import run_qgis

    layer = ctx.get_vector(dataset_id, vector_id)
    box = None
    if bbox:
        parts = [p.strip() for p in bbox.split(",") if p.strip()]
        if len(parts) == 4:
            box = tuple(float(p) for p in parts)
    result = await run_qgis(vector_core.list_features, layer, box, limit, 0, where, False)
    return _json(result)


async def _gis_vector_stats(ctx: ToolContext, dataset_id: str, vector_id: str) -> str:
    """统计矢量图层：要素总数、几何类型、范围、各字段取值分布。"""
    from . import vector as vector_core
    from .executor import run_qgis

    layer = ctx.get_vector(dataset_id, vector_id)
    result = await run_qgis(vector_core.vector_stats, layer, None, 10)
    return _json(result)


async def _gis_point_hit(
    ctx: ToolContext, dataset_id: str, vector_id: str, lon: float, lat: float
) -> str:
    """查询包含指定经纬度的矢量要素（如点落在哪个地块内）。"""
    from . import vector as vector_core
    from .executor import run_qgis

    layer = ctx.get_vector(dataset_id, vector_id)
    result = await run_qgis(vector_core.vector_hit, layer, lon, lat, 20)
    return _json(result)


# --------------------------------------------------------------------- 渲染


async def _gis_render_map(
    ctx: ToolContext,
    dataset_id: str,
    layer_id: str,
    bbox: str | None = None,
    width: int = 800,
):
    """把栅格图层渲染成 PNG 图片并返回。

    bbox 形如 "minx,miny,maxx,maxy"（经纬度），缺省渲染整个图层。
    分类图层会自动套用类别配色。
    """
    from . import render as render_core
    from .executor import run_qgis

    layer = ctx.get_raster(dataset_id, layer_id)
    box = None
    if bbox:
        parts = [p.strip() for p in bbox.split(",") if p.strip()]
        if len(parts) == 4:
            box = tuple(float(p) for p in parts)
    data, meta = await run_qgis(
        render_core.render_raster_png, layer, box, width, None, "EPSG:4326"
    )
    caption = f"{dataset_id}/{layer_id} ({meta['width']}x{meta['height']})"
    return RenderResult(
        data=data,
        caption=caption,
        width=int(meta.get("width") or 0),
        height=int(meta.get("height") or 0),
    )


# --------------------------------------------------------------------- Processing 工具箱


async def _gis_list_algorithms(
    ctx: ToolContext,
    query: str | None = None,
    group: str | None = None,
    provider: str | None = None,
) -> str:
    """检索 QGIS Processing 算法（共 440+ 个）。

    query 按算法 id / 名称 / 分组模糊搜索；provider 可取 native / gdal / qgis / pdal。
    """
    from . import processing as proc
    from .executor import run_qgis

    await run_qgis(proc.ensure_ready)
    items = await run_qgis(proc.list_algorithms, query, group, provider)
    return _json(
        {
            "count": len(items),
            "total": proc.algorithm_count(),
            "groups": await run_qgis(proc.algorithm_groups),
            "algorithms": items,
        }
    )


async def _gis_describe_algorithm(ctx: ToolContext, algorithm_id: str) -> str:
    """查看某个算法的参数定义（名称、类型、默认值、枚举选项）。"""
    from . import processing as proc
    from .executor import run_qgis

    await run_qgis(proc.ensure_ready)
    info = await run_qgis(proc.describe_algorithm, algorithm_id)
    info["available_layers"] = ctx.layer_refs()
    return _json(info)


async def _gis_run_algorithm(
    ctx: ToolContext, algorithm: str, parameters: dict | None = None
) -> str:
    """执行一个 Processing 算法。

    algorithm 形如 "native:buffer"；parameters 里的图层参数用
    「数据集:图层」引用（先用 gis_list_layers 查 ref）。
    例：{"INPUT": "demo:roads", "DISTANCE": 500}
    """
    from . import processing as proc
    from .executor import run_qgis

    await run_qgis(proc.ensure_ready)
    params = parameters or {}
    out_dir = Path(tempfile.gettempdir()) / "gisds_algo" / uuid.uuid4().hex[:8]
    out_dir.mkdir(parents=True, exist_ok=True)

    result = await run_qgis(
        proc.run_algorithm,
        algorithm,
        params,
        ctx.resolve_ref,
        out_dir,
    )
    return _json(result)


# --------------------------------------------------------------------- 面板


async def _gis_attribute_table(
    ctx: ToolContext,
    ref: str,
    limit: int = 50,
    offset: int = 0,
    where: str | None = None,
) -> str:
    """读属性表（要素字段 + 一页数据）。

    ref 形如「数据集:图层」；where 是 QGIS 属性过滤表达式。
    """
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    data = await run_qgis(panels_core.attribute_table, layer, limit, offset, where, None)
    return _json(data)


async def _gis_field_stats(ctx: ToolContext, ref: str, field: str | None = None) -> str:
    """字段统计：计数 / 唯一值 / 最小值 / 最大值 / 平均值 / 中位数 / 求和。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    data = await run_qgis(panels_core.field_statistics, layer, field)
    return _json(data)


async def _gis_select_features(
    ctx: ToolContext,
    ref: str,
    expression: str | None = None,
    geometry: dict | None = None,
    target_ref: str | None = None,
    predicate: str = "intersects",
    mode: str | None = None,
) -> str:
    """选择要素，返回命中的 fid 列表。

    四种选法：
    * ``expression="面积" > 100`` —— 按属性表达式
    * ``geometry={GeoJSON 几何}`` —— 按空间关系（与给定几何比）
    * ``target_ref="数据集:图层"`` —— 按空间关系（与另一个图层比）
    * ``mode="all"`` —— 全选

    predicate 可取 intersects / contains / within / touches / crosses /
    overlaps / disjoint。
    """
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    if mode and mode.lower() == "all":
        data = await run_qgis(panels_core.select_all, layer)
    elif expression:
        data = await run_qgis(panels_core.select_by_expression, layer, expression)
    elif target_ref:
        data = await run_qgis(
            panels_core.select_by_layer, layer, ctx.vector(target_ref), predicate
        )
    elif geometry:
        data = await run_qgis(panels_core.select_by_spatial, layer, geometry, predicate, "replace")
    else:
        raise InvalidRequestError("要么给 expression，要么给 geometry，要么给 target_ref，要么 mode=all")
    return _json(data)


async def _gis_edit_start(ctx: ToolContext, ref: str) -> str:
    """进入编辑模式（改动先落在内存里，要 gis_edit_commit 才写回文件）。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    return _json(await run_qgis(panels_core.start_edit, layer))


async def _gis_edit_commit(ctx: ToolContext, ref: str) -> str:
    """保存编辑（写回数据文件）。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    return _json(await run_qgis(panels_core.commit_edit, layer))


async def _gis_edit_rollback(ctx: ToolContext, ref: str) -> str:
    """放弃编辑（丢弃所有未保存的改动）。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    return _json(await run_qgis(panels_core.rollback_edit, layer))


async def _gis_edit_feature(
    ctx: ToolContext, ref: str, fid: int, attributes: dict
) -> str:
    """改一个要素的属性（需要先 gis_edit_start）。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    return _json(
        await run_qgis(panels_core.update_feature, layer, int(fid), attributes or {})
    )


async def _gis_add_feature(
    ctx: ToolContext, ref: str, geometry: dict, attributes: dict | None = None
) -> str:
    """新增一个要素（GeoJSON 几何，需要先 gis_edit_start）。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    return _json(
        await run_qgis(panels_core.add_feature, layer, geometry, attributes or {})
    )


async def _gis_delete_features(ctx: ToolContext, ref: str, fids: list[int]) -> str:
    """删除要素（需要先 gis_edit_start）。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    return _json(await run_qgis(panels_core.delete_features, layer, fids or []))


async def _gis_field_calculator(
    ctx: ToolContext,
    ref: str,
    field: str,
    expression: str,
    only_selected: bool = False,
    selection: list[int] | None = None,
) -> str:
    """字段计算器：按表达式给一个字段批量赋值（需要先 gis_edit_start）。"""
    from . import panels as panels_core
    from .executor import run_qgis

    layer = ctx.vector(ref)
    return _json(
        await run_qgis(
            panels_core.field_calculator,
            layer,
            field,
            expression,
            bool(only_selected),
            selection or [],
        )
    )


# --------------------------------------------------------------------- 书签


async def _gis_list_bookmarks(ctx: ToolContext, scope: str | None = None) -> str:
    """列出空间书签。scope 缺省是共享空间。"""
    from . import bookmarks as bookmarks_core

    target = ctx.check_scope(scope, write=False)
    items = bookmarks_core.list_bookmarks(ctx.config.data_root_path, target)
    return _json({"scope": target, "count": len(items), "bookmarks": items})


async def _gis_add_bookmark(
    ctx: ToolContext,
    name: str,
    extent: list[float],
    scope: str | None = None,
    crs: str = "EPSG:4326",
) -> str:
    """新建空间书签。extent 是 minx,miny,maxx,maxy 四个数。"""
    from . import bookmarks as bookmarks_core

    target = ctx.check_scope(scope, write=True)
    entry = bookmarks_core.add_bookmark(
        ctx.config.data_root_path, target, name, extent, crs
    )
    return _json(entry)


# --------------------------------------------------------------------- 注册表

#: 全部工具。**MCP Server 与网页 AI 助手都从这里取**（别再各写一份）。
#:
#: 顺序与原来 MCP Server 里的定义顺序一致（工具清单的顺序会体现在
#: ``/api/ai/tools`` 里，便于对照）。
TOOLS: list[ToolSpec] = [
    # ---------------- 数据集
    ToolSpec("gis_list_datasets", _gis_list_datasets),
    ToolSpec(
        "gis_get_dataset",
        _gis_get_dataset,
        param_docs={"dataset_id": "数据集 id"},
    ),
    ToolSpec(
        "gis_list_layers",
        _gis_list_layers,
        param_docs={
            "dataset_id": "只看这个数据集（可空）",
            "kind": "raster 或 vector（可空）",
            "keyword": "名称模糊匹配（可空）",
        },
    ),
    ToolSpec("gis_rescan", _gis_rescan),
    # ---------------- 栅格
    ToolSpec(
        "gis_query_point",
        _gis_query_point,
        param_docs={
            "dataset_id": "数据集 id",
            "layer_id": "栅格图层 id",
            "lon": "经度（WGS84）",
            "lat": "纬度（WGS84）",
        },
    ),
    ToolSpec(
        "gis_query_bbox",
        _gis_query_bbox,
        param_docs={
            "dataset_id": "数据集 id",
            "layer_id": "栅格图层 id",
            "minx": "最小经度",
            "miny": "最小纬度",
            "maxx": "最大经度",
            "maxy": "最大纬度",
        },
    ),
    ToolSpec(
        "gis_layer_area",
        _gis_layer_area,
        param_docs={
            "dataset_id": "数据集 id",
            "layer_id": "栅格图层 id",
        },
    ),
    ToolSpec(
        "gis_layer_histogram",
        _gis_layer_histogram,
        param_docs={
            "dataset_id": "数据集 id",
            "layer_id": "栅格图层 id",
            "bins": "分箱数（默认 32）",
        },
    ),
    # ---------------- 矢量
    ToolSpec(
        "gis_list_features",
        _gis_list_features,
        param_docs={
            "dataset_id": "数据集 id",
            "vector_id": "矢量图层 id",
            "bbox": "范围 minx,miny,maxx,maxy（可空）",
            "limit": "最多取几个要素（默认 50）",
            "where": "QGIS 属性过滤表达式（可空）",
        },
    ),
    ToolSpec(
        "gis_vector_stats",
        _gis_vector_stats,
        param_docs={
            "dataset_id": "数据集 id",
            "vector_id": "矢量图层 id",
        },
    ),
    ToolSpec(
        "gis_point_hit",
        _gis_point_hit,
        param_docs={
            "dataset_id": "数据集 id",
            "vector_id": "矢量图层 id",
            "lon": "经度（WGS84）",
            "lat": "纬度（WGS84）",
        },
    ),
    # ---------------- 渲染
    ToolSpec(
        "gis_render_map",
        _gis_render_map,
        param_docs={
            "dataset_id": "数据集 id",
            "layer_id": "栅格图层 id",
            "bbox": "范围 minx,miny,maxx,maxy（可空）",
            "width": "图片宽度（默认 800）",
        },
    ),
    # ---------------- Processing
    ToolSpec(
        "gis_list_algorithms",
        _gis_list_algorithms,
        param_docs={
            "query": "按 id / 名称 / 分组搜（可空）",
            "group": "分组名（可空）",
            "provider": "native / gdal / qgis / pdal（可空）",
        },
    ),
    ToolSpec(
        "gis_describe_algorithm",
        _gis_describe_algorithm,
        param_docs={"algorithm_id": "算法 id，如 native:buffer"},
    ),
    ToolSpec(
        "gis_run_algorithm",
        _gis_run_algorithm,
        param_docs={
            "algorithm": "算法 id，如 native:buffer",
            "parameters": "算法参数（可空）",
        },
    ),
    # ---------------- 面板
    ToolSpec(
        "gis_attribute_table",
        _gis_attribute_table,
        param_docs={
            "ref": "图层引用 数据集:图层",
            "limit": "一页几个（默认 50）",
            "offset": "跳过几个（默认 0）",
            "where": "QGIS 属性过滤表达式（可空）",
        },
    ),
    ToolSpec(
        "gis_field_stats",
        _gis_field_stats,
        param_docs={
            "ref": "图层引用 数据集:图层",
            "field": "只统计这个字段（可空）",
        },
    ),
    ToolSpec(
        "gis_select_features",
        _gis_select_features,
        param_docs={
            "ref": "图层引用 数据集:图层",
            "expression": "属性表达式（可空）",
            "geometry": "GeoJSON 几何（可空）",
            "target_ref": "另一个图层引用（可空）",
            "predicate": "空间关系算子，默认 intersects",
            "mode": "all 表示全选（可空）",
        },
    ),
    # ---------------- 编辑（写操作）
    ToolSpec(
        "gis_edit_start",
        _gis_edit_start,
        readonly=False,
        param_docs={"ref": "图层引用 数据集:图层"},
    ),
    ToolSpec(
        "gis_edit_commit",
        _gis_edit_commit,
        readonly=False,
        param_docs={"ref": "图层引用 数据集:图层"},
    ),
    ToolSpec(
        "gis_edit_rollback",
        _gis_edit_rollback,
        readonly=False,
        param_docs={"ref": "图层引用 数据集:图层"},
    ),
    ToolSpec(
        "gis_edit_feature",
        _gis_edit_feature,
        readonly=False,
        param_docs={
            "ref": "图层引用 数据集:图层",
            "fid": "要素 id",
            "attributes": "要改的属性",
        },
    ),
    ToolSpec(
        "gis_add_feature",
        _gis_add_feature,
        readonly=False,
        param_docs={
            "ref": "图层引用 数据集:图层",
            "geometry": "GeoJSON 几何",
            "attributes": "属性（可空）",
        },
    ),
    ToolSpec(
        "gis_delete_features",
        _gis_delete_features,
        readonly=False,
        param_docs={
            "ref": "图层引用 数据集:图层",
            "fids": "要删的要素 id 列表",
        },
    ),
    ToolSpec(
        "gis_field_calculator",
        _gis_field_calculator,
        readonly=False,
        param_docs={
            "ref": "图层引用 数据集:图层",
            "field": "要赋值的字段",
            "expression": "QGIS 表达式",
            "only_selected": "只改选中的要素（默认 false）",
            "selection": "选中的要素 id（可空）",
        },
    ),
    # ---------------- 书签
    ToolSpec(
        "gis_list_bookmarks",
        _gis_list_bookmarks,
        param_docs={"scope": "shared 或 user:<用户ID>（可空）"},
    ),
    ToolSpec(
        "gis_add_bookmark",
        _gis_add_bookmark,
        readonly=False,
        param_docs={
            "name": "书签名",
            "extent": "minx,miny,maxx,maxy",
            "scope": "shared 或 user:<用户ID>（可空）",
            "crs": "坐标系，默认 EPSG:4326",
        },
    ),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def tool_names() -> list[str]:
    """全部工具名（按注册顺序）。"""
    return [t.name for t in TOOLS]


def select_tools(allow_write: bool = False) -> list[ToolSpec]:
    """挑出这一轮允许给模型用的工具。

    ``allow_write`` 为假时**只给只读工具** —— 网页 AI 助手默认走这条：
    任何登录用户都不该借着「聊天」把数据改了。
    """
    if allow_write:
        return list(TOOLS)
    return [t for t in TOOLS if t.readonly]


def bind(spec: ToolSpec, ctx: ToolContext):
    """把工具绑到一个上下文上，返回**带原签名**的函数。

    MCP SDK 是靠 ``inspect.signature`` 推出参数 schema 的，
    所以这里必须把 ``ctx`` 那个参数藏掉、其余签名原样保留
    （连注解都要是**解析过的真类型**，否则 pydantic 建不出模型）。

    绑上去的函数另有一层用途：网页 AI 助手直接调它，不经过 MCP。
    """
    sig = resolved_signature(spec.handler)
    params = [p for p in sig.parameters.values() if p.name != "ctx"]
    new_sig = sig.replace(parameters=params)

    async def bound(*args, **kwargs):
        return await spec.handler(ctx, *args, **kwargs)

    bound.__name__ = spec.name
    bound.__qualname__ = spec.name
    bound.__doc__ = spec.description
    bound.__signature__ = new_sig
    annotations = {p.name: p.annotation for p in params}
    if new_sig.return_annotation is not inspect.Signature.empty:
        annotations["return"] = new_sig.return_annotation
    bound.__annotations__ = annotations
    return bound
