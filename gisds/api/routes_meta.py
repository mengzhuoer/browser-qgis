"""元信息与健康检查接口（公开，不含任何用户私有数据）。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__, qgis_env
from ..core import processing as proc
from ..core import ready
from ..core.auth import Principal
from ..core.executor import run_qgis
from .deps import get_config, get_registry

router = APIRouter(tags=["meta"])

# 公开接口只看共享空间，不泄漏任何用户私有数据
_ANONYMOUS = Principal(
    user_id="anonymous", username="anonymous", role="user", shared_only=True
)


@router.get("/healthz")
async def healthz() -> dict:
    """**存活**探针：进程在就 200，不等就绪。

    给外部监控做心跳用 —— 它问的是「服务还活着吗」，不是「能干活了吗」。
    启动期间这里也是 200（否则配置不当的监控会误判服务挂了、触发重启循环）；
    要问「能干活了吗」用 ``/readyz``。

    启动期**不碰注册中心**：``registry.datasets()`` 会**触发**一次数据目录扫描
    并阻塞在 QGIS 工作线程上 —— 存活探针不该有这种副作用，也不该拖慢。
    """
    if ready.is_starting():
        return {
            "status": "ok",
            "service": "gis-data-server",
            "version": __version__,
            "ready": False,
        }
    registry = get_registry()
    return {
        "status": "ok",
        "service": "gis-data-server",
        "version": __version__,
        "ready": ready.is_ready(),
        # **只数，不扫描**：``registry.datasets()`` 会触发一次数据目录扫描，
        # 而扫描要排到那条唯一的 QGIS 工作线程上。那条线程被一段卡死的控制台
        # 代码占着时，探针跟着一起卡住 —— 外部监控会误判服务挂了。
        # （真出过这事：控制台超时之后 /healthz 8 秒无响应。）
        "datasets": registry.dataset_count(_ANONYMOUS),
        "qgis": qgis_env.runtime_info(),
    }


@router.get("/readyz")
async def readyz() -> dict:
    """**就绪**探针：没准备好就 503（中间件拦截），就绪后 200。

    服务启动要等 PyQGIS 初始化、Processing 装载、首个管理员建号、数据目录扫描
    全部走完 —— 这期间它还不该被当成「能干活了」。
    """
    return await healthz()


@router.get("/api/meta")
async def meta() -> dict:
    config = get_config()
    registry = get_registry()
    datasets = registry.datasets(_ANONYMOUS)
    shared = [d for d in datasets if d.source == "shared"]
    return {
        "service": "gis-data-server",
        "version": __version__,
        "description": (
            "把 QGIS 内核搬到网页上的通用 GIS 工作台。"
            "浏览器上传自己的数据即可渲染、叠加、查询；"
            "同一份内核同时提供 REST API 与 MCP Server。"
        ),
        "runtime": qgis_env.runtime_info(),
        "config": {
            "data_root": str(config.data_root_path),
            "shared_root": str(config.shared_root_path),
            "users_root": str(config.users_root_path),
            "host": config.host,
            "port": config.port,
            "cors_origins": config.cors_origins,
            "require_login": config.require_login,
            "allow_registration": config.allow_registration,
            "max_upload_mb": config.max_upload_mb,
        },
        "counts": {
            "datasets": len(datasets),
            "shared_datasets": len(shared),
            "rasters": len(registry.all_rasters(_ANONYMOUS)),
            "vectors": len(registry.all_vectors(_ANONYMOUS)),
            "processing_algorithms": await run_qgis(proc.algorithm_count),
        },
        "capabilities": {
            "raster": ["point_query", "bbox_stats", "polygon_stats", "histogram", "area", "render_png", "xyz_tiles"],
            "vector": ["features", "stats", "point_hit", "render_png"],
            "datasets": ["list", "detail", "overview", "rescan"],
            "upload": ["upload", "list", "delete", "download"],
            "processing": ["list_algorithms", "describe", "run", "job_status", "add_to_map"],
            "auth": ["register", "login", "logout", "me", "admin"],
            "integration": ["rest", "mcp"],
        },
        "scanned_at": registry.scanned_at,
        "scan_errors": registry.scan_errors,
    }
