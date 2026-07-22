"""SQLAlchemy ORM models for QuickBooks Chart of Accounts.

Stage 6 tables:
- qb_account_source_snapshot: Raw account payloads
- qb_account: Canonical account records
- qb_transaction_account_ref: Transaction-to-account references
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
from sqlalchemy.orm import Mapped, mapped_column

from agentblue.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class QuickBooksAccountSourceSnapshot(Base):
    """Raw QuickBooks Account source snapshot."""

    __tablename__ = "qb_account_source_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_created_at: Mapped[str] = mapped_column(String(50), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(50), nullable=True)
    source_last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("realm_id", "quickbooks_id", name="uq_account_snapshot_identity"),
        Index("ix_account_snapshot_realm", "realm_id"),
    )


class QuickBooksAccount(Base):
    """Canonical normalized QuickBooks account."""

    __tablename__ = "qb_account"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    sync_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    fully_qualified_name: Mapped[str] = mapped_column(String(500), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    classification: Mapped[str] = mapped_column(String(50), nullable=True)
    account_type: Mapped[str] = mapped_column(String(100), nullable=True)
    account_subtype: Mapped[str] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    subaccount: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parent_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    parent_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    account_number: Mapped[str] = mapped_column(String(50), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(10), nullable=True)
    current_balance: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0")
    )
    current_balance_with_subaccounts: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0")
    )
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_created_at: Mapped[str] = mapped_column(String(50), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(50), nullable=True)
    source_last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("realm_id", "quickbooks_id", name="uq_account_identity"),
        Index("ix_account_realm_active", "realm_id", "active"),
        Index("ix_account_realm_deleted", "realm_id", "source_deleted"),
        Index("ix_account_realm_type", "realm_id", "account_type"),
        Index("ix_account_realm_classification", "realm_id", "classification"),
        Index("ix_account_realm_parent", "realm_id", "parent_quickbooks_id"),
        Index("ix_account_realm_fqn", "realm_id", "fully_qualified_name"),
    )


class QuickBooksTransactionAccountRef(Base):
    """Resolved account reference for a transaction or line."""

    __tablename__ = "qb_transaction_account_ref"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    quickbooks_account_id: Mapped[str] = mapped_column(String(50), nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("qb_account.id"), nullable=True)
    reference_role: Mapped[str] = mapped_column(String(30), nullable=False)
    source_line_id: Mapped[str] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_txn_account_ref_txn", "transaction_id"),
        Index("ix_txn_account_ref_realm_account", "realm_id", "quickbooks_account_id"),
    )
