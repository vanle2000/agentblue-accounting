"""Human review workflow with approve-and-apply."""

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
from agentblue.integrations.quickbooks.writeback.exceptions import (
    StaleSyncTokenError,
    UnsupportedEntityTypeError,
)
from agentblue.integrations.quickbooks.writeback.service import WriteBackService

logger = structlog.get_logger(__name__)


class ReviewService:
    """Handles human review actions for categorizations."""

    def __init__(self, session: AsyncSession, api_client: Any = None) -> None:
        self._repo = CategorizationRepository(session)
        self._acct_repo = AccountingRepository(session)
        self._session = session
        self._api_client = api_client

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
        """Process a review action (without write-back)."""
        if not reviewer:
            raise ReviewConflictError("Reviewer identity is required.")

        cat = await self._repo.get_categorization(realm_id, categorization_id)
        if cat is None:
            raise CategorizationNotFoundError(f"Categorization {categorization_id} not found.")

        if cat.status in ("APPROVED", "APPLIED"):
            raise InvalidCategorizationStateError("Categorization is already approved/applied.")

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

    async def approve_and_apply(
        self,
        realm_id: str,
        categorization_id: str,
        *,
        reviewer: str,
        selected_account_quickbooks_id: str = "",
        expected_categorization_version: int = 0,
        expected_transaction_sync_token: str = "",
        review_note: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Approve and apply categorization to QuickBooks.

        Two-phase: approve locally, then apply to QuickBooks.
        """
        if not reviewer:
            raise ReviewConflictError("Reviewer identity is required.")

        cat = await self._repo.get_categorization(realm_id, categorization_id)
        if cat is None:
            raise CategorizationNotFoundError(f"Categorization {categorization_id} not found.")

        if cat.status in ("APPROVED", "APPLIED", "APPLYING"):
            # Check idempotency
            if idempotency_key:
                existing_app = await self._repo.get_application_by_idempotency_key(idempotency_key)
                if existing_app:
                    return {
                        "status": existing_app.status,
                        "application_id": existing_app.id,
                        "idempotent": True,
                    }
            raise InvalidCategorizationStateError(f"Categorization already in state {cat.status}.")

        # Validate version
        if expected_categorization_version and expected_categorization_version != cat.version:
            raise ReviewConflictError(
                f"Version mismatch: expected {expected_categorization_version}, got {cat.version}"
            )

        # Validate selected account
        acct_qb_id = selected_account_quickbooks_id or cat.recommended_account_quickbooks_id
        if not acct_qb_id:
            raise InvalidTargetAccountError("No account selected.")

        acct = await self._acct_repo.get_account_by_quickbooks_id(realm_id, acct_qb_id)
        if acct is None:
            raise InvalidTargetAccountError("Selected account not found.")
        if acct.source_deleted:
            raise InvalidTargetAccountError("Cannot select a deleted account.")

        # Phase 1: Record approval locally
        cat.status = "APPROVED"
        cat.approved_account_quickbooks_id = acct_qb_id
        cat.reviewed_at = datetime.now(UTC)
        cat.reviewed_by = reviewer
        cat.approved_at = datetime.now(UTC)
        cat.requires_review = False
        cat.version += 1

        await self._repo.append_decision(
            cat.id,
            realm_id,
            decision="APPROVE",
            reviewer=reviewer,
            selected_account_id=acct.id,
            review_note=review_note,
            engine_version=ENGINE_VERSION,
            categorization_version=cat.version,
            recommendation_snapshot={
                "recommended": cat.recommended_account_quickbooks_id,
                "selected": acct_qb_id,
                "score": str(cat.confidence_score),
            },
        )
        await self._session.commit()

        result: dict[str, Any] = {
            "status": "APPROVED",
            "categorization_id": cat.id,
            "account": acct_qb_id,
        }

        # Phase 2: Apply to QuickBooks if supported
        can_writeback = WriteBackService.is_supported_type(cat.transaction_type or "")

        if can_writeback and idempotency_key:
            cat.status = "APPLYING"
            await self._session.commit()

            writeback = WriteBackService(self._session, self._api_client)
            try:
                wb_result = await writeback.apply_categorization(
                    realm_id=realm_id,
                    transaction_quickbooks_id=cat.transaction_quickbooks_id,
                    transaction_type=cat.transaction_type or "Purchase",
                    selected_account_quickbooks_id=acct_qb_id,
                    reviewed_sync_token=cat.source_sync_token or "",
                    reviewed_transaction_hash=cat.source_transaction_hash or "",
                    approved_by=reviewer,
                    idempotency_key=idempotency_key,
                )
                result["writeback"] = wb_result

                if wb_result.get("status") == "SUCCESS" or wb_result.get("status") == "SIMULATED":
                    cat.status = "APPLIED"
                else:
                    cat.status = "APPLY_FAILED"

            except StaleSyncTokenError as exc:
                cat.status = "STALE"
                result["error"] = str(exc)
            except UnsupportedEntityTypeError as exc:
                cat.status = "APPROVED"  # Revert to approved, can't apply
                result["error"] = str(exc)
            except Exception as exc:
                cat.status = "APPLY_FAILED"
                result["error"] = str(exc)[:200]
                logger.warning(
                    "apply_failed",
                    categorization_id=cat.id,
                    error=str(exc)[:200],
                )

            await self._session.commit()

        # Create training label with write-back status
        # Use distinct label_source so failed/stale writes are distinguishable
        label_source = "APPROVE"
        if cat.status == "APPLIED":
            label_source = "APPROVE_VERIFIED"
        elif cat.status == "APPLY_FAILED":
            label_source = "APPROVE_APPLY_FAILED"
        elif cat.status == "STALE":
            label_source = "APPROVE_STALE"

        await self._repo.create_training_label(
            realm_id=realm_id,
            transaction_id=cat.transaction_id,
            transaction_quickbooks_id=cat.transaction_quickbooks_id,
            selected_account_quickbooks_id=acct_qb_id,
            label_source=label_source,
            approved_by=reviewer,
            engine_version=ENGINE_VERSION,
            feature_snapshot={},
        )
        await self._session.commit()

        return result

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
        cat.approved_at = datetime.now(UTC)
        cat.requires_review = False

        await self._repo.append_decision(
            cat.id,
            realm_id,
            decision="APPROVE",
            reviewer=reviewer,
            selected_account_id=acct.id,
            review_note=note,
            engine_version=ENGINE_VERSION,
            categorization_version=cat.version,
            recommendation_snapshot={
                "recommended": acct_qb_id,
                "score": str(cat.confidence_score),
            },
        )

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
        cat.approved_at = datetime.now(UTC)
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
            categorization_version=cat.version,
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
            categorization_version=cat.version,
            recommendation_snapshot={},
        )
        return {"status": "REJECTED"}

    async def _defer(self, cat: Any, realm_id: str, reviewer: str, note: str) -> dict[str, Any]:
        cat.status = "DEFERRED"
        cat.reviewed_at = datetime.now(UTC)
        cat.reviewed_by = reviewer

        await self._repo.append_decision(
            cat.id,
            realm_id,
            decision="DEFER",
            reviewer=reviewer,
            review_note=note,
            engine_version=ENGINE_VERSION,
            categorization_version=cat.version,
            recommendation_snapshot={},
        )
        return {"status": "DEFERRED"}
