"""网页 AI 助手接口。前缀 ``/api/ai``。

**谁能用（不可配置）**：所有**登录用户**。免登录模式（``require_login=False``）
下的匿名访客一律拒绝 —— 那种模式下「能打开网页」就等于「能拿别人的数据问 AI」。

**API Key 从哪来**（两个来源，浏览器那份优先）：

* 服务端默认（管理员在「网页 → AI 助手 → 服务端配置」里配，存在
  ``<用户库目录>/ai_config.json``）
* 浏览器临时填的（只存在这台电脑的 localStorage 里，随请求发上来）

**Key 永不下发到前端** —— ``GET /api/ai/config`` 只回 ``has_key``。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..core import ai as ai_core
from ..core import llm
from ..core.auth import Principal
from ..core.errors import GisdsError, InvalidRequestError
from .deps import current_principal, get_config, get_registry

router = APIRouter(prefix="/api/ai", tags=["ai"])


class LLMUpstreamError(GisdsError):
    """模型服务那边出了问题（连不上、返回 4xx/5xx、返回的不是 JSON）。"""

    status_code = 502
    code = "llm_error"


# --------------------------------------------------------------------- 权限


def ai_allowed(principal: Principal | None) -> bool:
    """当前调用者能不能用 AI 助手。

    规则是**定死的**（产品决定，不给配置项）：
    * 所有登录用户都能用
    * 匿名访客（免登录模式）不行
    """
    if principal is None:
        return False
    if principal.shared_only or principal.user_id == "anonymous":
        return False
    return True


def _require_ai(principal: Principal | None) -> Principal:
    if not ai_allowed(principal):
        raise HTTPException(status_code=403, detail="AI 助手只对登录用户开放")
    return principal


# --------------------------------------------------------------------- 配置存取

_STORE: ai_core.AIConfigStore | None = None
_STORE_PATH: str | None = None


def get_store() -> ai_core.AIConfigStore:
    """取服务端默认配置的存取器。

    配置文件放在**用户库旁边**（``<db 所在目录>/ai_config.json``）——
    不放数据根目录，那里的东西会被当成数据集扫进来。
    """
    global _STORE, _STORE_PATH
    path = get_config().db_path_resolved.parent / "ai_config.json"
    if _STORE is None or _STORE_PATH != str(path):
        _STORE = ai_core.AIConfigStore(path)
        _STORE_PATH = str(path)
    return _STORE


@router.get("/config")
def read_config(principal: Principal | None = Depends(current_principal)) -> dict[str, Any]:
    """读服务端默认配置。

    **不回传 API Key 明文**，只回 ``has_key``。
    """
    _require_ai(principal)
    settings = get_store().load()
    return {
        "enabled": True,
        "ready": bool(settings.api_key),
        "providers": list(llm.PROVIDERS),
        "max_rounds": ai_core.MAX_ROUNDS,
        "can_edit_config": bool(principal.is_admin),
        **settings.public_dict(),
    }


@router.put("/config")
def write_config(
    body: dict = Body(default_factory=dict),
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """改服务端默认配置。**只有管理员能改**（会影响全机所有人）。

    ``api_key`` 字段：传了就替换，传空串就是清掉，不传就是保持原样。
    """
    _require_ai(principal)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员能改服务端默认配置")

    changes: dict[str, Any] = {}
    for key in ("provider", "base_url", "model", "api_key"):
        if key in body:
            changes[key] = body[key]
    if "allow_write" in body:
        changes["allow_write"] = bool(body["allow_write"])

    settings = get_store().update(**changes)
    return {"ok": True, **settings.public_dict()}


# --------------------------------------------------------------------- 工具


@router.get("/tools")
def list_tools(principal: Principal | None = Depends(current_principal)) -> dict[str, Any]:
    """这一轮给模型用的工具清单（写工具默认不出现）。"""
    _require_ai(principal)
    settings = get_store().load()
    tools = ai_core.describe_tools(allow_write=settings.allow_write)
    return {
        "count": len(tools),
        "allow_write": settings.allow_write,
        "tools": tools,
    }


# --------------------------------------------------------------------- 聊天


@router.post("/chat")
async def chat(
    body: dict = Body(default_factory=dict),
    principal: Principal | None = Depends(current_principal),
) -> dict[str, Any]:
    """问一句，模型自己决定要不要调工具。

    请求体::

        {
          "message": "示例数据里有哪些图层？",
          "history": [{"role": "user", "content": "..."}],
          "provider": "openai",       # 可选，临时覆盖服务端默认
          "base_url": "https://…/v1",  # 可选
          "model": "gpt-4o-mini",      # 可选
          "api_key": "sk-…"           # 可选，临时覆盖
        }
    """
    _require_ai(principal)

    request = ai_core.ChatRequest(
        message=str(body.get("message") or ""),
        history=body.get("history") or [],
        provider=str(body.get("provider") or ""),
        base_url=str(body.get("base_url") or ""),
        model=str(body.get("model") or ""),
        api_key=str(body.get("api_key") or ""),
    )

    try:
        return await ai_core.chat(
            request,
            get_store().load(),
            principal,
            get_registry(),
            get_config(),
        )
    except llm.LLMError as exc:
        raise LLMUpstreamError(exc.message, exc.detail or {"status": exc.status}) from exc
    except InvalidRequestError:
        raise
