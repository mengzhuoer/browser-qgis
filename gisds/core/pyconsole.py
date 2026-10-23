"""Python 控制台内核。

QGIS 桌面版有个 Python Console：左边写代码，右边看输出，能直接摸
``QgsProject``、图层、要素。这个模块把同样的能力搬到服务端。

三件事必须做对：

1. **线程**：PyQGIS 不是线程安全的（见 ``core/executor.py``）—— 所有 QGIS
   调用都要排队到那唯一一条工作线程上。控制台的代码也走 ``run_qgis``，
   自己开线程等于跟渲染抢 QGIS 对象 = 偶发崩溃。
2. **隔离**：每个用户一份命名空间。A 用户定义的变量不该出现在 B 用户那儿。
3. **别把服务搞死**：控制台等于在数据服务器上执行任意代码。
   ``os._exit()`` / ``os.abort()`` / ``os.exec*()`` 一执行，服务当场就没了。
   这几样在控制台里被挡住（见 :class:`_GuardedOs` 与 :func:`_guarded_import`）。

**已知限制**：代码在 QGIS 工作线程上跑，Python 没法杀掉一个正在跑的线程。
所以死循环 / 长时间阻塞的代码**超时后接口会返回，但那个线程还占着** ——
QGIS 相关的请求会跟着一起卡住。控制台只对管理员开放（见 ``config.console_access``），
出问题的也是管理员自己；真卡死了就重启服务。
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import io
import logging
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

from .executor import run_qgis

LOGGER = logging.getLogger("gisds.console")

#: 单次执行的输出上限（字符）。控制台是给人看的，不是日志文件，
#: 一段几 MB 的输出会把响应体撑爆。
MAX_OUTPUT_CHARS = 200_000

#: 预置到命名空间里的 QGIS 名字。QGIS 桌面版的控制台也是这么给的，
#: 不用每次都 import 一遍。
_QGIS_NAMES = (
    "Qgis",
    "QgsApplication",
    "QgsCoordinateReferenceSystem",
    "QgsCoordinateTransform",
    "QgsDistanceArea",
    "QgsFeature",
    "QgsFeatureRequest",
    "QgsFeatureSink",
    "QgsField",
    "QgsFields",
    "QgsGeometry",
    "QgsMapLayerType",
    "QgsMapSettings",
    "QgsMessageLog",
    "QgsPointXY",
    "QgsProcessingFeedback",
    "QgsProject",
    "QgsRasterLayer",
    "QgsRectangle",
    "QgsSettings",
    "QgsSpatialIndex",
    "QgsUnitTypes",
    "QgsVectorFileWriter",
    "QgsVectorLayer",
    "QgsWkbTypes",
)


# --------------------------------------------------------------------- 护栏

#: 会把服务进程弄没 / 换掉的 os 调用。挡住它们不是为了防攻击
#: （控制台本来就是执行任意代码的），是为了防**手滑**：
#: ``os._exit(0)`` 写在调试代码里太常见了。
_BLOCKED_OS = frozenset(
    {
        "_exit",
        "abort",
        "fork",
        "forkpty",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnl",
        "spawnle",
        "posix_spawn",
        "posix_spawnp",
        # 这几个一样能把服务弄没 / 改掉
        "kill",       # os.kill(os.getpid(), SIGTERM) = 自杀
        "killpg",
        "system",     # 弹命令行，绕开一切
        "putenv",     # 改环境变量
        "unsetenv",
    }
)

#: 代理自己内部用的名字，也不许从控制台里摸到。
#:
#: 原来真 os 是用**实例属性** ``_real`` 存着的，而 ``__getattr__`` 只在
#: 常规查找失败时才触发 —— 实例属性是能被直接拿到的，``os._real`` 就等于
#: 把真的 os 送出去了（``os._real._exit(0)`` 当场把服务干掉）。
#: 所以真 os 现在藏在**闭包**里，代理的 ``__dict__`` 是空的。
_BLOCKED_ATTRS = frozenset({"_real", "_gisd_real", "__wrapped__"})


def _make_guarded_module(real: Any, real_os: Any, guard: Any) -> Any:
    """``os`` 的子模块代理（目前只有 ``os.path``）。

    转发属性，但把里面引用的**真 os** 换成带护栏的那份 —— ``os.path`` 在
    Windows 上是 ``ntpath``，而 ``ntpath`` 自己 import 了 ``os``，
    不换掉的话 ``os.path.os._exit(0)`` 就把护栏绕过去了。
    """

    class _GuardedModule:
        def __getattr__(self, name: str) -> Any:
            value = getattr(real, name)
            if value is real_os:
                return guard
            return value

        def __setattr__(self, name: str, value: Any) -> None:
            setattr(real, name, value)

        def __dir__(self) -> list[str]:
            return dir(real)

    return _GuardedModule()


def _make_guarded_os(real: Any) -> Any:
    """造一个 ``os`` 代理。

    真 os **不能**挂在实例属性上（理由见 ``_BLOCKED_ATTRS``），
    所以这里用**闭包**把它藏起来：代理的 ``__dict__`` 是空的，摸不到真 os。
    """

    path_proxy: list[Any] = []

    class _GuardedOs:
        """``os`` 模块的代理：挡住会把服务进程弄没的几个调用。"""

        def __getattr__(self, name: str) -> Any:
            if name in _BLOCKED_OS:
                raise AttributeError(
                    f"os.{name} 在控制台里不可用 —— 它会直接结束（或替换）服务进程"
                )
            if name in _BLOCKED_ATTRS:
                raise AttributeError(f"os.{name} 在控制台里不可用")
            value = getattr(real, name)
            if value is real:
                return self
            if getattr(value, "__name__", "") in ("ntpath", "posixpath"):
                if not path_proxy:
                    path_proxy.append(_make_guarded_module(value, real, self))
                return path_proxy[0]
            return value

        def __setattr__(self, name: str, value: Any) -> None:
            setattr(real, name, value)

        def __dir__(self) -> list[str]:
            return dir(real)

    return _GuardedOs()


_REAL_OS = builtins.__import__("os")
_GUARDED_OS = _make_guarded_os(_REAL_OS)

_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
    """``__import__`` 的代理：把 ``os`` 换成带护栏的版本。"""
    module = _real_import(name, globals, locals, fromlist, level)
    if level != 0 or name.split(".")[0] != "os":
        return module
    if name == "os":
        # import os / from os import path —— 顶层换成带护栏的那份
        return _GUARDED_OS
    if fromlist:
        # from os.path import join —— 要的是**真的子模块**。
        # 原来这里一律返回 os 代理，而代理只转发**顶层**属性，join 取不到，
        # 于是 ``from os.path import join`` 报 ImportError（错误信息还指向
        # 错的模块名）。子模块里引用的真 os 仍会被换成护栏那份。
        return _make_guarded_module(module, _REAL_OS, _GUARDED_OS)
    # import os.path —— 顶层绑定要换成带护栏的那份（os.path 由代理转发）
    return _GUARDED_OS


def _make_safe_builtins() -> dict[str, Any]:
    """给控制台用的 builtins：换掉 import，加两个会报错而不是退出的 exit。"""
    safe = dict(vars(builtins))
    safe["__import__"] = _guarded_import

    def _blocked_exit(name: str):
        def _f(*_args, **_kwargs):
            raise RuntimeError(f"控制台里不能用 {name}() —— 它会结束服务进程")

        return _f

    safe["exit"] = _blocked_exit("exit")
    safe["quit"] = _blocked_exit("quit")
    return safe


_SAFE_BUILTINS = _make_safe_builtins()


class _LogCapture(logging.Handler):
    """把 ``logging`` 的输出也收进控制台的标准错误流。"""

    def __init__(self, sink: io.StringIO) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.sink.write(self.format(record) + "\n")
        except Exception:
            pass


# --------------------------------------------------------------------- 会话


@dataclass
class ConsoleResult:
    """一次执行的结果。"""

    stdout: str = ""
    stderr: str = ""
    result: str = ""
    error: str | None = None
    traceback: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "result": self.result,
            "error": self.error,
            "traceback": self.traceback,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ConsoleSession:
    """一个人的控制台会话：一份持久命名空间。"""

    user_id: str
    namespace: dict[str, Any] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.namespace = _bootstrap_namespace()
        self.history.clear()

    def variables(self) -> list[dict[str, str]]:
        """列出命名空间里的变量（给「变量」面板用）。"""
        out: list[dict[str, str]] = []
        for name, value in sorted(self.namespace.items()):
            if name.startswith("_") or name in _HIDDEN_NAMES:
                continue
            if isinstance(value, type(sys.modules.get("os"))):
                continue
            try:
                type_name = type(value).__name__
            except Exception:
                type_name = "?"
            try:
                preview = repr(value)
            except Exception:
                preview = "<无法显示>"
            if len(preview) > 120:
                preview = preview[:117] + "..."
            out.append({"name": name, "type": type_name, "value": preview})
        return out


_HIDDEN_NAMES = frozenset({"project", "layers"} | set(_QGIS_NAMES))


_SESSIONS: dict[str, ConsoleSession] = {}


def session_for(user_id: str) -> ConsoleSession:
    """取（必要时创建）某个用户的控制台会话。"""
    session = _SESSIONS.get(user_id)
    if session is None:
        session = ConsoleSession(user_id=user_id, namespace=_bootstrap_namespace())
        _SESSIONS[user_id] = session
    return session


def reset_session(user_id: str) -> ConsoleSession:
    """清空某个用户的命名空间。"""
    session = session_for(user_id)
    session.reset()
    return session


def sessions() -> dict[str, ConsoleSession]:
    return dict(_SESSIONS)


# --------------------------------------------------------------------- 执行


def _bootstrap_namespace() -> dict[str, Any]:
    """新建一份命名空间：预置 QGIS 名字和两个小助手。"""
    ns: dict[str, Any] = {
        "__name__": "__console__",
        "__doc__": "Browser QGIS Python 控制台",
        "__builtins__": _SAFE_BUILTINS,
    }
    try:
        import qgis.core as qc

        ns["qgis"] = qc
        for name in _QGIS_NAMES:
            obj = getattr(qc, name, None)
            if obj is not None:
                ns[name] = obj
    except Exception as exc:  # pragma: no cover - QGIS 未装时
        LOGGER.warning("控制台预置 QGIS 名字失败：%s", exc)

    try:
        ns["project"] = lambda: _project_instance()
        ns["layers"] = lambda: list(_project_instance().mapLayers().values())
    except Exception:
        pass
    return ns


def _project_instance():
    from qgis.core import QgsProject

    return QgsProject.instance()


def _compile_for_console(code: str) -> tuple[Any, Any]:
    """把代码编译成「(主体, 末行表达式)」两段。

    末行如果是表达式，就包一层 ``_console_echo`` —— 这样 ``1+1`` 会像
    REPL 一样回显 ``2``，而不是什么都不打印。
    """
    tree = ast.parse(code, mode="exec", filename="<console>")
    body = list(tree.body)
    echo_code = None
    if body and isinstance(body[-1], ast.Expr):
        last = body[-1]
        body = body[:-1]
        call = ast.Call(
            func=ast.Name(id="_console_echo", ctx=ast.Load()),
            args=[last.value],
            keywords=[],
        )
        echo_module = ast.Module(body=[ast.Expr(value=call)], type_ignores=[])
        ast.fix_missing_locations(echo_module)
        echo_code = compile(echo_module, "<console>", "exec")
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module, "<console>", "exec"), echo_code


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    dropped = len(text) - MAX_OUTPUT_CHARS
    return text[:MAX_OUTPUT_CHARS] + f"\n…（输出过长，已截断 {dropped} 字符）"


def run_in_session(session: ConsoleSession, code: str) -> ConsoleResult:
    """在会话里执行一段代码（**必须**跑在 QGIS 工作线程上）。"""
    out = io.StringIO()
    err = io.StringIO()
    captured: list[Any] = []

    def _echo(value: Any) -> None:
        # REPL 的 displayhook 遇到 None 是不打印的 —— 照它来，
        # 否则 `print("x")` 后面会多一行 "None"，看着像出了错。
        if value is None:
            return
        captured.append(value)
        try:
            out.write(repr(value) + "\n")
        except Exception:
            out.write("<无法显示该对象>\n")

    session.namespace["_console_echo"] = _echo
    log_handler = _LogCapture(err)
    log_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    started = time.perf_counter()
    try:
        try:
            body_code, echo_code = _compile_for_console(code)
        except SyntaxError:
            return ConsoleResult(
                stdout="",
                stderr="",
                result="",
                error="语法错误",
                traceback=traceback.format_exc(),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        with redirect_stdout(out), redirect_stderr(err):
            exec(body_code, session.namespace)  # noqa: S102 - 控制台就是要执行代码
            if echo_code is not None:
                exec(echo_code, session.namespace)  # noqa: S102
    except SystemExit as exc:
        return ConsoleResult(
            stdout=_truncate(out.getvalue()),
            stderr=_truncate(err.getvalue()),
            result="",
            error="代码调用了退出（sys.exit / exit）",
            traceback=f"SystemExit: {exc!r}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except BaseException as exc:  # noqa: BLE001 - 控制台要接住一切
        return ConsoleResult(
            stdout=_truncate(out.getvalue()),
            stderr=_truncate(err.getvalue()),
            result="",
            error=f"{type(exc).__name__}: {exc}",
            traceback=_truncate(traceback.format_exc()),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        root_logger.removeHandler(log_handler)
        session.namespace.pop("_console_echo", None)

    result_text = ""
    if captured:
        try:
            result_text = repr(captured[-1])
        except Exception:
            result_text = "<无法显示>"
        if len(result_text) > 4000:
            result_text = result_text[:3997] + "..."

    session.history.append(code)
    if len(session.history) > 100:
        del session.history[:-100]

    return ConsoleResult(
        stdout=_truncate(out.getvalue()),
        stderr=_truncate(err.getvalue()),
        result=result_text,
        error=None,
        traceback=None,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


async def execute(code: str, user_id: str, timeout: float = 30.0) -> ConsoleResult:
    """执行一段代码，返回结果。

    代码在 QGIS 工作线程上跑（PyQGIS 线程安全要求）。超时会返回错误，
    但**那个线程停不下来** —— 卡住的代码会一直占着 QGIS 线程。
    """
    session = session_for(user_id)
    started = time.perf_counter()
    try:
        return await asyncio.wait_for(
            run_qgis(run_in_session, session, code), timeout=max(1.0, float(timeout))
        )
    except asyncio.TimeoutError:
        return ConsoleResult(
            stdout="",
            stderr="",
            result="",
            error=(
                f"执行超时（{timeout:g} 秒）。"
                "注意：Python 没法中断一个正在跑的线程，"
                "这段代码可能还在后台占着 QGIS 线程（其它 QGIS 请求会一起卡住），"
                "必要时请重启服务。"
            ),
            traceback=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        return ConsoleResult(
            stdout="",
            stderr="",
            result="",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
