# -*- coding: utf-8 -*-
"""网页 AI 助手自检。

AI 助手是「模型 + 工具 + 权限」三样东西的组合，所以这一支要同时盯住三头：

1. **协议**：OpenAI 兼容与 Anthropic 原生两种格式，请求体和响应体都要转对。
   做法是**在测试进程里起一个假模型服务**（标准库 http.server，不联网），
   把模型服务真实收到的请求记下来，逐字段核对。
2. **权限**：所有登录用户都能用（产品定死）；免登录模式的匿名访客一律拒绝；
   改服务端默认配置只有管理员能改。
3. **隔离**：AI 调工具时是**按登录用户**的身份调的 —— B 问 AI，A 的私有数据集
   既列不出来也查不到。

还有一条不能忘的：**API Key 永不下发到前端**。服务端存的那份，浏览器只看得见
``has_key``；响应体里不能出现 key 明文。

跑法：``run-qgis-python.bat tests/check_ai.py``
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from server_helper import TestServer, _free_port  # noqa: E402

API_KEY = "test-ai-service-key"
ADMIN_PASSWORD = "adminpass123"
USER_PASSWORD = "pass12345"
#: 服务端存的那份 key。断言「响应里不能出现它」时要用。
SERVER_MODEL_KEY = "sk-server-side-keep-me-secret-0001"
#: 浏览器临时填的那份 key。
BROWSER_MODEL_KEY = "sk-browser-side-override-0002"

fails: list[str] = []
checked = 0

#: 断言总数（**不含**末尾那条「总数」断言本身）。
#:
#: 钉死它：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
#: 「到底哪几条没验」。断言空过比断言失败更难查。
EXPECTED_CHECKS = 82


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def call(client, method, path, **kw):
    """发一个请求，返回 (状态码, 响应体)。"""
    try:
        r = client.request(method, path, **kw)
    except Exception as exc:
        return None, {"_error": f"请求失败（服务可能已经没了）：{type(exc).__name__}: {exc}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_text": r.text}


# --------------------------------------------------------------------- 假模型服务


class FakeLLM:
    """按剧本回话的假模型服务。

    剧本是一串步骤，每步要么要求调工具（``{"tool": ..., "args": {...}}``），
    要么直接给答案（``{"text": "..."}``）。每收到一次请求就往下走一步。
    """

    def __init__(self) -> None:
        self.script: list[dict] = []
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None
        self.port = 0

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> int:
        fake = self
        handler = _make_handler(fake)
        self.port = _free_port(8899)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        return self.port

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # ------------------------------------------------------------------ 剧本

    def reset(self, script: list[dict]) -> None:
        with self.lock:
            self.script = list(script)
            self.requests = []

    def base_url(self, provider: str = "openai") -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def last_request(self) -> dict:
        with self.lock:
            return self.requests[-1] if self.requests else {}


def _make_handler(fake: FakeLLM):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 的约定
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}
            with fake.lock:
                fake.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                step = fake.script.pop(0) if fake.script else {"text": "（剧本用完了）"}
            if self.path.rstrip("/").endswith("/messages"):
                payload = _anthropic_reply(step)
            else:
                payload = _openai_reply(step)
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # 别把请求日志糊满终端
            pass

    return Handler


def _openai_reply(step: dict) -> dict:
    if "tool" in step:
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": step.get("text") or "",
                        "tool_calls": [
                            {
                                "id": "call_fake_1",
                                "type": "function",
                                "function": {
                                    "name": step["tool"],
                                    "arguments": json.dumps(
                                        step.get("args") or {}, ensure_ascii=False
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": step.get("text") or ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 9, "total_tokens": 29},
    }


def _anthropic_reply(step: dict) -> dict:
    if "tool" in step:
        content = [
            {"type": "text", "text": step.get("text") or "我先查一下"},
            {
                "type": "tool_use",
                "id": "toolu_fake_1",
                "name": step["tool"],
                "input": step.get("args") or {},
            },
        ]
        stop = "tool_use"
    else:
        content = [{"type": "text", "text": step.get("text") or ""}]
        stop = "end_turn"
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": "fake-model",
        "content": content,
        "stop_reason": stop,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


# --------------------------------------------------------------------- 工具函数


def _login(client, username: str, password: str) -> str:
    status, body = call(
        client, "POST", "/api/auth/login", json={"username": username, "password": password}
    )
    return str((body or {}).get("token") or "") if status == 200 else ""


def _register(client, username: str, password: str) -> str:
    status, body = call(
        client, "POST", "/api/auth/register", json={"username": username, "password": password}
    )
    return str((body or {}).get("token") or "") if status == 201 else ""


def _put_config(client, headers, **fields):
    return call(client, "PUT", "/api/ai/config", headers=headers, json=fields)


def _chat(client, headers, message: str, **extra):
    body = {"message": message, "history": []}
    body.update(extra)
    return call(client, "POST", "/api/ai/chat", headers=headers, json=body)


def _steps_text(body) -> str:
    return "\n".join(str(s.get("result") or "") for s in ((body or {}).get("steps") or []))


def _step_names(body) -> list[str]:
    return [str(s.get("name") or "") for s in ((body or {}).get("steps") or [])]


def _sent_key(req) -> str:
    """模型服务实际收到的 key（两种协议的请求头不一样，都看一遍）。"""
    headers = (req or {}).get("headers") or {}
    raw = headers.get("authorization") or headers.get("x-api-key") or ""
    return str(raw).replace("Bearer ", "").strip()


# --------------------------------------------------------------------- 0. 纯函数级


def check_pure() -> None:
    """不起服务也能验的部分：schema 派生、协议转换、工具清单。"""
    print("\n== 0. 纯函数：schema 派生与协议转换 ==")
    from gisds.core import llm
    from gisds.core.gis_tools import TOOLS, TOOLS_BY_NAME, select_tools

    names = [t.name for t in TOOLS]
    check("工具是 27 个", len(names) == 27, f"实际 {len(names)}")
    check(
        "工具名与 MCP 一致（含 gis_list_datasets / gis_add_bookmark）",
        "gis_list_datasets" in names and "gis_add_bookmark" in names,
    )
    check(
        "工具名没有重复",
        len(set(names)) == len(names),
    )

    spec = TOOLS_BY_NAME["gis_query_point"]
    props = spec.parameters.get("properties") or {}
    check("gis_query_point 的 dataset_id 是 string",
          (props.get("dataset_id") or {}).get("type") == "string", str(props.get("dataset_id")))
    check("gis_query_point 的 lon 是 number",
          (props.get("lon") or {}).get("type") == "number", str(props.get("lon")))
    check("gis_query_point 四个参数都是必填",
          set(spec.parameters.get("required") or []) == {"dataset_id", "layer_id", "lon", "lat"},
          str(spec.parameters.get("required")))
    check("参数带中文说明",
          "经度" in str((props.get("lon") or {}).get("description") or ""),
          str((props.get("lon") or {}).get("description")))

    optional = TOOLS_BY_NAME["gis_list_layers"]
    check(
        "可选参数不进 required（gis_list_layers 无 required）",
        not optional.parameters.get("required"),
        str(optional.parameters.get("required")),
    )
    check(
        "工具说明 = 处理函数的 docstring 原文",
        spec.description == (spec.handler.__doc__ or "").strip()
        and spec.description.startswith("查询某个经纬度位置"),
        spec.description[:40],
    )

    readonly = {t.name for t in select_tools(allow_write=False)}
    check("默认只给只读工具", "gis_edit_start" not in readonly and "gis_query_point" in readonly)
    check(
        "开 allow_write 后写工具才出现",
        "gis_edit_start" in {t.name for t in select_tools(allow_write=True)},
    )

    # ---- 协议转换
    neutral = [
        llm.system_message("系统提示"),
        llm.user_message("问一句"),
        llm.assistant_message("我去查", [{"id": "c1", "name": "gis_list_datasets", "arguments": {}}]),
        llm.tool_message("c1", "gis_list_datasets", "共 1 个数据集"),
    ]
    oa = llm._openai_messages(neutral)
    check("OpenAI：system 转成 system 消息", oa[0] == {"role": "system", "content": "系统提示"})
    check("OpenAI：assistant 的工具调用是 function 形式",
          (oa[2].get("tool_calls") or [{}])[0].get("function", {}).get("name") == "gis_list_datasets",
          str(oa[2])[:120])
    check("OpenAI：工具结果是 role=tool 的独立消息",
          oa[3].get("role") == "tool" and oa[3].get("tool_call_id") == "c1", str(oa[3])[:120])

    system, conv = llm._anthropic_messages(neutral)
    check("Anthropic：system 单独拿出来", system == "系统提示" and
          all(m.get("role") != "system" for m in conv), system[:60])
    check("Anthropic：工具调用是 tool_use 块",
          conv[1]["content"][-1].get("type") == "tool_use"
          and conv[1]["content"][-1].get("name") == "gis_list_datasets",
          str(conv[1])[:140])
    check("Anthropic：工具结果是 user 消息里的 tool_result 块",
          conv[2]["role"] == "user"
          and conv[2]["content"][0].get("type") == "tool_result"
          and conv[2]["content"][0].get("tool_use_id") == "c1",
          str(conv[2])[:140])

    two = [
        llm.assistant_message("", [{"id": "c1", "name": "a", "arguments": {}}]),
        llm.tool_message("c1", "a", "结果一"),
        llm.tool_message("c2", "b", "结果二"),
    ]
    _, conv2 = llm._anthropic_messages(two)
    check("Anthropic：连续的工具结果合并进同一条 user 消息",
          len(conv2) == 2 and len(conv2[1]["content"]) == 2, str(conv2)[:160])

    check("协议名归一化：claude -> anthropic",
          llm.normalize_provider("Claude") == llm.PROVIDER_ANTHROPIC)
    check("协议名归一化：其它一律按 OpenAI 兼容",
          llm.normalize_provider("openai-compatible") == llm.PROVIDER_OPENAI
          and llm.normalize_provider("") == llm.PROVIDER_OPENAI)

    # ---- 空间隔离（不需要服务）
    from gisds.core.auth import Principal
    from gisds.core.gis_tools import ToolContext

    alice = Principal(user_id="u_alice", username="alice", role="user")
    bob = Principal(user_id="u_bob", username="bob", role="user")
    ctx_bob = ToolContext(registry=None, config=None, principal=bob)
    check("别人家的空间：B 读不了 A 的私有空间",
          _scope_rejected(ctx_bob, "user:u_alice", write=False))
    check("自己的空间：B 读得了",
          ctx_bob.check_scope("user:u_bob", write=False) == "user:u_bob")
    check("共享空间：B 读得了也写得了",
          ctx_bob.check_scope("shared", write=False) == "shared"
          and ctx_bob.check_scope("shared", write=True) == "shared")
    check("写别人的空间：B 写不了",
          _scope_rejected(ctx_bob, "user:u_alice", write=True))

    service_ctx = ToolContext(registry=None, config=None, principal=None)
    check("服务级身份（MCP）：空间不过滤",
          service_ctx.check_scope("user:u_alice", write=True) == "user:u_alice")

    class _Dataset:
        def __init__(self, scope):
            self.scope = scope

    class _FakeRegistry:
        def __init__(self):
            self.items = [_Dataset("shared"), _Dataset("user:u_alice")]

        def datasets(self, principal=None):
            if principal is None:
                return list(self.items)
            return [d for d in self.items if principal.can_see(d.scope)]

    reg = _FakeRegistry()
    check("服务级身份（MCP）：数据集不过滤（两个都能看到）",
          len(ToolContext(registry=reg, principal=None).datasets()) == 2)
    check("用户身份：只看得见有权的那个",
          len(ToolContext(registry=reg, principal=bob).datasets()) == 1)
    check("管理员身份：全都看得见",
          len(ToolContext(registry=reg, principal=Principal(
              user_id="u_admin", username="admin", role="admin")).datasets()) == 2)


def _scope_rejected(ctx, scope: str, write: bool) -> bool:
    from gisds.core.errors import GisdsError

    try:
        ctx.check_scope(scope, write=write)
    except GisdsError:
        return True
    return False


# --------------------------------------------------------------------- 1~3. 端到端


def check_e2e(fake: FakeLLM, data_root: Path) -> None:
    """起真服务，跑真对话。"""
    print("\n== 1. 端到端：两种协议 + 权限 + 隔离 ==")
    with TestServer(
        port=_free_port(8871),
        require_login=True,
        data_root=data_root,
        extra_env={
            "GISDS_API_KEY": API_KEY,
            "GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "GISDS_ALLOW_REGISTRATION": "1",
        },
    ) as server:
        with httpx.Client(base_url=server.base, timeout=120.0) as client:
            admin = {"X-API-Key": API_KEY}

            # ---- 管理员配服务端默认
            status, body = _put_config(
                client,
                admin,
                provider="openai",
                base_url=fake.base_url("openai"),
                model="fake-model",
                api_key=SERVER_MODEL_KEY,
            )
            check("管理员能改服务端默认配置", status == 200, f"HTTP {status} {str(body)[:140]}")
            check("改完 has_key 为真", (body or {}).get("has_key") is True, str(body)[:140])

            # ---- OpenAI 兼容协议
            fake.reset([
                {"tool": "gis_list_datasets", "args": {}},
                {"text": "服务里只有一个示例数据集。"},
            ])
            status, body = _chat(client, admin, "现在有哪些数据集？")
            check("OpenAI 协议：聊天返回 200", status == 200, f"HTTP {status} {str(body)[:200]}")
            check("OpenAI 协议：模型确实调了工具",
                  _step_names(body) == ["gis_list_datasets"], str(_step_names(body)))
            check("OpenAI 协议：拿到了最终答复",
                  "示例数据集" in str((body or {}).get("answer") or ""),
                  str((body or {}).get("answer"))[:120])
            check("OpenAI 协议：工具结果里看得到示例数据",
                  "示例" in _steps_text(body), _steps_text(body)[:120])
            check("OpenAI 协议：成功的工具步骤标记为成功",
                  all(s.get("ok") is True for s in ((body or {}).get("steps") or [])),
                  str((body or {}).get("steps"))[:160])

            req = fake.last_request()
            check("OpenAI 协议：打的是 /chat/completions",
                  req.get("path", "").endswith("/chat/completions"), req.get("path", ""))
            oa_body = req.get("body") or {}
            oa_msgs = oa_body.get("messages") or []
            check("OpenAI 协议：请求里有 assistant 的 tool_calls",
                  any(m.get("role") == "assistant" and m.get("tool_calls") for m in oa_msgs),
                  str(oa_msgs)[:200])
            check("OpenAI 协议：请求里有 role=tool 的工具结果",
                  any(m.get("role") == "tool" for m in oa_msgs), str(oa_msgs)[:200])
            check("OpenAI 协议：tools 里有 gis_list_datasets",
                  any((t.get("function") or {}).get("name") == "gis_list_datasets"
                      for t in (oa_body.get("tools") or [])),
                  str(oa_body.get("tools"))[:160])
            check("OpenAI 协议：Authorization 用的是服务端配的 key",
                  (req.get("headers") or {}).get("authorization") == f"Bearer {SERVER_MODEL_KEY}",
                  str((req.get("headers") or {}).get("authorization")))
            # ---- Anthropic 原生协议
            status, body = _put_config(
                client, admin, provider="anthropic", model="fake-model"
            )
            check("能切成 Anthropic 原生协议", status == 200, f"HTTP {status}")
            fake.reset([
                {"tool": "gis_list_layers", "args": {"kind": "raster"}},
                {"text": "有 2 个栅格图层。"},
            ])
            status, body = _chat(client, admin, "有哪些栅格图层？")
            check("Anthropic 协议：聊天返回 200", status == 200, f"HTTP {status} {str(body)[:200]}")
            check("Anthropic 协议：模型确实调了工具",
                  _step_names(body) == ["gis_list_layers"], str(_step_names(body)))
            check("Anthropic 协议：拿到了最终答复",
                  "栅格图层" in str((body or {}).get("answer") or ""),
                  str((body or {}).get("answer"))[:120])

            req = fake.last_request()
            check("Anthropic 协议：打的是 /messages",
                  req.get("path", "").endswith("/messages"), req.get("path", ""))
            an_body = req.get("body") or {}
            an_msgs = an_body.get("messages") or []
            check("Anthropic 协议：system 是独立字段",
                  bool(str(an_body.get("system") or "").strip()), str(an_body.get("system"))[:80])
            check("Anthropic 协议：请求里有 tool_result 块",
                  any(b.get("type") == "tool_result"
                      for m in an_msgs for b in (m.get("content") or [])
                      if isinstance(b, dict)),
                  str(an_msgs)[:220])
            check("Anthropic 协议：tools 用的是 input_schema",
                  all("input_schema" in t for t in (an_body.get("tools") or [])) and
                  bool(an_body.get("tools")),
                  str(an_body.get("tools"))[:160])
            check("Anthropic 协议：x-api-key 用的是服务端配的 key",
                  (req.get("headers") or {}).get("x-api-key") == SERVER_MODEL_KEY,
                  str((req.get("headers") or {}).get("x-api-key")))

            # ---- 浏览器临时覆盖
            fake.reset([{"text": "用浏览器这份配置也能聊。"}])
            status, body = _chat(
                client,
                admin,
                "随便说点什么",
                provider="openai",
                model="browser-model",
                api_key=BROWSER_MODEL_KEY,
            )
            check("浏览器临时覆盖：聊天返回 200", status == 200, f"HTTP {status} {str(body)[:160]}")
            req = fake.last_request()
            check("浏览器临时覆盖：用的是浏览器那份 key",
                  (req.get("headers") or {}).get("authorization") == f"Bearer {BROWSER_MODEL_KEY}",
                  str((req.get("headers") or {}).get("authorization")))
            check("浏览器临时覆盖：用的是浏览器指定的模型",
                  (req.get("body") or {}).get("model") == "browser-model",
                  str((req.get("body") or {}).get("model")))
            check("浏览器临时覆盖：服务端的 key 没被发出去",
                  SERVER_MODEL_KEY not in json.dumps(req.get("body") or {}, ensure_ascii=False))

            # ---- 换接口地址却没给 key：服务端的 key **不得**跟着走
            # 否则任何登录用户都能让服务端把自己的 key 送到他指定的地址去。
            before_count = len(fake.requests)
            status, body = _chat(
                client, admin, "换个地址试试", base_url="http://127.0.0.1:9/v1"
            )
            check("换了接口地址又没给 Key：请求被拒",
                  status == 422, f"HTTP {status} {str(body)[:160]}")
            check("被拒之后模型服务一次请求都没收到",
                  len(fake.requests) == before_count,
                  f"{before_count} -> {len(fake.requests)}")
            check("拒绝时给的是能看懂的提示",
                  "Key" in str((body or {}).get("message") or ""), str(body)[:160])

            # ---- 普通用户
            token = _register(client, "aitester", USER_PASSWORD)
            check("能注册一个普通用户", bool(token))
            user = {"Authorization": f"Bearer {token}"}

            status, body = call(client, "GET", "/api/ai/config", headers=user)
            check("普通用户能读配置", status == 200, f"HTTP {status} {str(body)[:140]}")
            check("普通用户看到的是 allowed 的（不可配置）",
                  (body or {}).get("enabled") is True, str(body)[:140])
            check("普通用户不能改服务端默认配置",
                  _put_config(client, user, model="hacked")[0] == 403)

            fake.reset([{"text": "普通用户也能问。"}])
            status, body = _chat(client, user, "你好")
            check("普通用户能聊天（产品定死：所有登录用户都能用）",
                  status == 200, f"HTTP {status} {str(body)[:160]}")
            check("普通用户聊天走的是服务端默认 key",
                  _sent_key(fake.last_request()) == SERVER_MODEL_KEY,
                  _sent_key(fake.last_request()))

            # ---- 写工具默认关闭
            status, body = call(client, "GET", "/api/ai/tools", headers=user)
            tool_names = [t.get("name") for t in ((body or {}).get("tools") or [])]
            check("默认不给写工具",
                  "gis_edit_start" not in tool_names and "gis_query_point" in tool_names,
                  str(tool_names)[:160])
            check("管理员开了 allow_write 之后写工具才出现",
                  _put_config(client, admin, allow_write=True)[0] == 200)
            status, body = call(client, "GET", "/api/ai/tools", headers=user)
            tool_names = [t.get("name") for t in ((body or {}).get("tools") or [])]
            check("开了之后 gis_edit_start 出现了", "gis_edit_start" in tool_names,
                  str(tool_names)[:160])
            check("普通用户不能开 allow_write",
                  _put_config(client, user, allow_write=False)[0] == 403)
            _put_config(client, admin, allow_write=False)

            # ---- 租户隔离
            sample = ROOT / "tests" / "sample_data" / "shared" / "example" / "boundary.geojson"
            token_a = _register(client, "alice", USER_PASSWORD)
            token_b = _register(client, "bob", USER_PASSWORD)
            alice = {"Authorization": f"Bearer {token_a}"}
            bob = {"Authorization": f"Bearer {token_b}"}
            check("两个用户都注册成功", bool(token_a) and bool(token_b))

            status, body = call(
                client, "GET", "/api/auth/me", headers=alice
            )
            principal = (body or {}).get("principal") or {}
            alice_id = str(principal.get("user_id") or "")
            check("能拿到 alice 的 user_id", bool(alice_id), str(body)[:120])

            with sample.open("rb") as fh:
                status, body = call(
                    client,
                    "POST",
                    "/api/uploads?scope=me",
                    headers=alice,
                    files={"files": ("alice_private.geojson", fh, "application/geo+json")},
                )
            uploaded = (body or {}).get("results") or [{}]
            private_id = str(uploaded[0].get("dataset_id") or "")
            check("alice 能往自己的私有空间上传数据集",
                  status == 200 and bool(private_id), f"HTTP {status} {str(body)[:160]}")

            fake.reset([{"tool": "gis_list_datasets", "args": {}},
                        {"text": "我看到了你有权看的数据。"}])
            status, body = _chat(client, bob, "你都能看到哪些数据集？")
            check("bob 的 AI 列不出 alice 的私有数据集",
                  private_id and private_id not in _steps_text(body),
                  _steps_text(body)[:160])
            check("bob 的 AI 还能看到共享空间的数据集",
                  "example" in _steps_text(body), _steps_text(body)[:160])

            fake.reset([{"tool": "gis_get_dataset", "args": {"dataset_id": private_id}},
                        {"text": "查不到这个数据集。"}])
            status, body = _chat(client, bob, f"看看 {private_id} 这个数据集")
            check("bob 的 AI 查 alice 的私有数据集会报「不存在」",
                  "不存在" in _steps_text(body), _steps_text(body)[:200])
            check("失败的工具步骤标记为失败（不是成功）",
                  all(s.get("ok") is False for s in ((body or {}).get("steps") or [])),
                  str((body or {}).get("steps"))[:200])

            fake.reset([{"tool": "gis_list_datasets", "args": {}},
                        {"text": "我看到了你有权看的数据。"}])
            status, body = _chat(client, alice, "你都能看到哪些数据集？")
            check("alice 的 AI 能看到自己的私有数据集",
                  private_id and private_id in _steps_text(body), _steps_text(body)[:200])

            # ---- key 不外泄
            status, body = call(client, "GET", "/api/ai/config", headers=admin)
            raw = json.dumps(body or {}, ensure_ascii=False)
            check("读配置不会把 key 明文发回前端",
                  SERVER_MODEL_KEY not in raw, raw[:200])
            check("读配置只给 has_key",
                  (body or {}).get("has_key") is True, str(body)[:140])

            fake.reset([{"text": "顺便说一句。"}])
            status, body = _chat(client, admin, "再聊一句")
            raw = json.dumps(body or {}, ensure_ascii=False)
            check("聊天响应里也没有 key 明文", SERVER_MODEL_KEY not in raw, raw[:200])

            # ---- 服务还活着
            status, body = call(client, "GET", "/healthz")
            check("折腾完服务还活着", status == 200, f"HTTP {status}")


# --------------------------------------------------------------------- 4. 免登录模式


def check_anonymous(data_root: Path) -> None:
    """免登录模式：匿名访客一律不许用 AI。"""
    print("\n== 2. 免登录模式：匿名访客一律拒绝 ==")
    with TestServer(
        port=_free_port(8873),
        require_login=False,
        data_root=data_root,
        extra_env={"GISDS_ADMIN_PASSWORD": ADMIN_PASSWORD},
    ) as server:
        with httpx.Client(base_url=server.base, timeout=60.0) as client:
            status, body = call(client, "GET", "/api/ai/config")
            check("匿名访客读配置 -> 403", status == 403, f"HTTP {status} {str(body)[:140]}")
            status, body = _chat(client, {}, "你好")
            check("匿名访客聊天 -> 403", status == 403, f"HTTP {status} {str(body)[:140]}")
            status, body = call(client, "GET", "/api/ai/tools")
            check("匿名访客看工具清单 -> 403", status == 403, f"HTTP {status}")
            status, body = call(client, "GET", "/healthz")
            check("拒绝之后服务还活着", status == 200, f"HTTP {status}")


# --------------------------------------------------------------------- 主流程


def main() -> int:
    print("== 网页 AI 助手自检 ==")

    data_root = ROOT / "tests" / "_workspace" / "ai_check_data"
    if data_root.exists():
        shutil.rmtree(data_root, ignore_errors=True)
    shared = data_root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (data_root / "users").mkdir(parents=True, exist_ok=True)
    source = ROOT / "tests" / "sample_data" / "shared" / "example"
    if source.is_dir():
        shutil.copytree(source, shared / "example", dirs_exist_ok=True)

    fake = FakeLLM()
    fake.start()
    try:
        check_pure()
        check_e2e(fake, data_root)
        check_anonymous(data_root)
    finally:
        fake.stop()
        shutil.rmtree(data_root, ignore_errors=True)

    total = checked
    check(
        f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
        total == EXPECTED_CHECKS,
        f"实际 {total}",
    )

    print(f"\n== 结果：{checked - len(fails)} 通过 / {len(fails)} 失败 ==")
    for item in fails:
        print(f"  ! {item}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
