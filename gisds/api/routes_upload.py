"""上传接口：把数据送进指定的空间，立刻变成可查询、可渲染的图层。

空间有两个：

- 共享空间 ``shared``：所有登录用户都能看见，也都能往里传
- 个人空间 ``user:<用户ID>``：只有本人看得见、改得动
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..core.auth import Principal
from ..core.errors import InvalidRequestError, NotFoundError
from ..core.executor import run_qgis
from ..core.tenancy import SHARED, scope_label
from .deps import (
    current_principal,
    get_config,
    get_registry,
    get_workspace,
    write_scope_for,
)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _dataset_payload(registry, dataset_id: str) -> dict:
    dataset = registry._datasets.get(dataset_id)  # noqa: SLF001
    return {
        "layers": {k: v.to_dict() for k, v in dataset.layers.items()} if dataset else {},
        "vectors": {k: v.to_dict() for k, v in dataset.vectors.items()} if dataset else {},
        "registered": dataset is not None,
        # 数据集级告警（比如「这个 tif 没有坐标系信息，已按 EPSG:4326 解释」）——
        # 不带出去的话，界面上就只剩一张图/一句话，告警等于没有。
        # 单独用 dataset_warnings 命名：上传结果里本来就有个 warnings（存文件时的
        # 告警），直接覆盖会把那几条冲掉。
        "dataset_warnings": list(dataset.warnings) if dataset else [],
    }


def _merge_warnings(item: dict) -> dict:
    """把存文件时的告警和注册后的告警合到一起。"""
    merged = list(item.get("warnings") or [])
    for w in item.get("dataset_warnings") or []:
        if w not in merged:
            merged.append(w)
    item["warnings"] = merged
    item.pop("dataset_warnings", None)
    return item


def _scopes(principal: Principal) -> list[str]:
    """当前用户可以浏览的空间。"""
    scopes = [SHARED]
    if not principal.shared_only:
        scopes.append(principal.own_scope())
    return scopes


def _scope_holding(principal: Principal, dataset_id: str) -> str | None:
    """哪个可写空间里放着这个数据集（哪怕它没进注册中心）。

    只在「注册中心里没有」时用 —— 也就是文件保存了、QGIS 却打不开的那种
    （只传了 .shp、缺 .dbf/.shx/.prj 之类）。这类东西以前**删不掉**。
    """
    for scope in _scopes(principal):
        if not principal.can_write(scope):
            continue
        try:
            workspace = get_workspace(scope)
            for item in workspace.list_datasets():
                if item.get("dataset_id") == dataset_id:
                    return scope
        except Exception:
            continue
    return None


@router.get("", summary="列出可见空间里的数据集")
async def list_uploads(principal: Principal = Depends(current_principal)) -> dict:
    registry = get_registry()
    items: list[dict] = []
    for scope in _scopes(principal):
        workspace = get_workspace(scope)
        for item in workspace.list_datasets():
            item["scope"] = scope
            item["scope_label"] = scope_label(scope)
            item.update(_dataset_payload(registry, item["dataset_id"]))
            _merge_warnings(item)
            items.append(item)
    items.sort(key=lambda x: x.get("modified_at", 0), reverse=True)
    return {"count": len(items), "uploads": items}


@router.post("", summary="上传文件")
async def upload(
    files: list[UploadFile] = File(...),
    scope: str | None = Query(None, description="写到哪个空间：me（默认）| shared"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """上传一个或多个文件。

    支持栅格 ``.tif/.tiff/.img/.vrt`` 与矢量 ``.geojson/.gpkg/.shp/.gml``。
    每个文件各自成为一个数据集，上传完立即可在地图上渲染和查询。
    """
    if not files:
        raise InvalidRequestError("没有收到任何文件")

    config = get_config()
    limit = config.max_upload_mb * 1024 * 1024
    target_scope = write_scope_for(principal, scope)
    if not principal.can_write(target_scope):
        raise HTTPException(status_code=403, detail="这个空间你没有写入权限")
    workspace = get_workspace(target_scope)

    results = []
    for upload_file in files:
        filename = upload_file.filename or "upload"
        data = await upload_file.read()
        if len(data) > limit:
            raise InvalidRequestError(
                f"文件过大：{filename}（{len(data) / 1024 / 1024:.1f} MB），"
                f"上限 {config.max_upload_mb} MB"
            )
        try:
            saved = workspace.save(filename, data)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        results.append(
            {
                "dataset_id": saved.dataset_id,
                "filename": filename,
                "files": saved.files,
                "kind": saved.kind,
                "scope": target_scope,
                "scope_label": scope_label(target_scope),
                "warnings": saved.warnings,
            }
        )

    # 重新扫描，让新上传的数据进入注册中心
    registry = get_registry()
    await run_qgis(registry.scan)

    # 回填图层信息
    for item in results:
        item.update(_dataset_payload(registry, item["dataset_id"]))
        _merge_warnings(item)
        if not item["registered"]:
            # 注册中心**整个数据集都丢掉了**（加载失败），于是注册时的告警也跟着没了
            # —— 界面上就只剩一句「上传完成」，用户看着地图上什么都没有，
            # 根本不知道是文件本身的问题。必须在这里把话说明白。
            names = [str(f).lower() for f in (item.get("files") or [])]
            if any(n.endswith(".shp") for n in names):
                item["warning"] = (
                    "文件已保存，但 QGIS 没能把它识别成图层："
                    "shapefile 需要 .shp/.dbf/.shx/.prj 配套文件**一起上传**"
                    "（只传 .shp 是打不开的）。"
                )
            else:
                item["warning"] = (
                    "文件已保存，但 QGIS 没能把它识别成图层"
                    "（可能是格式不支持、文件损坏，或缺少坐标系信息）。"
                )
        elif not item["layers"] and not item["vectors"]:
            item["warning"] = "文件已保存，但 QGIS 未能识别为图层（可能是格式不支持或坐标系缺失）"

    return {
        "uploaded": len(results),
        "scope": target_scope,
        "results": results,
        "total_datasets": len(registry.datasets(principal)),
    }


@router.delete("/{dataset_id}", summary="删除数据集")
async def delete_upload(
    dataset_id: str, principal: Principal = Depends(current_principal)
) -> dict:
    """删除一个数据集（只能删自己空间里能删的）。"""
    registry = get_registry()
    try:
        dataset = registry.get_dataset(dataset_id, principal)
    except NotFoundError:
        # **没注册成功的数据集也必须能删。**
        # 文件保存了但 QGIS 打不开（比如只传了 .shp、缺 .dbf/.shx/.prj 配套文件），
        # 注册中心会把它整个丢掉 —— 于是它既不在索引里、也永远删不掉：
        # 用户传错一次，磁盘上就永久留一份几十 MB 的垃圾。
        # 这里退回到工作区（磁盘目录）去找，找到就照样删。
        scope = _scope_holding(principal, dataset_id)
        if scope is None:
            raise
        workspace = get_workspace(scope)
        if not workspace.delete(dataset_id):
            raise NotFoundError(f"数据集不存在：{dataset_id}")
        await run_qgis(registry.scan)
        return {
            "deleted": dataset_id,
            "scope": scope,
            "total_datasets": len(registry.datasets(principal)),
            "orphan": True,
        }
    scope = dataset.scope
    if not principal.can_write(scope):
        raise HTTPException(status_code=403, detail="这个数据集你没有删除权限")
    workspace = get_workspace(scope)
    # 先从索引里摘掉，释放 QGIS 对文件的占用（要在 QGIS 线程上做）
    await run_qgis(registry.forget, dataset_id)
    if not workspace.delete(dataset_id):
        raise NotFoundError(f"数据集不存在：{dataset_id}")
    await run_qgis(registry.scan)
    return {
        "deleted": dataset_id,
        "scope": scope,
        "total_datasets": len(registry.datasets(principal)),
    }


@router.get("/{dataset_id}/files/{filename}", summary="下载原始文件")
async def download_file(
    dataset_id: str, filename: str, principal: Principal = Depends(current_principal)
) -> FileResponse:
    """下载上传的原始文件。"""
    registry = get_registry()
    dataset = registry.get_dataset(dataset_id, principal)
    workspace = get_workspace(dataset.scope)
    try:
        path = workspace.file_path(dataset_id, filename)
    except FileNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    return FileResponse(str(path), filename=path.name)
