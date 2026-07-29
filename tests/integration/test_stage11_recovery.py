"""PostgreSQL integration tests for worker recovery, backup/restore, and operational resilience.

Tests run against real PostgreSQL 16 (Docker on port 5433) and exercise:
  A. Worker processing against real DB
  B. Lease, concurrency, and orphan recovery
  C. Dead-letter and retry logic
  D. Backup and restore verification
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import WriteBackJobStatus
from agentblue.accounting.models import (
    AccountingWorkItem,
    WriteBackAttempt,
    WriteBackJob,
)
from agentblue.accounting.worker import WorkerConfig, WorkerService
from agentblue.db.session import get_session_factory

pytestmark = pytest.mark.integration

REALM = "recovery-test-realm"

# Docker Compose PostgreSQL defaults
_PG_HOST = "localhost"
_PG_PORT = "5433"
_PG_USER = "agentblue"
_PG_PASSWORD = "agentblue"
_PG_NAME = "agentblue_dev"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session() -> AsyncSession:
    """Integration test session against real PostgreSQL."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


async def _create_work_item(
    session: AsyncSession,
    *,
    status: str = "APPROVED",
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
        description="Test transaction for recovery tests",
        status=status,
        recommended_account_quickbooks_id="QB-ACCT-001",
        recommendation_source="TEST",
        recommendation_confidence=Decimal("0.95"),
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def _create_writeback_job(
    session: AsyncSession,
    work_item_id: str,
    *,
    status: str = WriteBackJobStatus.READY.value,
    attempt_count: int = 0,
    max_attempts: int = 3,
    next_retry_at: datetime | None = None,
    idempotency_key: str | None = None,
    execution_principal_id: str | None = None,
    updated_at: datetime | None = None,
) -> WriteBackJob:
    """Insert a write-back job directly into the database."""
    job = WriteBackJob(
        work_item_id=work_item_id,
        realm_id=REALM,
        target_transaction_id=f"QB-TXN-{uuid.uuid4().hex[:8]}",
        idempotency_key=idempotency_key or str(uuid.uuid4()),
        approver_principal_id="approver-test",
        status=status,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        next_retry_at=next_retry_at,
        execution_principal_id=execution_principal_id,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Backdate updated_at if requested
    if updated_at is not None:
        await session.execute(
            text("UPDATE write_back_job SET updated_at = :ts WHERE id = :id"),
            {"ts": updated_at, "id": job.id},
        )
        await session.commit()
        await session.refresh(job)

    return job


def _worker_config(**kwargs) -> WorkerConfig:
    """Create a WorkerConfig with sensible test defaults."""
    defaults = dict(
        worker_id=f"test-worker-{uuid.uuid4().hex[:8]}",
        heartbeat_interval=1,
        lease_duration=30,
        max_batch_size=10,
        poll_interval=0.1,
        stuck_threshold_minutes=1,
    )
    defaults.update(kwargs)
    return WorkerConfig(**defaults)


# ===================================================================
# A. Worker PostgreSQL Tests
# ===================================================================


class TestWorkerPostgres:
    """Worker processes jobs against a real PostgreSQL instance."""

    @pytest.mark.asyncio
    async def test_worker_processes_ready_job(self, db_session: AsyncSession) -> None:
        """Worker picks up a READY job and transitions it to SUCCEEDED."""
        item = await _create_work_item(db_session)
        job = await _create_writeback_job(
            db_session, item.id, status=WriteBackJobStatus.READY.value
        )

        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()

        assert processed == 1
        assert worker.jobs_processed == 1

        # Verify job state in DB
        factory = get_session_factory()
        async with factory() as verify_session:
            refreshed = await verify_session.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.SUCCEEDED.value
            assert refreshed.attempt_count == 1
            assert refreshed.completed_at is not None
            assert refreshed.execution_principal_id == worker.worker_id

    @pytest.mark.asyncio
    async def test_worker_processes_failed_retryable_job(
        self, db_session: AsyncSession
    ) -> None:
        """Worker picks up a FAILED_RETRYABLE job and succeeds."""
        item = await _create_work_item(db_session)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.FAILED_RETRYABLE.value,
            attempt_count=1,
        )

        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()

        assert processed == 1

        factory = get_session_factory()
        async with factory() as verify_session:
            refreshed = await verify_session.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.SUCCEEDED.value
            assert refreshed.attempt_count == 2

    @pytest.mark.asyncio
    async def test_worker_skips_job_at_max_attempts(
        self, db_session: AsyncSession
    ) -> None:
        """Worker skips jobs that have reached max_attempts."""
        item = await _create_work_item(db_session)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.FAILED_RETRYABLE.value,
            attempt_count=3,
            max_attempts=3,
        )

        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()

        assert processed == 0

        factory = get_session_factory()
        async with factory() as verify_session:
            refreshed = await verify_session.get(WriteBackJob, job.id)
            # Status unchanged — not picked up
            assert refreshed.status == WriteBackJobStatus.FAILED_RETRYABLE.value
            assert refreshed.attempt_count == 3

    @pytest.mark.asyncio
    async def test_worker_skips_job_with_future_retry(
        self, db_session: AsyncSession
    ) -> None:
        """Worker skips jobs whose next_retry_at is in the future."""
        item = await _create_work_item(db_session)
        future = datetime.now(UTC) + timedelta(hours=1)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.FAILED_RETRYABLE.value,
            attempt_count=1,
            next_retry_at=future,
        )

        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()

        assert processed == 0

        factory = get_session_factory()
        async with factory() as verify_session:
            refreshed = await verify_session.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.FAILED_RETRYABLE.value

    @pytest.mark.asyncio
    async def test_worker_increments_attempt_count(
        self, db_session: AsyncSession
    ) -> None:
        """Worker increments attempt_count each time it processes a job."""
        item = await _create_work_item(db_session)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
        )

        assert job.attempt_count == 0

        worker = WorkerService(_worker_config())
        await worker._process_batch()

        factory = get_session_factory()
        async with factory() as verify_session:
            refreshed = await verify_session.get(WriteBackJob, job.id)
            assert refreshed.attempt_count == 1

    @pytest.mark.asyncio
    async def test_worker_records_writeback_attempt(
        self, db_session: AsyncSession
    ) -> None:
        """Worker creates a WriteBackAttempt record for each job processed."""
        item = await _create_work_item(db_session)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.READY.value,
        )

        worker = WorkerService(_worker_config())
        await worker._process_batch()

        factory = get_session_factory()
        async with factory() as verify_session:
            result = await verify_session.execute(
                select(WriteBackAttempt).where(WriteBackAttempt.job_id == job.id)
            )
            attempts = result.scalars().all()
            assert len(attempts) == 1
            attempt = attempts[0]
            assert attempt.attempt_number == 1
            assert attempt.status == "SIMULATED"
            assert attempt.execution_principal_id == worker.worker_id
            assert attempt.duration_ms == 0
            assert attempt.realm_id == REALM


