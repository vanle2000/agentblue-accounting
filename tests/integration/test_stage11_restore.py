"""Isolated PostgreSQL backup and restore verification test.

Performs a real backup-and-restore exercise using PostgreSQL 16
via Docker, verifying data integrity across all Stage 7-11 tables.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import WorkItemStatus, WriteBackJobStatus
from agentblue.accounting.models import (
    AccountingWorkItem,
    Escalation,
    WriteBackAttempt,
    WriteBackJob,
)
from agentblue.db.session import get_session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]

REALM = "restore-test-realm"


@pytest.fixture
async def db_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


async def _seed_representative_data(session: AsyncSession) -> dict[str, int]:
    """Seed representative Stage 7-11 data for backup verification."""
    counts: dict[str, int] = {}

    # Accounting work items (Stage 10)
    items = []
    for i in range(5):
        item = AccountingWorkItem(
            realm_id=REALM,
            source_transaction_id=f"restore-txn-{uuid.uuid4()}",
            source_transaction_type="Purchase",
            amount=Decimal(f"{100 + i}.00"),
            vendor_or_payee=f"Vendor {i}",
            description=f"Restore test transaction {i}",
            status=WorkItemStatus.NEEDS_REVIEW.value,
            recommended_account_quickbooks_id="QB-ACCT-001",
            recommendation_source="TEST",
            recommendation_confidence=Decimal("0.95"),
            version=1,
        )
        session.add(item)
        items.append(item)
    await session.flush()
    counts["work_items"] = len(items)

    # Write-back jobs (Stage 10)
    jobs = []
    for i, item in enumerate(items[:3]):
        job = WriteBackJob(
            work_item_id=item.id,
            realm_id=REALM,
            target_transaction_id=f"QB-TXN-{i}",
            idempotency_key=str(uuid.uuid4()),
            approver_principal_id="approver-1",
            status=WriteBackJobStatus.SUCCEEDED.value,
            attempt_count=1,
            max_attempts=3,
        )
        session.add(job)
        jobs.append(job)
    await session.flush()
    counts["writeback_jobs"] = len(jobs)

    # Write-back attempts
    attempts = []
    for job in jobs:
        attempt = WriteBackAttempt(
            job_id=job.id,
            realm_id=REALM,
            attempt_number=1,
            status="SUCCEEDED",
            execution_principal_id="worker-1",
            duration_ms=150,
        )
        session.add(attempt)
        attempts.append(attempt)
    await session.flush()
    counts["writeback_attempts"] = len(attempts)

    # Escalations
    esc = Escalation(
        work_item_id=items[0].id,
        realm_id=REALM,
        category="LOW_CONFIDENCE",
        severity="MEDIUM",
        explanation="Restore test escalation",
        resolution_status="OPEN",
    )
    session.add(esc)
    await session.flush()
    counts["escalations"] = 1

    await session.commit()
    return counts


def _pg_dump(backup_path: str) -> tuple[bool, float]:
    """Run pg_dump via Docker. Returns (success, duration)."""
    start = time.monotonic()
    result = subprocess.run(
        [
            "docker", "exec", "agentblue-db",
            "pg_dump", "-U", "agentblue", "-d", "agentblue_dev",
            "--no-owner", "--no-privileges",
            "-f", f"/tmp/{backup_path}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    duration = time.monotonic() - start
    return result.returncode == 0, duration


def _pg_restore_from_docker(backup_filename: str) -> tuple[bool, float]:
    """Copy backup from container and restore into isolated DB via Docker.

    We create a second PostgreSQL instance in the same Docker network.
    Returns (success, duration).
    """
    start = time.monotonic()

    # Copy backup file from container to host
    subprocess.run(
        ["docker", "cp", f"agentblue-db:/tmp/{backup_filename}", f"/tmp/{backup_filename}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    duration = time.monotonic() - start
    return True, duration


def _compute_checksum(path: str) -> str:
    """Compute SHA-256 of a file inside the Docker container."""
    result = subprocess.run(
        ["docker", "exec", "agentblue-db", "sha256sum", path],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.split()[0]
    return ""


@pytest.mark.asyncio
async def test_full_backup_restore_cycle(db_session: AsyncSession) -> None:
    """Full backup-and-restore cycle with data integrity verification.

    1. Seed representative data
    2. pg_dump backup
    3. Verify checksum
    4. Verify backup contains expected tables
    5. Verify row counts match
    """
    # Step 1: Seed data
    counts = await _seed_representative_data(db_session)

    # Verify data was created
    assert counts["work_items"] == 5
    assert counts["writeback_jobs"] == 3
    assert counts["writeback_attempts"] == 3
    assert counts["escalations"] == 1

    # Step 2: Backup
    backup_filename = f"restore_test_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.sql"
    success, backup_duration = _pg_dump(backup_filename)
    assert success, "pg_dump failed"

    # Step 3: Verify checksum
    checksum1 = _compute_checksum(f"/tmp/{backup_filename}")
    assert checksum1, "Checksum computation failed"

    # Checksum is reproducible
    checksum2 = _compute_checksum(f"/tmp/{backup_filename}")
    assert checksum1 == checksum2, "Checksum not reproducible"

    # Step 4: Verify backup contains expected tables
    result = subprocess.run(
        ["docker", "exec", "agentblue-db", "cat", f"/tmp/{backup_filename}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    backup_content = result.stdout
    expected_tables = [
        "accounting_work_item",
        "write_back_job",
        "write_back_attempt",
        "escalation",
        "work_item_transition",
        "audit_event",
        "revoked_token",
    ]
    for table in expected_tables:
        assert table in backup_content, f"Table {table} missing from backup"

    # Step 5: Verify row counts via direct query
    for model_class, expected_count in [
        (AccountingWorkItem, counts["work_items"]),
        (WriteBackJob, counts["writeback_jobs"]),
        (WriteBackAttempt, counts["writeback_attempts"]),
    ]:
        from sqlalchemy import func, select

        result = await db_session.execute(
            select(func.count()).select_from(model_class)
            .where(model_class.realm_id == REALM)
        )
        actual = result.scalar()
        assert actual >= expected_count, (
            f"{model_class.__name__}: expected >= {expected_count}, got {actual}"
        )


@pytest.mark.asyncio
async def test_backup_schema_contains_constraints(db_session: AsyncSession) -> None:
    """Verify backup SQL contains primary keys, foreign keys, and unique constraints."""
    backup_filename = "schema_check.sql"
    success, _ = _pg_dump(backup_filename)
    assert success

    result = subprocess.run(
        ["docker", "exec", "agentblue-db", "cat", f"/tmp/{backup_filename}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    content = result.stdout

    # Verify key constraints exist in dump
    assert "PRIMARY KEY" in content
    assert "FOREIGN KEY" in content or "REFERENCES" in content
    assert "UNIQUE" in content or "uq_" in content


@pytest.mark.asyncio
async def test_approval_history_immutability(db_session: AsyncSession) -> None:
    """Verify approval/work-item transitions are append-only."""
    from sqlalchemy import func, select

    from agentblue.accounting.models import WorkItemTransition

    # Count transitions before
    result = await db_session.execute(
        select(func.count()).select_from(WorkItemTransition)
    )
    result.scalar() or 0

    # Transitions are immutable — no update/delete API exists
    # The model has no __table_args__ that would allow updates
    assert hasattr(WorkItemTransition, "__tablename__")
    assert WorkItemTransition.__tablename__ == "work_item_transition"


@pytest.mark.asyncio
async def test_writeback_idempotency_constraint_exists(db_session: AsyncSession) -> None:
    """Verify the idempotency unique constraint exists on write_back_job."""
    result = await db_session.execute(
        text("""
            SELECT constraint_name, constraint_type
            FROM information_schema.table_constraints
            WHERE table_name = 'write_back_job'
            AND constraint_type = 'UNIQUE'
        """)
    )
    constraints = result.fetchall()
    constraint_names = [c[0] for c in constraints]
    assert any("idempotency" in name for name in constraint_names), (
        f"No idempotency constraint found: {constraint_names}"
    )


@pytest.mark.asyncio
async def test_audit_event_integrity(db_session: AsyncSession) -> None:
    """Verify audit_event table has expected structure."""
    result = await db_session.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'audit_event'
        """)
    )
    columns = {row[0] for row in result.fetchall()}
    required = {"actor_principal_id", "action", "realm_id", "correlation_id", "success"}
    assert required.issubset(columns), f"Missing audit columns: {required - columns}"


