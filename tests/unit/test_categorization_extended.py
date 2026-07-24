"""Extended tests for categorization module.

Covers review workflow, engine orchestration, feature extraction,
repository persistence, application services, and router endpoints.
All tests use mocked async sessions — no database required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentblue.categorization.domain import (
    CategorizationResult,
    CategorizationStatus,
    ConfidenceBand,
    RecommendationCandidate,
    RecommendationSource,
)
from agentblue.categorization.engine import CategorizationEngine, check_assisted_automation_gate
from agentblue.categorization.exceptions import (
    CategorizationNotFoundError,
    InvalidCategorizationStateError,
    InvalidTargetAccountError,
    ReviewConflictError,
)
from agentblue.categorization.features import extract_features
from agentblue.categorization.repository import CategorizationRepository
from agentblue.categorization.review import ReviewService
from agentblue.categorization.services import CategorizationService

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_session() -> AsyncMock:
    """Create a mock AsyncSession with execute/add/flush/commit."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.delete = AsyncMock()
    return session


def _make_cat(
    *,
    cat_id: str = "cat-001",
    realm_id: str = "realm-1",
    transaction_id: str = "txn-001",
    transaction_quickbooks_id: str = "qb-txn-001",
    transaction_type: str = "Purchase",
    status: str = "NEEDS_REVIEW",
    recommended_account_quickbooks_id: str = "acct-500",
    approved_account_quickbooks_id: str | None = None,
    confidence_score: Decimal = Decimal("0.850"),
    confidence_band: str = "HIGH",
    version: int = 1,
    requires_review: bool = True,
    source_sync_token: str = "5",
    source_transaction_hash: str = "hash-abc",
    reviewed_by: str | None = None,
    recommendation_source: str = "USER_RULE",
) -> MagicMock:
    """Build a mock TransactionCategorization object."""
    cat = MagicMock()
    cat.id = cat_id
    cat.realm_id = realm_id
    cat.transaction_id = transaction_id
    cat.transaction_quickbooks_id = transaction_quickbooks_id
    cat.transaction_type = transaction_type
    cat.status = status
    cat.recommended_account_quickbooks_id = recommended_account_quickbooks_id
    cat.approved_account_quickbooks_id = approved_account_quickbooks_id
    cat.confidence_score = confidence_score
    cat.confidence_band = confidence_band
    cat.version = version
    cat.requires_review = requires_review
    cat.source_sync_token = source_sync_token
    cat.source_transaction_hash = source_transaction_hash
    cat.reviewed_by = reviewed_by
    cat.recommendation_source = recommendation_source
    cat.explanation_summary = "test explanation"
    cat.engine_version = "1.0.0"
    cat.feature_version = "1.0"
    return cat


def _make_account(
    *,
    acct_id: str = "db-acct-500",
    quickbooks_id: str = "acct-500",
    source_deleted: bool = False,
    active: bool = True,
) -> MagicMock:
    """Build a mock QuickBooksAccount."""
    acct = MagicMock()
    acct.id = acct_id
    acct.quickbooks_id = quickbooks_id
    acct.source_deleted = source_deleted
    acct.active = active
    return acct


def _make_run(*, run_id: str = "run-001", status: str = "RUNNING") -> MagicMock:
    run = MagicMock()
    run.id = run_id
    run.status = status
    return run


def _make_application(
    *,
    app_id: str = "app-001",
    status: str = "SUCCESS",
) -> MagicMock:
    app = MagicMock()
    app.id = app_id
    app.status = status
    return app


# ---------------------------------------------------------------------------
# extract_features (features.py) — pure logic
# ---------------------------------------------------------------------------


class TestExtractFeatures:
    def test_full_transaction(self) -> None:
        txn = {
            "counterparty_name_snapshot": "HOME DEPOT",
            "document_number": "INV-12345",
            "private_note": "Office renovation",
            "entity_type": "Purchase",
            "total_amount": "250.00",
            "currency_code": "USD",
            "transaction_date": "2024-06-15",
            "quickbooks_id": "qb-txn-100",
            "account_quickbooks_id": "acct-50",
        }
        feat = extract_features("realm-1", txn, "txn-001")
        assert feat.realm_id == "realm-1"
        assert feat.transaction_id == "txn-001"
        assert feat.transaction_quickbooks_id == "qb-txn-100"
        assert feat.transaction_type == "Purchase"
        assert feat.normalized_vendor == "home depot"
        assert feat.normalized_description == "inv-12345"
        assert feat.normalized_memo == "office renovation"
        assert feat.amount == Decimal("250.00")
        assert feat.absolute_amount == Decimal("250.00")
        assert feat.currency == "USD"
        assert feat.transaction_date == "2024-06-15"
        assert feat.existing_account_ids == ["acct-50"]

    def test_empty_transaction(self) -> None:
        feat = extract_features("realm-1", {}, "txn-empty")
        assert feat.realm_id == "realm-1"
        assert feat.transaction_id == "txn-empty"
        assert feat.normalized_vendor == ""
        assert feat.normalized_description == ""
        assert feat.normalized_memo == ""
        assert feat.amount == Decimal("0")
        assert feat.currency == ""
        assert feat.existing_account_ids == []

    def test_missing_counterparty(self) -> None:
        txn = {"entity_type": "Bill"}
        feat = extract_features("realm-1", txn, "txn-002")
        assert feat.normalized_vendor == ""
        assert feat.transaction_type == "Bill"

    def test_negative_amount(self) -> None:
        txn = {"total_amount": "-150.75"}
        feat = extract_features("r", txn, "t")
        assert feat.amount == Decimal("-150.75")
        assert feat.absolute_amount == Decimal("150.75")

    def test_vendor_normalization_applied(self) -> None:
        txn = {"counterparty_name_snapshot": "SQ *HOME DEPOT LLC"}
        feat = extract_features("r", txn, "t")
        # normalize_vendor strips SQ* prefix and LLC suffix
        assert "home depot" in feat.normalized_vendor

    def test_existing_accounts_empty_when_no_account(self) -> None:
        txn = {"account_quickbooks_id": ""}
        feat = extract_features("r", txn, "t")
        assert feat.existing_account_ids == []


