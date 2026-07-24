"""Metrics computation for categorisation models."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    top_k_accuracy_score,
)

logger = structlog.get_logger(__name__)


def compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: np.ndarray | None = None,
    *,
    class_names: list[str] | None = None,
) -> dict[str, Any]:
    """Compute a standard set of evaluation metrics.

    Parameters
    ----------
    y_true:
        Ground-truth labels.
    y_pred:
        Predicted labels (argmax of probabilities).
    y_proba:
        Probability matrix (n_samples, n_classes).  Required for
        ``log_loss`` and ``top-3 accuracy``.
    class_names:
        Optional list of human-readable class names (same order as the
        label encoder).  Used in the per-class F1 breakdown.

    Returns
    -------
    dict
        Keys include ``accuracy``, ``macro_f1``, ``weighted_f1``,
        ``log_loss``, ``top3_accuracy``, ``brier_score``, etc.
    """
    metrics: dict[str, Any] = {}

    # Basic classification metrics
    metrics["accuracy"] = float(accuracy_score(y_true, y_pred))
    metrics["macro_f1"] = float(f1_score(y_true, y_pred, average="macro", zero_division=0))
    metrics["weighted_f1"] = float(f1_score(y_true, y_pred, average="weighted", zero_division=0))
    metrics["n_samples"] = len(y_true)
    metrics["n_classes"] = len(np.unique(y_true))

    # Probability-based metrics
    if y_proba is not None:
        try:
            metrics["log_loss"] = float(log_loss(y_true, y_proba))
        except Exception:
            metrics["log_loss"] = float("nan")

        # Top-3 accuracy (requires probability matrix and ≥ 3 classes)
        if y_proba.shape[1] >= 3:
            try:
                metrics["top3_accuracy"] = float(
                    top_k_accuracy_score(y_true, y_proba, k=3, labels=np.arange(y_proba.shape[1]))
                )
            except Exception:
                metrics["top3_accuracy"] = float("nan")
        else:
            metrics["top3_accuracy"] = float("nan")

        # Brier score — only meaningful for binary; compute the first class column
        if y_proba.shape[1] == 2:
            try:
                # Brier for the positive class
                metrics["brier_score"] = float(brier_score_loss(y_true, y_proba[:, 1]))
            except Exception:
                metrics["brier_score"] = float("nan")
        else:
            metrics["brier_score"] = float("nan")

    # Per-class F1
    per_class = f1_score(y_true, y_pred, average=None, zero_division=0)
    if class_names and len(class_names) == len(per_class):
        metrics["per_class_f1"] = {
            name: float(score) for name, score in zip(class_names, per_class, strict=False)
        }
    else:
        metrics["per_class_f1"] = {f"class_{i}": float(score) for i, score in enumerate(per_class)}

    logger.info(
        "metrics_computed",
        accuracy=metrics["accuracy"],
        macro_f1=metrics["macro_f1"],
        weighted_f1=metrics["weighted_f1"],
        n_samples=metrics["n_samples"],
    )

    return metrics


def threshold_coverage_report(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    *,
    thresholds: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Compute coverage at various confidence thresholds.

    For each threshold, report how many samples have a top-class
    probability ≥ the threshold, and what accuracy those samples have.

    Parameters
    ----------
    y_true:
        Ground-truth labels.
    y_proba:
        Probability matrix (n_samples, n_classes).
    thresholds:
        Confidence thresholds to evaluate.  Defaults to
        ``[0.50, 0.60, 0.70, 0.80, 0.90, 0.95]``.

    Returns
    -------
    list[dict]
        One dict per threshold with keys ``threshold``, ``coverage``,
        ``coverage_count``, ``total``, ``accuracy_at_threshold``.
    """
    if thresholds is None:
        thresholds = [0.50, 0.60, 0.70, 0.80, 0.90, 0.95]

    top_probs = np.max(y_proba, axis=1)
    top_pred_indices = np.argmax(y_proba, axis=1)
    total = len(y_true)

    # Map argmax indices back to original label values so that
    # accuracy_score sees consistent types when y_true is string-typed.
    unique_labels = np.unique(y_true)
    top_preds = unique_labels[top_pred_indices]

    report: list[dict[str, Any]] = []

    for thr in thresholds:
        mask = top_probs >= thr
        count = int(mask.sum())
        coverage = count / total if total > 0 else 0.0

        if count > 0:
            acc_at_thr = float(accuracy_score(y_true[mask], top_preds[mask]))
        else:
            acc_at_thr = float("nan")

        report.append(
            {
                "threshold": thr,
                "coverage": round(coverage, 4),
                "coverage_count": count,
                "total": total,
                "accuracy_at_threshold": round(acc_at_thr, 4)
                if not np.isnan(acc_at_thr)
                else None,
            }
        )

    logger.info(
        "threshold_coverage_computed",
        total=total,
        thresholds=thresholds,
    )

    return report
