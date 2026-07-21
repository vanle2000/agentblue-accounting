"""Unit tests for GET /api/v1/health/live."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


async def test_health_live_returns_200(client: AsyncClient) -> None:
    """Liveness probe returns 200 with expected JSON body."""
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "agentblue-accounting"


async def test_health_live_has_correct_content_type(client: AsyncClient) -> None:
    """Liveness probe returns JSON content type."""
    response = await client.get("/api/v1/health/live")
    assert response.headers["content-type"] == "application/json"
