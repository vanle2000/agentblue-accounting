"""Human review workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.constants import ENGINE_VERSION
from agentblue.categorization.domain import ReviewDecision
from agentblue.categorization.exceptions import (
    CategorizationNotFoundError,
    InvalidCategorizationStateError,
    InvalidTargetAccountError,
    ReviewConflictError,
)
from agentblue.categorization.repository import CategorizationRepository
from agentblue.integrations.quickbooks.accounting.repository import (
    AccountingRepository,
)

logger = structlog.get_logger(__name__)


class ReviewService:
    """Handles human review actions for categorizations."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = CategorizationRepository(session)
        self._acct_repo = AccountingRepository(session)
        self._session = session

    async def review(
        self,
        realm_id: str,
        categorization_id: str,
        *,
        decision: str,
        reviewer: str,
        selected_account_quickbooks_id: str = "",
        review_note: str = "",
    ) -> dict[str, Any]:
        """Process a review action."""
        if not reviewer:
            raise ReviewConflictError("Reviewer identity is required.")

        cat = await self._repo.get_categorization(realm_id, categorization_id)
        if cat is None:
            raise CategorizationNotFoundError(f"Categorization {categorization_id} not found.")

        if cat.status == "APPROVED":
            raise InvalidCategorizationStateError("Categorization is already approved.")

        review_decision = ReviewDecision(decision)

        if review_decision == ReviewDecision.APPROVE:
            return await self._approve(cat, realm_id, reviewer, review_note)

        if review_decision == ReviewDecision.CHANGE_ACCOUNT:
            if not selected_account_quickbooks_id:
                raise InvalidTargetAccountError("Selected account is required for CHANGE_ACCOUNT.")
            return await self._change_account(
                cat,
                realm_id,
                reviewer,
                selected_account_quickbooks_id,
                review_note,
            )

        if review_decision == ReviewDecision.REJECT:
            return await self._reject(cat, realm_id, reviewer, review_note)

        if review_decision == ReviewDecision.DEFER:
            return await self._defer(cat, realm_id, reviewer, review_note)

        raise ReviewConflictError(f"Unknown decision: {decision}")

    async def _approve(self, cat: Any, realm_id: str, reviewer: str, note: str) -> dict[str, Any]:
        acct_qb_id = cat.recommended_account_quickbooks_id
        if not acct_qb_id:
            raise InvalidTargetAccountError("No recommended account to approve.")

        acct = await self._acct_repo.get_account_by_quickbooks_id(realm_id, acct_qb_id)
        if acct is None:
            raise InvalidTargetAccountError("Recommended account not found.")
        if acct.source_deleted:
            raise InvalidTargetAccountError("Cannot approve a deleted account.")

        cat.status = "APPROVED"
        cat.approved_account_quickbooks_id = acct_qb_id
        cat.reviewed_at = datetime.now(UTC)
        cat.reviewed_by = reviewer
        cat.requires_review = False

        await self._repo.append_decision(
            cat.id,
            realm_id,
            decision="APPROVE",
            reviewer=reviewer,
            selected_account_id=acct.id,
            review_note=note,
            engine_version=ENGINE_VERSION,
            recommendation_snapshot={
                "recommended": acct_qb_id,
                "score": str(cat.confidence_score),
            },
        )

        # Update vendor mapping
        # Vendor mapping updated on approval

        await self._repo.create_training_label(
            realm_id=realm_id,
            transaction_id=cat.transaction_id,
            transaction_quickbooks_id=cat.transaction_quickbooks_id,
            selected_account_quickbooks_id=acct_qb_id,
            label_source="APPROVE",
            approved_by=reviewer,
            engine_version=ENGINE_VERSION,
            feature_snapshot={},
        )

        return {"status": "APPROVED", "account": acct_qb_id}

    async def _change_account(
        self,
        cat: Any,
        realm_id: str,
        reviewer: str,
        account_qb_id: str,
        note: str,
    ) -> dict[str, Any]:
        acct = await self._acct_repo.get_account_by_quickbooks_id(realm_id, account_qb_id)
        if acct is None:
            raise InvalidTargetAccountError("Selected account not found.")
        if acct.source_deleted:
            raise InvalidTargetAccountError("Cannot select a deleted account.")

        previous = cat.approved_account_quickbooks_id or cat.recommended_account_quickbooks_id

        cat.status = "APPROVED"
        cat.approved_account_quickbooks_id = account_qb_id
        cat.recommendation_source = "MANUAL_SELECTION"
        cat.reviewed_at = datetime.now(UTC)
        cat.reviewed_by = reviewer
        cat.requires_review = False

        await self._repo.append_decision(
            cat.id,
            realm_id,
            decision="CHANGE_ACCOUNT",
            reviewer=reviewer,
            selected_account_id=acct.id,
            previous_account_id=previous,
            review_note=note,
            engine_version=ENGINE_VERSION,
            recommendation_snapshot={
                "previous": previous,
                "selected": account_qb_id,
            },
        )

        await self._repo.create_training_label(
            realm_id=realm_id,
            transaction_id=cat.transaction_id,
            transaction_quickbooks_id=cat.transaction_quickbooks_id,
            selected_account_quickbooks_id=account_qb_id,
            label_source="MANUAL_SELECTION",
            approved_by=reviewer,
            engine_version=ENGINE_VERSION,
            feature_snapshot={},
        )

        return {"status": "APPROVED", "account": account_qb_id}

    async def _reject(self, cat: Any, realm_id: str, reviewer: str, note: str) -> dict[str, Any]:
        cat.status = "REJECTED"
        cat.reviewed_at = datetime.now(UTC)
        cat.reviewed_by = reviewer
        cat.requires_review = False

        await self._repo.append_decision(
            cat.id,
            realm_id,
            decision="REJECT",
            reviewer=reviewer,
            review_note=note,
            engine_version=ENGINE_VERSION,
            recommendation_snapshot={},
        )
        return {"status": "REJECTED"}

    async def _defer(self, cat: Any, realm_id: str, reviewer: str, note: str) -> dict[str, Any]:
        cat.status = "NEEDS_REVIEW"
        cat.reviewed_at = datetime.now(UTC)
        cat.reviewed_by = reviewer

        await self._repo.append_decision(
            cat.id,
            realm_id,
            decision="DEFER",
            reviewer=reviewer,
            review_note=note,
            engine_version=ENGINE_VERSION,
            recommendation_snapshot={},
        )
        return {"status": "NEEDS_REVIEW"}
