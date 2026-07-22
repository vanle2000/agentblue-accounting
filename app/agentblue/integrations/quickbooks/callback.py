"""QuickBooks OAuth callback validation.

Parses and validates Intuit OAuth callback parameters. Does not handle
state persistence — callers must supply the expected state for comparison.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksCallbackError,
    QuickBooksStateMismatchError,
)


@dataclass(frozen=True)
class CallbackParams:
    """Validated OAuth callback parameters from Intuit."""

    code: str
    state: str
    realm_id: str


@dataclass(frozen=True)
class CallbackError:
    """Intuit-reported authorization error from the callback."""

    error: str
    error_description: str


def validate_callback(
    query_params: dict[str, str],
    *,
    expected_state: str,
) -> CallbackParams:
    """Validate OAuth callback query parameters.

    Args:
        query_params: Raw query parameters from the callback request.
        expected_state: The state value that was sent in the authorization URL.

    Returns:
        CallbackParams with validated code, state, and realm_id.

    Raises:
        QuickBooksCallbackError: If required parameters are missing or Intuit
            reported an authorization error.
        QuickBooksStateMismatchError: If the state does not match.
    """
    error = query_params.get("error", "")
    error_description = query_params.get("error_description", "")

    if error:
        raise QuickBooksCallbackError(
            f"Intuit authorization error: {error}. {error_description}".strip()
        )

    code = query_params.get("code", "").strip()
    state = query_params.get("state", "").strip()
    realm_id = query_params.get("realmId", "").strip()

    if not code:
        raise QuickBooksCallbackError("Missing required callback parameter: code.")
    if not state:
        raise QuickBooksCallbackError("Missing required callback parameter: state.")
    if not realm_id:
        raise QuickBooksCallbackError("Missing required callback parameter: realmId.")

    # Constant-time comparison to prevent timing attacks.
    if not hmac.compare_digest(state, expected_state):
        raise QuickBooksStateMismatchError("Callback state does not match the expected value.")

    return CallbackParams(
        code=code,
        state=state,
        realm_id=realm_id,
    )
