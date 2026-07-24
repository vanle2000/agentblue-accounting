"""Model selection logic for choosing the best candidate model."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Weighting for the composite selection score.
_WEIGHT_ACCURACY = 0.30
_WEIGHT_MACRO_F1 = 0.40
_WEIGHT_LOG_LOSS = 0.30  # Inverted: lower is better


def select_best_model(
    candidates: list[dict[str, Any]],
    *,
    metrics: str = "test",
) -> dict[str, Any]:
    """Select the best model from a list of candidate training results.

    Each candidate is a dict (or :class:`TrainingRunResult`-like) with
    at least ``run_id``, ``model_type``, and ``metrics``.

    Selection uses a weighted composite of accuracy, macro-F1, and
    (inverted) log-loss on the *test* split.

    Parameters
    ----------
    candidates:
        List of training run result dicts.
    metrics:
        Which split to evaluate on — ``"test"`` (default) or ``"train"``.

    Returns
    -------
    dict
        The winning candidate, with an added ``selection_score`` key.

    Raises
    ------
    ValueError
        If no candidates are provided.
    """
    if not candidates:
        raise ValueError("No candidates provided for model selection")

    best: dict[str, Any] | None = None
    best_score = float("-inf")

    scored: list[dict[str, Any]] = []

    for c in candidates:
        m = c.get("metrics", {})
        split_m = m.get(metrics, m.get("test", {}))

        accuracy = split_m.get("accuracy", 0.0)
        macro_f1 = split_m.get("macro_f1", 0.0)
        logloss = split_m.get("log_loss", float("inf"))

        # Invert log_loss: lower is better → higher is better
        logloss_inv = 1.0 / (1.0 + logloss) if logloss != float("inf") else 0.0

        score = (
            _WEIGHT_ACCURACY * accuracy
            + _WEIGHT_MACRO_F1 * macro_f1
            + _WEIGHT_LOG_LOSS * logloss_inv
        )

        candidate_entry = dict(c)
        candidate_entry["selection_score"] = round(score, 6)
        scored.append(candidate_entry)

        if score > best_score:
            best_score = score
            best = candidate_entry

    # Sort for auditability
    scored.sort(key=lambda x: x["selection_score"], reverse=True)

    logger.info(
        "model_selected",
        best_run_id=best["run_id"] if best else None,
        best_model_type=best["model_type"] if best else None,
        best_score=best_score,
        n_candidates=len(candidates),
    )

    assert best is not None  # Guaranteed by earlier guard
    return best
