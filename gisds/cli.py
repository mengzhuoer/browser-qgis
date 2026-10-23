"""命令行入口。

::

    python -m gisds serve            # 启动 REST 服务
    python -m gisds mcp              # 以 stdio 方式启动 MCP Server（给 OpenClaw 用）
    python -m gisds mcp --transport streamable-http
    python -m gisds scan             # 列出已注册的数据集
    python -m gisds doctor           # 检查运行环境
    python -m gisds demo-data        # 把示例数据复制到数据根目录
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from .config import PROJECT_ROOT, load_config


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# --------------------------------------------------------------------- 子命令


def cmd_serve(args) -> int:
    import uvicorn

    from .api.app import create_app
    from .config import load_config

    config = load_config(args.config)
    _setup_logging(config.log_level)

    app = create_app(config)
    print("=" * 72)
    print(f"  Browser QGIS  v{app.version}")
    print(f"  数据根目录 : {config.data_root_path}")
    print(f"  本机访问   : http://127.0.0.1:{config.port}")
    print(f"  局域网访问 : http://<本机IP>:{config.port}")
    print(f"  接口文档   : http://127.0.0.1:{config.port}/docs")
    print(f"  网页控制台 : http://127.0.0.1:{config.port}/")
    print(f"  消费示例页 : http://127.0.0.1:{config.port}/demo")
    print("=" * 72)

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level,
        access_log=True,
    )
    # PyQGIS 在 Windows 上退出时可能触发 0xC0000005，这里直接结束进程
    sys.stdout.flush()
    sys.stderr.flush()
    import os

    os._exit(0)


def cmd_mcp(args) -> int:
    from .mcp.server import serve_mcp

    config = load_config(args.config)
    _setup_logging("warning" if args.transport == "stdio" else config.log_level)
    return serve_mcp(
        transport=args.transport,
        host=args.host,
        port=args.port,
        data_root=config.data_root_path,
    )


def cmd_scan(args) -> int:
    from . import qgis_env
    from .core.executor import run_qgis_sync
    from .core.registry import Registry

    run_qgis_sync(qgis_env.init_qgis)
    config = load_config(args.config)
    registry = Registry(config.data_root_path)
    datasets = run_qgis_sync(registry.scan)

    if args.json:
        print(json.dumps([d.to_dict(detailed=True) for d in datasets], ensure_ascii=False, indent=2))
    else:
        print(f"数据根目录：{registry.data_root}")
        print(f"数据集 {len(datasets)} 个")
        for dataset in datasets:
            print(f"\n  [{dataset.id}] {dataset.name}")
            if dataset.description:
                print(f"    {dataset.description}")
            for layer in dataset.layers.values():
                extra = f"  年份={layer.year}" if layer.year else ""
                print(f"    - 栅格 {layer.id:<16} {layer.kind:<12} {layer.width}x{layer.height} {layer.crs}{extra}")
            for vector in dataset.vectors.values():
                print(f"    - 矢量 {vector.id:<16} {vector.geometry_type:<12} {vector.feature_count} 个要素")
        for err in registry.scan_errors:
            print(f"  ! 扫描告警：{err}")

    sys.stdout.flush()
    import os

    os._exit(0)


def cmd_basemap(args) -> int:
    """下载并安装内置离线底图（Natural Earth 世界矢量数据）。"""
    from .core import basemap

    config = load_config(args.config)
    target = config.shared_root_path / "basemap"
    print("== 安装内置离线底图 ==")
    print(f"  数据来源 : Natural Earth（naturalearthdata.com，公共领域）")
    print(f"  安装位置 : {target}")
    try:
        result = basemap.install_basemap(target, force=args.force, log=print)
    except Exception as exc:
        print(f"  [错误] 安装失败：{exc}", file=sys.stderr)
        return 1
    if result.get("installed"):
        print(f"  图层     : {', '.join(result.get('layers', []))}")
        print(f"  体积     : {result.get('size_bytes', 0)/1024/1024:.1f} MB")
    print("\n完成。重启服务后，网页底图会自动启用内置底图。")
    sys.stdout.flush()
    import os

    os._exit(0)


def cmd_doctor(args) -> int:
    from . import qgis_env

    config = load_config(args.config)
    info = qgis_env.runtime_info()
    print("== Browser QGIS 运行环境检查 ==")
    print(f"  项目根目录 : {PROJECT_ROOT}")
    print(f"  数据根目录 : {config.data_root_path}  存在={config.data_root_path.is_dir()}")
    print(f"  QGIS 前缀  : {info.get('prefix_path')}")
    print(f"  QGIS 版本  : {info.get('qgis_version')}")
    print(f"  Python     : {info.get('python')}")
    print(f"  Processing : {info.get('processing')}  {info.get('processing_error') or ''}")

    try:
        import fastapi
        import uvicorn

        print(f"  FastAPI    : {fastapi.__version__}")
        print(f"  uvicorn    : {uvicorn.__version__}")
    except Exception as exc:
        print(f"  [错误] Web 依赖不可用：{exc}")
        return 1

    try:
        import mcp

        print("  MCP SDK    : 可用")
    except Exception as exc:
        print(f"  [错误] MCP SDK 不可用：{exc}")
        return 1

    from .core.executor import run_qgis_sync

    run_qgis_sync(qgis_env.init_qgis)
    from .core import processing as proc

    if run_qgis_sync(proc.ensure_ready):
        print(f"  PyQGIS     : 初始化成功")
        print(f"  Processing : 就绪，共 {run_qgis_sync(proc.algorithm_count)} 个算法")
    else:
        print(f"  PyQGIS     : 初始化成功")
        print(f"  Processing : 不可用 —— {qgis_env.runtime_info().get('processing_error')}")
    sys.stdout.flush()
    import os

    os._exit(0)


def cmd_set_password(args) -> int:
    """重置某个账号的密码。"""
    from .core.auth import AuthService, write_password_note
    from .core.db import Database, ROLE_ADMIN

    config = load_config(args.config)
    auth = AuthService(Database(config.db_path_resolved))
    user = auth.db.get_user_by_name(args.username)
    if not user:
        print(f"没有这个用户：{args.username}", file=sys.stderr)
        return 1
    password = args.password or os.urandom(9).hex()
    auth.set_password(user["id"], password)
    # 口令备忘要跟着更新 —— 判断依据只有一条：**当前有效口令还有没有别的明文副本**。
    #   * 刚改完密：config.json / 环境变量里那份 admin_password 已经过期作废 →
    #     备忘是当前口令唯一的明文副本 → 必须写（否则「忘了密码」彻底没辙）；
    #   * 自举那一刻：配置里那份还新鲜 → 有副本 → 那条路径不写（见 api/app.py）。
    # 写进去的永远是**当前有效**的口令，不会留下失效的旧口令让人照着登一直 401。
    # password_note=false 时一概不落盘。
    if user.get("role") == ROLE_ADMIN and config.password_note:
        write_password_note(config.db_path_resolved, user["username"], password)
    print(f"已重置 {user['username']} 的密码：{password}")
    sys.stdout.flush()
    import os as _os

    _os._exit(0)

def cmd_demo_data(args) -> int:
    config = load_config(args.config)
    source = PROJECT_ROOT / "tests" / "sample_data"
    if not source.is_dir():
        print(f"找不到示例数据：{source}", file=sys.stderr)
        return 1
    target_root = config.data_root_path
    target_root.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = target_root / item.name
        if target.exists():
            print(f"跳过（已存在）：{target}")
            continue
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        print(f"已复制：{target}")
    print(f"\n示例数据已就位，扫描数据根目录即可看到：{target_root}")
    return 0


# --------------------------------------------------------------------- 参数


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gisds", description="Browser QGIS")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 config.json）")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动 REST 服务")
    serve.set_defaults(func=cmd_serve)

    mcp = sub.add_parser("mcp", help="启动 MCP Server")
    mcp.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8849)
    mcp.set_defaults(func=cmd_mcp)

    scan = sub.add_parser("scan", help="列出已注册的数据集")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan)

    doctor = sub.add_parser("doctor", help="检查运行环境")
    doctor.set_defaults(func=cmd_doctor)

    demo = sub.add_parser("demo-data", help="把示例数据复制到数据根目录")
    demo.set_defaults(func=cmd_demo_data)

    base = sub.add_parser("basemap", help="下载安装内置离线底图（Natural Earth）")
    base.add_argument("--force", action="store_true", help="已存在时重新下载")
    base.set_defaults(func=cmd_basemap)

    pwd = sub.add_parser("set-password", help="重置某个账号的密码")
    pwd.add_argument("username", help="用户名")
    pwd.add_argument("password", nargs="?", default=None, help="新密码（不给就随机生成）")
    pwd.set_defaults(func=cmd_set_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
