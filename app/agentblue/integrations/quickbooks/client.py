"""QuickBooks OAuth token client.

Handles authorization-code exchange and token refresh via the Intuit
token endpoint. Uses httpx for async HTTP with explicit timeouts.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings  # noqa: TC001
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksTokenExchangeError,
    QuickBooksTokenRefreshError,
)
from agentblue.integrations.quickbooks.models import (
    TokenResponse,
    parse_token_response,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0

# Intuit error codes that are permanent and should not be retried.
_PERMANENT_ERROR_CODES = frozenset(
    {
        "invalid_grant",
        "invalid_client",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)


def _build_basic_auth_header(settings: QuickBooksOAuthSettings) -> str:
    """Build HTTP Basic auth header value from client credentials."""
    credentials = f"{settings.client_id}:{settings.client_secret.get_secret_value()}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


def _classify_intuit_error(body: dict[str, Any]) -> str:
    """Extract Intuit error code from response body."""
    result: str = body.get("error", "unknown_error")
    return result


async def exchange_code_for_token(
    settings: QuickBooksOAuthSettings,
    code: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> TokenResponse:
    """Exchange an authorization code for access and refresh tokens.

    Sends a POST to the Intuit token endpoint with HTTP Basic auth.

    Args:
        settings: Validated QuickBooks OAuth settings.
        code: The authorization code from the callback.
        timeout: Request timeout in seconds.

    Returns:
        Parsed TokenResponse.

    Raises:
        QuickBooksConfigurationError: If settings are invalid.
        QuickBooksTokenExchangeError: If the exchange fails.
    """
    settings.validate_for_oauth()

    auth_header = _build_basic_auth_header(settings)

    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.token_endpoint,
                headers=headers,
                data=data,
            )
    except httpx.TimeoutException as exc:
        raise QuickBooksTokenExchangeError("Token exchange request timed out.") from exc
    except httpx.HTTPError as exc:
        raise QuickBooksTokenExchangeError(
            "Token exchange request failed due to a network error."
        ) from exc

    if response.status_code == 200:
        return parse_token_response(response.json())

    is_json = "application/json" in response.headers.get("content-type", "")
    body = response.json() if is_json else {}
    error_code = _classify_intuit_error(body)

    if response.status_code == 401:
        raise QuickBooksTokenExchangeError("Token exchange failed: invalid client credentials.")
    if response.status_code == 400 and error_code in _PERMANENT_ERROR_CODES:
        raise QuickBooksTokenExchangeError(
            f"Token exchange failed: {error_code}. This is a permanent error — do not retry."
        )
    if response.status_code == 429:
        raise QuickBooksTokenExchangeError(
            "Token exchange failed: rate limited by Intuit. Retry after backoff."
        )
    if response.status_code >= 500:
        raise QuickBooksTokenExchangeError(
            f"Token exchange failed: Intuit server error (HTTP {response.status_code})."
        )

    raise QuickBooksTokenExchangeError(f"Token exchange failed: HTTP {response.status_code}.")


async def refresh_access_token(
    settings: QuickBooksOAuthSettings,
    refresh_token: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 2,
) -> TokenResponse:
    """Refresh an access token using a refresh token.

    Sends a POST to the Intuit token endpoint with HTTP Basic auth.
    Retries only on transient transport or server failures.

    Args:
        settings: Validated QuickBooks OAuth settings.
        refresh_token: The refresh token to use.
        timeout: Request timeout in seconds.
        max_retries: Maximum retry attempts for transient failures only.

    Returns:
        Parsed TokenResponse with potentially rotated refresh token.

    Raises:
        QuickBooksConfigurationError: If settings are invalid.
        QuickBooksTokenRefreshError: If refresh fails permanently or after retries.
    """
    settings.validate_for_oauth()

    auth_header = _build_basic_auth_header(settings)

    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    settings.token_endpoint,
                    headers=headers,
                    data=data,
                )
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < max_retries:
                continue
            raise QuickBooksTokenRefreshError(
                "Token refresh request timed out after retries."
            ) from exc
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < max_retries:
                continue
            raise QuickBooksTokenRefreshError(
                "Token refresh failed due to a network error after retries."
            ) from exc

        if response.status_code == 200:
            return parse_token_response(response.json())

        is_json = "application/json" in response.headers.get("content-type", "")
        body = response.json() if is_json else {}
        error_code = _classify_intuit_error(body)

        # Permanent failures — do not retry.
        if response.status_code == 401:
            raise QuickBooksTokenRefreshError("Token refresh failed: invalid client credentials.")
        if response.status_code == 400 and error_code in _PERMANENT_ERROR_CODES:
            raise QuickBooksTokenRefreshError(
                f"Token refresh failed: {error_code}. This is a permanent error — do not retry."
            )

        # Transient failures — retry.
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < max_retries:
                continue
            raise QuickBooksTokenRefreshError(
                f"Token refresh failed after {max_retries + 1} attempts: "
                f"HTTP {response.status_code}."
            )

        # Other failures — do not retry.
        raise QuickBooksTokenRefreshError(f"Token refresh failed: HTTP {response.status_code}.")

    # Should not reach here, but safety net.
    raise QuickBooksTokenRefreshError("Token refresh failed after all retries.") from last_exc