# ---------------------------------------------------------------------------
# check_assisted_automation_gate (engine.py) — pure logic
# ---------------------------------------------------------------------------


class TestAutomationGateExtended:
    def _candidate(
        self,
        score: Decimal,
        source: str = "USER_RULE",
        account_qb_id: str = "100",
    ) -> RecommendationCandidate:
        return RecommendationCandidate(
            account_quickbooks_id=account_qb_id,
            account_id="",
            rank=1,
            score=score,
            confidence_band=ConfidenceBand.HIGH,
            source=RecommendationSource(source),
        )

    def test_empty_candidates_returns_no_candidates(self) -> None:
        gate = check_assisted_automation_gate([])
        assert gate.passed is False
        assert gate.reason_codes == ["NO_CANDIDATES"]

    def test_score_below_threshold(self) -> None:
        cands = [self._candidate(Decimal("0.950"))]
        gate = check_assisted_automation_gate(cands)
        assert gate.passed is False
        assert any("SCORE_BELOW_THRESHOLD" in r for r in gate.reason_codes)

    def test_ambiguity_margin_not_met(self) -> None:
        cands = [
            self._candidate(Decimal("0.980")),
            self._candidate(Decimal("0.900"), source="APPROVED_HISTORY"),
        ]
        gate = check_assisted_automation_gate(cands)
        assert gate.passed is False
        assert any("AMBIGUITY_MARGIN_NOT_MET" in r for r in gate.reason_codes)

    def test_conflicting_user_rules(self) -> None:
        c1 = self._candidate(Decimal("0.990"), account_qb_id="100")
        c2 = self._candidate(Decimal("0.800"), account_qb_id="200")
        # Both from USER_RULE — conflicting targets
        c2.source = RecommendationSource.USER_RULE
        gate = check_assisted_automation_gate([c1, c2])
        assert gate.passed is False
        assert any("CONFLICTING_RULES" in r for r in gate.reason_codes)

    def test_conflicting_system_rules(self) -> None:
        c1 = self._candidate(Decimal("0.990"), source="SYSTEM_RULE", account_qb_id="100")
        c2 = self._candidate(Decimal("0.800"), source="SYSTEM_RULE", account_qb_id="200")
        gate = check_assisted_automation_gate([c1, c2])
        assert gate.passed is False
        assert any("CONFLICTING_RULES" in r for r in gate.reason_codes)

    def test_passing_gate_single_candidate(self) -> None:
        cands = [self._candidate(Decimal("0.980"))]
        gate = check_assisted_automation_gate(cands)
        assert gate.passed is True
        assert gate.reason_codes == []
        assert gate.top_score == Decimal("0.980")

    def test_passing_gate_with_non_rule_second(self) -> None:
        """Second candidate from APPROVED_HISTORY doesn't trigger conflicting rules."""
        c1 = self._candidate(Decimal("0.990"), source="USER_RULE", account_qb_id="100")
        c2 = self._candidate(Decimal("0.800"), source="APPROVED_HISTORY", account_qb_id="200")
        gate = check_assisted_automation_gate([c1, c2])
        assert gate.passed is True

    def test_gate_values_populated(self) -> None:
        cands = [
            self._candidate(Decimal("0.990")),
            self._candidate(Decimal("0.850"), source="APPROVED_HISTORY"),
        ]
        gate = check_assisted_automation_gate(cands)
        assert gate.top_score == Decimal("0.990")
        assert gate.second_score == Decimal("0.850")
        assert gate.ambiguity_gap == Decimal("0.140")

    def test_multiple_reasons_accumulate(self) -> None:
        """Score below threshold AND ambiguity not met."""
        cands = [
            self._candidate(Decimal("0.950")),
            self._candidate(Decimal("0.900"), source="APPROVED_HISTORY"),
        ]
        gate = check_assisted_automation_gate(cands)
        assert gate.passed is False
        assert len(gate.reason_codes) >= 2


# ---------------------------------------------------------------------------
# CategorizationEngine (engine.py) — mocked session/repo
# ---------------------------------------------------------------------------


