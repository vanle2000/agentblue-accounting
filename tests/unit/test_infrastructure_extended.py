"""Extended infrastructure tests covering logging, main lifespan, QuickBooks
client/API client, ML inference service, ML services, ML registry loading,
ML router, and ML CLI.

All tests use mocked dependencies — no database or network required.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token_response(
    *,
    expired: bool = False,
    expiring_soon: bool = False,
    realm_id: str = "realm-1",
) -> MagicMock:
    """Create a mock TokenResponse."""
    token = MagicMock()
    token.access_token = SecretStr("test-access-token")
    token.refresh_token = SecretStr("test-refresh-token")
    token.realm_id = realm_id
    if expired:
        token.is_access_token_expired = True
        token.is_access_token_expiring_soon.return_value = True
    elif expiring_soon:
        token.is_access_token_expired = False
        token.is_access_token_expiring_soon.return_value = True
    else:
        token.is_access_token_expired = False
        token.is_access_token_expiring_soon.return_value = False
    return token


def _make_mock_model(
    *,
    model_id: str = "m-001",
    realm_id: str = "realm-1",
    status: str = "CANDIDATE",
    artifact_path: str = "/tmp/model.joblib",
    artifact_uri: str = "",
    artifact_sha256: str = "abc123",
    metrics: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock MlModel."""
    m = MagicMock()
    m.id = model_id
    m.realm_id = realm_id
    m.status = status
    m.artifact_path = artifact_path
    m.artifact_uri = artifact_uri
    m.artifact_sha256 = artifact_sha256
    m.metrics = metrics or {}
    m.name = "test-model"
    m.model_version = "1"
    m.model_type = "HIST_GRADIENT_BOOSTING"
    m.feature_version = "1.0"
    m.code_version = "1.0.0"
    m.calibration_method = "ISOTONIC"
    m.dataset_fingerprint = "abc"
    m.label_policy_version = "1.0"
    m.training_run_id = "run-001"
    m.training_metrics = {}
    m.validation_metrics = {}
    m.test_metrics = {}
    m.calibration_metrics = {}
    m.class_mapping = {}
    m.hyperparameters = {}
    m.promoted_at = None
    m.retired_at = None
    m.created_at = datetime.now(UTC)
    m.updated_at = datetime.now(UTC)
    return m


def _mock_session_empty() -> AsyncMock:
    """Create a mock AsyncSession returning None for queries."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    session.execute.return_value = result_mock
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _mock_session_with(model: Any) -> AsyncMock:
    """Create a mock AsyncSession returning the given object."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = model
    session.execute.return_value = result_mock
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ===========================================================================
# A. Logging Tests
# ===========================================================================


class TestLoggingConfiguration:
    """configure_logging() behavior."""

    def test_configure_logging_development_mode(self) -> None:
        """Development mode uses ConsoleRenderer with colors."""

        from agentblue.logging import configure_logging

        # Should not raise
        configure_logging(level="DEBUG", is_development=True)

        # Root logger should have a handler
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        assert root.level == logging.DEBUG

    def test_configure_logging_production_mode(self) -> None:
        """Production mode uses JSONRenderer."""
        from agentblue.logging import configure_logging

        configure_logging(level="WARNING", is_development=False)

        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_configure_logging_level_uppercase(self) -> None:
        """Level string is uppercased."""
        from agentblue.logging import configure_logging

        configure_logging(level="info", is_development=True)
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_uvicorn_access_suppressed(self) -> None:
        """Uvicorn access logger is set to WARNING."""
        from agentblue.logging import configure_logging

        configure_logging(level="DEBUG", is_development=True)
        assert logging.getLogger("uvicorn.access").level == logging.WARNING


