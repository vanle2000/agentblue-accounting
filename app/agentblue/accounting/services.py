"""Accounting review, approval, and batch services.

Application-layer services for the Stage 10 accounting workflow.
All state changes go through WorkflowTransitionService — these
services add business logic, validation, and audit recording.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import WorkItemStatus, WriteBackJobStatus
from agentblue.accounting.models import (
    AccountingWorkItem,
    BatchOperation,
    BatchOperationItem,
    Escalation,
    ReconciliationResult,
    WorkItemCorrection,
    WriteBackJob,
)
from agentblue.accounting.workflow import WorkflowTransitionService
from agentblue.security.audit import record_audit_event

if TYPE_CHECKING:
    from agentblue.security.context import ExecutionContext

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ReviewQueueService
# ---------------------------------------------------------------------------


class ReviewQueueService:
    """List, filter, paginate, claim, and release work items for review."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_work_items(
        self,
        realm_id: str,
        *,
        status: str | None = None,
        risk_level: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AccountingWorkItem], int]:
        """List work items for a realm with optional filters.

        Returns:
            Tuple of (items, total_count).
        """
        base = select(AccountingWorkItem).where(
            AccountingWorkItem.realm_id == realm_id,
        )
        count_q = select(func.count()).select_from(AccountingWorkItem).where(
            AccountingWorkItem.realm_id == realm_id,
        )

        if status:
            base = base.where(AccountingWorkItem.status == status)
            count_q = count_q.where(AccountingWorkItem.status == status)
        if risk_level:
            base = base.where(AccountingWorkItem.risk_level == risk_level)
            count_q = count_q.where(AccountingWorkItem.risk_level == risk_level)

        total_result = await self._session.execute(count_q)
        total = total_result.scalar_one()

        items_result = await self._session.execute(
            base.order_by(
                AccountingWorkItem.priority.desc(),
                AccountingWorkItem.created_at.asc(),
            )
            .offset(offset)
            .limit(limit),
        )
        return list(items_result.scalars().all()), total

    async def get_work_item(self, work_item_id: str) -> AccountingWorkItem | None:
        """Fetch a single work item by ID."""
        result = await self._session.execute(
            select(AccountingWorkItem).where(AccountingWorkItem.id == work_item_id),
        )
        return result.scalar_one_or_none()

    async def claim(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
    ) -> AccountingWorkItem:
        """Claim a work item for review (NEEDS_REVIEW → IN_REVIEW)."""
        workflow = WorkflowTransitionService(self._session)
        item = await workflow.transition_work_item(
            ctx,
            work_item_id,
            WorkItemStatus.IN_REVIEW,
            reason="Claimed for review",
        )

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.claim",
            resource_type="accounting_work_item",
            resource_id=work_item_id,
            realm_id=item.realm_id,
        )

        return item

    async def release(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
    ) -> AccountingWorkItem:
        """Release a work item back to the queue (IN_REVIEW → NEEDS_REVIEW)."""
        workflow = WorkflowTransitionService(self._session)
        item = await workflow.transition_work_item(
            ctx,
            work_item_id,
            WorkItemStatus.NEEDS_REVIEW,
            reason="Released from review",
        )

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.release",
            resource_type="accounting_work_item",
            resource_id=work_item_id,
            realm_id=item.realm_id,
        )

        return item


# ---------------------------------------------------------------------------
# ApprovalService
# ---------------------------------------------------------------------------


