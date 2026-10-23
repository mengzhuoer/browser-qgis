"""网页里的 AI 助手 —— agent 循环 + 服务端默认配置。

**它和 MCP Server 用同一套工具**（``gisds.core.gis_tools``），差别只有一处：
身份。MCP 是服务级（能看到所有空间），这里是**按登录用户**过滤 ——
普通用户在聊天框里问一句，能查到的就只有他有权看的那些空间。

对话是怎么跑起来的（一轮）：

1. 把系统提示 + 历史 + 这次的问题交给大模型
2. 模型要么直接答（收工），要么要求调工具
3. 工具在**这一位用户的权限下**执行，结果回喂给模型
4. 重复 2~3，最多 ``MAX_ROUNDS`` 轮

模型自己看不见工具的原始返回（图片尤其如此），所以每一步都同时记一条
``steps``，网页上能展开看「它到底查了什么、拿到了什么」。
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .errors import GisdsError, InvalidRequestError
from .gis_tools import (
    TOOL_INSTRUCTIONS,
    RenderResult,
    ToolContext,
    ToolSpec,
    bind,
    select_tools,
)

LOGGER = logging.getLogger("gisds.ai")

#: 一轮对话最多让模型调几轮工具。防止「调工具 → 再调工具 → …」停不下来。
MAX_ROUNDS = 8

#: 历史消息最多带多少条进模型（太长了又慢又贵）。
MAX_HISTORY = 20

SYSTEM_PROMPT = """你是「Browser QGIS」里的 GIS 助手。

这个服务用 PyQGIS 内核提供 GIS 数据能力：浏览数据集、查询栅格与矢量、
读属性表、算统计、跑 Processing 算法、编辑要素。你通过下面这些工具干活。

工作方式：
- 别凭空猜数据集名和图层名 —— 先用 gis_list_datasets / gis_list_layers 摸清楚。
- 需要数字时用工具去查，不要凭印象作答。
- 回答用中文：先给结论，再给关键数字。数据一律以工具返回的为准。
- 工具报错时读懂错误信息，改参数再试一次；同一件事最多试两次。
- 工具没给的数据就说查不到，别编。

""" + TOOL_INSTRUCTIONS


# --------------------------------------------------------------------- 配置


@dataclass
class AISettings:
    """服务端存的那份默认配置。"""

    provider: str = llm.PROVIDER_OPENAI
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    #: 是否允许 AI 调**写**工具（改要素、算字段、建物签）。
    #: 默认关 —— 聊天是给所有人开的，写数据不是。
    allow_write: bool = False

    def public_dict(self) -> dict:
        """对外可见的部分。**绝不含 api_key 明文**。"""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "has_key": bool(self.api_key),
            "allow_write": self.allow_write,
        }


class AIConfigStore:
    """服务端默认配置的存取。

    落在**用户库旁边**（``<db 所在目录>/ai_config.json``）——
    不放数据根目录：那里面的东西会被当成数据集扫进来。

    这里存的是**明文** API Key：它的用途就是让所有登录用户直接用，
    不下发到前端（前端只能看到 ``has_key``）。要收紧就把这台机器的
    文件系统权限管好，或者干脆不配服务端默认、让每个人自己填 key。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._cached: AISettings | None = None

    def load(self) -> AISettings:
        if self._cached is not None:
            return self._cached
        raw: dict[str, Any] = {}
        try:
            if self.path.is_file():
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    raw = parsed
        except Exception as exc:  # 配置坏了不该让服务起不来
            LOGGER.warning("AI 配置读不出来，按默认处理：%s", exc)
            raw = {}
        self._cached = AISettings(
            provider=llm.normalize_provider(raw.get("provider")),
            base_url=str(raw.get("base_url") or ""),
            model=str(raw.get("model") or ""),
            api_key=str(raw.get("api_key") or ""),
            allow_write=bool(raw.get("allow_write")),
        )
        return self._cached

    def save(self, settings: AISettings) -> AISettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": settings.provider,
            "base_url": settings.base_url,
            "model": settings.model,
            "api_key": settings.api_key,
            "allow_write": settings.allow_write,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        self._cached = settings
        return settings

    def update(self, **changes: Any) -> AISettings:
        """改一部分，键为 None 表示「清空」。"""
        current = self.load()
        provider = changes.get("provider", current.provider)
        base_url = changes.get("base_url", current.base_url)
        model = changes.get("model", current.model)
        api_key = changes.get("api_key", current.api_key)
        allow_write = changes.get("allow_write", current.allow_write)
        return self.save(
            AISettings(
                provider=llm.normalize_provider(provider),
                base_url="" if base_url is None else str(base_url),
                model="" if model is None else str(model),
                api_key="" if api_key is None else str(api_key),
                allow_write=bool(allow_write),
            )
        )


