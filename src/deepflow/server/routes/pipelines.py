"""Pipeline 配置 CRUD + 组件文件管理端点。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from deepflow.engine.schema_extractor import extract_from_file
from deepflow.models.manifest import Manifest
from deepflow.server.dependencies import get_pipeline_store
from deepflow.server.models import (
    ComponentContentResponse,
    ComponentUpdateRequest,
    CustomComponentInfo,
    PipelineCreateRequest,
    PipelineResponse,
    PipelineUpdateRequest,
)
from deepflow.server.services.pipeline_store import PipelineStore

router = APIRouter(prefix="/api/v1/pipelines", tags=["pipelines"])

# 允许上传的组件文件后缀
_ALLOWED_SUFFIXES = frozenset({".py"})


# ── 导入（字面路由，必须在 /{pipeline_id} 之前）───────────────


@router.post("/import", response_model=PipelineResponse, status_code=status.HTTP_201_CREATED)
async def import_pipeline(
    file: UploadFile,
    store: PipelineStore = Depends(get_pipeline_store),
) -> PipelineResponse:
    """从导出的 YAML 文件创建 Pipeline。"""
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件编码必须为 UTF-8") from e
    try:
        return store.import_yaml(text)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e


# ── Pipeline CRUD ─────────────────────────────────────────────


@router.post("", response_model=PipelineResponse, status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    body: PipelineCreateRequest,
    store: PipelineStore = Depends(get_pipeline_store),
) -> PipelineResponse:
    try:
        Manifest.model_validate(body.manifest)
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=e.errors()) from e
    return store.create(body.name, body.manifest)


@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    store: PipelineStore = Depends(get_pipeline_store),
) -> list[PipelineResponse]:
    return store.list_all()


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    pipeline_id: str,
    store: PipelineStore = Depends(get_pipeline_store),
) -> PipelineResponse:
    pipeline = store.get(pipeline_id)
    if pipeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    return pipeline


@router.put("/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(
    pipeline_id: str,
    body: PipelineUpdateRequest,
    store: PipelineStore = Depends(get_pipeline_store),
) -> PipelineResponse:
    if body.manifest is not None:
        try:
            Manifest.model_validate(body.manifest)
        except ValidationError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=e.errors()) from e
    pipeline = store.update(pipeline_id, body.name, body.manifest)
    if pipeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    return pipeline


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_pipeline(
    pipeline_id: str,
    store: PipelineStore = Depends(get_pipeline_store),
):
    if not store.delete(pipeline_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")


# ── 导出 ─────────────────────────────────────────────────────


@router.get("/{pipeline_id}/export")
async def export_pipeline(
    pipeline_id: str,
    store: PipelineStore = Depends(get_pipeline_store),
) -> PlainTextResponse:
    """导出 Pipeline 为 YAML 文本（manifest + 组件源码）。"""
    yaml_text = store.export_yaml(pipeline_id)
    if yaml_text is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")

    pipeline = store.get(pipeline_id)
    safe_name = (pipeline.name if pipeline else pipeline_id).replace(" ", "_")

    return PlainTextResponse(
        yaml_text,
        media_type="text/yaml",
        headers={
            "Content-Disposition": f"attachment; filename=\"{pipeline_id}.yaml\"; filename*=UTF-8''{quote(safe_name)}.yaml"
        },
    )


# ── 组件文件管理 ──────────────────────────────────────────────


@router.post("/{pipeline_id}/components", status_code=status.HTTP_201_CREATED)
async def upload_component(
    pipeline_id: str,
    file: UploadFile,
    store: PipelineStore = Depends(get_pipeline_store),
) -> dict[str, str]:
    """上传自定义组件 .py 文件到 pipeline 目录。"""
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")

    filename = file.filename or "component.py"
    if not any(filename.endswith(s) for s in _ALLOWED_SUFFIXES):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .py files are allowed")

    content = await file.read()
    try:
        store.save_component(pipeline_id, filename, content)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return {"filename": filename, "path": f"./components/{filename}"}


@router.get("/{pipeline_id}/components", response_model=list[CustomComponentInfo])
async def list_components(
    pipeline_id: str,
    store: PipelineStore = Depends(get_pipeline_store),
) -> list[CustomComponentInfo]:
    """列出 pipeline 的所有自定义组件及其元信息。"""
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")

    results = []
    for path in store.list_component_paths(pipeline_id):
        meta = extract_from_file(path)
        if meta:
            results.append(CustomComponentInfo(
                filename=meta.filename,
                class_name=meta.class_name,
                stage=meta.stage,
                description=meta.description,
                config_schema=meta.config_schema,
            ))
        else:
            results.append(CustomComponentInfo(filename=path.name))
    return results


@router.get("/{pipeline_id}/components/{filename}", response_model=ComponentContentResponse)
async def get_component(
    pipeline_id: str,
    filename: str,
    store: PipelineStore = Depends(get_pipeline_store),
) -> ComponentContentResponse:
    """读取组件文件内容。"""
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    try:
        content = store.read_component(pipeline_id, filename)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Component {filename} not found")
    return ComponentContentResponse(filename=filename, content=content)


@router.put("/{pipeline_id}/components/{filename}", response_model=CustomComponentInfo)
async def update_component(
    pipeline_id: str,
    filename: str,
    body: ComponentUpdateRequest,
    store: PipelineStore = Depends(get_pipeline_store),
) -> CustomComponentInfo:
    """更新组件文件内容并提取元信息。"""
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    if not filename.endswith(".py"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .py files are allowed")
    try:
        path = store.save_component(pipeline_id, filename, body.content.encode("utf-8"))
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    meta = extract_from_file(path)
    if meta:
        return CustomComponentInfo(
            filename=meta.filename,
            class_name=meta.class_name,
            stage=meta.stage,
            description=meta.description,
            config_schema=meta.config_schema,
        )
    return CustomComponentInfo(filename=filename)


@router.delete("/{pipeline_id}/components/{filename}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_component(
    pipeline_id: str,
    filename: str,
    store: PipelineStore = Depends(get_pipeline_store),
):
    """删除指定组件文件。"""
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    try:
        deleted = store.delete_component(pipeline_id, filename)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Component {filename} not found")


# ── Hook 文件管理 ─────────────────────────────────────────────


@router.post("/{pipeline_id}/hooks", status_code=status.HTTP_201_CREATED)
async def upload_hook(
    pipeline_id: str,
    file: UploadFile,
    store: PipelineStore = Depends(get_pipeline_store),
) -> dict[str, str]:
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    filename = file.filename or "hook.py"
    try:
        store.save_hook(pipeline_id, filename, await file.read())
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return {"filename": filename, "path": f"./hooks/{filename}"}


@router.get("/{pipeline_id}/hooks", response_model=list[str])
async def list_hooks(
    pipeline_id: str,
    store: PipelineStore = Depends(get_pipeline_store),
) -> list[str]:
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    return [path.name for path in store.list_hook_paths(pipeline_id)]


@router.get("/{pipeline_id}/hooks/{filename}", response_model=ComponentContentResponse)
async def get_hook(
    pipeline_id: str,
    filename: str,
    store: PipelineStore = Depends(get_pipeline_store),
) -> ComponentContentResponse:
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    try:
        content = store.read_hook(pipeline_id, filename)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hook {filename} not found")
    return ComponentContentResponse(filename=filename, content=content)


@router.put("/{pipeline_id}/hooks/{filename}")
async def update_hook(
    pipeline_id: str,
    filename: str,
    body: ComponentUpdateRequest,
    store: PipelineStore = Depends(get_pipeline_store),
) -> dict[str, str]:
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    try:
        store.save_hook(pipeline_id, filename, body.content.encode("utf-8"))
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return {"filename": filename, "path": f"./hooks/{filename}"}


@router.delete(
    "/{pipeline_id}/hooks/{filename}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_hook(
    pipeline_id: str,
    filename: str,
    store: PipelineStore = Depends(get_pipeline_store),
):
    if store.get(pipeline_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")
    try:
        deleted = store.delete_hook(pipeline_id, filename)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hook {filename} not found")
