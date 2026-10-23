"""PyQGIS 运行时初始化。

整个服务进程共用一个 ``QgsApplication``，且只在需要时初始化一次。
QGIS 的 GUI 被关闭（``QgsApplication([], False)``），因此可以在无桌面环境下跑。
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

_STATE: dict[str, Any] = {
    "app": None,
    "prefix": None,
    "processing": False,
    "processing_error": None,
    "thread_id": None,
}


def qgis_thread_id() -> int | None:
    """创建 QgsApplication 的那个线程的 id。"""
    return _STATE.get("thread_id")


def in_qgis_thread() -> bool:
    """当前线程是不是 QGIS 线程。"""
    thread_id = _STATE.get("thread_id")
    return thread_id is not None and thread_id == threading.get_ident()


def ensure_qgis_thread(what: str) -> None:
    """确认当前就在 QGIS 线程上，否则报错。

    QgsApplication、Processing 的 registry/provider 都是 QObject，有线程亲和性。
    在别的线程上往它们挂子对象会触发
    ``QObject: Cannot create children for a parent that is in a different thread``，
    进而让整个进程段错误。所以所有会**创建** QGIS 对象的操作都必须跟
    ``QgsApplication`` 同线程（服务端就是 ``core.executor`` 里的那条工作线程）。
    """
    thread_id = _STATE.get("thread_id")
    if thread_id is not None and thread_id != threading.get_ident():
        raise RuntimeError(
            f"{what} 必须在 QGIS 工作线程上执行（当前线程 {threading.get_ident()}，"
            f"QGIS 线程 {thread_id}）"
        )


def qgis_prefix_path() -> str:
    """定位 QGIS 安装前缀（.../apps/qgis-ltr）。"""
    env = os.environ.get("QGIS_PREFIX_PATH")
    if env:
        return env
    try:
        import qgis.core as qgis_core

        # .../apps/qgis-ltr/python/qgis/core/__init__.py -> 上溯 4 层
        return str(Path(qgis_core.__file__).resolve().parents[3])
    except Exception:
        return ""


def processing_plugin_dir() -> str:
    prefix = _STATE.get("prefix") or qgis_prefix_path()
    if not prefix:
        return ""
    return str(Path(prefix) / "python" / "plugins")


def ensure_processing() -> bool:
    """按需初始化 Processing 算法框架（失败不致命，只是没有算法可用）。

    必须在 QGIS 工作线程上调用：Processing 会往 ``QgsProcessingRegistry`` 里
    挂 provider，而 registry 是 QObject，只能在创建它的线程上加子对象。
    """
    if _STATE.get("processing"):
        return True
    ensure_qgis_thread("Processing 初始化")
    # 兜底：确保翻译器在算法构造之前就位（参数说明在构造时翻译）
    if not _STATE.get("language"):
        _install_translation()
    plugin_dir = processing_plugin_dir()
    if not plugin_dir or not Path(plugin_dir).is_dir():
        return False
    if plugin_dir not in sys.path:
        sys.path.append(plugin_dir)
    try:
        from processing.core.Processing import Processing

        Processing.initialize()
        _STATE["processing"] = True
        _STATE["processing_error"] = None
        return True
    except Exception as exc:  # pragma: no cover - 取决于本地 QGIS 安装
        _STATE["processing_error"] = str(exc)
        return False


def init_qgis() -> bool:
    """初始化 QGIS 运行时（幂等）。

    第一次调用决定 QGIS 线程（``QgsApplication`` 是 QObject，之后所有图层、
    provider 都得跟它同线程）；之后的调用必须在同一条线程上。
    """
    if _STATE.get("app") is not None:
        ensure_qgis_thread("QGIS 初始化")
        return True

    from qgis.core import QgsApplication

    prefix = qgis_prefix_path()
    if prefix:
        QgsApplication.setPrefixPath(prefix, True)
    _STATE["prefix"] = prefix

    # 第二个参数 False = 不开 GUI；headless 服务器必需
    app = QgsApplication([], False)
    app.initQgis()
    _STATE["app"] = app
    _STATE["thread_id"] = threading.get_ident()

    # 装 QGIS 官方翻译包（必须在 Processing 初始化之前，
    # 否则参数的说明文字会在构造时就被固定成英文）
    _install_translation()
    return True


def _install_translation() -> None:
    """装载 QGIS 自带的翻译包（失败不影响功能，只是元数据保持英文）。"""
    from .config import load_config

    try:
        locale = load_config().language
    except Exception:
        locale = ""
    if not locale:
        return
    from .core import i18n

    try:
        if i18n.install_translator(locale):
            _STATE["language"] = locale
    except Exception as exc:  # pragma: no cover - 取决于本地 QGIS 安装
        _STATE["language_error"] = str(exc)


def is_initialized() -> bool:
    return _STATE.get("app") is not None


def shutdown_qgis() -> None:
    app = _STATE.get("app")
    if app is None:
        return
    try:
        app.exitQgis()
    except Exception:
        pass
    _STATE["app"] = None


def runtime_info() -> dict[str, Any]:
    """返回运行时信息，供 /api/meta 与 doctor 命令使用。"""
    info: dict[str, Any] = {
        "initialized": is_initialized(),
        "prefix_path": _STATE.get("prefix") or qgis_prefix_path(),
        "processing": bool(_STATE.get("processing")),
        "processing_error": _STATE.get("processing_error"),
    }
    try:
        from .core import i18n

        info["translation"] = i18n.status()
    except Exception:
        info["translation"] = {"installed": False}
    try:
        from qgis.core import Qgis

        info["qgis_version"] = Qgis.QGIS_VERSION
    except Exception:
        info["qgis_version"] = None
    info["python"] = sys.version.split()[0]
    return info
