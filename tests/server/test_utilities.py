"""工具端点测试：validate、health、plugin 组件列表。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_validate_valid_manifest(client: AsyncClient, valid_manifest: dict):
    resp = await client.post("/api/v1/validate", json={"manifest": valid_manifest})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["errors"] == []


async def test_validate_invalid_manifest(client: AsyncClient, invalid_manifest: dict):
    resp = await client.post("/api/v1/validate", json={"manifest": invalid_manifest})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert len(data["errors"]) > 0


async def test_health_check(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_list_builtin_components(client: AsyncClient):
    resp = await client.get("/api/v1/components")
    assert resp.status_code == 200
    components = resp.json()
    assert len(components) == 2
    names = {c["name"] for c in components}
    assert names == {"builtin:clean_workspace", "builtin:clean_casespace"}
    for c in components:
        assert "description" in c
        assert "stage" in c
        assert "config_schema" in c