class TestSecretRedaction:
    """_redact_sensitive processor."""

    def test_redacts_password_key(self) -> None:
        """password keys are redacted."""
        from agentblue.logging import _redact_sensitive

        event = {"password": "hunter2", "message": "test"}
        result = _redact_sensitive(None, None, event)  # type: ignore[arg-type]
        assert result["password"] == "[REDACTED]"
        assert result["message"] == "test"

    def test_redacts_token_key(self) -> None:
        """token keys are redacted."""
        from agentblue.logging import _redact_sensitive

        event = {"token": "secret123", "access_token": "abc"}
        result = _redact_sensitive(None, None, event)  # type: ignore[arg-type]
        assert result["token"] == "[REDACTED]"
        assert result["access_token"] == "[REDACTED]"

    def test_redacts_secret_key(self) -> None:
        """secret keys are redacted."""
        from agentblue.logging import _redact_sensitive

        event = {"secret": "my-secret", "api_key": "key123"}
        result = _redact_sensitive(None, None, event)  # type: ignore[arg-type]
        assert result["secret"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"

    def test_redacts_case_insensitive(self) -> None:
        """Redaction is case-insensitive."""
        from agentblue.logging import _redact_sensitive

        event = {"Password": "x", "TOKEN": "y", "Authorization": "z"}
        result = _redact_sensitive(None, None, event)  # type: ignore[arg-type]
        assert result["Password"] == "[REDACTED]"
        assert result["TOKEN"] == "[REDACTED]"
        assert result["Authorization"] == "[REDACTED]"

    def test_preserves_non_sensitive_keys(self) -> None:
        """Non-sensitive keys are preserved."""
        from agentblue.logging import _redact_sensitive

        event = {"user_id": "123", "action": "login", "count": 42}
        result = _redact_sensitive(None, None, event)  # type: ignore[arg-type]
        assert result["user_id"] == "123"
        assert result["action"] == "login"
        assert result["count"] == 42

    def test_redacts_database_url_key(self) -> None:
        """database_url is redacted."""
        from agentblue.logging import _redact_sensitive

        event = {"database_url": "postgresql://user:pass@host/db"}
        result = _redact_sensitive(None, None, event)  # type: ignore[arg-type]
        assert result["database_url"] == "[REDACTED]"


# ===========================================================================
# B. Main / Lifespan Tests
# ===========================================================================


class TestCreateApp:
    """create_app() factory."""

    def test_create_app_returns_fastapi_instance(self) -> None:
        """create_app returns a FastAPI app."""
        from fastapi import FastAPI

        from agentblue.main import create_app

        app = create_app()
        assert isinstance(app, FastAPI)

    def test_create_app_includes_all_routers(self) -> None:
        """All expected routers are included."""
        from agentblue.main import create_app

        app = create_app()
        route_paths = {r.path for r in app.routes}
        # Health router should be present
        assert "/api/v1/health/live" in route_paths
        # ML config endpoint
        assert "/api/v1/ml/config" in route_paths

    def test_create_app_title_and_version(self) -> None:
        """App has correct title and version."""
        from agentblue.main import create_app

        app = create_app()
        assert app.title == "Agent Blue Accounting"
        assert app.version == "0.1.0"

    def test_create_app_lifespan_set(self) -> None:
        """App uses the lifespan context manager."""
        from agentblue.main import create_app

        app = create_app()
        assert app.router.lifespan_context is not None


class TestLifespan:
    """lifespan() context manager."""

    async def test_lifespan_calls_configure_logging(self) -> None:
        """Lifespan calls configure_logging with settings."""
        from agentblue.main import lifespan

        mock_app = MagicMock()

        with (
            patch("agentblue.main.get_settings") as mock_get_settings,
            patch("agentblue.main.configure_logging") as mock_configure,
            patch("agentblue.main.dispose_engine", new_callable=AsyncMock) as mock_dispose,
        ):
            mock_settings = MagicMock()
            mock_settings.log_level = "INFO"
            mock_settings.is_development = True
            mock_get_settings.return_value = mock_settings

            async with lifespan(mock_app):
                mock_configure.assert_called_once_with(
                    level="INFO", is_development=True
                )

            # dispose_engine called on shutdown
            mock_dispose.assert_awaited_once()

    async def test_lifespan_disposes_engine_on_shutdown(self) -> None:
        """Engine is disposed even if an exception occurs during yield."""
        from agentblue.main import lifespan

        mock_app = MagicMock()

        with (
            patch("agentblue.main.get_settings") as mock_get_settings,
            patch("agentblue.main.configure_logging"),
            patch("agentblue.main.dispose_engine", new_callable=AsyncMock) as mock_dispose,
        ):
            mock_settings = MagicMock()
            mock_settings.log_level = "INFO"
            mock_settings.is_development = True
            mock_get_settings.return_value = mock_settings

            with pytest.raises(RuntimeError, match="boom"):
                async with lifespan(mock_app):
                    raise RuntimeError("boom")

            mock_dispose.assert_awaited_once()


# ===========================================================================
# C. QuickBooks Client Tests (refresh_access_token)
# ===========================================================================


class TestRefreshAccessToken:
    """refresh_access_token() with mocked HTTP."""

    async def test_refresh_success(self) -> None:
        """Successful token refresh returns TokenResponse."""
        from agentblue.integrations.quickbooks.client import refresh_access_token
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "x_refresh_token_expires_in": 8640000,
            "token_type": "bearer",
        }
        mock_response.headers = {"content-type": "application/json"}

        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await refresh_access_token(settings, "old-refresh-token")
            assert result is not None

    async def test_refresh_timeout_raises(self) -> None:
        """Timeout after retries raises QuickBooksTokenRefreshError."""
        import httpx

        from agentblue.integrations.quickbooks.client import refresh_access_token
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
        from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenRefreshError

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )

        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("timed out")
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(QuickBooksTokenRefreshError, match="timed out"):
                await refresh_access_token(settings, "refresh-token", max_retries=0)

    async def test_refresh_401_raises_permanent(self) -> None:
        """401 response raises permanent error without retry."""
        from agentblue.integrations.quickbooks.client import refresh_access_token
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
        from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenRefreshError

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(QuickBooksTokenRefreshError, match="invalid client credentials"):
                await refresh_access_token(settings, "refresh-token")

    async def test_refresh_429_retries_then_raises(self) -> None:
        """429 is retried, then raises after exhausting retries."""
        from agentblue.integrations.quickbooks.client import refresh_access_token
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
        from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenRefreshError

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(QuickBooksTokenRefreshError, match="after"):
                await refresh_access_token(settings, "refresh-token", max_retries=1)

    async def test_refresh_500_retries(self) -> None:
        """500 is retried as transient failure."""
        from agentblue.integrations.quickbooks.client import refresh_access_token
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
        from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenRefreshError

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(QuickBooksTokenRefreshError, match="after"):
                await refresh_access_token(settings, "refresh-token", max_retries=0)

    async def test_refresh_400_permanent_error(self) -> None:
        """400 with invalid_grant is permanent."""
        from agentblue.integrations.quickbooks.client import refresh_access_token
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
        from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenRefreshError

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "invalid_grant"}

        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(QuickBooksTokenRefreshError, match="permanent"):
                await refresh_access_token(settings, "refresh-token")

    async def test_refresh_network_error_retries(self) -> None:
        """Network errors are retried then raise."""
        import httpx

        from agentblue.integrations.quickbooks.client import refresh_access_token
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
        from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenRefreshError

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )

        with patch("agentblue.integrations.quickbooks.client.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.HTTPError("connection failed")
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(QuickBooksTokenRefreshError, match="network error"):
                await refresh_access_token(settings, "refresh-token", max_retries=0)


# ===========================================================================
# D. QuickBooks API Client Tests
# ===========================================================================


class TestQuickBooksApiClient:
    """QuickBooksApiClient uncovered paths."""

    async def test_client_context_manager(self) -> None:
        """Client can be used as async context manager."""
        from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )
        repository = MagicMock()

        async with QuickBooksApiClient(settings, repository, "realm-1") as client:
            assert client._http_client is not None

    async def test_client_request_without_context_raises(self) -> None:
        """Making a request without entering context raises RuntimeError."""
        from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )
        repository = MagicMock()

        client = QuickBooksApiClient(settings, repository, "realm-1")
        with pytest.raises(RuntimeError, match="not initialized"):
            await client.get("/test")

    async def test_client_no_token_raises(self) -> None:
        """No token found raises QuickBooksApiError."""
        from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings
        from agentblue.integrations.quickbooks.exceptions import QuickBooksApiError

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )
        repository = AsyncMock()
        repository.get_by_realm.return_value = None

        async with QuickBooksApiClient(settings, repository, "realm-1") as client:
            with pytest.raises(QuickBooksApiError, match="No token found"):
                await client.get("/test")

    def test_backoff_delay_calculation(self) -> None:
        """Backoff delay is exponential with jitter."""
        from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
            api_rate_limit_delay=1.0,
        )
        repository = MagicMock()

        client = QuickBooksApiClient(settings, repository, "realm-1")
        # Attempt 0: base = 2^0 * 1.0 = 1.0, jitter in [0, 0.25]
        delay = client._backoff_delay(0)
        assert 1.0 <= delay <= 1.25

        # Attempt 2: base = 2^2 * 1.0 = 4.0, jitter in [0, 1.0]
        delay = client._backoff_delay(2)
        assert 4.0 <= delay <= 5.0

    def test_build_url(self) -> None:
        """_build_url joins base URL with path."""
        from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
        from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings

        settings = QuickBooksOAuthSettings(
            client_id="cid",
            client_secret=SecretStr("csecret"),
            redirect_uri="https://example.com/callback",
        )
        repository = MagicMock()

        client = QuickBooksApiClient(settings, repository, "realm-1")
        url = client._build_url("/v3/company/realm-1/query")
        assert url.endswith("/v3/company/realm-1/query")


