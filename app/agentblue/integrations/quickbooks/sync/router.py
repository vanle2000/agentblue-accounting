"""QuickBooks sync FastAPI endpoints.

Minimal internal/admin endpoints for triggering and monitoring
sync operations. Business logic is delegated to the sync service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.db.session import get_db
from agentblue.integrations.quickbooks.config import get_quickbooks_settings
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksSyncError,
    QuickBooksUnsupportedEntityError,
)
from agentblue.integrations.quickbooks.repository import (
    InMemoryTokenRepository,
)
from agentblue.integrations.quickbooks.sync.domain import (
    EntityType,
    SyncMode,
    SyncRequest,
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/integrations/quickbooks/sync",
    tags=["quickbooks-sync"],
)


# --- Request/Response models ---


class BackfillRequest(BaseModel):
    """Request body for backfill operations."""

    realm_id: str
    entity_types: list[str] = Field(
        default=["Purchase", "Deposit", "Invoice"],
        description="QuickBooks entity types to sync",
    )
    start_at: datetime | None = None
    end_at: datetime | None = None
    page_size: int = Field(default=100, ge=1, le=1000)


class IncrementalRequest(BaseModel):
    """Request body for incremental sync operations."""

    realm_id: str
    entity_types: list[str] = Field(
        default=["Purchase", "Deposit", "Invoice"],
        description="QuickBooks entity types to sync",
    )


class SyncRunResponse(BaseModel):
    """Response from sync operations."""

    sync_run_id: str
    status: str
    records_fetched: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_unchanged: int = 0
    records_marked_deleted: int = 0
    records_failed: int = 0
    entity_results: list[dict[str, Any]] = []


class SyncStatusResponse(BaseModel):
    """Response for sync status lookups."""

    sync_run_id: str
    realm_id: str
    mode: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    records_fetched: int = 0
    records_inserted: int = 0
    records_updated: int = 0


def _parse_entity_types(entity_names: list[str]) -> list[EntityType]:
    """Parse entity type names, raising on unsupported types."""
    types: list[EntityType] = []
    for name in entity_names:
        try:
            types.append(EntityType(name))
        except ValueError as err:
            raise QuickBooksUnsupportedEntityError(f"Unsupported entity type: {name!r}") from err
    return types


def _build_sync_response(run_result: Any) -> SyncRunResponse:
    """Convert a SyncRunResult to a response model."""
    entity_results = []
    for er in run_result.entity_results:
        entity_results.append(
            {
                "entity_type": er.entity_type.value,
                "status": er.status.value,
                "records_fetched": er.records_fetched,
                "records_inserted": er.records_inserted,
                "records_updated": er.records_updated,
                "records_unchanged": er.records_unchanged,
                "records_marked_deleted": er.records_marked_deleted,
                "records_failed": er.records_failed,
                "safe_error_code": er.safe_error_code,
                "safe_error_message": er.safe_error_message,
            }
        )

    return SyncRunResponse(
        sync_run_id=run_result.sync_run_id,
        status=run_result.status.value,
        records_fetched=run_result.records_fetched,
        records_inserted=run_result.records_inserted,
        records_updated=run_result.records_updated,
        records_unchanged=run_result.records_unchanged,
        records_marked_deleted=run_result.records_marked_deleted,
        records_failed=run_result.records_failed,
        entity_results=entity_results,
    )


# --- Endpoints ---


@router.post("/backfill", response_model=SyncRunResponse)
async def trigger_backfill(
    body: BackfillRequest,
    db: AsyncSession = Depends(get_db),
) -> SyncRunResponse:
    """Trigger an initial historical backfill."""
    settings = get_quickbooks_settings()
    entity_types = _parse_entity_types(body.entity_types)

    from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
    from agentblue.integrations.quickbooks.sync.service import (
        QuickBooksTransactionSyncService,
    )

    repository = InMemoryTokenRepository()
    request = SyncRequest(
        realm_id=body.realm_id,
        entity_types=entity_types,
        mode=SyncMode.BACKFILL,
        start_at=body.start_at,
        end_at=body.end_at,
        page_size=body.page_size,
    )

    try:
        async with QuickBooksApiClient(settings, repository, body.realm_id) as client:
            service = QuickBooksTransactionSyncService(client, db)
            result = await service.backfill(request)
        return _build_sync_response(result)
    except QuickBooksUnsupportedEntityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QuickBooksSyncError as exc:
        logger.error("backfill_endpoint_failed", error=str(exc)[:200])
        raise HTTPException(status_code=500, detail="Sync operation failed.") from exc


@router.post("/incremental", response_model=SyncRunResponse)
async def trigger_incremental(
    body: IncrementalRequest,
    db: AsyncSession = Depends(get_db),
) -> SyncRunResponse:
    """Trigger an incremental CDC sync."""
    settings = get_quickbooks_settings()
    entity_types = _parse_entity_types(body.entity_types)

    from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient
    from agentblue.integrations.quickbooks.sync.service import (
        QuickBooksTransactionSyncService,
    )

    repository = InMemoryTokenRepository()
    request = SyncRequest(
        realm_id=body.realm_id,
        entity_types=entity_types,
        mode=SyncMode.INCREMENTAL,
    )

    try:
        async with QuickBooksApiClient(settings, repository, body.realm_id) as client:
            service = QuickBooksTransactionSyncService(client, db)
            result = await service.sync_incremental(request)
        return _build_sync_response(result)
    except QuickBooksUnsupportedEntityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QuickBooksSyncError as exc:
        logger.error("incremental_endpoint_failed", error=str(exc)[:200])
        raise HTTPException(status_code=500, detail="Sync operation failed.") from exc


@router.get("/runs/{sync_run_id}", response_model=SyncStatusResponse)
async def get_sync_run_status(
    sync_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> SyncStatusResponse:
    """Get the status of a specific sync run."""
    from agentblue.integrations.quickbooks.sync.repository import SyncRepository

    repo = SyncRepository(db)
    run = await repo.get_sync_run(sync_run_id)

    if run is None:
        raise HTTPException(status_code=404, detail="Sync run not found.")

    return SyncStatusResponse(
        sync_run_id=run.id,
        realm_id=run.realm_id,
        mode=run.mode,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        records_fetched=run.records_fetched,
        records_inserted=run.records_inserted,
        records_updated=run.records_updated,
    )
