"""QuickBooks OAuth authorization URL generation.

Generates the Intuit OAuth2 authorization URL with a cryptographically
secure state parameter. Does not implement callback handling, code
exchange, token storage, or token refresh.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings  # noqa: TC001
from agentblue.integrations.quickbooks.exceptions import QuickBooksOAuthError


@dataclass(frozen=True)
class AuthorizationResult:
    """Result of authorization URL generation."""

    authorization_url: str
    state: str


def generate_state() -> str:
    """Generate a cryptographically secure random state value.

    Returns a URL-safe string of 32 bytes (256 bits of entropy).
    """
    return secrets.token_urlsafe(32)


def build_authorization_url(
    settings: QuickBooksOAuthSettings,
    *,
    state: str | None = None,
) -> AuthorizationResult:
    """Build a QuickBooks OAuth2 authorization URL.

    Args:
        settings: Validated QuickBooks OAuth settings.
        state: Optional deterministic state value for testing.
            When None, a cryptographically secure state is generated.

    Returns:
        AuthorizationResult containing the URL and state.

    Raises:
        QuickBooksOAuthError: If the URL cannot be constructed.
    """
    try:
        settings.validate_for_oauth()
    except Exception as exc:
        raise QuickBooksOAuthError(f"Cannot build authorization URL: {exc}") from exc

    if state is None:
        state = generate_state()

    if not state:
        raise QuickBooksOAuthError("State value must be non-empty.")

    scopes_str = " ".join(settings.normalized_scopes)

    params = {
        "client_id": settings.client_id,
        "response_type": "code",
        "scope": scopes_str,
        "redirect_uri": settings.redirect_uri,
        "state": state,
    }

    url = f"{settings.authorization_endpoint}?{urlencode(params)}"

    return AuthorizationResult(
        authorization_url=url,
        state=state,
    )
