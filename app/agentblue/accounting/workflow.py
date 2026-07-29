"""Centralized workflow state transition service.

All state changes go through this service. No raw status
assignments allowed in routers or repositories.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import (
    WorkItemStatus,
    WriteBackJobStatus,
    validate_work_item_transition,
    validate_writeback_job_transition,
)
from agentblue.accounting.models import (
    AccountingWorkItem,
    WorkItemTransition,
    WriteBackJob,
)
from agentblue.security.roles import Permission

if TYPE_CHECKING:
    from agentblue.security.context import ExecutionContext

logger = structlog.get_logger(__name__)

# Transition → required permission
_TRANSITION_PERMISSIONS: dict[tuple[str, str], Permission] = {
    ("NEEDS_REVIEW", "IN_REVIEW"): Permission.ACCOUNTING_REVIEW,
    ("NEEDS_REVIEW", "APPROVED"): Permission.ACCOUNTING_APPROVE,
    ("NEEDS_REVIEW", "REJECTED"): Permission.ACCOUNTING_REVIEW,
    ("NEEDS_REVIEW", "DEFERRED"): Permission.ACCOUNTING_REVIEW,
    ("IN_REVIEW", "CORRECTED"): Permission.ACCOUNTING_REVIEW,
    ("IN_REVIEW", "APPROVED"): Permission.ACCOUNTING_APPROVE,
    ("IN_REVIEW", "REJECTED"): Permission.ACCOUNTING_REVIEW,
    ("IN_REVIEW", "DEFERRED"): Permission.ACCOUNTING_REVIEW,
    ("CORRECTED", "APPROVED"): Permission.ACCOUNTING_APPROVE,
    ("CORRECTED", "REJECTED"): Permission.ACCOUNTING_REVIEW,
    ("CORRECTED", "DEFERRED"): Permission.ACCOUNTING_REVIEW,
    ("APPROVED", "READY_FOR_WRITEBACK"): Permission.ACCOUNTING_WRITEBACK,
    ("WRITEBACK_FAILED", "READY_FOR_WRITEBACK"): Permission.ACCOUNTING_WRITEBACK,
    ("DEFERRED", "NEEDS_REVIEW"): Permission.ACCOUNTING_REVIEW,
    ("ESCALATED", "NEEDS_REVIEW"): Permission.ACCOUNTING_REVIEW,
}


class WorkflowTransitionService:
    """Centralized service for all workflow state transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def transition_work_item(
        self,
        ctx: ExecutionContext,
        work_item_id: str,
        target_status: WorkItemStatus,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AccountingWorkItem:
        """Transition a work item to a new status.

        Validates:
        - work item exists and belongs to the principal's realm
        - transition is valid per the state machine
        - principal has the required permission
        - realm access is authorized

        Args:
            ctx: Authenticated execution context.
            work_item_id: ID of the work item.
            target_status: Desired new status.
            reason: Human-readable reason for the transition.
            metadata: Additional metadata to record.

        Returns:
            The updated work item.

        Raises:
            ValueError: If the transition is invalid.
            PermissionError: If the principal lacks permission.
        """
        # Fetch work item with row lock to prevent concurrent state changes
        result = await self._session.execute(
            select(AccountingWorkItem)
            .where(AccountingWorkItem.id == work_item_id)
            .with_for_update()
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise ValueError(f"Work item not found: {work_item_id}")

        # Realm check
        ctx.require_realm(item.realm_id)

        # State machine validation
        current = WorkItemStatus(item.status)
        validate_work_item_transition(current, target_status)

        # Permission check
        perm_key = (current.value, target_status.value)
        required_perm = _TRANSITION_PERMISSIONS.get(perm_key)
        if required_perm is not None:
            ctx.require_permission(required_perm)

        # Record transition
        transition = WorkItemTransition(
            work_item_id=item.id,
            realm_id=item.realm_id,
            from_status=current.value,
            to_status=target_status.value,
            actor_principal_id=ctx.principal.principal_id,
            actor_roles=[r.value for r in ctx.principal.roles],
            reason=reason,
            metadata_snapshot=metadata or {},
            correlation_id=ctx.correlation_id,
        )
        self._session.add(transition)

        # Apply timestamp updates
        now = datetime.now(UTC)
        if target_status == WorkItemStatus.APPROVED:
            item.approved_at = now
            item.approved_by = ctx.principal.principal_id
        elif target_status == WorkItemStatus.REJECTED:
            item.rejected_at = now
        elif target_status == WorkItemStatus.DEFERRED:
            item.deferred_at = now
        elif target_status == WorkItemStatus.IN_REVIEW:
            item.assigned_reviewer = ctx.principal.principal_id
        elif target_status == WorkItemStatus.READY_FOR_WRITEBACK:
            item.writeback_status = "PENDING"
        elif target_status == WorkItemStatus.WRITTEN:
            item.writeback_status = "COMPLETED"
        elif target_status == WorkItemStatus.WRITEBACK_FAILED:
            item.writeback_status = "FAILED"
        elif target_status == WorkItemStatus.RECONCILED:
            item.reconciliation_status = "MATCHED"
        elif target_status == WorkItemStatus.RECONCILIATION_FAILED:
            item.reconciliation_status = "MISMATCH"

        item.status = target_status.value
        item.version += 1
        item.updated_at = now

        await self._session.flush()

        logger.info(
            "work_item_transition",
            work_item_id=item.id,
            from_status=current.value,
            to_status=target_status.value,
            actor=ctx.principal.principal_id,
            realm_id=item.realm_id,
            correlation_id=ctx.correlation_id,
        )

        return item

    async def transition_writeback_job(
        self,
        ctx: ExecutionContext,
        job_id: str,
        target_status: WriteBackJobStatus,
        *,
        failure_category: str = "",
        failure_message: str = "",
    ) -> WriteBackJob:
        """Transition a write-back job to a new status.

        Args:
            ctx: Authenticated execution context.
            job_id: ID of the write-back job.
            target_status: Desired new status.
            failure_category: Category if failing.
            failure_message: Safe failure message.

        Returns:
            The updated job.
        """
        result = await self._session.execute(
            select(WriteBackJob).where(WriteBackJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Write-back job not found: {job_id}")

        ctx.require_realm(job.realm_id)

        current = WriteBackJobStatus(job.status)
        validate_writeback_job_transition(current, target_status)

        now = datetime.now(UTC)

        if target_status == WriteBackJobStatus.IN_PROGRESS:
            job.started_at = now
            job.attempt_count += 1
            job.execution_principal_id = ctx.principal.principal_id
        elif target_status == WriteBackJobStatus.SUCCEEDED:
            job.completed_at = now
        elif target_status in (
            WriteBackJobStatus.FAILED_RETRYABLE,
            WriteBackJobStatus.FAILED_PERMANENT,
        ):
            job.failure_category = failure_category
            job.failure_message = failure_message[:500]
            if target_status == WriteBackJobStatus.FAILED_PERMANENT:
                job.completed_at = now

        job.status = target_status.value
        job.version += 1
        job.updated_at = now

        await self._session.flush()

        logger.info(
            "writeback_job_transition",
            job_id=job.id,
            from_status=current.value,
            to_status=target_status.value,
            attempt=job.attempt_count,
            actor=ctx.principal.principal_id,
        )

        return job
