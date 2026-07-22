"""Categorization engine — orchestration."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.constants import ENGINE_VERSION
from agentblue.categorization.domain import (
    CategorizationResult,
    CategorizationStatus,
    ConfidenceBand,
    RecommendationCandidate,
    RecommendationSource,
)
from agentblue.categorization.features import extract_features
from agentblue.categorization.repository import CategorizationRepository
from agentblue.categorization.rules import evaluate_rule
from agentblue.categorization.scoring import calculate_score, score_to_band

logger = structlog.get_logger(__name__)

_ELIGIBLE_TYPES = {
    "Purchase",
    "Bill",
    "Deposit",
    "Transfer",
    "JournalEntry",
    "Invoice",
    "SalesReceipt",
    "Payment",
    "CreditMemo",
    "RefundReceipt",
    "BillPayment",
    "VendorCredit",
}


def _rule_to_dict(rule: Any) -> dict[str, object]:
    """Convert a CategorizationRule ORM object to a dict for evaluate_rule."""
    return {
        "rule_type": str(rule.rule_type),
        "conditions": dict(rule.conditions),
        "target_account_quickbooks_id": str(rule.target_account_quickbooks_id),
        "precedence": int(rule.precedence),
        "id": str(rule.id),
        "stop_processing": bool(rule.stop_processing),
    }


class CategorizationEngine:
    """Orchestrates transaction categorization."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = CategorizationRepository(session)
        self._session = session

    async def categorize_transaction(
        self,
        realm_id: str,
        transaction: dict[str, Any],
        transaction_id: str,
        *,
        account_candidates: list[dict[str, Any]] | None = None,
        recategorize: bool = False,
    ) -> CategorizationResult:
        """Categorize a single transaction."""
        txn_type = transaction.get("entity_type", "")
        if txn_type not in _ELIGIBLE_TYPES:
            return CategorizationResult(
                transaction_id=transaction_id,
                status=CategorizationStatus.PENDING,
            )

        if not recategorize:
            existing = await self._repo.get_categorization_by_txn(realm_id, transaction_id)
            if existing and existing.status == "APPROVED":
                return CategorizationResult(
                    transaction_id=transaction_id,
                    status=CategorizationStatus.APPROVED,
                    recommended_account_quickbooks_id=(
                        existing.approved_account_quickbooks_id or ""
                    ),
                    confidence_score=existing.confidence_score,
                    confidence_band=ConfidenceBand(existing.confidence_band),
                )

        features = extract_features(realm_id, transaction, transaction_id)

        # Evaluate rules
        rules = await self._repo.get_active_rules(realm_id)
        rule_matches: list[dict[str, Any]] = []
        for rule in rules:
            rule_dict = _rule_to_dict(rule)
            matched, evidence = evaluate_rule(
                rule_dict,
                features.normalized_vendor,
                features.normalized_description,
                features.normalized_memo,
                features.transaction_type,
                features.amount,
            )
            if matched:
                rule_matches.append({"rule": rule_dict, "evidence": evidence})
                if rule.stop_processing:
                    break

        # Check vendor history
        vendor_mapping = None
        if features.normalized_vendor:
            vendor_mapping = await self._repo.get_vendor_mapping(
                realm_id, features.normalized_vendor
            )

        # Build candidates
        candidates: list[RecommendationCandidate] = []

        for match in rule_matches:
            r = match["rule"]
            candidates.append(
                RecommendationCandidate(
                    account_quickbooks_id=str(r["target_account_quickbooks_id"]),
                    account_id="",
                    rank=len(candidates) + 1,
                    score=Decimal("0"),
                    confidence_band=ConfidenceBand.NONE,
                    source=RecommendationSource.USER_RULE,
                    explanation={"evidence": match["evidence"]},
                    rule_id=str(r.get("id", "")),
                )
            )

        if vendor_mapping and vendor_mapping.approval_count >= 2:
            already = any(
                c.account_quickbooks_id == vendor_mapping.target_account_quickbooks_id
                for c in candidates
            )
            if not already:
                candidates.append(
                    RecommendationCandidate(
                        account_quickbooks_id=vendor_mapping.target_account_quickbooks_id,
                        account_id="",
                        rank=len(candidates) + 1,
                        score=Decimal("0"),
                        confidence_band=ConfidenceBand.NONE,
                        source=RecommendationSource.APPROVED_HISTORY,
                        explanation={
                            "vendor_approvals": vendor_mapping.approval_count,
                            "vendor_rejections": vendor_mapping.rejection_count,
                        },
                    )
                )

        # Score candidates
        scored: list[RecommendationCandidate] = []
        targets = {str(m["rule"]["target_account_quickbooks_id"]) for m in rule_matches}
        has_conflict = len(rule_matches) > 1 and len(targets) > 1

        for c in candidates:
            has_rule = c.source in (
                RecommendationSource.USER_RULE,
                RecommendationSource.SYSTEM_RULE,
            )
            hist_score = Decimal("0")
            if c.source == RecommendationSource.APPROVED_HISTORY and vendor_mapping:
                total = vendor_mapping.approval_count + vendor_mapping.rejection_count
                ratio = vendor_mapping.approval_count / max(1, total)
                hist_score = Decimal(str(ratio))

            score, components = calculate_score(
                user_rule_match=has_rule,
                vendor_history_score=hist_score,
                account_compatible=True,
                has_conflict=has_conflict,
            )
            c.score = score
            c.confidence_band = score_to_band(score)
            c.explanation["score_components"] = components
            scored.append(c)

        scored.sort(key=lambda x: (-float(x.score), x.rank))
        for i, c in enumerate(scored):
            c.rank = i + 1

        if not scored:
            return CategorizationResult(
                transaction_id=transaction_id,
                status=CategorizationStatus.NEEDS_REVIEW,
                confidence_band=ConfidenceBand.NONE,
                explanation={"reason": "NO_VALID_CANDIDATE"},
                requires_review=True,
            )

        best = scored[0]
        status = CategorizationStatus.NEEDS_REVIEW
        if best.confidence_band == ConfidenceBand.HIGH:
            status = CategorizationStatus.RECOMMENDED

        return CategorizationResult(
            transaction_id=transaction_id,
            status=status,
            recommended_account_quickbooks_id=best.account_quickbooks_id,
            confidence_score=best.score,
            confidence_band=best.confidence_band,
            source=best.source,
            candidates=scored,
            explanation=best.explanation,
            requires_review=True,
        )

    async def persist_result(
        self,
        realm_id: str,
        result: CategorizationResult,
        transaction_quickbooks_id: str,
    ) -> str:
        """Persist a categorization result. Returns categorization ID."""
        cat = await self._repo.upsert_categorization(
            realm_id=realm_id,
            transaction_id=result.transaction_id,
            transaction_quickbooks_id=transaction_quickbooks_id,
            status=result.status.value,
            recommended_account_quickbooks_id=result.recommended_account_quickbooks_id,
            confidence_score=result.confidence_score,
            confidence_band=result.confidence_band.value,
            recommendation_source=result.source.value,
            engine_version=ENGINE_VERSION,
            explanation_summary=str(result.explanation.get("reason", "")),
            requires_review=result.requires_review,
        )

        if result.candidates:
            candidate_dicts = [
                {
                    "account_quickbooks_id": c.account_quickbooks_id,
                    "rank": c.rank,
                    "score": c.score,
                    "confidence_band": c.confidence_band.value,
                    "source": c.source.value,
                    "explanation": c.explanation,
                    "feature_snapshot": {},
                    "rule_id": c.rule_id,
                }
                for c in result.candidates
            ]
            await self._repo.save_recommendations(cat.id, realm_id, candidate_dicts)

        return cat.id
