"""QuickBooks transaction sync service — orchestration layer.

Coordinates backfill and incremental CDC synchronization between
the QuickBooks API and the local database. No business logic in
routers; no database details in API client; no normalization in
repositories.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.integrations.quickbooks.api_client import QuickBooksApiClient  # noqa: TC001
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksBackfillError,
    QuickBooksCdcWindowError,
    QuickBooksIncrementalSyncError,
    QuickBooksNormalizationError,
)
from agentblue.integrations.quickbooks.sync.domain import (
    EntityType,
    RecordOutcome,
    SyncMode,
    SyncRequest,
    SyncRunResult,
    SyncStatus,
    SyncWindow,
)
from agentblue.integrations.quickbooks.sync.query_builder import (
    build_backfill_query,
    build_cdc_query,
    format_date,
)
from agentblue.integrations.quickbooks.sync.registry import (
    get_registry_entry,
    normalize_entity,
)
from agentblue.integrations.quickbooks.sync.repository import SyncRepository

if TYPE_CHECKING:
    from agentblue.db.models.quickbooks_sync import QuickBooksSyncRun

logger = structlog.get_logger(__name__)

# CDC constants
_CDC_MAX_OBJECTS = 1000
_CDC_LOOKBACK_DAYS = 30
_CDC_DEFAULT_OVERLAP_SECONDS = 300  # 5 minutes
_CDC_MIN_WINDOW_SECONDS = 3600  # 1 hour minimum
_CDC_MAX_SPLIT_DEPTH = 10


class QuickBooksTransactionSyncService:
    """Orchestrates QuickBooks transaction synchronization.

    Dependencies are injected: API client, database session, settings.
    No global mutable state.
    """

    def __init__(
        self,
        client: QuickBooksApiClient,
        session: AsyncSession,
        *,
        cdc_overlap_seconds: int = _CDC_DEFAULT_OVERLAP_SECONDS,
        cdc_min_window_seconds: int = _CDC_MIN_WINDOW_SECONDS,
        cdc_max_split_depth: int = _CDC_MAX_SPLIT_DEPTH,
    ) -> None:
        self._client = client
        self._repo = SyncRepository(session)
        self._session = session
        self._cdc_overlap = cdc_overlap_seconds
        self._cdc_min_window = cdc_min_window_seconds
        self._cdc_max_split_depth = cdc_max_split_depth

    async def backfill(
        self,
        request: SyncRequest,
    ) -> SyncRunResult:
        """Perform an initial historical backfill.

        Fetches all pages, normalizes records, persists in bounded
        database batches, and updates checkpoints after successful commits.
        """
        run = await self._repo.create_sync_run(
            realm_id=request.realm_id,
            mode=SyncMode.BACKFILL,
            entity_types=request.entity_types,
            start_at=request.start_at,
            end_at=request.end_at,
        )

        result = SyncRunResult(
            sync_run_id=run.id,
            realm_id=request.realm_id,
            mode=SyncMode.BACKFILL,
            status=SyncStatus.RUNNING,
        )

        try:
            for entity_type in request.entity_types:
                entity_result = await self._backfill_entity(
                    request=request,
                    entity_type=entity_type,
                    sync_run_id=run.id,
                )
                result.entity_results.append(entity_result)

                if entity_result.status == SyncStatus.FAILED:
                    result.status = SyncStatus.PARTIAL
                elif result.status != SyncStatus.PARTIAL:
                    result.status = SyncStatus.COMPLETED

        except Exception as exc:
            result.status = SyncStatus.FAILED
            logger.error("backfill_failed", error=str(exc), sync_run_id=run.id)
            raise QuickBooksBackfillError(f"Backfill failed: {exc}") from exc
        finally:
            await self._repo.complete_sync_run(
                run.id,
                result.status,
            )
            await self._session.commit()

        return result

    async def _backfill_entity(
        self,
        request: SyncRequest,
        entity_type: EntityType,
        sync_run_id: str,
    ) -> Any:
        """Backfill a single entity type."""
        from agentblue.integrations.quickbooks.sync.domain import EntitySyncResult

        entry = get_registry_entry(entity_type)
        entity_run = await self._repo.create_sync_run_entity(sync_run_id, entity_type)

        entity_result = EntitySyncResult(
            entity_type=entity_type,
            status=SyncStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

        try:
            start_date = request.start_at or datetime.now(UTC) - timedelta(days=90)
            end_date = request.end_at or datetime.now(UTC)
            page_size = request.page_size

            start_position = 0
            page_count = 0

            while True:
                query = build_backfill_query(
                    entity_type,
                    start_date=format_date(start_date),
                    end_date=format_date(end_date),
                    start_position=start_position,
                    page_size=page_size,
                )

                result = await self._client.get(
                    f"/v3/company/{request.realm_id}/query",
                    params={"query": query},
                )

                query_response = result.get("QueryResponse", {})
                items = query_response.get(entry.quickbooks_entity_name, [])
                max_results = int(query_response.get("MaxResults", 0))
                total_count = int(query_response.get("TotalCount", 0))

                if not items:
                    break

                # Persist batch
                counts = await self._persist_batch(request.realm_id, entity_type, items)
                entity_result.records_fetched += counts["fetched"]
                entity_result.records_inserted += counts["inserted"]
                entity_result.records_updated += counts["updated"]
                entity_result.records_unchanged += counts["unchanged"]
                entity_result.records_marked_deleted += counts["marked_deleted"]
                entity_result.records_failed += counts["failed"]
                page_count += 1

                # Commit batch
                await self._session.commit()

                # Advance checkpoint
                await self._repo.advance_checkpoint(
                    request.realm_id,
                    entity_type,
                    SyncMode.BACKFILL,
                    end_date,
                )
                await self._session.commit()

                entity_result.window_start = start_date
                entity_result.window_end = end_date

                if len(items) < page_size or start_position + max_results >= total_count:
                    break
                start_position += max_results

            entity_result.pages_processed = page_count
            entity_result.status = SyncStatus.COMPLETED

        except Exception as exc:
            entity_result.status = SyncStatus.FAILED
            entity_result.safe_error_code = type(exc).__name__
            entity_result.safe_error_message = str(exc)[:500]
            logger.error(
                "backfill_entity_failed",
                entity_type=entity_type.value,
                error=str(exc)[:200],
            )

        finally:
            await self._repo.update_sync_run_entity(
                entity_run.id,
                status=entity_result.status,
                pages_processed=entity_result.pages_processed,
                records_fetched=entity_result.records_fetched,
                records_inserted=entity_result.records_inserted,
                records_updated=entity_result.records_updated,
                records_unchanged=entity_result.records_unchanged,
                records_marked_deleted=entity_result.records_marked_deleted,
                records_failed=entity_result.records_failed,
                safe_error_code=entity_result.safe_error_code,
                safe_error_message=entity_result.safe_error_message,
            )

        return entity_result

    async def sync_incremental(
        self,
        request: SyncRequest,
    ) -> SyncRunResult:
        """Perform incremental synchronization using CDC.

        Reads checkpoints, applies overlap window, processes CDC responses,
        and advances checkpoints after successful persistence.
        """
        run = await self._repo.create_sync_run(
            realm_id=request.realm_id,
            mode=SyncMode.INCREMENTAL,
            entity_types=request.entity_types,
        )

        result = SyncRunResult(
            sync_run_id=run.id,
            realm_id=request.realm_id,
            mode=SyncMode.INCREMENTAL,
            status=SyncStatus.RUNNING,
        )

        try:
            for entity_type in request.entity_types:
                entity_result = await self._incremental_entity(
                    request=request,
                    entity_type=entity_type,
                    sync_run_id=run.id,
                )
                result.entity_results.append(entity_result)

                if entity_result.status == SyncStatus.FAILED:
                    result.status = SyncStatus.PARTIAL
                elif result.status != SyncStatus.PARTIAL:
                    result.status = SyncStatus.COMPLETED

        except Exception as exc:
            result.status = SyncStatus.FAILED
            logger.error("incremental_sync_failed", error=str(exc), sync_run_id=run.id)
            raise QuickBooksIncrementalSyncError(f"Incremental sync failed: {exc}") from exc
        finally:
            await self._repo.complete_sync_run(run.id, result.status)
            await self._session.commit()

        return result

    async def _incremental_entity(
        self,
        request: SyncRequest,
        entity_type: EntityType,
        sync_run_id: str,
    ) -> Any:
        """Incremental sync for a single entity type using CDC with window splitting."""
        from agentblue.integrations.quickbooks.sync.domain import EntitySyncResult

        entry = get_registry_entry(entity_type)
        if not entry.cdc_support:
            entity_result = EntitySyncResult(
                entity_type=entity_type,
                status=SyncStatus.FAILED,
                safe_error_code="CDC_NOT_SUPPORTED",
                safe_error_message=f"CDC not supported for {entity_type.value}",
            )
            return entity_result

        entity_run = await self._repo.create_sync_run_entity(sync_run_id, entity_type)
        entity_result = EntitySyncResult(
            entity_type=entity_type,
            status=SyncStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

        try:
            checkpoint = await self._repo.get_checkpoint(
                request.realm_id, entity_type, SyncMode.INCREMENTAL
            )

            if checkpoint and checkpoint.last_successful_source_timestamp:
                since = checkpoint.last_successful_source_timestamp - timedelta(
                    seconds=self._cdc_overlap
                )
            elif request.start_at:
                since = request.start_at
            else:
                since = datetime.now(UTC) - timedelta(days=1)

            now = datetime.now(UTC)
            # Ensure we don't go beyond CDC lookback
            lookback_limit = now - timedelta(days=_CDC_LOOKBACK_DAYS)
            if since < lookback_limit:
                since = lookback_limit

            window = SyncWindow(start_at=since, end_at=now)

            counts = await self._cdc_window_with_splitting(
                request.realm_id,
                [entity_type],
                window,
                depth=0,
            )

            entity_result.records_fetched = counts["fetched"]
            entity_result.records_inserted = counts["inserted"]
            entity_result.records_updated = counts["updated"]
            entity_result.records_unchanged = counts["unchanged"]
            entity_result.records_marked_deleted = counts["marked_deleted"]
            entity_result.records_failed = counts["failed"]
            entity_result.pages_processed = counts.get("pages", 1)
            entity_result.window_start = window.start_at
            entity_result.window_end = window.end_at

            # Advance checkpoint
            await self._repo.advance_checkpoint(
                request.realm_id,
                entity_type,
                SyncMode.INCREMENTAL,
                now,
            )
            await self._session.commit()

            entity_result.status = SyncStatus.COMPLETED

        except Exception as exc:
            entity_result.status = SyncStatus.FAILED
            entity_result.safe_error_code = type(exc).__name__
            entity_result.safe_error_message = str(exc)[:500]
            logger.error(
                "incremental_entity_failed",
                entity_type=entity_type.value,
                error=str(exc)[:200],
            )

        finally:
            await self._repo.update_sync_run_entity(
                entity_run.id,
                status=entity_result.status,
                pages_processed=entity_result.pages_processed,
                records_fetched=entity_result.records_fetched,
                records_inserted=entity_result.records_inserted,
                records_updated=entity_result.records_updated,
                records_unchanged=entity_result.records_unchanged,
                records_marked_deleted=entity_result.records_marked_deleted,
                records_failed=entity_result.records_failed,
                safe_error_code=entity_result.safe_error_code,
                safe_error_message=entity_result.safe_error_message,
            )

        return entity_result

    async def _cdc_window_with_splitting(
        self,
        realm_id: str,
        entity_types: list[EntityType],
        window: SyncWindow,
        depth: int,
    ) -> dict[str, int]:
        """Execute a CDC query with window splitting if the response is near the limit.

        Recursively splits windows when the response approaches _CDC_MAX_OBJECTS.
        """
        if depth > self._cdc_max_split_depth:
            logger.warning(
                "cdc_max_split_depth_reached",
                depth=depth,
                window_start=str(window.start_at),
                window_end=str(window.end_at),
            )
            raise QuickBooksCdcWindowError(
                f"CDC window splitting exceeded max depth ({self._cdc_max_split_depth}). "
                f"Window: {window.start_at} to {window.end_at}"
            )

        if window.duration_seconds < self._cdc_min_window:
            logger.warning(
                "cdc_minimum_window_reached",
                duration_seconds=window.duration_seconds,
                min_seconds=self._cdc_min_window,
            )
            raise QuickBooksCdcWindowError(
                f"CDC window ({window.duration_seconds}s) is below minimum "
                f"({self._cdc_min_window}s). Cannot split further."
            )

        query = build_cdc_query(
            entity_types,
            changed_since=format_date(window.start_at),
        )

        result = await self._client.get(
            f"/v3/company/{realm_id}/cdc",
            params={"entities": query, "changedSince": format_date(window.start_at)},
        )

        cdc_response = result.get("CDCResponse", {})
        cdc_entities = (
            cdc_response.get("CDCResponse", [{}])[0] if cdc_response.get("CDCResponse") else {}
        )

        total_objects = 0
        counts = {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "marked_deleted": 0,
            "failed": 0,
            "pages": 1,
        }

        for entity_type in entity_types:
            entry = get_registry_entry(entity_type)
            items = cdc_entities.get(entry.quickbooks_entity_name, [])
            total_objects += len(items)

            if items:
                batch_counts = await self._persist_batch(realm_id, entity_type, items)
                for k in [
                    "fetched",
                    "inserted",
                    "updated",
                    "unchanged",
                    "marked_deleted",
                    "failed",
                ]:
                    counts[k] += batch_counts[k]

        await self._session.commit()

        # Check if near CDC limit — split if needed
        if total_objects >= _CDC_MAX_OBJECTS * 0.9:
            logger.info(
                "cdc_splitting_window",
                total_objects=total_objects,
                limit=_CDC_MAX_OBJECTS,
                depth=depth,
            )
            window_a, window_b = window.split()
            counts_a = await self._cdc_window_with_splitting(
                realm_id, entity_types, window_a, depth + 1
            )
            counts_b = await self._cdc_window_with_splitting(
                realm_id, entity_types, window_b, depth + 1
            )
            for k in counts:
                if k != "pages":
                    counts[k] = counts_a.get(k, 0) + counts_b.get(k, 0)
                else:
                    counts[k] = counts_a.get(k, 0) + counts_b.get(k, 0)

        return counts

    async def _persist_batch(
        self,
        realm_id: str,
        entity_type: EntityType,
        items: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Persist a batch of raw QuickBooks entities.

        Classifies each record as inserted/updated/unchanged/marked_deleted/failed.
        """
        counts = {
            "fetched": len(items),
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "marked_deleted": 0,
            "failed": 0,
        }

        for raw in items:
            try:
                qb_id = str(raw.get("Id", ""))
                sync_token = int(raw.get("SyncToken", 0))
                is_deleted = raw.get("domain") == "QBO" and raw.get("status") == "Deleted"

                # Upsert source snapshot
                await self._repo.upsert_source_snapshot(
                    realm_id=realm_id,
                    entity_type=entity_type,
                    raw=raw,
                    quickbooks_id=qb_id,
                    sync_token=sync_token,
                    source_status="deleted" if is_deleted else "active",
                    source_created_at=str(raw.get("MetaData", {}).get("CreateTime", "")),
                    source_updated_at=str(raw.get("MetaData", {}).get("LastUpdatedTime", "")),
                    source_deleted_at=str(raw.get("MetaData", {}).get("DeletedTime", ""))
                    if is_deleted
                    else "",
                )

                if is_deleted:
                    await self._repo.mark_source_deleted(realm_id, entity_type, qb_id)
                    counts["marked_deleted"] += 1
                    continue

                # Normalize and upsert canonical transaction
                normalized = normalize_entity(entity_type, raw, realm_id)
                txn_outcome = await self._repo.upsert_transaction(normalized)

                if txn_outcome == RecordOutcome.INSERTED:
                    counts["inserted"] += 1
                elif txn_outcome == RecordOutcome.UPDATED:
                    counts["updated"] += 1
                elif txn_outcome == RecordOutcome.UNCHANGED:
                    counts["unchanged"] += 1

            except QuickBooksNormalizationError as exc:
                counts["failed"] += 1
                logger.warning(
                    "normalization_failed",
                    entity_type=entity_type.value,
                    error=str(exc)[:200],
                )
            except Exception as exc:
                counts["failed"] += 1
                logger.warning(
                    "record_persist_failed",
                    entity_type=entity_type.value,
                    error=str(exc)[:200],
                )

        return counts

    async def get_sync_status(self, run_id: str) -> QuickBooksSyncRun | None:
        """Get the status of a sync run."""
        return await self._repo.get_sync_run(run_id)
