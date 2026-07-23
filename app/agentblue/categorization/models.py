"""SQLAlchemy ORM models for categorization (Stage 7)."""

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


class CategorizationRule(Base):
    """Deterministic categorization rule."""

    __tablename__ = "categorization_rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    precedence: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    conditions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    target_account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    target_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    minimum_confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("0")
    )
    stop_processing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_system_rule: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_rule_realm_status", "realm_id", "rule_status"),
        Index("ix_rule_realm_active", "realm_id", "precedence"),
    )


class CategorizationRun(Base):
    """Tracks each categorization operation."""

    __tablename__ = "categorization_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    run_type: Mapped[str] = mapped_column(String(20), nullable=False, default="BATCH")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recommended_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preselected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    apply_failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_catrun_realm", "realm_id"),)


class TransactionCategorization(Base):
    """Current categorization state for a transaction."""

    __tablename__ = "transaction_categorization"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transaction_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    recommended_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    recommended_account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=True)
    approved_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    approved_account_quickbooks_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_quickbooks_account_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence_score: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0")
    )
    confidence_band: Mapped[str] = mapped_column(String(10), nullable=False, default="NONE")
    recommendation_source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="FEATURE_RANKING"
    )
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    rule_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("categorization_rule.id"), nullable=True
    )
    explanation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_transaction_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_sync_token: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_last_updated_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("realm_id", "transaction_id", name="uq_categorization_transaction"),
        Index("ix_cat_txn_realm_status", "realm_id", "status"),
        Index("ix_cat_txn_review", "realm_id", "requires_review", "status"),
    )


class CategorizationRecommendation(Base):
    """Ranked candidate recommendation."""

    __tablename__ = "categorization_recommendation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    categorization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transaction_categorization.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, default=Decimal("0"))
    confidence_band: Mapped[str] = mapped_column(String(10), nullable=False, default="NONE")
    recommendation_source: Mapped[str] = mapped_column(String(30), nullable=False)
    explanation: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    feature_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rule_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("categorization_rule.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "categorization_id",
            "account_quickbooks_id",
            name="uq_rec_categorization_account",
        ),
        Index("ix_rec_categorization_rank", "categorization_id", "rank"),
    )


class CategorizationDecision(Base):
    """Immutable audit trail of human decisions."""

    __tablename__ = "categorization_decision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    categorization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transaction_categorization.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    previous_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    selected_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    reviewer: Mapped[str] = mapped_column(String(100), nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    categorization_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)
    recommendation_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_decision_categorization", "categorization_id"),)


class VendorMapping(Base):
    """Normalized vendor-to-account mapping."""

    __tablename__ = "vendor_mapping"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    normalized_vendor_name: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_vendor_example: Mapped[str | None] = mapped_column(String(500), nullable=True)
    vendor_quickbooks_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    target_account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    approval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "realm_id",
            "normalized_vendor_name",
            "target_account_quickbooks_id",
            name="uq_vendor_mapping",
        ),
        Index("ix_vendor_realm_normalized", "realm_id", "normalized_vendor_name"),
    )


class CategorizationTrainingLabel(Base):
    """Reusable approved labels for future ML training."""

    __tablename__ = "categorization_training_label"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transaction_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    selected_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    selected_account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    label_source: Mapped[str] = mapped_column(String(30), nullable=False)
    feature_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_label_realm_txn", "realm_id", "transaction_id"),)


class QuickBooksCategorizationApplication(Base):
    """Tracks the approved QuickBooks write operation."""

    __tablename__ = "qb_categorization_application"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    categorization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transaction_categorization.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transaction_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    selected_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("qb_account.id"), nullable=True
    )
    selected_account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_sync_token: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_transaction_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    response_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    resulting_sync_token: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quickbooks_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_by: Mapped[str] = mapped_column(String(100), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_application_idempotency_key"),
        Index("ix_app_categorization", "categorization_id"),
        Index("ix_app_realm_status", "realm_id", "status"),
    )
