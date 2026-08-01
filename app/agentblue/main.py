"""FastAPI application factory."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from agentblue.accounting.router import router as accounting_router
from agentblue.api.health import record_startup_complete
from agentblue.api.health import router as health_router
from agentblue.categorization.router import (
    router as categorization_router,
)
from agentblue.config import get_settings
from agentblue.db.session import dispose_engine
from agentblue.integrations.quickbooks.accounting.router import (
    router as quickbooks_accounting_router,
)
from agentblue.integrations.quickbooks.router import (
    router as quickbooks_router,
)
from agentblue.integrations.quickbooks.sync.router import (
    router as quickbooks_sync_router,
)
from agentblue.logging import configure_logging
from agentblue.ml.router import router as ml_router
from agentblue.observability.metrics import APP_INFO
from agentblue.security.middleware import CorrelationIDMiddleware
from agentblue.security.rate_limit import RateLimitMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Configure startup, validate production safety, and shutdown cleanly."""
    startup_start = time.monotonic()
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        is_development=settings.is_development,
    )

    logger = structlog.get_logger("agentblue")

    # Production-shadow safety banner
    if settings.is_production_shadow or settings.is_production:
        logger.warning(
            "production_mode_banner",
            environment=settings.app_env,
            accounting_execution_mode=settings.accounting_execution_mode,
            automatic_approval=settings.automatic_approval_enabled,
            autonomous_writeback=settings.autonomous_writeback_enabled,
            ml_promotion=settings.ml_promotion_enabled,
        )

    logger.info(
        "application_starting",
        environment=settings.app_env,
        execution_mode=settings.accounting_execution_mode,
    )

    # Set application info for metrics
    APP_INFO.info({
        "version": "0.1.0",
        "environment": settings.app_env,
        "execution_mode": settings.accounting_execution_mode,
    })

    # Startup validation
    try:
        # Validate production-shadow safety
        if settings.requires_strict_validation:
            if settings.automatic_approval_enabled:
                raise RuntimeError("PRODUCTION SAFETY: automatic_approval_enabled is true")
            if settings.autonomous_writeback_enabled:
                raise RuntimeError("PRODUCTION SAFETY: autonomous_writeback_enabled is true")
            if settings.ml_promotion_enabled:
                raise RuntimeError("PRODUCTION SAFETY: ml_promotion_enabled is true")

        record_startup_complete()
        startup_duration = time.monotonic() - startup_start
        logger.info(
            "application_started",
            startup_duration_seconds=round(startup_duration, 3),
            environment=settings.app_env,
        )

    except Exception as exc:
        logger.critical("startup_failed", error=str(exc)[:500])
        raise

    try:
        yield
    finally:
        logger.info("application_stopping", lifecycle_stage="shutdown")
        await dispose_engine()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title="Agent Blue Accounting",
        version="0.1.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Middleware (order matters — outermost first)
    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(RateLimitMiddleware)

    # Prometheus metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Public endpoints
    app.include_router(health_router)

    # Protected endpoints (authentication required)
    app.include_router(quickbooks_router)
    app.include_router(quickbooks_sync_router)
    app.include_router(quickbooks_accounting_router)
    app.include_router(categorization_router)
    app.include_router(accounting_router)
    app.include_router(ml_router)

    return app


app = create_app()
