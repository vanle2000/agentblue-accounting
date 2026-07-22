"""Tests for QuickBooks token client.

Uses mocked HTTP responses. No live Intuit API calls.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentblue.integrations.quickbooks.client import (
    exchange_code_for_token,
    refresh_access_token,
)
from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksTokenExchangeError,
    QuickBooksTokenRefreshError,
)

pytestmark = pytest.mark.unit


def _make_settings() -> QuickBooksOAuthSettings:
    return QuickBooksOAuthSettings(
        client_id="fake-id",
        client_secret="fake-secret",
        redirect_uri="https://localhost/callback",
        environment="sandbox",
        scopes="com.intuit.quickbooks.accounting",
    )


def _token_json() -> dict[str, object]:
    return {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_in": 3600,
        "x_refresh_token_expires_in": 8640000,
        "token_type": "bearer",
        "realm_id": "123",
        "issued_at": int(time.time()),
    }


def _mock_response(
    status_code: int = 200,
    json_data: dict[str, object] | None = None,
    content_type: str = "application/json",
) -> httpx.Response:
    """Build a mock httpx.Response."""
    headers = {"content-type": content_type}
    return httpx.Response(
        status_code=status_code,
        json=json_data or _token_json(),
        headers=headers,
        request=httpx.Request("POST", "https://example.com"),
    )


def _mock_client(response: httpx.Response) -> AsyncMock:
    """Build a mock httpx.AsyncClient."""
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.post = AsyncMock(return_value=response)
    return mock


# ---------------------------------------------------------------------------
# Code exchange: success
# ---------------------------------------------------------------------------


class TestExchangeSuccess:
    @pytest.mark.asyncio
    async def test_successful_exchange(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response())
            token = await exchange_code_for_token(settings, "test-code")
        assert token.token_type == "bearer"
        assert token.realm_id == "123"


# ---------------------------------------------------------------------------
# Code exchange: HTTP errors
# ---------------------------------------------------------------------------


class TestExchangeErrors:
    @pytest.mark.asyncio
    async def test_401_raises_exchange_error(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(401, {"error": "invalid_client"}))
            with pytest.raises(QuickBooksTokenExchangeError, match="invalid client"):
                await exchange_code_for_token(settings, "test-code")

    @pytest.mark.asyncio
    async def test_400_invalid_grant_permanent(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(400, {"error": "invalid_grant"}))
            with pytest.raises(QuickBooksTokenExchangeError, match="permanent"):
                await exchange_code_for_token(settings, "test-code")

    @pytest.mark.asyncio
    async def test_429_rate_limited(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(429))
            with pytest.raises(QuickBooksTokenExchangeError, match="rate limited"):
                await exchange_code_for_token(settings, "test-code")

    @pytest.mark.asyncio
    async def test_500_server_error(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(500))
            with pytest.raises(QuickBooksTokenExchangeError, match="server error"):
                await exchange_code_for_token(settings, "test-code")

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            mock = _mock_client(_mock_response())
            mock.post.side_effect = httpx.TimeoutException("timed out")
            cls.return_value = mock
            with pytest.raises(QuickBooksTokenExchangeError, match="timed out"):
                await exchange_code_for_token(settings, "test-code")

    @pytest.mark.asyncio
    async def test_network_error_raises(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            mock = _mock_client(_mock_response())
            mock.post.side_effect = httpx.ConnectError("refused")
            cls.return_value = mock
            with pytest.raises(QuickBooksTokenExchangeError, match="network"):
                await exchange_code_for_token(settings, "test-code")


# ---------------------------------------------------------------------------
# Token refresh: success
# ---------------------------------------------------------------------------


class TestRefreshSuccess:
    @pytest.mark.asyncio
    async def test_successful_refresh(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response())
            token = await refresh_access_token(settings, "old-refresh")
        assert token.access_token is not None

    @pytest.mark.asyncio
    async def test_rotated_refresh_token_parsed(self) -> None:
        data = _token_json()
        data["refresh_token"] = "rotated-refresh-token"
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, data))
            token = await refresh_access_token(settings, "old-refresh")
        assert "rotated" in token.refresh_token.get_secret_value()


# ---------------------------------------------------------------------------
# Token refresh: errors and retries
# ---------------------------------------------------------------------------


class TestRefreshErrors:
    @pytest.mark.asyncio
    async def test_400_invalid_grant_no_retry(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(400, {"error": "invalid_grant"}))
            with pytest.raises(QuickBooksTokenRefreshError, match="permanent"):
                await refresh_access_token(settings, "bad-refresh")
        # Should only be called once — no retries for permanent errors.
        assert cls.return_value.post.call_count == 1

    @pytest.mark.asyncio
    async def test_500_retries_then_fails(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(500))
            with pytest.raises(QuickBooksTokenRefreshError, match="after 3 attempts"):
                await refresh_access_token(settings, "refresh", max_retries=2)
        assert cls.return_value.post.call_count == 3

    @pytest.mark.asyncio
    async def test_429_retries_then_fails(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(429))
            with pytest.raises(QuickBooksTokenRefreshError):
                await refresh_access_token(settings, "refresh", max_retries=1)
        assert cls.return_value.post.call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_retries_then_fails(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            mock = _mock_client(_mock_response())
            mock.post.side_effect = httpx.TimeoutException("timeout")
            cls.return_value = mock
            with pytest.raises(QuickBooksTokenRefreshError, match="timed out"):
                await refresh_access_token(settings, "refresh", max_retries=1)
        assert mock.post.call_count == 2


# ---------------------------------------------------------------------------
# Secret protection
# ---------------------------------------------------------------------------


class TestClientSecretProtection:
    @pytest.mark.asyncio
    async def test_auth_header_does_not_leak_secret_in_exception(self) -> None:
        settings = _make_settings()
        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as cls:
            mock = _mock_client(_mock_response())
            mock.post.side_effect = httpx.ConnectError("refused")
            cls.return_value = mock
            with pytest.raises(QuickBooksTokenExchangeError) as exc_info:
                await exchange_code_for_token(settings, "code")
        assert "fake-secret" not in str(exc_info.value)
