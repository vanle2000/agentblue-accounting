"""Categorization repository."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.models import (
    CategorizationDecision,
    CategorizationRecommendation,
    CategorizationRule,
    CategorizationRun,
    CategorizationTrainingLabel,
    TransactionCategorization,
    VendorMapping,
)

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CategorizationRepository:
    """Repository for categorization persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Rules ---

    async def get_active_rules(self, realm_id: str) -> list[CategorizationRule]:
        stmt = (
            select(CategorizationRule)
            .where(
                CategorizationRule.realm_id == realm_id,
                CategorizationRule.rule_status == "ACTIVE",
            )
            .order_by(CategorizationRule.precedence, CategorizationRule.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_rule(self, rule: CategorizationRule) -> CategorizationRule:
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def get_rule_by_id(self, realm_id: str, rule_id: str) -> CategorizationRule | None:
        stmt = select(CategorizationRule).where(
            CategorizationRule.realm_id == realm_id,
            CategorizationRule.id == rule_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # --- Categorization runs ---

    async def create_run(self, realm_id: str, engine_version: str) -> CategorizationRun:
        run = CategorizationRun(
            realm_id=realm_id,
            engine_version=engine_version,
            status="RUNNING",
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def complete_run(
        self,
        run_id: str,
        *,
        status: str = "COMPLETED",
        transaction_count: int = 0,
        recommended_count: int = 0,
        needs_review_count: int = 0,
        failed_count: int = 0,
        error_summary: str = "",
    ) -> None:
        stmt = select(CategorizationRun).where(CategorizationRun.id == run_id).with_for_update()
        result = await self._session.execute(stmt)
        run = result.scalar_one_or_none()
        if run:
            run.status = status
            run.completed_at = _utcnow()
            run.transaction_count = transaction_count
            run.recommended_count = recommended_count
            run.needs_review_count = needs_review_count
            run.failed_count = failed_count
            run.error_summary = error_summary or ""

    async def get_run(self, realm_id: str, run_id: str) -> CategorizationRun | None:
        stmt = select(CategorizationRun).where(
            CategorizationRun.realm_id == realm_id,
            CategorizationRun.id == run_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # --- Transaction categorization ---

    async def get_categorization(
        self, realm_id: str, categorization_id: str
    ) -> TransactionCategorization | None:
        stmt = select(TransactionCategorization).where(
            TransactionCategorization.realm_id == realm_id,
            TransactionCategorization.id == categorization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_categorization_by_txn(
        self, realm_id: str, transaction_id: str
    ) -> TransactionCategorization | None:
        stmt = select(TransactionCategorization).where(
            TransactionCategorization.realm_id == realm_id,
            TransactionCategorization.transaction_id == transaction_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_categorization(
        self,
        realm_id: str,
        transaction_id: str,
        transaction_quickbooks_id: str,
        *,
        status: str,
        recommended_account_quickbooks_id: str = "",
        confidence_score: Decimal = Decimal("0"),
        confidence_band: str = "NONE",
        recommendation_source: str = "FEATURE_RANKING",
        engine_version: str = "1.0.0",
        rule_id: str = "",
        explanation_summary: str = "",
        requires_review: bool = True,
    ) -> TransactionCategorization:
        existing = await self.get_categorization_by_txn(realm_id, transaction_id)
        if existing:
            existing.status = status
            existing.recommended_account_quickbooks_id = recommended_account_quickbooks_id
            existing.confidence_score = confidence_score
            existing.confidence_band = confidence_band
            existing.recommendation_source = recommendation_source
            existing.engine_version = engine_version
            existing.rule_id = rule_id
            existing.explanation_summary = explanation_summary
            existing.requires_review = requires_review
            existing.version += 1
            return existing

        cat = TransactionCategorization(
            realm_id=realm_id,
            transaction_id=transaction_id,
            transaction_quickbooks_id=transaction_quickbooks_id,
            status=status,
            recommended_account_quickbooks_id=recommended_account_quickbooks_id,
            confidence_score=confidence_score,
            confidence_band=confidence_band,
            recommendation_source=recommendation_source,
            engine_version=engine_version,
            rule_id=rule_id,
            explanation_summary=explanation_summary,
            requires_review=requires_review,
        )
        self._session.add(cat)
        await self._session.flush()
        return cat

    # --- Recommendations ---

    async def save_recommendations(
        self,
        categorization_id: str,
        realm_id: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        # Delete existing recommendations for this categorization
        stmt = select(CategorizationRecommendation).where(
            CategorizationRecommendation.categorization_id == categorization_id,
        )
        result = await self._session.execute(stmt)
        for rec in result.scalars().all():
            await self._session.delete(rec)

        for c in candidates:
            rec = CategorizationRecommendation(
                categorization_id=categorization_id,
                realm_id=realm_id,
                account_quickbooks_id=c["account_quickbooks_id"],
                rank=c["rank"],
                score=c["score"],
                confidence_band=c["confidence_band"],
                recommendation_source=c["source"],
                explanation=c.get("explanation", {}),
                feature_snapshot=c.get("feature_snapshot", {}),
                rule_id=c.get("rule_id") or None,
            )
            self._session.add(rec)

    # --- Decisions ---

    async def append_decision(
        self,
        categorization_id: str,
        realm_id: str,
        *,
        decision: str,
        reviewer: str,
        selected_account_id: str = "",
        previous_account_id: str = "",
        review_note: str = "",
        engine_version: str = "1.0.0",
        recommendation_snapshot: dict[str, Any] | None = None,
    ) -> CategorizationDecision:
        dec = CategorizationDecision(
            categorization_id=categorization_id,
            realm_id=realm_id,
            decision=decision,
            selected_account_id=selected_account_id or None,
            previous_account_id=previous_account_id or None,
            reviewer=reviewer,
            review_note=review_note or None,
            engine_version=engine_version,
            recommendation_snapshot=recommendation_snapshot or {},
        )
        self._session.add(dec)
        await self._session.flush()
        return dec

    # --- Vendor mappings ---

    async def get_vendor_mapping(
        self, realm_id: str, normalized_vendor: str
    ) -> VendorMapping | None:
        stmt = select(VendorMapping).where(
            VendorMapping.realm_id == realm_id,
            VendorMapping.normalized_vendor_name == normalized_vendor,
            VendorMapping.active.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_vendor_mapping(
        self,
        realm_id: str,
        normalized_vendor: str,
        raw_vendor: str,
        target_account_quickbooks_id: str,
        source: str = "APPROVED",
    ) -> VendorMapping:
        existing = await self.get_vendor_mapping(realm_id, normalized_vendor)
        if existing:
            if existing.target_account_quickbooks_id == target_account_quickbooks_id:
                existing.approval_count += 1
                return existing
            existing.rejection_count += 1
            return existing

        mapping = VendorMapping(
            realm_id=realm_id,
            normalized_vendor_name=normalized_vendor,
            raw_vendor_example=raw_vendor,
            target_account_quickbooks_id=target_account_quickbooks_id,
            source=source,
            approval_count=1,
        )
        self._session.add(mapping)
        await self._session.flush()
        return mapping

    # --- Training labels ---

    async def create_training_label(
        self,
        realm_id: str,
        transaction_id: str,
        transaction_quickbooks_id: str,
        selected_account_quickbooks_id: str,
        label_source: str,
        approved_by: str,
        engine_version: str,
        feature_snapshot: dict[str, Any],
    ) -> CategorizationTrainingLabel:
        label = CategorizationTrainingLabel(
            realm_id=realm_id,
            transaction_id=transaction_id,
            transaction_quickbooks_id=transaction_quickbooks_id,
            selected_account_quickbooks_id=selected_account_quickbooks_id,
            label_source=label_source,
            approved_by=approved_by,
            engine_version=engine_version,
            feature_snapshot=feature_snapshot,
        )
        self._session.add(label)
        await self._session.flush()
        return label

    # --- Review queue ---

    async def get_review_queue(
        self,
        realm_id: str,
        *,
        limit: int = 50,
    ) -> list[TransactionCategorization]:
        stmt = (
            select(TransactionCategorization)
            .where(
                TransactionCategorization.realm_id == realm_id,
                TransactionCategorization.requires_review.is_(True),
                TransactionCategorization.status.in_(["NEEDS_REVIEW", "RECOMMENDED"]),
            )
            .order_by(TransactionCategorization.created_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