# --------------------------------------------------------------------- 对话请求


@dataclass
class ChatRequest:
    """一次聊天请求（浏览器发来的）。"""

    message: str
    history: list[dict] = field(default_factory=list)
    #: 浏览器侧的临时覆盖。留空就用服务端默认。
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""


def _endpoint_changed(server: AISettings, request: ChatRequest) -> bool:
    """浏览器是不是换了个接口地址或协议。

    换了就意味着「请求会发到别的地方去」—— 服务端那份 key 不能再跟着走。
    """
    if llm.normalize_provider(request.provider or server.provider) != server.provider:
        return True
    want = (request.base_url or "").strip().rstrip("/")
    have = (server.base_url or "").strip().rstrip("/")
    return bool(want) and want != have


def merge_settings(server: AISettings, request: ChatRequest) -> AISettings:
    """把浏览器侧的临时覆盖合到服务端默认上。

    **浏览器只能覆盖「用哪个模型、拿哪个 key」，不能改权限** ——
    能不能写数据由服务端的 ``allow_write`` 说了算。

    还有一条**安全线**：浏览器换了接口地址或协议时，服务端那份 key **不跟着走**。
    否则任何登录用户都能让服务端把自己的 key 送到他指定的任意地址去 ——
    那就是把全机的凭据交出去了。所以「换地址」必须同时「自带 key」。
    """
    provider = llm.normalize_provider(request.provider or server.provider)
    key = (request.api_key or "").strip()
    if not key and not _endpoint_changed(server, request):
        key = server.api_key
    return AISettings(
        provider=provider,
        base_url=(request.base_url or server.base_url or "").strip(),
        model=(request.model or server.model or "").strip(),
        api_key=key,
        allow_write=server.allow_write,
    )


def normalize_history(raw: Any) -> list[dict]:
    """把浏览器传来的历史收拾成 ``[{"role","content"}]``。

    只留 user / assistant 的纯文本 —— 工具调用那几轮不重放：
    没有配对的 tool_call id，重放会被模型服务拒掉。
    """
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if role == "user":
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            out.append({"role": "assistant", "content": content})
    return out[-MAX_HISTORY:]


# --------------------------------------------------------------------- 工具执行


def _check_arguments(spec: ToolSpec, arguments: dict) -> tuple[dict, list[str], list[str]]:
    """按 schema 收拾参数。返回 (可用参数, 缺的必填项, 多余的键)。"""
    schema = spec.parameters
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    known = set(props)
    usable = {k: v for k, v in (arguments or {}).items() if k in known}
    missing = [name for name in required if name not in usable]
    extra = sorted(set(arguments or {}) - known)
    return usable, missing, extra


