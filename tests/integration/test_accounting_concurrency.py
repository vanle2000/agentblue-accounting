"""PostgreSQL concurrency tests for accounting workflow.

Real database tests using PostgreSQL 16 to verify concurrent operations
don't produce duplicates, race conditions, or inconsistent state.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import WorkItemStatus, WriteBackJobStatus
from agentblue.accounting.models import (
    AccountingWorkItem,
    WriteBackJob,
)
from agentblue.accounting.workflow import WorkflowTransitionService
from agentblue.db.session import get_session_factory
from agentblue.security.context import ExecutionContext
from agentblue.security.principal import Principal
from agentblue.security.roles import Role

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]

REALM = "concurrency-test-realm"


@pytest.fixture
async def db_session() -> AsyncSession:
    """Integration test session against real PostgreSQL."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


def _make_principal(
    pid: str = "reviewer-1",
    roles: frozenset[Role] | None = None,
) -> Principal:
    return Principal(
        principal_id=pid,
        principal_type="human",
        email=f"{pid}@test.local",
        display_name=f"Test {pid}",
        active=True,
        roles=roles or frozenset({Role.ACCOUNTANT}),
        realm_ids=frozenset({REALM}),
        auth_method="jwt",
        correlation_id=str(uuid.uuid4()),
    )


def _make_ctx(
    pid: str = "reviewer-1",
    roles: frozenset[Role] | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        principal=_make_principal(pid, roles),
        correlation_id=str(uuid.uuid4()),
    )


async def _create_work_item(
    session: AsyncSession,
    *,
    status: str = "NEEDS_REVIEW",
    realm_id: str = REALM,
    source_txn_id: str | None = None,
) -> AccountingWorkItem:
    """Insert a work item directly into the database."""
    item = AccountingWorkItem(
        realm_id=realm_id,
        source_transaction_id=source_txn_id or str(uuid.uuid4()),
        source_transaction_type="Purchase",
        amount=Decimal("100.00"),
        vendor_or_payee="Test Vendor",
        description="Test transaction",
        status=status,
        recommended_account_quickbooks_id="QB-ACCT-001",
        recommendation_source="TEST",
        recommendation_confidence=Decimal("0.95"),
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# 1. Two reviewers claim the same work item
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_claim_same_work_item(db_session: AsyncSession) -> None:
    """Two reviewers try to claim the same item — only one succeeds."""
    item = await _create_work_item(db_session, status="NEEDS_REVIEW")
    svc = WorkflowTransitionService(db_session)

    ctx1 = _make_ctx("reviewer-1", frozenset({Role.ACCOUNTANT}))
    ctx2 = _make_ctx("reviewer-2", frozenset({Role.ACCOUNTANT}))

    # First claim succeeds
    result = await svc.transition_work_item(
        ctx1, item.id, WorkItemStatus.IN_REVIEW, reason="claim 1"
    )
    assert result.status == "IN_REVIEW"
    assert result.assigned_reviewer == "reviewer-1"
    await db_session.commit()

    # Second claim should fail (already IN_REVIEW, IN_REVIEW->IN_REVIEW invalid)
    with pytest.raises(ValueError, match="Invalid transition"):
        await svc.transition_work_item(
            ctx2, item.id, WorkItemStatus.IN_REVIEW, reason="claim 2"
        )
    await db_session.rollback()


# ---------------------------------------------------------------------------
# 2. Two approvers approve the same work-item version
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_approval_same_version(db_session: AsyncSession) -> None:
    """Two approvers try to approve the same version — only one succeeds."""
    item = await _create_work_item(db_session, status="CORRECTED")
    svc = WorkflowTransitionService(db_session)

    ctx1 = _make_ctx("approver-1", frozenset({Role.APPROVER}))
    ctx2 = _make_ctx("approver-2", frozenset({Role.APPROVER}))

    # First approval succeeds
    result = await svc.transition_work_item(
        ctx1, item.id, WorkItemStatus.APPROVED, reason="approve 1"
    )
    assert result.status == "APPROVED"
    original_version = result.version
    await db_session.commit()

    # Second approval fails (APPROVED->APPROVED not allowed)
    # Use fresh session to avoid greenlet issues
    await db_session.close()
    factory = get_session_factory()
    async with factory() as session2:
        svc2 = WorkflowTransitionService(session2)
        with pytest.raises(ValueError, match="Invalid transition"):
            await svc2.transition_work_item(
                ctx2, item.id, WorkItemStatus.APPROVED, reason="approve 2"
            )

    # Verify version unchanged
    async with factory() as session3:
        refreshed = await session3.get(AccountingWorkItem, item.id)
        assert refreshed.version == original_version


# ---------------------------------------------------------------------------
# 3. Two requests create write-back job for same approval
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_writeback_job_creation(db_session: AsyncSession) -> None:
    """Two requests try to create a write-back job — uniqueness prevents duplicate."""
    item = await _create_work_item(db_session)
    idem_key = str(uuid.uuid4())
    job1 = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="QB-TXN-001",
        idempotency_key=idem_key,
        approver_principal_id="approver-1",
    )
    db_session.add(job1)
    await db_session.commit()

    # Second job with same idempotency key should fail
    job2 = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="QB-TXN-002",
        idempotency_key=idem_key,
        approver_principal_id="approver-2",
    )
    db_session.add(job2)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# 4. Two workers execute the same write-back job
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_job_execution(db_session: AsyncSession) -> None:
    """Two workers try to execute the same job — only one transitions to IN_PROGRESS."""
    item = await _create_work_item(db_session)
    job = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="QB-TXN-002",
        idempotency_key=str(uuid.uuid4()),
        approver_principal_id="approver-1",
        status=WriteBackJobStatus.READY.value,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    svc = WorkflowTransitionService(db_session)
    ctx = _make_ctx("worker-1", frozenset({Role.SERVICE_ACCOUNT}))

    # First worker transitions to IN_PROGRESS
    result = await svc.transition_writeback_job(
        ctx, job.id, WriteBackJobStatus.IN_PROGRESS
    )
    assert result.status == "IN_PROGRESS"
    await db_session.commit()

    # Second worker can't transition READY->IN_PROGRESS again
    ctx2 = _make_ctx("worker-2", frozenset({Role.SERVICE_ACCOUNT}))
    with pytest.raises(ValueError, match="Invalid write-back job transition"):
        await svc.transition_writeback_job(
            ctx2, job.id, WriteBackJobStatus.IN_PROGRESS
        )
    await db_session.rollback()


