"""Integration tests for readiness failure path (HTTP 503)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentblue.db.session import get_db

pytestmark = pytest.mark.integration


@pytest.fixture
async def broken_client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Client with a broken database connection override.

    Uses a port that does not exist to guarantee connection failure.
    This is deterministic and does not require stopping Docker.
    """
    broken_engine = create_async_engine(
        "postgresql+asyncpg://agentblue:agentblue@localhost:19999/agentblue_dev",
        pool_pre_ping=True,
    )
    broken_session_factory = async_sessionmaker(
        bind=broken_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _broken_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with broken_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _broken_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
    await broken_engine.dispose()


async def test_health_ready_returns_503_when_db_unavailable(
    broken_client: AsyncClient,
) -> None:
    """Readiness probe returns 503 when PostgreSQL is unreachable."""
    response = await broken_client.get("/api/v1/health/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "error"
    assert data["database"] == "unavailable"


async def test_health_ready_error_does_not_expose_secrets(
    broken_client: AsyncClient,
) -> None:
    """Readiness failure response must not leak credentials or stack traces."""
    response = await broken_client.get("/api/v1/health/ready")
    body = response.text.lower()

    # Must not contain connection details
    assert "agentblue" not in body or "unavailable" in body
    assert "password" not in body
    assert "traceback" not in body
    assert "stack" not in body