class TestCategorizationEngine:
    def _make_engine(self) -> tuple[CategorizationEngine, AsyncMock]:
        session = _make_mock_session()
        engine = CategorizationEngine(session, api_client=None)
        return engine, session

    async def test_categorize_eligible_type_no_rules(self) -> None:
        engine, session = self._make_engine()
        # Mock repository methods
        engine._repo.get_categorization_by_txn = AsyncMock(return_value=None)
        engine._repo.get_active_rules = AsyncMock(return_value=[])
        engine._repo.get_vendor_mapping = AsyncMock(return_value=None)

        txn = {
            "entity_type": "Purchase",
            "counterparty_name_snapshot": "ACME CORP",
            "document_number": "",
            "private_note": "",
            "total_amount": "100",
            "currency_code": "USD",
            "transaction_date": "2024-01-01",
            "quickbooks_id": "qb-1",
            "account_quickbooks_id": "",
        }
        result = await engine.categorize_transaction("realm-1", txn, "txn-001")
        assert isinstance(result, CategorizationResult)
        assert result.status == CategorizationStatus.NEEDS_REVIEW
        assert result.requires_review is True

    async def test_categorize_ineligible_type_returns_pending(self) -> None:
        engine, _ = self._make_engine()
        txn = {"entity_type": "TaxPayment"}
        result = await engine.categorize_transaction("realm-1", txn, "txn-002")
        assert result.status == CategorizationStatus.PENDING

    async def test_categorize_unknown_type_returns_pending(self) -> None:
        engine, _ = self._make_engine()
        txn = {"entity_type": "UnknownType"}
        result = await engine.categorize_transaction("realm-1", txn, "txn-003")
        assert result.status == CategorizationStatus.PENDING

    async def test_categorize_with_existing_approved_returns_existing(self) -> None:
        engine, _ = self._make_engine()
        existing = _make_cat(
            status="APPROVED",
            approved_account_quickbooks_id="acct-999",
            confidence_score=Decimal("0.950"),
            confidence_band="HIGH",
        )
        engine._repo.get_categorization_by_txn = AsyncMock(return_value=existing)

        txn = {"entity_type": "Purchase"}
        result = await engine.categorize_transaction("realm-1", txn, "txn-004")
        assert result.status == CategorizationStatus.APPROVED
        assert result.recommended_account_quickbooks_id == "acct-999"

    async def test_categorize_with_existing_applied_returns_existing(self) -> None:
        engine, _ = self._make_engine()
        existing = _make_cat(status="APPLIED", confidence_band="HIGH")
        engine._repo.get_categorization_by_txn = AsyncMock(return_value=existing)

        txn = {"entity_type": "Purchase"}
        result = await engine.categorize_transaction("realm-1", txn, "txn-005")
        assert result.status == CategorizationStatus.APPLIED

    async def test_categorize_recategorize_ignores_existing(self) -> None:
        engine, _ = self._make_engine()
        existing = _make_cat(status="APPROVED")
        engine._repo.get_categorization_by_txn = AsyncMock(return_value=existing)
        engine._repo.get_active_rules = AsyncMock(return_value=[])
        engine._repo.get_vendor_mapping = AsyncMock(return_value=None)

        txn = {
            "entity_type": "Purchase",
            "counterparty_name_snapshot": "VENDOR",
            "document_number": "",
            "private_note": "",
            "total_amount": "50",
            "currency_code": "USD",
            "transaction_date": "2024-01-01",
            "quickbooks_id": "qb-6",
            "account_quickbooks_id": "",
        }
        result = await engine.categorize_transaction(
            "realm-1", txn, "txn-006", recategorize=True
        )
        # With recategorize=True, it should NOT short-circuit
        assert result.transaction_id == "txn-006"

    async def test_persist_result_upserts_and_saves_candidates(self) -> None:
        engine, session = self._make_engine()
        mock_cat = _make_cat()
        engine._repo.upsert_categorization = AsyncMock(return_value=mock_cat)
        engine._repo.save_recommendations = AsyncMock()

        result = CategorizationResult(
            transaction_id="txn-001",
            status=CategorizationStatus.RECOMMENDED,
            recommended_account_quickbooks_id="acct-500",
            confidence_score=Decimal("0.850"),
            confidence_band=ConfidenceBand.HIGH,
            candidates=[
                RecommendationCandidate(
                    account_quickbooks_id="acct-500",
                    account_id="",
                    rank=1,
                    score=Decimal("0.850"),
                    confidence_band=ConfidenceBand.HIGH,
                    source=RecommendationSource.USER_RULE,
                ),
            ],
            explanation={"reason": "rule match"},
        )
        txn = {
            "entity_type": "Purchase",
            "sync_token": "5",
        }
        cat_id = await engine.persist_result("realm-1", result, "qb-txn-001", txn)
        assert cat_id == "cat-001"
        engine._repo.save_recommendations.assert_called_once()

    async def test_persist_result_no_candidates_skips_save(self) -> None:
        engine, _ = self._make_engine()
        mock_cat = _make_cat()
        engine._repo.upsert_categorization = AsyncMock(return_value=mock_cat)
        engine._repo.save_recommendations = AsyncMock()

        result = CategorizationResult(
            transaction_id="txn-001",
            status=CategorizationStatus.NEEDS_REVIEW,
        )
        cat_id = await engine.persist_result("realm-1", result, "qb-txn-001")
        assert cat_id == "cat-001"
        engine._repo.save_recommendations.assert_not_called()


# ---------------------------------------------------------------------------
# ReviewService (review.py) — mocked repository
# ---------------------------------------------------------------------------


