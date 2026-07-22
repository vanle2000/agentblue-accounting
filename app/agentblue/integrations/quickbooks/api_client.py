"""Production QuickBooks Online API client.

Authenticated async HTTP client with automatic token refresh, retry,
rate limiting, pagination, and structured error mapping.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx
import structlog

from agentblue.integrations.quickbooks.client import refresh_access_token
from agentblue.integrations.quickbooks.config import QuickBooksOAuthSettings  # noqa: TC001
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksApiError,
    QuickBooksAuthenticationError,
    QuickBooksPermissionError,
    QuickBooksRateLimitError,
    QuickBooksResourceNotFoundError,
    QuickBooksServerError,
    QuickBooksTokenRefreshError,
    QuickBooksTransportError,
    QuickBooksValidationError,
)
from agentblue.integrations.quickbooks.models import TokenResponse  # noqa: TC001
from agentblue.integrations.quickbooks.repository import TokenRepository  # noqa: TC001

logger = structlog.get_logger(__name__)

# HTTP status codes that are safe to retry.
_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})

# HTTP status codes that are permanent failures.
_NEVER_RETRY_CODES = frozenset({400, 401, 403, 404, 409, 422})


def _parse_retry_after(value: str | None) -> float:
    """Parse a Retry-After header value (seconds)."""
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        return 0.0


def _extract_intuit_error(response: httpx.Response) -> tuple[str, str]:
    """Extract Intuit error code and fault message from response."""
    try:
        body = response.json()
    except Exception:
        return "unknown_error", ""

    fault = body.get("Fault", {})
    error_list = fault.get("Error", [])
    if error_list:
        first = error_list[0]
        return first.get("code", "unknown_error"), first.get("Detail", "")
    return body.get("error", "unknown_error"), ""


def _extract_intuit_tid(response: httpx.Response) -> str:
    """Extract Intuit transaction ID from response headers."""
    result: str = response.headers.get("intuit_tid", "")
    return result


def _map_response_error(response: httpx.Response) -> QuickBooksApiError:
    """Map an HTTP response to the appropriate domain exception."""
    code = response.status_code
    intuit_tid = _extract_intuit_tid(response)
    error_code, fault_message = _extract_intuit_error(response)
    retry_after = _parse_retry_after(response.headers.get("retry-after"))

    base_msg = f"QuickBooks API error (HTTP {code}, intuit_tid={intuit_tid})"

    if code == 401:
        return QuickBooksAuthenticationError(
            base_msg, status_code=code, intuit_tid=intuit_tid, fault_message=fault_message
        )
    if code == 403:
        return QuickBooksPermissionError(
            base_msg, status_code=code, intuit_tid=intuit_tid, fault_message=fault_message
        )
    if code == 404:
        return QuickBooksResourceNotFoundError(
            base_msg, status_code=code, intuit_tid=intuit_tid, fault_message=fault_message
        )
    if code == 429:
        return QuickBooksRateLimitError(
            base_msg,
            retry_after=retry_after,
            status_code=code,
            intuit_tid=intuit_tid,
            fault_message=fault_message,
        )
    if code in (400, 422):
        return QuickBooksValidationError(
            base_msg, status_code=code, intuit_tid=intuit_tid, fault_message=fault_message
        )
    if code >= 500:
        return QuickBooksServerError(
            base_msg, status_code=code, intuit_tid=intuit_tid, fault_message=fault_message
        )
    return QuickBooksApiError(
        base_msg, status_code=code, intuit_tid=intuit_tid, fault_message=fault_message
    )


class QuickBooksApiClient:
    """Authenticated QuickBooks Online API client.

    Handles:
    - Bearer token authentication
    - Automatic token refresh on expiry
    - Retry with exponential backoff for transient failures
    - Rate limit handling with Retry-After
    - Pagination via STARTPOSITION/MAXRESULTS
    - Structured error mapping

    Usage:
        async with QuickBooksApiClient(settings, repository, realm_id) as client:
            result = await client.get("/v3/company/{realmId}/query", params={"query": "..."})
    """

    def __init__(
        self,
        settings: QuickBooksOAuthSettings,
        repository: TokenRepository,
        realm_id: str,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_connections: int | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._realm_id = realm_id
        self._timeout = timeout or settings.api_timeout
        self._max_retries = max_retries if max_retries is not None else settings.api_max_retries
        self._max_connections = max_connections or settings.api_max_connections
        self._http_client: httpx.AsyncClient | None = None
        self._token: TokenResponse | None = None

    async def __aenter__(self) -> QuickBooksApiClient:
        self._http_client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_connections,
            ),
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _ensure_token(self) -> TokenResponse:
        """Load token from repository; refresh if expired."""
        if self._token is None:
            self._token = await self._repository.get_by_realm(self._realm_id)
        if self._token is None:
            raise QuickBooksApiError(
                f"No token found for realm {self._realm_id!r}. "
                "Complete the OAuth flow before making API calls."
            )

        if self._token.is_access_token_expired or self._token.is_access_token_expiring_soon():
            logger.info("quickbooks_token_refreshing", realm_id=self._realm_id)
            try:
                new_token = await refresh_access_token(
                    self._settings,
                    self._token.refresh_token.get_secret_value(),
                    timeout=self._timeout,
                    max_retries=1,
                )
            except QuickBooksTokenRefreshError:
                raise
            new_token = new_token.model_copy(update={"realm_id": self._realm_id})
            await self._repository.update(new_token)
            self._token = new_token
            logger.info("quickbooks_token_refreshed", realm_id=self._realm_id)

        return self._token

    def _build_url(self, path: str) -> str:
        """Build full API URL from path."""
        base = self._settings.api_base_url.rstrip("/")
        return f"{base}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute an authenticated API request with retry and rate limiting.

        Automatically refreshes the token once on 401.
        Retries transient failures according to the retry policy.
        """
        if self._http_client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context.")

        url = self._build_url(path)
        last_exc: Exception | None = None
        refreshed_on_401 = False

        for attempt in range(self._max_retries + 1):
            token = await self._ensure_token()
            headers: dict[str, str] = {
                "Authorization": f"Bearer {token.access_token.get_secret_value()}",
                "Accept": "application/json",
            }
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            if extra_headers:
                headers.update(extra_headers)

            start = time.monotonic()
            try:
                response = await self._http_client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
            except httpx.TimeoutException as exc:
                duration = time.monotonic() - start
                logger.warning(
                    "quickbooks_request_timeout",
                    method=method,
                    endpoint=path,
                    duration_ms=round(duration * 1000),
                    attempt=attempt + 1,
                )
                last_exc = QuickBooksTransportError(
                    f"Request timed out: {method} {path}", status_code=0
                )
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise last_exc from exc
            except httpx.HTTPError as exc:
                duration = time.monotonic() - start
                logger.warning(
                    "quickbooks_request_transport_error",
                    method=method,
                    endpoint=path,
                    duration_ms=round(duration * 1000),
                    attempt=attempt + 1,
                )
                last_exc = QuickBooksTransportError(
                    f"Network error: {method} {path}", status_code=0
                )
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise last_exc from exc

            duration = time.monotonic() - start
            status = response.status_code
            intuit_tid = _extract_intuit_tid(response)

            logger.info(
                "quickbooks_api_response",
                method=method,
                endpoint=path,
                status_code=status,
                duration_ms=round(duration * 1000),
                attempt=attempt + 1,
                intuit_tid=intuit_tid,
            )

            # Success
            if 200 <= status < 300:
                is_json = "application/json" in response.headers.get("content-type", "")
                if is_json:
                    result: dict[str, Any] = response.json()
                    return result
                return {}

            # 401 — try refresh once, then retry
            if status == 401 and not refreshed_on_401:
                refreshed_on_401 = True
                logger.info("quickbooks_401_refreshing_token", endpoint=path)
                try:
                    new_token = await refresh_access_token(
                        self._settings,
                        token.refresh_token.get_secret_value(),
                        timeout=self._timeout,
                        max_retries=1,
                    )
                    new_token = new_token.model_copy(update={"realm_id": self._realm_id})
                    await self._repository.update(new_token)
                    self._token = new_token
                except Exception:
                    logger.warning("quickbooks_401_refresh_failed", endpoint=path)
                    self._token = None
                continue

            # Rate limit
            if status == 429:
                retry_after = _parse_retry_after(response.headers.get("retry-after"))
                logger.warning(
                    "quickbooks_rate_limited",
                    endpoint=path,
                    retry_after=retry_after,
                    attempt=attempt + 1,
                )
                if attempt < self._max_retries:
                    delay = max(retry_after, self._backoff_delay(attempt))
                    await self._sleep(delay)
                    continue
                raise _map_response_error(response)

            # Non-retryable
            if status in _NEVER_RETRY_CODES:
                raise _map_response_error(response)

            # Retryable server error
            if status in _RETRYABLE_STATUS_CODES:
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise _map_response_error(response)

            # Other errors — do not retry
            raise _map_response_error(response)

        # Safety net
        if last_exc:
            raise last_exc
        raise QuickBooksApiError("Request failed after all retries.")

    def _backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter."""
        base = 2.0**attempt * self._settings.api_rate_limit_delay
        jitter = random.uniform(0, base * 0.25)
        return base + jitter

    async def _backoff(self, attempt: int) -> None:
        """Sleep for exponential backoff duration."""
        delay = self._backoff_delay(attempt)
        await self._sleep(delay)

    async def _sleep(self, seconds: float) -> None:
        """Async sleep wrapper."""
        import asyncio

        await asyncio.sleep(seconds)

    # --- Public request methods ---

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated GET request."""
        return await self._request("GET", path, params=params, extra_headers=headers)

    async def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated POST request."""
        return await self._request(
            "POST", path, params=params, json_body=json_body, extra_headers=headers
        )

    async def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated PATCH request."""
        return await self._request(
            "PATCH", path, params=params, json_body=json_body, extra_headers=headers
        )

    # --- Pagination ---

    async def query_all(
        self,
        entity: str,
        *,
        where: str = "",
        order_by: str = "",
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a query with automatic pagination.

        Yields all results across pages using STARTPOSITION/MAXRESULTS.

        Args:
            entity: QuickBooks entity type (e.g. "Invoice", "Account").
            where: Optional WHERE clause.
            order_by: Optional ORDER BY clause.
            page_size: Results per page (default from settings).

        Returns:
            List of all entity records across all pages.
        """
        if page_size is None:
            page_size = self._settings.api_page_size

        all_items: list[dict[str, Any]] = []
        start_position = 0

        while True:
            query_parts = [f"SELECT * FROM {entity}"]
            if where:
                query_parts.append(f"WHERE {where}")
            if order_by:
                query_parts.append(f"ORDERBY {order_by}")
            query_parts.append(f"STARTPOSITION {start_position}")
            query_parts.append(f"MAXRESULTS {page_size}")
            query = " ".join(query_parts)

            result = await self.get(
                f"/v3/company/{self._realm_id}/query",
                params={"query": query},
            )

            query_response = result.get("QueryResponse", {})
            items = query_response.get(entity, [])
            all_items.extend(items)

            max_results = int(query_response.get("MaxResults", 0))
            total_count = int(query_response.get("TotalCount", 0))

            if len(items) < page_size or start_position + max_results >= total_count:
                break

            start_position += max_results

        return all_items
