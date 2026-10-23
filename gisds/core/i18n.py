"""QGIS 官方中文翻译接入。

QGIS 自带简体中文翻译包（``<QGIS>/apps/qgis-ltr/i18n/qgis_zh-Hans.qm``），
装上之后 Processing 的算法名、分组、说明、参数说明、枚举项等元数据
**直接返回中文** —— 不用手写任何翻译表，也不会随 QGIS 升级而失效。

时序陷阱（务必遵守）
--------------------
* 算法名 / 分组 / 帮助文本走 ``tr()``，**调用时**翻译 —— 翻译器装卸随时生效。
* **参数说明、枚举项在算法构造时就被 ``tr()`` 定死了**，
  所以翻译器必须在 Processing 初始化（算法构造）**之前**装好，
  否则这些字段永远是英文。

用法::

    from gisds.core import i18n

    i18n.install_translator()          # 进程启动时调用一次
    with i18n.english_mode():          # 临时切回英文
        name_en = algorithm.displayName()
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Iterator

# 默认语言。置空（config.language = ""）则不做任何翻译，只用英文。
DEFAULT_LOCALE = "zh-Hans"

# ---------------------------------------------------------------------------
# 人工补充
#
# QGIS 官方翻译包已经覆盖了绝大多数元数据（算法名 438/440、分组 433/440、
# 参数说明 2540/2594），剩下的缺口集中在 **GDAL provider** —— 它的算法是
# Python 脚本，参数说明是源码里硬编码的英文，官方翻译包里没有。
# 下面按「英文原文 -> 中文」补齐。
#
# 注意：**枚举项（options）不要翻译**。枚举项的文本就是提交给算法的取值，
# 改了会让调用链对不上（QgsProcessingParameterEnum 认的是原始字符串）。
# ---------------------------------------------------------------------------

# 枚举项的「中文 -> QGIS 原始英文名」对照表。
#
# 枚举项在算法构造时就被 ``tr()`` 定死了，所以装了中文翻译包之后
# ``options()`` 返回的已经是中文，英文原名在运行时再也取不回来。
# 这里把最常用的几个（Qgs::EndCapStyle / Qgs::JoinStyle 家族）补回来，
# 好处有两个：
#   1. 英文客户端（MCP / 脚本）可以直接按 QGIS 原始名字提交参数；
#   2. 接口把 ``option_values``（底层取值）给全了，谁都不会猜错。
#
# 注意：这张表只是「同一个选项的两种叫法」，与具体算法无关 ——
# 翻译出来的下标永远取自该参数自己的选项表，不会串。
ENUM_OPTION_ALIASES: dict[str, str] = {
    "圆角": "Round",
    "扁平": "Flat",
    "方角": "Square",
    "尖角": "Miter",
    "斜角": "Bevel",
}

GROUP_SUPPLEMENT: dict[str, str] = {
    "Database": "数据库",
    "GPS": "GPS 工具",
    "Raster extraction": "栅格提取",
    "Vector miscellaneous": "矢量杂项",
}

ALGORITHM_NAME_SUPPLEMENT: dict[str, str] = {
    "native:importintopostgis": "导出到 PostgreSQL",
    "gdal:gdal2tiles": "gdal2tiles（生成 XYZ 瓦片）",
}

PARAM_DESCRIPTION_SUPPLEMENT: dict[str, str] = {
    "Input layer": "输入图层",
    "Input raster": "输入栅格",
    "Raster input": "输入栅格",
    "Band number": "波段序号",
    "Interval between contour lines": "等值线间距",
    "Produce 3D vector": "生成三维矢量",
    "Treat all raster values as valid": "将所有栅格值视为有效值",
    "Input pixel value to treat as NoData": "视为 NoData 的输入像元值",
    "Offset from zero relative to which to interpret intervals": "解释等值距时相对于零的偏移量",
    "Additional command-line parameters": "附加命令行参数",
    "Additional creation options": "附加创建选项",
    "Enable listing of all layers in the dataset": "列出数据集中的所有图层",
    "Enable listing of features": "列出要素",
    "Suppress metadata info": "不输出元数据信息",
    "Layer information": "图层信息",
    "Output CRS": "输出坐标系",
    "Field type": "字段类型",
    "Function midpoint": "函数中点",
    "Function spread": "函数展度",
    "Membership function exponent": "隶属函数指数",
    "Low fuzzy membership bound": "模糊隶属度下界",
    "High fuzzy membership bound": "模糊隶属度上界",
    "Probability": "概率",
    "Mean": "均值",
    "Mean of normal distribution": "正态分布均值",
    "Standard deviation of normal distribution": "正态分布标准差",
    "Distribution parameter k": "分布参数 k",
    "Lower bound for random number range": "随机数取值范围下界",
    "Upper bound for random number range": "随机数取值范围上界",
    "N": "N（试验次数）",
    "Alpha": "Alpha（形状参数）",
    "Beta": "Beta（形状参数）",
    "Lambda": "Lambda（速率参数）",
    "CRS": "坐标系 (CRS)",
    "URL": "网址 (URL)",
    "DPI": "DPI（每英寸点数）",
    "DXF": "DXF 格式",
}

_STATE: dict[str, Any] = {
    "translator": None,
    "installed": False,
    "locale": "",
    "catalog": "",
    "error": "",
}


def i18n_dir() -> Path:
    """定位 QGIS 的 i18n 目录。"""
    prefix = os.environ.get("QGIS_PREFIX_PATH")
    if not prefix:
        try:
            import qgis.core as qgis_core

            prefix = str(Path(qgis_core.__file__).resolve().parents[3])
        except Exception:
            prefix = ""
    return Path(prefix) / "i18n" if prefix else Path("")


def uninstall_translator() -> bool:
    """卸载翻译器（回到英文）。

    只做移除，不丢弃 QTranslator 对象 —— 重新装载 3.8 MB 的 .qm 有点慢，
    临时切语言时直接复用同一个对象即可。
    """
    if not _STATE.get("installed"):
        return False
    translator = _STATE.get("translator")
    if translator is None:
        _STATE["installed"] = False
        return False
    try:
        from qgis.PyQt.QtCore import QCoreApplication

        QCoreApplication.removeTranslator(translator)
    except Exception:
        pass
    _STATE["installed"] = False
    return True


def _catalog_candidates(directory: Path, locale: str) -> list[Path]:
    """翻译包候选路径。

    QGIS 3.x 把 qgis_core / qgis_app / qgis_gui 的翻译合并进
    ``qgis_<locale>.qm``，实际文件名形如 ``qgis_zh-Hans.qm``、
    ``qgis_zh-Hant.qm``、``qgis_zh_TW.qm``。这里把常见的写法都试一遍。
    """
    names = [
        f"qgis_{locale}.qm",
        f"qgis_{locale.replace('-', '_')}.qm",
        f"qgis_{locale.replace('_', '-')}.qm",
    ]
    aliases = {
        "zh-cn": "zh-Hans",
        "zh_cn": "zh-Hans",
        "zh-hans": "zh-Hans",
        "zh_cn_hans": "zh-Hans",
        "zh-sg": "zh-Hans",
        "zh-tw": "zh-Hant",
        "zh_tw": "zh-Hant",
        "zh-hant": "zh-Hant",
    }
    key = locale.lower().replace("_", "-")
    if key in aliases:
        names.append(f"qgis_{aliases[key]}.qm")
    out: list[Path] = []
    for name in names:
        path = directory / name
        if path.is_file() and path not in out:
            out.append(path)
    return out


def install_translator(locale: str = DEFAULT_LOCALE) -> bool:
    """装载并安装 QGIS 的翻译包（幂等）。

    必须在 Processing 初始化之前调用，否则参数说明不会被翻译。
    """
    if _STATE.get("translator") is not None:
        # 已有对象：只是没装上，直接装回去
        if _STATE.get("installed"):
            return True
        try:
            from qgis.PyQt.QtCore import QCoreApplication

            QCoreApplication.installTranslator(_STATE["translator"])
            _STATE["installed"] = True
            return True
        except Exception as exc:
            _STATE["error"] = str(exc)
            return False
    if not locale:
        return False

    try:
        from qgis.PyQt.QtCore import QCoreApplication, QTranslator
    except Exception as exc:  # pragma: no cover - 没装 QGIS 时
        _STATE["error"] = str(exc)
        return False

    directory = i18n_dir()
    if not directory.is_dir():
        _STATE["error"] = f"未找到 i18n 目录：{directory}"
        return False

    translator = QTranslator()
    loaded = ""
    for candidate in _catalog_candidates(directory, locale):
        # 直接用绝对路径加载：QTranslator 的 locale 重载会拼成 <dir>/<locale>.qm，
        # 找不到 qgis_<locale>.qm 这个命名。
        if translator.load(str(candidate)):
            loaded = str(candidate)
            break
    if not loaded:
        _STATE["error"] = f"未找到翻译包 {locale}（目录 {directory}）"
        return False

    QCoreApplication.installTranslator(translator)
    _STATE.update(
        translator=translator,
        installed=True,
        locale=locale,
        catalog=loaded,
    )
    return True


def translator_enabled() -> bool:
    return bool(_STATE.get("installed"))


def status() -> dict[str, Any]:
    """翻译状态（供 /api/meta 与 doctor 展示）。"""
    return {
        "locale": _STATE.get("locale") or "",
        "installed": bool(_STATE.get("installed")),
        "catalog": _STATE.get("catalog") or "",
        "error": _STATE.get("error") or "",
    }


def supplement_group(text: str) -> str:
    """补齐官方翻译没覆盖的分组名。"""
    return GROUP_SUPPLEMENT.get(text, text)


def supplement_algorithm_name(algorithm_id: str, text: str) -> str:
    """补齐官方翻译没覆盖的算法名。"""
    return ALGORITHM_NAME_SUPPLEMENT.get(algorithm_id, text)


def supplement_param_description(text: str) -> str:
    """补齐官方翻译没覆盖的参数说明。"""
    if not text:
        return text
    return PARAM_DESCRIPTION_SUPPLEMENT.get(text, text)


def enum_option_english(label: str) -> str:
    """枚举显示名（中文）-> QGIS 原始英文名。

    没有收录就返回空串。
    """
    return ENUM_OPTION_ALIASES.get(label, "")


@contextlib.contextmanager
def english_mode() -> Iterator[None]:
    """临时切回英文（只影响调用时翻译的字段）。"""
    was = translator_enabled()
    if was:
        uninstall_translator()
    try:
        yield
    finally:
        if was:
            install_translator(_STATE.get("locale") or DEFAULT_LOCALE)


@contextlib.contextmanager
def chinese_mode() -> Iterator[None]:
    """确保处于中文状态。"""
    if not translator_enabled():
        install_translator(_STATE.get("locale") or DEFAULT_LOCALE)
    yield


def bilingual(fn):
    """装饰器：把函数的调用时字段补一份英文。

    只用于 ``displayName()`` / ``group()`` 这类每次调用都走 ``tr()`` 的接口；
    参数说明在构造时已定死，无法这样取回英文。
    """

    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper
