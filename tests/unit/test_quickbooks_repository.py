"""Tests for QuickBooks token repository interface and in-memory implementation.

No external dependencies.
"""

from __future__ import annotations

import time

import pytest

from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenStorageError
from agentblue.integrations.quickbooks.models import TokenResponse, parse_token_response
from agentblue.integrations.quickbooks.repository import InMemoryTokenRepository

pytestmark = pytest.mark.unit


def _make_token(realm_id: str = "123") -> TokenResponse:
    return parse_token_response(
        {
            "access_token": "test-access",
            "refresh_token": "test-refresh",
            "expires_in": 3600,
            "x_refresh_token_expires_in": 8640000,
            "token_type": "bearer",
            "realm_id": realm_id,
            "issued_at": int(time.time()),
        }
    )


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


class TestSave:
    @pytest.mark.asyncio
    async def test_save_and_retrieve(self) -> None:
        repo = InMemoryTokenRepository()
        token = _make_token()
        await repo.save(token)
        result = await repo.get_by_realm("123")
        assert result is not None
        assert result.realm_id == "123"

    @pytest.mark.asyncio
    async def test_save_without_realm_id_raises(self) -> None:
        repo = InMemoryTokenRepository()
        token = parse_token_response(
            {
                "access_token": "test",
                "refresh_token": "test",
                "expires_in": 3600,
                "x_refresh_token_expires_in": 8640000,
                "token_type": "bearer",
                "realm_id": "",
                "issued_at": int(time.time()),
            }
        )
        with pytest.raises(QuickBooksTokenStorageError, match="realm_id"):
            await repo.save(token)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self) -> None:
        repo = InMemoryTokenRepository()
        result = await repo.get_by_realm("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_different_realms(self) -> None:
        repo = InMemoryTokenRepository()
        await repo.save(_make_token("aaa"))
        await repo.save(_make_token("bbb"))
        result = await repo.get_by_realm("aaa")
        assert result is not None
        assert result.realm_id == "aaa"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_existing(self) -> None:
        repo = InMemoryTokenRepository()
        await repo.save(_make_token("123"))
        updated = parse_token_response(
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 7200,
                "x_refresh_token_expires_in": 8640000,
                "token_type": "bearer",
                "realm_id": "123",
                "issued_at": int(time.time()),
            }
        )
        await repo.update(updated)
        result = await repo.get_by_realm("123")
        assert result is not None

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self) -> None:
        repo = InMemoryTokenRepository()
        token = _make_token("missing")
        with pytest.raises(QuickBooksTokenStorageError, match="No token found"):
            await repo.update(token)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self) -> None:
        repo = InMemoryTokenRepository()
        await repo.save(_make_token("123"))
        assert await repo.delete("123") is True
        assert await repo.get_by_realm("123") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self) -> None:
        repo = InMemoryTokenRepository()
        assert await repo.delete("missing") is False


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_in_memory_is_token_repository(self) -> None:
        from agentblue.integrations.quickbooks.repository import TokenRepository

        repo = InMemoryTokenRepository()
        assert isinstance(repo, TokenRepository)
