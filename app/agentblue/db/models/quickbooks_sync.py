"""SQLAlchemy ORM models for QuickBooks transaction synchronization.

Stage 5 tables:
- qb_source_snapshot: Latest raw source representation of every QuickBooks entity
- qb_transaction: Canonical normalized accounting transaction
- qb_transaction_line: Normalized transaction line items
- qb_sync_checkpoint: Synchronization progress per realm/entity/mode
- qb_sync_run: Sync execution history
- qb_sync_run_entity: Per-entity result within a sync run
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentblue.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# A. QuickBooks source snapshot
# ---------------------------------------------------------------------------


class QuickBooksSourceSnapshot(Base):
    """Latest raw source representation of a QuickBooks entity."""

    __tablename__ = "qb_source_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    source_created_at: Mapped[str] = mapped_column(String(50), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(50), nullable=True)
    source_deleted_at: Mapped[str] = mapped_column(String(50), nullable=True)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "realm_id",
            "entity_type",
            "quickbooks_id",
            name="uq_source_snapshot_identity",
        ),
        Index("ix_source_snapshot_realm_entity", "realm_id", "entity_type"),
        Index("ix_source_snapshot_last_seen", "last_seen_at"),
    )


# ---------------------------------------------------------------------------
# B. Canonical accounting transaction
# ---------------------------------------------------------------------------


class QuickBooksTransaction(Base):
    """Normalized accounting transaction header."""

    __tablename__ = "qb_transaction"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transaction_date: Mapped[str] = mapped_column(String(20), nullable=True)
    document_number: Mapped[str] = mapped_column(String(50), nullable=True)
    private_note: Mapped[str] = mapped_column(Text, nullable=True)
    currency_code: Mapped[str] = mapped_column(String(10), nullable=True)
    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=6), nullable=True
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0")
    )
    balance_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=2), nullable=True
    )
    source_entity_id: Mapped[str] = mapped_column(String(36), nullable=True)
    counterparty_type: Mapped[str] = mapped_column(String(20), nullable=True)
    counterparty_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    counterparty_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    account_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    payment_type: Mapped[str] = mapped_column(String(20), nullable=True)
    transaction_status: Mapped[str] = mapped_column(String(20), nullable=True)
    source_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_created_at: Mapped[str] = mapped_column(String(50), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(50), nullable=True)
    first_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    lines: Mapped[list[QuickBooksTransactionLine]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint(
            "realm_id",
            "entity_type",
            "quickbooks_id",
            name="uq_transaction_identity",
        ),
        Index("ix_transaction_realm_entity", "realm_id", "entity_type"),
        Index("ix_transaction_date", "transaction_date"),
        Index("ix_transaction_source_deleted", "source_deleted"),
    )


# ---------------------------------------------------------------------------
# C. Canonical transaction lines
# ---------------------------------------------------------------------------


class QuickBooksTransactionLine(Base):
    """Normalized transaction line item."""

    __tablename__ = "qb_transaction_line"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qb_transaction.id"), nullable=False
    )
    source_line_id: Mapped[str] = mapped_column(String(50), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0")
    )
    detail_type: Mapped[str] = mapped_column(String(50), nullable=True)
    posting_type: Mapped[str] = mapped_column(String(10), nullable=True)
    account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    account_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    item_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    item_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    customer_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    customer_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    vendor_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    vendor_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    class_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    class_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    department_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    department_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=True)
    billable_status: Mapped[str] = mapped_column(String(20), nullable=True)
    tax_code_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    raw_line_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    transaction: Mapped[QuickBooksTransaction] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            "source_line_id",
            name="uq_transaction_line_identity",
        ),
        Index("ix_transaction_line_txn", "transaction_id"),
    )


# ---------------------------------------------------------------------------
# D. Sync checkpoint
# ---------------------------------------------------------------------------


class QuickBooksSyncCheckpoint(Base):
    """Synchronization progress by realm and entity type."""

    __tablename__ = "qb_sync_checkpoint"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    last_successful_source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "realm_id",
            "entity_type",
            "sync_mode",
            name="uq_checkpoint_identity",
        ),
        Index("ix_checkpoint_realm_entity_mode", "realm_id", "entity_type", "sync_mode"),
    )


# ---------------------------------------------------------------------------
# E. Sync run
# ---------------------------------------------------------------------------


class QuickBooksSyncRun(Base):
    """Sync execution history."""

    __tablename__ = "qb_sync_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_entity_types: Mapped[str] = mapped_column(Text, nullable=False)
    requested_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_marked_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safe_error_code: Mapped[str] = mapped_column(String(50), nullable=True)
    safe_error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    entity_results: Mapped[list[QuickBooksSyncRunEntity]] = relationship(
        back_populates="sync_run", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_sync_run_realm", "realm_id"),
        Index("ix_sync_run_status", "status"),
    )


# ---------------------------------------------------------------------------
# E2. Sync run entity result
# ---------------------------------------------------------------------------


class QuickBooksSyncRunEntity(Base):
    """Per-entity result within a sync run."""

    __tablename__ = "qb_sync_run_entity"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sync_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qb_sync_run.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pages_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_marked_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safe_error_code: Mapped[str] = mapped_column(String(50), nullable=True)
    safe_error_message: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sync_run: Mapped[QuickBooksSyncRun] = relationship(back_populates="entity_results")

    __table_args__ = (
        Index("ix_sync_run_entity_run", "sync_run_id"),
        Index("ix_sync_run_entity_type", "entity_type"),
    )
