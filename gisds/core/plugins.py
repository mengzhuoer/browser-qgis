"""插件系统（服务端 Python 插件）。

QGIS 桌面版的插件是装在 ``plugins/`` 下的一个 Python 包：带一份元数据，
带 ``initGui()``，能加菜单、加载 Processing 算法。网页版没有 QWidget，
所以「加界面」改成**声明式**的：插件在服务端登记面板和菜单项，
网页去 ``/api/plugins`` 取清单，把面板画到右栏、把菜单项挂到菜单栏。

插件包长这样（装进去就是一个目录）::

    my_plugin/
        metadata.json
        __init__.py

``metadata.json``::

    {
      "name": "我的插件",
      "version": "1.0.0",
      "description": "做什么用的",
      "author": "谁写的",
      "enabled": true
    }

``__init__.py``::

    class GisdPlugin:
        def init(self, ctx):
            ctx.add_panel(id="hello", title="打个招呼",
                          endpoint="/api/plugin/my_plugin/hello")
            ctx.add_menu_item(menu="tools", label="打个招呼", endpoint="...")

        def unload(self):
            pass

插件能做的事：

* ``ctx.add_panel(...)``  往右栏加一个面板
* ``ctx.add_menu_item(...)`` 往菜单栏加一项
* ``ctx.add_route(...)``  注册自己的 REST 接口
* ``ctx.add_action(...)``  注册一个能从前端调用的动作
* ``ctx.register_processing_provider(...)``  注册 Processing 算法
  （自动出现在工具箱里，和 QGIS 自带的算法并列）
* ``ctx.log(...)``  写进服务端日志

**插件是服务端代码**，和 Python 控制台一样是任意代码执行 ——
所以只有管理员能装 / 启停，装之前请自己审一遍。
"""

from __future__ import annotations

import importlib.util
import inspect
import io
import json
import logging
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger("gisds.plugins")

#: zip 安装的护栏
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_PLUGIN_FILES = 500

#: 插件类名（按优先级找）
_PLUGIN_CLASS_NAMES = ("GisdPlugin", "QgisPlugin", "Plugin")

_STATE_FILE = ".state.json"


# --------------------------------------------------------------------- 声明


@dataclass
class PanelSpec:
    """插件往右栏加的一个面板。"""

    id: str
    title: str
    endpoint: str
    type: str = "html"  # html | table
    icon: str = ""
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "endpoint": self.endpoint,
            "type": self.type,
            "icon": self.icon,
            "order": self.order,
        }


@dataclass
class MenuSpec:
    """插件往菜单栏加的一项。"""

    menu: str
    label: str
    endpoint: str = ""
    method: str = "POST"
    params: dict[str, Any] = field(default_factory=dict)
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "menu": self.menu,
            "label": self.label,
            "endpoint": self.endpoint,
            "method": self.method,
            "params": self.params,
            "order": self.order,
        }


@dataclass
class PluginRecord:
    """一个插件的运行时状态。"""

    slug: str
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    folder: Path | None = None
    enabled: bool = True
    loaded: bool = False
    error: str = ""
    panels: list[PanelSpec] = field(default_factory=list)
    menus: list[MenuSpec] = field(default_factory=list)
    actions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    routes: list[dict[str, Any]] = field(default_factory=list)
    instance: Any = None
    provider: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "folder": str(self.folder) if self.folder else "",
            "enabled": self.enabled,
            "loaded": self.loaded,
            "error": self.error,
            "panels": [p.to_dict() for p in self.panels],
            "menus": [m.to_dict() for m in self.menus],
            "actions": sorted(self.actions.keys()),
            "routes": [r["path"] for r in self.routes],
        }


# --------------------------------------------------------------------- SDK


