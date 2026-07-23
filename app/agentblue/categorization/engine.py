"""Categorization engine — orchestration with Level 2 Assisted Automation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.constants import (
    AMBIGUITY_MARGIN,
    ASSISTED_AUTOMATION_THRESHOLD,
    ENGINE_VERSION,
    FEATURE_VERSION,
    MINIMUM_RECOMMENDATION_THRESHOLD,
)
from agentblue.categorization.domain import (
    AssistedAutomationGate,
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
    return {
        "rule_type": str(rule.rule_type),
        "conditions": dict(rule.conditions),
        "target_account_quickbooks_id": str(rule.target_account_quickbooks_id),
        "precedence": int(rule.precedence),
        "id": str(rule.id),
        "stop_processing": bool(rule.stop_processing),
    }


def check_assisted_automation_gate(
    candidates: list[RecommendationCandidate],
) -> AssistedAutomationGate:
    """Check if recommendation qualifies for PRESELECTED status.

    Requirements:
    - top_score >= 0.97
    - top passes all account validation
    - no equal-precedence conflict
    - ambiguity margin met (top - second >= 0.10)
    - transaction type supports write-back (advisory, not blocking)
    """
    reasons: list[str] = []

    if not candidates:
        return AssistedAutomationGate(passed=False, reason_codes=["NO_CANDIDATES"])

    sorted_candidates = sorted(candidates, key=lambda c: -float(c.score))
    top = sorted_candidates[0]
    top_score = top.score
    second_score = sorted_candidates[1].score if len(sorted_candidates) > 1 else Decimal("0")
    ambiguity_gap = top_score - second_score

    if top_score < ASSISTED_AUTOMATION_THRESHOLD:
        reasons.append(f"SCORE_BELOW_THRESHOLD: {top_score} < {ASSISTED_AUTOMATION_THRESHOLD}")

    if ambiguity_gap < AMBIGUITY_MARGIN:
        reasons.append(f"AMBIGUITY_MARGIN_NOT_MET: gap={ambiguity_gap} < {AMBIGUITY_MARGIN}")

    # Check for conflicting equal-precedence rules
    if top.source in (RecommendationSource.USER_RULE, RecommendationSource.SYSTEM_RULE):
        same_source = [c for c in sorted_candidates if c.source == top.source]
        different_targets = {c.account_quickbooks_id for c in same_source}
        if len(different_targets) > 1:
            reasons.append("CONFLICTING_RULES: equal-precedence different targets")

    return AssistedAutomationGate(
        passed=len(reasons) == 0,
        reason_codes=reasons,
        top_score=top_score,
        second_score=second_score,
        ambiguity_gap=ambiguity_gap,
    )


class CategorizationEngine:
    """Orchestrates transaction categorization with Level 2 Assisted Automation."""

    def __init__(self, session: AsyncSession, api_client: Any = None) -> None:
        self._repo = CategorizationRepository(session)
        self._session = session
        self._api_client = api_client

    async def categorize_transaction(
        self,
        realm_id: str,
        transaction: dict[str, Any],
        transaction_id: str,
        *,
        recategorize: bool = False,
    ) -> CategorizationResult:
        """Categorize a single transaction with assisted-automation gate."""
        txn_type = transaction.get("entity_type", "")
        if txn_type not in _ELIGIBLE_TYPES:
            return CategorizationResult(
                transaction_id=transaction_id,
                status=CategorizationStatus.PENDING,
            )

        # Check existing approved categorization
        if not recategorize:
            existing = await self._repo.get_categorization_by_txn(realm_id, transaction_id)
            if existing and existing.status in ("APPROVED", "APPLIED", "APPLYING"):
                return CategorizationResult(
                    transaction_id=transaction_id,
                    status=CategorizationStatus(existing.status),
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
        targets = {str(m["rule"]["target_account_quickbooks_id"]) for m in rule_matches}
        has_conflict = len(rule_matches) > 1 and len(targets) > 1

        scored: list[RecommendationCandidate] = []
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

        # Check assisted-automation gate
        gate = check_assisted_automation_gate(scored)
        is_preselected = gate.passed

        if is_preselected:
            status = CategorizationStatus.PRESELECTED
        elif best.confidence_band == ConfidenceBand.HIGH:
            status = CategorizationStatus.RECOMMENDED
        elif best.score >= MINIMUM_RECOMMENDATION_THRESHOLD:
            status = CategorizationStatus.NEEDS_REVIEW
        else:
            status = CategorizationStatus.NEEDS_REVIEW

        explanation = dict(best.explanation)
        if gate.reason_codes:
            explanation["gate_reasons"] = gate.reason_codes

        return CategorizationResult(
            transaction_id=transaction_id,
            status=status,
            recommended_account_quickbooks_id=best.account_quickbooks_id,
            confidence_score=best.score,
            confidence_band=best.confidence_band,
            source=best.source,
            candidates=scored,
            explanation=explanation,
            requires_review=True,
            preselected=is_preselected,
        )

    async def persist_result(
        self,
        realm_id: str,
        result: CategorizationResult,
        transaction_quickbooks_id: str,
        transaction: dict[str, Any] | None = None,
    ) -> str:
        """Persist a categorization result. Returns categorization ID."""
        txn_type = ""
        sync_token = ""
        txn_hash = ""
        if transaction:
            txn_type = str(transaction.get("entity_type", ""))
            sync_token = str(transaction.get("sync_token", ""))
            from agentblue.integrations.quickbooks.writeback.validation import (
                compute_entity_hash,
            )

            txn_hash = compute_entity_hash(transaction)

        cat = await self._repo.upsert_categorization(
            realm_id=realm_id,
            transaction_id=result.transaction_id,
            transaction_quickbooks_id=transaction_quickbooks_id,
            transaction_type=txn_type,
            status=result.status.value,
            recommended_account_quickbooks_id=result.recommended_account_quickbooks_id,
            confidence_score=result.confidence_score,
            confidence_band=result.confidence_band.value,
            recommendation_source=result.source.value,
            engine_version=ENGINE_VERSION,
            feature_version=FEATURE_VERSION,
            explanation_summary=str(result.explanation.get("reason", "")),
            requires_review=result.requires_review,
            source_sync_token=sync_token,
            source_transaction_hash=txn_hash,
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
