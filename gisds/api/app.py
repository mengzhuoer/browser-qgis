"""FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, qgis_env
from ..config import PROJECT_ROOT, Config
from ..core.errors import GisdsError
from ..core.executor import run_qgis
from ..core import logbuf
from ..core import ready
from ..core.storage import migrate_legacy_layout
from .deps import get_auth, get_config, get_registry, set_config
from .routes_admin import router as admin_router
from .routes_auth import router as auth_router
from .routes_ai import router as ai_router
from .routes_console import router as console_router
from .routes_datasets import router as datasets_router
from .routes_meta import router as meta_router
from .routes_panels import router as panels_router
from .routes_plugins import router as plugins_router
from .routes_plugins import make_route_registrar
from .routes_processing import router as processing_router
from .routes_crs import router as crs_router
from .routes_query import router as query_router
from .routes_raster import router as raster_router
from .routes_render import router as render_router
from .routes_upload import router as upload_router
from .routes_vector import router as vector_router

LOGGER = logging.getLogger("gisds")

WEB_DIR = PROJECT_ROOT / "web"


def create_app(config: Config | None = None) -> FastAPI:
    """构造 FastAPI 应用。

    ``config`` 为 None 时从 config.json / 环境变量读取。
    """
    if config is not None:
        set_config(config)
    cfg = get_config()

    # 瓦片缓存上限从配置读（网页端按层级出瓦片，缓存太小会一直重渲）
    from ..core import render as render_core

    render_core.configure_cache(cfg.tile_cache_size)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 日志缓冲要赶在 uvicorn 重置 root handler 之后重装一次，
        # 否则「日志消息」面板会是空的
        logbuf.install()
        ready.mark_starting()
        LOGGER.info("初始化 PyQGIS 运行时 ...")
        # 旧布局迁移（共享库直接挂在 data 下 + 独立 workspace）-> 新空间模型
        for note in migrate_legacy_layout(cfg.data_root_path, cfg.workspace_root_path):
            LOGGER.info("数据布局迁移：%s", note)

        # QgsApplication、Processing 的 registry/provider 都是 QObject，
        # 必须和后面的图层、渲染在同一条线程上，所以初始化也走 QGIS 工作线程。
        await run_qgis(qgis_env.init_qgis)
        await run_qgis(qgis_env.ensure_processing)
        info = qgis_env.runtime_info()
        LOGGER.info("QGIS %s | 数据根目录 %s", info.get("qgis_version"), cfg.data_root_path)

        # 首个管理员：没有账号时自动建一个
        auth = get_auth()
        # 内置超级账号的口令（config.super_admin_password）。跟 admin_password
        # 不是一回事：那个只在库为空时用一次，这个是**每次启动都对齐**。
        super_pwd = cfg.super_admin_password_resolved
        if auth.db.count_users() == 0:
            # 自举时如果配了内置超级账号，就直接用它当初始口令 ——
            # 别先生成一个随机的、启动日志和备忘里留下一串从没生效过的口令，
            # 下一行再改回来（那样备忘里记的是个死口令）。
            bootstrap_pwd = super_pwd or cfg.admin_password or None
            user, password = auth.ensure_admin(cfg.admin_username, bootstrap_pwd)
            # 口令备忘写在**用户库旁边**（不是数据根目录）：
            # 两者可以各自配置，写到 data_root 的话，临时库初始化会把
            # 正式库的口令备忘覆盖掉 —— 照着文件登就永远登不进去。
            from ..core.auth import password_note_path, write_password_note

            # 口令从哪来，决定它要不要落盘、要不要打进日志：
            #   * 配置里已经写了 admin_password / super_admin_password
            #     → 明文本来就在 config.json（或环境变量）里，再复制一份到磁盘、
            #       再打进日志都是净损失，而且日志往往比配置文件传得更远。
            #       只说一声建好了即可。
            #   * 没配 → 随机生成 → 这台机器上只有日志和备忘两处能找到它，
            #     多机部署时（服务在数据机、管理员在另一台机器用启动器连过去）
            #     这两处就是唯一的凭据副本，必须留。
            from_config = bool(cfg.admin_password or super_pwd)
            note_path = password_note_path(cfg.db_path_resolved)
            if from_config:
                src = "内置超级账号" if super_pwd else "admin_password"
                note = (
                    f"首次启动，已创建管理员账号\n"
                    f"    用户名：{user['username']}\n"
                    f"    口令来自配置（{src}），未写入备忘文件、未打印明文"
                )
            else:
                note_file = (
                    f"密码同时写在 {note_path}（登录后请尽快修改）" if cfg.password_note else ""
                )
                note = (
                    f"首次启动，已创建管理员账号\n"
                    f"    用户名：{user['username']}\n"
                    f"    密码  ：{password}\n"
                    f"{note_file}"
                )
            LOGGER.warning("=" * 68)
            for line in note.splitlines():
                LOGGER.warning("%s", line)
            LOGGER.warning("=" * 68)
            # 口令是配置带来的就不落盘（避免重复留明文）；随机生成的才写
            if cfg.password_note and not from_config:
                write_password_note(cfg.db_path_resolved, user["username"], password)
        elif cfg.admin_password:
            # 配置里写了口令，但它跟库里当前的口令对不上 → 配置那份已经过期。
            # 这时「照着 config 登不进去」，而账号状态和日志看上去都正常，最难查。
            from ..core.auth import verify_password

            acct = auth.db.get_user_by_name(cfg.admin_username)
            stored = (acct or {}).get("password_hash") or ""
            if acct and stored and not verify_password(cfg.admin_password, stored):
                LOGGER.warning(
                    "配置里的 admin_password 与 %s 当前的口令不一致（多半是后来改过密码）。"
                    "以数据为准，别照着配置登。",
                    cfg.admin_username,
                )

        # ---- 内置超级账号：所有用户都能用的那把万能钥匙 ----
        #
        # 语义跟 admin_password **不一样**（见 config.py 的注释）：
        # admin_password 只在库为空时用一次；内置超级账号的口令是**每次启动
        # 都对齐**的 —— 账号不存在就建、角色不是管理员就升、口令对不上就重设。
        # 所以「配置里这组口令永远能登进去」，哪怕有人中途把它改了。
        #
        # 代价要说清楚：服务监听 0.0.0.0 时，这等于把管理员身份发给整个网段。
        if super_pwd:
            from ..core.auth import password_note_path, write_password_note

            acct, changed = auth.ensure_super_admin(cfg.admin_username, super_pwd)
            LOGGER.warning(
                "内置超级账号 %s 已启用：口令钉在配置里，同网段任何人拿它登录都是管理员。",
                acct["username"],
            )
            if changed:
                LOGGER.warning(
                    "内置超级账号 %s 的口令已按配置对齐（此前不是这个口令）。",
                    acct["username"],
                )
            # 口令备忘同步更新 —— 不变式只有一条：**文件里记着的永远是
            # 当前有效口令**。备忘已经躺在那儿却记着旧口令，比没有更糟
            # （照着登一直 401，而账号和服务看上去都正常）。
            # 备忘不存在就不新建：口令明文本来就在 config.json 里，
            # 再复制一份到磁盘是净损失 —— 与自举那条路径同一个口径。
            if cfg.password_note:
                note_path = password_note_path(cfg.db_path_resolved)
                if note_path.is_file():
                    try:
                        note_text = note_path.read_text(encoding="utf-8")
                    except OSError:
                        note_text = ""
                    if super_pwd not in note_text:
                        write_password_note(cfg.db_path_resolved, acct["username"], super_pwd)
        registry = get_registry()
        LOGGER.info("已注册 %d 个数据集", len(registry.datasets()))

        # 插件：先把「怎么把插件的接口挂上去」教给管理器，再在 QGIS 线程上加载。
        # 加载放在 Processing 就绪之后 —— 插件可能要注册自己的算法 provider。
        plugin_manager = None
        if cfg.plugins_enabled:
            from ..core import plugins as plugins_core

            plugin_manager = plugins_core.get_manager(cfg.plugin_dir_path)
            plugin_manager.set_route_registrar(make_route_registrar(app))
            try:
                await run_qgis(plugin_manager.load_all)
                loaded = [r.slug for r in plugin_manager.records.values() if r.loaded]
                if loaded:
                    LOGGER.info("已加载插件：%s", "、".join(loaded))
                failed = [
                    f"{r.slug}（{r.error}）"
                    for r in plugin_manager.records.values()
                    if r.error
                ]
                if failed:
                    LOGGER.warning("插件加载有问题：%s", "；".join(failed))
            except Exception as exc:  # noqa: BLE001 - 插件坏不许拖垮服务
                LOGGER.warning("插件加载失败（服务照常启动）：%s", exc)

        # 到这里才算真正就绪：QGIS 起来了、Processing 装好了、管理员建好了、
        # 数据目录扫完了。之前的 /healthz 会在这之前就报 ok，调用方扑个空。
        ready.mark_ready()
        try:
            yield
        finally:
            ready.mark_idle()
            if plugin_manager is not None:
                try:
                    await run_qgis(plugin_manager.unload_all)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("插件卸载有问题：%s", exc)
            await run_qgis(qgis_env.shutdown_qgis)

    app = FastAPI(
        title="Browser QGIS",
        description=(
            "基于 PyQGIS 内核的通用 GIS 数据服务。"
            "同一套内核同时提供 REST API（给网页消费）与 MCP Server（给 OpenClaw 等 Agent 调用）。"
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 允许任意网页跨域取数（局域网内其它项目直接 fetch 即可）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # 启动中（QGIS 还没初始化完、管理员还没建好、目录还没扫完）时，
    # **/readyz** 老实报 503，而不是「进程活着」就报 ok ——
    # 否则调用方会在服务还没准备好时就来取数 / 建账号，扑个空。
    #
    # /healthz 保持纯存活语义（启动期也 200）：它是给外部监控做心跳的，
    # 让它 503 会被配置不当的监控误判成「服务挂了」，反而触发重启循环。
    @app.middleware("http")
    async def _readiness_probe(request: Request, call_next):
        if request.url.path == "/readyz" and ready.is_starting():
            return JSONResponse(
                status_code=503,
                content={"status": "starting", "service": "gis-data-server", "ready": False},
            )
        return await call_next(request)

    # 网页资源不许被浏览器缓存住。
    #
    # 不设 Cache-Control 的话，浏览器会按启发式规则缓存（没有 Last-Modified 时
    # 干脆直接用缓存），于是「我明明改了前端，怎么还是老样子」—— 这种问题
    # 最难查，因为服务端明明已经是新的了。
    # no-cache 不是「不缓存」，是「每次都回源校验」：配上有 ETag，没改就回 304，
    # 改了才回新内容 —— 局域网里这点开销可以忽略。
    @app.middleware("http")
    async def _no_cache_web_assets(request: Request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if path in ("/", "/login", "/demo") or path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.exception_handler(GisdsError)
    async def _gisds_error_handler(request: Request, exc: GisdsError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        LOGGER.warning("请求参数错误: %s", exc)
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_request", "message": str(exc), "detail": {}},
        )

    app.include_router(meta_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(datasets_router)
    app.include_router(upload_router)
    app.include_router(raster_router)
    app.include_router(vector_router)
    app.include_router(render_router)
    app.include_router(crs_router)
    app.include_router(processing_router)
    app.include_router(query_router)
    app.include_router(panels_router)
    app.include_router(console_router)
    app.include_router(ai_router)
    app.include_router(plugins_router)

    # ---- 内置网页（控制台 / 消费示例）
    #
    # 缓存：这几个文件**必须**每次都回源校验（no-cache + ETag → 未改就回 304，
    # 改了才回新内容）。不设的话浏览器会按启发式缓存，网页改了也不生效 ——
    # 那种「我明明改了前端，怎么还是老样子」的问题最难查。
    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

        @app.get("/login", include_in_schema=False)
        async def login_page() -> FileResponse:
            return FileResponse(str(WEB_DIR / "login.html"))

        @app.get("/demo", include_in_schema=False)
        async def demo() -> FileResponse:
            return FileResponse(str(WEB_DIR / "demo.html"))

        @app.get("/help", include_in_schema=False)
        async def help_page() -> FileResponse:
            """使用帮助（纯静态页，和控制台同一套纸面风格）。"""
            return FileResponse(str(WEB_DIR / "help.html"))

    return app


app = create_app()