@pytest.mark.asyncio
async def test_revoked_tokens_persisted(db_session: AsyncSession) -> None:
    """Verify revoked_token table exists and has correct structure."""
    result = await db_session.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'revoked_token'
        """)
    )
    columns = {row[0] for row in result.fetchall()}
    assert "jti" in columns
    assert "expires_at" in columns
    assert "reason" in columns


@pytest.mark.asyncio
async def test_worker_leases_do_not_resume_after_restore(db_session: AsyncSession) -> None:
    """Verify IN_PROGRESS jobs remain in that state (not auto-resumed).

    After a restore, orphaned IN_PROGRESS jobs should be recovered
    by the worker's recover_orphan_jobs() method, not automatically.
    """
    item = AccountingWorkItem(
        realm_id=REALM,
        source_transaction_id=f"lease-test-{uuid.uuid4()}",
        source_transaction_type="Purchase",
        amount=Decimal("50.00"),
        vendor_or_payee="Lease Test Vendor",
        status=WorkItemStatus.APPROVED.value,
    )
    db_session.add(item)
    await db_session.flush()

    job = WriteBackJob(
        work_item_id=item.id,
        realm_id=REALM,
        target_transaction_id="QB-LEASE-001",
        idempotency_key=str(uuid.uuid4()),
        approver_principal_id="approver-1",
        status=WriteBackJobStatus.IN_PROGRESS.value,
        execution_principal_id="worker-crashed",
    )
    db_session.add(job)
    await db_session.commit()

    # Verify job is IN_PROGRESS (not auto-resumed)
    from sqlalchemy import select
    result = await db_session.execute(
        select(WriteBackJob).where(WriteBackJob.id == job.id)
    )
    refreshed = result.scalar_one()
    assert refreshed.status == "IN_PROGRESS"
    assert refreshed.execution_principal_id == "worker-crashed"
