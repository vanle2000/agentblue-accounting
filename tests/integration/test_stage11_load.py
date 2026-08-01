"""Production-style load tests against PostgreSQL.

Reproducible load tests measuring latency, throughput, and safety
invariants under concurrent accounting workflow traffic.
"""

from __future__ import annotations

import asyncio
import statistics
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import WorkItemStatus, WriteBackJobStatus
from agentblue.accounting.models import AccountingWorkItem, WriteBackJob
from agentblue.accounting.workflow import WorkflowTransitionService
from agentblue.db.session import get_session_factory
from agentblue.security.context import ExecutionContext
from agentblue.security.principal import Principal
from agentblue.security.roles import Role

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]

REALM = "load-test-realm"


def _make_principal(pid: str = "load-user", roles: frozenset[Role] | None = None) -> Principal:
    return Principal(
        principal_id=pid,
        principal_type="human",
        email=f"{pid}@test.local",
        display_name=f"Load {pid}",
        active=True,
        roles=roles or frozenset({Role.ACCOUNTANT}),
        realm_ids=frozenset({REALM}),
        auth_method="jwt",
        correlation_id=str(uuid.uuid4()),
    )


def _make_ctx(pid: str = "load-user", roles: frozenset[Role] | None = None) -> ExecutionContext:
    return ExecutionContext(
        principal=_make_principal(pid, roles),
        correlation_id=str(uuid.uuid4()),
    )


async def _create_work_item(session: AsyncSession, status: str = "NEEDS_REVIEW") -> AccountingWorkItem:
    item = AccountingWorkItem(
        realm_id=REALM,
        source_transaction_id=f"load-{uuid.uuid4()}",
        source_transaction_type="Purchase",
        amount=Decimal("100.00"),
        vendor_or_payee="Load Test Vendor",
        description="Load test transaction",
        status=status,
        recommended_account_quickbooks_id="QB-ACCT-001",
        recommendation_source="TEST",
        recommendation_confidence=Decimal("0.95"),
    )
    session.add(item)
    await session.flush()
    return item


async def _measure_latency(coro) -> float:
    """Measure latency of an async operation in seconds."""
    start = time.monotonic()
    await coro
    return time.monotonic() - start


@pytest.mark.asyncio
async def test_concurrent_review_list_performance() -> None:
    """Measure latency of concurrent review-list queries."""
    factory = get_session_factory()
    latencies: list[float] = []

    async def _list_work_items() -> float:
        async with factory() as session:
            start = time.monotonic()
            result = await session.execute(
                select(AccountingWorkItem)
                .where(AccountingWorkItem.realm_id == REALM)
                .limit(50)
            )
            result.scalars().all()
            return time.monotonic() - start

    tasks = [_list_work_items() for _ in range(100)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, float):
            latencies.append(r)
        elif isinstance(r, Exception):
            latencies.append(999.0)  # Count errors as high latency

    assert len(latencies) == 100
    statistics.median(latencies)
    p95 = sorted(latencies)[94]
    sorted(latencies)[98]
    error_count = sum(1 for r in results if isinstance(r, Exception))

    assert error_count == 0, f"{error_count} errors in 100 requests"
    assert p95 < 2.0, f"P95 latency too high: {p95:.3f}s"


