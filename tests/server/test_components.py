"""组件文件管理测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

SAMPLE_CODE = '''\
from pydantic import BaseModel, Field
from deepflow import CasewiseComponent, CasewiseOutput, CaseContext


class MyComponent(CasewiseComponent):
    """测试组件"""

    class Config(BaseModel):
        url: str = Field(description="API 地址")
        count: int = Field(default=10, description="数量")

    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        return CasewiseOutput(message="ok")
'''

SAMPLE_CODE_NO_CONFIG = '''\
from deepflow import CasewiseComponent, CasewiseOutput, CaseContext


class SimpleComponent(CasewiseComponent):
    """无 Config 的简单组件"""

    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        return CasewiseOutput(message="ok")
'''


async def _create_pipeline(client: AsyncClient, valid_manifest: dict) -> str:
    resp = await client.post("/api/v1/pipelines", json={"name": "p", "manifest": valid_manifest})
    return resp.json()["id"]


async def test_upload_component(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    resp = await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("my_component.py", SAMPLE_CODE.encode(), "text/x-python")},
    )
    assert resp.status_code == 201
    assert resp.json()["filename"] == "my_component.py"


async def test_upload_non_py_rejected(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    resp = await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("data.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


async def test_list_components_with_schema(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("my_component.py", SAMPLE_CODE.encode(), "text/x-python")},
    )
    resp = await client.get(f"/api/v1/pipelines/{pid}/components")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    comp = items[0]
    assert comp["filename"] == "my_component.py"
    assert comp["class_name"] == "MyComponent"
    assert comp["stage"] == "casewise"
    assert comp["description"] == "测试组件"
    assert comp["config_schema"] is not None
    assert "properties" in comp["config_schema"]
    assert "url" in comp["config_schema"]["properties"]
    assert "count" in comp["config_schema"]["properties"]


async def test_list_components_no_config(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("simple.py", SAMPLE_CODE_NO_CONFIG.encode(), "text/x-python")},
    )
    resp = await client.get(f"/api/v1/pipelines/{pid}/components")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    comp = items[0]
    assert comp["filename"] == "simple.py"
    assert comp["class_name"] == "SimpleComponent"
    assert comp["stage"] == "casewise"
    assert comp["config_schema"] is None


async def test_list_components_invalid_code(client: AsyncClient, valid_manifest: dict):
    """语法错误的 .py 文件应返回 filename 但无元信息。"""
    pid = await _create_pipeline(client, valid_manifest)
    await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("broken.py", b"def ??? broken", "text/x-python")},
    )
    resp = await client.get(f"/api/v1/pipelines/{pid}/components")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["filename"] == "broken.py"
    assert items[0]["config_schema"] is None


async def test_read_component_content(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("reader.py", SAMPLE_CODE.encode(), "text/x-python")},
    )
    resp = await client.get(f"/api/v1/pipelines/{pid}/components/reader.py")
    assert resp.status_code == 200
    assert resp.json()["content"] == SAMPLE_CODE


async def test_read_component_not_found(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    resp = await client.get(f"/api/v1/pipelines/{pid}/components/nonexistent.py")
    assert resp.status_code == 404


async def test_update_component_with_schema(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("updatable.py", b"# v1", "text/x-python")},
    )
    resp = await client.put(
        f"/api/v1/pipelines/{pid}/components/updatable.py",
        json={"content": SAMPLE_CODE},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["class_name"] == "MyComponent"
    assert data["config_schema"] is not None


async def test_delete_component(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    await client.post(
        f"/api/v1/pipelines/{pid}/components",
        files={"file": ("deletable.py", b"# x", "text/x-python")},
    )
    resp = await client.delete(f"/api/v1/pipelines/{pid}/components/deletable.py")
    assert resp.status_code == 204