class ApprovalService:
    """Approve, reject, and defer work items with state-machine validation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def approve(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
        *,
        approved_account_quickbooks_id: str = "",
        reason: str = "",
    ) -> AccountingWorkItem:
        """Approve a work item.

        Valid from: NEEDS_REVIEW, IN_REVIEW, CORRECTED.
        Target: APPROVED.
        """
        workflow = WorkflowTransitionService(self._session)

        # Fetch current item to optionally update approved_account
        item_result = await self._session.execute(
            select(AccountingWorkItem).where(AccountingWorkItem.id == work_item_id),
        )
        item = item_result.scalar_one_or_none()
        if item is None:
            raise ValueError(f"Work item not found: {work_item_id}")

        # Set the approved account before transition
        if approved_account_quickbooks_id:
            item.approved_account_quickbooks_id = approved_account_quickbooks_id

        updated = await workflow.transition_work_item(
            ctx,
            work_item_id,
            WorkItemStatus.APPROVED,
            reason=reason or "Approved",
            metadata={
                "approved_account_quickbooks_id": approved_account_quickbooks_id,
            },
        )

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.approve",
            resource_type="accounting_work_item",
            resource_id=work_item_id,
            realm_id=updated.realm_id,
            metadata={
                "approved_account_quickbooks_id": approved_account_quickbooks_id,
                "reason": reason,
            },
        )

        return updated

    async def reject(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
        *,
        reason: str = "",
    ) -> AccountingWorkItem:
        """Reject a work item.

        Valid from: NEEDS_REVIEW, IN_REVIEW, CORRECTED.
        Target: REJECTED.
        """
        workflow = WorkflowTransitionService(self._session)
        item = await workflow.transition_work_item(
            ctx,
            work_item_id,
            WorkItemStatus.REJECTED,
            reason=reason or "Rejected",
        )

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.reject",
            resource_type="accounting_work_item",
            resource_id=work_item_id,
            realm_id=item.realm_id,
            metadata={"reason": reason},
        )

        return item

    async def defer(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
        *,
        reason: str = "",
    ) -> AccountingWorkItem:
        """Defer a work item for later review.

        Valid from: NEEDS_REVIEW, IN_REVIEW, CORRECTED.
        Target: DEFERRED.
        """
        workflow = WorkflowTransitionService(self._session)
        item = await workflow.transition_work_item(
            ctx,
            work_item_id,
            WorkItemStatus.DEFERRED,
            reason=reason or "Deferred",
        )

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.defer",
            resource_type="accounting_work_item",
            resource_id=work_item_id,
            realm_id=item.realm_id,
            metadata={"reason": reason},
        )

        return item


# ---------------------------------------------------------------------------
# CorrectionService
# ---------------------------------------------------------------------------


class CorrectionService:
    """Record corrections to recommendations and validate accounts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_correction(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
        *,
        field_name: str,
        new_value: str,
        reason: str,
    ) -> WorkItemCorrection:
        """Record a correction to a work item's recommendation.

        Transitions IN_REVIEW → CORRECTED after recording.
        """
        # Fetch work item
        result = await self._session.execute(
            select(AccountingWorkItem).where(AccountingWorkItem.id == work_item_id),
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise ValueError(f"Work item not found: {work_item_id}")

        ctx.require_realm(item.realm_id)

        # Record previous value for the corrected field
        previous_value = getattr(item, field_name, None)
        if previous_value is not None:
            previous_value = str(previous_value)

        # Create correction record
        correction = WorkItemCorrection(
            work_item_id=work_item_id,
            realm_id=item.realm_id,
            field_name=field_name,
            previous_value=previous_value,
            new_value=new_value,
            reason=reason,
            corrected_by=ctx.principal.principal_id,
        )
        self._session.add(correction)
        await self._session.flush()

        # Apply correction to the work item
        if hasattr(item, field_name):
            setattr(item, field_name, new_value)
        item.correction_reason = reason

        # Transition to CORRECTED if in a reviewable state
        current = WorkItemStatus(item.status)
        if current in (WorkItemStatus.IN_REVIEW, WorkItemStatus.NEEDS_REVIEW):
            workflow = WorkflowTransitionService(self._session)
            await workflow.transition_work_item(
                ctx,
                work_item_id,
                WorkItemStatus.CORRECTED,
                reason=f"Corrected {field_name}: {reason}",
                metadata={
                    "field_name": field_name,
                    "previous_value": previous_value,
                    "new_value": new_value,
                },
            )

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.correct",
            resource_type="accounting_work_item",
            resource_id=work_item_id,
            realm_id=item.realm_id,
            metadata={
                "field_name": field_name,
                "reason": reason,
            },
        )

        logger.info(
            "work_item_corrected",
            work_item_id=work_item_id,
            field_name=field_name,
            corrected_by=ctx.principal.principal_id,
        )

        return correction


# ---------------------------------------------------------------------------
# BatchService
# ---------------------------------------------------------------------------