class PluginContext:
    """交给插件的上下文 —— 插件只能通过它往服务里加东西。"""

    def __init__(self, manager: "PluginManager", record: PluginRecord) -> None:
        self._manager = manager
        self._record = record
        #: 插件自己的目录（放数据、模板都往这儿放）
        self.plugin_dir = record.folder or Path(".")
        #: 插件自己的配置（来自 metadata.json 的 ``config`` 段）
        self.config: dict[str, Any] = dict(record.__dict__.get("plugin_config") or {})

    # -- 日志 ------------------------------------------------------------
    def log(self, message: str) -> None:
        """写一条带插件名的日志。"""
        LOGGER.info("[插件 %s] %s", self._record.slug, message)

    # -- 界面 ------------------------------------------------------------
    def add_panel(
        self,
        *,
        id: str,
        title: str,
        endpoint: str,
        type: str = "html",
        icon: str = "",
        order: int = 0,
    ) -> None:
        """往右栏加一个面板。

        ``endpoint`` 是插件自己注册的接口（用 ``ctx.add_route``），
        网页打开面板时会 GET 它。返回 JSON：``{"html": "..."}`` 直接显示，
        或 ``{"columns": [...], "rows": [[...]]}`` 渲染成表格。
        """
        if not id or not title or not endpoint:
            raise ValueError("add_panel 需要 id / title / endpoint")
        self._record.panels.append(
            PanelSpec(id=id, title=title, endpoint=endpoint, type=type, icon=icon, order=order)
        )

    def add_menu_item(
        self,
        *,
        menu: str,
        label: str,
        endpoint: str = "",
        method: str = "POST",
        params: dict[str, Any] | None = None,
        order: int = 0,
    ) -> None:
        """往菜单栏加一项。``menu`` 可以是 file/edit/view/layer/tools/plugins。"""
        if not menu or not label:
            raise ValueError("add_menu_item 需要 menu / label")
        self._record.menus.append(
            MenuSpec(
                menu=menu,
                label=label,
                endpoint=endpoint,
                method=(method or "POST").upper(),
                params=dict(params or {}),
                order=order,
            )
        )

    # -- 接口与动作 ------------------------------------------------------
    def add_route(
        self,
        path: str,
        handler: Callable[..., Any],
        methods: tuple[str, ...] = ("POST",),
    ) -> None:
        """注册一个 REST 接口。

        ``handler`` 的签名是 ``handler(request, principal)`` ——
        ``request`` 是 FastAPI 的 ``Request``，``principal`` 是当前调用者
        （免登录模式下是匿名身份）。可以是同步或异步函数。
        """
        if not path.startswith("/"):
            path = "/" + path
        self._manager.register_route(self._record, path, handler, methods)

    def add_action(self, name: str, fn: Callable[..., Any]) -> None:
        """注册一个能从网页调用的动作。

        ``fn`` 收到一个 dict（请求里的 ``params``），返回什么就原样回给前端。

        **想按调用者身份做判断的话，把 ``principal`` 也收下**：

        .. code-block:: python

            def _action(self, params, principal):
                if not principal.is_admin:
                    return {"error": "这个动作只有管理员能跑"}
                ...

        写成只收 ``params`` 的老样子也照常能用 —— 服务按签名决定传不传第二个参数。
        """
        if not name:
            raise ValueError("add_action 需要名字")
        self._record.actions[name] = fn

    # -- Processing ------------------------------------------------------
    def register_processing_provider(self, provider: Any) -> None:
        """注册一个 Processing provider（算法会自动出现在工具箱里）。"""
        from qgis.core import QgsApplication

        registry = QgsApplication.processingRegistry()
        existing = registry.providerById(provider.id())
        if existing is not None:
            registry.removeProvider(existing)
        registry.addProvider(provider)
        self._record.provider = provider
        self.log(f"注册了 Processing provider：{provider.id()}")


# --------------------------------------------------------------------- 管理器


