"""Integration test fixtures.

Overrides database settings to match the Docker Compose PostgreSQL service
credentials so that integration tests can connect to the development database
regardless of what is in the local .env file.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentblue.config import get_settings
from agentblue.db import session as session_mod
from agentblue.main import create_app

# Docker Compose PostgreSQL defaults from docker-compose.yml.
_DB_HOST = "localhost"
_DB_PORT = "5433"
_DB_USER = "agentblue"
_DB_PASSWORD = "agentblue"
_DB_NAME = "agentblue_dev"


@pytest.fixture(autouse=True)
def _db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override database environment variables to match Docker PostgreSQL.

    Resets the settings cache and the module-level engine/session factory
    so that the next request creates a fresh engine with the overridden
    credentials.
    """
    monkeypatch.setenv("DB_HOST", _DB_HOST)
    monkeypatch.setenv("DB_PORT", _DB_PORT)
    monkeypatch.setenv("DB_USER", _DB_USER)
    monkeypatch.setenv("DB_PASSWORD", _DB_PASSWORD)
    monkeypatch.setenv("DB_NAME", _DB_NAME)
    get_settings.cache_clear()
    session_mod._engine = None
    session_mod._session_factory = None


@pytest.fixture
def app(_db_env: None) -> FastAPI:
    """Create a FastAPI application with Docker database settings."""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client for integration testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
