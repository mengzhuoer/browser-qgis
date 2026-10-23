"""QGIS Processing 工具箱接入。

把 QGIS 自带的 Processing 框架搬到服务端：
- **自省**：列出全部算法及其参数定义（名称 / 类型 / 默认值 / 枚举选项 / 取值范围），
  网页据此自动生成参数表单；
- **执行**：在 QGIS 线程里跑算法，输出落到工作区目录，随后可在地图上叠加或下载。

算法清单来自 QGIS 本体，不需要在本项目里维护任何算法实现。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from .. import qgis_env
from . import i18n
from .errors import InvalidRequestError, NotFoundError

_STATE: dict[str, Any] = {"ready": False, "algorithm_count": 0}

# 参数类型 -> 网页表单类型
_PARAM_TYPES: list[tuple[str, str]] = []


def _param_type(param) -> str:
    """把参数对象映射成稳定的字符串类型。

    QGIS 各版本的参数类不完全一致（有的老类已移除），所以逐个探测存在性。
    """
    import qgis.core as qc

    checks: list[tuple[str, str]] = [
        # 先具体后一般：Destination 继承 Sink，Distance 继承 Number
        ("QgsProcessingParameterVectorDestination", "vectorout"),
        ("QgsProcessingParameterRasterDestination", "rasterout"),
        ("QgsProcessingParameterFeatureSink", "sink"),
        ("QgsProcessingParameterSink", "sink"),
        ("QgsProcessingParameterFeatureSource", "source"),
        ("QgsProcessingParameterSource", "source"),
        ("QgsProcessingParameterVectorLayer", "vector"),
        ("QgsProcessingParameterRasterLayer", "raster"),
        ("QgsProcessingParameterPointCloudLayer", "pointcloud"),
        ("QgsProcessingParameterMeshLayer", "mesh"),
        ("QgsProcessingParameterEnum", "enum"),
        ("QgsProcessingParameterBoolean", "boolean"),
        ("QgsProcessingParameterDistance", "distance"),
        ("QgsProcessingParameterNumber", "number"),
        ("QgsProcessingParameterExtent", "extent"),
        ("QgsProcessingParameterCrs", "crs"),
        ("QgsProcessingParameterField", "field"),
        ("QgsProcessingParameterFileDestination", "fileout"),
        ("QgsProcessingParameterFileOutput", "fileout"),
        ("QgsProcessingParameterFolderDestination", "folderout"),
        ("QgsProcessingParameterFolderOutput", "folderout"),
        ("QgsProcessingParameterFile", "file"),
        ("QgsProcessingParameterString", "string"),
    ]
    for class_name, type_name in checks:
        cls = getattr(qc, class_name, None)
        if cls is not None and isinstance(param, cls):
            return type_name
    return "string"


# 输入型参数（可以填图层引用）
LAYER_PARAM_TYPES = {"source", "vector", "raster"}
OUTPUT_PARAM_TYPES = {"sink", "vectorout", "rasterout", "fileout", "folderout"}


def _uses_static_strings(param) -> bool:
    """枚举参数的取值形态判定。

    ``QgsProcessingParameterEnum`` 有两种形态：

    * ``usesStaticStrings() == False``（绝大多数）：取值是**下标整数**（0 起）。
    * ``usesStaticStrings() == True``：取值就是**选项文本本身**。
    """
    fn = getattr(param, "usesStaticStrings", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            return False
    return False


def enum_value_map(param) -> dict[str, Any]:
    """枚举参数的「显示名 -> 实际提交值」映射。

    ``QgsProcessingParameterEnum`` 有两种形态，取值方式完全不同：

    * **下标型**（``usesStaticStrings() == False``，占绝大多数）：
      底层存的是整数下标。例如 ``native:buffer`` 的 ``END_CAP_STYLE``：
      0=圆角(Round) / 1=扁平(Flat) / 2=方角(Square)。直接喂字符串会被判非法参数。
    * **字符串型**（``usesStaticStrings() == True``）：
      底层存的就是选项文本本身。例如 ``native:dxfexport`` 的 ``ENCODING``：``'CP936'``。

    装了 QGIS 官方中文翻译包之后，选项文本是中文（枚举项在算法构造时就被
    ``tr()`` 定死了，运行时取不回英文），所以这张表同时承担
    「中文显示名 -> 底层取值」的翻译。英文原名也一并收进来，
    方便英文客户端按 QGIS 原始名字提交。
    """
    try:
        options = [str(o) for o in param.options()]
    except Exception:
        return {}
    if not options:
        return {}

    static = _uses_static_strings(param)
    mapping: dict[str, Any] = {}
    for index, name in enumerate(options):
        value: Any = name if static else index
        mapping[name] = value
        if static:
            # 字符串型枚举要的是文本本身；这里把下标也登记上，方便按 0/1/2 提交
            mapping.setdefault(str(index), value)
        english = i18n.enum_option_english(name)
        if english and english not in mapping:
            mapping[english] = value
    return mapping


def ensure_ready() -> bool:
    """确保 Processing 框架已初始化（幂等）。"""
    if _STATE.get("ready"):
        return True
    if not qgis_env.ensure_processing():
        return False
    from qgis.core import QgsApplication

    _STATE["algorithm_count"] = len(QgsApplication.processingRegistry().algorithms())
    _STATE["ready"] = True
    return True


def is_ready() -> bool:
    return bool(_STATE.get("ready"))


def _provider_name(provider) -> str:
    """QGIS 不同版本上 provider 的显示名方法不一致，逐个尝试。"""
    if provider is None:
        return ""
    for attr in ("longName", "displayName", "name"):
        fn = getattr(provider, attr, None)
        if callable(fn):
            try:
                value = fn()
                if value:
                    return str(value)
            except Exception:
                continue
    return ""


def _algorithm_fields(algorithm, *, english: bool = False) -> dict[str, str]:
    """取算法的名称 / 分组 / 说明 / 提供者名（按当前语言）。

    这几项都走 ``tr()``，是**调用时**翻译的，所以临时切换翻译器即可拿到另一种语言。
    （参数说明不行 —— 它在算法构造时就被定死了。）
    """
    try:
        description = (algorithm.shortHelpString() or "").strip()[:400]
    except Exception:
        description = ""
    name = algorithm.displayName() or ""
    group = algorithm.group() or ""
    if not english:
        # 官方翻译没覆盖到的少数几项，用人工补充表补齐
        name = i18n.supplement_algorithm_name(algorithm.id(), name)
        group = i18n.supplement_group(group)
    return {
        "name": name,
        "group": group,
        "provider_name": _provider_name(algorithm.provider()),
        "description": description,
    }


def _algorithm_dict(
    algorithm, english: dict[str, str] | None = None
) -> dict[str, Any]:
    provider = algorithm.provider()
    current = _algorithm_fields(algorithm)
    en = english or current
    return {
        "id": algorithm.id(),
        "name": current["name"],
        "name_en": en["name"],
        "group": current["group"],
        "group_en": en["group"],
        "provider": provider.id() if provider else "",
        "provider_name": current["provider_name"],
        "provider_name_en": en["provider_name"],
        "description": current["description"],
        "description_en": en["description"],
        "help_url": algorithm.helpUrl() or "",
    }


def list_algorithms(
    query: str | None = None,
    group: str | None = None,
    provider: str | None = None,
    include_en: bool = False,
) -> list[dict[str, Any]]:
    """列出算法（可按关键字 / 分组 / 提供者过滤）。

    关键字同时匹配中英文，方便用户按英文名（如 ``buffer``）搜到中文算法。

    ``include_en`` 为 False 时不输出 ``*_en`` 字段 —— 清单有 440 个算法，
    带上英文名和英文描述会让响应体从 ~180 KB 涨到 ~320 KB，
    而列表界面只需要中文。英文信息按需从单算法详情
    （:func:`describe_algorithm`）里取。
    """
    if not ensure_ready():
        return []
    from qgis.core import QgsApplication

    algorithms = QgsApplication.processingRegistry().algorithms()
    # 先用英文过一遍：查询要用它匹配英文名 / 英文描述
    with i18n.english_mode():
        english = {a.id(): _algorithm_fields(a, english=True) for a in algorithms}

    items = [_algorithm_dict(a, english.get(a.id())) for a in algorithms]
    if provider:
        items = [i for i in items if i["provider"].lower() == provider.lower()]
    if group:
        needle = group.lower()
        items = [
            i
            for i in items
            if needle in i["group"].lower() or needle in i.get("group_en", "").lower()
        ]
    if query:
        needle = query.lower()
        items = [
            i
            for i in items
            if needle in i["id"].lower()
            or needle in i["name"].lower()
            or needle in i["group"].lower()
            or needle in i["description"].lower()
            or needle in i.get("name_en", "").lower()
            or needle in i.get("group_en", "").lower()
            or needle in i.get("description_en", "").lower()
        ]
    items.sort(key=lambda i: (i["group"], i["name"]))
    if not include_en:
        # 列表界面只要中文；英文按需从单算法详情取
        for item in items:
            item.pop("name_en", None)
            item.pop("group_en", None)
            item.pop("description_en", None)
            item.pop("provider_name_en", None)
    return items


def algorithm_groups() -> list[str]:
    if not ensure_ready():
        return []
    from qgis.core import QgsApplication

    groups = {
        i18n.supplement_group(a.group())
        for a in QgsApplication.processingRegistry().algorithms()
    }
    return sorted(g for g in groups if g)


# 这些 provider 由用户运行时动态填充（建模器、脚本、当前工程里的模型），
# 没有内容是正常现象，不算「不可用」。
_DYNAMIC_PROVIDERS = {"model", "script", "project"}


def providers() -> list[dict[str, Any]]:
    """列出全部 provider，并标注哪些是**空的**。

    空 provider 的典型例子是 GRASS：插件加载了，但 GRASS 本体没装好，
    结果一个算法都贡献不出来。这种 provider 要在界面上提示用户，
    否则用户会以为「分组是空的」。
    """
    if not ensure_ready():
        return []
    from qgis.core import QgsApplication

    registry = QgsApplication.processingRegistry()
    counts: dict[str, int] = {}
    for algorithm in registry.algorithms():
        provider = algorithm.provider()
        if provider is None:
            continue
        counts[provider.id()] = counts.get(provider.id(), 0) + 1

    out = []
    for provider in registry.providers():
        pid = provider.id()
        count = counts.get(pid, 0)
        out.append(
            {
                "id": pid,
                "name": _provider_name(provider),
                "algorithm_count": count,
                "available": count > 0 or pid in _DYNAMIC_PROVIDERS,
                "dynamic": pid in _DYNAMIC_PROVIDERS,
            }
        )
    return sorted(out, key=lambda p: p["id"])


def _is_optional(param) -> bool:
    """参数是否可选（不同版本 Flag 枚举位置略有差异）。"""
    try:
        flags = param.flags()
    except Exception:
        return True
    try:
        from qgis.core import QgsProcessingParameterDefinition

        flag = getattr(QgsProcessingParameterDefinition, "FlagOptional", None)
        if flag is None:
            flag = getattr(QgsProcessingParameterDefinition, "Optional", None)
        if flag is not None:
            return bool(int(flags) & int(flag))
    except Exception:
        pass
    return False


def _allows_multiple(param) -> bool:
    """参数是否允许多值（枚举参数里比较常见）。"""
    for attr in ("allowMultiple", "isMulti"):
        fn = getattr(param, attr, None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                continue
    return False


def _parameter_dict(param) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": param.name(),
        "type": _param_type(param),
        # 官方翻译没覆盖的参数说明（集中在 GDAL provider），用补充表补齐
        "description": i18n.supplement_param_description(param.description() or ""),
        "default": None,
        "optional": _is_optional(param),
        "help": (param.help() or "").strip()[:300],
    }

    try:
        spec["default"] = _jsonable(param.defaultValue())
    except Exception:
        spec["default"] = None

    if hasattr(param, "options"):
        try:
            options = [str(o) for o in param.options()]
        except Exception:
            options = []
        spec["options"] = options
        if options:
            mapping = enum_value_map(param)
            # option_values = 真正要提交给 QGIS 的取值
            #   下标型 -> 整数下标；字符串型 -> 选项文本本身
            spec["option_values"] = [mapping.get(name, index) for index, name in enumerate(options)]
            spec["multi"] = bool(_allows_multiple(param))
            # 默认值可能是下标，也可能是下标列表或文本
            default = spec.get("default")
            if isinstance(default, bool):
                pass
            elif isinstance(default, int):
                spec["default_label"] = (
                    options[default] if 0 <= default < len(options) else str(default)
                )
            elif isinstance(default, str) and default in options:
                spec["default_label"] = default
            elif isinstance(default, list) and default and all(
                isinstance(d, int) and 0 <= d < len(options) for d in default
            ):
                spec["default_label"] = [options[d] for d in default]
            elif isinstance(default, list) and default and all(
                isinstance(d, str) and d in options for d in default
            ):
                spec["default_label"] = list(default)
    if hasattr(param, "minimum"):
        try:
            spec["min"] = param.minimum()
            spec["max"] = param.maximum()
        except Exception:
            pass
    if hasattr(param, "defaultFileExtension"):
        try:
            ext = param.defaultFileExtension()
            if ext:
                spec["extension"] = ext
        except Exception:
            pass
    if hasattr(param, "dataTypes"):
        try:
            spec["data_types"] = [str(t) for t in param.dataTypes()]
        except Exception:
            pass
    return spec


def describe_algorithm(algorithm_id: str) -> dict[str, Any]:
    """算法的完整描述（含参数定义），网页据此生成参数表单。"""
    if not ensure_ready():
        raise NotFoundError("Processing 框架未就绪，无法列出算法")
    from qgis.core import QgsApplication

    algorithm = QgsApplication.processingRegistry().algorithmById(algorithm_id)
    if algorithm is None:
        available = [a.id() for a in QgsApplication.processingRegistry().algorithms()]
        raise NotFoundError(
            f"算法不存在：{algorithm_id}",
            {"suggestions": [i for i in available if algorithm_id.split(":")[-1] in i][:10]},
        )

    with i18n.english_mode():
        english = _algorithm_fields(algorithm, english=True)
    info = _algorithm_dict(algorithm, english)
    info["parameters"] = [_parameter_dict(p) for p in algorithm.parameterDefinitions()]
    info["outputs"] = [
        {"name": o.name(), "description": o.description(), "type": type(o).__name__}
        for o in algorithm.outputDefinitions()
    ]
    return info


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


# --------------------------------------------------------------------- 执行


try:
    from qgis.core import QgsProcessingFeedback as _FeedbackBase
except Exception:  # pragma: no cover - QGIS 未安装时
    class _FeedbackBase:  # type: ignore[no-redef]
        def __init__(self) -> None:
            pass


class _Feedback(_FeedbackBase):
    """把 Processing 的日志实时写进作业状态。"""

    def __init__(self, job_id: str | None = None, jobs=None):
        super().__init__()
        self.job_id = job_id
        self.jobs = jobs
        self.lines: list[str] = []

    def _emit(self, line: str) -> None:
        self.lines.append(line)
        if self.job_id and self.jobs is not None:
            self.jobs.append_log(self.job_id, line)

    def pushInfo(self, info: str) -> None:
        self._emit(str(info))

    def pushCommandInfo(self, info: str) -> None:
        self._emit(str(info))

    def pushDebugInfo(self, info: str) -> None:
        self._emit("DEBUG " + str(info))

    def pushConsoleInfo(self, info: str) -> None:
        self._emit(str(info))

    def reportError(self, error: str, fatalError: bool = False) -> None:
        self._emit(("FATAL " if fatalError else "ERROR ") + str(error))


def _materialize(layer, path: Path) -> Path:
    """把内存图层落成真实文件，便于下载与叠加。"""
    from qgis.core import QgsMapLayerType, QgsVectorFileWriter

    path.parent.mkdir(parents=True, exist_ok=True)
    if layer.type() == QgsMapLayerType.VectorLayer:
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = path.stem
        options.fileEncoding = "UTF-8"
        error = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, str(path), layer.transformContext(), options
        )
        if error[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"矢量输出失败：{error}")
        return path

    # 栅格：走 GDAL
    from osgeo import gdal

    source = layer.dataProvider().source()
    gdal.UseExceptions()
    gdal.Translate(str(path), source)
    return path


def _enum_english_map(param) -> dict[str, int]:
    """英文原名 -> 取值。

    枚举选项在**算法构造时**就被翻译成了中文，所以
    ``param.options()`` 拿不到英文。但 ``valueAsString()`` 是调用时翻译的，
    临时切回英文就能拿到英文原名 —— 外部脚本 / 保存的工作流里可能还在用
    英文选项名（比如 ``END_CAP_STYLE=Round``），这里做一层兼容。
    """
    try:
        options = [str(o) for o in param.options()]
    except Exception:
        return {}
    if not options:
        return {}
    try:
        from qgis.core import QgsProcessingContext

        ctx = QgsProcessingContext()
    except Exception:
        ctx = None
    if ctx is None:
        return {}

    out: dict[str, int] = {}
    try:
        with i18n.english_mode():
            for value in range(max(64, len(options) * 4)):
                try:
                    name = param.valueAsString(value, ctx)
                except Exception:
                    break
                if not name:
                    continue
                name = str(name)
                if name not in out:
                    out[name] = value
    except Exception:
        pass
    return out


def _coerce_enum(param, value):
    """把枚举参数的可读名称翻译成底层取值。

    支持四种写法：整数（下标）、显示名（中文或英文原名）、
    字符串数字、以及字符串列表（多值枚举）。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_enum(param, v) for v in value]

    mapping = enum_value_map(param)
    if isinstance(value, int):
        # 字符串型枚举要的是选项文本本身，下标得先换算回去
        if str(value) in mapping:
            return mapping[str(value)]
        return value

    name = str(value)
    if name in mapping:
        return mapping[name]
    # 大小写不敏感再试一次
    lowered = {k.lower(): v for k, v in mapping.items() if isinstance(k, str)}
    if name.lower() in lowered:
        return lowered[name.lower()]
    # 中文选项对不上时，再按英文原名找一次（兼容旧脚本）
    english = _enum_english_map(param)
    if name in english:
        return english[name]
    lowered_en = {k.lower(): v for k, v in english.items()}
    if name.lower() in lowered_en:
        return lowered_en[name.lower()]
    try:
        return int(name)
    except (TypeError, ValueError):
        return value