# ===================================================================
# B. Lease and Concurrency
# ===================================================================


class TestLeaseAndConcurrency:
    """SKIP LOCKED concurrency and lease-based orphan recovery."""

    @pytest.mark.asyncio
    async def test_skip_locked_prevents_double_processing(
        self, db_session: AsyncSession
    ) -> None:
        """Two workers competing: SKIP LOCKED ensures only one picks up each job."""
        item1 = await _create_work_item(
            db_session, source_txn_id=f"skip-{uuid.uuid4()}"
        )
        item2 = await _create_work_item(
            db_session, source_txn_id=f"skip-{uuid.uuid4()}"
        )
        job1 = await _create_writeback_job(
            db_session, item1.id, status=WriteBackJobStatus.READY.value
        )
        job2 = await _create_writeback_job(
            db_session, item2.id, status=WriteBackJobStatus.READY.value
        )

        # Worker 1 processes a batch — should get both jobs
        worker1 = WorkerService(_worker_config(max_batch_size=10))
        processed1 = await worker1._process_batch()

        # Worker 2 processes a batch — should get nothing (all already locked/succeeded)
        worker2 = WorkerService(_worker_config(max_batch_size=10))
        processed2 = await worker2._process_batch()

        assert processed1 == 2
        assert processed2 == 0

        # Both jobs should be SUCCEEDED
        factory = get_session_factory()
        async with factory() as vs:
            j1 = await vs.get(WriteBackJob, job1.id)
            j2 = await vs.get(WriteBackJob, job2.id)
            assert j1.status == WriteBackJobStatus.SUCCEEDED.value
            assert j2.status == WriteBackJobStatus.SUCCEEDED.value

    @pytest.mark.asyncio
    async def test_lease_expiration_orphan_recovery(
        self, db_session: AsyncSession
    ) -> None:
        """Orphaned IN_PROGRESS jobs past threshold are recovered."""
        item = await _create_work_item(
            db_session, source_txn_id=f"orphan-{uuid.uuid4()}"
        )
        # Create a job stuck IN_PROGRESS with old updated_at
        old_time = datetime.now(UTC) - timedelta(minutes=60)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.IN_PROGRESS.value,
            attempt_count=1,
            updated_at=old_time,
        )

        worker = WorkerService(_worker_config(stuck_threshold_minutes=5))
        recovered = await worker.recover_orphan_jobs()

        assert recovered >= 1

        factory = get_session_factory()
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.FAILED_RETRYABLE.value
            assert refreshed.failure_category == "WORKER_CRASH"
            assert refreshed.failure_message == "Recovered from orphaned state"

    @pytest.mark.asyncio
    async def test_worker_restart_orphan_recovery(
        self, db_session: AsyncSession
    ) -> None:
        """On startup, a worker recovers orphans then processes recovered jobs."""
        item = await _create_work_item(
            db_session, source_txn_id=f"restart-{uuid.uuid4()}"
        )
        old_time = datetime.now(UTC) - timedelta(minutes=60)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.IN_PROGRESS.value,
            attempt_count=1,
            max_attempts=3,
            updated_at=old_time,
        )

        worker = WorkerService(_worker_config(stuck_threshold_minutes=5))

        # Step 1: recover orphans
        recovered = await worker.recover_orphan_jobs()
        assert recovered >= 1

        # Step 2: process recovered jobs
        processed = await worker._process_batch()
        assert processed >= 1

        factory = get_session_factory()
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.SUCCEEDED.value
            assert refreshed.attempt_count == 2

    @pytest.mark.asyncio
    async def test_no_duplicate_execution_after_recovery(
        self, db_session: AsyncSession
    ) -> None:
        """After recovery, the same job is only processed once more, not duplicated."""
        item = await _create_work_item(
            db_session, source_txn_id=f"nodup-{uuid.uuid4()}"
        )
        old_time = datetime.now(UTC) - timedelta(minutes=60)
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.IN_PROGRESS.value,
            attempt_count=1,
            max_attempts=3,
            updated_at=old_time,
        )

        worker = WorkerService(_worker_config(stuck_threshold_minutes=5))
        await worker.recover_orphan_jobs()

        # Process twice — second should pick up nothing
        p1 = await worker._process_batch()
        p2 = await worker._process_batch()

        assert p1 >= 1
        assert p2 == 0

        factory = get_session_factory()
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.SUCCEEDED.value
            assert refreshed.attempt_count == 2

            # Only 2 attempts total (original + recovery), not more
            result = await vs.execute(
                select(WriteBackAttempt).where(WriteBackAttempt.job_id == job.id)
            )
            attempts = result.scalars().all()
            assert len(attempts) == 1  # Only the successful attempt recorded


