"""运行生命周期测试。"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def _create_pipeline(client: AsyncClient, valid_manifest: dict) -> str:
    resp = await client.post("/api/v1/pipelines", json={"name": "run-test", "manifest": valid_manifest})
    return resp.json()["id"]


async def test_create_run(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    resp = await client.post(f"/api/v1/pipelines/{pid}/runs")
    assert resp.status_code == 202
    data = resp.json()
    assert "id" in data
    assert data["status"] in ("pending", "running")


async def test_get_run(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    run_resp = await client.post(f"/api/v1/pipelines/{pid}/runs")
    run_id = run_resp.json()["id"]

    resp = await client.get(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


async def test_get_run_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/runs/nonexistent")
    assert resp.status_code == 404


async def test_list_pipeline_runs(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    await client.post(f"/api/v1/pipelines/{pid}/runs")
    resp = await client.get(f"/api/v1/pipelines/{pid}/runs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


async def test_run_reaches_terminal_state(client: AsyncClient, valid_manifest: dict):
    """运行最终应到达终态（completed 或 failed）。"""
    pid = await _create_pipeline(client, valid_manifest)
    run_resp = await client.post(f"/api/v1/pipelines/{pid}/runs")
    run_id = run_resp.json()["id"]

    for _ in range(50):
        resp = await client.get(f"/api/v1/runs/{run_id}")
        s = resp.json()["status"]
        if s in ("completed", "failed", "cancelled"):
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("Run did not reach terminal state")

    assert s in ("completed", "failed")


async def test_delete_completed_run(client: AsyncClient, valid_manifest: dict):
    pid = await _create_pipeline(client, valid_manifest)
    run_resp = await client.post(f"/api/v1/pipelines/{pid}/runs")
    run_id = run_resp.json()["id"]

    for _ in range(50):
        resp = await client.get(f"/api/v1/runs/{run_id}")
        if resp.json()["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.1)

    resp = await client.delete(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 204


async def test_direct_manifest_run_removed(client: AsyncClient, valid_manifest: dict):
    """POST /api/v1/runs 已移除，应返回 404 或 405。"""
    resp = await client.post("/api/v1/runs", json={"manifest": valid_manifest})
    assert resp.status_code in (404, 405)