class PluginManager:
    """插件的发现、加载、启停、安装。"""

    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.records: dict[str, PluginRecord] = {}
        self._route_registrar: Callable[..., None] | None = None

    # -- 基础设施 --------------------------------------------------------
    def set_route_registrar(self, registrar: Any) -> None:
        """告诉管理器怎么把插件的接口挂到 FastAPI 上。

        这一步由 API 层来做（核心层不该认识 FastAPI）。
        registrar 要有 ``register(path, handler, methods, slug)`` 和
        ``unregister(slug)`` 两个方法。
        """
        self._route_registrar = registrar

    def register_route(
        self,
        record: PluginRecord,
        path: str,
        handler: Callable[..., Any],
        methods: tuple[str, ...],
    ) -> None:
        record.routes.append({"path": path, "methods": list(methods)})
        if self._route_registrar is None:
            raise RuntimeError("插件接口只能在应用启动后注册")
        self._route_registrar.register(path, handler, methods, record.slug)

    @property
    def state_path(self) -> Path:
        return self.plugin_dir / _STATE_FILE

    def _load_state(self) -> dict[str, Any]:
        try:
            if self.state_path.is_file():
                return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("插件状态文件读不了，按默认处理：%s", exc)
        return {}

    def _save_state(self) -> None:
        try:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            state = {
                slug: {"enabled": rec.enabled}
                for slug, rec in self.records.items()
            }
            self.state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            LOGGER.warning("插件状态写不进去：%s", exc)

    # -- 发现 ------------------------------------------------------------
    def discover(self) -> list[PluginRecord]:
        """扫描插件目录，把每个插件的元数据读出来。

        **已加载的插件不能被重新发现冲掉**：网页每次拉清单都会调这里，
        如果每次都造一条新的记录，已加载插件的面板 / 菜单 / 实例就没了
        （``loaded`` 变 false，页面上的插件面板会凭空消失）。
        所以这里只**更新元数据**，运行时状态留着。
        """
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        found: list[PluginRecord] = []
        for folder in sorted(self.plugin_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith((".", "_")):
                continue
            meta_path = folder / "metadata.json"
            if not meta_path.is_file():
                continue
            record = self._read_metadata(folder, meta_path)
            if record is None:
                continue
            if slug_state := state.get(record.slug):
                record.enabled = bool(slug_state.get("enabled", record.enabled))
            existing = self.records.get(record.slug)
            if existing is not None and existing.loaded:
                # 只换元数据，运行时状态（面板/菜单/动作/实例/provider）留着
                existing.name = record.name
                existing.version = record.version
                existing.description = record.description
                existing.author = record.author
                existing.folder = record.folder
                existing.__dict__["plugin_config"] = record.__dict__.get("plugin_config", {})
                record = existing
            elif existing is not None and existing.error and not record.error:
                # 之前加载失败过的插件：**把错误留住**。
                # 原来只在 ``existing.loaded`` 时才留运行时状态，坏插件
                # （loaded=False）每次拉清单都被一份新 record 盖掉 →
                # 管理员再也看不到「当初为什么加载失败」，只剩一个空错误。
                record.error = existing.error
                self.records[record.slug] = record
            else:
                self.records[record.slug] = record
            found.append(record)
        return found

    def _read_metadata(self, folder: Path, meta_path: Path) -> PluginRecord | None:
        """读一个插件的 metadata.json。读不了就返回一条带错误的记录。"""
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return PluginRecord(
                slug=folder.name,
                name=folder.name,
                folder=folder,
                error=f"metadata.json 解析失败：{exc}",
            )
        if not isinstance(raw, dict):
            return PluginRecord(
                slug=folder.name,
                name=folder.name,
                folder=folder,
                error="metadata.json 不是一个 JSON 对象",
            )
        slug = str(raw.get("slug") or folder.name).strip() or folder.name
        if slug != folder.name:
            return PluginRecord(
                slug=folder.name,
                name=folder.name,
                folder=folder,
                error=f"metadata.json 里的 slug（{slug}）和目录名（{folder.name}）不一致",
            )
        record = PluginRecord(
            slug=slug,
            name=str(raw.get("name") or slug),
            version=str(raw.get("version") or ""),
            description=str(raw.get("description") or ""),
            author=str(raw.get("author") or ""),
            folder=folder,
            enabled=bool(raw.get("enabled", True)),
        )
        record.__dict__["plugin_config"] = raw.get("config") or {}
        return record

    # -- 加载 ------------------------------------------------------------
    def load_all(self) -> list[PluginRecord]:
        """加载所有启用的插件（**必须**在 QGIS 工作线程上跑）。"""
        for record in self.discover():
            if record.enabled and not record.loaded:
                self.load(record)
        return [r for r in self.records.values() if r.loaded]

    def load(self, record: PluginRecord) -> PluginRecord:
        """加载一个插件。失败只标记错误，不影响其它插件。"""
        if record.loaded:
            return record
        record.error = ""
        record.panels.clear()
        record.menus.clear()
        record.actions.clear()
        record.routes.clear()
        record.provider = None
        folder = record.folder
        if folder is None or not (folder / "__init__.py").is_file():
            record.error = "插件目录里没有 __init__.py"
            return record
        try:
            instance = self._import_plugin(record, folder)
            ctx = PluginContext(self, record)
            instance.init(ctx)
            record.instance = instance
            record.loaded = True
            LOGGER.info("插件已加载：%s (%s)", record.name, record.slug)
        except Exception as exc:  # noqa: BLE001 - 插件坏不许拖垮服务
            record.loaded = False
            record.error = f"{type(exc).__name__}: {exc}"
            LOGGER.warning("插件 %s 加载失败：%s", record.slug, record.error)
            LOGGER.debug("插件 %s 的完整回溯", record.slug, exc_info=True)
            # init() 挂了一半的时候炸了，那些接口得摘掉 ——
            # 否则插件加载失败，它的接口却还能被调。
            if self._route_registrar is not None:
                try:
                    self._route_registrar.unregister(record.slug)
                except Exception as unreg_exc:  # noqa: BLE001
                    LOGGER.warning("摘掉插件 %s 的接口失败：%s", record.slug, unreg_exc)
        return record

    def _import_plugin(self, record: PluginRecord, folder: Path) -> Any:
        """按 QGIS 插件的方式把插件目录导进来。"""
        init_file = folder / "__init__.py"
        mod_name = f"gisds_plugin_{record.slug}"
        spec = importlib.util.spec_from_file_location(
            mod_name, init_file, submodule_search_locations=[str(folder)]
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"没法导入 {init_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        # 插件内部按包名 import 自己的子模块
        sys.modules[record.slug] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(mod_name, None)
            sys.modules.pop(record.slug, None)
            raise

        factory = getattr(module, "classFactory", None)
        if callable(factory):
            instance = factory()
            if instance is not None:
                return instance
        for class_name in _PLUGIN_CLASS_NAMES:
            cls = getattr(module, class_name, None)
            if cls is not None and inspect.isclass(cls):
                return cls()
        raise AttributeError(
            f"插件里找不到可用的类：需要 class GisdPlugin（或 classFactory 函数）"
        )

    def unload(self, record: PluginRecord) -> None:
        """卸载一个插件（跑它的 ``unload()``，撤掉 provider 和接口）。"""
        if not record.loaded:
            record.panels.clear()
            record.menus.clear()
            record.actions.clear()
            record.routes.clear()
            return
        try:
            if record.instance is not None and hasattr(record.instance, "unload"):
                record.instance.unload()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("插件 %s 的 unload() 出错：%s", record.slug, exc)
        if record.provider is not None:
            try:
                from qgis.core import QgsApplication

                QgsApplication.processingRegistry().removeProvider(record.provider)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("撤掉 provider %s 失败：%s", record.slug, exc)
        record.provider = None
        record.instance = None
        record.loaded = False
        record.panels.clear()
        record.menus.clear()
        record.actions.clear()
        record.routes.clear()
        # 把这个插件在 sys.modules 里的痕迹清掉 —— 否则改了子模块的代码
        # 再点「重载」，旧模块还在缓存里，插件拿到的还是旧值
        # （``from .helper import X`` 直接命中缓存，根本不重新读文件）。
        self._purge_modules(record)
        # 插件自己注册的接口也要摘掉 —— 否则插件停了，它的接口还能被调，
        # 里面引用的插件对象就成了悬空的（插件已经 unload 了）。
        if self._route_registrar is not None:
            try:
                removed = self._route_registrar.unregister(record.slug)
                if removed:
                    LOGGER.info("插件 %s 停用，摘掉 %d 个接口", record.slug, removed)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("摘掉插件 %s 的接口失败：%s", record.slug, exc)

    def _purge_modules(self, record: PluginRecord) -> None:
        """把这个插件（及其子模块）从 ``sys.modules`` 里摘掉。

        只摘**属于这个插件**的模块：``sys.modules[record.slug]`` 那个别名是
        插件自己按 slug 塞进去的（见 ``_import_plugin``），但 slug 是用户取的，
        万一叫 ``os`` 就会把真的标准库模块顶掉 —— 那种情况下宁可留着，
        也不能把 ``os`` 从 ``sys.modules`` 里删掉。
        """
        prefix = f"gisds_plugin_{record.slug}"
        for name in [n for n in list(sys.modules) if n == prefix or n.startswith(prefix + ".")]:
            sys.modules.pop(name, None)
        alias = sys.modules.get(record.slug)
        if alias is not None and getattr(alias, "__file__", "") :
            try:
                inside = Path(str(alias.__file__)).resolve().is_relative_to(
                    Path(record.folder).resolve()
                )
            except (AttributeError, OSError, ValueError):
                inside = False
            if inside:
                sys.modules.pop(record.slug, None)

    def reload(self, record: PluginRecord) -> PluginRecord:
        """重载一个插件（改完代码不用重启服务）。"""
        self.unload(record)
        return self.load(record)

    def unload_all(self) -> None:
        """卸载所有插件（服务退出时调用）。"""
        for record in list(self.records.values()):
            try:
                self.unload(record)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("卸载插件 %s 失败：%s", record.slug, exc)

    # -- 启停 ------------------------------------------------------------
    def set_enabled(self, slug: str, enabled: bool) -> PluginRecord:
        record = self._require(slug)
        if enabled:
            record.enabled = True
            self.load(record)
        else:
            record.enabled = False
            self.unload(record)
        self._save_state()
        return record

    def uninstall(self, slug: str) -> None:
        record = self._require(slug)
        self.unload(record)
        folder = record.folder
        self.records.pop(slug, None)
        if folder and folder.is_dir() and folder.parent.resolve() == self.plugin_dir.resolve():
            shutil.rmtree(folder, ignore_errors=True)
        self._save_state()

    def _require(self, slug: str) -> PluginRecord:
        record = self.records.get(slug)
        if record is None:
            # 可能还没扫描过
            self.discover()
            record = self.records.get(slug)
        if record is None:
            raise KeyError(slug)
        return record

    # -- 调用 ------------------------------------------------------------
    def call_action(self, slug: str, action: str, params: dict[str, Any]) -> Any:
        record = self._require(slug)
        fn = record.actions.get(action)
        if fn is None:
            raise KeyError(action)
        return fn(params or {})

    # -- 清单 ------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        """插件清单（给网页渲染面板和菜单用）。"""
        return [rec.to_dict() for rec in sorted(self.records.values(), key=lambda r: r.slug)]

    def panels(self) -> list[dict[str, Any]]:
        """所有**已加载**插件贡献的面板。"""
        out: list[dict[str, Any]] = []
        for rec in self.records.values():
            if not rec.loaded:
                continue
            for panel in rec.panels:
                item = panel.to_dict()
                item["plugin"] = rec.slug
                item["plugin_name"] = rec.name
                out.append(item)
        out.sort(key=lambda p: (p.get("order", 0), p.get("title", "")))
        return out

    def menus(self) -> list[dict[str, Any]]:
        """所有**已加载**插件贡献的菜单项。"""
        out: list[dict[str, Any]] = []
        for rec in self.records.values():
            if not rec.loaded:
                continue
            for menu in rec.menus:
                item = menu.to_dict()
                item["plugin"] = rec.slug
                item["plugin_name"] = rec.name
                out.append(item)
        out.sort(key=lambda m: (m.get("order", 0), m.get("label", "")))
        return out

    # -- 安装 ------------------------------------------------------------
    def install_zip(self, data: bytes, filename: str = "") -> PluginRecord:
        """从 zip 装一个插件。

        zip 是**上传上来的任意文件**，所以这里的每一步都得当成不可信输入处理：
        路径穿越（``../``）、绝对路径、符号链接、超大文件、文件数超限、
        没有 metadata.json —— 一样都不放过。
        """
        if not data:
            raise ValueError("上传的文件是空的")
        if len(data) > MAX_ZIP_BYTES:
            raise ValueError(
                f"插件包太大（{len(data) / 1024 / 1024:.1f} MB），上限 "
                f"{MAX_ZIP_BYTES // 1024 // 1024} MB"
            )
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"这不是一个合法的 zip：{exc}") from exc

        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise ValueError(f"压缩包里的条目太多（{len(infos)} 个）")
            total = sum(i.file_size for i in infos)
            if total > MAX_ZIP_UNCOMPRESSED:
                raise ValueError("压缩包解压后太大")

            # 1) 找出插件根目录：要么就在压缩包根上，要么是唯一的一层目录
            names = [i.filename for i in infos if not i.is_dir()]
            root = self._detect_root(names)

            # 2) 逐条检查路径合法性
            for info in infos:
                self._check_entry(info)

            meta_name = f"{root}metadata.json" if root else "metadata.json"
            if meta_name not in names:
                raise ValueError("压缩包里没有 metadata.json（插件根目录找错了？）")

            meta_raw = archive.read(meta_name)
            try:
                meta = json.loads(meta_raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError(f"metadata.json 解析不了：{exc}") from exc
            if not isinstance(meta, dict):
                raise ValueError("metadata.json 不是一个 JSON 对象")

            slug = str(meta.get("slug") or (root.rstrip("/") if root else "")).strip()
            if not slug:
                raise ValueError("压缩包根目录下就是插件的话，metadata.json 里要写 slug")
            if not _valid_slug(slug):
                raise ValueError(f"插件标识不合法：{slug!r}（只允许字母数字 _ - .）")
            if root:
                slug = root.rstrip("/")

            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            target = (self.plugin_dir / slug).resolve()
            if target.parent != self.plugin_dir.resolve():
                raise ValueError("插件目录名不合法")

            # 3) 解压（自己写文件，不交给 extractall —— 它不防路径穿越）
            if target.exists():
                # **重装同名插件：先把旧的卸掉。**
                # 原来直接 rmtree 覆盖目录，旧 record 还在（loaded=True、旧路由
                # 还挂在 FastAPI 上），而 FastAPI 按注册顺序匹配 → 旧路由永远赢。
                # 结果是「接口是旧版、动作是新版」，两份代码同时活着。
                # 覆盖之前得让旧的先退场（unload 会顺手摘掉它的接口）。
                old = self.records.get(slug)
                if old is not None:
                    try:
                        self.unload(old)
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning("重装插件 %s 时卸载旧版本出错：%s", slug, exc)
                    self.records.pop(slug, None)
                shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            written = 0
            try:
                for info in infos:
                    if info.is_dir():
                        continue
                    rel = info.filename
                    if root:
                        if not rel.startswith(root):
                            continue
                        rel = rel[len(root):]
                    if not rel:
                        continue
                    written += 1
                    if written > MAX_PLUGIN_FILES:
                        raise ValueError(f"插件文件太多（上限 {MAX_PLUGIN_FILES} 个）")
                    dest = (target / rel).resolve()
                    if not str(dest).startswith(str(target) + "\\") and dest.parent != target:
                        raise ValueError(f"压缩包里有越界的路径：{info.filename}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise

        record = self._read_metadata(target, target / "metadata.json")
        if record is None:
            raise ValueError("装完了但 metadata 读不出来")
        if record.error:
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError(record.error)
        self.records[record.slug] = record
        self._save_state()
        return record

    @staticmethod
    def _detect_root(names: list[str]) -> str:
        """插件根目录在压缩包里的相对前缀（``""`` 表示就在根上）。"""
        if "metadata.json" in names:
            return ""
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(tops) == 1:
            top = tops.pop()
            if f"{top}/metadata.json" in names:
                return top + "/"
        return ""

    @staticmethod
    def _check_entry(info: zipfile.ZipInfo) -> None:
        """挡住路径穿越、绝对路径、符号链接。"""
        name = info.filename
        if not name:
            return
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized.split("/")[0][1:2]:
            raise ValueError(f"压缩包里有绝对路径：{name}")
        if ".." in normalized.split("/"):
            raise ValueError(f"压缩包里有路径穿越：{name}")
        # 符号链接（unix 权限位 0o120000）
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"压缩包里不接受符号链接：{name}")


def _valid_slug(slug: str) -> bool:
    if not slug or len(slug) > 64:
        return False
    return all(ch.isalnum() or ch in "_-." for ch in slug) and not slug.startswith(".")


# --------------------------------------------------------------------- 单例

_MANAGER: PluginManager | None = None


def get_manager(plugin_dir: Path | None = None) -> PluginManager:
    """取插件管理器。

    目录变了就换一个 —— 测试里每个临时服务都有自己的插件目录，
    缓存住旧的那个会让测试互相串台。

    ``plugin_dir`` 省略时用**上一次建的那个**的目录（不会再去读配置）——
    核心层不反向依赖 API 层，配置由 API 层传进来。
    """
    global _MANAGER
    if plugin_dir is None:
        if _MANAGER is None:
            raise RuntimeError("插件管理器还没初始化（API 层要先给一个插件目录）")
        return _MANAGER
    plugin_dir = Path(plugin_dir)
    if _MANAGER is None or _MANAGER.plugin_dir != plugin_dir:
        _MANAGER = PluginManager(plugin_dir)
    return _MANAGER


def set_manager(manager: PluginManager) -> None:
    global _MANAGER
    _MANAGER = manager
