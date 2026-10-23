"""Processing 参数的图层类型自检。

钉的是这一条：**算法要的图层类型和实际喂进去的不一致时，必须给一句能看懂的中文错**。

不查这一下的话，QGIS 原生报的是「无法加载 INPUT 源图层：无效值」——
压根看不出是「你把栅格喂给了要要素源的算法」。网页端的下拉框原来还会把栅格
和矢量混在一起列出来（``source`` 是「要素源」，不是「任意图层」），用户一挑就撞上那句天书。

覆盖四条路：
  矢量算法 + 栅格输入 -> 报「需要矢量图层」
  矢量算法 + 矢量输入 -> 正常跑成功（证明这个检查没误伤）
  栅格算法 + 矢量输入 -> 报「需要栅格图层」
  栅格算法 + 栅格输入 -> 正常跑成功

数据全部隔离到临时目录，跑完整个删掉，不碰生产目录、也不在仓库里留文件。

独立运行::

    run-qgis-python.bat tests/check_processing_types.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---- 数据隔离：整个数据根目录搬到临时目录，跑完删掉 ----------------------------
#
# 不能直接用 tests/sample_data 当 data_root：Processing 的作业输出目录是
# <data_root>/jobs/，跑一次算法就往那儿落一堆文件，留在仓库里就是垃圾。
_TMP_ROOT = PROJECT_ROOT / "tests" / "_workspace" / f"types_tmp_{os.getpid()}"
_SAMPLE = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"

if _TMP_ROOT.exists():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
(_TMP_ROOT / "shared").mkdir(parents=True, exist_ok=True)
if _SAMPLE.is_dir():
    shutil.copytree(_SAMPLE, _TMP_ROOT / "shared" / "example", dirs_exist_ok=True)
(_TMP_ROOT / "users").mkdir(parents=True, exist_ok=True)

os.environ["GISDS_DATA_ROOT"] = str(_TMP_ROOT)
os.environ["GISDS_SHARED_ROOT"] = str(_TMP_ROOT / "shared")
os.environ["GISDS_USERS_ROOT"] = str(_TMP_ROOT / "users")
os.environ["GISDS_DB_PATH"] = str(_TMP_ROOT / "gisds.db")
os.environ["GISDS_REQUIRE_LOGIN"] = "0"
# 插件目录指到空目录：这里只数 QGIS 自带的算法，插件贡献的算法不参与
os.environ["GISDS_PLUGIN_DIR"] = str(PROJECT_ROOT / "tests" / "_workspace" / "no_plugins")

from fastapi.testclient import TestClient  # noqa: E402

from gisds.api.app import create_app  # noqa: E402

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 14 = 参数元信息 6 + 矢量算法吃栅格 4 + 矢量算法吃矢量 1
#            + 栅格算法吃矢量 2 + 栅格算法吃栅格 1。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 14


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def run_and_wait(client, algorithm: str, parameters: dict) -> dict:
    """提交一个算法作业，轮询到终态，返回终态字典。

    算法是在 QGIS 专属线程里跑的（跟别的 QGIS 调用互斥），所以这里是**异步**的：
    POST 只返回 job_id，结果要轮询 ``/api/processing/jobs/{id}``。
    """
    r = client.post("/api/processing/run", json={
        "algorithm": algorithm,
        "parameters": parameters,
        "name": f"类型自检-{algorithm}",
    })
    if r.status_code != 200:
        return {"status": f"HTTP {r.status_code}", "error": r.text[:300]}
    job_id = (r.json() or {}).get("job_id")
    if not job_id:
        return {"status": "没有 job_id", "error": r.text[:300]}
    final: dict = {}
    for _ in range(80):
        time.sleep(0.5)
        final = client.get(f"/api/processing/jobs/{job_id}").json() or {}
        if final.get("status") in ("success", "failed"):
            break
    return final


def main() -> int:
    try:
        return _run()
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)


def _run() -> int:
    with TestClient(create_app()) as client:
        print("== 参数元信息 ==")
        # 这两条把「QGIS 自己的事实」钉住：source 是「要素源」（只收矢量），
        # raster 才是栅格。前后端的判断都建立在这上面。
        r = client.get("/api/processing/algorithms/native:buffer")
        check("取到 buffer 算法详情", r.status_code == 200, f"HTTP {r.status_code}")
        buffer_params = {p["name"]: p for p in (r.json() or {}).get("parameters", [])}
        check("buffer 的 INPUT 是 source（要素源，只收矢量）",
              (buffer_params.get("INPUT") or {}).get("type") == "source",
              str((buffer_params.get("INPUT") or {}).get("type")))

        r = client.get("/api/processing/algorithms/native:slope")
        check("取到 slope 算法详情", r.status_code == 200, f"HTTP {r.status_code}")
        slope_params = {p["name"]: p for p in (r.json() or {}).get("parameters", [])}
        check("slope 的 INPUT 是 raster",
              (slope_params.get("INPUT") or {}).get("type") == "raster",
              str((slope_params.get("INPUT") or {}).get("type")))

        # 网页端的下拉框就是拿「参数类型」去筛「图层 kind」——
        # 所以每个图层都必须带 kind，前端才筛得动。
        r = client.get("/api/layers")
        layers = (r.json() or {}).get("layers") or []
        kinds = {l.get("ref"): l.get("kind") for l in layers}
        check("每个图层都带 kind，且只有 vector/raster 两种",
              bool(layers) and all(v in ("vector", "raster") for v in kinds.values()),
              str(kinds)[:200])
        check("示例栅格的 kind 是 raster、示例矢量的 kind 是 vector",
              kinds.get("example:classes") == "raster" and kinds.get("example:boundary") == "vector",
              str(kinds)[:200])

        print("\n== 矢量算法吃栅格 ==")
        final = run_and_wait(client, "native:buffer", {"INPUT": "example:classes"})
        err = str(final.get("error") or "")
        check("作业失败", final.get("status") == "failed", f"status={final.get('status')}")
        check("报错说清了「需要矢量图层」且点出「栅格」",
              "需要矢量图层" in err and "栅格" in err, err[:240])
        check("没有拿 QGIS 原生那句「无效值」搪塞",
              "无效值" not in err, err[:240])
        # 图层名要是**名字**：``QgsMapLayer.name`` 在 PyQGIS 里是方法不是属性，
        # 直接 getattr 拿到的是绑定方法，打出来是
        # ``<built-in method name of QgsRasterLayer object at 0x...>``（看不出是哪个图层）。
        check("报错里带的是图层名字（不是方法 repr）",
              "built-in method" not in err, err[:240])

        print("\n== 矢量算法吃矢量（不该误伤）==")
        final = run_and_wait(client, "native:buffer", {"INPUT": "example:boundary"})
        check("矢量喂矢量正常跑成功", final.get("status") == "success",
              f"status={final.get('status')} err={str(final.get('error'))[:200]}")

        print("\n== 栅格算法吃矢量 ==")
        final = run_and_wait(client, "native:slope", {"INPUT": "example:boundary"})
        err = str(final.get("error") or "")
        check("作业失败", final.get("status") == "failed", f"status={final.get('status')}")
        check("报错说清了「需要栅格图层」且点出「矢量」",
              "需要栅格图层" in err and "矢量" in err, err[:240])

        print("\n== 栅格算法吃栅格（不该误伤）==")
        final = run_and_wait(client, "native:slope", {"INPUT": "example:classes"})
        check("栅格喂栅格正常跑成功", final.get("status") == "success",
              f"status={final.get('status')} err={str(final.get('error'))[:200]}")

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
        # 真实原因（那条 KeyError）反而被埋了。改成：打印回溯 + 退出码 1。
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