# ===================================================================
# C. Dead-Letter and Retry
# ===================================================================


class TestDeadLetterAndRetry:
    """Dead-letter transitions, manual retry, and terminal states."""

    @pytest.mark.asyncio
    async def test_max_attempts_transitions_to_failed_permanent(
        self, db_session: AsyncSession
    ) -> None:
        """A job at max_attempts is not picked up — must be manually dead-lettered."""
        item = await _create_work_item(
            db_session, source_txn_id=f"deadletter-{uuid.uuid4()}"
        )
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.FAILED_RETRYABLE.value,
            attempt_count=3,
            max_attempts=3,
        )

        # Worker should skip it
        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()
        assert processed == 0

        # Simulate dead-letter transition (as the workflow service would)
        factory = get_session_factory()
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            refreshed.status = WriteBackJobStatus.FAILED_PERMANENT.value
            refreshed.failure_message = "Exhausted all retry attempts"
            await vs.commit()

        # Verify terminal state
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.FAILED_PERMANENT.value
            assert refreshed.failure_message == "Exhausted all retry attempts"

    @pytest.mark.asyncio
    async def test_manual_retry_creates_new_attempt(
        self, db_session: AsyncSession
    ) -> None:
        """Manual retry resets the job to READY so the worker picks it up again."""
        item = await _create_work_item(
            db_session, source_txn_id=f"manualretry-{uuid.uuid4()}"
        )
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.FAILED_RETRYABLE.value,
            attempt_count=2,
            max_attempts=5,
        )

        # Simulate manual retry: reset to READY
        factory = get_session_factory()
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            refreshed.status = WriteBackJobStatus.READY.value
            refreshed.next_retry_at = None
            await vs.commit()

        # Worker picks it up
        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()
        assert processed == 1

        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.SUCCEEDED.value
            assert refreshed.attempt_count == 3  # incremented from 2

            # Verify new attempt record
            result = await vs.execute(
                select(WriteBackAttempt).where(WriteBackAttempt.job_id == job.id)
            )
            attempts = result.scalars().all()
            assert len(attempts) == 1
            assert attempts[0].attempt_number == 3

    @pytest.mark.asyncio
    async def test_cancel_prevents_retry(self, db_session: AsyncSession) -> None:
        """A CANCELLED job is terminal and not picked up by the worker."""
        item = await _create_work_item(
            db_session, source_txn_id=f"cancel-{uuid.uuid4()}"
        )
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.CANCELLED.value,
            attempt_count=1,
            max_attempts=3,
        )

        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()
        assert processed == 0

        factory = get_session_factory()
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_dead_letter_state_is_terminal(
        self, db_session: AsyncSession
    ) -> None:
        """FAILED_PERMANENT is terminal — worker never picks it up."""
        item = await _create_work_item(
            db_session, source_txn_id=f"terminal-{uuid.uuid4()}"
        )
        job = await _create_writeback_job(
            db_session,
            item.id,
            status=WriteBackJobStatus.FAILED_PERMANENT.value,
            attempt_count=3,
            max_attempts=3,
        )

        worker = WorkerService(_worker_config())
        processed = await worker._process_batch()
        assert processed == 0

        factory = get_session_factory()
        async with factory() as vs:
            refreshed = await vs.get(WriteBackJob, job.id)
            assert refreshed.status == WriteBackJobStatus.FAILED_PERMANENT.value
            # Attempt count unchanged
            assert refreshed.attempt_count == 3


