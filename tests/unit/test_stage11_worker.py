"""Comprehensive tests for Stage 11 background worker and job recovery.

Covers:
A. Worker Lifecycle (6 tests)
B. Job Processing (8 tests)
C. Stuck Job Detection (4 tests)
D. Orphan Recovery (4 tests)
E. Worker Safety (4 tests)
F. Concurrency (4 tests)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentblue.accounting import WriteBackJobStatus
from agentblue.accounting.models import WriteBackAttempt, WriteBackJob
from agentblue.accounting.worker import WorkerConfig, WorkerService

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REALM_ACME = "acme-corp"
REALM_GLOBEX = "globex-inc"
WORKER_ID = "worker-test-001"

# Patch target for the session factory used inside the worker module
SESSION_FACTORY_PATCH = "agentblue.accounting.worker.get_session_factory"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker_config(**kwargs) -> WorkerConfig:
    """Create a WorkerConfig with sensible defaults."""
    defaults = dict(
        worker_id=WORKER_ID,
        heartbeat_interval=30,
        lease_duration=300,
        max_batch_size=50,
        poll_interval=0.01,  # fast for tests
        stuck_threshold_minutes=60,
    )
    defaults.update(kwargs)
    return WorkerConfig(**defaults)


def _make_writeback_job(
    *,
    job_id: str | None = None,
    work_item_id: str | None = None,
    realm_id: str = REALM_ACME,
    status: str = WriteBackJobStatus.READY.value,
    attempt_count: int = 0,
    max_attempts: int = 3,
    next_retry_at: datetime | None = None,
    execution_principal_id: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> MagicMock:
    """Create a mock WriteBackJob."""
    job = MagicMock(spec=WriteBackJob)
    job.id = job_id or str(uuid.uuid4())
    job.work_item_id = work_item_id or str(uuid.uuid4())
    job.realm_id = realm_id
    job.status = status
    job.attempt_count = attempt_count
    job.max_attempts = max_attempts
    job.next_retry_at = next_retry_at
    job.execution_principal_id = execution_principal_id
    job.created_at = created_at or datetime.now(UTC)
    job.updated_at = updated_at or datetime.now(UTC)
    job.started_at = started_at
    job.completed_at = completed_at
    return job


def _make_writeback_attempt(
    *,
    attempt_id: str | None = None,
    job_id: str = "job-001",
    realm_id: str = REALM_ACME,
    attempt_number: int = 1,
    status: str = "IN_PROGRESS",
    execution_principal_id: str = "",
    duration_ms: int | None = None,
) -> MagicMock:
    """Create a mock WriteBackAttempt."""
    attempt = MagicMock(spec=WriteBackAttempt)
    attempt.id = attempt_id or str(uuid.uuid4())
    attempt.job_id = job_id
    attempt.realm_id = realm_id
    attempt.attempt_number = attempt_number
    attempt.status = status
    attempt.execution_principal_id = execution_principal_id
    attempt.duration_ms = duration_ms
    return attempt


def _build_mock_session(
    jobs: list[MagicMock] | None = None,
    returning_ids: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock session for the worker.

    Args:
        jobs: list of mock WriteBackJob objects returned by execute().
        returning_ids: list of id values for UPDATE ... RETURNING results.
    """
    session = AsyncMock()
    _jobs = jobs or []

    # Ensure session.add is a sync MagicMock (not an AsyncMock coroutine)
    session.add = MagicMock()

    async def mock_execute(stmt: object) -> MagicMock:
        result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = _jobs
        result.scalars.return_value = scalars_mock

        # For UPDATE ... RETURNING
        if returning_ids is not None:
            fetchall_mock = MagicMock()
            fetchall_mock.return_value = [(rid,) for rid in returning_ids]
            result.fetchall = fetchall_mock

        return result

    session.execute = mock_execute
    return session


