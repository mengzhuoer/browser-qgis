"""新能力自检：上传工作区 + 图层引用 + Processing 工具箱。

独立运行::

    run-qgis-python.bat tests/check_new.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["GISDS_DATA_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data")
os.environ["GISDS_SHARED_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data" / "shared")
os.environ["GISDS_USERS_ROOT"] = str(PROJECT_ROOT / "tests" / "sample_data" / "users")
os.environ["GISDS_DB_PATH"] = str(PROJECT_ROOT / "tests" / "_workspace" / "gisds.db")
os.environ["GISDS_REQUIRE_LOGIN"] = "0"

from gisds import qgis_env  # noqa: E402
from gisds.core import processing as proc  # noqa: E402
from gisds.core import raster as raster_core  # noqa: E402
from gisds.core.registry import Registry  # noqa: E402
from gisds.core.storage import Workspace  # noqa: E402

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 24 = 多空间注册 3 + 上传 4 + 图层引用 3 + Processing 工具箱 7
#            + 跑算法 3 + 分类统计 2 + 清理 2。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 24


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    qgis_env.init_qgis()
    print("== 初始化 ==")
    print(f"  QGIS {qgis_env.runtime_info().get('qgis_version')}")

    sample = PROJECT_ROOT / "tests" / "sample_data" / "shared" / "example"
    if not sample.is_dir():
        print(f"  缺少示例数据：{sample}")
        return 1

    print("\n== 多空间注册 ==")
    shared_root = sample.parent          # sample_data/shared
    users_root = shared_root.parent / "users"
    users_root.mkdir(parents=True, exist_ok=True)
    registry = Registry(shared_root.parent, shared_root, users_root)
    registry.scan()
    check("共享空间发现 1 个数据集", len(registry.datasets()) == 1, str(len(registry.datasets())))
    check("数据集标记为 shared", registry.datasets()[0].source == "shared")
    check("数据集空间标识为 shared", registry.datasets()[0].scope == "shared",
          registry.datasets()[0].scope)

    print("\n== 上传 ==")
    raster_src = sample / "classes.tif"
    vector_src = sample / "boundary.geojson"
    workspace = Workspace(users_root / "tester")
    saved_r = workspace.save("uploaded_classes.tif", raster_src.read_bytes())
    saved_v = workspace.save("uploaded_boundary.geojson", vector_src.read_bytes())
    check("栅格上传成功", Path(saved_r.directory, saved_r.files[0]).is_file())
    check("矢量上传成功", Path(saved_v.directory, saved_v.files[0]).is_file())

    registry.scan()
    check("上传后数据集变成 3 个", len(registry.datasets()) == 3, str(len(registry.datasets())))
    user_datasets = [d for d in registry.datasets() if d.scope == "user:tester"]
    check("其中 2 个在用户空间", len(user_datasets) == 2, str(len(user_datasets)))

    print("\n== 图层引用 ==")
    refs = registry.layer_refs()
    check("图层清单非空", len(refs) >= 5, str(len(refs)))
    ref_list = [r["ref"] for r in refs]
    print("    " + ", ".join(ref_list))
    check("引用形如 数据集:图层", all(":" in r for r in ref_list))

    target_ref = next(r for r in ref_list if r.endswith(":classes"))
    layer = registry.resolve_ref(target_ref)
    check("能按引用解析出图层", layer is not None and layer.isValid())

    print("\n== Processing 工具箱 ==")
    check("Processing 就绪", proc.ensure_ready())
    print(f"  算法总数: {proc.algorithm_count()}")
    check("算法数 >= 400", proc.algorithm_count() >= 400, str(proc.algorithm_count()))

    algos = proc.list_algorithms(query="buffer")
    check("按关键字搜到 buffer 相关算法", len(algos) > 0, str(len(algos)))
    print(f"    buffer 相关: {[a['id'] for a in algos[:5]]}")

    groups = proc.algorithm_groups()
    check("算法分组非空", len(groups) > 10, str(len(groups)))
    print(f"    分组 {len(groups)} 个，例如：{groups[:5]}")

    info = proc.describe_algorithm("native:buffer")
    check("能拿到算法参数定义", len(info["parameters"]) >= 5, str(len(info["parameters"])))
    param_names = [p["name"] for p in info["parameters"]]
    check("参数含 INPUT/DISTANCE", "INPUT" in param_names and "DISTANCE" in param_names, str(param_names))
    enum_param = next(p for p in info["parameters"] if p["type"] == "enum")
    check("枚举参数带选项", len(enum_param.get("options", [])) > 0, str(enum_param))

    print("\n== 跑算法 ==")
    out_dir = Path(tempfile.gettempdir()) / "gisds_check_algo"
    out_dir.mkdir(parents=True, exist_ok=True)
    vector_ref = next(r for r in ref_list if r.endswith(":boundary"))
    result = proc.run_algorithm(
        "native:buffer",
        {"INPUT": vector_ref, "DISTANCE": 800, "OUTPUT": str(out_dir / "buffered.gpkg")},
        registry.resolve_ref,
        out_dir,
    )
    check("算法执行成功", "outputs" in result, str(result)[:150])
    output = result["outputs"].get("OUTPUT", {})
    check("输出文件已生成", output.get("kind") == "file", str(output))
    # 「输出文件存在」这条**无条件执行**：没给出路径就让断言**失败**，
    # 而不是整段跳过（跳过会让断言总数变少，钉子就看不出少了哪几条）。
    out_path = output.get("path") or ""
    check("输出文件存在", bool(out_path) and Path(out_path).is_file(), out_path)
    if out_path:
        print(f"    输出 {Path(out_path).name}  {output.get('size', 0) / 1024:.1f} KB")
    print(f"    耗时 {result.get('elapsed_seconds')} 秒")

    print("\n== 分类统计（上传的栅格）==")
    uploaded_dataset = next(d for d in registry.datasets() if d.scope == "user:tester" and d.layers)
    uploaded_layer = list(uploaded_dataset.layers.values())[0]
    minx, miny, maxx, maxy = uploaded_layer.extent_wgs84
    stats = raster_core.bbox_stats(uploaded_layer, (minx, miny, maxx, maxy))
    check("上传的栅格可统计", len(stats["classes"]) == 6, str(len(stats["classes"])))
    check("面积换算有效", (stats.get("area_m2") or 0) > 0)
    print(f"    面积 {stats['area_m2'] / 1e4:.1f} ha")

    print("\n== 清理 ==")
    # 先从索引摘掉，释放 QGIS 对文件的占用
    for dataset in list(registry.datasets()):
        if dataset.scope == "user:tester":
            registry.forget(dataset.id)
    import gc

    gc.collect()
    cleared = workspace.clear()
    check("用户空间可清空", cleared == 2, str(workspace.list_datasets()))
    registry.scan()
    check("清理后回到 1 个数据集", len(registry.datasets()) == 1, str(len(registry.datasets())))

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
