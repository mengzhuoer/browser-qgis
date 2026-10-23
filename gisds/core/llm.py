"""大模型客户端 —— 同时支持 **OpenAI 兼容** 与 **Anthropic 原生** 两种协议。

为什么要自己写而不是直接用 SDK：OpenAI 兼容协议被无数家服务商实现
（DeepSeek、Moonshot、通义、智谱、硅基流动、Ollama…），而 Anthropic 是
另一套完全不同的消息格式。**与其装两个 SDK 并祈祷它们对得上**，
不如把「内部中立格式 → 各家协议」的转换写在自己手里：

* 内部中立格式：``user`` / ``assistant``(+tool_calls) / ``tool`` 三种消息
* OpenAI 适配器：``POST {base_url}/chat/completions``，工具结果是独立的
  ``role:"tool"`` 消息
* Anthropic 适配器：``POST {base_url}/messages``，工具结果是 ``user`` 消息里的
  ``tool_result`` 内容块

两种协议对外表现一致：同样的中立消息进去，同样结构的
:class:`LLMReply` 出来。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDERS = (PROVIDER_OPENAI, PROVIDER_ANTHROPIC)

#: 各协议的默认接入地址。填了别的就按填的来（自建网关、代理、本地推理都靠它）。
DEFAULT_BASE_URL = {
    PROVIDER_OPENAI: "https://api.openai.com/v1",
    PROVIDER_ANTHROPIC: "https://api.anthropic.com/v1",
}

#: 各协议的默认模型名只是个起手式，用户在界面上随便改。
DEFAULT_MODEL = {
    PROVIDER_OPENAI: "gpt-4o-mini",
    PROVIDER_ANTHROPIC: "claude-sonnet-4-5",
}

#: Anthropic 必须给 max_tokens；OpenAI 那边新一代模型不爱收这个字段，
#: 所以默认不发（让模型用自己的上限）。
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096


class LLMError(Exception):
    """调模型失败。

    ``detail`` 带上服务商返回的原文（截断过）—— 用户在界面上要能看出
    到底是「key 不对」「模型名不存在」还是「余额不足」。
    """

    def __init__(self, message: str, status: int = 0, detail: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "error": "llm_error",
            "message": self.message,
            "status": self.status,
            "detail": self.detail,
        }


# --------------------------------------------------------------------- 配置


@dataclass
class LLMConfig:
    """一次调用的模型参数。"""

    provider: str = PROVIDER_OPENAI
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout: float = 120.0
    max_tokens: int | None = None

    def resolved_base_url(self) -> str:
        base = (self.base_url or "").strip() or DEFAULT_BASE_URL.get(self.provider, "")
        return base.rstrip("/")

    def resolved_model(self) -> str:
        return (self.model or "").strip() or DEFAULT_MODEL.get(self.provider, "")

    def to_public_dict(self) -> dict:
        """对外可见的部分。**绝不含 api_key**。"""
        return {
            "provider": self.provider,
            "base_url": self.resolved_base_url(),
            "model": self.resolved_model(),
            "has_key": bool(self.api_key),
        }


def normalize_provider(raw: str | None) -> str:
    """把用户填的协议名归一化。认不出来就当 OpenAI 兼容。"""
    value = (raw or "").strip().lower().replace("_", "-")
    if value in ("anthropic", "claude", "anthropic-native", "anthropic-native-api"):
        return PROVIDER_ANTHROPIC
    return PROVIDER_OPENAI


# --------------------------------------------------------------------- 中立消息


def user_message(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant_message(text: str, tool_calls: list[dict] | None = None) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": tool_calls or []}


def tool_message(call_id: str, name: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def system_message(text: str) -> dict:
    return {"role": "system", "content": text}


@dataclass
class ToolCall:
    """模型要求调用的一个工具。"""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class LLMReply:
    """模型的一次回复。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    stop_reason: str = ""
    raw: dict = field(default_factory=dict)

    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


# --------------------------------------------------------------------- 适配器


