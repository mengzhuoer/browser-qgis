"""账号与登录接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..core.auth import AuthError, AuthzError, Principal
from ..core.tenancy import SHARED, user_scope
from .deps import current_principal, get_auth, get_config

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field("", max_length=64)


class LoginIn(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)


def _user_payload(user: dict) -> dict:
    return {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": user["role"],
        "status": user["status"],
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
    }


@router.get("/config", summary="登录页需要的开关")
async def auth_config() -> dict:
    config = get_config()
    payload = {
        "require_login": config.require_login,
        "allow_registration": config.allow_registration,
        "admin_username": config.admin_username,
    }
    # 内置的共享超级账号：启用时把凭据一并告诉登录页。
    #
    # 「所有用户都能用」这句话，页面上得看得见 —— 不然别人怎么知道
    # 还能有这组账号？口令是明文暴露的，这正是「共享账号」的定义：
    # 拿到它的人就是管理员。这项**留空即关闭**，没配就一点都不露。
    super_pwd = config.super_admin_password_resolved
    if super_pwd:
        payload["shared_account"] = {
            "username": config.admin_username,
            "password": super_pwd,
            "note": "共享超级账号：同网段任何人拿它登录都是管理员，能管账号、能删数据、能跑 Python。",
        }
    return payload


@router.post("/register", status_code=201, summary="注册")
async def register(payload: RegisterIn) -> dict:
    config = get_config()
    if not config.allow_registration:
        raise HTTPException(status_code=403, detail="本服务未开放自助注册，请联系管理员开号")
    auth = get_auth()
    try:
        user = auth.register(
            payload.username, payload.password, display_name=payload.display_name
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token, _user = auth.login(payload.username, payload.password)
    return {"token": token, "user": _user_payload(user)}


@router.post("/login", summary="登录")
async def login(payload: LoginIn) -> dict:
    auth = get_auth()
    try:
        token, user = auth.login(payload.username, payload.password)
    except AuthzError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"token": token, "user": _user_payload(user)}


@router.post("/logout", summary="登出")
async def logout(
    authorization: str | None = Header(default=None),
) -> dict:
    token = None
    if authorization:
        parts = authorization.split(None, 1)
        token = parts[1].strip() if len(parts) == 2 else authorization.strip()
    ok = get_auth().logout(token or "")
    return {"logged_out": bool(ok)}


@router.get("/me", summary="当前登录者")
async def me(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "principal": principal.to_dict(),
        "scopes": [
            {"scope": SHARED, "label": "共享空间", "writable": principal.can_write(SHARED)},
            {
                "scope": user_scope(principal.user_id),
                "label": "个人空间",
                "writable": principal.can_write(user_scope(principal.user_id)),
            },
        ],
    }
