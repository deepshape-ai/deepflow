"""Pipeline Hook 源码管理与导入导出测试。"""

from __future__ import annotations

import io

import pytest
import yaml
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

HOOK_CODE = '''\
from deepflow import Hook


class Notify(Hook):
    def on_run_finish(self, ctx, status, error):
        pass
'''


async def _create_pipeline(client: AsyncClient, valid_manifest: dict) -> str:
    response = await client.post(
        "/api/v1/pipelines", json={"name": "hooks", "manifest": valid_manifest}
    )
    return response.json()["id"]


async def test_hook_crud(client: AsyncClient, valid_manifest: dict):
    pipeline_id = await _create_pipeline(client, valid_manifest)
    uploaded = await client.post(
        f"/api/v1/pipelines/{pipeline_id}/hooks",
        files={"file": ("notify.py", HOOK_CODE.encode(), "text/x-python")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["path"] == "./hooks/notify.py"
    assert (await client.get(f"/api/v1/pipelines/{pipeline_id}/hooks")).json() == ["notify.py"]

    content = await client.get(f"/api/v1/pipelines/{pipeline_id}/hooks/notify.py")
    assert content.json()["content"] == HOOK_CODE
    updated = await client.put(
        f"/api/v1/pipelines/{pipeline_id}/hooks/notify.py",
        json={"content": HOOK_CODE + "\n# v2\n"},
    )
    assert updated.status_code == 200
    assert (await client.delete(
        f"/api/v1/pipelines/{pipeline_id}/hooks/notify.py"
    )).status_code == 204


async def test_hook_export_import_roundtrip(client: AsyncClient, valid_manifest: dict):
    pipeline_id = await _create_pipeline(client, valid_manifest)
    await client.post(
        f"/api/v1/pipelines/{pipeline_id}/hooks",
        files={"file": ("notify.py", HOOK_CODE.encode(), "text/x-python")},
    )

    exported = await client.get(f"/api/v1/pipelines/{pipeline_id}/export")
    payload = yaml.safe_load(exported.text)
    assert payload["deepflow_export"] == "2.0"
    assert payload["hooks"] == {"notify.py": HOOK_CODE}

    imported = await client.post(
        "/api/v1/pipelines/import",
        files={"file": ("pipeline.yaml", io.BytesIO(exported.content), "text/yaml")},
    )
    imported_id = imported.json()["id"]
    restored = await client.get(f"/api/v1/pipelines/{imported_id}/hooks/notify.py")
    assert restored.json()["content"] == HOOK_CODE


async def test_import_rejects_unsupported_export_version(client: AsyncClient):
    imported = await client.post(
        "/api/v1/pipelines/import",
        files={
            "file": (
                "pipeline.yaml",
                io.BytesIO(b'deepflow_export: "1.0"\nmanifest: {}\n'),
                "text/yaml",
            )
        },
    )
    assert imported.status_code == 422
    assert "2.0" in imported.json()["detail"]


async def test_hook_filename_traversal_rejected(client: AsyncClient, valid_manifest: dict):
    pipeline_id = await _create_pipeline(client, valid_manifest)
    response = await client.post(
        f"/api/v1/pipelines/{pipeline_id}/hooks",
        files={"file": ("../evil.py", b"# no", "text/x-python")},
    )
    assert response.status_code == 400
