"""MCP stdio 端到端测试：真的把 mcp.bat 拉起来当子进程，用 MCP 客户端连上去。

这正是 OpenClaw（龙虾）挂载本服务的方式，因此这个测试通过 = 龙虾能正常调用。

用法::

    run-qgis-python.bat tests/check_mcp_stdio.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 30 = 握手 + 工具清单 8 + 6 个工具各 1~2 条（合计 12）
#            + 跑算法 3 轮 × 3 条（9）。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 30


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def _text(result) -> str:
    chunks = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks)


async def main() -> int:
    launcher = PROJECT_ROOT / "mcp.bat"
    print(f"== MCP stdio 端到端 ==\n   启动：{launcher}")

    env = dict(os.environ)
    env["GISDS_DATA_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data")
    env["GISDS_WORKSPACE_ROOT"] = str(PROJECT_ROOT / "tests" / "_workspace")
    # 用户库也隔离：自检不碰生产库 data/gisds.db
    env["GISDS_DB_PATH"] = str(PROJECT_ROOT / "tests" / "_workspace" / "mcp_stdio_check.db")

    params = StdioServerParameters(
        command="cmd.exe",
        args=["/c", str(launcher)],
        env=env,
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            check("MCP 握手成功", init is not None)
            print(f"    服务端：{getattr(init, 'serverInfo', None)}")

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"    工具 {len(names)} 个：{', '.join(names)}")
            check("工具数为 27", len(names) == 27, str(len(names)))
            for required in (
                "gis_query_point",
                "gis_render_map",
                "gis_list_layers",
                "gis_list_algorithms",
                "gis_describe_algorithm",
                "gis_run_algorithm",
            ):
                check(f"含 {required}", required in names)

            print("\n-- 调用 gis_list_datasets --")
            res = await session.call_tool("gis_list_datasets", {})
            text = _text(res)
            check("返回非空", len(text) > 30, str(len(text)))
            check("列出示例数据集", "示例数据" in text, text[:120])
            print("    " + text.replace("\n", "\n    ")[:300])

            print("\n-- 调用 gis_list_layers --")
            res = await session.call_tool("gis_list_layers", {})
            payload = json.loads(_text(res))
            refs = [i["ref"] for i in payload.get("layers", [])]
            check("含 example:classes", "example:classes" in refs, str(refs))

            print("\n-- 调用 gis_query_point --")
            res = await session.call_tool(
                "gis_query_point",
                {"dataset_id": "example", "layer_id": "classes", "lon": 112.48, "lat": 39.685},
            )
            text = _text(res)
            check("返回 JSON", text.strip().startswith("{"), text[:80])
            payload = json.loads(text)
            check("取到 类别六", payload.get("class_name") == "类别六", str(payload.get("class_name")))

            print("\n-- 调用 gis_query_bbox --")
            res = await session.call_tool(
                "gis_query_bbox",
                {
                    "dataset_id": "example",
                    "layer_id": "classes",
                    "minx": 112.40,
                    "miny": 39.62,
                    "maxx": 112.56,
                    "maxy": 39.75,
                },
            )
            payload = json.loads(_text(res))
            check("返回 6 类", len(payload.get("classes", [])) == 6)
            check("面积有效", (payload.get("area_m2") or 0) > 0)

            print("\n-- 调用 gis_render_map（图片） --")
            res = await session.call_tool(
                "gis_render_map",
                {"dataset_id": "example", "layer_id": "classes", "width": 320},
            )
            content = getattr(res, "content", None) or []
            images = [c for c in content if getattr(c, "type", "") == "image"]
            check("返回图片块", len(images) == 1, str(len(images)))
            # 「图片是 PNG」这条**无条件执行**：没拿到图片就让断言**失败**，
            # 而不是整段跳过（跳过会让断言总数变少，钉子就看不出少了哪几条）。
            data = images[0].data if images else b""
            if isinstance(data, str):
                import base64

                data = base64.b64decode(data)
            check("图片是 PNG", bytes(data[:4]) == b"\x89PNG")
            print(f"    PNG {len(data) / 1024:.1f} KB")

            print("\n-- 调用 gis_list_algorithms --")
            res = await session.call_tool("gis_list_algorithms", {"query": "buffer"})
            payload = json.loads(_text(res))
            check("算法总数 440", payload.get("total") == 440, str(payload.get("total")))
            ids = [a["id"] for a in payload.get("algorithms", [])]
            check("搜索命中 native:buffer", "native:buffer" in ids, str(ids[:5]))

            print("\n-- 调用 gis_describe_algorithm --")
            res = await session.call_tool(
                "gis_describe_algorithm", {"algorithm_id": "native:buffer"}
            )
            payload = json.loads(_text(res))
            check("返回参数定义", len(payload.get("parameters", [])) > 0)
            params_by_name = {p["name"]: p for p in payload.get("parameters", [])}
            cap = params_by_name.get("END_CAP_STYLE", {})
            opts = cap.get("options") or []
            vals = cap.get("option_values") or []
            # 显示名随 QGIS 翻译包的语言变化，只校验结构，不写死中英文
            check("枚举参数带 options / option_values",
                  bool(opts) and len(vals) == len(opts) and len(opts) > 0,
                  str(cap))

            print("\n-- 调用 gis_run_algorithm --")
            # 三种取值写法都要能跑通：整数下标 / 接口给出的显示名 / QGIS 英文原名
            for label, style in (("下标", vals[1] if len(vals) > 1 else 1),
                                 ("显示名", opts[1] if len(opts) > 1 else 1),
                                 ("英文原名", "Flat")):
                res = await session.call_tool(
                    "gis_run_algorithm",
                    {
                        "algorithm": "native:buffer",
                        "parameters": {
                            "INPUT": "example:boundary",
                            "DISTANCE": 0.01,
                            "END_CAP_STYLE": style,
                            "JOIN_STYLE": 1,
                        },
                    },
                )
                payload = json.loads(_text(res))
                check(f"算法执行成功（枚举取值：{label}）", not payload.get("error"),
                      str(payload.get("error"))[:200])
                outputs = payload.get("outputs") or {}
                check(f"产出 OUTPUT（{label}）", "OUTPUT" in outputs, str(outputs)[:200])
                if "OUTPUT" in outputs:
                    path = Path(outputs["OUTPUT"].get("path", ""))
                    check(f"输出文件已落盘（{label}）", path.exists(), str(path))
                else:
                    # 没有 OUTPUT -> 这条断言**判失败**，而不是整段跳过
                    # （跳过会让断言总数变少，钉子就看不出少了哪几条）
                    check(f"输出文件已落盘（{label}）", False, "没有 OUTPUT 输出")

    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    os._exit(code)