class BatchService:
    """Batch approve/reject with per-item validation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def batch_approve(
        self,
        ctx: ExecutionContext,
        realm_id: str,
        work_item_ids: list[str],
        *,
        reason: str = "",
    ) -> tuple[BatchOperation, list[BatchOperationItem]]:
        """Batch approve multiple work items.

        Each item is validated individually. Items that cannot be
        transitioned are marked SKIPPED with an error message.

        Returns:
            Tuple of (batch_operation, list_of_item_results).
        """
        # Create the batch operation record
        batch = BatchOperation(
            realm_id=realm_id,
            operation_type="APPROVE",
            requested_count=len(work_item_ids),
            actor_principal_id=ctx.principal.principal_id,
            correlation_id=ctx.correlation_id,
        )
        self._session.add(batch)
        await self._session.flush()

        approval_service = ApprovalService(self._session)
        items: list[BatchOperationItem] = []
        successful = 0
        failed = 0
        skipped = 0

        for work_item_id in work_item_ids:
            batch_item = BatchOperationItem(
                batch_id=batch.id,
                work_item_id=work_item_id,
                outcome="PENDING",
            )
            self._session.add(batch_item)
            await self._session.flush()

            try:
                await approval_service.approve(
                    ctx,
                    work_item_id,
                    reason=reason or "Batch approval",
                )
                batch_item.outcome = "SUCCESS"
                successful += 1
            except ValueError as exc:
                batch_item.outcome = "SKIPPED"
                batch_item.error_message = str(exc)[:500]
                skipped += 1
                logger.warning(
                    "batch_approve_item_skipped",
                    batch_id=batch.id,
                    work_item_id=work_item_id,
                    error=str(exc)[:200],
                )
            except PermissionError as exc:
                batch_item.outcome = "FAILED"
                batch_item.error_message = str(exc)[:500]
                failed += 1
                logger.warning(
                    "batch_approve_item_failed",
                    batch_id=batch.id,
                    work_item_id=work_item_id,
                    error=str(exc)[:200],
                )
            except Exception as exc:
                batch_item.outcome = "FAILED"
                batch_item.error_message = str(exc)[:500]
                failed += 1
                logger.warning(
                    "batch_approve_item_error",
                    batch_id=batch.id,
                    work_item_id=work_item_id,
                    error=str(exc)[:200],
                )

            items.append(batch_item)

        # Update batch summary
        batch.eligible_count = successful + failed
        batch.successful_count = successful
        batch.failed_count = failed
        batch.skipped_count = skipped
        batch.status = "COMPLETED"
        await self._session.flush()

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.batch_approve",
            resource_type="batch_operation",
            resource_id=batch.id,
            realm_id=realm_id,
            metadata={
                "requested_count": len(work_item_ids),
                "successful_count": successful,
                "failed_count": failed,
                "skipped_count": skipped,
            },
        )

        return batch, items


# ---------------------------------------------------------------------------
# EscalationService
# ---------------------------------------------------------------------------


class EscalationService:
    """Create, list, and resolve escalations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_escalation(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
        *,
        category: str,
        explanation: str,
        severity: str = "MEDIUM",
        recommended_next_step: str = "",
    ) -> tuple[Escalation, AccountingWorkItem]:
        """Create an escalation and transition the work item to ESCALATED.

        Returns:
            Tuple of (escalation, updated_work_item).
        """
        # Fetch work item
        result = await self._session.execute(
            select(AccountingWorkItem).where(AccountingWorkItem.id == work_item_id),
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise ValueError(f"Work item not found: {work_item_id}")

        ctx.require_realm(item.realm_id)

        # Create escalation record
        escalation = Escalation(
            work_item_id=work_item_id,
            realm_id=item.realm_id,
            category=category,
            severity=severity,
            explanation=explanation,
            recommended_next_step=recommended_next_step or None,
            correlation_id=ctx.correlation_id,
        )
        self._session.add(escalation)
        await self._session.flush()

        # Transition work item to ESCALATED
        workflow = WorkflowTransitionService(self._session)
        updated = await workflow.transition_work_item(
            ctx,
            work_item_id,
            WorkItemStatus.ESCALATED,
            reason=f"Escalated: {category} - {explanation[:200]}",
            metadata={
                "escalation_id": escalation.id,
                "category": category,
                "severity": severity,
            },
        )

        # Update escalation status on the work item
        updated.escalation_status = "OPEN"

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="work_item.escalate",
            resource_type="escalation",
            resource_id=escalation.id,
            realm_id=item.realm_id,
            metadata={
                "work_item_id": work_item_id,
                "category": category,
                "severity": severity,
            },
        )

        return escalation, updated

    async def list_escalations(
        self,
        realm_id: str,
        *,
        resolution_status: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Escalation], int]:
        """List escalations for a realm.

        Returns:
            Tuple of (escalations, total_count).
        """
        base = select(Escalation).where(Escalation.realm_id == realm_id)
        count_q = (
            select(func.count())
            .select_from(Escalation)
            .where(Escalation.realm_id == realm_id)
        )

        if resolution_status:
            base = base.where(Escalation.resolution_status == resolution_status)
            count_q = count_q.where(
                Escalation.resolution_status == resolution_status,
            )

        total_result = await self._session.execute(count_q)
        total = total_result.scalar_one()

        items_result = await self._session.execute(
            base.order_by(Escalation.created_at.desc()).limit(limit),
        )
        return list(items_result.scalars().all()), total

    async def get_escalation(self, escalation_id: str) -> Escalation | None:
        """Fetch a single escalation by ID."""
        result = await self._session.execute(
            select(Escalation).where(Escalation.id == escalation_id),
        )
        return result.scalar_one_or_none()

    async def resolve_escalation(
        self,
        ctx: ExecutionContext,
        escalation_id: str,
        *,
        resolution_note: str,
        action: str = "RESOLVED",
    ) -> Escalation:
        """Resolve an escalation and optionally reopen the work item.

        Args:
            ctx: Authenticated execution context.
            escalation_id: ID of the escalation to resolve.
            resolution_note: Note explaining the resolution.
            action: RESOLVED or REOPENED.

        Returns:
            The updated escalation.
        """
        from datetime import UTC, datetime

        result = await self._session.execute(
            select(Escalation).where(Escalation.id == escalation_id),
        )
        escalation = result.scalar_one_or_none()
        if escalation is None:
            raise ValueError(f"Escalation not found: {escalation_id}")

        if escalation.resolution_status != "OPEN":
            raise ValueError(
                f"Escalation is not open: current status is {escalation.resolution_status}"
            )

        escalation.resolution_status = action
        escalation.resolution_note = resolution_note
        escalation.resolved_by = ctx.principal.principal_id
        escalation.resolved_at = datetime.now(UTC)

        # If reopened, transition work item back to NEEDS_REVIEW
        if action == "REOPENED":
            workflow = WorkflowTransitionService(self._session)
            work_item = await workflow.transition_work_item(
                ctx,
                escalation.work_item_id,
                WorkItemStatus.NEEDS_REVIEW,
                reason=f"Escalation reopened: {resolution_note[:200]}",
            )
            work_item.escalation_status = "REOPENED"

        await self._session.flush()

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action=f"escalation.{action.lower()}",
            resource_type="escalation",
            resource_id=escalation_id,
            realm_id=escalation.realm_id,
            metadata={
                "work_item_id": escalation.work_item_id,
                "resolution_note": resolution_note,
            },
        )

        return escalation


