"""Health check endpoints — liveness, readiness, and startup probes."""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.config import Settings, get_settings
from agentblue.db.session import get_db

router = APIRouter(prefix="/api/v1/health", tags=["health"])
logger = structlog.get_logger(__name__)

# Module-level startup timestamp
_startup_time: float = 0.0
_startup_checks_passed: bool = False


def record_startup_complete() -> None:
    """Record that startup checks have passed."""
    global _startup_checks_passed, _startup_time
    _startup_checks_passed = True
    _startup_time = time.monotonic()


class LivenessResponse(BaseModel):
    """Response model for the liveness probe."""

    status: str = "ok"
    service: str = "agentblue-accounting"
    uptime_seconds: float = 0.0


class ReadinessResponse(BaseModel):
    """Response model for the readiness probe."""

    status: str = "ok"
    database: str = "connected"
    migrations_current: bool = True


class StartupResponse(BaseModel):
    """Response model for the startup probe."""

    status: str = "ok"
    checks_passed: bool = False
    environment: str = ""
    accounting_execution_mode: str = ""


class ModeResponse(BaseModel):
    """Response model for the mode confirmation endpoint."""

    environment: str
    accounting_execution_mode: str
    automatic_approval_enabled: bool
    autonomous_writeback_enabled: bool
    ml_promotion_enabled: bool
    worker_enabled: bool
    rate_limit_enabled: bool


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    """Liveness probe.

    Returns HTTP 200 when the FastAPI process is running.
    Does not check external dependencies.
    """
    uptime = time.monotonic() - _startup_time if _startup_time else 0.0
    return LivenessResponse(uptime_seconds=round(uptime, 1))


@router.get("/ready", response_model=ReadinessResponse)
async def ready(
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    """Readiness probe.

    Verifies database connectivity and migration state.
    Returns HTTP 200 when ready to serve traffic.
    Returns HTTP 503 when not ready.
    """
    try:
        # Check database connectivity
        await session.execute(text("SELECT 1"))

        # Check migration state
        result = await session.execute(
            text("SELECT version_num FROM alembic_version")
        )
        current_version = result.scalar()

        if not current_version:
            logger.warning("readiness_check_failed", reason="no_migration_version")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return ReadinessResponse(
                status="error",
                database="connected",
                migrations_current=False,
            )

        return ReadinessResponse(
            status="ok",
            database="connected",
            migrations_current=True,
        )

    except Exception:
        logger.error("readiness_check_failed", reason="db_unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="error",
            database="unavailable",
            migrations_current=False,
        )


@router.get("/startup", response_model=StartupResponse)
async def startup(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> StartupResponse:
    """Startup probe.

    Verifies that startup checks have completed and production-shadow
    safety controls are enforced.
    """
    if not _startup_checks_passed:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return StartupResponse(
            status="starting",
            checks_passed=False,
            environment=settings.app_env,
            accounting_execution_mode=settings.accounting_execution_mode,
        )

    return StartupResponse(
        status="ok",
        checks_passed=True,
        environment=settings.app_env,
        accounting_execution_mode=settings.accounting_execution_mode,
    )


@router.get("/mode", response_model=ModeResponse)
async def mode(
    settings: Settings = Depends(get_settings),
) -> ModeResponse:
    """Confirm current deployment mode (no secrets exposed)."""
    return ModeResponse(**settings.get_mode_summary())
