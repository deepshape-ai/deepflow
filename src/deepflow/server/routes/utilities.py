"""工具端点：manifest 校验、健康检查。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Query
from pydantic import ValidationError

from deepflow.models.manifest import Manifest
from deepflow.server.models import ValidateRequest, ValidateResponse

router = APIRouter(prefix="/api/v1", tags=["utilities"])


@router.post("/validate", response_model=ValidateResponse)
async def validate_manifest(body: ValidateRequest) -> ValidateResponse:
    """校验 manifest 结构，不创建 Pipeline。"""
    try:
        Manifest.model_validate(body.manifest)
    except ValidationError as e:
        return ValidateResponse(valid=False, errors=e.errors())
    return ValidateResponse(valid=True)


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/env/check")
async def check_env_vars(
    keys: str = Query(description="逗号分隔的环境变量名"),
) -> dict[str, bool]:
    """检查指定环境变量是否在服务器环境中已设置。只返回存在性，不暴露值。"""
    return {k.strip(): k.strip() in os.environ for k in keys.split(",") if k.strip()}