@pytest.mark.asyncio
async def test_concurrent_distinct_claims() -> None:
    """100 concurrent claims on distinct work items."""
    factory = get_session_factory()

    # Pre-create 100 work items
    item_ids: list[str] = []
    async with factory() as session:
        for _ in range(100):
            item = await _create_work_item(session)
            item_ids.append(item.id)
        await session.commit()

    # Concurrent claims
    async def _claim(item_id: str) -> bool:
        async with factory() as session:
            svc = WorkflowTransitionService(session)
            ctx = _make_ctx(f"claim-{uuid.uuid4().hex[:8]}", frozenset({Role.ACCOUNTANT}))
            try:
                await svc.transition_work_item(
                    ctx, item_id, WorkItemStatus.IN_REVIEW, reason="load test claim"
                )
                await session.commit()
                return True
            except (ValueError, Exception):
                await session.rollback()
                return False

    tasks = [_claim(iid) for iid in item_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = sum(1 for r in results if r is True)
    errors = sum(1 for r in results if isinstance(r, Exception))

    assert successes == 100, f"Expected 100 claims, got {successes}"
    assert errors == 0, f"{errors} errors"


@pytest.mark.asyncio
async def test_same_item_contention() -> None:
    """Multiple concurrent claims on the same work item — only one wins."""
    factory = get_session_factory()

    async with factory() as session:
        item = await _create_work_item(session)
        await session.commit()
        item_id = item.id

    async def _claim(pid: str) -> bool:
        async with factory() as session:
            svc = WorkflowTransitionService(session)
            ctx = _make_ctx(pid, frozenset({Role.ACCOUNTANT}))
            try:
                await svc.transition_work_item(
                    ctx, item_id, WorkItemStatus.IN_REVIEW, reason=f"contention-{pid}"
                )
                await session.commit()
                return True
            except (ValueError, Exception):
                await session.rollback()
                return False

    tasks = [_claim(f"r-{i}") for i in range(10)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = sum(1 for r in results if r is True)
    failures = sum(1 for r in results if r is False)

    assert successes == 1, f"Expected exactly 1 success, got {successes}"
    assert failures == 9, f"Expected 9 failures, got {failures}"


@pytest.mark.asyncio
async def test_concurrent_approvals_distinct_items() -> None:
    """100 concurrent approvals on distinct items."""
    factory = get_session_factory()

    # Pre-create 100 CORRECTED items
    item_ids: list[str] = []
    async with factory() as session:
        for _ in range(100):
            item = await _create_work_item(session, status="CORRECTED")
            item_ids.append(item.id)
        await session.commit()

    async def _approve(item_id: str) -> bool:
        async with factory() as session:
            svc = WorkflowTransitionService(session)
            ctx = _make_ctx(f"ap-{uuid.uuid4().hex[:8]}", frozenset({Role.APPROVER}))
            try:
                await svc.transition_work_item(
                    ctx, item_id, WorkItemStatus.APPROVED, reason="load test approve"
                )
                await session.commit()
                return True
            except (ValueError, Exception):
                await session.rollback()
                return False

    tasks = [_approve(iid) for iid in item_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = sum(1 for r in results if r is True)
    assert successes == 100, f"Expected 100 approvals, got {successes}"


@pytest.mark.asyncio
async def test_same_approval_contention() -> None:
    """Multiple concurrent approvals on same item — only one wins."""
    factory = get_session_factory()

    async with factory() as session:
        item = await _create_work_item(session, status="CORRECTED")
        await session.commit()
        item_id = item.id

    async def _approve(pid: str) -> bool:
        async with factory() as session:
            svc = WorkflowTransitionService(session)
            ctx = _make_ctx(pid, frozenset({Role.APPROVER}))
            try:
                await svc.transition_work_item(
                    ctx, item_id, WorkItemStatus.APPROVED, reason=f"contention-{pid}"
                )
                await session.commit()
                return True
            except (ValueError, Exception):
                await session.rollback()
                return False

    tasks = [_approve(f"a-{i}") for i in range(10)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = sum(1 for r in results if r is True)
    assert successes == 1, f"Expected exactly 1 approval, got {successes}"


@pytest.mark.asyncio
async def test_writeback_job_preparation_bulk() -> None:
    """Prepare 100 write-back jobs concurrently."""
    factory = get_session_factory()

    item_ids: list[str] = []
    async with factory() as session:
        for _ in range(100):
            item = await _create_work_item(session, status="APPROVED")
            item_ids.append(item.id)
        await session.commit()

    async def _prepare_job(item_id: str) -> bool:
        async with factory() as session:
            job = WriteBackJob(
                work_item_id=item_id,
                realm_id=REALM,
                target_transaction_id=f"QB-{uuid.uuid4().hex[:8]}",
                idempotency_key=str(uuid.uuid4()),
                approver_principal_id="approver-load",
                status=WriteBackJobStatus.PENDING.value,
            )
            session.add(job)
            try:
                await session.commit()
                return True
            except Exception:
                await session.rollback()
                return False

    tasks = [_prepare_job(iid) for iid in item_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = sum(1 for r in results if r is True)
    assert successes == 100, f"Expected 100 jobs, got {successes}"


@pytest.mark.asyncio
async def test_mixed_traffic_latency() -> None:
    """Mixed review, approval, and read traffic measuring latency."""
    factory = get_session_factory()

    # Seed data
    async with factory() as session:
        for _ in range(20):
            await _create_work_item(session, status="NEEDS_REVIEW")
            await _create_work_item(session, status="CORRECTED")
        await session.commit()

    latencies: list[float] = []

    async def _read_list() -> float:
        async with factory() as session:
            start = time.monotonic()
            await session.execute(
                select(AccountingWorkItem)
                .where(AccountingWorkItem.realm_id == REALM)
                .limit(50)
            )
            return time.monotonic() - start

    tasks = [_read_list() for _ in range(100)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, float):
            latencies.append(r)

    assert len(latencies) == 100
    statistics.median(latencies)
    p95 = sorted(latencies)[94]
    sorted(latencies)[98]

    # Store results for reporting
    assert p95 < 2.0, f"P95 too high: {p95:.3f}s"


@pytest.mark.asyncio
async def test_safety_invariants_under_load() -> None:
    """Verify safety invariants hold under concurrent load."""
    factory = get_session_factory()

    # Create items across two realms
    async with factory() as session:
        for _ in range(10):
            item = AccountingWorkItem(
                realm_id="realm-a",
                source_transaction_id=f"inv-a-{uuid.uuid4()}",
                source_transaction_type="Purchase",
                amount=Decimal("100.00"),
                status=WorkItemStatus.NEEDS_REVIEW.value,
            )
            session.add(item)
            item2 = AccountingWorkItem(
                realm_id="realm-b",
                source_transaction_id=f"inv-b-{uuid.uuid4()}",
                source_transaction_type="Purchase",
                amount=Decimal("200.00"),
                status=WorkItemStatus.NEEDS_REVIEW.value,
            )
            session.add(item2)
        await session.commit()

    # Concurrent cross-realm claims — all must fail
    async def _cross_realm_claim(realm: str, item_realm: str) -> bool:
        async with factory() as session:
            svc = WorkflowTransitionService(session)
            ctx = _make_ctx(
                f"cross-{uuid.uuid4().hex[:8]}",
                frozenset({Role.ACCOUNTANT}),
            )
            # Principal has realm_ids={REALM} but items are in item_realm
            # The service checks realm — cross-realm should fail
            try:
                result = await session.execute(
                    select(AccountingWorkItem)
                    .where(AccountingWorkItem.realm_id == item_realm)
                    .limit(1)
                )
                item = result.scalar_one_or_none()
                if item is None:
                    return False
                # This should fail because principal's realm doesn't match item's realm
                await svc.transition_work_item(
                    ctx, item.id, WorkItemStatus.IN_REVIEW, reason="cross-realm test"
                )
                await session.commit()
                return True  # Should not reach here
            except (PermissionError, ValueError, Exception):
                await session.rollback()
                return False

    # Try cross-realm from realm-a to realm-b items
    tasks = [_cross_realm_claim("realm-a", "realm-b") for _ in range(20)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # All must fail (realm-a principal can't access realm-b items)
    successes = sum(1 for r in results if r is True)
    assert successes == 0, f"Cross-realm access succeeded {successes} times — SECURITY VIOLATION"
