"""MCP Server 自检（进程内调用，覆盖全部 15 个工具）。

独立运行::

    run-qgis-python.bat tests/check_mcp.py
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["GISDS_DATA_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data")
os.environ["GISDS_WORKSPACE_ROOT"] = str(PROJECT_ROOT / "tests" / "_workspace")
# 用户库也隔离：自检不碰生产库 data/gisds.db
os.environ["GISDS_DB_PATH"] = str(PROJECT_ROOT / "tests" / "_workspace" / "mcp_check.db")

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 67 = 构建 1 + 工具清单 29 + 13 个工具各 1~4 条（合计 33）
#            + 跑算法 3 轮 × 3 条（9）+ 错误处理 2。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 67


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _tool_text(result) -> str:
    """把 MCP 调用结果里的文本内容拼起来。"""
    chunks = []
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    for item in content or []:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            chunks.append(text)
    return "\n".join(chunks)


async def main() -> int:
    from gisds.mcp.server import build_server

    print("== 构建 MCP Server ==")
    server = build_server()
    check("server 构建成功", server is not None)

    print("\n== 工具清单 ==")
    tools = await _maybe_await(server.list_tools())
    names = [t.name for t in tools]
    print(f"    共 {len(names)} 个工具：{', '.join(names)}")
    for required in (
        "gis_list_datasets",
        "gis_get_dataset",
        "gis_list_layers",
        "gis_rescan",
        "gis_query_point",
        "gis_query_bbox",
        "gis_layer_area",
        "gis_layer_histogram",
        "gis_list_features",
        "gis_vector_stats",
        "gis_point_hit",
        "gis_render_map",
        "gis_list_algorithms",
        "gis_describe_algorithm",
        "gis_run_algorithm",
        "gis_attribute_table",
        "gis_field_stats",
        "gis_select_features",
        "gis_edit_start",
        "gis_edit_commit",
        "gis_edit_rollback",
        "gis_edit_feature",
        "gis_add_feature",
        "gis_delete_features",
        "gis_field_calculator",
        "gis_list_bookmarks",
        "gis_add_bookmark",
    ):
        check(f"提供工具 {required}", required in names)
    check("工具数量为 27", len(names) == 27, f"实际 {len(names)}")

    descriptions = {t.name: (t.description or "") for t in tools}
    check("每个工具都有说明", all(descriptions.values()))

    print("\n== 调用：gis_list_datasets ==")
    result = await _maybe_await(server.call_tool("gis_list_datasets", {}))
    text = _tool_text(result)
    check("返回非空文本", len(text) > 50, f"实际 {len(text)} 字符")
    check("列出示例数据集", "示例数据" in text, text[:120])
    print("    " + text.replace("\n", "\n    ")[:300])

    print("\n== 调用：gis_list_layers ==")
    result = await _maybe_await(server.call_tool("gis_list_layers", {}))
    payload = json.loads(_tool_text(result))
    refs = [i["ref"] for i in payload.get("layers", [])]
    check("返回图层清单", len(refs) >= 3, str(refs))
    check("含 example:classes", "example:classes" in refs, str(refs))
    check("含 example:boundary", "example:boundary" in refs, str(refs))

    print("\n== 调用：gis_query_point ==")
    result = await _maybe_await(
        server.call_tool(
            "gis_query_point",
            {"dataset_id": "example", "layer_id": "classes", "lon": 112.48, "lat": 39.685},
        )
    )
    text = _tool_text(result)
    check("返回 JSON", text.strip().startswith("{"), text[:80])
    payload = json.loads(text)
    check("取到 类别六", payload.get("class_name") == "类别六", str(payload.get("class_name")))
    check("带数值", payload.get("value") is not None)

    print("\n== 调用：gis_query_bbox ==")
    result = await _maybe_await(
        server.call_tool("gis_query_bbox", {
            "dataset_id": "example", "layer_id": "classes",
            "minx": 112.40, "miny": 39.62, "maxx": 112.56, "maxy": 39.75,
        })
    )
    payload = json.loads(_tool_text(result))
    check("范围统计返回 6 类", len(payload.get("classes", [])) == 6, str(len(payload.get("classes", []))))
    check("面积非零", (payload.get("area_m2") or 0) > 0)

    print("\n== 调用：gis_layer_area ==")
    result = await _maybe_await(
        server.call_tool("gis_layer_area", {"dataset_id": "example", "layer_id": "classes"})
    )
    payload = json.loads(_tool_text(result))
    check("面积统计返回 6 类", len(payload.get("classes", [])) == 6, str(len(payload.get("classes", []))))

    print("\n== 调用：gis_layer_histogram ==")
    result = await _maybe_await(
        server.call_tool(
            "gis_layer_histogram", {"dataset_id": "example", "layer_id": "surface", "bins": 8}
        )
    )
    payload = json.loads(_tool_text(result))
    check("直方图有 8 个 bin", len(payload.get("bins", [])) == 8, str(len(payload.get("bins", []))))

    print("\n== 调用：gis_list_features ==")
    result = await _maybe_await(
        server.call_tool("gis_list_features", {"dataset_id": "example", "vector_id": "boundary"})
    )
    payload = json.loads(_tool_text(result))
    check("返回 FeatureCollection", payload.get("type") == "FeatureCollection")
    check("取到 1 个要素", payload.get("count") == 1)

    print("\n== 调用：gis_vector_stats ==")
    result = await _maybe_await(
        server.call_tool("gis_vector_stats", {"dataset_id": "example", "vector_id": "boundary"})
    )
    payload = json.loads(_tool_text(result))
    check("统计含字段", len(payload.get("fields", [])) >= 1)

    print("\n== 调用：gis_point_hit ==")
    result = await _maybe_await(
        server.call_tool(
            "gis_point_hit", {"dataset_id": "example", "vector_id": "boundary", "lon": 112.48, "lat": 39.685}
        )
    )
    payload = json.loads(_tool_text(result))
    check("点命中 1 个要素", payload.get("count") == 1, str(payload.get("count")))

    print("\n== 调用：gis_render_map ==")
    result = await _maybe_await(
        server.call_tool(
            "gis_render_map",
            {"dataset_id": "example", "layer_id": "classes", "width": 320},
        )
    )
    content = getattr(result, "content", None)
    check("返回图片内容块", bool(content) and len(content) > 0, str(type(result)))
    first = content[0] if content else None
    kind = getattr(first, "type", None)
    check("内容块类型为 image", kind == "image", f"实际 {kind}")
    data = getattr(first, "data", None)
    # MCP 在结果转换时会把图片编码成 base64 字符串
    if isinstance(data, str):
        import base64

        try:
            data = base64.b64decode(data)
        except Exception:
            pass
    check("图片数据解码后是 PNG", isinstance(data, (bytes, bytearray)) and bytes(data[:4]) == b"\x89PNG", str(type(data)))
    if isinstance(data, (bytes, bytearray)):
        print(f"    PNG {len(data) / 1024:.1f} KB")

    print("\n== 调用：gis_list_algorithms ==")
    result = await _maybe_await(server.call_tool("gis_list_algorithms", {"query": "buffer"}))
    payload = json.loads(_tool_text(result))
    check("返回算法总数", payload.get("total") == 440, str(payload.get("total")))
    check("搜索命中 buffer", payload.get("count", 0) > 0, str(payload.get("count")))
    ids = [a["id"] for a in payload.get("algorithms", [])]
    check("含 native:buffer", "native:buffer" in ids, str(ids[:5]))

    print("\n== 调用：gis_describe_algorithm ==")
    result = await _maybe_await(
        server.call_tool("gis_describe_algorithm", {"algorithm_id": "native:buffer"})
    )
    payload = json.loads(_tool_text(result))
    check("返回参数定义", len(payload.get("parameters", [])) > 0)
    names_ = {p["name"]: p for p in payload.get("parameters", [])}
    check("含 INPUT 参数", "INPUT" in names_)
    cap = names_.get("END_CAP_STYLE", {})
    opts = cap.get("options") or []
    vals = cap.get("option_values") or []
    # 显示名随 QGIS 翻译包的语言变化，只校验结构，不写死中英文
    check("枚举参数带 options 与 option_values",
          bool(opts) and len(vals) == len(opts) and len(opts) > 0,
          str(cap))
    check("带可用图层清单", len(payload.get("available_layers", [])) >= 3)

    print("\n== 调用：gis_run_algorithm ==")
    # 三种取值写法都要能跑通：整数下标 / 接口给出的显示名 / QGIS 英文原名
    for label, style in (("下标", vals[1] if len(vals) > 1 else 1),
                         ("显示名", opts[1] if len(opts) > 1 else 1),
                         ("英文原名", "Flat")):
        result = await _maybe_await(
            server.call_tool(
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
        )
        text = _tool_text(result)
        payload = json.loads(text)
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

    print("\n== 错误处理 ==")
    result = await _maybe_await(
        server.call_tool(
            "gis_query_point",
            {"dataset_id": "nope", "layer_id": "nope", "lon": 0, "lat": 0},
        )
    )
    text = _tool_text(result)
    check("不存在的图层返回错误说明而非崩溃", "not_found" in text or "不存在" in text, text[:160])
    print(f"    {text.strip()[:120]}")

    result = await _maybe_await(
        server.call_tool("gis_run_algorithm", {"algorithm": "no:such", "parameters": {}})
    )
    text = _tool_text(result)
    check("不存在的算法返回错误说明", "not_found" in text or "不存在" in text, text[:160])

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
