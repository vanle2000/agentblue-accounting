"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from agentblue.api.health import router as health_router
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Configure startup logging and dispose the database engine on shutdown."""
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        is_development=settings.is_development,
    )

    logger = structlog.get_logger("agentblue")

    logger.info(
        "application_starting",
    )

    try:
        yield
    finally:
        logger.info(
            "application_stopping",
            lifecycle_stage="shutdown",
        )
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
    app.include_router(health_router)
    app.include_router(quickbooks_router)
    app.include_router(quickbooks_sync_router)
    app.include_router(quickbooks_accounting_router)
    return app


app = create_app()
