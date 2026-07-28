"""Shared pytest fixtures for Agent Blue Accounting tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentblue.main import create_app


@pytest.fixture
def app() -> FastAPI:
    """Create a fresh FastAPI application for testing."""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client for testing."""
    from agentblue.security.auth import get_authenticated_principal
    from agentblue.security.principal import Principal
    from agentblue.security.roles import Role

    mock_principal = Principal(
        principal_id="test-user",
        principal_type="human",
        email="test@example.com",
        display_name="Test User",
        active=True,
        roles=frozenset({Role.ADMIN}),
        realm_ids=frozenset({"dev-realm"}),
        auth_method="bypass",
        correlation_id="test-correlation-id",
    )
    app.dependency_overrides[get_authenticated_principal] = (
        lambda: mock_principal
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac
