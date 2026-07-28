"""Post-write reconciliation service.

Verifies that QuickBooks write-back results match the approved state.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import ReconciliationStatus
from agentblue.accounting.models import ReconciliationResult, WriteBackJob

logger = structlog.get_logger(__name__)


class ReconciliationService:
    """Post-write reconciliation comparing approved vs observed state."""

    def __init__(self, session: AsyncSession, api_client: Any = None) -> None:
        self._session = session
        self._api_client = api_client

    async def reconcile(
        self,
        job_id: str,
        realm_id: str,
        approved_state: dict[str, Any],
        observed_state: dict[str, Any],
    ) -> ReconciliationResult:
        """Reconcile a write-back job against observed QuickBooks state.

        Args:
            job_id: Write-back job ID.
            realm_id: Realm context.
            approved_state: The state that was approved for write-back.
            observed_state: The actual state observed in QuickBooks.

        Returns:
            ReconciliationResult with status and differences.
        """
        result_qb = await self._session.execute(
            select(WriteBackJob).where(WriteBackJob.id == job_id)
        )
        job = result_qb.scalar_one_or_none()

        work_item_id = job.work_item_id if job else ""

        # Compare field by field
        differences: list[dict[str, Any]] = []
        all_keys = set(approved_state.keys()) | set(observed_state.keys())

        for key in sorted(all_keys):
            approved_val = approved_state.get(key)
            observed_val = observed_state.get(key)
            if approved_val != observed_val:
                differences.append({
                    "field": key,
                    "approved": str(approved_val),
                    "observed": str(observed_val),
                })

        # Determine status
        if not differences:
            status = ReconciliationStatus.MATCHED.value
        elif not observed_state:
            status = ReconciliationStatus.TARGET_MISSING.value
        else:
            status = ReconciliationStatus.MISMATCH.value

        recon = ReconciliationResult(
            job_id=job_id,
            work_item_id=work_item_id,
            realm_id=realm_id,
            status=status,
            approved_state=approved_state,
            observed_state=observed_state,
            differences=differences,
            external_transaction_id=str(observed_state.get("Id", "")),
            external_sync_token=str(observed_state.get("SyncToken", "")),
            reconciled_by="system",
        )
        self._session.add(recon)
        await self._session.flush()

        # Update job reconciliation status
        if job:
            job.reconciliation_status = status
            await self._session.flush()

        logger.info(
            "reconciliation_complete",
            job_id=job_id,
            status=status,
            difference_count=len(differences),
        )

        return recon
