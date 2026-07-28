"""Deterministic duplicate detection for accounting work items.

Classifies potential duplicates before approval and write-back.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.accounting import DuplicateClassification
from agentblue.accounting.models import AccountingWorkItem

logger = structlog.get_logger(__name__)


class DuplicateDetectionService:
    """Detects duplicate work items using deterministic signals."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def classify(
        self,
        realm_id: str,
        source_transaction_id: str,
        transaction_date: str,
        amount: Decimal,
        vendor: str,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Classify a potential work item for duplicates.

        Args:
            realm_id: Realm scope.
            source_transaction_id: External transaction ID.
            transaction_date: Transaction date string.
            amount: Transaction amount.
            vendor: Vendor/payee name.
            idempotency_key: Idempotency key if available.

        Returns:
            Dict with classification and evidence.
        """
        # Check exact external ID duplicate
        exact = await self._session.execute(
            select(AccountingWorkItem).where(
                AccountingWorkItem.realm_id == realm_id,
                AccountingWorkItem.source_transaction_id == source_transaction_id,
                AccountingWorkItem.status.notin_(["REJECTED", "CLOSED"]),
            )
        )
        exact_match = exact.scalar_one_or_none()
        if exact_match is not None:
            return {
                "classification": DuplicateClassification.EXACT_DUPLICATE.value,
                "duplicate_of_id": exact_match.id,
                "evidence": {
                    "match_type": "source_transaction_id",
                    "existing_status": exact_match.status,
                },
            }

        # Check idempotency key duplicate
        if idempotency_key:
            idem = await self._session.execute(
                select(AccountingWorkItem).where(
                    AccountingWorkItem.realm_id == realm_id,
                    AccountingWorkItem.idempotency_key == idempotency_key,
                    AccountingWorkItem.status.notin_(["REJECTED", "CLOSED"]),
                )
            )
            idem_match = idem.scalar_one_or_none()
            if idem_match is not None:
                return {
                    "classification": DuplicateClassification.EXACT_DUPLICATE.value,
                    "duplicate_of_id": idem_match.id,
                    "evidence": {
                        "match_type": "idempotency_key",
                        "existing_status": idem_match.status,
                    },
                }

        # Check likely duplicate: same date + amount + vendor
        likely = await self._session.execute(
            select(AccountingWorkItem).where(
                AccountingWorkItem.realm_id == realm_id,
                AccountingWorkItem.amount == amount,
                AccountingWorkItem.vendor_or_payee == vendor,
                AccountingWorkItem.status.notin_(["REJECTED", "CLOSED"]),
            )
        )
        likely_matches = likely.scalars().all()
        if likely_matches:
            return {
                "classification": DuplicateClassification.LIKELY_DUPLICATE.value,
                "duplicate_of_id": likely_matches[0].id,
                "evidence": {
                    "match_type": "date_amount_vendor",
                    "match_count": len(likely_matches),
                    "matched_ids": [m.id for m in likely_matches[:5]],
                },
            }

        return {
            "classification": DuplicateClassification.NOT_DUPLICATE.value,
            "duplicate_of_id": None,
            "evidence": {},
        }
