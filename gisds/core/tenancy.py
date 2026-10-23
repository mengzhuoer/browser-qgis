"""数据空间（scope）模型。

本服务把数据按「空间」隔离：

- ``shared``        共享空间：所有登录用户都能读，也能写
- ``user:<用户ID>`` 私有空间：只有该用户本人能读写

磁盘布局::

    <data_root>/
        gisds.db            用户库（SQLite）
        shared/             共享空间
            example/        一个数据集 = 一个目录
        users/
            <用户ID>/        该用户的私有空间
                my_data/     一个数据集 = 一个目录
"""

from __future__ import annotations

from pathlib import Path

SHARED = "shared"
USER_PREFIX = "user:"


def user_scope(user_id: str) -> str:
    """用户私有空间标识。"""
    return f"{USER_PREFIX}{user_id}"


def is_shared(scope: str | None) -> bool:
    return scope == SHARED


def is_user_scope(scope: str | None) -> bool:
    return bool(scope) and scope.startswith(USER_PREFIX)


def scope_user_id(scope: str | None) -> str | None:
    """从空间标识里取出用户 ID。"""
    if not scope or not scope.startswith(USER_PREFIX):
        return None
    return scope[len(USER_PREFIX):] or None


def scope_label(scope: str | None) -> str:
    """空间的中文名。"""
    if is_shared(scope):
        return "共享空间"
    if is_user_scope(scope):
        return "个人空间"
    return scope or "—"


def shared_root(data_root: str | Path) -> Path:
    return Path(data_root).expanduser().resolve() / "shared"


def users_root(data_root: str | Path) -> Path:
    return Path(data_root).expanduser().resolve() / "users"


def user_dir(data_root: str | Path, user_id: str) -> Path:
    return users_root(data_root) / user_id


def ensure_user_dir(data_root: str | Path, user_id: str) -> Path:
    path = user_dir(data_root, user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_layout(data_root: str | Path, workspace_root: str | Path | None = None) -> list[str]:
    """把旧的两根目录布局（共享库 + 上传区）并到新的共享空间。

    旧布局::

        <data_root>/example/          共享库数据集
        <workspace_root>/upload_x/    上传区的数据集

    新布局::

        <data_root>/shared/example/
        <data_root>/shared/upload_x/

    返回搬迁说明（每条一句）。已经是新布局时什么都不做。
    """
    import shutil

    root = Path(data_root).expanduser().resolve()
    shared = shared_root(root)
    notes: list[str] = []

    if shared.is_dir():
        return notes

    shared.mkdir(parents=True, exist_ok=True)

    # 1) 旧共享库里的数据集
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in ("shared", "users"):
            continue
        dest = shared / child.name
        if dest.exists():
            continue
        shutil.move(str(child), str(dest))
        notes.append(f"共享库 {child.name} → shared/{child.name}")

    # 2) 旧上传区
    if workspace_root:
        ws = Path(workspace_root).expanduser().resolve()
        if ws.is_dir():
            for child in sorted(ws.iterdir()):
                if not child.is_dir() or child.name.startswith((".", "_")):
                    continue
                dest = shared / child.name
                if dest.exists():
                    notes.append(f"上传区 {child.name} 已在共享空间中，跳过")
                    continue
                shutil.move(str(child), str(dest))
                notes.append(f"上传区 {child.name} → shared/{child.name}")

    return notes
