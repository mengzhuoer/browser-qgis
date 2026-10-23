"""管理员接口：用户管理、空间概览、审计日志。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.auth import AuthError, Principal
from ..core.db import ROLE_ADMIN
from ..core.tenancy import SHARED, scope_label, user_scope
from .deps import current_principal, get_auth, get_config, get_registry

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field("user", pattern="^(user|admin)$")
    display_name: str = Field("", max_length=64)


class UserPatchIn(BaseModel):
    password: str | None = Field(None, min_length=6, max_length=128)
    role: str | None = Field(None, pattern="^(user|admin)$")
    status: str | None = Field(None, pattern="^(active|disabled)$")
    display_name: str | None = Field(None, max_length=64)


def _payload(user: dict, extra: dict | None = None) -> dict:
    data = {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": user["role"],
        "status": user["status"],
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
        "scope": user_scope(user["id"]),
        "scope_label": "个人空间",
    }
    if extra:
        data.update(extra)
    return data


@router.get("/users", summary="全部账号")
async def list_users(principal: Principal = Depends(current_principal)) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    auth = get_auth()
    users = [_payload(u) for u in auth.list_users()]
    return {"count": len(users), "users": users}


@router.post("/users", status_code=201, summary="新建账号")
async def create_user(
    payload: UserIn, principal: Principal = Depends(current_principal)
) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    auth = get_auth()
    try:
        user = auth.register(
            payload.username,
            payload.password,
            role=payload.role,
            display_name=payload.display_name,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 顺手把该用户的私有空间建出来
    from ..core.tenancy import ensure_user_dir

    config = get_config()
    ensure_user_dir(config.data_root_path, user["id"])
    return _payload(user)


@router.patch("/users/{user_id}", summary="改账号")
async def patch_user(
    user_id: str, payload: UserPatchIn, principal: Principal = Depends(current_principal)
) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    auth = get_auth()
    try:
        if payload.password:
            auth.set_password(user_id, payload.password)
            # 口令备忘要跟着更新 —— 判断依据只有一条：
            # **当前有效口令是否还有别的明文副本**。
            #   * 自举那一刻：config / env 里那个就是副本 → 不写；
            #   * 改密这一刻：config 里那份已经过期 → 备忘是当前口令唯一的
            #     明文副本 → 必须写（否则「忘了密码」就彻底没辙了）。
            # 文件里因此永远记着**当前有效**的口令，不会留下失效的旧密码
            # 让人照着登一直 401。password_note=false 时一概不落盘。
            target = auth.db.get_user(user_id)
            if target and target.get("role") == ROLE_ADMIN:
                config = get_config()
                if config.password_note:
                    from ..core.auth import write_password_note

                    write_password_note(
                        config.db_path_resolved, target["username"], payload.password
                    )
        if payload.role:
            auth.set_role(user_id, payload.role)
        if payload.status:
            auth.set_status(user_id, payload.status)
        if payload.display_name is not None:
            auth.db.update_user(user_id, display_name=payload.display_name)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = auth.db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _payload(user)


@router.delete("/users/{user_id}", summary="删除账号")
async def delete_user(
    user_id: str, principal: Principal = Depends(current_principal)
) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    if user_id == principal.user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    auth = get_auth()
    if not auth.delete_user(user_id):
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"deleted": user_id}


@router.get("/spaces", summary="各空间占用")
async def spaces(principal: Principal = Depends(current_principal)) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    registry = get_registry()
    out = []
    for dataset in registry.datasets():
        out.append(
            {
                "scope": dataset.scope,
                "scope_label": scope_label(dataset.scope),
                "dataset_id": dataset.id,
                "dataset_name": dataset.name,
                "path": dataset.path,
                "raster_count": len(dataset.layers),
                "vector_count": len(dataset.vectors),
            }
        )
    return {"count": len(out), "spaces": out}


@router.get("/audit", summary="审计日志")
async def audit(
    limit: int = 100, principal: Principal = Depends(current_principal)
) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rows = get_auth().db.audit(limit=limit)
    return {"count": len(rows), "entries": rows}


@router.get("/config", summary="服务配置")
async def service_config(principal: Principal = Depends(current_principal)) -> dict:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    config = get_config()
    super_pwd = config.super_admin_password_resolved
    return {
        "require_login": config.require_login,
        "allow_registration": config.allow_registration,
        "admin_username": config.admin_username,
        # 内置超级账号开没开 —— 开着就在界面上把风险讲清楚
        # （口令本身不往外发：知道的人本来就该是管理员）
        "super_admin_enabled": bool(super_pwd),
        "super_admin_username": config.admin_username if super_pwd else "",
        # Python 控制台谁能用：admin / user / off
        "console_access": (config.console_access or "admin").strip().lower(),
        "data_root": str(config.data_root_path),
        "shared_root": str(config.shared_root_path),
        "users_root": str(config.users_root_path),
        "host": config.host,
        "port": config.port,
        "max_upload_mb": config.max_upload_mb,
        "shared_scope": SHARED,
    }