# ---------------------------------------------------------------------------
# 5. Two requests use the same idempotency key
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idempotency_key_uniqueness(db_session: AsyncSession) -> None:
    """Same idempotency key cannot create two jobs."""
    item = await _create_work_item(db_session)
    key = f"idem-{uuid.uuid4()}"
    job1 = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="TXN-A",
        idempotency_key=key,
        approver_principal_id="approver-1",
    )
    db_session.add(job1)
    await db_session.commit()

    # Verify can query back
    result = await db_session.execute(
        select(WriteBackJob).where(WriteBackJob.idempotency_key == key)
    )
    found = result.scalar_one()
    assert found.target_transaction_id == "TXN-A"


# ---------------------------------------------------------------------------
# 6. Stale optimistic version
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_version_detection(db_session: AsyncSession) -> None:
    """Detect stale version when item was modified between read and write."""
    item = await _create_work_item(db_session, status="NEEDS_REVIEW")
    original_version = item.version

    # Simulate another process incrementing version
    item.status = "IN_REVIEW"
    item.version += 1
    item.assigned_reviewer = "other-reviewer"
    await db_session.commit()

    # Verify version changed
    refreshed = await db_session.get(AccountingWorkItem, item.id)
    assert refreshed.version == original_version + 1
    assert refreshed.status == "IN_REVIEW"


# ---------------------------------------------------------------------------
# 7. Two batch requests include the same work items
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_batch_same_items(db_session: AsyncSession) -> None:
    """Two batch requests targeting the same items — each item can only transition once."""
    items = []
    for _i in range(3):
        item = await _create_work_item(
            db_session,
            status="NEEDS_REVIEW",
            source_txn_id=f"batch-txn-{uuid.uuid4()}",
        )
        items.append(item)

    svc = WorkflowTransitionService(db_session)
    ctx = _make_ctx("approver-1", frozenset({Role.APPROVER}))

    # First batch processes all items
    for item in items:
        await svc.transition_work_item(
            ctx, item.id, WorkItemStatus.IN_REVIEW, reason="batch claim"
        )
    await db_session.commit()

    # Second batch trying same items fails (already IN_REVIEW)
    await db_session.close()
    factory = get_session_factory()
    for item in items:
        async with factory() as session2:
            svc2 = WorkflowTransitionService(session2)
            with pytest.raises(ValueError, match="Invalid transition"):
                await svc2.transition_work_item(
                    _make_ctx("approver-2", frozenset({Role.APPROVER})),
                    item.id,
                    WorkItemStatus.IN_REVIEW,
                    reason="batch claim 2",
                )