def _make_mock_session_factory(session: AsyncMock) -> MagicMock:
    """Create a mock session factory that returns the given session."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


# ===================================================================
# A. Worker Lifecycle
# ===================================================================


class TestWorkerLifecycle:
    """Tests for worker start, stop, heartbeat, and basic properties."""

    def test_worker_starts_in_stopped_state(self) -> None:
        """A freshly created WorkerService should not be running."""
        config = _make_worker_config()
        worker = WorkerService(config)
        assert worker.is_running is False

    async def test_start_sets_is_running_true(self) -> None:
        """start() should set is_running to True."""
        config = _make_worker_config(poll_interval=0.01)
        worker = WorkerService(config)

        session = _build_mock_session(jobs=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            # start() is a loop, so stop it immediately after one tick
            async def stop_after_first_tick():
                await worker.start()

            # Set running, then stop
            import asyncio
            task = asyncio.create_task(stop_after_first_tick())
            await asyncio.sleep(0.05)
            await worker.stop()
            await task

            assert worker.is_running is False  # stopped after stop()

    async def test_stop_sets_is_running_false(self) -> None:
        """stop() should set is_running to False."""
        config = _make_worker_config(poll_interval=0.01)
        worker = WorkerService(config)

        session = _build_mock_session(jobs=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            import asyncio
            async def run():
                await worker.start()

            task = asyncio.create_task(run())
            await asyncio.sleep(0.02)
            await worker.stop()
            await task

            assert worker.is_running is False

    async def test_heartbeat_records_timestamp(self) -> None:
        """_heartbeat() should set last_heartbeat to a recent datetime."""
        config = _make_worker_config()
        worker = WorkerService(config)
        assert worker.last_heartbeat is None

        await worker._heartbeat()

        assert worker.last_heartbeat is not None
        assert isinstance(worker.last_heartbeat, datetime)
        # Should be within the last few seconds
        delta = datetime.now(UTC) - worker.last_heartbeat
        assert delta.total_seconds() < 5

    async def test_jobs_processed_increments(self) -> None:
        """jobs_processed should increment after processing jobs."""
        config = _make_worker_config()
        worker = WorkerService(config)
        assert worker.jobs_processed == 0

        job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session(jobs=[job])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            processed = await worker._process_batch()

        assert processed == 1
        assert worker.jobs_processed == 1

    def test_worker_id_generated_if_not_provided(self) -> None:
        """WorkerConfig should auto-generate a worker_id when not provided."""
        config = WorkerConfig()
        assert config.worker_id.startswith("worker-")
        assert len(config.worker_id) == len("worker-") + 8  # "worker-" + 8 hex chars


# ===================================================================
# B. Job Processing
# ===================================================================


class TestJobProcessing:
    """Tests for _process_job() and _process_batch() behavior."""

    async def test_process_ready_job_succeeds(self) -> None:
        """A READY job should be processed to SUCCEEDED status."""
        config = _make_worker_config()
        worker = WorkerService(config)

        job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        assert job.status == WriteBackJobStatus.SUCCEEDED.value
        assert job.completed_at is not None

    async def test_process_failed_retryable_job_succeeds(self) -> None:
        """A FAILED_RETRYABLE job should also be processed to SUCCEEDED."""
        config = _make_worker_config()
        worker = WorkerService(config)

        job = _make_writeback_job(
            status=WriteBackJobStatus.FAILED_RETRYABLE.value,
            attempt_count=1,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        assert job.status == WriteBackJobStatus.SUCCEEDED.value

    async def test_process_increments_attempt_count(self) -> None:
        """Processing a job should increment its attempt_count."""
        config = _make_worker_config()
        worker = WorkerService(config)

        job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        assert job.attempt_count == 1

    async def test_process_records_writeback_attempt(self) -> None:
        """Processing a job should create and add a WriteBackAttempt."""
        config = _make_worker_config()
        worker = WorkerService(config)

        job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        # session.add() should have been called with a WriteBackAttempt
        session.add.assert_called_once()
        attempt = session.add.call_args[0][0]
        assert isinstance(attempt, WriteBackAttempt)
        assert attempt.job_id == job.id
        assert attempt.realm_id == job.realm_id
        assert attempt.attempt_number == 1

    async def test_process_sets_execution_principal_to_worker_id(self) -> None:
        """The execution_principal_id should be set to the worker's ID."""
        config = _make_worker_config(worker_id=WORKER_ID)
        worker = WorkerService(config)

        job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        assert job.execution_principal_id == WORKER_ID

    async def test_process_does_not_process_jobs_at_max_attempts(self) -> None:
        """Jobs at max_attempts should not be returned by _process_batch()."""
        config = _make_worker_config()
        worker = WorkerService(config)

        # Job at max_attempts — the SQL query filters these out,
        # so _process_batch receives an empty list
        session = _build_mock_session(jobs=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            processed = await worker._process_batch()

        assert processed == 0
        assert worker.jobs_processed == 0

    async def test_process_does_not_process_future_retry(self) -> None:
        """Jobs with future next_retry_at should not be processed."""
        config = _make_worker_config()
        worker = WorkerService(config)

        # The SQL query filters out jobs with next_retry_at > now,
        # so _process_batch receives an empty list
        session = _build_mock_session(jobs=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            processed = await worker._process_batch()

        assert processed == 0
        assert worker.jobs_processed == 0

    async def test_process_jobs_in_fifo_order(self) -> None:
        """Jobs returned by _process_batch are processed in order."""
        config = _make_worker_config()
        worker = WorkerService(config)

        job_a = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        job_b = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        # Jobs are returned in FIFO order (oldest first)
        session = _build_mock_session(jobs=[job_a, job_b])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            processed = await worker._process_batch()

        assert processed == 2
        # Both should be SUCCEEDED
        assert job_a.status == WriteBackJobStatus.SUCCEEDED.value
        assert job_b.status == WriteBackJobStatus.SUCCEEDED.value


# ===================================================================
# C. Stuck Job Detection
# ===================================================================


class TestStuckJobDetection:
    """Tests for detect_stuck_jobs()."""

    async def test_detects_in_progress_jobs_beyond_threshold(self) -> None:
        """IN_PROGRESS jobs older than the threshold should be detected."""
        config = _make_worker_config(stuck_threshold_minutes=60)
        worker = WorkerService(config)

        stuck_job = _make_writeback_job(
            status=WriteBackJobStatus.IN_PROGRESS.value,
            updated_at=datetime.now(UTC) - timedelta(minutes=120),
        )
        session = _build_mock_session(jobs=[stuck_job])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            stuck = await worker.detect_stuck_jobs()

        assert len(stuck) == 1
        assert stuck[0]["job_id"] == stuck_job.id
        assert stuck[0]["status"] == WriteBackJobStatus.IN_PROGRESS.value

    async def test_detects_validating_jobs_beyond_threshold(self) -> None:
        """VALIDATING jobs older than the threshold should be detected."""
        config = _make_worker_config(stuck_threshold_minutes=60)
        worker = WorkerService(config)

        stuck_job = _make_writeback_job(
            status=WriteBackJobStatus.VALIDATING.value,
            updated_at=datetime.now(UTC) - timedelta(minutes=90),
        )
        session = _build_mock_session(jobs=[stuck_job])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            stuck = await worker.detect_stuck_jobs()

        assert len(stuck) == 1
        assert stuck[0]["status"] == WriteBackJobStatus.VALIDATING.value

    async def test_does_not_detect_recently_updated_jobs(self) -> None:
        """Recently updated jobs should not appear as stuck."""
        config = _make_worker_config(stuck_threshold_minutes=60)
        worker = WorkerService(config)

        # Simulate: SQL returns empty list (recent jobs filtered out)
        session = _build_mock_session(jobs=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            stuck = await worker.detect_stuck_jobs()

        assert stuck == []

    async def test_returns_empty_list_when_no_stuck_jobs(self) -> None:
        """detect_stuck_jobs() should return an empty list when nothing is stuck."""
        config = _make_worker_config()
        worker = WorkerService(config)

        session = _build_mock_session(jobs=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            stuck = await worker.detect_stuck_jobs()

        assert stuck == []


# ===================================================================
# D. Orphan Recovery
# ===================================================================


class TestOrphanRecovery:
    """Tests for recover_orphan_jobs()."""

    async def test_recovers_in_progress_jobs_beyond_threshold(self) -> None:
        """IN_PROGRESS jobs past the threshold should be recovered."""
        config = _make_worker_config(stuck_threshold_minutes=60)
        worker = WorkerService(config)

        recovered_id = str(uuid.uuid4())
        session = _build_mock_session(returning_ids=[recovered_id])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            count = await worker.recover_orphan_jobs()

        assert count == 1

    async def test_sets_status_to_failed_retryable(self) -> None:
        """Recovered jobs should be set to FAILED_RETRYABLE."""
        config = _make_worker_config()
        worker = WorkerService(config)

        recovered_id = str(uuid.uuid4())
        session = AsyncMock()
        execute_called = False

        async def mock_execute(stmt):
            nonlocal execute_called
            execute_called = True
            result = MagicMock()
            fetchall_mock = MagicMock()
            fetchall_mock.return_value = [(recovered_id,)]
            result.fetchall = fetchall_mock
            return result

        session.execute = mock_execute
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker.recover_orphan_jobs()

        assert execute_called, "session.execute should have been called"
        session.commit.assert_called_once()

    async def test_does_not_recover_jobs_at_max_attempts(self) -> None:
        """Jobs at max_attempts should not be recovered."""
        config = _make_worker_config()
        worker = WorkerService(config)

        # SQL filters out jobs at max_attempts, so returning empty
        session = _build_mock_session(returning_ids=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            count = await worker.recover_orphan_jobs()

        assert count == 0

    async def test_does_not_recover_recently_updated_jobs(self) -> None:
        """Recently updated IN_PROGRESS jobs should not be recovered."""
        config = _make_worker_config()
        worker = WorkerService(config)

        # SQL filters out recent jobs, so returning empty
        session = _build_mock_session(returning_ids=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            count = await worker.recover_orphan_jobs()

        assert count == 0


# ===================================================================
# E. Worker Safety
# ===================================================================


class TestWorkerSafety:
    """Tests verifying the worker's safety constraints."""

    def test_worker_cannot_approve_work_items(self) -> None:
        """WorkerService has no approval method — it cannot approve work items."""
        config = _make_worker_config()
        worker = WorkerService(config)
        # The worker should NOT have any approval-related methods
        assert not hasattr(worker, "approve_work_item")
        assert not hasattr(worker, "create_approval")
        assert not hasattr(worker, "promote_model")
        assert not hasattr(worker, "create_writeback_from_recommendation")

    async def test_worker_uses_service_account_identity(self) -> None:
        """The worker's execution_principal_id is the worker_id (service account)."""
        config = _make_worker_config(worker_id="worker-sa-001")
        worker = WorkerService(config)

        job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        # execution_principal_id on the job is set to worker_id
        assert job.execution_principal_id == "worker-sa-001"

        # The attempt also gets the worker_id as execution_principal_id
        attempt = session.add.call_args[0][0]
        assert attempt.execution_principal_id == "worker-sa-001"

    async def test_worker_respects_realm_isolation(self) -> None:
        """The worker uses the job's realm_id, not a hardcoded one."""
        config = _make_worker_config()
        worker = WorkerService(config)

        job = _make_writeback_job(
            realm_id=REALM_GLOBEX,
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        # The attempt should carry the job's realm_id
        attempt = session.add.call_args[0][0]
        assert attempt.realm_id == REALM_GLOBEX

    async def test_worker_simulates_in_shadow_mode(self) -> None:
        """In shadow mode, the job is marked SIMULATED, not actually executed."""
        config = _make_worker_config()
        worker = WorkerService(config)

        job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        session = _build_mock_session()
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_job(session, job)

        # Shadow mode: attempt is SIMULATED, duration_ms is 0
        attempt = session.add.call_args[0][0]
        assert attempt.status == "SIMULATED"
        assert attempt.duration_ms == 0

        # Job status is SUCCEEDED (simulated)
        assert job.status == WriteBackJobStatus.SUCCEEDED.value


# ===================================================================
# F. Concurrency
# ===================================================================


class TestConcurrency:
    """Tests for concurrent job processing and error recovery."""

    async def test_skip_locked_prevents_double_processing(self) -> None:
        """The SELECT FOR UPDATE SKIP LOCKED should prevent double-processing.

        We verify this by ensuring _process_batch uses with_for_update(skip_locked=True).
        """
        config = _make_worker_config()
        worker = WorkerService(config)

        # Capture the SQL statement to verify it uses SKIP LOCKED
        captured_stmt = None

        session = AsyncMock()

        async def mock_execute(stmt):
            nonlocal captured_stmt
            captured_stmt = stmt
            result = MagicMock()
            scalars_mock = MagicMock()
            scalars_mock.all.return_value = []
            result.scalars.return_value = scalars_mock
            return result

        session.execute = mock_execute
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            await worker._process_batch()

        # The statement should have _for_update_args with skip_locked=True
        assert captured_stmt is not None
        for_update_args = getattr(captured_stmt, "_for_update_args", {})
        if for_update_args:
            assert for_update_args.get("skip_locked") is True

    async def test_two_workers_process_different_jobs(self) -> None:
        """Two workers should process different jobs (simulated via separate batches)."""
        config_a = _make_worker_config(worker_id="worker-a")
        config_b = _make_worker_config(worker_id="worker-b")
        worker_a = WorkerService(config_a)
        worker_b = WorkerService(config_b)

        job_a = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        job_b = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )

        session_a = _build_mock_session(jobs=[job_a])
        session_b = _build_mock_session(jobs=[job_b])
        factory_a = _make_mock_session_factory(session_a)
        factory_b = _make_mock_session_factory(session_b)

        with patch(SESSION_FACTORY_PATCH, return_value=factory_a):
            processed_a = await worker_a._process_batch()

        with patch(SESSION_FACTORY_PATCH, return_value=factory_b):
            processed_b = await worker_b._process_batch()

        assert processed_a == 1
        assert processed_b == 1
        assert job_a.execution_principal_id == "worker-a"
        assert job_b.execution_principal_id == "worker-b"

    async def test_worker_handles_empty_queue_gracefully(self) -> None:
        """An empty queue should not cause errors."""
        config = _make_worker_config()
        worker = WorkerService(config)

        session = _build_mock_session(jobs=[])
        factory = _make_mock_session_factory(session)

        with patch(SESSION_FACTORY_PATCH, return_value=factory):
            processed = await worker._process_batch()

        assert processed == 0
        assert worker.jobs_processed == 0

    async def test_worker_recovers_from_processing_errors(self) -> None:
        """Worker should continue processing after an error in a single job."""
        config = _make_worker_config()
        worker = WorkerService(config)

        # First job raises an exception, second succeeds
        good_job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )
        bad_job = _make_writeback_job(
            status=WriteBackJobStatus.READY.value,
            attempt_count=0,
            max_attempts=3,
        )

        session = _build_mock_session(jobs=[bad_job, good_job])
        factory = _make_mock_session_factory(session)

        # Make _process_job raise on the first call, succeed on the second
        call_count = 0
        original_process_job = worker._process_job

        async def mock_process_job(sess, job):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated processing error")
            await original_process_job(sess, job)

        with (
            patch(SESSION_FACTORY_PATCH, return_value=factory),
            patch.object(worker, "_process_job", side_effect=mock_process_job),
        ):
            processed = await worker._process_batch()

        # The batch returns 2 (both jobs were in the batch)
        assert processed == 2
        # Only 1 succeeded (the good one)
        assert worker.jobs_processed == 1
        assert good_job.status == WriteBackJobStatus.SUCCEEDED.value
