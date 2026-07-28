"""Basic rate limiting middleware.

Provides per-IP rate limiting for sensitive endpoints.
Uses a simple in-memory sliding-window counter.

For production multi-worker deployments, replace with Redis-backed
rate limiting. This implementation is suitable for single-instance
development and initial production deployment.
"""

from __future__ import annotations

import time
from collections import defaultdict

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger(__name__)

# Default limits: requests per window per IP.
_DEFAULT_LIMIT = 60
_DEFAULT_WINDOW_SECONDS = 60

# Stricter limits for sensitive endpoints.
_SENSITIVE_LIMITS: dict[str, tuple[int, int]] = {
    # path_prefix: (max_requests, window_seconds)
    "/api/v1/integrations/quickbooks/authorize": (10, 60),
    "/api/v1/integrations/quickbooks/callback": (10, 60),
    "/api/v1/ml/models": (30, 60),
    "/api/v1/categorization/categorizations": (100, 60),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP sliding-window rate limiter."""

    def __init__(self, app: object, **kwargs: object) -> None:
        super().__init__(app, **kwargs)  # type: ignore[arg-type]
        # {ip: {path_prefix: [timestamp, ...]}}
        self._requests: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP, respecting X-Forwarded-For behind a proxy."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Take the first IP (original client).
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _get_limit(self, path: str) -> tuple[int, int]:
        """Return (max_requests, window_seconds) for the path."""
        for prefix, (limit, window) in _SENSITIVE_LIMITS.items():
            if path.startswith(prefix):
                return limit, window
        return _DEFAULT_LIMIT, _DEFAULT_WINDOW_SECONDS

    def _cleanup(self, timestamps: list[float], window: float) -> None:
        """Remove expired entries from the sliding window."""
        cutoff = time.monotonic() - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Check rate limit before processing the request."""
        client_ip = self._get_client_ip(request)
        path = request.url.path
        limit, window = self._get_limit(path)

        timestamps = self._requests[client_ip][path]
        self._cleanup(timestamps, window)

        if len(timestamps) >= limit:
            logger.warning(
                "rate_limit_exceeded",
                client_ip=client_ip,
                path=path,
                limit=limit,
                window=window,
            )
            retry_after = int(window - (time.monotonic() - timestamps[0]))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Try again later.",
                    "retry_after": max(retry_after, 1),
                },
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        timestamps.append(time.monotonic())
        return await call_next(request)