def _check_layer_type(param, ptype: str, layer, algorithm_id: str) -> None:
    """图层类型和参数要求对不上时，给一句**人能看懂**的错。

    不查这一下的话，QGIS 原生报的是「无法加载 INPUT 源图层：无效值」——
    压根看不出是「你把栅格喂给了要要素源的算法」。

    ``source``（QgsProcessingParameterFeatureSource）**只收矢量**：它是「要素源」，
    不是「任意图层」。QGIS 自己会拒，但只给一句「无效值」。
    """
    if ptype not in ("source", "vector", "raster"):
        return
    try:
        from qgis.core import QgsMapLayerType

        ltype = layer.type()
    except Exception:
        return
    if ltype == QgsMapLayerType.VectorLayer:
        got = "矢量"
    elif ltype == QgsMapLayerType.RasterLayer:
        got = "栅格"
    else:
        return
    want = "栅格" if ptype == "raster" else "矢量"
    if got == want:
        return
    raise InvalidRequestError(
        f"算法 {algorithm_id} 的参数 {param.name()} 需要{want}图层，"
        f"但拿到的是{got}图层（{_layer_label(layer)}）"
    )


def _layer_label(layer) -> str:
    """图层的**名字**（不是那个方法的 repr）。

    ``QgsMapLayer.name`` 在 PyQGIS 里是**方法**不是属性 ——
    ``getattr(layer, "name")`` 拿到的是绑定方法，打出来是
    ``<built-in method name of QgsRasterLayer object at 0x...>``，
    用户根本看不出是哪个图层（真踩过：报错的括号里全是这个）。
    """
    try:
        name = getattr(layer, "name", None)
        if callable(name):
            name = name()
        return str(name or "?")
    except Exception:
        return "?"


