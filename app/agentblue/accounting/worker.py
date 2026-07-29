"""Background worker for processing approved write-back jobs.

Implements database-backed job leasing with SELECT FOR UPDATE SKIP LOCKED,
heartbeat, graceful shutdown, and stuck-job recovery.

The worker may NOT:
- approve work items
- create approvals
- bypass separation of duties
- promote ML models
- autonomously create write-backs from recommendations
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import WriteBackJobStatus
from agentblue.accounting.models import WriteBackAttempt, WriteBackJob
from agentblue.db.session import get_session_factory

logger = structlog.get_logger(__name__)


class WorkerConfig:
    """Configuration for the background worker."""

    def __init__(
        self,
        *,
        worker_id: str = "",
        heartbeat_interval: int = 30,
        lease_duration: int = 300,
        max_batch_size: int = 50,
        poll_interval: float = 5.0,
        stuck_threshold_minutes: int = 60,
    ) -> None:
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.heartbeat_interval = heartbeat_interval
        self.lease_duration = lease_duration
        self.max_batch_size = max_batch_size
        self.poll_interval = poll_interval
        self.stuck_threshold_minutes = stuck_threshold_minutes


class WorkerService:
    """Background worker that processes approved write-back jobs."""

    def __init__(self, config: WorkerConfig | None = None) -> None:
        self._config = config or WorkerConfig()
        self._running = False
        self._last_heartbeat: datetime | None = None
        self._jobs_processed = 0

    @property
    def worker_id(self) -> str:
        return self._config.worker_id

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_heartbeat(self) -> datetime | None:
        return self._last_heartbeat

    @property
    def jobs_processed(self) -> int:
        return self._jobs_processed

    async def start(self) -> None:
        """Start the worker loop."""
        self._running = True
        logger.info("worker_started", worker_id=self.worker_id)

        while self._running:
            try:
                await self._heartbeat()
                processed = await self._process_batch()
                if processed > 0:
                    logger.info(
                        "worker_batch_complete",
                        worker_id=self.worker_id,
                        jobs_processed=processed,
                    )
                await asyncio.sleep(self._config.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "worker_error",
                    worker_id=self.worker_id,
                    error=str(exc)[:200],
                )
                await asyncio.sleep(self._config.poll_interval * 2)

        logger.info("worker_stopped", worker_id=self.worker_id)

    async def stop(self) -> None:
        """Signal the worker to stop gracefully."""
        self._running = False
        logger.info("worker_stop_requested", worker_id=self.worker_id)

    async def _heartbeat(self) -> None:
        """Record worker heartbeat."""
        self._last_heartbeat = datetime.now(UTC)

    async def _process_batch(self) -> int:
        """Process a batch of eligible jobs. Returns count processed."""
        factory = get_session_factory()
        async with factory() as session:
            # Acquire jobs using SELECT FOR UPDATE SKIP LOCKED
            now = datetime.now(UTC)
            result = await session.execute(
                select(WriteBackJob)
                .where(
                    WriteBackJob.status.in_([
                        WriteBackJobStatus.READY.value,
                        WriteBackJobStatus.FAILED_RETRYABLE.value,
                    ]),
                    WriteBackJob.attempt_count < WriteBackJob.max_attempts,
                    (WriteBackJob.next_retry_at.is_(None))
                    | (WriteBackJob.next_retry_at <= now),
                )
                .order_by(WriteBackJob.created_at)
                .limit(self._config.max_batch_size)
                .with_for_update(skip_locked=True)
            )
            jobs = result.scalars().all()

            for job in jobs:
                try:
                    await self._process_job(session, job)
                    self._jobs_processed += 1
                except Exception as exc:
                    logger.error(
                        "worker_job_error",
                        worker_id=self.worker_id,
                        job_id=job.id,
                        error=str(exc)[:200],
                    )

            await session.commit()
            return len(jobs)

    async def _process_job(self, session: AsyncSession, job: WriteBackJob) -> None:
        """Process a single write-back job.

        Transitions: READY/FAILED_RETRYABLE -> IN_PROGRESS -> SUCCEEDED/FAILED
        """
        # Record attempt
        attempt = WriteBackAttempt(
            job_id=job.id,
            realm_id=job.realm_id,
            attempt_number=job.attempt_count + 1,
            status="IN_PROGRESS",
            execution_principal_id=self._config.worker_id,
        )
        session.add(attempt)

        # Transition to IN_PROGRESS
        job.status = WriteBackJobStatus.IN_PROGRESS.value
        job.started_at = datetime.now(UTC)
        job.attempt_count += 1
        job.execution_principal_id = self._config.worker_id

        # In shadow mode, simulate success
        attempt.status = "SIMULATED"
        attempt.duration_ms = 0
        job.status = WriteBackJobStatus.SUCCEEDED.value
        job.completed_at = datetime.now(UTC)

        logger.info(
            "worker_job_processed",
            worker_id=self._config.worker_id,
            job_id=job.id,
            status=job.status,
            attempt=job.attempt_count,
        )

    async def detect_stuck_jobs(self) -> list[dict[str, Any]]:
        """Detect jobs stuck in non-terminal states beyond the lease duration."""
        factory = get_session_factory()
        stuck_jobs: list[dict[str, Any]] = []

        async with factory() as session:
            threshold = datetime.now(UTC) - timedelta(
                minutes=self._config.stuck_threshold_minutes
            )
            result = await session.execute(
                select(WriteBackJob).where(
                    WriteBackJob.status.in_([
                        WriteBackJobStatus.IN_PROGRESS.value,
                        WriteBackJobStatus.VALIDATING.value,
                    ]),
                    WriteBackJob.updated_at < threshold,
                )
            )
            for job in result.scalars().all():
                stuck_jobs.append({
                    "job_id": job.id,
                    "status": job.status,
                    "updated_at": job.updated_at.isoformat(),
                    "attempt_count": job.attempt_count,
                })

        if stuck_jobs:
            logger.warning(
                "stuck_jobs_detected",
                count=len(stuck_jobs),
                worker_id=self._config.worker_id,
            )

        return stuck_jobs

    async def recover_orphan_jobs(self) -> int:
        """Recover jobs left in IN_PROGRESS by crashed workers."""
        factory = get_session_factory()
        recovered = 0

        async with factory() as session:
            threshold = datetime.now(UTC) - timedelta(
                minutes=self._config.stuck_threshold_minutes
            )
            result = await session.execute(
                update(WriteBackJob)
                .where(
                    WriteBackJob.status == WriteBackJobStatus.IN_PROGRESS.value,
                    WriteBackJob.updated_at < threshold,
                    WriteBackJob.attempt_count < WriteBackJob.max_attempts,
                )
                .values(
                    status=WriteBackJobStatus.FAILED_RETRYABLE.value,
                    failure_category="WORKER_CRASH",
                    failure_message="Recovered from orphaned state",
                )
                .returning(WriteBackJob.id)
            )
            recovered_ids = [row[0] for row in result.fetchall()]
            recovered = len(recovered_ids)
            await session.commit()

        if recovered > 0:
            logger.info("orphan_jobs_recovered", count=recovered)

        return recovered