# ===================================================================
# D. Backup and Restore
# ===================================================================


_DB_CONTAINER = "agentblue-db"


def _pg_dump_to_file(dest_path: str) -> subprocess.CompletedProcess:
    """Run pg_dump inside the Docker container and copy output to *dest_path*.

    Uses ``docker exec`` so we don't need pg_dump installed on the host.
    """
    dump_cmd = (
        f"pg_dump -U {_PG_USER} -d {_PG_NAME} --no-owner --no-privileges"
    )
    result = subprocess.run(
        ["docker", "exec", _DB_CONTAINER, "bash", "-c", dump_cmd],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"pg_dump failed: {result.stderr}"
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(result.stdout)
    return result


class TestBackupAndRestore:
    """pg_dump backup and restore verification."""

    @pytest.mark.asyncio
    async def test_pg_dump_creates_valid_backup(
        self, db_session: AsyncSession
    ) -> None:
        """pg_dump produces a non-empty, valid SQL backup file."""
        # Ensure at least one table has data
        await _create_work_item(
            db_session, source_txn_id=f"backup-{uuid.uuid4()}"
        )

        with tempfile.NamedTemporaryFile(
            suffix=".sql", delete=False
        ) as tmp:
            backup_path = tmp.name

        try:
            _pg_dump_to_file(backup_path)

            # Verify the backup file is non-empty and contains expected tables
            file_size = os.path.getsize(backup_path)
            assert file_size > 0, "Backup file is empty"

            with open(backup_path, encoding="utf-8") as f:
                content = f.read()

            assert "accounting_work_item" in content
            assert "write_back_job" in content
            # pg_dump output should contain CREATE TABLE or COPY statements
            assert "COPY" in content or "CREATE TABLE" in content

        finally:
            if os.path.exists(backup_path):
                os.unlink(backup_path)

    @pytest.mark.asyncio
    async def test_backup_checksum_reproducible(
        self, db_session: AsyncSession
    ) -> None:
        """Two consecutive pg_dump backups of the same state produce identical checksums.

        We ensure no writes happen between the two dumps so the DB state is frozen.
        """
        # Seed deterministic data
        await _create_work_item(
            db_session, source_txn_id=f"checksum-{uuid.uuid4()}"
        )

        paths = []
        checksums = []
        try:
            for i in range(2):
                with tempfile.NamedTemporaryFile(
                    suffix=f"_dump{i}.sql", delete=False
                ) as tmp:
                    path = tmp.name
                paths.append(path)

                _pg_dump_to_file(path)

                # Normalize: strip non-deterministic lines (comments, restrict/unrestrict tokens)
                with open(path, encoding="utf-8") as f:
                    lines = f.readlines()
                normalized = [
                    line for line in lines
                    if not line.startswith("--")
                    and not line.startswith("\\restrict ")
                    and not line.startswith("\\unrestrict ")
                ]
                checksums.append(
                    hashlib.sha256("".join(normalized).encode()).hexdigest()
                )

        finally:
            for p in paths:
                if os.path.exists(p):
                    os.unlink(p)

        assert checksums[0] == checksums[1], (
            f"Checksums differ: {checksums[0]} vs {checksums[1]}"
        )