def _output_path(param, ptype: str, requested: Any, out_dir: Path) -> str:
    """算输出的落盘路径 —— **一律在作业目录里**。

    只认「光秃秃的名字」：没有目录分隔符、没有盘符/冒号、不是 ``.`` / ``..``。
    其余（绝对路径、带目录的相对路径、``../``）一律换成参数自己的默认名。
    这样调用者既不能写到数据根目录外面，也不能写到别的用户空间里去。
    """
    ext = getattr(param, "defaultFileExtension", lambda: "")() or "gpkg"
    name = str(requested or "").strip()
    separators = {"/", "\\", ":", os.sep, os.altsep or "/"}
    safe = bool(name) and name not in (".", "..") and not (separators & set(name))
    if not safe:
        name = param.name()
    if ptype == "folderout":
        return str(out_dir / name)
    return str(out_dir / f"{name}.{ext}")


def run_algorithm(
    algorithm_id: str,
    parameters: dict[str, Any],
    resolve_layer: Callable[[str], Any],
    output_dir: str | Path,
    job_id: str | None = None,
    jobs=None,
) -> dict[str, Any]:
    """执行一个 Processing 算法。

    ``parameters`` 里的图层参数可以写成 ``"数据集:图层"`` 引用，由
    ``resolve_layer`` 解析成 QgsMapLayer。
    """
    if not ensure_ready():
        raise InvalidRequestError("Processing 框架未就绪")

    import processing
    from qgis.core import QgsApplication, QgsProcessingContext, QgsProject

    algorithm = QgsApplication.processingRegistry().algorithmById(algorithm_id)
    if algorithm is None:
        raise NotFoundError(f"算法不存在：{algorithm_id}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    resolved: dict[str, Any] = {}
    for param in algorithm.parameterDefinitions():
        name = param.name()
        ptype = _param_type(param)
        if name not in parameters or parameters[name] in (None, ""):
            # 缺省输出参数：自动给个文件路径
            if ptype in OUTPUT_PARAM_TYPES:
                ext = getattr(param, "defaultFileExtension", lambda: "")() or "gpkg"
                resolved[name] = str(out_dir / f"{name}.{ext}")
            continue

        value = parameters[name]
        if ptype in LAYER_PARAM_TYPES and isinstance(value, str):
            value = resolve_layer(value)
            _check_layer_type(param, ptype, value, algorithm_id)
        elif ptype in OUTPUT_PARAM_TYPES:
            # 输出**一律落在作业目录里**。
            #
            # 原来是把调用者给的 OUTPUT 原样透传给 QGIS：给绝对路径就写绝对路径，
            # 给相对路径就按服务端 cwd 解析（= 项目根目录）。于是：
            #   * 普通用户能指定别的用户私有空间，把文件写进去（实测能写进
            #     对方的 users/<uid>/）；
            #   * 相对路径会把产物丢在项目根目录（真出现过一个 TASKOUT.gpkg）。
            #
            # 现在只认「光秃秃的名字」（没有目录分隔符、不含盘符/冒号、不是 ..），
            # 其余一律用作业目录下的默认名。
            resolved[name] = _output_path(param, ptype, value, out_dir)
            continue
        elif ptype == "crs" and isinstance(value, str):
            from qgis.core import QgsCoordinateReferenceSystem

            value = QgsCoordinateReferenceSystem(value)
        elif ptype == "extent" and isinstance(value, (list, tuple)) and len(value) == 4:
            from qgis.core import QgsRectangle

            value = QgsRectangle(*[float(v) for v in value])
        elif ptype == "field" and isinstance(value, str) and value == "":
            continue
        elif ptype == "enum":
            value = _coerce_enum(param, value)
        elif ptype in ("number", "distance"):
            if isinstance(value, str):
                text = value.strip()
                try:
                    if text.lstrip("+-").isdigit():
                        value = int(text)
                    else:
                        value = float(text)
                except (TypeError, ValueError):
                    pass
        elif ptype == "boolean":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "on")
        resolved[name] = value

    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    try:
        context.setEllipsoid("WGS84")
    except Exception:
        pass

    feedback = _Feedback(job_id, jobs)
    started = time.time()
    try:
        result = processing.run(algorithm_id, resolved, context=context, feedback=feedback)
    except Exception as exc:
        raise RuntimeError(f"算法执行失败：{exc}") from exc

    outputs: dict[str, Any] = {}
    for key, value in (result or {}).items():
        entry: dict[str, Any] = {}
        if isinstance(value, str):
            path = Path(value)
            if path.exists():
                entry = {
                    "path": str(path),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "kind": "file",
                }
            else:
                entry = {"kind": "value", "value": value}
        elif hasattr(value, "source") and hasattr(value, "crs"):
            # 内存图层：落盘
            suffix = ".gpkg" if value.type().name.startswith("Vector") else ".tif"
            path = out_dir / f"{key}{suffix}"
            try:
                _materialize(value, path)
                entry = {
                    "path": str(path),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "kind": "file",
                }
            except Exception as exc:
                entry = {"kind": "layer", "name": key, "error": str(exc)}
        elif isinstance(value, (int, float, str, bool)):
            entry = {"kind": "value", "value": value}
        else:
            entry = {"kind": "value", "value": str(value)}
        outputs[key] = entry

    return {
        "algorithm": algorithm_id,
        "algorithm_name": algorithm.displayName(),
        "elapsed_seconds": round(time.time() - started, 3),
        "outputs": outputs,
        "logs": feedback.lines,
    }


def algorithm_count() -> int:
    if not ensure_ready():
        return 0
    return int(_STATE.get("algorithm_count", 0))
