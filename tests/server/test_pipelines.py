"""Pipeline CRUD 和 manifest 校验测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_create_pipeline_valid(client: AsyncClient, valid_manifest: dict):
    resp = await client.post(
        "/api/v1/pipelines",
        json={"name": "test", "manifest": valid_manifest},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test"
    assert "id" in data


async def test_create_pipeline_invalid_manifest(client: AsyncClient, invalid_manifest: dict):
    resp = await client.post(
        "/api/v1/pipelines",
        json={"name": "bad", "manifest": invalid_manifest},
    )
    assert resp.status_code == 422


async def test_list_pipelines(client: AsyncClient, valid_manifest: dict):
    await client.post("/api/v1/pipelines", json={"name": "p1", "manifest": valid_manifest})
    resp = await client.get("/api/v1/pipelines")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


async def test_get_pipeline(client: AsyncClient, valid_manifest: dict):
    create_resp = await client.post(
        "/api/v1/pipelines",
        json={"name": "p1", "manifest": valid_manifest},
    )
    pid = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/pipelines/{pid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pid


async def test_get_pipeline_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/pipelines/nonexistent")
    assert resp.status_code == 404


async def test_update_pipeline(client: AsyncClient, valid_manifest: dict):
    create_resp = await client.post(
        "/api/v1/pipelines",
        json={"name": "original", "manifest": valid_manifest},
    )
    pid = create_resp.json()["id"]
    resp = await client.put(
        f"/api/v1/pipelines/{pid}",
        json={"name": "updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated"


async def test_update_pipeline_invalid_manifest(client: AsyncClient, valid_manifest: dict, invalid_manifest: dict):
    create_resp = await client.post(
        "/api/v1/pipelines",
        json={"name": "p1", "manifest": valid_manifest},
    )
    pid = create_resp.json()["id"]
    resp = await client.put(
        f"/api/v1/pipelines/{pid}",
        json={"manifest": invalid_manifest},
    )
    assert resp.status_code == 422


async def test_delete_pipeline(client: AsyncClient, valid_manifest: dict):
    create_resp = await client.post(
        "/api/v1/pipelines",
        json={"name": "to-delete", "manifest": valid_manifest},
    )
    pid = create_resp.json()["id"]
    resp = await client.delete(f"/api/v1/pipelines/{pid}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/pipelines/{pid}")
    assert resp.status_code == 404
