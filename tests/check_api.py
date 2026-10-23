"""REST API 自检。

针对中性合成示例数据（tests/sample_data/shared/example）。

独立运行::

    run-qgis-python.bat tests/check_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 指向示例数据；工作区也隔离到 tests/_workspace，避免污染项目 workspace
os.environ["GISDS_DATA_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data")
os.environ["GISDS_SHARED_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data" / "shared")
os.environ["GISDS_USERS_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data" / "users")
os.environ["GISDS_DB_PATH"] = str(PROJECT_ROOT / "tests" / "_workspace" / "gisds.db")
os.environ["GISDS_REQUIRE_LOGIN"] = "0"
# **插件目录指到一个空目录**：下面「算法总数 440」钉的是 **QGIS 自带的**那 440 个。
# 装了插件的话，插件贡献的算法会一起算进来（示例插件贡献 1 个 → 441），
# 那个数就不再固定了。插件算法本身由 check_plugins.py 验。
os.environ["GISDS_PLUGIN_DIR"] = str(PROJECT_ROOT / "tests" / "_workspace" / "no_plugins")

from fastapi.testclient import TestClient  # noqa: E402

from gisds.api.app import create_app  # noqa: E402
from pngutil import color_clusters, pixels  # noqa: E402

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 88 = 健康检查 3 + 存活/就绪 8 + 元信息 6 + 数据集 17 + 栅格查询 14
#            + 矢量查询 9 + 渲染 7 + 综合点查询 3 + 工具箱 18 + 跨域 1 + 文档 2。
# 工具箱那 18 条 = 参数结构 7 + 跑算法 3 轮 × 3 条 + 「下标」那轮多 2 条。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 88


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    app = create_app()
    with TestClient(app) as client:
        print("== 健康检查 ==")
        r = client.get("/healthz")
        check("GET /healthz -> 200", r.status_code == 200, r.text[:200])
        body = r.json()
        check("healthz 报告 ok", body.get("status") == "ok")
        check("healthz 带 QGIS 版本", bool(body.get("qgis", {}).get("qgis_version")))
        print(f"    QGIS {body['qgis']['qgis_version']} | 数据集 {body['datasets']}")

        print("\n== 存活 / 就绪 两个探针 ==")
        # /healthz = 存活（进程在就 200，启动期也是）
        # /readyz = 就绪（没准备好就 503）
        # 之前是一个端点背两个语义 —— 启动期 503 会让配置不当的外部监控
        # 误判成「服务挂了」，反而触发重启循环。
        r = client.get("/readyz")
        check("GET /readyz -> 200（已就绪）", r.status_code == 200, r.text[:200])
        check("readyz 报告 ready", r.json().get("ready") is True, r.text[:120])
        check("healthz 也报告 ready", body.get("ready") is True, str(body.get("ready")))

        from gisds.core import ready as _ready

        orig_state = _ready.state()
        _ready.mark_starting()
        try:
            rs = client.get("/readyz")
            rh = client.get("/healthz")
            check("启动期 /readyz -> 503", rs.status_code == 503, rs.text[:120])
            check("启动期 /readyz 报 ready=false", rs.json().get("ready") is False)
            check("启动期 /healthz 仍 200（存活语义，不误伤外部监控）",
                  rh.status_code == 200, rh.text[:120])
            check("启动期 /healthz 报 ready=false", rh.json().get("ready") is False)
            # 存活探针不该有副作用：datasets() 会触发一次扫描并阻塞在 QGIS 线程上
            check("启动期 /healthz 不碰注册中心（不报 datasets）",
                  "datasets" not in rh.json(), rh.text[:120])
        finally:
            if orig_state == "ready":
                _ready.mark_ready()
            elif orig_state == "starting":
                _ready.mark_starting()
            else:
                _ready.mark_idle()

        print("\n== 元信息 ==")
        r = client.get("/api/meta")
        check("GET /api/meta -> 200", r.status_code == 200)
        meta = r.json()
        check("meta 声明 REST + MCP 能力", "rest" in meta["capabilities"]["integration"])
        check("meta 统计到 1 个数据集", meta["counts"]["datasets"] == 1, str(meta["counts"]))
        check("meta 统计到 2 个栅格", meta["counts"]["rasters"] == 2, str(meta["counts"]))
        check("meta 统计到 1 个矢量", meta["counts"]["vectors"] == 1, str(meta["counts"]))
        check("meta 统计到 440 个算法", meta["counts"]["processing_algorithms"] == 440,
              str(meta["counts"].get("processing_algorithms")))

        print("\n== 数据集 ==")
        r = client.get("/api/datasets")
        check("GET /api/datasets -> 200", r.status_code == 200)
        datasets = r.json()
        check("返回 1 个数据集", datasets["count"] == 1)
        ds_id = datasets["datasets"][0]["id"]
        print(f"    数据集 id = {ds_id}")

        r = client.get(f"/api/datasets/{ds_id}")
        check("GET /api/datasets/{id} -> 200", r.status_code == 200)
        detail = r.json()
        check("详情含 2 个栅格图层", len(detail["layers"]) == 2, str(len(detail["layers"])))
        check("详情含 1 个矢量图层", len(detail["vectors"]) == 1, str(len(detail["vectors"])))
        check("详情含类别表", len(detail["layers"]["classes"]["classes"]) == 6)
        check("栅格 ref 形如 数据集:图层",
              detail["layers"]["classes"]["ref"] == "example:classes",
              detail["layers"]["classes"].get("ref"))

        r = client.get("/api/datasets/does-not-exist")
        check("不存在的数据集 -> 404", r.status_code == 404, str(r.status_code))
        check("404 返回结构化错误", r.json().get("error") == "not_found")

        r = client.get(f"/api/datasets/{ds_id}/overview")
        check("GET overview -> 200", r.status_code == 200)
        overview = r.json()
        check("overview 含图层统计", len(overview["layers"]) >= 3, str(len(overview["layers"])))

        r = client.post("/api/datasets/rescan")
        check("POST rescan -> 200", r.status_code == 200)

        r = client.get("/api/layers?kind=vector")
        check("GET /api/layers?kind=vector -> 200", r.status_code == 200)
        check("矢量图层过滤正确", r.json()["count"] == 1, str(r.json()["count"]))

        r = client.get("/api/layers")
        check("GET /api/layers -> 200", r.status_code == 200)
        refs = [i["ref"] for i in r.json()["layers"]]
        check("图层清单包含 example:classes", "example:classes" in refs, str(refs))
        check("图层清单包含 example:boundary", "example:boundary" in refs, str(refs))

        print("\n== 栅格查询 ==")
        layer = detail["layers"]["classes"]
        minx, miny, maxx, maxy = layer["extent_wgs84"]
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

        r = client.get(f"/api/rasters/{ds_id}/classes/point", params={"lon": cx, "lat": cy})
        check("GET point -> 200", r.status_code == 200, r.text[:200])
        point = r.json()
        check("点查询返回类别名", point.get("class_name") == "类别六", str(point.get("class_name")))
        check("点查询返回数值", point.get("value") is not None)

        r = client.get(
            f"/api/rasters/{ds_id}/classes/bbox",
            params={"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy},
        )
        check("GET bbox -> 200", r.status_code == 200, r.text[:200])
        bbox = r.json()
        check("bbox 返回 6 类统计", len(bbox["classes"]) == 6, str(len(bbox["classes"])))
        check("bbox 面积换算有效", bbox["area_m2"] and bbox["area_m2"] > 0)
        print(f"    面积 {bbox['area_m2'] / 1e4:.1f} ha，{bbox['valid_pixels']} 像元")

        r = client.get(f"/api/rasters/{ds_id}/classes/area")
        check("GET area -> 200", r.status_code == 200)
        check("area 返回 6 类", len(r.json()["classes"]) == 6)

        r = client.get(f"/api/rasters/{ds_id}/surface/histogram", params={"bins": 16})
        check("GET histogram -> 200", r.status_code == 200)
        check("直方图有 16 个 bin", len(r.json()["bins"]) == 16)

        r = client.get(f"/api/rasters/{ds_id}/surface/stats")
        check("GET band stats -> 200", r.status_code == 200)
        check("统计含 mean", "mean" in r.json())

        poly = [
            [minx + (maxx - minx) * 0.2, miny + (maxy - miny) * 0.2],
            [maxx - (maxx - minx) * 0.2, miny + (maxy - miny) * 0.2],
            [maxx - (maxx - minx) * 0.2, maxy - (maxy - miny) * 0.2],
            [minx + (maxx - minx) * 0.2, maxy - (maxy - miny) * 0.2],
        ]
        r = client.post(f"/api/rasters/{ds_id}/classes/polygon", json=poly)
        check("POST polygon -> 200", r.status_code == 200, r.text[:200])
        check("多边形统计命中", r.json()["valid_pixels"] > 0)

        print("\n== 矢量查询 ==")
        r = client.get(f"/api/vectors/{ds_id}/boundary/features", params={"limit": 5})
        check("GET features -> 200", r.status_code == 200, r.text[:200])
        feats = r.json()
        check("返回 GeoJSON", feats["type"] == "FeatureCollection")
        check("取到 1 个要素", feats["count"] == 1)
        check("几何已转到 WGS84", feats["crs"] == "EPSG:4326")
        check("要素含属性", feats["features"][0]["properties"].get("name") == "示例范围")

        r = client.get(f"/api/vectors/{ds_id}/boundary/stats")
        check("GET vector stats -> 200", r.status_code == 200)
        check("统计含字段列表", len(r.json()["fields"]) >= 1)

        r = client.get(f"/api/vectors/{ds_id}/boundary/hit", params={"lon": cx, "lat": cy})
        check("GET hit -> 200", r.status_code == 200)
        check("点命中 1 个要素", r.json()["count"] == 1, str(r.json()["count"]))

        print("\n== 渲染 ==")
        r = client.get(f"/api/render/raster/{ds_id}/classes.png", params={"width": 480})
        check("GET render png -> 200", r.status_code == 200, r.text[:200])
        check("返回 PNG", r.headers.get("content-type") == "image/png")
        check("PNG 非空", len(r.content) > 2000, f"{len(r.content)} bytes")
        print(f"    PNG {len(r.content) / 1024:.1f} KB")

        r = client.get(f"/api/render/vector/{ds_id}/boundary.png", params={"width": 320})
        check("GET render vector png -> 200", r.status_code == 200)

        r = client.get(
            f"/api/render/combined/{ds_id}.png",
            params={"layers": "classes", "vectors": "boundary", "width": 480},
        )
        check("GET combined png -> 200", r.status_code == 200, r.text[:200])

        import math

        z = 13
        tx = int((cx + 180.0) / 360.0 * (2**z))
        rad = math.radians(cy)
        ty = int((1.0 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2.0 * (2**z))
        r = client.get(f"/api/tiles/{ds_id}/classes/{z}/{tx}/{ty}.png")
        check("GET tile -> 200", r.status_code == 200)
        # 像素级判定：字节数大不等于有内容，全白 PNG 也是 200、也不小
        clusters = color_clusters(pixels(r.content))
        check("瓦片有内容（像素级）", clusters >= 2, f"{clusters} 种颜色，{len(r.content)} bytes")

        print("\n== 综合点查询 ==")
        r = client.get("/api/query/point", params={"lon": cx, "lat": cy})
        check("GET /api/query/point -> 200", r.status_code == 200)
        combined = r.json()
        check("同时返回 2 个栅格与 1 个矢量",
              len(combined["rasters"]) == 2 and len(combined["vectors"]) == 1,
              f"{len(combined['rasters'])}/{len(combined['vectors'])}")
        check("栅格命中 类别六", combined["rasters"][0]["class_name"] == "类别六",
              str(combined["rasters"][0].get("class_name")))

        print("\n== 工具箱 ==")
        r = client.get("/api/processing/algorithms")
        check("GET algorithms -> 200", r.status_code == 200)
        algos = r.json()
        check("算法总数 440", algos["total"] == 440, str(algos.get("total")))

        r = client.get("/api/processing/algorithms/native:buffer")
        check("GET algorithm detail -> 200", r.status_code == 200)
        detail_algo = r.json()
        params = {p["name"]: p for p in detail_algo["parameters"]}
        check("buffer 有 INPUT 参数", "INPUT" in params)
        cap = params.get("END_CAP_STYLE", {})
        # 枚举项的显示名随 QGIS 翻译包的语言变化，所以这里只校验「结构」，
        # 不写死具体是中文还是英文。
        opts = cap.get("options") or []
        vals = cap.get("option_values") or []
        check("枚举参数给出 options", bool(opts) and all(isinstance(o, str) for o in opts),
              str(opts))
        check("枚举参数给出 option_values", len(vals) == len(opts) and len(vals) > 0,
              str(vals))
        check("枚举参数给出可读默认值",
              cap.get("default_label") in opts or cap.get("default_label") == [o for o in opts],
              str(cap.get("default_label")))

        # 用「接口自己给出的取值」跑一遍 —— 下标、显示名、英文原名三种写法都该成立
        for label, style in (("下标", vals[1]), ("显示名", opts[1]), ("英文原名", "Flat")):
            r = client.post("/api/processing/run", json={
                "algorithm": "native:buffer",
                "parameters": {
                    "INPUT": "example:boundary",
                    "DISTANCE": 0.01,
                    "END_CAP_STYLE": style,
                    "JOIN_STYLE": 1,
                },
                "name": f"自检缓冲区-{label}",
            })
            check(f"POST run（枚举取值：{label}）-> 200", r.status_code == 200, r.text[:300])
            job_id = (r.json() or {}).get("job_id")
            check(f"run 立即返回 job_id（{label}）", bool(job_id))

            import time

            final = None
            for _ in range(40):
                time.sleep(0.5)
                r = client.get(f"/api/processing/jobs/{job_id}")
                final = r.json()
                if final.get("status") in ("success", "failed"):
                    break
            check(f"作业执行成功（{label}）", final and final.get("status") == "success",
                  str(final.get("error")) if final else "无返回")
            outputs = (final or {}).get("outputs") or {}
            if label == "下标":
                # 这两条断言**无条件执行**：条件不满足就让断言**失败**，而不是跳过
                # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
                job_ok = bool(final) and final.get("status") == "success"
                check("作业产出 OUTPUT 文件", job_ok and "OUTPUT" in outputs, str(list(outputs)))
                if "OUTPUT" in outputs:
                    check("输出文件已落盘", Path(outputs["OUTPUT"]["path"]).exists(),
                          outputs["OUTPUT"]["path"])
                else:
                    check("输出文件已落盘", False, "没有 OUTPUT 输出")

        print("\n== 跨域头 ==")
        r = client.get("/api/datasets", headers={"Origin": "http://example.com"})
        check("返回 CORS 允许头", "access-control-allow-origin" in {k.lower() for k in r.headers})

        print("\n== 文档 ==")
        check("GET /docs -> 200", client.get("/docs").status_code == 200)
        check("GET /openapi.json -> 200", client.get("/openapi.json").status_code == 200)

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
    # PyQGIS 在 Windows 上退出时可能触发 0xC0000005，这里直接结束进程
    import os

    os._exit(code)
