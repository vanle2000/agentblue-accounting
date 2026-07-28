"""Accounting FastAPI router for review, approval, and workflow management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    BatchApproveRequest,
    BatchApproveResponse,
    BatchItemResult,
    ClaimResponse,
    CorrectionRequest,
    CorrectionResponse,
    DeferRequest,
    DeferResponse,
    EscalateRequest,
    EscalateResponse,
    EscalationListResponse,
    EscalationResolveRequest,
    EscalationResolveResponse,
    EscalationSummary,
    ReconciliationResultResponse,
    RejectionRequest,
    RejectionResponse,
    ReleaseResponse,
    WorkItemDetail,
    WorkItemListResponse,
    WorkItemSummary,
    WriteBackExecuteResponse,
    WriteBackJobListResponse,
    WriteBackJobSummary,
)
from agentblue.accounting.services import (
    ApprovalService,
    BatchService,
    CorrectionService,
    EscalationService,
    ReviewQueueService,
    WriteBackQueryService,
)
from agentblue.db.session import get_db
from agentblue.security.context import ExecutionContext
from agentblue.security.policy import (
    require_accounting_approve,
    require_accounting_read,
    require_accounting_review,
    require_accounting_writeback,
)
from agentblue.security.realm import require_realm_access

if TYPE_CHECKING:
    from agentblue.accounting.models import AccountingWorkItem
    from agentblue.security.principal import Principal

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/accounting",
    tags=["accounting"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_summary(item: AccountingWorkItem) -> WorkItemSummary:
    """Map an AccountingWorkItem ORM object to a WorkItemSummary."""
    return WorkItemSummary(
        id=item.id,
        realm_id=item.realm_id,
        source_transaction_id=item.source_transaction_id,
        source_transaction_type=item.source_transaction_type or "",
        vendor_or_payee=item.vendor_or_payee or "",
        amount=str(item.amount),
        currency=item.currency,
        status=item.status,
        risk_level=item.risk_level,
        recommended_account_quickbooks_id=item.recommended_account_quickbooks_id or "",
        recommended_account_name=item.recommended_account_name or "",
        recommendation_confidence=str(item.recommendation_confidence),
        recommendation_source=item.recommendation_source,
        assigned_reviewer=item.assigned_reviewer,
        priority=item.priority,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_detail(item: AccountingWorkItem) -> WorkItemDetail:
    """Map an AccountingWorkItem ORM object to a WorkItemDetail."""
    return WorkItemDetail(
        id=item.id,
        realm_id=item.realm_id,
        source_transaction_id=item.source_transaction_id,
        source_transaction_type=item.source_transaction_type or "",
        vendor_or_payee=item.vendor_or_payee or "",
        amount=str(item.amount),
        currency=item.currency,
        status=item.status,
        risk_level=item.risk_level,
        recommended_account_quickbooks_id=item.recommended_account_quickbooks_id or "",
        recommended_account_name=item.recommended_account_name or "",
        recommendation_confidence=str(item.recommendation_confidence),
        recommendation_source=item.recommendation_source,
        assigned_reviewer=item.assigned_reviewer,
        priority=item.priority,
        created_at=item.created_at,
        updated_at=item.updated_at,
        description=item.description or "",
        memo=item.memo or "",
        transaction_date=item.transaction_date,
        current_account_quickbooks_id=item.current_account_quickbooks_id,
        current_account_name=item.current_account_name,
        recommendation_explanation=item.recommendation_explanation or {},
        supporting_evidence=item.supporting_evidence or {},
        assigned_approver=item.assigned_approver,
        reviewed_at=item.reviewed_at,
        approved_at=item.approved_at,
        approved_by=item.approved_by,
        approved_account_quickbooks_id=item.approved_account_quickbooks_id,
        correction_reason=item.correction_reason,
        rejected_at=item.rejected_at,
        deferred_at=item.deferred_at,
        writeback_status=item.writeback_status,
        reconciliation_status=item.reconciliation_status,
        failure_code=item.failure_code,
        failure_details=item.failure_details,
        escalation_status=item.escalation_status,
        duplicate_classification=item.duplicate_classification,
        duplicate_of_id=item.duplicate_of_id,
        source_system=item.source_system,
        correlation_id=item.correlation_id,
        version=item.version,
    )


# ---------------------------------------------------------------------------
# Work Items
# ---------------------------------------------------------------------------


@router.get("/work-items", response_model=WorkItemListResponse)
async def list_work_items(
    realm_id: str = Query(),
    status: str = Query(default=""),
    risk_level: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_read)
    ] = Depends(require_accounting_read),
) -> WorkItemListResponse:
    """List work items for a realm."""
    require_realm_access(principal, realm_id)
    service = ReviewQueueService(db)
    items, total = await service.list_work_items(
        realm_id,
        status=status or None,
        risk_level=risk_level or None,
        limit=limit,
        offset=offset,
    )
    return WorkItemListResponse(
        items=[_to_summary(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/work-items/{work_item_id}", response_model=WorkItemDetail)
async def get_work_item(
    work_item_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_read)
    ] = Depends(require_accounting_read),
) -> WorkItemDetail:
    """Get a single work item by ID."""
    service = ReviewQueueService(db)
    item = await service.get_work_item(work_item_id)
    if item is None:
        raise HTTPException(404, "Work item not found.")
    return _to_detail(item)


@router.post("/work-items/{work_item_id}/claim", response_model=ClaimResponse)
async def claim_work_item(
    work_item_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_review)
    ] = Depends(require_accounting_review),
) -> ClaimResponse:
    """Claim a work item for review (NEEDS_REVIEW → IN_REVIEW)."""
    service = ReviewQueueService(db)
    try:
        item = await service.claim(
            __build_ctx(principal),
            work_item_id,
        )
        return ClaimResponse(
            work_item_id=item.id,
            status=item.status,
            assigned_reviewer=item.assigned_reviewer or "",
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/work-items/{work_item_id}/release", response_model=ReleaseResponse)
async def release_work_item(
    work_item_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_review)
    ] = Depends(require_accounting_review),
) -> ReleaseResponse:
    """Release a work item back to the queue (IN_REVIEW → NEEDS_REVIEW)."""
    service = ReviewQueueService(db)
    try:
        item = await service.release(
            __build_ctx(principal),
            work_item_id,
        )
        return ReleaseResponse(
            work_item_id=item.id,
            status=item.status,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post(
    "/work-items/{work_item_id}/correct",
    response_model=CorrectionResponse,
)
async def correct_recommendation(
    work_item_id: str,
    body: CorrectionRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_review)
    ] = Depends(require_accounting_review),
) -> CorrectionResponse:
    """Correct a work item recommendation."""
    require_realm_access(principal, body.realm_id)
    service = CorrectionService(db)
    try:
        correction = await service.record_correction(
            __build_ctx(principal),
            work_item_id,
            field_name=body.field_name,
            new_value=body.new_value,
            reason=body.reason,
        )
        return CorrectionResponse(
            correction_id=correction.id,
            work_item_id=work_item_id,
            field_name=correction.field_name,
            previous_value=correction.previous_value,
            new_value=correction.new_value,
            status="CORRECTED",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/work-items/{work_item_id}/approve", response_model=ApprovalResponse)
async def approve_work_item(
    work_item_id: str,
    body: ApprovalRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_approve)
    ] = Depends(require_accounting_approve),
) -> ApprovalResponse:
    """Approve a work item."""
    require_realm_access(principal, body.realm_id)
    service = ApprovalService(db)
    try:
        item = await service.approve(
            __build_ctx(principal),
            work_item_id,
            approved_account_quickbooks_id=body.approved_account_quickbooks_id,
            reason=body.reason,
        )
        return ApprovalResponse(
            work_item_id=item.id,
            status=item.status,
            approved_by=item.approved_by or "",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/work-items/{work_item_id}/reject", response_model=RejectionResponse)
async def reject_work_item(
    work_item_id: str,
    body: RejectionRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_review)
    ] = Depends(require_accounting_review),
) -> RejectionResponse:
    """Reject a work item."""
    service = ApprovalService(db)
    try:
        item = await service.reject(
            __build_ctx(principal),
            work_item_id,
            reason=body.reason,
        )
        return RejectionResponse(
            work_item_id=item.id,
            status=item.status,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/work-items/{work_item_id}/defer", response_model=DeferResponse)
async def defer_work_item(
    work_item_id: str,
    body: DeferRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_review)
    ] = Depends(require_accounting_review),
) -> DeferResponse:
    """Defer a work item for later review."""
    service = ApprovalService(db)
    try:
        item = await service.defer(
            __build_ctx(principal),
            work_item_id,
            reason=body.reason,
        )
        return DeferResponse(
            work_item_id=item.id,
            status=item.status,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post(
    "/work-items/{work_item_id}/escalate",
    response_model=EscalateResponse,
)
async def escalate_work_item(
    work_item_id: str,
    body: EscalateRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_review)
    ] = Depends(require_accounting_review),
) -> EscalateResponse:
    """Escalate a work item for human resolution."""
    service = EscalationService(db)
    try:
        escalation, item = await service.create_escalation(
            __build_ctx(principal),
            work_item_id,
            category=body.category,
            explanation=body.explanation,
            severity=body.severity,
            recommended_next_step=body.recommended_next_step,
        )
        return EscalateResponse(
            work_item_id=item.id,
            escalation_id=escalation.id,
            status=item.status,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


# ---------------------------------------------------------------------------
# Batch Operations
# ---------------------------------------------------------------------------


@router.post("/batch/approve", response_model=BatchApproveResponse)
async def batch_approve(
    body: BatchApproveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_approve)
    ] = Depends(require_accounting_approve),
) -> BatchApproveResponse:
    """Batch approve multiple work items."""
    require_realm_access(principal, body.realm_id)
    service = BatchService(db)
    batch, items = await service.batch_approve(
        __build_ctx(principal),
        body.realm_id,
        body.work_item_ids,
        reason=body.reason,
    )
    return BatchApproveResponse(
        batch_id=batch.id,
        operation_type=batch.operation_type,
        requested_count=batch.requested_count,
        successful_count=batch.successful_count,
        failed_count=batch.failed_count,
        skipped_count=batch.skipped_count,
        items=[
            BatchItemResult(
                work_item_id=bi.work_item_id,
                outcome=bi.outcome,
                error_message=bi.error_message or "",
            )
            for bi in items
        ],
    )


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


@router.get("/escalations", response_model=EscalationListResponse)
async def list_escalations(
    realm_id: str = Query(),
    resolution_status: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_read)
    ] = Depends(require_accounting_read),
) -> EscalationListResponse:
    """List escalations for a realm."""
    require_realm_access(principal, realm_id)
    service = EscalationService(db)
    items, total = await service.list_escalations(
        realm_id,
        resolution_status=resolution_status or None,
        limit=limit,
    )
    return EscalationListResponse(
        items=[
            EscalationSummary(
                id=e.id,
                work_item_id=e.work_item_id,
                realm_id=e.realm_id,
                category=e.category,
                severity=e.severity,
                explanation=e.explanation,
                resolution_status=e.resolution_status,
                assigned_owner=e.assigned_owner,
                due_date=e.due_date,
                created_at=e.created_at,
            )
            for e in items
        ],
        total=total,
        limit=limit,
    )


@router.post(
    "/escalations/{escalation_id}/resolve",
    response_model=EscalationResolveResponse,
)
async def resolve_escalation(
    escalation_id: str,
    body: EscalationResolveRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_approve)
    ] = Depends(require_accounting_approve),
) -> EscalationResolveResponse:
    """Resolve an escalation."""
    service = EscalationService(db)
    try:
        escalation = await service.resolve_escalation(
            __build_ctx(principal),
            escalation_id,
            resolution_note=body.resolution_note,
            action=body.action,
        )
        return EscalationResolveResponse(
            escalation_id=escalation.id,
            resolution_status=escalation.resolution_status,
            resolved_by=escalation.resolved_by or "",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


# ---------------------------------------------------------------------------
# Write-back Jobs
# ---------------------------------------------------------------------------


@router.get("/writeback-jobs", response_model=WriteBackJobListResponse)
async def list_writeback_jobs(
    realm_id: str = Query(),
    status: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_read)
    ] = Depends(require_accounting_read),
) -> WriteBackJobListResponse:
    """List write-back jobs for a realm."""
    require_realm_access(principal, realm_id)
    service = WriteBackQueryService(db)
    items, total = await service.list_writeback_jobs(
        realm_id,
        status=status or None,
        limit=limit,
    )
    return WriteBackJobListResponse(
        items=[
            WriteBackJobSummary(
                id=j.id,
                work_item_id=j.work_item_id,
                realm_id=j.realm_id,
                status=j.status,
                operation_type=j.operation_type,
                attempt_count=j.attempt_count,
                max_attempts=j.max_attempts,
                failure_category=j.failure_category,
                failure_message=j.failure_message,
                created_at=j.created_at,
                updated_at=j.updated_at,
            )
            for j in items
        ],
        total=total,
        limit=limit,
    )


@router.post(
    "/writeback-jobs/{job_id}/execute",
    response_model=WriteBackExecuteResponse,
)
async def execute_writeback_job(
    job_id: str,
    realm_id: str = Query(),
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_writeback)
    ] = Depends(require_accounting_writeback),
) -> WriteBackExecuteResponse:
    """Execute a write-back job (transition to IN_PROGRESS)."""
    require_realm_access(principal, realm_id)
    service = WriteBackQueryService(db)
    try:
        job = await service.execute_writeback_job(
            __build_ctx(principal),
            job_id,
        )
        return WriteBackExecuteResponse(
            job_id=job.id,
            work_item_id=job.work_item_id,
            status=job.status,
            attempt_count=job.attempt_count,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@router.get(
    "/reconciliation/{result_id}",
    response_model=ReconciliationResultResponse,
)
async def get_reconciliation_result(
    result_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Annotated[
        Principal, Depends(require_accounting_read)
    ] = Depends(require_accounting_read),
) -> ReconciliationResultResponse:
    """Get a reconciliation result by ID."""
    service = WriteBackQueryService(db)
    result = await service.get_reconciliation_result(result_id)
    if result is None:
        raise HTTPException(404, "Reconciliation result not found.")
    return ReconciliationResultResponse(
        id=result.id,
        job_id=result.job_id,
        work_item_id=result.work_item_id,
        realm_id=result.realm_id,
        status=result.status,
        approved_state=result.approved_state or {},
        observed_state=result.observed_state or {},
        differences=result.differences or [],
        external_transaction_id=result.external_transaction_id,
        external_sync_token=result.external_sync_token,
        reconciled_by=result.reconciled_by,
        notes=result.notes,
        created_at=result.created_at,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def __build_ctx(
    principal: Principal,
) -> ExecutionContext:
    """Build an ExecutionContext from the principal.

    The correlation_id is carried on the Principal from the JWT/auth layer.
    """
    return ExecutionContext(
        principal=principal,
        correlation_id=principal.correlation_id,
    )
