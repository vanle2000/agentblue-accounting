"""QuickBooks OAuth FastAPI endpoints.

Provides minimal HTTP endpoints for OAuth authorization and callback.
Business logic is delegated to the service layer (callback, client modules).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from agentblue.integrations.quickbooks.callback import validate_callback
from agentblue.integrations.quickbooks.client import exchange_code_for_token
from agentblue.integrations.quickbooks.config import get_quickbooks_settings
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksCallbackError,
    QuickBooksConfigurationError,
    QuickBooksStateMismatchError,
    QuickBooksTokenExchangeError,
)
from agentblue.integrations.quickbooks.oauth import build_authorization_url

router = APIRouter(
    prefix="/api/v1/integrations/quickbooks",
    tags=["quickbooks"],
)

logger = structlog.get_logger(__name__)


class AuthorizeResponse(BaseModel):
    """Response from the authorize endpoint."""

    authorization_url: str
    state: str


class CallbackResponse(BaseModel):
    """Response from the callback endpoint after successful code exchange."""

    realm_id: str
    token_type: str
    expires_in: int


class ErrorResponse(BaseModel):
    """Safe error response — never exposes secrets."""

    error: str
    detail: str


@router.get("/authorize", response_model=AuthorizeResponse)
async def authorize() -> AuthorizeResponse:
    """Generate a QuickBooks OAuth authorization URL.

    Returns the URL and state value. The client must redirect the user
    to the authorization URL to begin the OAuth flow.
    """
    settings = get_quickbooks_settings()
    result = build_authorization_url(settings)
    return AuthorizeResponse(
        authorization_url=result.authorization_url,
        state=result.state,
    )


@router.get("/callback", response_model=CallbackResponse)
async def callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    realm_id: str = Query(default="", alias="realmId"),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
) -> CallbackResponse:
    """Handle the OAuth callback from Intuit.

    Validates callback parameters, exchanges the authorization code for
    tokens, and returns a safe summary. Tokens are never exposed in the
    response.
    """
    query_params = {
        "code": code,
        "state": state,
        "realmId": realm_id,
        "error": error,
        "error_description": error_description,
    }

    # Retrieve the expected state from the session or query.
    # In production, this should come from a server-side session store.
    # For now, we accept the state parameter and validate format only.
    settings = get_quickbooks_settings()

    try:
        callback_params = validate_callback(
            query_params,
            expected_state=state,  # Passed through; real validation needs session.
        )
    except QuickBooksCallbackError as exc:
        logger.warning("quickbooks_callback_error", error=str(exc))
        raise
    except QuickBooksStateMismatchError:
        logger.warning("quickbooks_state_mismatch")
        raise

    try:
        token = await exchange_code_for_token(settings, callback_params.code)
    except QuickBooksTokenExchangeError:
        logger.error("quickbooks_token_exchange_failed")
        raise
    except QuickBooksConfigurationError:
        logger.error("quickbooks_config_error")
        raise

    logger.info(
        "quickbooks_token_acquired",
        realm_id=callback_params.realm_id,
        token_type=token.token_type,
        expires_in=token.expires_in,
    )

    return CallbackResponse(
        realm_id=callback_params.realm_id,
        token_type=token.token_type,
        expires_in=token.expires_in,
    )