class TestReviewService:
    def _make_service(self) -> tuple[ReviewService, AsyncMock]:
        session = _make_mock_session()
        with patch("agentblue.categorization.review.CategorizationRepository"), \
             patch("agentblue.categorization.review.AccountingRepository"):
            service = ReviewService(session, api_client=None)
            # Replace the mocked repos with accessible AsyncMock instances
            service._repo = AsyncMock()
            service._acct_repo = AsyncMock()
            return service, session

    # --- review() ---

    async def test_review_approve_success(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(recommended_account_quickbooks_id="acct-500")
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        result = await svc.review(
            "realm-1", "cat-001", decision="APPROVE", reviewer="alice"
        )
        assert result["status"] == "APPROVED"
        assert result["account"] == "acct-500"
        assert cat.status == "APPROVED"

    async def test_review_approve_no_recommended_account_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(recommended_account_quickbooks_id="")
        svc._repo.get_categorization = AsyncMock(return_value=cat)

        with pytest.raises(InvalidTargetAccountError, match="No recommended account"):
            await svc.review("realm-1", "cat-001", decision="APPROVE", reviewer="alice")

    async def test_review_approve_deleted_account_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        acct = _make_account(source_deleted=True)
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)

        with pytest.raises(InvalidTargetAccountError, match="deleted"):
            await svc.review("realm-1", "cat-001", decision="APPROVE", reviewer="alice")

    async def test_review_change_account_success(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        acct = _make_account(quickbooks_id="acct-999")
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        result = await svc.review(
            "realm-1",
            "cat-001",
            decision="CHANGE_ACCOUNT",
            reviewer="alice",
            selected_account_quickbooks_id="acct-999",
        )
        assert result["status"] == "APPROVED"
        assert result["account"] == "acct-999"
        assert cat.approved_account_quickbooks_id == "acct-999"

    async def test_review_change_account_missing_account_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        svc._repo.get_categorization = AsyncMock(return_value=cat)

        with pytest.raises(InvalidTargetAccountError, match="required"):
            await svc.review(
                "realm-1", "cat-001", decision="CHANGE_ACCOUNT", reviewer="alice"
            )

    async def test_review_change_account_not_found_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=None)

        with pytest.raises(InvalidTargetAccountError, match="not found"):
            await svc.review(
                "realm-1",
                "cat-001",
                decision="CHANGE_ACCOUNT",
                reviewer="alice",
                selected_account_quickbooks_id="acct-999",
            )

    async def test_review_reject_success(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._repo.append_decision = AsyncMock()

        result = await svc.review("realm-1", "cat-001", decision="REJECT", reviewer="bob")
        assert result["status"] == "REJECTED"
        assert cat.status == "REJECTED"

    async def test_review_defer_success(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._repo.append_decision = AsyncMock()

        result = await svc.review("realm-1", "cat-001", decision="DEFER", reviewer="carol")
        assert result["status"] == "DEFERRED"
        assert cat.status == "DEFERRED"

    async def test_review_missing_reviewer_raises(self) -> None:
        svc, _ = self._make_service()

        with pytest.raises(ReviewConflictError, match="Reviewer identity is required"):
            await svc.review("realm-1", "cat-001", decision="APPROVE", reviewer="")

    async def test_review_not_found_raises(self) -> None:
        svc, _ = self._make_service()
        svc._repo.get_categorization = AsyncMock(return_value=None)

        with pytest.raises(CategorizationNotFoundError, match="not found"):
            await svc.review("realm-1", "cat-001", decision="APPROVE", reviewer="alice")

    async def test_review_already_approved_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(status="APPROVED")
        svc._repo.get_categorization = AsyncMock(return_value=cat)

        with pytest.raises(InvalidCategorizationStateError, match="already approved"):
            await svc.review("realm-1", "cat-001", decision="APPROVE", reviewer="alice")

    async def test_review_already_applied_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(status="APPLIED")
        svc._repo.get_categorization = AsyncMock(return_value=cat)

        with pytest.raises(InvalidCategorizationStateError, match="already approved"):
            await svc.review("realm-1", "cat-001", decision="APPROVE", reviewer="alice")

    async def test_review_unknown_decision_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        svc._repo.get_categorization = AsyncMock(return_value=cat)

        with pytest.raises((ReviewConflictError, ValueError)):
            await svc.review("realm-1", "cat-001", decision="INVALID", reviewer="alice")

    async def test_review_approve_account_not_found_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=None)

        with pytest.raises(InvalidTargetAccountError, match="not found"):
            await svc.review("realm-1", "cat-001", decision="APPROVE", reviewer="alice")

    # --- approve_and_apply() ---

    async def test_approve_and_apply_success_simulated(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True
            mock_wb = MockWB.return_value
            mock_wb.apply_categorization = AsyncMock(
                return_value={"status": "SIMULATED"}
            )

            result = await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
                idempotency_key="idem-001",
            )
        assert result["status"] == "APPROVED"
        assert cat.status == "APPLIED"

    async def test_approve_and_apply_stale_sync_token(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        from agentblue.integrations.quickbooks.writeback.exceptions import (
            StaleSyncTokenError,
        )

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True
            mock_wb = MockWB.return_value
            mock_wb.apply_categorization = AsyncMock(
                side_effect=StaleSyncTokenError("SyncToken changed")
            )

            result = await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
                idempotency_key="idem-002",
            )
        assert cat.status == "STALE"
        assert "error" in result

    async def test_approve_and_apply_unsupported_entity_type(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        from agentblue.integrations.quickbooks.writeback.exceptions import (
            UnsupportedEntityTypeError,
        )

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True
            mock_wb = MockWB.return_value
            mock_wb.apply_categorization = AsyncMock(
                side_effect=UnsupportedEntityTypeError("Not supported")
            )

            result = await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
                idempotency_key="idem-003",
            )
        assert cat.status == "APPROVED"  # Reverted
        assert "error" in result

    async def test_approve_and_apply_generic_exception(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True
            mock_wb = MockWB.return_value
            mock_wb.apply_categorization = AsyncMock(
                side_effect=RuntimeError("unexpected error")
            )

            result = await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
                idempotency_key="idem-004",
            )
        assert cat.status == "APPLY_FAILED"
        assert "error" in result

    async def test_approve_and_apply_no_writeback_when_not_supported_type(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat(transaction_type="Bill")
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = False

            result = await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
            )
        assert result["status"] == "APPROVED"
        assert "writeback" not in result
        assert cat.status == "APPROVED"

    async def test_approve_and_apply_no_writeback_without_idempotency_key(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True

            result = await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
                idempotency_key="",  # empty = no writeback attempted
            )
        assert result["status"] == "APPROVED"

    async def test_approve_and_apply_idempotency_hit(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(status="APPROVED")
        existing_app = _make_application(app_id="app-existing", status="SUCCESS")
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._repo.get_application_by_idempotency_key = AsyncMock(
            return_value=existing_app
        )

        result = await svc.approve_and_apply(
            "realm-1", "cat-001", reviewer="alice", idempotency_key="idem-existing"
        )
        assert result["idempotent"] is True
        assert result["application_id"] == "app-existing"

    async def test_approve_and_apply_already_approved_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(status="APPROVED")
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._repo.get_application_by_idempotency_key = AsyncMock(return_value=None)

        with pytest.raises(InvalidCategorizationStateError, match="already in state"):
            await svc.approve_and_apply(
                "realm-1", "cat-001", reviewer="alice", idempotency_key="idem-new"
            )

    async def test_approve_and_apply_version_mismatch_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(version=3)
        svc._repo.get_categorization = AsyncMock(return_value=cat)

        with pytest.raises(ReviewConflictError, match="Version mismatch"):
            await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
                expected_categorization_version=1,
            )

    async def test_approve_and_apply_no_account_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat(recommended_account_quickbooks_id="")
        svc._repo.get_categorization = AsyncMock(return_value=cat)

        with pytest.raises(InvalidTargetAccountError, match="No account selected"):
            await svc.approve_and_apply("realm-1", "cat-001", reviewer="alice")

    async def test_approve_and_apply_account_not_found_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=None)

        with pytest.raises(InvalidTargetAccountError, match="not found"):
            await svc.approve_and_apply("realm-1", "cat-001", reviewer="alice")

    async def test_approve_and_apply_deleted_account_raises(self) -> None:
        svc, _ = self._make_service()
        cat = _make_cat()
        acct = _make_account(source_deleted=True)
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)

        with pytest.raises(InvalidTargetAccountError, match="deleted"):
            await svc.approve_and_apply("realm-1", "cat-001", reviewer="alice")

    async def test_approve_and_apply_missing_reviewer_raises(self) -> None:
        svc, _ = self._make_service()

        with pytest.raises(ReviewConflictError, match="Reviewer identity"):
            await svc.approve_and_apply("realm-1", "cat-001", reviewer="")

    async def test_approve_and_apply_not_found_raises(self) -> None:
        svc, _ = self._make_service()
        svc._repo.get_categorization = AsyncMock(return_value=None)

        with pytest.raises(CategorizationNotFoundError, match="not found"):
            await svc.approve_and_apply("realm-1", "cat-001", reviewer="alice")

    async def test_approve_and_apply_uses_selected_account_over_recommended(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat(recommended_account_quickbooks_id="acct-500")
        acct = _make_account(quickbooks_id="acct-999")
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = False

            result = await svc.approve_and_apply(
                "realm-1",
                "cat-001",
                reviewer="alice",
                selected_account_quickbooks_id="acct-999",
            )
        assert result["account"] == "acct-999"

    async def test_approve_and_apply_label_source_approve_verified(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True
            mock_wb = MockWB.return_value
            mock_wb.apply_categorization = AsyncMock(
                return_value={"status": "SUCCESS"}
            )

            await svc.approve_and_apply(
                "realm-1", "cat-001", reviewer="alice", idempotency_key="idem-v"
            )
        # Should have created label with APPROVE_VERIFIED source
        svc._repo.create_training_label.assert_called_once()
        call_kwargs = svc._repo.create_training_label.call_args
        assert call_kwargs.kwargs["label_source"] == "APPROVE_VERIFIED"

    async def test_approve_and_apply_label_source_approve_apply_failed(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True
            mock_wb = MockWB.return_value
            mock_wb.apply_categorization = AsyncMock(
                side_effect=RuntimeError("boom")
            )

            await svc.approve_and_apply(
                "realm-1", "cat-001", reviewer="alice", idempotency_key="idem-f"
            )
        call_kwargs = svc._repo.create_training_label.call_args
        assert call_kwargs.kwargs["label_source"] == "APPROVE_APPLY_FAILED"

    async def test_approve_and_apply_label_source_approve_stale(self) -> None:
        svc, session = self._make_service()
        cat = _make_cat()
        acct = _make_account()
        svc._repo.get_categorization = AsyncMock(return_value=cat)
        svc._acct_repo.get_account_by_quickbooks_id = AsyncMock(return_value=acct)
        svc._repo.append_decision = AsyncMock()
        svc._repo.create_training_label = AsyncMock()

        from agentblue.integrations.quickbooks.writeback.exceptions import (
            StaleSyncTokenError,
        )

        with patch("agentblue.categorization.review.WriteBackService") as MockWB:
            MockWB.is_supported_type.return_value = True
            mock_wb = MockWB.return_value
            mock_wb.apply_categorization = AsyncMock(
                side_effect=StaleSyncTokenError("stale")
            )

            await svc.approve_and_apply(
                "realm-1", "cat-001", reviewer="alice", idempotency_key="idem-s"
            )
        call_kwargs = svc._repo.create_training_label.call_args
        assert call_kwargs.kwargs["label_source"] == "APPROVE_STALE"


# ---------------------------------------------------------------------------
# CategorizationRepository (repository.py) — AsyncMock session
# ---------------------------------------------------------------------------


class TestCategorizationRepository:
    def _make_repo(self) -> tuple[CategorizationRepository, AsyncMock]:
        session = _make_mock_session()
        repo = CategorizationRepository(session)
        return repo, session

    async def test_get_active_rules_executes_query(self) -> None:
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        rules = await repo.get_active_rules("realm-1")
        assert rules == []
        session.execute.assert_called_once()

    async def test_create_rule_adds_and_flushes(self) -> None:
        repo, session = self._make_repo()
        rule = MagicMock()
        result = await repo.create_rule(rule)
        session.add.assert_called_once_with(rule)
        session.flush.assert_called_once()
        assert result is rule

    async def test_get_categorization_returns_result(self) -> None:
        repo, session = self._make_repo()
        mock_cat = _make_cat()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cat
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_categorization("realm-1", "cat-001")
        assert result is mock_cat

    async def test_get_categorization_not_found(self) -> None:
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_categorization("realm-1", "missing")
        assert result is None

    async def test_get_categorization_by_txn(self) -> None:
        repo, session = self._make_repo()
        mock_cat = _make_cat()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cat
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_categorization_by_txn("realm-1", "txn-001")
        assert result is mock_cat

    async def test_upsert_categorization_creates_new(self) -> None:
        repo, session = self._make_repo()
        # get_categorization_by_txn returns None (no existing)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        await repo.upsert_categorization(
            realm_id="realm-1",
            transaction_id="txn-001",
            transaction_quickbooks_id="qb-txn-001",
            status="RECOMMENDED",
            recommended_account_quickbooks_id="acct-500",
        )
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_upsert_categorization_updates_existing(self) -> None:
        repo, session = self._make_repo()
        existing = _make_cat(version=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=mock_result)

        cat = await repo.upsert_categorization(
            realm_id="realm-1",
            transaction_id="txn-001",
            transaction_quickbooks_id="qb-txn-001",
            status="PRESELECTED",
            recommended_account_quickbooks_id="acct-999",
        )
        assert cat.status == "PRESELECTED"
        assert cat.version == 2

    async def test_save_recommendations_deletes_and_adds(self) -> None:
        repo, session = self._make_repo()
        mock_old_rec = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_old_rec]
        session.execute = AsyncMock(return_value=mock_result)

        candidates = [
            {
                "account_quickbooks_id": "acct-500",
                "rank": 1,
                "score": Decimal("0.850"),
                "confidence_band": "HIGH",
                "source": "USER_RULE",
                "explanation": {},
                "feature_snapshot": {},
                "rule_id": None,
            }
        ]
        await repo.save_recommendations("cat-001", "realm-1", candidates)
        session.delete.assert_called_once_with(mock_old_rec)
        session.add.assert_called_once()

    async def test_append_decision_adds_and_flushes(self) -> None:
        repo, session = self._make_repo()
        await repo.append_decision(
            "cat-001",
            "realm-1",
            decision="APPROVE",
            reviewer="alice",
        )
        session.add.assert_called_once()
        session.flush.assert_called_once()
        # Verify the CategorizationDecision was created with correct fields
        added = session.add.call_args[0][0]
        assert added.decision == "APPROVE"
        assert added.reviewer == "alice"

    async def test_get_vendor_mapping_returns_result(self) -> None:
        repo, session = self._make_repo()
        mock_mapping = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_mapping
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_vendor_mapping("realm-1", "home depot")
        assert result is mock_mapping

    async def test_upsert_vendor_mapping_creates_new(self) -> None:
        repo, session = self._make_repo()
        # get_vendor_mapping returns None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        await repo.upsert_vendor_mapping(
            "realm-1", "home depot", "HOME DEPOT", "acct-500"
        )
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_upsert_vendor_mapping_increments_approval(self) -> None:
        repo, session = self._make_repo()
        existing = MagicMock()
        existing.target_account_quickbooks_id = "acct-500"
        existing.approval_count = 2
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=mock_result)

        mapping = await repo.upsert_vendor_mapping(
            "realm-1", "home depot", "HOME DEPOT", "acct-500"
        )
        assert mapping.approval_count == 3

    async def test_upsert_vendor_mapping_increments_rejection(self) -> None:
        repo, session = self._make_repo()
        existing = MagicMock()
        existing.target_account_quickbooks_id = "acct-500"
        existing.rejection_count = 1
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=mock_result)

        mapping = await repo.upsert_vendor_mapping(
            "realm-1", "home depot", "HOME DEPOT", "acct-999"
        )
        assert mapping.rejection_count == 2

    async def test_create_training_label_adds_and_flushes(self) -> None:
        repo, session = self._make_repo()
        await repo.create_training_label(
            realm_id="realm-1",
            transaction_id="txn-001",
            transaction_quickbooks_id="qb-txn-001",
            selected_account_quickbooks_id="acct-500",
            label_source="APPROVE",
            approved_by="alice",
            engine_version="1.0.0",
            feature_snapshot={},
        )
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_create_application_creates_new(self) -> None:
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        await repo.create_application(
            categorization_id="cat-001",
            realm_id="realm-1",
            transaction_id="txn-001",
            transaction_quickbooks_id="qb-txn-001",
            transaction_type="Purchase",
            selected_account_quickbooks_id="acct-500",
            idempotency_key="idem-001",
            source_sync_token="5",
            source_transaction_hash="hash",
            approved_by="alice",
        )
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_create_application_returns_existing_idempotent(self) -> None:
        repo, session = self._make_repo()
        existing_app = _make_application(app_id="app-existing")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_app
        session.execute = AsyncMock(return_value=mock_result)

        app = await repo.create_application(
            categorization_id="cat-001",
            realm_id="realm-1",
            transaction_id="txn-001",
            transaction_quickbooks_id="qb-txn-001",
            transaction_type="Purchase",
            selected_account_quickbooks_id="acct-500",
            idempotency_key="idem-existing",
            source_sync_token="5",
            source_transaction_hash="hash",
            approved_by="alice",
        )
        assert app.id == "app-existing"
        session.add.assert_not_called()

    async def test_get_application_returns_result(self) -> None:
        repo, session = self._make_repo()
        mock_app = _make_application()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_app
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_application("realm-1", "app-001")
        assert result is mock_app

    async def test_get_application_by_idempotency_key(self) -> None:
        repo, session = self._make_repo()
        mock_app = _make_application()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_app
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_application_by_idempotency_key("idem-001")
        assert result is mock_app

    async def test_create_run_creates_and_flushes(self) -> None:
        repo, session = self._make_repo()
        await repo.create_run("realm-1", "1.0.0")
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_complete_run_updates_when_found(self) -> None:
        repo, session = self._make_repo()
        mock_run = _make_run()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_run
        session.execute = AsyncMock(return_value=mock_result)

        await repo.complete_run("run-001", status="COMPLETED", transaction_count=5)
        assert mock_run.status == "COMPLETED"
        assert mock_run.transaction_count == 5

    async def test_complete_run_noop_when_not_found(self) -> None:
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        # Should not raise
        await repo.complete_run("run-missing", status="COMPLETED")

    async def test_get_run_returns_result(self) -> None:
        repo, session = self._make_repo()
        mock_run = _make_run()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_run
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_run("realm-1", "run-001")
        assert result is mock_run

    async def test_get_rule_by_id(self) -> None:
        repo, session = self._make_repo()
        mock_rule = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rule
        session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_rule_by_id("realm-1", "rule-001")
        assert result is mock_rule

    async def test_get_review_queue(self) -> None:
        repo, session = self._make_repo()
        mock_cat = _make_cat()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_cat]
        session.execute = AsyncMock(return_value=mock_result)

        items = await repo.get_review_queue("realm-1", limit=10)
        assert len(items) == 1
        assert items[0] is mock_cat


