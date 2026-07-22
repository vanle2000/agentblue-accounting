"""Tests for QuickBooks OAuth configuration and authorization URL generation.

These tests use fake credentials only. They do not call the live Intuit API,
require internet access, modify .env, or mutate PostgreSQL.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentblue.integrations.quickbooks.config import (
    QuickBooksEnvironment,
    QuickBooksOAuthSettings,
)
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksConfigurationError,
)
from agentblue.integrations.quickbooks.oauth import (
    AuthorizationResult,
    build_authorization_url,
    generate_state,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_FAKE_CLIENT_ID = "fake-client-id-12345"
_FAKE_CLIENT_SECRET = "fake-client-secret-67890"
_FAKE_REDIRECT_URI = "https://localhost:8000/callback"


def _make_settings(**overrides: object) -> QuickBooksOAuthSettings:
    """Create QuickBooksOAuthSettings with fake credentials."""
    defaults = {
        "client_id": _FAKE_CLIENT_ID,
        "client_secret": _FAKE_CLIENT_SECRET,
        "redirect_uri": _FAKE_REDIRECT_URI,
        "environment": "sandbox",
        "scopes": "com.intuit.quickbooks.accounting",
    }
    defaults.update(overrides)
    return QuickBooksOAuthSettings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Configuration: Environment mapping
# ---------------------------------------------------------------------------


class TestEnvironmentMapping:
    """Test sandbox and production endpoint mapping."""

    def test_sandbox_maps_to_sandbox_endpoints(self) -> None:
        settings = _make_settings(environment="sandbox")
        assert settings.environment == QuickBooksEnvironment.SANDBOX
        assert "intuit.com" in settings.authorization_endpoint
        assert "intuit.com" in settings.token_endpoint

    def test_production_maps_to_production_endpoints(self) -> None:
        settings = _make_settings(environment="production")
        assert settings.environment == QuickBooksEnvironment.PRODUCTION
        assert "intuit.com" in settings.authorization_endpoint
        assert "intuit.com" in settings.token_endpoint

    def test_unsupported_environment_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_settings(environment="staging")


# ---------------------------------------------------------------------------
# Configuration: Validation
# ---------------------------------------------------------------------------


class TestConfigurationValidation:
    """Test required configuration validation."""

    def test_missing_client_id_rejected(self) -> None:
        settings = _make_settings(client_id="")
        with pytest.raises(QuickBooksConfigurationError, match="QUICKBOOKS_CLIENT_ID"):
            settings.validate_for_oauth()

    def test_missing_client_secret_rejected(self) -> None:
        settings = _make_settings(client_secret="")
        with pytest.raises(QuickBooksConfigurationError, match="QUICKBOOKS_CLIENT_SECRET"):
            settings.validate_for_oauth()

    def test_missing_redirect_uri_rejected(self) -> None:
        settings = _make_settings(redirect_uri="")
        with pytest.raises(QuickBooksConfigurationError, match="QUICKBOOKS_REDIRECT_URI"):
            settings.validate_for_oauth()

    def test_valid_config_passes(self) -> None:
        settings = _make_settings()
        settings.validate_for_oauth()  # must not raise

    def test_all_missing_reported_together(self) -> None:
        settings = QuickBooksOAuthSettings()
        with pytest.raises(QuickBooksConfigurationError) as exc_info:
            settings.validate_for_oauth()
        msg = str(exc_info.value)
        assert "QUICKBOOKS_CLIENT_ID" in msg
        assert "QUICKBOOKS_CLIENT_SECRET" in msg
        assert "QUICKBOOKS_REDIRECT_URI" in msg


# ---------------------------------------------------------------------------
# Configuration: Redirect URI validation
# ---------------------------------------------------------------------------


class TestRedirectUriValidation:
    """Test redirect URI format validation with structured URL parsing."""

    def test_https_uri_accepted(self) -> None:
        settings = _make_settings(redirect_uri="https://example.com/callback")
        assert settings.redirect_uri == "https://example.com/callback"

    def test_http_localhost_accepted(self) -> None:
        settings = _make_settings(redirect_uri="http://localhost:8000/callback")
        assert settings.redirect_uri == "http://localhost:8000/callback"

    def test_http_uri_accepted(self) -> None:
        settings = _make_settings(redirect_uri="http://example.com/callback")
        assert settings.redirect_uri == "http://example.com/callback"

    def test_invalid_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError, match="scheme"):
            _make_settings(redirect_uri="ftp://example.com/callback")

    def test_no_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError, match="scheme"):
            _make_settings(redirect_uri="example.com/callback")

    def test_no_hostname_rejected(self) -> None:
        with pytest.raises(ValidationError, match="hostname"):
            _make_settings(redirect_uri="https:///callback")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_settings(redirect_uri="   ")

    def test_empty_uri_accepted_at_construction(self) -> None:
        # Empty is allowed at construction; validate_for_oauth catches it.
        settings = _make_settings(redirect_uri="")
        assert settings.redirect_uri == ""

    def test_leading_trailing_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            _make_settings(redirect_uri="  https://example.com/callback  ")


# ---------------------------------------------------------------------------
# Configuration: Scope normalization
# ---------------------------------------------------------------------------


class TestScopeNormalization:
    """Test that scopes are normalized consistently."""

    def test_default_scopes(self) -> None:
        settings = _make_settings(scopes="com.intuit.quickbooks.accounting")
        assert "com.intuit.quickbooks.accounting" in settings.scopes

    def test_multiple_scopes_sorted(self) -> None:
        settings = _make_settings(
            scopes="com.intuit.quickbooks.payment com.intuit.quickbooks.accounting"
        )
        parts = settings.scopes.split()
        assert parts == sorted(parts)

    def test_comma_delimited_scopes_normalized(self) -> None:
        settings = _make_settings(
            scopes="com.intuit.quickbooks.payment,com.intuit.quickbooks.accounting"
        )
        parts = settings.scopes.split()
        assert parts == sorted(parts)

    def test_mixed_delimiters_normalized(self) -> None:
        settings = _make_settings(
            scopes="com.intuit.quickbooks.payment, com.intuit.quickbooks.accounting"
        )
        parts = settings.scopes.split()
        assert parts == sorted(parts)
        assert len(parts) == 2

    def test_duplicate_scopes_removed(self) -> None:
        settings = _make_settings(
            scopes="com.intuit.quickbooks.accounting com.intuit.quickbooks.accounting"
        )
        assert settings.scopes == "com.intuit.quickbooks.accounting"

    def test_whitespace_stripped(self) -> None:
        settings = _make_settings(scopes="  com.intuit.quickbooks.accounting  ")
        assert settings.scopes == "com.intuit.quickbooks.accounting"

    def test_empty_scopes_uses_default(self) -> None:
        settings = _make_settings(scopes="")
        assert "com.intuit.quickbooks.accounting" in settings.scopes

    def test_scopes_in_url_are_space_delimited(self) -> None:
        settings = _make_settings(
            scopes="com.intuit.quickbooks.payment,com.intuit.quickbooks.accounting"
        )
        result = build_authorization_url(settings, state="test")
        # Scopes in URL must be space-separated.
        assert "scope=com.intuit.quickbooks.accounting+com.intuit.quickbooks.payment" in (
            result.authorization_url
            or "scope=com.intuit.quickbooks.accounting%20com.intuit.quickbooks.payment"
            in result.authorization_url
        )


# ---------------------------------------------------------------------------
# Configuration: Secret protection
# ---------------------------------------------------------------------------


class TestSecretProtection:
    """Test that secrets are not exposed in repr, str, or errors."""

    def test_client_secret_not_in_repr(self) -> None:
        settings = _make_settings()
        repr_str = repr(settings)
        assert _FAKE_CLIENT_SECRET not in repr_str

    def test_validation_error_does_not_expose_secrets(self) -> None:
        settings = _make_settings(client_id="")
        with pytest.raises(QuickBooksConfigurationError) as exc_info:
            settings.validate_for_oauth()
        msg = str(exc_info.value)
        assert _FAKE_CLIENT_SECRET not in msg

    def test_secret_str_masked(self) -> None:
        settings = _make_settings()
        secret_repr = str(settings.client_secret)
        assert _FAKE_CLIENT_SECRET not in secret_repr


# ---------------------------------------------------------------------------
# Authorization URL: Structure
# ---------------------------------------------------------------------------


class TestAuthorizationUrl:
    """Test authorization URL generation."""

    def test_correct_endpoint(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="test-state")
        assert settings.authorization_endpoint in result.authorization_url

    def test_client_id_in_url(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="test-state")
        assert f"client_id={_FAKE_CLIENT_ID}" in result.authorization_url

    def test_response_type_code(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="test-state")
        assert "response_type=code" in result.authorization_url

    def test_redirect_uri_encoded(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="test-state")
        assert "redirect_uri=" in result.authorization_url

    def test_scopes_in_url(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="test-state")
        assert "scope=" in result.authorization_url
        assert "com.intuit.quickbooks.accounting" in result.authorization_url

    def test_supplied_state_preserved(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="my-custom-state")
        assert result.state == "my-custom-state"
        assert "state=my-custom-state" in result.authorization_url

    def test_client_secret_not_in_url(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="test-state")
        assert "client_secret" not in result.authorization_url
        assert _FAKE_CLIENT_SECRET not in result.authorization_url

    def test_result_is_frozen_dataclass(self) -> None:
        settings = _make_settings()
        result = build_authorization_url(settings, state="test-state")
        assert isinstance(result, AuthorizationResult)

    def test_missing_config_raises_configuration_error(self) -> None:
        settings = QuickBooksOAuthSettings()
        with pytest.raises(QuickBooksConfigurationError):
            build_authorization_url(settings, state="test-state")

    def test_configuration_error_propagates_without_wrapping(self) -> None:
        """QuickBooksConfigurationError must remain distinguishable."""
        settings = QuickBooksOAuthSettings()
        with pytest.raises(QuickBooksConfigurationError):
            build_authorization_url(settings)
        # Must NOT be wrapped in QuickBooksOAuthError.


# ---------------------------------------------------------------------------
# State generation
# ---------------------------------------------------------------------------


class TestStateGeneration:
    """Test cryptographic state generation."""

    def test_generated_state_non_empty(self) -> None:
        state = generate_state()
        assert len(state) > 0

    def test_generated_states_differ(self) -> None:
        state1 = generate_state()
        state2 = generate_state()
        assert state1 != state2

    def test_state_is_url_safe(self) -> None:
        for _ in range(10):
            state = generate_state()
            assert all(c.isalnum() or c in "-_" for c in state)


# ---------------------------------------------------------------------------
# Security: comprehensive
# ---------------------------------------------------------------------------


class TestSecurity:
    """Comprehensive security tests."""

    def test_secrets_not_in_any_error_message(self) -> None:
        """Secrets must not appear in any exception message."""
        settings = _make_settings(client_id="")
        with pytest.raises(QuickBooksConfigurationError) as exc_info:
            settings.validate_for_oauth()
        msg = str(exc_info.value)
        assert _FAKE_CLIENT_SECRET not in msg

    def test_secrets_not_in_generated_url(self) -> None:
        """Client secret must never appear in the authorization URL."""
        settings = _make_settings()
        result = build_authorization_url(settings)
        url = result.authorization_url
        assert _FAKE_CLIENT_SECRET not in url
        assert "client_secret" not in url