class TestApiErrorMapping:
    """_map_response_error and helper functions."""

    def test_parse_retry_after_valid(self) -> None:
        """Valid retry-after value is parsed."""
        from agentblue.integrations.quickbooks.api_client import _parse_retry_after

        assert _parse_retry_after("5.0") == 5.0
        assert _parse_retry_after("0") == 0.0

    def test_parse_retry_after_none(self) -> None:
        """None returns 0.0."""
        from agentblue.integrations.quickbooks.api_client import _parse_retry_after

        assert _parse_retry_after(None) == 0.0

    def test_parse_retry_after_invalid(self) -> None:
        """Invalid value returns 0.0."""
        from agentblue.integrations.quickbooks.api_client import _parse_retry_after

        assert _parse_retry_after("not-a-number") == 0.0

    def test_extract_intuit_error_with_fault(self) -> None:
        """Extracts error code from Fault.Error structure."""
        from agentblue.integrations.quickbooks.api_client import _extract_intuit_error

        response = MagicMock()
        response.json.return_value = {
            "Fault": {"Error": [{"code": "2020", "Detail": "Too many requests"}]}
        }
        code, detail = _extract_intuit_error(response)
        assert code == "2020"
        assert detail == "Too many requests"

    def test_extract_intuit_error_no_fault(self) -> None:
        """Falls back to top-level 'error' key."""
        from agentblue.integrations.quickbooks.api_client import _extract_intuit_error

        response = MagicMock()
        response.json.return_value = {"error": "invalid_request"}
        code, detail = _extract_intuit_error(response)
        assert code == "invalid_request"

    def test_extract_intuit_error_json_failure(self) -> None:
        """Returns unknown_error if JSON parsing fails."""
        from agentblue.integrations.quickbooks.api_client import _extract_intuit_error

        response = MagicMock()
        response.json.side_effect = ValueError("bad json")
        code, detail = _extract_intuit_error(response)
        assert code == "unknown_error"

    def test_map_response_error_401(self) -> None:
        """401 maps to QuickBooksAuthenticationError."""
        from agentblue.integrations.quickbooks.api_client import _map_response_error
        from agentblue.integrations.quickbooks.exceptions import QuickBooksAuthenticationError

        response = MagicMock()
        response.status_code = 401
        response.headers = {"intuit_tid": "tid-1", "content-type": "application/json"}
        response.json.return_value = {}
        err = _map_response_error(response)
        assert isinstance(err, QuickBooksAuthenticationError)

    def test_map_response_error_403(self) -> None:
        """403 maps to QuickBooksPermissionError."""
        from agentblue.integrations.quickbooks.api_client import _map_response_error
        from agentblue.integrations.quickbooks.exceptions import QuickBooksPermissionError

        response = MagicMock()
        response.status_code = 403
        response.headers = {"intuit_tid": "tid-2", "content-type": "application/json"}
        response.json.return_value = {}
        err = _map_response_error(response)
        assert isinstance(err, QuickBooksPermissionError)

    def test_map_response_error_404(self) -> None:
        """404 maps to QuickBooksResourceNotFoundError."""
        from agentblue.integrations.quickbooks.api_client import _map_response_error
        from agentblue.integrations.quickbooks.exceptions import QuickBooksResourceNotFoundError

        response = MagicMock()
        response.status_code = 404
        response.headers = {"intuit_tid": "tid-3", "content-type": "application/json"}
        response.json.return_value = {}
        err = _map_response_error(response)
        assert isinstance(err, QuickBooksResourceNotFoundError)

    def test_map_response_error_429(self) -> None:
        """429 maps to QuickBooksRateLimitError."""
        from agentblue.integrations.quickbooks.api_client import _map_response_error
        from agentblue.integrations.quickbooks.exceptions import QuickBooksRateLimitError

        response = MagicMock()
        response.status_code = 429
        response.headers = {"intuit_tid": "tid-4", "content-type": "application/json", "retry-after": "5"}
        response.json.return_value = {}
        err = _map_response_error(response)
        assert isinstance(err, QuickBooksRateLimitError)

    def test_map_response_error_400(self) -> None:
        """400 maps to QuickBooksValidationError."""
        from agentblue.integrations.quickbooks.api_client import _map_response_error
        from agentblue.integrations.quickbooks.exceptions import QuickBooksValidationError

        response = MagicMock()
        response.status_code = 400
        response.headers = {"intuit_tid": "tid-5", "content-type": "application/json"}
        response.json.return_value = {}
        err = _map_response_error(response)
        assert isinstance(err, QuickBooksValidationError)

    def test_map_response_error_500(self) -> None:
        """500 maps to QuickBooksServerError."""
        from agentblue.integrations.quickbooks.api_client import _map_response_error
        from agentblue.integrations.quickbooks.exceptions import QuickBooksServerError

        response = MagicMock()
        response.status_code = 500
        response.headers = {"intuit_tid": "tid-6", "content-type": "application/json"}
        response.json.return_value = {}
        err = _map_response_error(response)
        assert isinstance(err, QuickBooksServerError)

    def test_map_response_error_other(self) -> None:
        """Other status codes map to base QuickBooksApiError."""
        from agentblue.integrations.quickbooks.api_client import _map_response_error
        from agentblue.integrations.quickbooks.exceptions import QuickBooksApiError

        response = MagicMock()
        response.status_code = 418
        response.headers = {"intuit_tid": "tid-7", "content-type": "application/json"}
        response.json.return_value = {}
        err = _map_response_error(response)
        assert isinstance(err, QuickBooksApiError)