# ---------------------------------------------------------------------------
# CategorizationService (services.py) — mocked engine/repo
# ---------------------------------------------------------------------------


class TestCategorizationService:
    def _make_service(self) -> tuple[CategorizationService, AsyncMock]:
        session = _make_mock_session()
        with patch("agentblue.categorization.services.CategorizationRepository"), \
             patch("agentblue.categorization.services.CategorizationEngine"):
            service = CategorizationService(session, api_client=None)
            service._repo = AsyncMock()
            service._engine = AsyncMock()
            service._session = session
            return service, session

    async def test_run_categorization_counts_results(self) -> None:
        svc, session = self._make_service()
        mock_run = _make_run()
        svc._repo.create_run = AsyncMock(return_value=mock_run)
        svc._repo.complete_run = AsyncMock()

        # First transaction: RECOMMENDED
        r1 = CategorizationResult(
            transaction_id="t1",
            status=CategorizationStatus.RECOMMENDED,
        )
        # Second transaction: PRESELECTED
        r2 = CategorizationResult(
            transaction_id="t2",
            status=CategorizationStatus.PRESELECTED,
        )
        # Third transaction: NEEDS_REVIEW
        r3 = CategorizationResult(
            transaction_id="t3",
            status=CategorizationStatus.NEEDS_REVIEW,
        )
        svc._engine.categorize_transaction = AsyncMock(side_effect=[r1, r2, r3])
        svc._engine.persist_result = AsyncMock(return_value="cat-id")

        txns = [
            {"id": "t1", "quickbooks_id": "qb-1", "entity_type": "Purchase"},
            {"id": "t2", "quickbooks_id": "qb-2", "entity_type": "Purchase"},
            {"id": "t3", "quickbooks_id": "qb-3", "entity_type": "Purchase"},
        ]
        result = await svc.run_categorization("realm-1", txns)
        assert result["total"] == 3
        assert result["recommended"] == 1
        assert result["preselected"] == 1
        assert result["needs_review"] == 1
        assert result["failed"] == 0

    async def test_run_categorization_handles_exceptions(self) -> None:
        svc, session = self._make_service()
        mock_run = _make_run()
        svc._repo.create_run = AsyncMock(return_value=mock_run)
        svc._repo.complete_run = AsyncMock()

        r1 = CategorizationResult(
            transaction_id="t1", status=CategorizationStatus.RECOMMENDED
        )
        svc._engine.categorize_transaction = AsyncMock(
            side_effect=[r1, RuntimeError("boom")]
        )
        svc._engine.persist_result = AsyncMock(return_value="cat-id")

        txns = [
            {"id": "t1", "quickbooks_id": "qb-1"},
            {"id": "t2", "quickbooks_id": "qb-2"},
        ]
        result = await svc.run_categorization("realm-1", txns)
        assert result["total"] == 1
        assert result["failed"] == 1


