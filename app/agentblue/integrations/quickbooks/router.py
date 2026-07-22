"""QuickBooks OAuth FastAPI endpoints.

Provides minimal HTTP endpoints for OAuth authorization, callback,
and API health check. Business logic is delegated to the service layer.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
from agentblue.integrations.quickbooks.callback import validate_callback
from agentblue.integrations.quickbooks.client import exchange_code_for_token
from agentblue.integrations.quickbooks.config import get_quickbooks_settings
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksCallbackError,
    QuickBooksConfigurationError,
    QuickBooksStateMismatchError,
    QuickBooksTokenExchangeError,
)
from agentblue.integrations.quickbooks.health import (
    check_quickbooks_health,
)
from agentblue.integrations.quickbooks.oauth import build_authorization_url
from agentblue.integrations.quickbooks.repository import (
    InMemoryTokenRepository,
)

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


class HealthResponse(BaseModel):
    """Response from the QuickBooks health endpoint."""

    healthy: bool
    realm_id: str
    company_name: str
    environment: str
    error: str = ""


@router.get("/authorize", response_model=AuthorizeResponse)
async def authorize() -> AuthorizeResponse:
    """Generate a QuickBooks OAuth authorization URL."""
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
    """Handle the OAuth callback from Intuit."""
    query_params = {
        "code": code,
        "state": state,
        "realmId": realm_id,
        "error": error,
        "error_description": error_description,
    }

    settings = get_quickbooks_settings()

    try:
        callback_params = validate_callback(
            query_params,
            expected_state=state,
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


@router.get("/health", response_model=HealthResponse)
async def quickbooks_health(
    realm_id: str = Query(default=""),
) -> HealthResponse:
    """Check QuickBooks API health.

    Verifies OAuth token validity and company reachability.
    """
    settings = get_quickbooks_settings()

    # For health check, we need a realm_id and token.
    # Use the provided realm_id or fall back to a default.
    if not realm_id:
        return HealthResponse(
            healthy=False,
            realm_id="",
            company_name="",
            environment=settings.environment.value,
            error="realm_id is required for health check.",
        )

    repository = InMemoryTokenRepository()
    async with QuickBooksApiClient(settings, repository, realm_id) as client:
        result = await check_quickbooks_health(client, environment=settings.environment.value)

    return HealthResponse(
        healthy=result.healthy,
        realm_id=result.realm_id,
        company_name=result.company_name,
        environment=result.environment,
        error=result.error,
    )
