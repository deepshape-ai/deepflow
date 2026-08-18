"""Plugin 组件发现端点。"""

from __future__ import annotations

from fastapi import APIRouter

from deepflow.engine.loader import ComponentLoader
from deepflow.server.models import ComponentInfo

router = APIRouter(prefix="/api/v1/components", tags=["components"])


@router.get("", response_model=list[ComponentInfo])
async def list_components() -> list[ComponentInfo]:
    """列出所有可用的 plugin 组件及其元信息。"""
    return [ComponentInfo(**meta) for meta in ComponentLoader.get_plugin_metadata()]
