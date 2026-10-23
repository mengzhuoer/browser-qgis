"""对真实运行中的服务做端到端检查（含上传与 Processing 工具箱）。

默认自己拉起一个临时服务实例（端口 8851，免登录模式），用完关掉；
也可以用命令行参数指定一个已经在跑的服务地址。

用法::

    run-qgis-python.bat tests/check_live.py
    run-qgis-python.bat tests/check_live.py http://127.0.0.1:8848
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from server_helper import TestServer  # noqa: E402

DEFAULT_BASE = "http://127.0.0.1:8848"

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 42 = 存活 1 + 就绪 3 + 网页 9 + 服务信息 2 + 数据源 3 + 上传 6
#            + 上传后可直接查询 2 + Processing 工具箱 6 + 跑算法 6 + 跨域 2
#            + 清理 2。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 42


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def run_checks(base: str) -> None:
    with httpx.Client(base_url=base, timeout=180.0) as client:
        print("\n-- 存活 --")
        r = client.get("/healthz")
        check("GET /healthz", r.status_code == 200, r.text[:150])
        if r.status_code != 200:
            print("  服务未启动，先跑 start.bat")
            return 1
        health = r.json()
        print(f"    QGIS {health['qgis']['qgis_version']} | 数据集 {health['datasets']}")

        print("\n-- 就绪 --")
        r = client.get("/readyz")
        check("GET /readyz", r.status_code == 200, r.text[:150])
        check("readyz 报告 ready", r.json().get("ready") is True, r.text[:120])
        check("healthz 报告 ready", health.get("ready") is True, str(health.get("ready")))

        print("\n-- 网页 --")
        r = client.get("/")
        check("GET / （控制台）", r.status_code == 200)
        check("控制台含 QGIS 式菜单栏", 'data-menu="file"' in r.text and "视图" in r.text)
        check("控制台含添加图层对话框", 'id="dlgAdd"' in r.text)
        check("控制台含图层面板", 'id="treeList"' in r.text)
        check("控制台含状态栏", 'id="stCoord"' in r.text and 'id="stScale"' in r.text)
        check("控制台含工具箱面板", "Processing" in r.text)
        r = client.get("/demo")
        check("GET /demo （接入示例）", r.status_code == 200)
        r = client.get("/static/vendor/leaflet.js")
        check("GET /static/vendor/leaflet.js", r.status_code == 200)
        r = client.get("/docs")
        check("GET /docs", r.status_code == 200)

        print("\n-- 服务信息 --")
        meta = client.get("/api/meta").json()
        check("统计到算法数 >= 400", meta["counts"]["processing_algorithms"] >= 400,
              str(meta["counts"]))
        check("区分共享空间与个人空间", "shared_datasets" in meta["counts"])
        print(f"    算法 {meta['counts']['processing_algorithms']} 个")

        print("\n-- 数据源 --")
        ds = client.get("/api/datasets").json()
        check("GET /api/datasets", ds["count"] >= 1, str(ds["count"]))
        ds_id = ds["datasets"][0]["id"]
        detail = client.get(f"/api/datasets/{ds_id}").json()
        raster_ids = list(detail["layers"].keys())
        vector_ids = list(detail["vectors"].keys())
        print(f"    数据集 {ds_id}：{len(raster_ids)} 栅格 / {len(vector_ids)} 矢量")

        layers = client.get("/api/layers").json()
        check("GET /api/layers", layers["count"] >= 3, str(layers["count"]))
        check("图层带 ref", all(":" in l["ref"] for l in layers["layers"]))
        print("    " + ", ".join(l["ref"] for l in layers["layers"]))

        print("\n-- 上传 --")
        sample = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
        with (sample / "classes.tif").open("rb") as fh:
            r = client.post("/api/uploads", files={"files": ("my_classes.tif", fh, "image/tiff")})
        check("POST /api/uploads", r.status_code == 200, r.text[:200])
        up = r.json()
        check("上传 1 个文件", up["uploaded"] == 1, str(up))
        new_ds = up["results"][0]["dataset_id"]
        check("上传的文件已注册为图层", up["results"][0]["registered"] is True, str(up["results"][0]))
        print(f"    新数据集 {new_ds}")

        r = client.get("/api/uploads")
        check("GET /api/uploads", r.status_code == 200)
        check("上传列表含新数据集", any(u["dataset_id"] == new_ds for u in r.json()["uploads"]))

        layers2 = client.get("/api/layers").json()
        check("上传后图层清单变大", layers2["count"] > layers["count"])
        new_ref = next(l["ref"] for l in layers2["layers"] if l["dataset_id"] == new_ds)

        print("\n-- 上传后可直接查询 --")
        r = client.get(f"/api/rasters/{new_ds}/{new_ref.split(':')[1]}/point",
                       params={"lon": 112.48, "lat": 39.685})
        check("上传的栅格可点查询", r.status_code == 200, r.text[:150])
        print(f"    -> {r.json().get('class_name') or r.json().get('value')}")

        r = client.get(f"/api/render/raster/{new_ds}/{new_ref.split(':')[1]}.png",
                       params={"width": 400})
        check("上传的栅格可出图", r.status_code == 200 and r.content[:4] == b"\x89PNG")

        print("\n-- Processing 工具箱 --")
        r = client.get("/api/processing/algorithms")
        check("GET /api/processing/algorithms", r.status_code == 200, r.text[:150])
        algos = r.json()
        check("算法总数 >= 400", algos["total"] >= 400, str(algos["total"]))
        print(f"    共 {algos['total']} 个，{len(algos['groups'])} 个分组")

        r = client.get("/api/processing/algorithms", params={"q": "buffer"})
        check("按关键字检索算法", r.status_code == 200 and r.json()["count"] > 0)

        r = client.get("/api/processing/algorithms/native:buffer")
        check("GET 算法详情", r.status_code == 200, r.text[:150])
        info = r.json()
        check("详情含参数定义", len(info["parameters"]) >= 5, str(len(info["parameters"])))
        check("详情附可用图层", len(info.get("available_layers", [])) > 0)
        print(f"    native:buffer 参数 {len(info['parameters'])} 个")

        print("\n-- 跑算法（异步作业）--")
        vec_ref = next(l["ref"] for l in layers["layers"] if l["kind"] == "vector")
        r = client.post("/api/processing/run", json={
            "algorithm": "native:buffer",
            "parameters": {"INPUT": vec_ref, "DISTANCE": 900},
            "name": "缓冲测试",
        })
        check("POST /api/processing/run", r.status_code == 200, r.text[:200])
        job = r.json()
        job_id = job["job_id"]
        print(f"    job_id = {job_id}")

        status = "queued"
        for _ in range(60):
            time.sleep(0.5)
            j = client.get(f"/api/processing/jobs/{job_id}").json()
            status = j["status"]
            if status in ("success", "failed"):
                break
        check("作业完成", status == "success", f"状态={status} 错误={j.get('error')}")
        print(f"    状态 {status}，输出 {list((j.get('outputs') or {}).keys())}")

        outputs = j.get("outputs") or {}
        check("有输出文件", any(o.get("kind") == "file" for o in outputs.values()), str(outputs))
        key = next((k for k, v in outputs.items() if v.get("kind") == "file"), None)
        # 下面这三条**无条件执行**：条件不满足就让断言**失败**，而不是整段跳过
        # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
        if key:
            r = client.get(f"/api/processing/jobs/{job_id}/output/{key}")
            check("下载算法输出", r.status_code == 200 and len(r.content) > 100,
                  f"{r.status_code} {len(r.content)}")
        else:
            check("下载算法输出", False, "作业没有文件类型的输出")
        r = client.post(f"/api/processing/jobs/{job_id}/add-to-map", json={"key": key})
        check("结果加入地图", r.status_code == 200, r.text[:150])
        body = r.json() if r.status_code == 200 else {}
        check("结果已注册为图层",
              bool(body.get("layers") or body.get("vectors")), str(body)[:150])
        # 自检跑在正式数据目录上，登记进来的结果顺手清掉，别留垃圾
        added_ds = body.get("dataset_id")
        if added_ds:
            client.delete(f"/api/uploads/{added_ds}")

        print("\n-- 跨域 --")
        r = client.get("/api/datasets", headers={"Origin": "http://localhost:3000"})
        allow = r.headers.get("access-control-allow-origin")
        check("返回跨域允许头", allow is not None, str(allow))
        r = client.options("/api/processing/algorithms",
                            headers={"Origin": "http://localhost:3000",
                                     "Access-Control-Request-Method": "GET"})
        check("预检 OPTIONS 通过", r.status_code in (200, 204), str(r.status_code))

        print("\n-- 清理 --")
        r = client.delete(f"/api/uploads/{new_ds}")
        check("DELETE /api/uploads/{id}", r.status_code == 200, r.text[:150])
        r = client.get("/api/uploads")
        check("上传列表已移除", not any(u["dataset_id"] == new_ds for u in r.json()["uploads"]))


def main() -> int:
    external = sys.argv[1] if len(sys.argv) > 1 else None
    if external:
        base = external.rstrip("/")
        print(f"== 端到端检查 {base} ==")
        run_checks(base)
    else:
        print("== 端到端检查（自起临时服务） ==")
        with TestServer(port=8851) as server:
            run_checks(server.base)
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
    import os

    os._exit(code)
