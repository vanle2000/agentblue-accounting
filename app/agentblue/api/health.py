"""Health check endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.db.session import get_db

router = APIRouter(prefix="/api/v1/health", tags=["health"])
logger = structlog.get_logger(__name__)


class LivenessResponse(BaseModel):
    """Response model for the liveness probe."""

    status: str = "ok"
    service: str = "agentblue-accounting"


class ReadinessResponse(BaseModel):
    """Response model for the readiness probe."""

    status: str = "ok"
    database: str = "connected"


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    """Liveness probe.

    Returns HTTP 200 when the FastAPI process is running.
    Does not check PostgreSQL.
    """
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def ready(
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> ReadinessResponse:
    """Readiness probe.

    Verifies the PostgreSQL connection using SELECT 1.
    Returns HTTP 200 when the database is reachable.
    Returns HTTP 503 when the database is unavailable.
    """
    try:
        await session.execute(text("SELECT 1"))
        return ReadinessResponse()
    except Exception:
        logger.error("readiness_check_failed", reason="db_unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="error", database="unavailable")
