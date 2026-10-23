"""MCP 面板工具自检（属性表 / 统计 / 选择 / 编辑 / 书签）。

只测新加的那批工具，原有的 15 个由 check_mcp.py 负责。

编辑功能会**写回数据文件**，所以这里用临时数据目录跑，
绝不碰项目里的样例数据。

独立运行::

    run-qgis-python.bat tests/check_mcp_panels.py
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 临时数据目录：编辑会写回文件，样例数据不能被改
_TMP = Path(tempfile.mkdtemp(prefix="gisds_mcp_panels_"))
os.environ["GISDS_DATA_ROOT"] = str(_TMP)
os.environ["GISDS_SHARED_ROOT"] = str(_TMP / "shared")
os.environ["GISDS_USERS_ROOT"] = str(_TMP / "users")
os.environ["GISDS_DB_PATH"] = str(_TMP / "gisds.db")

_SAMPLE = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
if _SAMPLE.is_dir():
    shutil.copytree(_SAMPLE, _TMP / "shared" / "example")

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 34 = 构建 14（12 个新工具 + 工具总数 + 能取到矢量图层）
#            + 属性表 3 + 字段统计 2 + 选择 3 + 编辑 9 + 书签 3。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
# （取不到矢量图层那条会提前 return，那条路径也走同一道钉子，所以照样红。）
EXPECTED_CHECKS = 34

NEW_TOOLS = (
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
)


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


async def _call(server, tool: str, **kwargs) -> dict:
    result = await _maybe_await(server.call_tool(tool, kwargs))
    return json.loads(_tool_text(result))


async def main() -> int:
    from gisds.mcp.server import build_server

    print("== 构建 MCP Server ==")
    server = build_server()
    tools = await _maybe_await(server.list_tools())
    names = [t.name for t in tools]
    for required in NEW_TOOLS:
        check(f"提供工具 {required}", required in names)
    check("工具总数为 27", len(names) == 27, str(len(names)))

    layers = await _call(server, "gis_list_layers", kind="vector")
    refs = [i["ref"] for i in layers.get("layers", [])]
    check("能取到矢量图层", bool(refs), str(refs))
    if not refs:
        check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
              _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
        print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
        return 1
    ref = refs[0]

    print("\n== 属性表 ==")
    table = await _call(server, "gis_attribute_table", ref=ref, limit=10)
    check("返回字段定义", len(table.get("fields", [])) >= 1, str(table.get("fields")))
    check("返回要素行", table.get("count", 0) >= 1, str(table.get("count")))
    check("行里有 fid", all("fid" in r for r in table.get("rows", [])), str(table.get("rows"))[:120])

    print("\n== 字段统计 ==")
    stats = await _call(server, "gis_field_stats", ref=ref)
    check("返回逐字段统计", len(stats.get("fields", [])) >= 1, str(stats.get("fields"))[:160])
    first = (stats.get("fields") or [{}])[0]
    check("统计里有计数", "count" in first, str(first))

    print("\n== 选择 ==")
    sel = await _call(server, "gis_select_features", ref=ref, mode="all")
    check("全选返回 fid 列表", sel.get("count", 0) >= 1, str(sel.get("count")))
    fids = sel.get("fids") or []
    sel2 = await _call(server, "gis_select_features", ref=ref, expression='"fid" > 0')
    check("按表达式选择有结果", "fids" in sel2, str(sel2)[:160])
    bad = await _call(server, "gis_select_features", ref=ref)
    check("没给条件时报错", bad.get("error") is not None, str(bad)[:160])

    print("\n== 编辑 ==")
    started = await _call(server, "gis_edit_start", ref=ref)
    check("进入编辑模式", started.get("is_editing") is True, str(started))

    fid = fids[0]
    fields = [f["name"] for f in table.get("fields", [])]
    target = fields[0]
    upd = await _call(server, "gis_edit_feature", ref=ref, fid=fid, attributes={target: "自检写入"})
    check("改属性成功", not upd.get("error"), str(upd)[:160])

    bad_field = await _call(server, "gis_edit_feature", ref=ref, fid=fid, attributes={"没有这个字段": 1})
    check("改不存在的字段报错", bad_field.get("error") is not None, str(bad_field)[:160])

    geom = {
        "type": "Polygon",
        "coordinates": [[[113.0, 40.0], [113.1, 40.0], [113.1, 40.1], [113.0, 40.1], [113.0, 40.0]]],
    }
    added = await _call(server, "gis_add_feature", ref=ref, geometry=geom, attributes={})
    check("新增要素成功", not added.get("error") and added.get("fid") is not None, str(added)[:160])

    after = await _call(server, "gis_attribute_table", ref=ref, limit=100)
    check("要素数加 1", after.get("count") == table.get("count", 0) + 1, str(after.get("count")))

    if added.get("fid") is not None:
        deleted = await _call(server, "gis_delete_features", ref=ref, fids=[added["fid"]])
        check("删除要素成功", deleted.get("deleted") == 1, str(deleted)[:160])
    else:
        # 新增没拿到 fid -> 这条断言**判失败**，而不是整段跳过
        # （跳过会让断言总数变少，钉子就看不出少了哪几条）
        check("删除要素成功", False, "新增要素没返回 fid")

    calc = await _call(server, "gis_field_calculator", ref=ref, field=target, expression="1")
    check("字段计算器成功", calc.get("updated", 0) >= 1, str(calc)[:160])

    bad_calc = await _call(server, "gis_field_calculator", ref=ref, field=target, expression="1 +")
    check("非法表达式报错", bad_calc.get("error") is not None, str(bad_calc)[:160])

    committed = await _call(server, "gis_edit_commit", ref=ref)
    check("保存编辑成功", committed.get("is_editing") is False, str(committed))

    print("\n== 书签 ==")
    listed = await _call(server, "gis_list_bookmarks")
    check("列书签成功", "bookmarks" in listed, str(listed)[:160])
    created = await _call(server, "gis_add_bookmark", name="MCP 自检书签", extent=[112.0, 39.0, 112.1, 39.1])
    check("新建书签成功", created.get("name") == "MCP 自检书签", str(created)[:160])
    listed = await _call(server, "gis_list_bookmarks")
    check("书签已列出", any(b.get("name") == "MCP 自检书签" for b in listed.get("bookmarks", [])),
          str(listed.get("bookmarks"))[:160])

    shutil.rmtree(_TMP, ignore_errors=True)
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
