"""Categorization application services."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.constants import ENGINE_VERSION, MAX_TRANSACTIONS_PER_RUN
from agentblue.categorization.domain import CategorizationStatus
from agentblue.categorization.engine import CategorizationEngine
from agentblue.categorization.repository import CategorizationRepository

logger = structlog.get_logger(__name__)


class CategorizationService:
    """Application-level categorization operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = CategorizationRepository(session)
        self._engine = CategorizationEngine(session)
        self._session = session

    async def run_categorization(
        self,
        realm_id: str,
        transactions: list[dict[str, Any]],
        *,
        recategorize: bool = False,
    ) -> dict[str, Any]:
        """Run categorization on a set of transactions."""
        run = await self._repo.create_run(realm_id, ENGINE_VERSION)

        counts = {
            "total": 0,
            "recommended": 0,
            "needs_review": 0,
            "failed": 0,
        }

        for txn in transactions[:MAX_TRANSACTIONS_PER_RUN]:
            txn_id = str(txn.get("id", ""))
            qb_id = str(txn.get("quickbooks_id", ""))
            try:
                result = await self._engine.categorize_transaction(
                    realm_id,
                    txn,
                    txn_id,
                    recategorize=recategorize,
                )
                await self._engine.persist_result(realm_id, result, qb_id)
                counts["total"] += 1

                if result.status == CategorizationStatus.RECOMMENDED:
                    counts["recommended"] += 1
                elif result.status == CategorizationStatus.NEEDS_REVIEW:
                    counts["needs_review"] += 1

            except Exception as exc:
                counts["failed"] += 1
                logger.warning(
                    "categorization_failed",
                    transaction_id=txn_id,
                    error=str(exc)[:200],
                )

        await self._repo.complete_run(
            run.id,
            status="COMPLETED",
            transaction_count=counts["total"],
            recommended_count=counts["recommended"],
            needs_review_count=counts["needs_review"],
            failed_count=counts["failed"],
        )
        await self._session.commit()

        return {"run_id": run.id, **counts}