# ---------------------------------------------------------------------------
# Router (router.py) — FastAPI TestClient with dependency overrides
# ---------------------------------------------------------------------------


class _StubReviewService:
    """Lightweight stub for router tests — no actual DB interaction."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class _StubCategorizationService:
    """Lightweight stub for router tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class _StubCategorizationRepository:
    """Lightweight stub for router tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


def _make_router_app() -> FastAPI:
    """Create a FastAPI app with the categorization router and stubbed dependencies."""
    from agentblue.categorization.router import router

    app = FastAPI()
    app.include_router(router)

    @asynccontextmanager
    async def _noop_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = _noop_lifespan
    return app


def _stub_db():
    """Async generator that yields a mock session (for get_db override)."""
    session = _make_mock_session()
    return session


class TestRouterEndpoints:
    def _setup_app(self) -> tuple[FastAPI, TestClient]:
        app = _make_router_app()

        async def override_get_db():
            yield _make_mock_session()

        from agentblue.db.session import get_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        return app, client

    def test_supported_writeback_types_endpoint(self) -> None:
        app, client = self._setup_app()
        resp = client.get("/api/v1/categorization/supported-writeback-types")
        assert resp.status_code == 200
        data = resp.json()
        assert "supported_types" in data
        assert "deferred_types" in data
        assert "Purchase" in data["supported_types"]

    def test_create_run_endpoint(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.CategorizationService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.run_categorization = AsyncMock(
                return_value={
                    "run_id": "run-001",
                    "total": 0,
                    "recommended": 0,
                    "preselected": 0,
                    "needs_review": 0,
                    "failed": 0,
                }
            )
            resp = client.post(
                "/api/v1/categorization/runs",
                json={"realm_id": "realm-1", "transaction_ids": [], "recategorize": False},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-001"

    def test_get_run_not_found(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_run = AsyncMock(return_value=None)
            resp = client.get(
                "/api/v1/categorization/runs/run-missing",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 404

    def test_get_run_found(self) -> None:
        app, client = self._setup_app()
        mock_run = MagicMock()
        mock_run.id = "run-001"
        mock_run.status = "COMPLETED"
        mock_run.transaction_count = 5
        mock_run.recommended_count = 3
        mock_run.preselected_count = 1
        mock_run.needs_review_count = 1
        mock_run.applied_count = 0
        mock_run.failed_count = 0

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_run = AsyncMock(return_value=mock_run)
            resp = client.get(
                "/api/v1/categorization/runs/run-001",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-001"
        assert data["transaction_count"] == 5

    def test_list_categorizations_empty(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_review_queue = AsyncMock(return_value=[])
            resp = client.get(
                "/api/v1/categorization/categorizations",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_categorization_not_found(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_categorization = AsyncMock(return_value=None)
            resp = client.get(
                "/api/v1/categorization/categorizations/cat-missing",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 404

    def test_get_categorization_found(self) -> None:
        app, client = self._setup_app()
        mock_cat = _make_cat()

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_categorization = AsyncMock(return_value=mock_cat)
            resp = client.get(
                "/api/v1/categorization/categorizations/cat-001",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "cat-001"

    def test_approve_and_apply_success(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.approve_and_apply = AsyncMock(
                return_value={"status": "APPROVED", "account": "acct-500"}
            )
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-001/approve-and-apply",
                params={"realm_id": "realm-1"},
                json={
                    "realm_id": "realm-1",
                    "reviewer": "alice",
                    "idempotency_key": "idem-001",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "APPROVED"

    def test_approve_and_apply_not_found(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.approve_and_apply = AsyncMock(
                side_effect=CategorizationNotFoundError("not found")
            )
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-missing/approve-and-apply",
                params={"realm_id": "realm-1"},
                json={
                    "realm_id": "realm-1",
                    "reviewer": "alice",
                    "idempotency_key": "idem-001",
                },
            )
        assert resp.status_code == 404

    def test_approve_and_apply_conflict_409(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.approve_and_apply = AsyncMock(
                side_effect=InvalidCategorizationStateError("already in state APPROVED")
            )
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-001/approve-and-apply",
                params={"realm_id": "realm-1"},
                json={
                    "realm_id": "realm-1",
                    "reviewer": "alice",
                    "idempotency_key": "idem-001",
                },
            )
        assert resp.status_code == 409

    def test_approve_and_apply_bad_request_400(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.approve_and_apply = AsyncMock(
                side_effect=InvalidTargetAccountError("no account")
            )
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-001/approve-and-apply",
                params={"realm_id": "realm-1"},
                json={
                    "realm_id": "realm-1",
                    "reviewer": "alice",
                    "idempotency_key": "idem-001",
                },
            )
        assert resp.status_code == 400

    def test_reject_endpoint_success(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.review = AsyncMock(return_value={"status": "REJECTED"})
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-001/reject",
                params={"realm_id": "realm-1"},
                json={"decision": "REJECT", "reviewer": "bob"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"

    def test_reject_not_found(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.review = AsyncMock(
                side_effect=CategorizationNotFoundError("not found")
            )
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-missing/reject",
                params={"realm_id": "realm-1"},
                json={"decision": "REJECT", "reviewer": "bob"},
            )
        assert resp.status_code == 404

    def test_defer_endpoint_success(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.review = AsyncMock(return_value={"status": "DEFERRED"})
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-001/defer",
                params={"realm_id": "realm-1"},
                json={"decision": "DEFER", "reviewer": "carol"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "DEFERRED"

    def test_defer_not_found(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.review = AsyncMock(
                side_effect=CategorizationNotFoundError("not found")
            )
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-missing/defer",
                params={"realm_id": "realm-1"},
                json={"decision": "DEFER", "reviewer": "carol"},
            )
        assert resp.status_code == 404

    def test_create_rule_endpoint(self) -> None:
        app, client = self._setup_app()
        mock_rule = MagicMock()
        mock_rule.id = "rule-001"
        mock_rule.name = "Test Rule"
        mock_rule.rule_type = "EXACT_VENDOR"
        mock_rule.rule_status = "ACTIVE"
        mock_rule.precedence = 100
        mock_rule.target_account_quickbooks_id = "acct-500"
        mock_rule.match_count = 0

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.create_rule = AsyncMock(return_value=mock_rule)
            resp = client.post(
                "/api/v1/categorization/rules",
                json={
                    "realm_id": "realm-1",
                    "name": "Test Rule",
                    "rule_type": "EXACT_VENDOR",
                    "conditions": {"vendor": "home depot"},
                    "target_account_quickbooks_id": "acct-500",
                    "precedence": 100,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "rule-001"
        assert data["name"] == "Test Rule"

    def test_list_rules_endpoint(self) -> None:
        app, client = self._setup_app()
        mock_rule = MagicMock()
        mock_rule.id = "rule-001"
        mock_rule.name = "Test Rule"
        mock_rule.rule_type = "EXACT_VENDOR"
        mock_rule.rule_status = "ACTIVE"
        mock_rule.precedence = 100
        mock_rule.target_account_quickbooks_id = "acct-500"
        mock_rule.match_count = 5

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_active_rules = AsyncMock(return_value=[mock_rule])
            resp = client.get(
                "/api/v1/categorization/rules",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["match_count"] == 5

    def test_review_queue_endpoint(self) -> None:
        app, client = self._setup_app()
        mock_cat = _make_cat()

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_review_queue = AsyncMock(return_value=[mock_cat])
            resp = client.get(
                "/api/v1/categorization/review-queue",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    def test_approve_and_apply_review_conflict_400(self) -> None:
        app, client = self._setup_app()

        with patch("agentblue.categorization.router.ReviewService") as MockSvc:
            mock_svc = MockSvc.return_value
            mock_svc.approve_and_apply = AsyncMock(
                side_effect=ReviewConflictError("version mismatch")
            )
            resp = client.post(
                "/api/v1/categorization/categorizations/cat-001/approve-and-apply",
                params={"realm_id": "realm-1"},
                json={
                    "realm_id": "realm-1",
                    "reviewer": "alice",
                    "idempotency_key": "idem-001",
                },
            )
        assert resp.status_code == 400

    def test_list_categorizations_with_results(self) -> None:
        app, client = self._setup_app()
        mock_cat = _make_cat()

        with patch("agentblue.categorization.router.CategorizationRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_review_queue = AsyncMock(return_value=[mock_cat])
            resp = client.get(
                "/api/v1/categorization/categorizations",
                params={"realm_id": "realm-1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "cat-001"