# ===========================================================================
# E. ML Inference Service Tests
# ===========================================================================


class TestInferenceService:
    """InferenceService.categorize_with_shadow()."""

    async def test_ml_disabled_returns_shadow_none(self) -> None:
        """When ML_ENABLED is False, shadow is None."""
        from agentblue.ml.inference.service import InferenceService

        service = InferenceService(registry=MagicMock(), shadow=MagicMock())
        session = AsyncMock()

        with patch("agentblue.ml.inference.service.ML_ENABLED", False):
            result = await service.categorize_with_shadow(
                session=session,
                realm_id="realm-1",
                categorization_id="cat-001",
                transaction={},
                deterministic_result={"recommended_account_quickbooks_id": "acct-1"},
            )

        assert result["shadow"] is None
        assert result["recommended_account_quickbooks_id"] == "acct-1"

    async def test_no_shadow_model_returns_shadow_none(self) -> None:
        """When no shadow model exists, shadow is None."""
        from agentblue.ml.inference.service import InferenceService

        registry = AsyncMock()
        registry.get_active_shadow.return_value = None

        service = InferenceService(registry=registry, shadow=MagicMock())
        session = AsyncMock()

        with patch("agentblue.ml.inference.service.ML_ENABLED", True):
            result = await service.categorize_with_shadow(
                session=session,
                realm_id="realm-1",
                categorization_id="cat-001",
                transaction={},
                deterministic_result={"recommended_account_quickbooks_id": "acct-1"},
            )

        assert result["shadow"] is None

    async def test_shadow_success_returns_shadow_data(self) -> None:
        """When shadow model exists and inference succeeds, shadow data is returned."""
        from agentblue.ml.inference.service import InferenceService

        shadow_model = _make_mock_model(model_id="m-shadow", status="SHADOW")
        registry = AsyncMock()
        registry.get_active_shadow.return_value = shadow_model

        shadow_runner = AsyncMock()
        shadow_runner.run_shadow.return_value = {
            "ml_top_account": "acct-2",
            "outcome": "DISAGREEMENT",
        }

        service = InferenceService(registry=registry, shadow=shadow_runner)

        with (
            patch("agentblue.ml.inference.service.ML_ENABLED", True),
            patch(
                "agentblue.ml.inference.service.load_registered_model",
                new_callable=AsyncMock,
            ) as mock_load,
        ):
            mock_load.return_value = (MagicMock(), {}, {"acct-1": 0, "acct-2": 1})

            result = await service.categorize_with_shadow(
                session=AsyncMock(),
                realm_id="realm-1",
                categorization_id="cat-001",
                transaction={},
                deterministic_result={"recommended_account_quickbooks_id": "acct-1"},
                feature_vector=MagicMock(),
            )

        assert result["shadow"] is not None
        assert result["shadow"]["ml_top_account"] == "acct-2"

    async def test_shadow_failure_does_not_affect_deterministic(self) -> None:
        """Shadow inference failure sets shadow=None without affecting deterministic."""
        from agentblue.ml.inference.service import InferenceService

        shadow_model = _make_mock_model(model_id="m-shadow", status="SHADOW")
        registry = AsyncMock()
        registry.get_active_shadow.return_value = shadow_model

        service = InferenceService(registry=registry, shadow=MagicMock())

        with (
            patch("agentblue.ml.inference.service.ML_ENABLED", True),
            patch(
                "agentblue.ml.inference.service.load_registered_model",
                new_callable=AsyncMock,
                side_effect=RuntimeError("artifact corrupt"),
            ),
        ):
            det = {"recommended_account_quickbooks_id": "acct-1", "score": 0.95}
            result = await service.categorize_with_shadow(
                session=AsyncMock(),
                realm_id="realm-1",
                categorization_id="cat-001",
                transaction={},
                deterministic_result=det,
            )

        assert result["shadow"] is None
        assert result["recommended_account_quickbooks_id"] == "acct-1"
        assert result["score"] == 0.95


