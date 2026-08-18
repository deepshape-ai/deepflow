"""
FastAPI 依赖注入容器。

模块级单例，由 app.py 的 lifespan 初始化，通过 Depends() 注入路由。
遵循 "显胜于隐" 原则：服务实例的创建和注入路径清晰可溯。
"""

from __future__ import annotations

from deepflow.server.services.pipeline_store import PipelineStore
from deepflow.server.services.run_manager import RunManager

_run_manager: RunManager | None = None
_pipeline_store: PipelineStore | None = None


def init_services(run_manager: RunManager, pipeline_store: PipelineStore) -> None:
    """应用启动时调用一次，注册服务单例。"""
    global _run_manager, _pipeline_store
    _run_manager = run_manager
    _pipeline_store = pipeline_store


def get_run_manager() -> RunManager:
    """FastAPI Depends 注入点。"""
    assert _run_manager is not None, "RunManager not initialized — is the app lifespan configured?"
    return _run_manager


def get_pipeline_store() -> PipelineStore:
    """FastAPI Depends 注入点。"""
    assert _pipeline_store is not None, "PipelineStore not initialized — is the app lifespan configured?"
    return _pipeline_store