async def run_tool(
    call: llm.ToolCall,
    specs: dict[str, ToolSpec],
    ctx: ToolContext,
) -> tuple[str, str | None, bool]:
    """执行一个工具调用。

    返回 ``(给模型看的文本, 给用户看的图片 data URL, 有没有成功)``。
    失败**不抛异常** —— 工具报错是模型该知道的事（它可以据此改参数重试），
    抛出去会把整轮对话带崩。
    """
    spec = specs.get(call.name)
    if spec is None:
        return (f"没有这个工具：{call.name}", None, False)

    usable, missing, extra = _check_arguments(spec, call.arguments)
    if missing:
        return (f"参数不对：缺少 {', '.join(missing)}", None, False)
    if extra:
        LOGGER.info("模型多给了几个参数，忽略：%s", extra)

    fn = bind(spec, ctx)
    try:
        result = await fn(**usable)
    except GisdsError as exc:
        return (
            json.dumps(
                {"error": exc.code, "message": exc.message, "detail": exc.detail},
                ensure_ascii=False,
            ),
            None,
            False,
        )
    except Exception as exc:  # noqa: BLE001 - 工具炸了要告诉模型，别把整轮带崩
        LOGGER.exception("AI 调工具失败：%s", call.name)
        return (f"工具 {call.name} 执行失败：{type(exc).__name__}: {exc}", None, False)

    if isinstance(result, RenderResult):
        data_url = "data:image/png;base64," + base64.b64encode(result.data).decode("ascii")
        text = (
            f"已渲染地图图片（{result.width}x{result.height}）：{result.caption}。"
            "图片已展示给用户。"
        )
        return (text, data_url, True)

    return (str(result), None, True)


# --------------------------------------------------------------------- 主循环


async def chat(
    request: ChatRequest,
    settings: AISettings,
    principal: Any,
    registry: Any,
    config: Any,
) -> dict:
    """跑一轮对话。

    返回::

        {
          "ok": True,
          "answer": "模型最后说的话",
          "steps": [{"name","arguments","result","ok","image"}...],
          "rounds": 2,
          "usage": {...},
          "provider": "openai",
          "model": "gpt-4o-mini",
        }
    """
    message = (request.message or "").strip()
    if not message:
        raise InvalidRequestError("说点什么吧")

    merged = merge_settings(settings, request)
    if not merged.api_key:
        if settings.api_key:
            # 服务端是有 key 的，只是这次换了接口地址 —— 服务端那份不能跟着走。
            raise InvalidRequestError(
                "换了接口地址就得用对应的 Key：请在页面上填一份 API Key"
            )
        raise InvalidRequestError(
            "还没有配置大模型 API：请在页面上填 API Key，或让管理员在服务端配一份"
        )

    llm_cfg = llm.LLMConfig(
        provider=merged.provider,
        base_url=merged.base_url,
        model=merged.model,
        api_key=merged.api_key,
    )

    specs = {spec.name: spec for spec in select_tools(allow_write=merged.allow_write)}
    ctx = ToolContext(registry=registry, config=config, principal=principal)

    messages: list[dict] = [llm.system_message(SYSTEM_PROMPT)]
    for item in normalize_history(request.history):
        messages.append(llm.user_message(item["content"]) if item["role"] == "user"
                        else llm.assistant_message(item["content"]))
    messages.append(llm.user_message(message))

    steps: list[dict] = []
    usage: dict = {}

    for round_no in range(1, MAX_ROUNDS + 1):
        reply = await llm.complete(llm_cfg, messages, list(specs.values()))
        if reply.usage:
            usage = reply.usage

        if not reply.wants_tools():
            return {
                "ok": True,
                "answer": reply.content,
                "steps": steps,
                "rounds": round_no,
                "usage": usage,
                "provider": merged.provider,
                "model": llm_cfg.resolved_model(),
            }

        messages.append(
            llm.assistant_message(
                reply.content,
                [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in reply.tool_calls
                ],
            )
        )

        for call in reply.tool_calls:
            text, image, ok = await run_tool(call, specs, ctx)
            messages.append(llm.tool_message(call.id, call.name, text))
            steps.append(
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "result": text,
                    "ok": ok,
                    "image": image,
                }
            )

    return {
        "ok": False,
        "answer": (
            f"工具调用轮次用尽（最多 {MAX_ROUNDS} 轮）。"
            "可以把问题拆小一点，或者直接说「先列一下有哪些数据集」。"
        ),
        "steps": steps,
        "rounds": MAX_ROUNDS,
        "usage": usage,
        "provider": merged.provider,
        "model": llm_cfg.resolved_model(),
    }


def describe_tools(allow_write: bool = False) -> list[dict]:
    """给网页看的工具清单。"""
    out = []
    for spec in select_tools(allow_write=allow_write):
        out.append(
            {
                "name": spec.name,
                "description": spec.description,
                "readonly": spec.readonly,
                "parameters": spec.parameters,
            }
        )
    return out