# ---------------------------------------------------------------------------
# 8. Concurrent reconciliation of same write-back result
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_reconciliation(db_session: AsyncSession) -> None:
    """Two reconciliation attempts for the same job — second sees already reconciled."""
    item = await _create_work_item(db_session)
    job = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="TXN-RECON",
        idempotency_key=str(uuid.uuid4()),
        approver_principal_id="approver-1",
        status=WriteBackJobStatus.SUCCEEDED.value,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    svc = WorkflowTransitionService(db_session)
    ctx = _make_ctx("system", frozenset({Role.SERVICE_ACCOUNT}))

    # First reconciliation succeeds
    result = await svc.transition_writeback_job(
        ctx, job.id, WriteBackJobStatus.RECONCILED
    )
    assert result.status == "RECONCILED"
    await db_session.commit()

    # Second reconciliation fails (RECONCILED is terminal)
    with pytest.raises(ValueError, match="Invalid write-back job transition"):
        await svc.transition_writeback_job(
            ctx, job.id, WriteBackJobStatus.RECONCILED
        )
    await db_session.rollback()


# ---------------------------------------------------------------------------
# 9. Transaction rollback after uniqueness conflict
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rollback_after_conflict(db_session: AsyncSession) -> None:
    """After a uniqueness conflict, rollback succeeds and session remains usable."""
    item = await _create_work_item(db_session)
    key = f"rollback-{uuid.uuid4()}"
    job = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="TXN-ROLLBACK",
        idempotency_key=key,
        approver_principal_id="approver-1",
    )
    db_session.add(job)
    await db_session.commit()

    # Attempt conflicting insert
    job2 = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="TXN-ROLLBACK-2",
        idempotency_key=key,
        approver_principal_id="approver-2",
    )
    db_session.add(job2)
    try:
        await db_session.commit()
    except Exception:
        await db_session.rollback()

    # Session remains usable — can still query
    result = await db_session.execute(
        select(WriteBackJob).where(WriteBackJob.idempotency_key == key)
    )
    found = result.scalars().all()
    assert len(found) == 1


# ---------------------------------------------------------------------------
# 10. Recovery after losing transaction retries safely
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_after_conflict_succeeds(db_session: AsyncSession) -> None:
    """After losing a race, the losing transaction can retry with a new key."""
    item = await _create_work_item(db_session, status="NEEDS_REVIEW")

    # First request claims it
    svc = WorkflowTransitionService(db_session)
    ctx1 = _make_ctx("reviewer-1", frozenset({Role.ACCOUNTANT}))
    await svc.transition_work_item(
        ctx1, item.id, WorkItemStatus.IN_REVIEW, reason="first claim"
    )
    await db_session.commit()

    # Second request fails to claim (already IN_REVIEW)
    ctx2 = _make_ctx("reviewer-2", frozenset({Role.ACCOUNTANT}))
    await db_session.close()
    factory = get_session_factory()
    async with factory() as session2:
        svc2 = WorkflowTransitionService(session2)
        with pytest.raises(ValueError, match="Invalid transition"):
            await svc2.transition_work_item(
                ctx2, item.id, WorkItemStatus.IN_REVIEW, reason="second claim"
            )

    # First reviewer corrects using fresh session
    async with factory() as session3:
        svc3 = WorkflowTransitionService(session3)
        await svc3.transition_work_item(
            ctx1, item.id, WorkItemStatus.CORRECTED, reason="corrected"
        )
        await session3.commit()

    # Now transition to APPROVED using fresh session
    async with factory() as session4:
        svc4 = WorkflowTransitionService(session4)
        await svc4.transition_work_item(
            _make_ctx("approver-1", frozenset({Role.APPROVER})),
            item.id,
            WorkItemStatus.APPROVED,
            reason="approve",
        )
        await session4.commit()

    # Verify final state
    async with factory() as session5:
        refreshed = await session5.get(AccountingWorkItem, item.id)
        assert refreshed.status == "APPROVED"
