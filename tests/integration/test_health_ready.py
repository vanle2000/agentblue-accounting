"""Integration tests for GET /api/v1/health/ready (happy path)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_health_ready_returns_200(client: AsyncClient) -> None:
    """Readiness probe returns 200 when PostgreSQL is reachable."""
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


async def test_health_ready_has_correct_content_type(client: AsyncClient) -> None:
    """Readiness probe returns JSON content type."""
    response = await client.get("/api/v1/health/ready")
    assert response.headers["content-type"] == "application/json"
