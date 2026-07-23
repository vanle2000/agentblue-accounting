"""Tests for categorization (Stage 7 Level 2 Assisted Automation).

Covers normalization, rules, scoring, assisted-automation gate, writeback, and security.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentblue.categorization.domain import (
    ConfidenceBand,
    RecommendationCandidate,
    RecommendationSource,
)
from agentblue.categorization.engine import check_assisted_automation_gate
from agentblue.categorization.normalization import normalize_text, normalize_vendor
from agentblue.categorization.rules import evaluate_rule
from agentblue.categorization.scoring import calculate_score, score_to_band
from agentblue.integrations.quickbooks.writeback.payloads import (
    build_update_payload,
    get_entity_endpoint,
)
from agentblue.integrations.quickbooks.writeback.validation import (
    check_stale,
    compute_entity_hash,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_lowercase_and_trim(self) -> None:
        # "Corp" is stripped as a legal suffix
        assert normalize_vendor("  ABC Corp  ") == "abc"

    def test_processor_prefix_stripped(self) -> None:
        assert normalize_vendor("SQ *ABC PLUMBING") == "abc plumbing"
        assert normalize_vendor("PAYPAL *VENDOR NAME") == "vendor name"
        assert normalize_vendor("ACH PAYMENT - ABC SERVICES") == "abc services"

    def test_legal_suffix_stripped(self) -> None:
        assert normalize_vendor("ABC SERVICES LLC") == "abc services"
        assert normalize_vendor("ABC SERVICES, L.L.C.") == "abc services"
        assert normalize_vendor("ABC SERVICES INC.") == "abc services"

    def test_blank_value(self) -> None:
        assert normalize_vendor("") == ""
        assert normalize_vendor("   ") == ""

    def test_repeated_whitespace(self) -> None:
        assert normalize_vendor("ABC   SERVICES   LLC") == "abc services"

    def test_unicode(self) -> None:
        result = normalize_vendor("Café Résumé LLC")
        assert "café" in result or "cafe" in result

    def test_apostrophe(self) -> None:
        assert "o'brien" in normalize_vendor("O'Brien LLC")

    def test_ampersand(self) -> None:
        assert "a & b" in normalize_vendor("A & B Corp")

    def test_numbers_preserved(self) -> None:
        assert "7-eleven" in normalize_vendor("7-ELEVEN STORE #1234")

    def test_text_normalization(self) -> None:
        assert normalize_text("  Hello   World  ") == "hello world"
        assert normalize_text("") == ""

    def test_deterministic(self) -> None:
        v = "SQ *HOME DEPOT #1234"
        assert normalize_vendor(v) == normalize_vendor(v)


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


class TestRuleEvaluation:
    def test_exact_vendor_match(self) -> None:
        rule = {"rule_type": "EXACT_VENDOR", "conditions": {"vendor": "home depot"}}
        matched, _ = evaluate_rule(rule, "HOME DEPOT", "", "", "Purchase", Decimal("100"))
        assert matched is True

    def test_exact_vendor_no_match(self) -> None:
        rule = {"rule_type": "EXACT_VENDOR", "conditions": {"vendor": "Lowes"}}
        matched, _ = evaluate_rule(rule, "HOME DEPOT", "", "", "Purchase", Decimal("100"))
        assert matched is False

    def test_description_contains(self) -> None:
        rule = {"rule_type": "DESCRIPTION_CONTAINS", "conditions": {"keyword": "plumbing"}}
        matched, _ = evaluate_rule(
            rule, "", "Emergency plumbing repair", "", "Purchase", Decimal("500")
        )
        assert matched is True

    def test_memo_contains(self) -> None:
        rule = {"rule_type": "MEMO_CONTAINS", "conditions": {"keyword": "rent"}}
        matched, _ = evaluate_rule(rule, "", "", "Monthly rent payment", "Bill", Decimal("2000"))
        assert matched is True

    def test_transaction_type_match(self) -> None:
        rule = {"rule_type": "TRANSACTION_TYPE", "conditions": {"transaction_type": "Purchase"}}
        matched, _ = evaluate_rule(rule, "Vendor", "", "", "Purchase", Decimal("50"))
        assert matched is True

    def test_amount_range_match(self) -> None:
        rule = {
            "rule_type": "AMOUNT_RANGE",
            "conditions": {"min_amount": "100", "max_amount": "500"},
        }
        matched, _ = evaluate_rule(rule, "Vendor", "", "", "Purchase", Decimal("250"))
        assert matched is True

    def test_amount_range_no_match(self) -> None:
        rule = {
            "rule_type": "AMOUNT_RANGE",
            "conditions": {"min_amount": "100", "max_amount": "500"},
        }
        matched, _ = evaluate_rule(rule, "Vendor", "", "", "Purchase", Decimal("600"))
        assert matched is False

    def test_composite_match(self) -> None:
        rule = {
            "rule_type": "COMPOSITE",
            "conditions": {
                "all": [
                    {"type": "vendor_equals", "value": "home depot"},
                    {"type": "description_contains", "value": "repair"},
                ]
            },
        }
        matched, _ = evaluate_rule(
            rule, "HOME DEPOT", "Home repair supplies", "", "Purchase", Decimal("200")
        )
        assert matched is True

    def test_composite_partial_no_match(self) -> None:
        rule = {
            "rule_type": "COMPOSITE",
            "conditions": {
                "all": [
                    {"type": "vendor_equals", "value": "home depot"},
                    {"type": "description_contains", "value": "catering"},
                ]
            },
        }
        matched, _ = evaluate_rule(
            rule, "HOME DEPOT", "Home repair supplies", "", "Purchase", Decimal("200")
        )
        assert matched is False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_user_rule_high_score(self) -> None:
        score, components = calculate_score(user_rule_match=True, account_compatible=True)
        assert score >= Decimal("0.65")
        assert "user_rule" in components

    def test_vendor_history_score(self) -> None:
        score, _ = calculate_score(vendor_history_score=Decimal("0.8"), account_compatible=True)
        assert score > Decimal("0")

    def test_conflict_penalty(self) -> None:
        s1, _ = calculate_score(user_rule_match=True, account_compatible=True, has_conflict=False)
        s2, _ = calculate_score(user_rule_match=True, account_compatible=True, has_conflict=True)
        assert s2 < s1

    def test_score_never_above_one(self) -> None:
        score, _ = calculate_score(
            user_rule_match=True,
            vendor_history_score=Decimal("1"),
            keyword_match=True,
            account_compatible=True,
            fuzzy_score=Decimal("1"),
        )
        assert score <= Decimal("1")

    def test_score_never_below_zero(self) -> None:
        score, _ = calculate_score(has_conflict=True)
        assert score >= Decimal("0")

    def test_confidence_bands(self) -> None:
        assert score_to_band(Decimal("0.9")) == ConfidenceBand.HIGH
        assert score_to_band(Decimal("0.7")) == ConfidenceBand.MEDIUM
        assert score_to_band(Decimal("0.3")) == ConfidenceBand.LOW
        assert score_to_band(Decimal("0")) == ConfidenceBand.NONE

    def test_deterministic(self) -> None:
        s1, _ = calculate_score(user_rule_match=True, account_compatible=True)
        s2, _ = calculate_score(user_rule_match=True, account_compatible=True)
        assert s1 == s2


# ---------------------------------------------------------------------------
# Assisted-automation gate
# ---------------------------------------------------------------------------


class TestAssistedAutomationGate:
    def _candidate(self, score: Decimal, source: str = "USER_RULE") -> RecommendationCandidate:
        return RecommendationCandidate(
            account_quickbooks_id="100",
            account_id="db-100",
            rank=1,
            score=score,
            confidence_band=score_to_band(score),
            source=RecommendationSource(source),
        )

    def test_score_below_threshold_does_not_preselect(self) -> None:
        """Score 0.969 does not preselect."""
        candidates = [self._candidate(Decimal("0.969"))]
        gate = check_assisted_automation_gate(candidates)
        assert gate.passed is False
        assert any("SCORE_BELOW_THRESHOLD" in r for r in gate.reason_codes)

    def test_score_at_threshold_preselects(self) -> None:
        """Score 0.970 preselects when all safeguards pass."""
        candidates = [self._candidate(Decimal("0.970"))]
        gate = check_assisted_automation_gate(candidates)
        assert gate.passed is True
        assert gate.top_score == Decimal("0.970")

    def test_score_above_threshold_with_ambiguity_does_not_preselect(self) -> None:
        """Score above threshold with ambiguity does not preselect."""
        candidates = [
            self._candidate(Decimal("0.980")),
            self._candidate(Decimal("0.900")),  # gap = 0.080 < 0.10
        ]
        gate = check_assisted_automation_gate(candidates)
        assert gate.passed is False
        assert any("AMBIGUITY_MARGIN_NOT_MET" in r for r in gate.reason_codes)

    def test_score_above_threshold_with_clear_winner_preselects(self) -> None:
        """Score above threshold with clear gap preselects."""
        candidates = [
            self._candidate(Decimal("0.980")),
            self._candidate(Decimal("0.800")),  # gap = 0.180 >= 0.10
        ]
        gate = check_assisted_automation_gate(candidates)
        assert gate.passed is True

    def test_no_candidates_does_not_preselect(self) -> None:
        gate = check_assisted_automation_gate([])
        assert gate.passed is False
        assert gate.reason_codes == ["NO_CANDIDATES"]

    def test_conflicting_rules_do_not_preselect(self) -> None:
        """Equal-precedence conflicting targets do not preselect."""
        c1 = RecommendationCandidate(
            account_quickbooks_id="100",
            account_id="",
            rank=1,
            score=Decimal("0.980"),
            confidence_band=ConfidenceBand.HIGH,
            source=RecommendationSource.USER_RULE,
        )
        c2 = RecommendationCandidate(
            account_quickbooks_id="200",
            account_id="",
            rank=2,
            score=Decimal("0.850"),
            confidence_band=ConfidenceBand.HIGH,
            source=RecommendationSource.USER_RULE,
        )
        gate = check_assisted_automation_gate([c1, c2])
        assert gate.passed is False
        assert any("CONFLICTING_RULES" in r for r in gate.reason_codes)

    def test_ambiguity_gap_calculated_correctly(self) -> None:
        candidates = [
            self._candidate(Decimal("0.990")),
            self._candidate(Decimal("0.850")),
        ]
        gate = check_assisted_automation_gate(candidates)
        assert gate.ambiguity_gap == Decimal("0.140")


# ---------------------------------------------------------------------------
# Write-back payload
# ---------------------------------------------------------------------------


class TestWriteBackPayload:
    def test_purchase_update_preserves_lines(self) -> None:
        entity = {
            "Id": "123",
            "SyncToken": "5",
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": "100.00",
                    "Description": "Office supplies",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "50"},
                        "BillableStatus": "NotBillable",
                    },
                }
            ],
        }
        payload = build_update_payload("Purchase", entity, "999")
        assert payload["Id"] == "123"
        assert payload["SyncToken"] == "5"
        assert payload["sparse"] is True
        assert len(payload["Line"]) == 1
        assert payload["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] == "999"
        assert payload["Line"][0]["Amount"] == "100.00"

    def test_unsupported_entity_rejected(self) -> None:
        from agentblue.integrations.quickbooks.writeback.exceptions import (
            UnsupportedEntityTypeError,
        )

        with pytest.raises(UnsupportedEntityTypeError):
            build_update_payload("JournalEntry", {}, "999")

    def test_bill_writeback_not_supported(self) -> None:
        """Bill uses separate /bill endpoint - deferred from Stage 7."""
        from agentblue.integrations.quickbooks.writeback.exceptions import (
            UnsupportedEntityTypeError,
        )

        with pytest.raises(UnsupportedEntityTypeError):
            build_update_payload("Bill", {}, "888")


# ---------------------------------------------------------------------------
# SyncToken and stale-state validation
# ---------------------------------------------------------------------------


class TestStaleDetection:
    def test_not_stale_when_unchanged(self) -> None:
        entity = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [],
        }
        entity_hash = compute_entity_hash(entity)
        reasons = check_stale("5", entity_hash, entity)
        assert reasons == []

    def test_stale_when_sync_token_changed(self) -> None:
        entity = {
            "Id": "1",
            "SyncToken": "6",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [],
        }
        entity_hash = compute_entity_hash(
            {"Id": "1", "SyncToken": "5", "TotalAmt": "100", "TxnDate": "2024-01-01", "Line": []}
        )
        reasons = check_stale("5", entity_hash, entity)
        assert len(reasons) >= 1
        assert any("sync_token_changed" in r for r in reasons)

    def test_stale_when_amount_changed(self) -> None:
        reviewed = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [],
        }
        current = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "200",
            "TxnDate": "2024-01-01",
            "Line": [],
        }
        reviewed_hash = compute_entity_hash(reviewed)
        reasons = check_stale("5", reviewed_hash, current)
        assert any("transaction_hash_changed" in r for r in reasons)

    def test_entity_hash_deterministic(self) -> None:
        entity = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [],
        }
        assert compute_entity_hash(entity) == compute_entity_hash(entity)

    def test_line_added_makes_stale(self) -> None:
        reviewed = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [],
        }
        current = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": "100",
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "50"}},
                }
            ],
        }
        reviewed_hash = compute_entity_hash(reviewed)
        reasons = check_stale("5", reviewed_hash, current)
        assert len(reasons) >= 1

    def test_line_account_changed_makes_stale(self) -> None:
        reviewed = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": "100",
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "50"}},
                }
            ],
        }
        current = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": "100",
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "99"}},
                }
            ],
        }
        reviewed_hash = compute_entity_hash(reviewed)
        reasons = check_stale("5", reviewed_hash, current)
        assert len(reasons) >= 1

    def test_line_id_changed_makes_stale(self) -> None:
        reviewed = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": "100",
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "50"}},
                }
            ],
        }
        current = {
            "Id": "1",
            "SyncToken": "5",
            "TotalAmt": "100",
            "TxnDate": "2024-01-01",
            "Line": [
                {
                    "Id": "2",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": "100",
                    "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "50"}},
                }
            ],
        }
        reviewed_hash = compute_entity_hash(reviewed)
        reasons = check_stale("5", reviewed_hash, current)
        assert len(reasons) >= 1


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestEntityEndpoints:
    def test_purchase_endpoint(self) -> None:
        endpoint = get_entity_endpoint("Purchase", "realm-1", "123")
        assert "/purchase/123" in endpoint

    def test_purchase_base_endpoint(self) -> None:
        endpoint = get_entity_endpoint("Purchase", "realm-1")
        assert "/purchase" in endpoint

    def test_unsupported_endpoint(self) -> None:
        from agentblue.integrations.quickbooks.writeback.exceptions import (
            UnsupportedEntityTypeError,
        )

        with pytest.raises(UnsupportedEntityTypeError):
            get_entity_endpoint("JournalEntry", "realm-1")


class TestSecurity:
    def test_no_secrets_in_normalization(self) -> None:
        result = normalize_vendor("HOME DEPOT")
        assert "token" not in result.lower()
        assert "secret" not in result.lower()

    def test_rule_conditions_are_data(self) -> None:
        rule = {"rule_type": "EXACT_VENDOR", "conditions": {"vendor": "test"}}
        assert isinstance(rule["conditions"], dict)

    def test_writeback_rejects_unsupported_types(self) -> None:
        from agentblue.integrations.quickbooks.writeback.exceptions import (
            UnsupportedEntityTypeError,
        )

        with pytest.raises(UnsupportedEntityTypeError):
            build_update_payload("Transfer", {}, "999")