def _loads(raw: Any) -> dict:
    """工具参数可能是 JSON 字符串，也可能已经是 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _openai_messages(messages: list[dict]) -> list[dict]:
    """中立消息 → OpenAI 的 messages。"""
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system":
            out.append({"role": "system", "content": content})
        elif role == "user":
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": content}
            calls = msg.get("tool_calls") or []
            if calls:
                item["tool_calls"] = [
                    {
                        "id": call.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": call.get("name") or "",
                            "arguments": json.dumps(
                                call.get("arguments") or {}, ensure_ascii=False
                            ),
                        },
                    }
                    for call in calls
                ]
            out.append(item)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id") or "",
                    "content": content,
                }
            )
    return out


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """中立消息 → Anthropic 的 (system, messages)。

    Anthropic 那边 system 是**独立字段**，不在 messages 里；
    而且连续的同角色消息要合并成一条（工具结果常常连着来好几条）。
    """
    system = ""
    conv: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system":
            system = f"{system}\n\n{content}" if system else content
            continue
        if role == "user":
            conv.append({"role": "user", "content": [{"type": "text", "text": content}]})
        elif role == "assistant":
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for call in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": call.get("name") or "",
                        "input": call.get("arguments") or {},
                    }
                )
            if blocks:
                conv.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id") or "",
                "content": content,
            }
            # 工具结果必须是 user 消息里的 tool_result 块；
            # 连续的合并进同一条 user 消息。
            if conv and conv[-1]["role"] == "user" and _all_tool_results(conv[-1]):
                conv[-1]["content"].append(block)
            else:
                conv.append({"role": "user", "content": [block]})
    return system, conv


def _all_tool_results(message: dict) -> bool:
    blocks = message.get("content") or []
    return bool(blocks) and all(b.get("type") == "tool_result" for b in blocks)


class BaseAdapter:
    """协议适配器的公共接口。"""

    provider = ""

    def build(
        self, cfg: LLMConfig, messages: list[dict], tools: list
    ) -> tuple[str, dict, dict]:
        """返回 (url, headers, body)。"""
        raise NotImplementedError

    def parse(self, data: dict) -> LLMReply:
        """把服务商的响应解析成中立回复。"""
        raise NotImplementedError


class OpenAIAdapter(BaseAdapter):
    """OpenAI 兼容协议（``/chat/completions``）。"""

    provider = PROVIDER_OPENAI

    def build(
        self, cfg: LLMConfig, messages: list[dict], tools: list
    ) -> tuple[str, dict, dict]:
        url = f"{cfg.resolved_base_url()}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        }
        body: dict[str, Any] = {
            "model": cfg.resolved_model(),
            "messages": _openai_messages(messages),
        }
        if cfg.max_tokens:
            body["max_tokens"] = cfg.max_tokens
        if tools:
            body["tools"] = [t.schema() for t in tools]
            body["tool_choice"] = "auto"
        return url, headers, body

    def parse(self, data: dict) -> LLMReply:
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}
        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=_loads(fn.get("arguments")),
                )
            )
        return LLMReply(
            content=message.get("content") or "",
            tool_calls=calls,
            usage=data.get("usage") or {},
            stop_reason=str(choices[0].get("finish_reason") or "") if choices else "",
            raw=data,
        )


class AnthropicAdapter(BaseAdapter):
    """Anthropic 原生协议（``/messages``）。"""

    provider = PROVIDER_ANTHROPIC

    def build(
        self, cfg: LLMConfig, messages: list[dict], tools: list
    ) -> tuple[str, dict, dict]:
        url = f"{cfg.resolved_base_url()}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
        }
        system, conv = _anthropic_messages(messages)
        body: dict[str, Any] = {
            "model": cfg.resolved_model(),
            "max_tokens": cfg.max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS,
            "messages": conv,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [t.anthropic_schema() for t in tools]
            body["tool_choice"] = {"type": "auto"}
        return url, headers, body

    def parse(self, data: dict) -> LLMReply:
        calls = []
        texts: list[str] = []
        for block in data.get("content") or []:
            kind = block.get("type")
            if kind == "text":
                texts.append(block.get("text") or "")
            elif kind == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=block.get("input") or {},
                    )
                )
        return LLMReply(
            content="".join(texts),
            tool_calls=calls,
            usage=data.get("usage") or {},
            stop_reason=str(data.get("stop_reason") or ""),
            raw=data,
        )


_ADAPTERS = {
    PROVIDER_OPENAI: OpenAIAdapter(),
    PROVIDER_ANTHROPIC: AnthropicAdapter(),
}


def get_adapter(provider: str) -> BaseAdapter:
    return _ADAPTERS.get(normalize_provider(provider), _ADAPTERS[PROVIDER_OPENAI])


# --------------------------------------------------------------------- 发请求


def _error_detail(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text[:limit]


async def complete(
    cfg: LLMConfig,
    messages: list[dict],
    tools: list | None = None,
    client: httpx.AsyncClient | None = None,
) -> LLMReply:
    """发一次请求，拿到中立回复。

    ``client`` 可以传进来复用（自检里会传一个指向假服务的）。
    """
    adapter = get_adapter(cfg.provider)
    url, headers, body = adapter.build(cfg, messages, tools or [])

    async def _post(http: httpx.AsyncClient) -> httpx.Response:
        return await http.post(url, headers=headers, json=body)

    try:
        if client is not None:
            resp = await _post(client)
        else:
            async with httpx.AsyncClient(timeout=cfg.timeout) as http:
                resp = await _post(http)
    except httpx.HTTPError as exc:
        raise LLMError(f"连不上模型服务：{type(exc).__name__}: {exc}") from exc

    if resp.status_code >= 400:
        raise LLMError(
            f"模型服务返回 {resp.status_code}",
            status=resp.status_code,
            detail=_error_detail(resp.text),
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError(
            "模型服务返回的不是 JSON", detail=_error_detail(resp.text)
        ) from exc

    if not isinstance(data, dict):
        raise LLMError("模型服务返回的 JSON 不是对象", detail=_error_detail(resp.text))

    return adapter.parse(data)