# ===========================================================================
# F. ML Services Tests
# ===========================================================================


class TestMLService:
    """MLService: build_dataset, start_training, activate_shadow."""

    async def test_build_dataset_creates_and_returns(self) -> None:
        """build_dataset creates a MlDataset and returns metadata."""
        from agentblue.ml.services import MLService

        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        service = MLService()
        result = await service.build_dataset(session, realm_id="realm-1")

        assert "dataset_id" in result
        assert "status" in result
        assert result["status"] == "READY"
        session.add.assert_called_once()
        session.flush.assert_called()

    async def test_start_training_not_found_raises(self) -> None:
        """start_training raises MLError when dataset not found."""
        from agentblue.ml.exceptions import MLError
        from agentblue.ml.services import MLService

        session = _mock_session_empty()

        service = MLService()
        with pytest.raises(MLError, match="Dataset not found"):
            await service.start_training(session, dataset_id="nonexistent", realm_id="realm-1")

    async def test_start_training_dataset_not_ready_raises(self) -> None:
        """start_training raises MLError when dataset is not READY."""
        from agentblue.ml.domain import DatasetStatus
        from agentblue.ml.exceptions import MLError
        from agentblue.ml.services import MLService

        dataset = MagicMock()
        dataset.id = "ds-1"
        dataset.status = DatasetStatus.BUILDING.value

        session = _mock_session_with(dataset)

        service = MLService()
        with pytest.raises(MLError, match="not READY"):
            await service.start_training(session, dataset_id="ds-1", realm_id="realm-1")

    async def test_start_training_success(self) -> None:
        """start_training creates a training run when dataset is READY."""
        from agentblue.ml.domain import DatasetStatus, TrainingRunStatus
        from agentblue.ml.services import MLService

        dataset = MagicMock()
        dataset.id = "ds-1"
        dataset.status = DatasetStatus.READY.value

        session = _mock_session_with(dataset)
        # Override flush to simulate auto-ID assignment
        async def mock_flush() -> None:
            for call in session.add.call_args_list:
                obj = call[0][0]
                if hasattr(obj, "id") and obj.id is None:
                    obj.id = "run-auto"

        session.flush = mock_flush

        service = MLService()
        result = await service.start_training(
            session, dataset_id="ds-1", realm_id="realm-1"
        )

        assert "training_run_id" in result
        assert result["status"] == TrainingRunStatus.PENDING.value

    async def test_activate_shadow_delegates_to_registry(self) -> None:
        """activate_shadow delegates to registry.transition_status."""
        from agentblue.ml.services import MLService

        mock_model = MagicMock()
        mock_model.id = "m-001"
        mock_model.realm_id = "realm-1"
        mock_model.status = "SHADOW"

        registry = AsyncMock()
        registry.transition_status.return_value = mock_model

        service = MLService(registry=registry)
        result = await service.activate_shadow(AsyncMock(), model_id="m-001")

        assert result["model_id"] == "m-001"
        assert result["status"] == "SHADOW"
        registry.transition_status.assert_awaited_once()


# ===========================================================================
# G. ML Registry Loading Tests
# ===========================================================================


