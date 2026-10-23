"""面板（QGIS 桌面版 dock 面板）自检。

覆盖网页版右栏各面板对应的 REST 端点：
- 属性表 / 统计 / 信息 / 日志
- 按表达式选择、按空间关系选择
- 空间书签的增删查
- 要素编辑全流程：进入编辑 → 改属性 → 增要素 → 删要素 → 字段计算器 → 保存 → 放弃
- 空间权限：别人的私有空间不能编辑

独立运行::

    run-qgis-python.bat tests/check_panels.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 用临时数据目录：编辑功能会写回数据文件，绝不能碰项目里的样例数据
_TMP = Path(tempfile.mkdtemp(prefix="gisds_panels_"))
_DATA = _TMP / "data"
os.environ["GISDS_DATA_ROOT"] = str(_DATA)
os.environ["GISDS_SHARED_ROOT"] = str(_DATA / "shared")
os.environ["GISDS_USERS_ROOT"] = str(_DATA / "users")
os.environ["GISDS_DB_PATH"] = str(_DATA / "gisds.db")
os.environ["GISDS_REQUIRE_LOGIN"] = "1"
os.environ["GISDS_ALLOW_REGISTRATION"] = "1"

# 共享空间放一份样例数据，跟正式服务的数据结构一致
_SAMPLE = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
if _SAMPLE.is_dir():
    shutil.copytree(_SAMPLE, _DATA / "shared" / "example")

from fastapi.testclient import TestClient  # noqa: E402

from gisds.api.app import create_app  # noqa: E402

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 80 = 账号 2 + 属性表 11 + 统计 10 + 信息 4 + 选择 13 + 日志 5
#            + 空间书签 10 + 要素编辑 19 + 空间权限 3 + 面板能力 3。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 80

# 两个相邻的小多边形，用来测空间关系选择
POLY_A = {
    "type": "Polygon",
    "coordinates": [[[112.00, 39.00], [112.10, 39.00], [112.10, 39.10], [112.00, 39.10], [112.00, 39.00]]],
}
POLY_B = {
    "type": "Polygon",
    "coordinates": [[[112.40, 39.40], [112.50, 39.40], [112.50, 39.50], [112.40, 39.50], [112.40, 39.40]]],
}
FIXTURE = (
    '{"type":"FeatureCollection","features":['
    '{"type":"Feature","properties":{"名称":"甲","面积":100},'
    '"geometry":{"type":"Polygon","coordinates":[[[112.00,39.00],[112.10,39.00],'
    '[112.10,39.10],[112.00,39.10],[112.00,39.00]]]}},'
    '{"type":"Feature","properties":{"名称":"乙","面积":250},'
    '"geometry":{"type":"Polygon","coordinates":[[[112.40,39.40],[112.50,39.40],'
    '[112.50,39.50],[112.40,39.50],[112.40,39.40]]]}}'
    "]}"
)


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def upload(client, headers, name: str, body: str, scope: str = "me") -> str:
    """上传一个 GeoJSON，返回图层引用（数据集:图层）。"""
    r = client.post(
        f"/api/uploads?scope={scope}",
        files={"files": (name, body.encode("utf-8"), "application/geo+json")},
        headers=headers,
    )
    if r.status_code != 200:
        raise RuntimeError(f"上传失败：{r.status_code} {r.text[:200]}")
    dataset_id = r.json()["results"][0]["dataset_id"]
    layers = client.get("/api/layers", headers=headers).json()["layers"]
    for item in layers:
        if item["ref"].startswith(f"{dataset_id}:"):
            return item["ref"]
    raise RuntimeError(f"上传后找不到图层：{dataset_id}")


def main() -> int:
    app = create_app()
    with TestClient(app) as client:
        # ---------------------------------------------------------------- 登录
        print("== 账号 ==")
        r = client.post("/api/auth/register", json={"username": "paneler", "password": "panel12345"})
        check("注册成功", r.status_code == 201, r.text[:200])
        mine = {"Authorization": f"Bearer {r.json()['token']}"}

        r = client.post("/api/auth/register", json={"username": "other", "password": "other12345"})
        other = {"Authorization": f"Bearer {r.json()['token']}"}

        ref = upload(client, mine, "panels.geojson", FIXTURE)
        check("上传后拿到图层引用", ":" in ref, ref)

        # ---------------------------------------------------------------- 属性表
        print("\n== 属性表 ==")
        r = client.get(f"/api/panels/table?ref={ref}", headers=mine)
        check("GET /api/panels/table -> 200", r.status_code == 200, r.text[:200])
        table = r.json()
        check("属性表给出字段定义", len(table["fields"]) >= 2, str(table.get("fields")))
        check("属性表带中文别名", any(f["alias"] == "名称" for f in table["fields"]), str(table["fields"]))
        check("属性表有两行要素", table["count"] == 2, str(table.get("count")))
        check("属性表给出 fid", all("fid" in row for row in table["rows"]), str(table["rows"]))
        check("属性表给出单元格值", table["rows"][0]["cells"].get("面积") in (100, "100"), str(table["rows"]))
        check("属性表带总行数", table["total"] == 2, str(table.get("total")))
        check("属性表报告未在编辑", table["is_editing"] is False, str(table.get("is_editing")))

        r = client.get(f"/api/panels/table?ref={ref}&where={chr(37)}22名称%22%20=%20%27甲%27", headers=mine)
        check("属性表支持表达式过滤", r.json().get("count") == 1, r.text[:200])

        r = client.get(f"/api/panels/table?ref={ref}&limit=1&offset=1", headers=mine)
        check("属性表支持分页", r.json().get("count") == 1 and r.json().get("offset") == 1, r.text[:200])

        r = client.get(f"/api/panels/table?ref=nosuch:layer", headers=mine)
        check("属性表对不存在的图层报 404", r.status_code == 404, str(r.status_code))

        # ---------------------------------------------------------------- 统计
        print("\n== 统计 ==")
        r = client.get(f"/api/panels/statistics?ref={ref}", headers=mine)
        check("GET /api/panels/statistics -> 200", r.status_code == 200, r.text[:200])
        stats = r.json()
        names = [f["name"] for f in stats["fields"]]
        check("统计覆盖全部字段", "面积" in names and "名称" in names, str(names))
        area = next((f for f in stats["fields"] if f["name"] == "面积"), {})
        check("数值字段给出最小值", area.get("min") == 100, str(area))
        check("数值字段给出最大值", area.get("max") == 250, str(area))
        check("数值字段给出均值", area.get("mean") == 175, str(area))
        check("数值字段给出中位数", area.get("median") == 175, str(area))
        check("数值字段给出求和", area.get("sum") == 350, str(area))
        name_field = next((f for f in stats["fields"] if f["name"] == "名称"), {})
        check("文本字段给出唯一值数", name_field.get("unique") == 2, str(name_field))
        check("文本字段给出高频值", len(name_field.get("top") or []) == 2, str(name_field.get("top")))

        r = client.get(f"/api/panels/statistics?ref={ref}&field=面积", headers=mine)
        check("统计可以只算单个字段", len(r.json()["fields"]) == 1, r.text[:200])

        # ---------------------------------------------------------------- 信息
        print("\n== 信息 ==")
        r = client.get(f"/api/panels/overview?ref={ref}", headers=mine)
        check("GET /api/panels/overview -> 200", r.status_code == 200, r.text[:200])
        check("信息面板给出要素数", r.json().get("feature_count") == 2, r.text[:200])
        check("信息面板给出字段数", r.json().get("field_count") == 2, r.text[:200])
        check("信息面板给出空间", r.json().get("scope", "").startswith("user:"), r.text[:200])

        # ---------------------------------------------------------------- 选择
        print("\n== 选择 ==")
        r = client.post("/api/panels/select", json={"ref": ref, "expression": '"面积" > 200'}, headers=mine)
        check("按表达式选择 -> 200", r.status_code == 200, r.text[:200])
        check("按表达式选出 1 个", r.json().get("count") == 1, r.text[:200])

        r = client.post("/api/panels/select", json={"ref": ref, "expression": '"面积" >'}, headers=mine)
        check("非法表达式被挡下", r.status_code == 422, str(r.status_code))

        r = client.post(
            "/api/panels/select",
            json={"ref": ref, "geometry": POLY_A, "predicate": "intersects"},
            headers=mine,
        )
        check("按空间关系选择 -> 200", r.status_code == 200, r.text[:200])
        check("按空间关系选出 1 个", r.json().get("count") == 1, r.text[:200])

        r = client.post(
            "/api/panels/select",
            json={"ref": ref, "geometry": POLY_B, "predicate": "intersects"},
            headers=mine,
        )
        check("空间关系选的是另一个要素", r.json().get("count") == 1, r.text[:200])
        first_a = client.post(
            "/api/panels/select", json={"ref": ref, "geometry": POLY_A, "predicate": "intersects"}, headers=mine
        ).json().get("fids") or []
        first_b = client.post(
            "/api/panels/select", json={"ref": ref, "geometry": POLY_B, "predicate": "intersects"}, headers=mine
        ).json().get("fids") or []
        check("两个多边形选出的要素不同", first_a != first_b, f"{first_a} vs {first_b}")

        r = client.post(
            "/api/panels/select",
            json={"ref": ref, "geometry": POLY_A, "predicate": "disjoint"},
            headers=mine,
        )
        check("disjoint 选出另一个", r.json().get("count") == 1, r.text[:200])

        r = client.post("/api/panels/select", json={"ref": ref}, headers=mine)
        check("既没表达式也没几何时报错", r.status_code == 422, str(r.status_code))

        r = client.post("/api/panels/select", json={"ref": ref, "mode": "all"}, headers=mine)
        check("全选返回全部 fid", r.status_code == 200 and r.json().get("count") == 2, r.text[:200])

        # 按空间关系选择：拿另一个图层当参照物
        other_ref = upload(client, mine, "panels_b.geojson", FIXTURE)
        r = client.post(
            "/api/panels/select",
            json={"ref": ref, "target_ref": other_ref, "predicate": "intersects"},
            headers=mine,
        )
        check("按图层选 -> 200", r.status_code == 200, r.text[:200])
        check("按图层选命中 2 个", r.json().get("count") == 2, r.text[:200])

        r = client.post(
            "/api/panels/select",
            json={"ref": ref, "target_ref": other_ref, "predicate": "disjoint"},
            headers=mine,
        )
        check("按图层选 disjoint 命中 0 个", r.json().get("count") == 0, r.text[:200])

        # ---------------------------------------------------------------- 日志
        print("\n== 日志 ==")
        # 日志接口**要登录**（里面有绝对路径、用户名、内部报错），所以带上令牌
        r = client.get("/api/panels/logs?limit=50", headers=mine)
        # 取值要兜住：响应不是日志载荷时**断言失败**就行，
        # 别让脚本自己 KeyError 崩掉（崩在 QGIS 拆库阶段会变成 0xC0000005，
        # 真实原因反而被埋了 —— 这个坑真出过）。
        body = r.json() if r.status_code == 200 else {}
        check("GET /api/panels/logs -> 200", r.status_code == 200, r.text[:200])
        check("日志面板有内容", body.get("count", 0) > 0, r.text[:200])
        check("日志条目带级别", all("level" in e for e in body.get("entries", [])), r.text[:200])
        check("日志条目有可读文本", all(e.get("text") for e in body.get("entries", [])), r.text[:200])

        r = client.get("/api/panels/logs?limit=50")
        check("匿名读日志 -> 401（日志里有绝对路径和用户名）", r.status_code == 401, r.text[:200])

        # ---------------------------------------------------------------- 书签
        print("\n== 空间书签 ==")
        r = client.get("/api/panels/bookmarks", headers=mine)
        check("GET /api/panels/bookmarks -> 200", r.status_code == 200, r.text[:200])
        check("书签默认在自己的空间", r.json().get("scope", "").startswith("user:"), r.text[:200])
        check("初始没有书签", r.json().get("count") == 0, r.text[:200])

        r = client.post(
            "/api/panels/bookmarks",
            json={"name": "测试书签", "extent": [112.0, 39.0, 112.1, 39.1]},
            headers=mine,
        )
        check("POST /api/panels/bookmarks -> 200", r.status_code == 200, r.text[:200])
        check("书签带范围", r.json()["bookmark"]["extent"] == [112.0, 39.0, 112.1, 39.1], r.text[:200])

        r = client.get("/api/panels/bookmarks", headers=mine)
        check("书签列出来了", r.json().get("count") == 1, r.text[:200])

        r = client.get("/api/panels/bookmarks", headers=other)
        check("别人的空间看不到我的书签", r.json().get("count") == 0, r.text[:200])

        r = client.post("/api/panels/bookmarks", json={"name": "", "extent": [1, 2, 3, 4]}, headers=mine)
        check("空书签名被挡下", r.status_code == 422, str(r.status_code))

        r = client.delete("/api/panels/bookmarks?name=测试书签", headers=mine)
        check("DELETE /api/panels/bookmarks -> 200", r.status_code == 200, r.text[:200])
        r = client.get("/api/panels/bookmarks", headers=mine)
        check("书签删掉了", r.json().get("count") == 0, r.text[:200])

        # ---------------------------------------------------------------- 编辑
        print("\n== 要素编辑 ==")
        r = client.post("/api/panels/edit/start", json={"ref": ref}, headers=mine)
        check("进入编辑模式", r.status_code == 200 and r.json().get("is_editing"), r.text[:200])

        r = client.post("/api/panels/edit/start", json={"ref": ref}, headers=mine)
        check("重复进入编辑模式不报错", r.status_code == 200, r.text[:200])

        rows = client.get(f"/api/panels/table?ref={ref}", headers=mine).json()["rows"]
        fid = rows[0]["fid"]

        r = client.patch(
            "/api/panels/edit/feature",
            json={"ref": ref, "fid": fid, "attributes": {"面积": 999}},
            headers=mine,
        )
        check("改属性成功", r.status_code == 200, r.text[:200])
        check("改属性返回字段名", "面积" in (r.json().get("changed") or []), r.text[:200])

        r = client.patch(
            "/api/panels/edit/feature",
            json={"ref": ref, "fid": fid, "attributes": {"没有这个字段": 1}},
            headers=mine,
        )
        check("改不存在的字段被挡下", r.status_code == 422, str(r.status_code))

        r = client.post(
            "/api/panels/edit/feature",
            json={"ref": ref, "geometry": POLY_B, "attributes": {"名称": "丙", "面积": 7}},
            headers=mine,
        )
        check("新增要素成功", r.status_code == 200 and r.json().get("fid") is not None, r.text[:200])
        new_fid = r.json().get("fid")

        r = client.get(f"/api/panels/table?ref={ref}", headers=mine)
        check("要素数变成 3", r.json().get("count") == 3, r.text[:200])
        check("属性表显示编辑状态", r.json().get("is_editing") is True, r.text[:200])

        r = client.request(
            "DELETE",
            "/api/panels/edit/feature",
            json={"ref": ref, "fids": [new_fid]},
            headers=mine,
        )
        check("删除要素成功", r.status_code == 200 and r.json().get("deleted") == 1, r.text[:200])
        r = client.get(f"/api/panels/table?ref={ref}", headers=mine)
        check("要素数回到 2", r.json().get("count") == 2, r.text[:200])

        r = client.post(
            "/api/panels/edit/calculator",
            json={"ref": ref, "field": "面积", "expression": '"面积" * 2'},
            headers=mine,
        )
        check("字段计算器成功", r.status_code == 200 and r.json().get("updated") == 2, r.text[:200])

        r = client.post(
            "/api/panels/edit/calculator",
            json={"ref": ref, "field": "面积", "expression": '"面积" *'},
            headers=mine,
        )
        check("非法表达式被挡下", r.status_code == 422, str(r.status_code))

        r = client.post("/api/panels/edit/commit", json={"ref": ref}, headers=mine)
        check("保存编辑成功", r.status_code == 200 and r.json().get("is_editing") is False, r.text[:200])

        r = client.get(f"/api/panels/table?ref={ref}", headers=mine)
        check("保存后仍在编辑态为否", r.json().get("is_editing") is False, r.text[:200])
        # 甲被改成 999 之后又跑了字段计算器（×2），乙是 250×2
        areas = sorted(int(row["cells"]["面积"]) for row in r.json()["rows"])
        check("保存后属性已写回", areas == [500, 1998], str(areas))

        # 放弃编辑：改了不保存，应该还原到上次保存的状态
        client.post("/api/panels/edit/start", json={"ref": ref}, headers=mine)
        client.patch(
            "/api/panels/edit/feature",
            json={"ref": ref, "fid": fid, "attributes": {"面积": 1}},
            headers=mine,
        )
        r = client.post("/api/panels/edit/rollback", json={"ref": ref}, headers=mine)
        check("放弃编辑成功", r.status_code == 200, r.text[:200])
        r = client.get(f"/api/panels/table?ref={ref}", headers=mine)
        areas = sorted(int(row["cells"]["面积"]) for row in r.json()["rows"])
        check("放弃后属性还原", areas == [500, 1998], str(areas))

        r = client.post("/api/panels/edit/commit", json={"ref": ref}, headers=mine)
        check("不在编辑态时保存报错", r.status_code == 422, str(r.status_code))

        # 未进入编辑态就改属性 -> 拒绝
        r = client.patch(
            "/api/panels/edit/feature",
            json={"ref": ref, "fid": fid, "attributes": {"面积": 5}},
            headers=mine,
        )
        check("未进编辑态不能改属性", r.status_code == 422, str(r.status_code))

        # ---------------------------------------------------------------- 权限
        print("\n== 空间权限 ==")
        r = client.get(f"/api/panels/table?ref={ref}", headers=other)
        check("别人看不到我的私有图层", r.status_code == 404, str(r.status_code))

        r = client.post("/api/panels/edit/start", json={"ref": ref}, headers=other)
        check("别人不能进我的空间编辑", r.status_code in (403, 404), str(r.status_code))

        # 共享空间人人可写
        shared_ref = upload(client, other, "shared_panel.geojson", FIXTURE, scope="shared")
        r = client.post("/api/panels/edit/start", json={"ref": shared_ref}, headers=mine)
        check("共享空间可以编辑", r.status_code == 200, r.text[:200])
        if r.status_code == 200:
            client.post("/api/panels/edit/rollback", json={"ref": shared_ref}, headers=mine)

        # ---------------------------------------------------------------- 能力
        print("\n== 面板能力 ==")
        r = client.get("/api/panels/capabilities", headers=mine)
        check("GET /api/panels/capabilities -> 200", r.status_code == 200, r.text[:200])
        check("能力清单列出面板", "table" in r.json().get("panels", []), r.text[:200])
        check("能力清单带矢量图层", len(r.json().get("vector_layers", [])) >= 1, r.text[:200])

    shutil.rmtree(_TMP, ignore_errors=True)
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001
        # main() 抛异常时**不能**让它一路退到解释器退出 ——
        # 那样会先跑 QGIS 的析构，Windows 上大概率变成 0xC0000005（访问违例），
        # 真实原因（那条 KeyError 之类）反而被埋了。改成：打印回溯 + 退出码 1。
        import traceback

        traceback.print_exc()
        code = 1

    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    os._exit(code)
