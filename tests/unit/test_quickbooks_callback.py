"""Tests for QuickBooks OAuth callback validation.

Uses fake credentials only. No live Intuit API calls.
"""

from __future__ import annotations

import pytest

from agentblue.integrations.quickbooks.callback import (
    CallbackParams,
    validate_callback,
)
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksCallbackError,
    QuickBooksStateMismatchError,
)

pytestmark = pytest.mark.unit


_FAKE_CODE = "fake-auth-code-xyz"
_FAKE_STATE = "fake-state-abc"
_FAKE_REALM = "123456789"


def _make_params(**overrides: str) -> dict[str, str]:
    """Build callback query params with defaults."""
    params = {
        "code": _FAKE_CODE,
        "state": _FAKE_STATE,
        "realmId": _FAKE_REALM,
    }
    params.update(overrides)
    return params


# ---------------------------------------------------------------------------
# Successful validation
# ---------------------------------------------------------------------------


class TestSuccessfulCallback:
    def test_valid_callback_returns_params(self) -> None:
        result = validate_callback(_make_params(), expected_state=_FAKE_STATE)
        assert isinstance(result, CallbackParams)
        assert result.code == _FAKE_CODE
        assert result.state == _FAKE_STATE
        assert result.realm_id == _FAKE_REALM

    def test_params_are_frozen(self) -> None:
        result = validate_callback(_make_params(), expected_state=_FAKE_STATE)
        with pytest.raises(AttributeError):
            result.code = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Intuit authorization errors
# ---------------------------------------------------------------------------


class TestIntuitErrors:
    def test_error_param_raises_callback_error(self) -> None:
        params = _make_params(error="access_denied", error_description="User denied")
        with pytest.raises(QuickBooksCallbackError, match="access_denied"):
            validate_callback(params, expected_state=_FAKE_STATE)

    def test_error_without_description(self) -> None:
        params = _make_params(error="server_error")
        with pytest.raises(QuickBooksCallbackError, match="server_error"):
            validate_callback(params, expected_state=_FAKE_STATE)


# ---------------------------------------------------------------------------
# Missing parameters
# ---------------------------------------------------------------------------


class TestMissingParams:
    def test_missing_code(self) -> None:
        params = _make_params(code="")
        with pytest.raises(QuickBooksCallbackError, match="code"):
            validate_callback(params, expected_state=_FAKE_STATE)

    def test_missing_state(self) -> None:
        params = _make_params(state="")
        with pytest.raises(QuickBooksCallbackError, match="state"):
            validate_callback(params, expected_state=_FAKE_STATE)

    def test_missing_realm_id(self) -> None:
        params = _make_params(realmId="")
        with pytest.raises(QuickBooksCallbackError, match="realmId"):
            validate_callback(params, expected_state=_FAKE_STATE)

    def test_absent_code_key(self) -> None:
        params = {"state": _FAKE_STATE, "realmId": _FAKE_REALM}
        with pytest.raises(QuickBooksCallbackError, match="code"):
            validate_callback(params, expected_state=_FAKE_STATE)


# ---------------------------------------------------------------------------
# State mismatch
# ---------------------------------------------------------------------------


class TestStateMismatch:
    def test_wrong_state_raises(self) -> None:
        params = _make_params(state="wrong-state")
        with pytest.raises(QuickBooksStateMismatchError, match="does not match"):
            validate_callback(params, expected_state=_FAKE_STATE)

    def test_state_mismatch_uses_constant_time(self) -> None:
        """Verify hmac.compare_digest is used (no timing leak)."""
        params = _make_params(state="wrong")
        with pytest.raises(QuickBooksStateMismatchError):
            validate_callback(params, expected_state=_FAKE_STATE)


# ---------------------------------------------------------------------------
# Whitespace handling
# ---------------------------------------------------------------------------


class TestWhitespace:
    def test_code_stripped(self) -> None:
        params = _make_params(code=f"  {_FAKE_CODE}  ")
        result = validate_callback(params, expected_state=_FAKE_STATE)
        assert result.code == _FAKE_CODE

    def test_state_stripped(self) -> None:
        params = _make_params(state=f"  {_FAKE_STATE}  ")
        result = validate_callback(params, expected_state=_FAKE_STATE)
        assert result.state == _FAKE_STATE
