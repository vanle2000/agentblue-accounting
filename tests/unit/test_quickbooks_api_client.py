"""Tests for QuickBooks API client, services, and health check.

Uses mocked HTTP responses. No live QuickBooks API calls.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksApiError,
    QuickBooksAuthenticationError,
    QuickBooksPermissionError,
    QuickBooksRateLimitError,
    QuickBooksResourceNotFoundError,
    QuickBooksServerError,
    QuickBooksTransportError,
    QuickBooksValidationError,
)
from agentblue.integrations.quickbooks.health import check_quickbooks_health
from agentblue.integrations.quickbooks.models import TokenResponse, parse_token_response
from agentblue.integrations.quickbooks.repository import InMemoryTokenRepository
from agentblue.integrations.quickbooks.services import (
    ChartOfAccountsService,
    CompanyInfoService,
    CustomersService,
    TransactionsService,
    VendorsService,
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


def _make_token(realm_id: str = "123") -> TokenResponse:
    return parse_token_response(
        {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
            "x_refresh_token_expires_in": 8640000,
            "token_type": "bearer",
            "realm_id": realm_id,
            "issued_at": int(time.time()),
        }
    )


def _mock_response(
    status_code: int = 200,
    json_data: dict[str, object] | None = None,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    resp_headers = {"content-type": content_type}
    if headers:
        resp_headers.update(headers)
    return httpx.Response(
        status_code=status_code,
        json=json_data if json_data is not None else {},
        headers=resp_headers,
        request=httpx.Request("GET", "https://example.com"),
    )


def _mock_client(response: httpx.Response) -> AsyncMock:
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.request = AsyncMock(return_value=response)
    mock.aclose = AsyncMock()
    return mock


def _company_info_response() -> dict[str, object]:
    return {
        "CompanyInfo": {
            "Id": "123",
            "CompanyName": "Test Company",
            "LegalName": "Test Company LLC",
        }
    }


# ---------------------------------------------------------------------------
# API Client: successful requests
# ---------------------------------------------------------------------------


class TestApiGetSuccess:
    @pytest.mark.asyncio
    async def test_successful_get(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, _company_info_response()))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                result = await client.get("/v3/company/123/companyinfo/123")
        assert result["CompanyInfo"]["CompanyName"] == "Test Company"

    @pytest.mark.asyncio
    async def test_successful_post(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        post_response = {"Invoice": {"Id": "456", "DocNumber": "1001"}}
        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, post_response))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                result = await client.post(
                    "/v3/company/123/invoice",
                    json_body={"Invoice": {"DocNumber": "1001"}},
                )
        assert result["Invoice"]["Id"] == "456"

    @pytest.mark.asyncio
    async def test_successful_patch(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        patch_response = {"Invoice": {"Id": "456", "SyncToken": "1"}}
        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, patch_response))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                result = await client.patch(
                    "/v3/company/123/invoice",
                    json_body={"Invoice": {"Id": "456", "SyncToken": "0"}},
                )
        assert result["Invoice"]["SyncToken"] == "1"


# ---------------------------------------------------------------------------
# API Client: error mapping
# ---------------------------------------------------------------------------


class TestApiErrorMapping:
    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        token = _make_token()
        await repo.save(token)

        # First call returns 401, refresh succeeds, second call returns 401 again
        auth_error = _mock_response(
            401,
            {"Fault": {"Error": [{"code": "AuthenticationFailed", "Detail": "Token expired"}]}},
            headers={"intuit_tid": "tid-123"},
        )
        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = _mock_client(auth_error)
            mock.request = AsyncMock(return_value=auth_error)
            cls.return_value = mock
            refresh_target = "agentblue.integrations.quickbooks.api_client.refresh_access_token"
            with patch(refresh_target) as mock_refresh:
                mock_refresh.return_value = token
                async with QuickBooksApiClient(settings, repo, "123") as client:
                    with pytest.raises(QuickBooksAuthenticationError) as exc_info:
                        await client.get("/v3/company/123/query")
                    assert exc_info.value.status_code == 401
                    assert exc_info.value.intuit_tid == "tid-123"

    @pytest.mark.asyncio
    async def test_403_raises_permission_error(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(403))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                with pytest.raises(QuickBooksPermissionError):
                    await client.get("/v3/company/123/query")

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(404))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                with pytest.raises(QuickBooksResourceNotFoundError):
                    await client.get("/v3/company/123/account/999")

    @pytest.mark.asyncio
    async def test_400_raises_validation_error(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(400))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                with pytest.raises(QuickBooksValidationError):
                    await client.post("/v3/company/123/invoice", json_body={})

    @pytest.mark.asyncio
    async def test_422_raises_validation_error(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(422))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                with pytest.raises(QuickBooksValidationError):
                    await client.post("/v3/company/123/invoice", json_body={})


# ---------------------------------------------------------------------------
# API Client: rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_429_retries_with_backoff(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        rate_limit_resp = _mock_response(
            429,
            {"Fault": {"Error": [{"code": "REQUEST_THROTTLED"}]}},
            headers={"retry-after": "1"},
        )
        success_resp = _mock_response(200, {"ok": True})

        call_count = 0

        async def side_effect(*args: object, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return rate_limit_resp
            return success_resp

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(side_effect=side_effect)
            mock.aclose = AsyncMock()
            cls.return_value = mock

            sleep_target = (
                "agentblue.integrations.quickbooks.api_client.QuickBooksApiClient._sleep"
            )
            with patch(sleep_target) as mock_sleep:
                mock_sleep.return_value = None
                async with QuickBooksApiClient(settings, repo, "123") as client:
                    result = await client.get("/test")
        assert result == {"ok": True}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_429_exhausted_retries_raises(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(return_value=_mock_response(429))
            mock.aclose = AsyncMock()
            cls.return_value = mock

            with patch("agentblue.integrations.quickbooks.api_client.QuickBooksApiClient._sleep"):
                async with QuickBooksApiClient(settings, repo, "123", max_retries=1) as client:
                    with pytest.raises(QuickBooksRateLimitError):
                        await client.get("/test")


# ---------------------------------------------------------------------------
# API Client: retry policy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    @pytest.mark.asyncio
    async def test_500_retries_then_fails(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(return_value=_mock_response(500))
            mock.aclose = AsyncMock()
            cls.return_value = mock

            with patch("agentblue.integrations.quickbooks.api_client.QuickBooksApiClient._sleep"):
                async with QuickBooksApiClient(settings, repo, "123", max_retries=2) as client:
                    with pytest.raises(QuickBooksServerError):
                        await client.get("/test")
        assert mock.request.call_count == 3

    @pytest.mark.asyncio
    async def test_502_retries(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(return_value=_mock_response(502))
            mock.aclose = AsyncMock()
            cls.return_value = mock

            with patch("agentblue.integrations.quickbooks.api_client.QuickBooksApiClient._sleep"):
                async with QuickBooksApiClient(settings, repo, "123", max_retries=1) as client:
                    with pytest.raises(QuickBooksServerError):
                        await client.get("/test")
        assert mock.request.call_count == 2

    @pytest.mark.asyncio
    async def test_404_no_retry(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(return_value=_mock_response(404))
            mock.aclose = AsyncMock()
            cls.return_value = mock

            async with QuickBooksApiClient(settings, repo, "123", max_retries=3) as client:
                with pytest.raises(QuickBooksResourceNotFoundError):
                    await client.get("/test")
        assert mock.request.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_retries_then_fails(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock.aclose = AsyncMock()
            cls.return_value = mock

            with patch("agentblue.integrations.quickbooks.api_client.QuickBooksApiClient._sleep"):
                async with QuickBooksApiClient(settings, repo, "123", max_retries=1) as client:
                    with pytest.raises(QuickBooksTransportError):
                        await client.get("/test")
        assert mock.request.call_count == 2


# ---------------------------------------------------------------------------
# API Client: automatic token refresh
# ---------------------------------------------------------------------------


class TestAutoRefresh:
    @pytest.mark.asyncio
    async def test_expired_token_triggers_refresh(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        expired = parse_token_response(
            {
                "access_token": "expired-token",
                "refresh_token": "valid-refresh",
                "expires_in": 1,
                "x_refresh_token_expires_in": 8640000,
                "token_type": "bearer",
                "realm_id": "123",
                "issued_at": int(time.time()) - 7200,
            }
        )
        await repo.save(expired)

        fresh_token = _make_token()
        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, {"ok": True}))
            refresh_target = "agentblue.integrations.quickbooks.api_client.refresh_access_token"
            with patch(refresh_target) as mock_refresh:
                mock_refresh.return_value = fresh_token
                async with QuickBooksApiClient(settings, repo, "123") as client:
                    result = await client.get("/test")
        assert result == {"ok": True}
        mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_token_raises(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200))
            async with QuickBooksApiClient(settings, repo, "nonexistent") as client:
                with pytest.raises(QuickBooksApiError, match="No token found"):
                    await client.get("/test")


# ---------------------------------------------------------------------------
# API Client: 401 auto-refresh
# ---------------------------------------------------------------------------


class Test401AutoRefresh:
    @pytest.mark.asyncio
    async def test_401_triggers_single_refresh(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        token = _make_token()
        await repo.save(token)

        resp_401 = _mock_response(401)
        resp_200 = _mock_response(200, {"ok": True})
        call_count = 0

        async def side_effect(*args: object, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp_401
            return resp_200

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(side_effect=side_effect)
            mock.aclose = AsyncMock()
            cls.return_value = mock

            refresh_target = "agentblue.integrations.quickbooks.api_client.refresh_access_token"
            with patch(refresh_target) as mock_refresh:
                mock_refresh.return_value = token
                async with QuickBooksApiClient(settings, repo, "123") as client:
                    result = await client.get("/test")
        assert result == {"ok": True}
        mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    @pytest.mark.asyncio
    async def test_single_page(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        page_response = {
            "QueryResponse": {
                "Account": [{"Id": "1", "Name": "Checking"}, {"Id": "2", "Name": "Savings"}],
                "MaxResults": 2,
                "TotalCount": 2,
            }
        }
        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, page_response))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                result = await client.query_all("Account")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_multi_page(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        page1 = {
            "QueryResponse": {
                "Account": [{"Id": "1"}],
                "MaxResults": 1,
                "TotalCount": 2,
            }
        }
        page2 = {
            "QueryResponse": {
                "Account": [{"Id": "2"}],
                "MaxResults": 1,
                "TotalCount": 2,
            }
        }

        call_count = 0

        async def side_effect(*args: object, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_response(200, page1)
            return _mock_response(200, page2)

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(side_effect=side_effect)
            mock.aclose = AsyncMock()
            cls.return_value = mock

            async with QuickBooksApiClient(settings, repo, "123") as client:
                result = await client.query_all("Account", page_size=1)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------


class TestConnectionPool:
    @pytest.mark.asyncio
    async def test_custom_max_connections(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(return_value=_mock_response(200, {}))
            mock.aclose = AsyncMock()
            cls.return_value = mock

            async with QuickBooksApiClient(settings, repo, "123", max_connections=5) as client:
                await client.get("/test")

        call_kwargs = cls.call_args
        limits = call_kwargs.kwargs.get("limits") or call_kwargs[1].get("limits")
        assert limits.max_connections == 5


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


class TestCompanyInfoService:
    @pytest.mark.asyncio
    async def test_get_company_info(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, _company_info_response()))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                service = CompanyInfoService(client)
                info = await service.get_company_info()
        assert info["CompanyName"] == "Test Company"


class TestDeferredServices:
    @pytest.mark.asyncio
    async def test_chart_of_accounts_not_implemented(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                service = ChartOfAccountsService(client)
                with pytest.raises(NotImplementedError):
                    await service.list_accounts()

    @pytest.mark.asyncio
    async def test_vendors_not_implemented(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                service = VendorsService(client)
                with pytest.raises(NotImplementedError):
                    await service.list_vendors()

    @pytest.mark.asyncio
    async def test_customers_not_implemented(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                service = CustomersService(client)
                with pytest.raises(NotImplementedError):
                    await service.list_customers()

    @pytest.mark.asyncio
    async def test_transactions_not_implemented(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                service = TransactionsService(client)
                with pytest.raises(NotImplementedError):
                    await service.list_transactions()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(200, _company_info_response()))
            async with QuickBooksApiClient(settings, repo, "123") as client:
                result = await check_quickbooks_health(client, environment="sandbox")
        assert result.healthy is True
        assert result.company_name == "Test Company"
        assert result.realm_id == "123"
        assert result.environment == "sandbox"

    @pytest.mark.asyncio
    async def test_unhealthy_api_error(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client(_mock_response(401))
            refresh_target = "agentblue.integrations.quickbooks.api_client.refresh_access_token"
            with patch(refresh_target) as mock_refresh:
                mock_refresh.return_value = _make_token()
                async with QuickBooksApiClient(settings, repo, "123") as client:
                    result = await check_quickbooks_health(client)
        assert result.healthy is False
        assert result.error != ""


# ---------------------------------------------------------------------------
# Secret protection
# ---------------------------------------------------------------------------


class TestSecretProtection:
    @pytest.mark.asyncio
    async def test_no_secrets_in_exceptions(self) -> None:
        settings = _make_settings()
        repo = InMemoryTokenRepository()
        await repo.save(_make_token())

        with patch("agentblue.integrations.quickbooks.api_client.httpx.AsyncClient") as cls:
            mock = AsyncMock()
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=False)
            mock.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock.aclose = AsyncMock()
            cls.return_value = mock

            async with QuickBooksApiClient(settings, repo, "123") as client:
                with pytest.raises(QuickBooksTransportError) as exc_info:
                    await client.get("/test")
        assert "test-access-token" not in str(exc_info.value)
        assert "test-refresh-token" not in str(exc_info.value)
        assert "fake-secret" not in str(exc_info.value)
