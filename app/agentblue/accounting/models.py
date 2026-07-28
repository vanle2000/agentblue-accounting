"""SQLAlchemy ORM models for the Stage 10 accounting workflow.

Durable models for work items, write-back jobs, reconciliation,
escalation, and batch operations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
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


class AccountingWorkItem(Base):
    """Durable work item representing one accounting operation requiring review."""

    __tablename__ = "accounting_work_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)

    # Source
    source_system: Mapped[str] = mapped_column(String(50), nullable=False, default="QUICKBOOKS")
    source_transaction_id: Mapped[str] = mapped_column(String(50), nullable=False)
    source_transaction_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    transaction_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    vendor_or_payee: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    memo: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Current state
    current_account_quickbooks_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Recommendation
    recommended_account_quickbooks_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    recommended_account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recommendation_source: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    recommendation_confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0")
    )
    recommendation_explanation: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    supporting_evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    # Workflow
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="INGESTED")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW")
    assigned_reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_approver: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Timestamps
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Write-back tracking
    writeback_status: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_STARTED")
    reconciliation_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NOT_STARTED"
    )

    # Identity and versioning
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    # Failure tracking
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE")

    # Approval tracking
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_account_quickbooks_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Duplicate tracking
    duplicate_classification: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NOT_DUPLICATE"
    )
    duplicate_of_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accounting_work_item.id"), nullable=True
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
            "source_transaction_id",
            name="uq_work_item_source_txn",
        ),
        Index("ix_work_item_realm_status", "realm_id", "status"),
        Index("ix_work_item_review_queue", "realm_id", "status", "priority"),
        Index("ix_work_item_idempotency", "realm_id", "idempotency_key"),
    )


class WorkItemTransition(Base):
    """Immutable audit trail of state transitions."""

    __tablename__ = "work_item_transition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounting_work_item.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    from_status: Mapped[str] = mapped_column(String(30), nullable=False)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_transition_work_item", "work_item_id"),
        Index("ix_transition_realm", "realm_id"),
    )


class WorkItemCorrection(Base):
    """Records accountant corrections to recommendations."""

    __tablename__ = "work_item_correction"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounting_work_item.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_correction_work_item", "work_item_id"),)


class WriteBackJob(Base):
    """Durable write-back job with retry tracking."""

    __tablename__ = "write_back_job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounting_work_item.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    quickbooks_company_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    target_transaction_id: Mapped[str] = mapped_column(String(50), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False, default="UPDATE")
    expected_sync_token: Mapped[str | None] = mapped_column(String(50), nullable=True)
    approved_payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    approval_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    approver_principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    execution_principal_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quickbooks_response_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)

    failure_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconciliation_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NOT_STARTED"
    )

    request_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    response_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_writeback_job_idempotency"),
        Index("ix_wb_job_work_item", "work_item_id"),
        Index("ix_wb_job_status", "realm_id", "status"),
        Index("ix_wb_job_retry", "status", "next_retry_at"),
    )


class WriteBackAttempt(Base):
    """Immutable record of each write-back attempt."""

    __tablename__ = "write_back_attempt"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("write_back_job.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    response_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    quickbooks_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    execution_principal_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_wb_attempt_job", "job_id"),
        Index("ix_wb_attempt_realm", "realm_id"),
    )


class ReconciliationResult(Base):
    """Post-write reconciliation result."""

    __tablename__ = "reconciliation_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("write_back_job.id"), nullable=False
    )
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounting_work_item.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    approved_state: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    observed_state: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    differences: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    external_transaction_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    external_sync_token: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reconciled_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_recon_job", "job_id"),
        Index("ix_recon_status", "realm_id", "status"),
    )


class Escalation(Base):
    """Exception requiring human resolution."""

    __tablename__ = "escalation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounting_work_item.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    attempted_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    failure_history: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    recommended_next_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_escalation_work_item", "work_item_id"),
        Index("ix_escalation_status", "realm_id", "resolution_status"),
    )


class BatchOperation(Base):
    """Tracks batch review/approval operations."""

    __tablename__ = "batch_operation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="IN_PROGRESS")
    actor_principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_batch_realm", "realm_id"),
        Index("ix_batch_actor", "actor_principal_id"),
    )


class BatchOperationItem(Base):
    """Individual item within a batch operation."""

    __tablename__ = "batch_operation_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("batch_operation.id"), nullable=False
    )
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounting_work_item.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_batch_item_batch", "batch_id"),)
