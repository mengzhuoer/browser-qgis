"""Processing 工具箱接口：算法清单、参数自省、作业化执行、结果取用。"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..core import processing as proc
from ..core.auth import Principal
from ..core.errors import InvalidRequestError, NotFoundError
from ..core.executor import run_qgis
from ..core.jobs import MANAGER
from ..core.tenancy import scope_label
from .deps import (
    current_principal,
    get_config,
    get_registry,
    get_workspace,
    write_scope_for,
)

router = APIRouter(prefix="/api/processing", tags=["processing"])


# 算法输出目录：服务级的临时区，不属于任何数据空间
def _jobs_root() -> Path:
    root = get_config().data_root_path / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.get("/algorithms")
async def list_algorithms(
    q: str | None = Query(None, description="按 id / 名称 / 分组模糊搜索（中英文都能搜）"),
    group: str | None = Query(None),
    provider: str | None = Query(None, description="native / gdal / qgis / pdal"),
    include_en: bool = Query(False, description="是否附带英文名 / 英文说明"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """列出全部可用的 Processing 算法。

    默认只给中文（响应体约 180 KB）；需要英文对照时加 ``?include_en=true``。
    """
    await run_qgis(proc.ensure_ready)
    items = await run_qgis(proc.list_algorithms, q, group, provider, include_en)
    return {
        "count": len(items),
        "total": proc.algorithm_count(),
        "groups": await run_qgis(proc.algorithm_groups),
        "providers": await run_qgis(proc.providers),
        "algorithms": items,
    }


@router.get("/algorithms/{algorithm_id}")
async def describe_algorithm(
    algorithm_id: str, principal: Principal = Depends(current_principal)
) -> dict:
    """算法详情（含全部参数定义），网页据此生成参数表单。"""
    await run_qgis(proc.ensure_ready)
    info = await run_qgis(proc.describe_algorithm, algorithm_id)
    info["available_layers"] = get_registry().layer_refs(principal)
    return info


@router.get("/groups")
async def groups(principal: Principal = Depends(current_principal)) -> dict:
    await run_qgis(proc.ensure_ready)
    return {"groups": await run_qgis(proc.algorithm_groups)}


@router.post("/run")
async def run(
    body: dict = Body(...), principal: Principal = Depends(current_principal)
) -> dict:
    """执行一个算法（异步作业）。

    body::

        {"algorithm": "native:buffer",
         "parameters": {"INPUT": "数据集:图层", "DISTANCE": 500},
         "name": "缓冲分析"}

    返回 job_id，用 ``GET /api/processing/jobs/{id}`` 轮询进度与结果。
    """
    algorithm_id = str(body.get("algorithm") or body.get("algorithm_id") or "").strip()
    if not algorithm_id:
        raise InvalidRequestError("缺少 algorithm 参数")
    parameters = body.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise InvalidRequestError("parameters 必须是对象")

    await run_qgis(proc.ensure_ready)
    registry = get_registry()
    config = get_config()

    job_id = MANAGER.create(
        kind="processing",
        title=str(body.get("name") or algorithm_id),
        meta={"algorithm": algorithm_id, "parameters": parameters},
        owner=principal.user_id,
    )

    out_dir = _jobs_root() / job_id

    def _resolve(ref: str):
        return registry.resolve_ref(ref, principal)

    def _execute():
        return proc.run_algorithm(
            algorithm_id,
            parameters,
            _resolve,
            out_dir,
            job_id=job_id,
            jobs=MANAGER,
        )

    async def _on_done(job: str, result: dict):
        MANAGER.set_outputs(job, result.get("outputs", {}))
        MANAGER.append_log(job, f"耗时 {result.get('elapsed_seconds')} 秒")

    await MANAGER.start(job_id, _execute, on_done=_on_done)

    return {
        "job_id": job_id,
        "algorithm": algorithm_id,
        "status": "queued",
        "poll": f"/api/processing/jobs/{job_id}",
        "timeout_seconds": config.job_timeout,
    }


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(current_principal),
) -> dict:
    """作业清单。

    **默认只看得到自己的作业**（管理员看全部）—— 作业的 meta 里有图层引用、
    outputs 里有服务器上的绝对路径，不该让所有人互相可见。
    """
    if principal.is_admin:
        jobs = MANAGER.list(limit)
    else:
        jobs = MANAGER.list(limit, owner=principal.user_id)
    return {"count": len(jobs), "jobs": jobs}


def _own_job(job_id: str, principal: Principal) -> dict:
    """取作业，**按 owner 隔离**。

    看不见就当它不存在（404）—— 回 403 等于告诉对方「这个作业确实存在」。
    """
    job = MANAGER.get(job_id)
    if job is None:
        raise NotFoundError(f"作业不存在：{job_id}")
    owner = job.get("owner") or ""
    if not principal.is_admin and owner and owner != principal.user_id:
        raise NotFoundError(f"作业不存在：{job_id}")
    return job


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str, principal: Principal = Depends(current_principal)
) -> dict:
    return _own_job(job_id, principal)


@router.get("/jobs/{job_id}/output/{key}")
async def download_output(
    job_id: str, key: str, principal: Principal = Depends(current_principal)
) -> FileResponse:
    """下载算法输出文件。"""
    job = _own_job(job_id, principal)
    entry = (job.get("outputs") or {}).get(key)
    if not entry or not entry.get("path"):
        raise NotFoundError(f"作业 {job_id} 没有名为 {key} 的输出文件")
    path = Path(entry["path"])
    if not path.is_file():
        raise NotFoundError(f"输出文件已不存在：{path.name}")
    return FileResponse(str(path), filename=path.name)


@router.post("/jobs/{job_id}/add-to-map")
async def add_output_to_map(
    job_id: str,
    key: str = Body(..., embed=True),
    scope: str | None = Body(None, embed=True),
    principal: Principal = Depends(current_principal),
) -> dict:
    """把算法输出登记进某个空间，这样它就能立刻在地图上叠加显示。"""
    job = _own_job(job_id, principal)
    entry = (job.get("outputs") or {}).get(key)
    if not entry or not entry.get("path"):
        raise NotFoundError(f"作业 {job_id} 没有名为 {key} 的输出文件")

    source = Path(entry["path"])
    if not source.is_file():
        raise NotFoundError(f"输出文件已不存在：{source.name}")

    target_scope = write_scope_for(principal, scope)
    if not principal.can_write(target_scope):
        raise HTTPException(status_code=403, detail="这个空间你没有写入权限")
    workspace = get_workspace(target_scope)
    saved = workspace.save(source.name, source.read_bytes())
    registry = get_registry()
    await run_qgis(registry.scan)

    dataset = registry._datasets.get(saved.dataset_id)  # noqa: SLF001
    return {
        "dataset_id": saved.dataset_id,
        "scope": target_scope,
        "scope_label": scope_label(target_scope),
        "layers": {k: v.to_dict() for k, v in dataset.layers.items()} if dataset else {},
        "vectors": {k: v.to_dict() for k, v in dataset.vectors.items()} if dataset else {},
    }


@router.delete("/jobs")
async def clear_jobs(principal: Principal = Depends(current_principal)) -> dict:
    """清空作业历史（连输出文件一起删）。

    **只有管理员能清**：作业目录是服务级的，删掉就没了 ——
    原来任何登录用户都能一键把所有人的作业输出删光。
    """
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员能清空作业")
    count = len(MANAGER.list(100))
    root = _jobs_root()
    for child in list(root.iterdir()):
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError:
            pass
    # 内存里的作业记录也要清 —— 原来只删了文件，MANAGER 里原封不动，
    # 于是「清空」之后还能查到作业，下载它的输出却 404。
    MANAGER.clear()
    return {"cleared": count}
