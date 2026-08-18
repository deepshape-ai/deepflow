"""共享测试 fixtures。"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from deepflow.server.app import create_app
from deepflow.server.dependencies import init_services
from deepflow.server.services.pipeline_store import PipelineStore
from deepflow.server.services.run_manager import RunManager


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "deepflow-test"


@pytest.fixture
async def client(data_dir: Path) -> AsyncGenerator[AsyncClient, None]:
    app = create_app(data_dir=data_dir)

    # 手动初始化服务（ASGITransport 不触发 lifespan）
    run_manager = RunManager(data_dir / "runs")
    pipeline_store = PipelineStore(data_dir / "pipelines")
    init_services(run_manager, pipeline_store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await run_manager.shutdown()


@pytest.fixture
def valid_manifest() -> dict:
    """最小合法 manifest — 仅 plugin 组件。"""
    return {
        "version": "2.0",
        "name": "test-pipeline",
        "workspace": "./workspace",
        "concurrency": 1,
        "pipeline": {
            "preprocess": [{"src": "builtin:clean_workspace"}],
            "casewise": [],
            "postprocess": [],
        },
    }


@pytest.fixture
def invalid_manifest() -> dict:
    """非法 manifest — 缺少 pipeline 字段。"""
    return {
        "version": "2.0",
        "name": "bad-pipeline",
    }
