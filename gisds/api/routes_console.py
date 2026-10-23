"""Python 控制台接口。

前缀 ``/api/console``。控制台等于在数据服务器上执行任意代码，所以
**默认只对管理员开放**（``config.console_access``）：

* ``admin``（默认）：只有管理员能用
* ``user``：所有登录用户都能用
* ``off``：整个关掉

免登录模式（``require_login=False``）下**一律拒绝** —— 那种模式下
「能访问网页」就等于「能在服务器上跑代码」，不能接受。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..core import pyconsole
from ..core.auth import Principal
from ..core.errors import InvalidRequestError
from .deps import current_principal, get_config

router = APIRouter(prefix="/api/console", tags=["console"])


def console_allowed(principal: Principal | None) -> bool:
    """当前调用者能不能用控制台。"""
    config = get_config()
    mode = (config.console_access or "admin").strip().lower()
    if mode == "off":
        return False
    if principal is None:
        return False
    # 免登录模式的匿名身份：不能给
    if principal.shared_only or principal.user_id == "anonymous":
        return False
    if mode == "user":
        return True
    return bool(principal.is_admin)


def _require_console(principal: Principal | None) -> None:
    if not console_allowed(principal):
        mode = (get_config().console_access or "admin").strip().lower()
        if mode == "off":
            raise HTTPException(status_code=403, detail="Python 控制台已关闭")
        raise HTTPException(status_code=403, detail="Python 控制台只对管理员开放")


@router.get("/state")
def state(principal: Principal | None = Depends(current_principal)) -> dict[str, Any]:
    """控制台状态：能不能用、当前命名空间里有哪些变量。"""
    allowed = console_allowed(principal)
    if not allowed:
        return {
            "allowed": False,
            "access": (get_config().console_access or "admin").strip().lower(),
            "variables": [],
            "history": 0,
        }
    session = pyconsole.session_for(principal.user_id)
    return {
        "allowed": True,
        "access": (get_config().console_access or "admin").strip().lower(),
        "variables": session.variables(),
        "history": len(session.history),
    }


@router.post("/exec")
async def execute(
    body: dict = Body(default_factory=dict),
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """执行一段 Python 代码。"""
    _require_console(principal)
    code = str(body.get("code") or "")
    if not code.strip():
        raise InvalidRequestError("代码不能为空")
    timeout = float(get_config().console_timeout or 30)
    result = await pyconsole.execute(code, principal.user_id, timeout=timeout)
    return result.to_dict()


@router.post("/reset")
def reset(principal: Principal | None = Depends(current_principal)) -> dict[str, Any]:
    """清空命名空间（变量、import 过的模块都忘掉）。"""
    _require_console(principal)
    session = pyconsole.reset_session(principal.user_id)
    return {"ok": True, "variables": len(session.variables())}
