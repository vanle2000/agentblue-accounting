"""Request correlation ID middleware.

Accepts an incoming X-Correlation-ID header or generates one.
Attaches it to structlog context, the response headers, and
the authenticated principal.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

_CORRELATION_HEADER = "X-Correlation-ID"
_MAX_CORRELATION_LENGTH = 128


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Middleware that ensures every request has a correlation ID."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process the request with a correlation ID."""
        # Accept incoming correlation ID or generate one.
        raw_id = request.headers.get(_CORRELATION_HEADER, "")
        if raw_id and len(raw_id) <= _MAX_CORRELATION_LENGTH:
            # Validate: only allow alphanumeric, hyphens, underscores.
            cleaned = raw_id.strip()
            if cleaned and all(c.isalnum() or c in "-_" for c in cleaned):
                correlation_id = cleaned
            else:
                correlation_id = str(uuid.uuid4())
        else:
            correlation_id = str(uuid.uuid4())

        # Attach to structlog context for this request.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
        )

        # Store in request state for access by dependencies.
        request.state.correlation_id = correlation_id

        response = await call_next(request)

        # Return correlation ID in response headers.
        response.headers[_CORRELATION_HEADER] = correlation_id

        return response
