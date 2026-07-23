"""Deterministic scoring model for categorization."""

from __future__ import annotations

from decimal import Decimal

from agentblue.categorization.constants import (
    CONFLICT_PENALTY,
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    WEIGHT_ACCOUNT_COMPAT,
    WEIGHT_FUZZY_MAX,
    WEIGHT_KEYWORD,
    WEIGHT_USER_RULE,
    WEIGHT_VENDOR_HISTORY,
)
from agentblue.categorization.domain import ConfidenceBand


def calculate_score(
    *,
    user_rule_match: bool = False,
    vendor_history_score: Decimal = Decimal("0"),
    keyword_match: bool = False,
    account_compatible: bool = False,
    fuzzy_score: Decimal = Decimal("0"),
    has_conflict: bool = False,
) -> tuple[Decimal, dict[str, str]]:
    """Calculate a deterministic confidence score.

    Returns (score, component_breakdown).
    """
    components: dict[str, str] = {}
    score = Decimal("0")

    if user_rule_match:
        score += WEIGHT_USER_RULE
        components["user_rule"] = str(WEIGHT_USER_RULE)

    if vendor_history_score > 0:
        contribution = min(vendor_history_score, WEIGHT_VENDOR_HISTORY)
        score += contribution
        components["vendor_history"] = str(contribution)

    if keyword_match:
        score += WEIGHT_KEYWORD
        components["keyword"] = str(WEIGHT_KEYWORD)

    if account_compatible:
        score += WEIGHT_ACCOUNT_COMPAT
        components["account_compatibility"] = str(WEIGHT_ACCOUNT_COMPAT)

    if fuzzy_score > 0:
        contribution = min(fuzzy_score, WEIGHT_FUZZY_MAX)
        score += contribution
        components["fuzzy"] = str(contribution)

    if has_conflict:
        score = max(Decimal("0"), score - CONFLICT_PENALTY)
        components["conflict_penalty"] = str(-CONFLICT_PENALTY)

    score = min(Decimal("1"), max(Decimal("0"), score))
    components["total"] = str(score)

    return score, components


def score_to_band(score: Decimal) -> ConfidenceBand:
    """Convert a numeric score to a confidence band."""
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceBand.HIGH
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return ConfidenceBand.MEDIUM
    if score > 0:
        return ConfidenceBand.LOW
    return ConfidenceBand.NONE
