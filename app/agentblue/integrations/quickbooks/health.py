"""QuickBooks API health check service.

Lightweight verification that the OAuth token is valid and the
company is reachable via the QuickBooks API.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient  # noqa: TC001
from agentblue.integrations.quickbooks.exceptions import QuickBooksApiError
from agentblue.integrations.quickbooks.services import CompanyInfoService

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HealthCheckResult:
    """Result of a QuickBooks API health check."""

    healthy: bool
    realm_id: str
    company_name: str
    environment: str
    error: str = ""


async def check_quickbooks_health(
    client: QuickBooksApiClient,
    *,
    environment: str = "sandbox",
) -> HealthCheckResult:
    """Perform a lightweight health check against the QuickBooks API.

    Verifies:
    - OAuth token is valid (not expired or refreshable)
    - Company is reachable
    - API returns company info

    Returns:
        HealthCheckResult with status and company details.
    """
    try:
        service = CompanyInfoService(client)
        info = await service.get_company_info()

        company_name = info.get("CompanyName", "Unknown")
        realm_id = info.get("Id", client._realm_id)

        logger.info(
            "quickbooks_health_check_ok",
            realm_id=realm_id,
            company_name=company_name,
        )

        return HealthCheckResult(
            healthy=True,
            realm_id=str(realm_id),
            company_name=company_name,
            environment=environment,
        )
    except QuickBooksApiError as exc:
        logger.warning(
            "quickbooks_health_check_failed",
            error=str(exc),
            status_code=exc.status_code,
        )
        return HealthCheckResult(
            healthy=False,
            realm_id=client._realm_id,
            company_name="",
            environment=environment,
            error=str(exc),
        )
    except Exception as exc:
        logger.error("quickbooks_health_check_error", error=str(exc))
        return HealthCheckResult(
            healthy=False,
            realm_id=client._realm_id,
            company_name="",
            environment=environment,
            error=str(exc),
        )
