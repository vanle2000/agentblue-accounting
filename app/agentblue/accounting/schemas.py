"""Accounting Pydantic schemas for review, approval, and workflow endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# --- Work Item ---


class WorkItemSummary(BaseModel):
    """Compact representation for list endpoints."""

    id: str
    realm_id: str
    source_transaction_id: str
    source_transaction_type: str = ""
    vendor_or_payee: str = ""
    amount: str = "0"
    currency: str = "USD"
    status: str
    risk_level: str = "LOW"
    recommended_account_quickbooks_id: str = ""
    recommended_account_name: str = ""
    recommendation_confidence: str = "0"
    recommendation_source: str = ""
    assigned_reviewer: str | None = None
    priority: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkItemDetail(WorkItemSummary):
    """Full work item representation."""

    description: str = ""
    memo: str = ""
    transaction_date: datetime | None = None
    current_account_quickbooks_id: str | None = None
    current_account_name: str | None = None
    recommendation_explanation: dict[str, Any] = {}
    supporting_evidence: dict[str, Any] = {}
    assigned_approver: str | None = None
    reviewed_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    approved_account_quickbooks_id: str | None = None
    correction_reason: str | None = None
    rejected_at: datetime | None = None
    deferred_at: datetime | None = None
    writeback_status: str = "NOT_STARTED"
    reconciliation_status: str = "NOT_STARTED"
    failure_code: str | None = None
    failure_details: str | None = None
    escalation_status: str = "NONE"
    duplicate_classification: str = "NOT_DUPLICATE"
    duplicate_of_id: str | None = None
    source_system: str = "QUICKBOOKS"
    correlation_id: str = ""
    version: int = 1


class WorkItemListResponse(BaseModel):
    """Paginated work item list."""

    items: list[WorkItemSummary]
    total: int
    limit: int
    offset: int


# --- Correction ---


class CorrectionRequest(BaseModel):
    """Request to correct a recommendation."""

    realm_id: str
    field_name: str
    new_value: str
    reason: str = Field(..., min_length=1, max_length=2000)


class CorrectionResponse(BaseModel):
    """Response after recording a correction."""

    correction_id: str
    work_item_id: str
    field_name: str
    previous_value: str | None = None
    new_value: str | None = None
    status: str


# --- Approval / Review actions ---


class ApprovalRequest(BaseModel):
    """Approve a work item."""

    realm_id: str
    approved_account_quickbooks_id: str = ""
    reason: str = ""


class ApprovalResponse(BaseModel):
    """Response after approval."""

    work_item_id: str
    status: str
    approved_by: str


class RejectionRequest(BaseModel):
    """Reject a work item."""

    reason: str = ""


class RejectionResponse(BaseModel):
    """Response after rejection."""

    work_item_id: str
    status: str


class DeferRequest(BaseModel):
    """Defer a work item for later review."""

    reason: str = ""


class DeferResponse(BaseModel):
    """Response after deferral."""

    work_item_id: str
    status: str


class EscalateRequest(BaseModel):
    """Escalate a work item."""

    category: str
    explanation: str = Field(..., min_length=1, max_length=5000)
    severity: str = "MEDIUM"
    recommended_next_step: str = ""


class EscalateResponse(BaseModel):
    """Response after escalation."""

    work_item_id: str
    escalation_id: str
    status: str


class ClaimResponse(BaseModel):
    """Response after claiming a work item for review."""

    work_item_id: str
    status: str
    assigned_reviewer: str


class ReleaseResponse(BaseModel):
    """Response after releasing from review."""

    work_item_id: str
    status: str


# --- Batch ---


class BatchApproveRequest(BaseModel):
    """Batch approve multiple work items."""

    realm_id: str
    work_item_ids: list[str] = Field(..., min_length=1, max_length=100)
    reason: str = ""


class BatchItemResult(BaseModel):
    """Result for a single item in a batch."""

    work_item_id: str
    outcome: str  # SUCCESS, FAILED, SKIPPED
    error_message: str = ""


class BatchApproveResponse(BaseModel):
    """Response for a batch operation."""

    batch_id: str
    operation_type: str
    requested_count: int
    successful_count: int
    failed_count: int
    skipped_count: int
    items: list[BatchItemResult]


# --- Escalation ---


class EscalationSummary(BaseModel):
    """Compact escalation representation."""

    id: str
    work_item_id: str
    realm_id: str
    category: str
    severity: str
    explanation: str
    resolution_status: str = "OPEN"
    assigned_owner: str | None = None
    due_date: datetime | None = None
    created_at: datetime | None = None


class EscalationDetail(EscalationSummary):
    """Full escalation representation."""

    supporting_evidence: dict[str, Any] = {}
    attempted_actions: list[str] = []
    failure_history: list[dict[str, Any]] = []
    recommended_next_step: str | None = None
    resolution_note: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    correlation_id: str = ""


class EscalationListResponse(BaseModel):
    """Paginated escalation list."""

    items: list[EscalationSummary]
    total: int
    limit: int


class EscalationResolveRequest(BaseModel):
    """Resolve an escalation."""

    resolution_note: str = Field(..., min_length=1, max_length=5000)
    action: str = "RESOLVED"  # RESOLVED, REOPENED


class EscalationResolveResponse(BaseModel):
    """Response after resolving an escalation."""

    escalation_id: str
    resolution_status: str
    resolved_by: str


# --- Write-back Jobs ---


class WriteBackJobSummary(BaseModel):
    """Compact write-back job representation."""

    id: str
    work_item_id: str
    realm_id: str
    status: str
    operation_type: str = "UPDATE"
    attempt_count: int = 0
    max_attempts: int = 3
    failure_category: str | None = None
    failure_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WriteBackJobListResponse(BaseModel):
    """Paginated write-back job list."""

    items: list[WriteBackJobSummary]
    total: int
    limit: int


class WriteBackExecuteResponse(BaseModel):
    """Response after executing a write-back job."""

    job_id: str
    work_item_id: str
    status: str
    attempt_count: int


# --- Reconciliation ---


class ReconciliationResultResponse(BaseModel):
    """Reconciliation result detail."""

    id: str
    job_id: str
    work_item_id: str
    realm_id: str
    status: str
    approved_state: dict[str, Any] = {}
    observed_state: dict[str, Any] = {}
    differences: list[dict[str, Any]] = []
    external_transaction_id: str = ""
    external_sync_token: str | None = None
    reconciled_by: str = "system"
    notes: str | None = None
    created_at: datetime | None = None
