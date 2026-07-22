"""Tests for QuickBooks token response models.

Uses fake data only. No live Intuit API calls.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenExchangeError
from agentblue.integrations.quickbooks.models import (
    TokenResponse,
    parse_token_response,
)

pytestmark = pytest.mark.unit


def _make_token_data(**overrides: object) -> dict[str, object]:
    """Build a raw token response dict with defaults."""
    now = int(time.time())
    data: dict[str, object] = {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "x_refresh_token_expires_in": 8640000,
        "token_type": "bearer",
        "realm_id": "123456789",
        "issued_at": now,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Successful parsing
# ---------------------------------------------------------------------------


class TestTokenParsing:
    def test_valid_token_parses(self) -> None:
        token = parse_token_response(_make_token_data())
        assert isinstance(token, TokenResponse)
        assert token.token_type == "bearer"
        assert token.realm_id == "123456789"
        assert token.expires_in == 3600

    def test_access_token_is_secret(self) -> None:
        token = parse_token_response(_make_token_data())
        repr_str = repr(token)
        assert "fake-access-token" not in repr_str
        assert "fake-refresh-token" not in repr_str

    def test_id_token_optional(self) -> None:
        data = _make_token_data(id_token="fake-id-token")
        token = parse_token_response(data)
        assert token.id_token is not None


# ---------------------------------------------------------------------------
# Expiration calculations
# ---------------------------------------------------------------------------


class TestExpirationCalculations:
    def test_access_token_expires_at_calculated(self) -> None:
        now = int(time.time())
        data = _make_token_data(issued_at=now, expires_in=3600)
        token = parse_token_response(data)
        assert token.access_token_expires_at is not None
        expected = datetime.fromtimestamp(now + 3600, tz=UTC)
        assert token.access_token_expires_at == expected

    def test_refresh_token_expires_at_calculated(self) -> None:
        now = int(time.time())
        data = _make_token_data(issued_at=now, x_refresh_token_expires_in=8640000)
        token = parse_token_response(data)
        assert token.refresh_token_expires_at is not None
        expected = datetime.fromtimestamp(now + 8640000, tz=UTC)
        assert token.refresh_token_expires_at == expected

    def test_expirations_are_utc_aware(self) -> None:
        token = parse_token_response(_make_token_data())
        assert token.access_token_expires_at is not None
        assert token.access_token_expires_at.tzinfo == UTC
        assert token.refresh_token_expires_at is not None
        assert token.refresh_token_expires_at.tzinfo == UTC

    def test_without_issued_at_no_expirations(self) -> None:
        data = _make_token_data(issued_at=None)
        token = parse_token_response(data)
        assert token.access_token_expires_at is None
        assert token.refresh_token_expires_at is None


# ---------------------------------------------------------------------------
# Expiration checks
# ---------------------------------------------------------------------------


class TestExpirationChecks:
    def test_expired_access_token(self) -> None:
        past = int(time.time()) - 7200
        data = _make_token_data(issued_at=past, expires_in=3600)
        token = parse_token_response(data)
        assert token.is_access_token_expired is True

    def test_valid_access_token(self) -> None:
        now = int(time.time())
        data = _make_token_data(issued_at=now, expires_in=3600)
        token = parse_token_response(data)
        assert token.is_access_token_expired is False

    def test_expiring_soon(self) -> None:
        now = int(time.time())
        data = _make_token_data(issued_at=now, expires_in=600)
        token = parse_token_response(data)
        assert token.is_access_token_expiring_soon(margin_seconds=900) is True

    def test_not_expiring_soon(self) -> None:
        now = int(time.time())
        data = _make_token_data(issued_at=now, expires_in=3600)
        token = parse_token_response(data)
        assert token.is_access_token_expiring_soon(margin_seconds=300) is False

    def test_expired_refresh_token(self) -> None:
        past = int(time.time()) - 8640001
        data = _make_token_data(issued_at=past, x_refresh_token_expires_in=8640000)
        token = parse_token_response(data)
        assert token.is_refresh_token_expired is True


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_missing_access_token_raises(self) -> None:
        data = _make_token_data()
        del data["access_token"]
        with pytest.raises(QuickBooksTokenExchangeError, match="parse"):
            parse_token_response(data)

    def test_zero_expires_in_raises(self) -> None:
        data = _make_token_data(expires_in=0)
        with pytest.raises(QuickBooksTokenExchangeError, match="parse"):
            parse_token_response(data)

    def test_negative_expires_in_raises(self) -> None:
        data = _make_token_data(expires_in=-1)
        with pytest.raises(QuickBooksTokenExchangeError, match="parse"):
            parse_token_response(data)


# ---------------------------------------------------------------------------
# Repr safety
# ---------------------------------------------------------------------------


class TestReprSafety:
    def test_repr_does_not_contain_tokens(self) -> None:
        token = parse_token_response(_make_token_data())
        r = repr(token)
        assert "fake-access-token" not in r
        assert "fake-refresh-token" not in r
        assert "expires_in=3600" in r
