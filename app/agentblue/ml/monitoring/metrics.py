"""Monitoring metrics for shadow evaluations.

Computes agreement and override rates from shadow evaluation records.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def compute_shadow_agreement_rate(evaluations: list[dict[str, Any]]) -> float:
    """Compute the rate at which ML and rule engine agree.

    Agreement means the ML top-1 account matches the rule-based
    recommendation.

    Args:
        evaluations: List of shadow evaluation dicts, each with an
            ``outcome`` key (e.g. AGREEMENT, DISAGREEMENT).

    Returns:
        Agreement rate as a float in [0.0, 1.0].  Returns 0.0 if
        no evaluations are provided.
    """
    if not evaluations:
        return 0.0

    agreements = sum(1 for e in evaluations if e.get("outcome") == "AGREEMENT")
    return agreements / len(evaluations)


def compute_override_rate(evaluations: list[dict[str, Any]]) -> float:
    """Compute the rate at which the user overrides the rule recommendation.

    An override is detected when the resolved account differs from the
    rule-based recommendation (i.e. the user chose the ML suggestion or
    a third option).

    Args:
        evaluations: List of shadow evaluation dicts, each with keys:
            ``rule_account_quickbooks_id``, ``resolution`` (or
            ``ml_account_quickbooks_id``), ``resolved``.

    Returns:
        Override rate as a float in [0.0, 1.0].  Returns 0.0 if no
        resolved evaluations are provided.
    """
    resolved = [e for e in evaluations if e.get("resolved")]
    if not resolved:
        return 0.0

    overrides = 0
    for eval_rec in resolved:
        rule_account = eval_rec.get("rule_account_quickbooks_id", "")
        resolution = eval_rec.get("resolution", "")
        if resolution and resolution != rule_account:
            overrides += 1

    return overrides / len(resolved)
