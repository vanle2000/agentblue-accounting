"""QuickBooks sync repository — idempotent persistence for sync data.

Uses the project's async SQLAlchemy session. All upserts are idempotent:
replaying the same payload must not create duplicates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.db.models.quickbooks_sync import (
    QuickBooksSourceSnapshot,
    QuickBooksSyncCheckpoint,
    QuickBooksSyncRun,
    QuickBooksSyncRunEntity,
    QuickBooksTransaction,
    QuickBooksTransactionLine,
)
from agentblue.integrations.quickbooks.sync.domain import (
    EntityType,
    NormalizedTransaction,
    RecordOutcome,
    SyncMode,
    SyncStatus,
)

logger = structlog.get_logger(__name__)


def _hash_payload(payload: dict[str, Any]) -> str:
    """Compute a stable SHA-256 hash of a payload dict."""
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SyncRepository:
    """Repository for QuickBooks sync data persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Source snapshots ---

    async def upsert_source_snapshot(
        self,
        realm_id: str,
        entity_type: EntityType,
        raw: dict[str, Any],
        quickbooks_id: str,
        sync_token: int,
        source_status: str = "active",
        source_created_at: str = "",
        source_updated_at: str = "",
        source_deleted_at: str = "",
    ) -> RecordOutcome:
        """Upsert a source snapshot. Returns the record outcome."""
        payload_hash = _hash_payload(raw)
        now = _utcnow()

        stmt = select(QuickBooksSourceSnapshot).where(
            QuickBooksSourceSnapshot.realm_id == realm_id,
            QuickBooksSourceSnapshot.entity_type == entity_type.value,
            QuickBooksSourceSnapshot.quickbooks_id == quickbooks_id,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            if existing.payload_hash == payload_hash and source_status == existing.source_status:
                existing.last_seen_at = now
                return RecordOutcome.UNCHANGED
            existing.sync_token = sync_token
            existing.source_status = source_status
            existing.source_created_at = source_created_at
            existing.source_updated_at = source_updated_at
            existing.source_deleted_at = source_deleted_at
            existing.raw_payload = raw
            existing.payload_hash = payload_hash
            existing.last_seen_at = now
            return RecordOutcome.UPDATED

        snapshot = QuickBooksSourceSnapshot(
            realm_id=realm_id,
            entity_type=entity_type.value,
            quickbooks_id=quickbooks_id,
            sync_token=sync_token,
            source_status=source_status,
            source_created_at=source_created_at,
            source_updated_at=source_updated_at,
            source_deleted_at=source_deleted_at,
            raw_payload=raw,
            payload_hash=payload_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
        self._session.add(snapshot)
        return RecordOutcome.INSERTED

    async def mark_source_deleted(
        self,
        realm_id: str,
        entity_type: EntityType,
        quickbooks_id: str,
    ) -> RecordOutcome:
        """Mark a source snapshot as deleted."""
        stmt = select(QuickBooksSourceSnapshot).where(
            QuickBooksSourceSnapshot.realm_id == realm_id,
            QuickBooksSourceSnapshot.entity_type == entity_type.value,
            QuickBooksSourceSnapshot.quickbooks_id == quickbooks_id,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            if existing.source_status == "deleted":
                return RecordOutcome.UNCHANGED
            existing.source_status = "deleted"
            existing.source_deleted_at = _utcnow().isoformat()
            existing.last_seen_at = _utcnow()
            return RecordOutcome.MARKED_DELETED
        return RecordOutcome.UNCHANGED

    # --- Canonical transactions ---

    async def upsert_transaction(
        self,
        normalized: NormalizedTransaction,
        source_entity_id: str = "",
    ) -> RecordOutcome:
        """Upsert a canonical transaction and its lines."""
        now = _utcnow()

        stmt = select(QuickBooksTransaction).where(
            QuickBooksTransaction.realm_id == normalized.realm_id,
            QuickBooksTransaction.entity_type == normalized.entity_type.value,
            QuickBooksTransaction.quickbooks_id == normalized.quickbooks_id,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        source_deleted = normalized.source_deleted_at != ""

        if existing:
            changed = (
                existing.sync_token != normalized.sync_token
                or existing.total_amount != normalized.total_amount
                or existing.transaction_date != normalized.transaction_date
                or existing.source_deleted != source_deleted
            )
            if not changed:
                existing.last_synced_at = now
                return RecordOutcome.UNCHANGED

            # Update header
            existing.sync_token = normalized.sync_token
            existing.transaction_date = normalized.transaction_date
            existing.document_number = normalized.document_number
            existing.private_note = normalized.private_note
            existing.currency_code = normalized.currency_code
            existing.exchange_rate = normalized.exchange_rate
            existing.total_amount = normalized.total_amount
            existing.balance_amount = normalized.balance_amount
            existing.counterparty_type = normalized.counterparty_type
            existing.counterparty_quickbooks_id = normalized.counterparty_quickbooks_id
            existing.counterparty_name_snapshot = normalized.counterparty_name_snapshot
            existing.account_quickbooks_id = normalized.account_quickbooks_id
            existing.account_name_snapshot = normalized.account_name_snapshot
            existing.payment_type = normalized.payment_type
            existing.transaction_status = normalized.transaction_status
            existing.source_deleted = source_deleted
            existing.source_created_at = normalized.source_created_at
            existing.source_updated_at = normalized.source_updated_at
            existing.last_synced_at = now
            existing.source_entity_id = source_entity_id

            # Replace lines
            for line in existing.lines:
                await self._session.delete(line)
            await self._session.flush()

            for nline in normalized.lines:
                db_line = QuickBooksTransactionLine(
                    transaction_id=existing.id,
                    source_line_id=nline.source_line_id,
                    line_number=nline.line_number,
                    description=nline.description or None,
                    amount=nline.amount,
                    detail_type=nline.detail_type or None,
                    posting_type=nline.posting_type or None,
                    account_quickbooks_id=nline.account_quickbooks_id or None,
                    account_name_snapshot=nline.account_name_snapshot or None,
                    item_quickbooks_id=nline.item_quickbooks_id or None,
                    item_name_snapshot=nline.item_name_snapshot or None,
                    customer_quickbooks_id=nline.customer_quickbooks_id or None,
                    customer_name_snapshot=nline.customer_name_snapshot or None,
                    vendor_quickbooks_id=nline.vendor_quickbooks_id or None,
                    vendor_name_snapshot=nline.vendor_name_snapshot or None,
                    class_quickbooks_id=nline.class_quickbooks_id or None,
                    class_name_snapshot=nline.class_name_snapshot or None,
                    department_quickbooks_id=nline.department_quickbooks_id or None,
                    department_name_snapshot=nline.department_name_snapshot or None,
                    billable_status=nline.billable_status or None,
                    tax_code_quickbooks_id=nline.tax_code_quickbooks_id or None,
                    raw_line_payload=nline.raw_line_payload,
                )
                self._session.add(db_line)

            return RecordOutcome.UPDATED

        # Insert new
        txn = QuickBooksTransaction(
            realm_id=normalized.realm_id,
            entity_type=normalized.entity_type.value,
            quickbooks_id=normalized.quickbooks_id,
            sync_token=normalized.sync_token,
            transaction_date=normalized.transaction_date,
            document_number=normalized.document_number,
            private_note=normalized.private_note,
            currency_code=normalized.currency_code,
            exchange_rate=normalized.exchange_rate,
            total_amount=normalized.total_amount,
            balance_amount=normalized.balance_amount,
            source_entity_id=source_entity_id or None,
            counterparty_type=normalized.counterparty_type,
            counterparty_quickbooks_id=normalized.counterparty_quickbooks_id,
            counterparty_name_snapshot=normalized.counterparty_name_snapshot,
            account_quickbooks_id=normalized.account_quickbooks_id,
            account_name_snapshot=normalized.account_name_snapshot,
            payment_type=normalized.payment_type,
            transaction_status=normalized.transaction_status,
            source_deleted=source_deleted,
            source_created_at=normalized.source_created_at,
            source_updated_at=normalized.source_updated_at,
            first_synced_at=now,
            last_synced_at=now,
        )
        self._session.add(txn)
        await self._session.flush()

        for nline in normalized.lines:
            db_line = QuickBooksTransactionLine(
                transaction_id=txn.id,
                source_line_id=nline.source_line_id,
                line_number=nline.line_number,
                description=nline.description or None,
                amount=nline.amount,
                detail_type=nline.detail_type or None,
                posting_type=nline.posting_type or None,
                account_quickbooks_id=nline.account_quickbooks_id or None,
                account_name_snapshot=nline.account_name_snapshot or None,
                item_quickbooks_id=nline.item_quickbooks_id or None,
                item_name_snapshot=nline.item_name_snapshot or None,
                customer_quickbooks_id=nline.customer_quickbooks_id or None,
                customer_name_snapshot=nline.customer_name_snapshot or None,
                vendor_quickbooks_id=nline.vendor_quickbooks_id or None,
                vendor_name_snapshot=nline.vendor_name_snapshot or None,
                class_quickbooks_id=nline.class_quickbooks_id or None,
                class_name_snapshot=nline.class_name_snapshot or None,
                department_quickbooks_id=nline.department_quickbooks_id or None,
                department_name_snapshot=nline.department_name_snapshot or None,
                billable_status=nline.billable_status or None,
                tax_code_quickbooks_id=nline.tax_code_quickbooks_id or None,
                raw_line_payload=nline.raw_line_payload,
            )
            self._session.add(db_line)

        return RecordOutcome.INSERTED

    # --- Checkpoints ---

    async def get_checkpoint(
        self,
        realm_id: str,
        entity_type: EntityType,
        sync_mode: SyncMode,
    ) -> QuickBooksSyncCheckpoint | None:
        """Get the current checkpoint for a realm/entity/mode."""
        stmt = select(QuickBooksSyncCheckpoint).where(
            QuickBooksSyncCheckpoint.realm_id == realm_id,
            QuickBooksSyncCheckpoint.entity_type == entity_type.value,
            QuickBooksSyncCheckpoint.sync_mode == sync_mode.value,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def advance_checkpoint(
        self,
        realm_id: str,
        entity_type: EntityType,
        sync_mode: SyncMode,
        source_timestamp: datetime,
    ) -> None:
        """Atomically advance the checkpoint after successful persistence.

        Uses optimistic concurrency via checkpoint_version.
        Only advances forward — never moves a checkpoint backward.
        """
        now = _utcnow()
        stmt = (
            select(QuickBooksSyncCheckpoint)
            .where(
                QuickBooksSyncCheckpoint.realm_id == realm_id,
                QuickBooksSyncCheckpoint.entity_type == entity_type.value,
                QuickBooksSyncCheckpoint.sync_mode == sync_mode.value,
                # Use SELECT FOR UPDATE to prevent concurrent advancement
            )
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        checkpoint = result.scalar_one_or_none()

        if checkpoint is None:
            checkpoint = QuickBooksSyncCheckpoint(
                realm_id=realm_id,
                entity_type=entity_type.value,
                sync_mode=sync_mode.value,
                last_successful_source_timestamp=source_timestamp,
                last_successful_completed_at=now,
                checkpoint_version=1,
            )
            self._session.add(checkpoint)
        else:
            # Only advance forward
            if (
                checkpoint.last_successful_source_timestamp is not None
                and source_timestamp <= checkpoint.last_successful_source_timestamp
            ):
                logger.debug(
                    "checkpoint_not_advancing_backward",
                    realm_id=realm_id,
                    entity_type=entity_type.value,
                    current=str(checkpoint.last_successful_source_timestamp),
                    attempted=str(source_timestamp),
                )
                return
            checkpoint.last_successful_source_timestamp = source_timestamp
            checkpoint.last_successful_completed_at = now
            checkpoint.checkpoint_version += 1

    # --- Sync runs ---

    async def create_sync_run(
        self,
        realm_id: str,
        mode: SyncMode,
        entity_types: list[EntityType],
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> QuickBooksSyncRun:
        """Create a new sync run record."""
        run = QuickBooksSyncRun(
            realm_id=realm_id,
            mode=mode.value,
            requested_entity_types=",".join(e.value for e in entity_types),
            requested_start_at=start_at,
            requested_end_at=end_at,
            status=SyncStatus.RUNNING.value,
            started_at=_utcnow(),
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def create_sync_run_entity(
        self,
        sync_run_id: str,
        entity_type: EntityType,
    ) -> QuickBooksSyncRunEntity:
        """Create a per-entity result record within a sync run."""
        entity = QuickBooksSyncRunEntity(
            sync_run_id=sync_run_id,
            entity_type=entity_type.value,
            status=SyncStatus.RUNNING.value,
            started_at=_utcnow(),
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def update_sync_run_entity(
        self,
        entity_result_id: str,
        *,
        status: SyncStatus,
        pages_processed: int = 0,
        records_fetched: int = 0,
        records_inserted: int = 0,
        records_updated: int = 0,
        records_unchanged: int = 0,
        records_marked_deleted: int = 0,
        records_failed: int = 0,
        safe_error_code: str = "",
        safe_error_message: str = "",
    ) -> None:
        """Update a sync run entity result."""
        stmt = (
            update(QuickBooksSyncRunEntity)
            .where(QuickBooksSyncRunEntity.id == entity_result_id)
            .values(
                status=status.value,
                pages_processed=pages_processed,
                records_fetched=records_fetched,
                records_inserted=records_inserted,
                records_updated=records_updated,
                records_unchanged=records_unchanged,
                records_marked_deleted=records_marked_deleted,
                records_failed=records_failed,
                safe_error_code=safe_error_code or None,
                safe_error_message=safe_error_message or None,
                completed_at=_utcnow(),
            )
        )
        await self._session.execute(stmt)

    async def complete_sync_run(
        self,
        run_id: str,
        status: SyncStatus,
        safe_error_code: str = "",
        safe_error_message: str = "",
    ) -> None:
        """Mark a sync run as completed."""
        stmt = (
            update(QuickBooksSyncRun)
            .where(QuickBooksSyncRun.id == run_id)
            .values(
                status=status.value,
                completed_at=_utcnow(),
                safe_error_code=safe_error_code or None,
                safe_error_message=safe_error_message or None,
            )
        )
        await self._session.execute(stmt)

    async def get_sync_run(self, run_id: str) -> QuickBooksSyncRun | None:
        """Get a sync run by ID."""
        stmt = select(QuickBooksSyncRun).where(QuickBooksSyncRun.id == run_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