# ---------------------------------------------------------------------------
# WriteBackQueryService
# ---------------------------------------------------------------------------


class WriteBackQueryService:
    """Query write-back jobs and reconciliation results."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_writeback_jobs(
        self,
        realm_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[list[WriteBackJob], int]:
        """List write-back jobs for a realm.

        Returns:
            Tuple of (jobs, total_count).
        """
        base = select(WriteBackJob).where(WriteBackJob.realm_id == realm_id)
        count_q = (
            select(func.count())
            .select_from(WriteBackJob)
            .where(WriteBackJob.realm_id == realm_id)
        )

        if status:
            base = base.where(WriteBackJob.status == status)
            count_q = count_q.where(WriteBackJob.status == status)

        total_result = await self._session.execute(count_q)
        total = total_result.scalar_one()

        items_result = await self._session.execute(
            base.order_by(WriteBackJob.created_at.desc()).limit(limit),
        )
        return list(items_result.scalars().all()), total

    async def get_writeback_job(self, job_id: str) -> WriteBackJob | None:
        """Fetch a single write-back job by ID."""
        result = await self._session.execute(
            select(WriteBackJob).where(WriteBackJob.id == job_id),
        )
        return result.scalar_one_or_none()

    async def get_reconciliation_result(
        self,
        result_id: str,
    ) -> ReconciliationResult | None:
        """Fetch a reconciliation result by ID."""
        result = await self._session.execute(
            select(ReconciliationResult).where(ReconciliationResult.id == result_id),
        )
        return result.scalar_one_or_none()

    async def execute_writeback_job(
        self,
        ctx: ExecutionContext,
        job_id: str,
    ) -> WriteBackJob:
        """Transition a write-back job to IN_PROGRESS.

        The actual write-back execution is handled by the QuickBooks
        integration layer. This service transitions the job state
        and records the audit event.
        """
        workflow = WorkflowTransitionService(self._session)
        job = await workflow.transition_writeback_job(
            ctx,
            job_id,
            WriteBackJobStatus.IN_PROGRESS,
        )

        await record_audit_event(
            self._session,
            principal=ctx.principal,
            action="writeback_job.execute",
            resource_type="write_back_job",
            resource_id=job_id,
            realm_id=job.realm_id,
        )

        return job