class TestLoadRegisteredModel:
    """load_registered_model() function."""

    async def test_model_not_found_raises(self) -> None:
        """ModelNotFoundError when model doesn't exist."""
        from agentblue.ml.exceptions import ModelNotFoundError
        from agentblue.ml.registry.loading import load_registered_model

        session = _mock_session_empty()

        with patch("agentblue.ml.registry.loading.ModelRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_model.return_value = None
            mock_reg_cls.return_value = mock_reg

            with pytest.raises(ModelNotFoundError, match="Model not found"):
                await load_registered_model(session, "nonexistent")

    async def test_no_artifact_path_raises(self) -> None:
        """ArtifactError when model has no artifact_path or artifact_uri."""
        from agentblue.ml.exceptions import ArtifactError
        from agentblue.ml.registry.loading import load_registered_model

        model = _make_mock_model(artifact_path="", artifact_uri="", artifact_sha256="abc")
        session = _mock_session_with(model)

        with patch("agentblue.ml.registry.loading.ModelRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_model.return_value = model
            mock_reg_cls.return_value = mock_reg

            with pytest.raises(ArtifactError, match="no artifact path"):
                await load_registered_model(session, "m-001")

    async def test_no_artifact_hash_raises(self) -> None:
        """ArtifactError when model has no artifact_sha256."""
        from agentblue.ml.exceptions import ArtifactError
        from agentblue.ml.registry.loading import load_registered_model

        model = _make_mock_model(artifact_path="/tmp/model.joblib", artifact_sha256="")
        session = _mock_session_with(model)

        with patch("agentblue.ml.registry.loading.ModelRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_model.return_value = model
            mock_reg_cls.return_value = mock_reg

            with pytest.raises(ArtifactError, match="no artifact hash"):
                await load_registered_model(session, "m-001")

    async def test_success_loads_model(self) -> None:
        """Successful load returns (model_obj, calibration_params, class_mapping)."""
        from agentblue.ml.registry.loading import load_registered_model

        model = _make_mock_model(
            artifact_path="/tmp/model.joblib",
            artifact_sha256="abc123",
            metrics={
                "calibration_params": {"method": "isotonic"},
                "class_mapping": {"acct_1": 0, "acct_2": 1},
            },
        )
        session = _mock_session_with(model)

        mock_artifact_mgr = MagicMock()
        mock_artifact_mgr.load_artifact.return_value = MagicMock()

        with patch("agentblue.ml.registry.loading.ModelRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_model.return_value = model
            mock_reg_cls.return_value = mock_reg

            model_obj, cal_params, class_map = await load_registered_model(
                session, "m-001", artifact_manager=mock_artifact_mgr
            )

        assert model_obj is not None
        assert cal_params == {"method": "isotonic"}
        assert class_map == {"acct_1": 0, "acct_2": 1}

    async def test_artifact_uri_preferred_over_artifact_path(self) -> None:
        """artifact_uri is used when both artifact_uri and artifact_path are set."""
        from agentblue.ml.registry.loading import load_registered_model

        model = _make_mock_model(
            artifact_path="/tmp/old.joblib",
            artifact_uri="s3://bucket/model.joblib",
            artifact_sha256="abc123",
            metrics={"class_mapping": {"a": 0}},
        )
        session = _mock_session_with(model)

        mock_artifact_mgr = MagicMock()
        mock_artifact_mgr.load_artifact.return_value = MagicMock()

        with patch("agentblue.ml.registry.loading.ModelRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_model.return_value = model
            mock_reg_cls.return_value = mock_reg

            await load_registered_model(
                session, "m-001", artifact_manager=mock_artifact_mgr
            )

        # Verify artifact_uri was used, not artifact_path
        mock_artifact_mgr.load_artifact.assert_called_once_with(
            uri="s3://bucket/model.joblib",
            expected_sha256="abc123",
        )

    async def test_metrics_without_calibration_params(self) -> None:
        """Metrics without calibration_params returns empty dict."""
        from agentblue.ml.registry.loading import load_registered_model

        model = _make_mock_model(
            artifact_path="/tmp/model.joblib",
            artifact_sha256="abc123",
            metrics={"accuracy": 0.95},
        )
        session = _mock_session_with(model)

        mock_artifact_mgr = MagicMock()
        mock_artifact_mgr.load_artifact.return_value = MagicMock()

        with patch("agentblue.ml.registry.loading.ModelRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_model.return_value = model
            mock_reg_cls.return_value = mock_reg

            _, cal_params, class_map = await load_registered_model(
                session, "m-001", artifact_manager=mock_artifact_mgr
            )

        assert cal_params == {}
        assert class_map == {}

    async def test_non_dict_metrics_handled(self) -> None:
        """Non-dict metrics are handled gracefully."""
        from agentblue.ml.registry.loading import load_registered_model

        model = _make_mock_model(
            artifact_path="/tmp/model.joblib",
            artifact_sha256="abc123",
            metrics=None,
        )
        session = _mock_session_with(model)

        mock_artifact_mgr = MagicMock()
        mock_artifact_mgr.load_artifact.return_value = MagicMock()

        with patch("agentblue.ml.registry.loading.ModelRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_model.return_value = model
            mock_reg_cls.return_value = mock_reg

            _, cal_params, class_map = await load_registered_model(
                session, "m-001", artifact_manager=mock_artifact_mgr
            )

        assert cal_params == {}
        assert class_map == {}


# ===========================================================================
# H. ML Router Tests
# ===========================================================================


class TestMLRouterAdditional:
    """Additional ML router endpoint tests with mocked DB."""

    async def test_get_dataset_not_found(self) -> None:
        """GET /datasets/{id} returns 404 for nonexistent."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from agentblue.ml.router import router

        app = FastAPI()
        app.include_router(router)

        # Override get_db dependency
        session = _mock_session_empty()
        from agentblue.db.session import get_db

        async def override_get_db():  # type: ignore[no-untyped-def]
            yield session

        app.dependency_overrides[get_db] = override_get_db

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/ml/datasets/nonexistent")
            assert resp.status_code == 404

    async def test_get_training_run_not_found(self) -> None:
        """GET /training-runs/{id} returns 404 for nonexistent."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from agentblue.ml.router import router

        app = FastAPI()
        app.include_router(router)

        session = _mock_session_empty()
        from agentblue.db.session import get_db

        async def override_get_db():  # type: ignore[no-untyped-def]
            yield session

        app.dependency_overrides[get_db] = override_get_db

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/ml/training-runs/nonexistent")
            assert resp.status_code == 404

    async def test_get_model_not_found(self) -> None:
        """GET /models/{id} returns 404 for nonexistent."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from agentblue.ml.router import router

        app = FastAPI()
        app.include_router(router)

        session = _mock_session_empty()
        from agentblue.db.session import get_db

        async def override_get_db():  # type: ignore[no-untyped-def]
            yield session

        app.dependency_overrides[get_db] = override_get_db

        # Also need to patch the module-level _registry used by get_model
        with patch("agentblue.ml.router._registry") as mock_registry:
            mock_registry.get_model = AsyncMock(return_value=None)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/v1/ml/models/nonexistent")
                assert resp.status_code == 404

    async def test_ml_config_endpoint(self) -> None:
        """GET /config returns ML configuration."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from agentblue.ml.router import router

        app = FastAPI()
        app.include_router(router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/ml/config")
            assert resp.status_code == 200
            data = resp.json()
            assert "enabled" in data
            assert "inference_mode" in data
            assert "feature_version" in data
            assert "code_version" in data
            assert "top_k" in data
            assert "inference_timeout_ms" in data

    async def test_model_metrics_not_found(self) -> None:
        """GET /models/{id}/metrics returns 404 for nonexistent."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from agentblue.ml.router import router

        app = FastAPI()
        app.include_router(router)

        with patch("agentblue.ml.router._registry") as mock_registry:
            mock_registry.get_model = AsyncMock(return_value=None)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/v1/ml/models/nonexistent/metrics")
                assert resp.status_code == 404

    async def test_drift_report_model_not_found(self) -> None:
        """POST /drift-reports returns 404 when model doesn't exist."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from agentblue.ml.router import router

        app = FastAPI()
        app.include_router(router)

        session = _mock_session_empty()
        from agentblue.db.session import get_db

        async def override_get_db():  # type: ignore[no-untyped-def]
            yield session

        app.dependency_overrides[get_db] = override_get_db

        with patch("agentblue.ml.router._registry") as mock_registry:
            mock_registry.get_model = AsyncMock(return_value=None)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/ml/drift-reports",
                    json={"realm_id": "r1", "model_id": "nonexistent", "window_days": 30},
                )
                assert resp.status_code == 404


# ===========================================================================
# I. ML CLI Tests
# ===========================================================================


class TestCLIExtended:
    """Extended CLI tests: _output_json, main(), argument parsing."""

    def test_output_json_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_output_json with use_json=True prints JSON."""
        from agentblue.ml.cli import _output_json

        _output_json({"key": "value"}, use_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["key"] == "value"

    def test_output_json_false(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_output_json with use_json=False prints key-value pairs."""
        from agentblue.ml.cli import _output_json

        _output_json({"key": "value", "num": 42}, use_json=False)
        out = capsys.readouterr().out
        assert "key: value" in out
        assert "num: 42" in out

    def test_output_json_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_output_json with empty dict produces minimal output."""
        from agentblue.ml.cli import _output_json

        _output_json({}, use_json=True)
        out = capsys.readouterr().out
        assert json.loads(out) == {}

    def test_build_parser_all_subcommands(self) -> None:
        """Parser supports all documented subcommands."""
        from agentblue.ml.cli import build_parser

        parser = build_parser()

        # build-dataset
        args = parser.parse_args(["build-dataset", "--realm-id", "r1"])
        assert args.command == "build-dataset"
        assert args.realm_id == "r1"
        assert args.feature_version == "1.0"  # default
        assert args.min_rows == 500  # default
        assert args.min_class_support == 20  # default

        # train
        args = parser.parse_args(["train", "--dataset-id", "d1", "--realm-id", "r1"])
        assert args.command == "train"
        assert args.dataset_id == "d1"
        assert args.model_type == "HIST_GRADIENT_BOOSTING"  # default
        assert args.calibration_method == "ISOTONIC"  # default
        assert args.seed == 42  # default

        # evaluate
        args = parser.parse_args(["evaluate", "--model-id", "m1"])
        assert args.command == "evaluate"
        assert args.model_id == "m1"

        # activate-shadow
        args = parser.parse_args(["activate-shadow", "--model-id", "m1"])
        assert args.command == "activate-shadow"
        assert args.model_id == "m1"

        # drift-report
        args = parser.parse_args(["drift-report", "--realm-id", "r1", "--model-id", "m1"])
        assert args.command == "drift-report"
        assert args.realm_id == "r1"
        assert args.model_id == "m1"
        assert args.window_days == 30  # default

    def test_build_parser_custom_values(self) -> None:
        """Parser accepts custom values for optional args."""
        from agentblue.ml.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "build-dataset",
            "--realm-id", "r1",
            "--feature-version", "2.0",
            "--min-rows", "100",
            "--min-class-support", "5",
            "--json",
        ])
        assert args.feature_version == "2.0"
        assert args.min_rows == 100
        assert args.min_class_support == 5
        assert args.json is True

    def test_build_parser_train_custom_values(self) -> None:
        """Parser accepts custom train args."""
        from agentblue.ml.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "train",
            "--dataset-id", "d1",
            "--realm-id", "r1",
            "--model-type", "LOGISTIC_REGRESSION",
            "--calibration-method", "SIGMOID",
            "--seed", "99",
            "--json",
        ])
        assert args.model_type == "LOGISTIC_REGRESSION"
        assert args.calibration_method == "SIGMOID"
        assert args.seed == 99
        assert args.json is True

    def test_main_no_command_returns_1(self) -> None:
        """main() with no command returns 1."""
        from agentblue.ml.cli import main

        with patch("sys.argv", ["ml-cli"]), pytest.raises(SystemExit):
            main()

    def test_commands_dict_keys(self) -> None:
        """_COMMANDS contains all expected subcommands."""
        from agentblue.ml.cli import _COMMANDS

        expected = {"build-dataset", "train", "evaluate", "activate-shadow", "drift-report"}
        assert set(_COMMANDS.keys()) == expected

    def test_commands_dict_values_are_coroutines(self) -> None:
        """All command handlers are async functions."""
        import inspect

        from agentblue.ml.cli import _COMMANDS

        for name, handler in _COMMANDS.items():
            assert inspect.iscoroutinefunction(handler), f"{name} handler is not async"


# ===========================================================================
# J. Categorization Constants/Domain
# ===========================================================================


class TestCategorizationConstants:
    """Verify categorization constants are defined."""

    def test_engine_version_defined(self) -> None:
        from agentblue.categorization.constants import ENGINE_VERSION

        assert isinstance(ENGINE_VERSION, str)
        assert ENGINE_VERSION  # non-empty

    def test_scoring_weights_sum_check(self) -> None:
        """Scoring weights should be meaningful decimals."""
        from agentblue.categorization.constants import (
            WEIGHT_ACCOUNT_COMPAT,
            WEIGHT_FUZZY_MAX,
            WEIGHT_KEYWORD,
            WEIGHT_USER_RULE,
            WEIGHT_VENDOR_HISTORY,
        )

        total = WEIGHT_USER_RULE + WEIGHT_VENDOR_HISTORY + WEIGHT_KEYWORD + WEIGHT_ACCOUNT_COMPAT + WEIGHT_FUZZY_MAX
        from decimal import Decimal

        assert total == Decimal("1.05")

    def test_threshold_ordering(self) -> None:
        """Confidence thresholds are properly ordered."""
        from agentblue.categorization.constants import (
            ASSISTED_AUTOMATION_THRESHOLD,
            HIGH_CONFIDENCE_THRESHOLD,
            MEDIUM_CONFIDENCE_THRESHOLD,
            MINIMUM_RECOMMENDATION_THRESHOLD,
        )

        assert MINIMUM_RECOMMENDATION_THRESHOLD < MEDIUM_CONFIDENCE_THRESHOLD
        assert MEDIUM_CONFIDENCE_THRESHOLD < HIGH_CONFIDENCE_THRESHOLD
        assert HIGH_CONFIDENCE_THRESHOLD < ASSISTED_AUTOMATION_THRESHOLD


class TestCategorizationDomain:
    """Verify categorization domain enums and dataclasses."""

    def test_categorization_status_members(self) -> None:
        from agentblue.categorization.domain import CategorizationStatus

        assert CategorizationStatus.PENDING.value == "PENDING"
        assert CategorizationStatus.APPROVED.value == "APPROVED"

    def test_recommendation_source_members(self) -> None:
        from agentblue.categorization.domain import RecommendationSource

        assert RecommendationSource.USER_RULE.value == "USER_RULE"
        assert RecommendationSource.MANUAL_SELECTION.value == "MANUAL_SELECTION"

    def test_confidence_band_members(self) -> None:
        from agentblue.categorization.domain import ConfidenceBand

        assert len(ConfidenceBand) == 4

    def test_transaction_feature_dataclass(self) -> None:
        from decimal import Decimal

        from agentblue.categorization.domain import TransactionFeature

        tf = TransactionFeature(
            realm_id="r1",
            transaction_id="t1",
            transaction_quickbooks_id="qb-1",
            transaction_type="Payment",
            normalized_vendor="vendor",
            normalized_description="desc",
            normalized_memo="memo",
            amount=Decimal("100.00"),
            absolute_amount=Decimal("100.00"),
            currency="USD",
            transaction_date="2024-01-01",
            line_count=1,
        )
        assert tf.realm_id == "r1"
        assert tf.feature_version == "1.0"  # default

    def test_assisted_automation_gate_dataclass(self) -> None:
        from decimal import Decimal

        from agentblue.categorization.domain import AssistedAutomationGate

        gate = AssistedAutomationGate(
            passed=True,
            top_score=Decimal("0.95"),
        )
        assert gate.passed is True
        assert gate.reason_codes == []  # default
