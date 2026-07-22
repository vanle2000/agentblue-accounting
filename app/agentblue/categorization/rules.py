"""Categorization rule evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

from agentblue.categorization.domain import (
    RuleType,
)
from agentblue.categorization.normalization import normalize_text, normalize_vendor

logger = structlog.get_logger(__name__)


def evaluate_rule(
    rule: dict[str, Any],
    vendor: str,
    description: str,
    memo: str,
    transaction_type: str,
    amount: Decimal,
) -> tuple[bool, dict[str, Any]]:
    """Evaluate a single rule against transaction features.

    Returns (matched, evidence).
    """
    rule_type = rule.get("rule_type", "")
    conditions = rule.get("conditions", {})
    evidence: dict[str, Any] = {"rule_type": rule_type, "matched": False}

    if rule_type == RuleType.EXACT_VENDOR.value:
        target = normalize_vendor(str(conditions.get("vendor", "")))
        actual = normalize_vendor(vendor)
        matched = bool(target and target == actual)
        evidence["matched"] = matched
        evidence["target_vendor"] = target
        evidence["actual_vendor"] = actual
        return matched, evidence

    if rule_type == RuleType.NORMALIZED_VENDOR.value:
        target = normalize_vendor(str(conditions.get("vendor", "")))
        actual = normalize_vendor(vendor)
        matched = bool(target and target in actual)
        evidence["matched"] = matched
        return matched, evidence

    if rule_type == RuleType.DESCRIPTION_CONTAINS.value:
        keyword = normalize_text(str(conditions.get("keyword", "")))
        actual = normalize_text(description)
        matched = bool(keyword and keyword in actual)
        evidence["matched"] = matched
        return matched, evidence

    if rule_type == RuleType.MEMO_CONTAINS.value:
        keyword = normalize_text(str(conditions.get("keyword", "")))
        actual = normalize_text(memo)
        matched = bool(keyword and keyword in actual)
        evidence["matched"] = matched
        return matched, evidence

    if rule_type == RuleType.TRANSACTION_TYPE.value:
        target = str(conditions.get("transaction_type", ""))
        matched = bool(target and target == transaction_type)
        evidence["matched"] = matched
        return matched, evidence

    if rule_type == RuleType.AMOUNT_RANGE.value:
        min_amt = Decimal(str(conditions.get("min_amount", "0")))
        max_amt = Decimal(str(conditions.get("max_amount", "999999999")))
        matched = min_amt <= abs(amount) <= max_amt
        evidence["matched"] = matched
        return matched, evidence

    if rule_type == RuleType.COMPOSITE.value:
        sub_conditions = conditions.get("all", [])
        all_matched = True
        sub_evidence: list[dict[str, Any]] = []
        for sub in sub_conditions:
            sub_type = sub.get("type", "")
            sub_val = str(sub.get("value", ""))
            if sub_type == "vendor_equals":
                m = normalize_vendor(vendor) == normalize_vendor(sub_val)
            elif sub_type == "description_contains":
                m = normalize_text(sub_val) in normalize_text(description)
            elif sub_type == "memo_contains":
                m = normalize_text(sub_val) in normalize_text(memo)
            else:
                m = False
            sub_evidence.append({"type": sub_type, "matched": m})
            if not m:
                all_matched = False
        evidence["matched"] = all_matched
        evidence["sub_conditions"] = sub_evidence
        return all_matched, evidence

    evidence["matched"] = False
    evidence["reason"] = f"Unsupported rule type: {rule_type}"
    return False, evidence
