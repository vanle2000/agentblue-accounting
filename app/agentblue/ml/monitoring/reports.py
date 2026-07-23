"""Report generation for ML monitoring.

Generates structured evaluation reports from metrics, calibration data,
and threshold tables.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def generate_evaluation_report(
    metrics: dict[str, Any],
    calibration: dict[str, Any] | None = None,
    threshold_table: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate a structured evaluation report.

    Args:
        metrics: Evaluation metrics dict with keys like accuracy, macro_f1,
            weighted_f1, log_loss, brier_score, per_class_metrics, etc.
        calibration: Optional calibration diagnostics dict.
        threshold_table: Optional list of threshold-performance rows.

    Returns:
        A structured report dict with sections for metrics, calibration,
        thresholds, and metadata.
    """
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": _build_metrics_section(metrics),
        "calibration": _build_calibration_section(calibration),
        "threshold_table": _build_threshold_section(threshold_table),
        "warnings": _collect_warnings(metrics, calibration),
    }

    logger.info(
        "evaluation_report_generated",
        warning_count=len(report["warnings"]),
    )
    return report


def _build_metrics_section(metrics: dict[str, Any]) -> dict[str, Any]:
    """Build the metrics summary section."""
    section: dict[str, Any] = {
        "accuracy": metrics.get("accuracy"),
        "macro_f1": metrics.get("macro_f1"),
        "weighted_f1": metrics.get("weighted_f1"),
        "log_loss": metrics.get("log_loss"),
        "brier_score": metrics.get("brier_score"),
    }

    per_class = metrics.get("per_class_metrics", {})
    if per_class:
        section["per_class"] = per_class
        # Identify classes with low F1.
        low_f1_classes = []
        for cls, cls_metrics in per_class.items():
            if isinstance(cls_metrics, dict):
                f1 = cls_metrics.get("f1_score", 1.0)
                if isinstance(f1, int | float) and f1 < 0.5:
                    low_f1_classes.append({"class": cls, "f1": f1})
        if low_f1_classes:
            section["low_f1_classes"] = low_f1_classes

    confusion = metrics.get("confusion_matrix", [])
    if confusion:
        section["confusion_matrix"] = confusion

    return section


def _build_calibration_section(
    calibration: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the calibration diagnostics section."""
    if calibration is None:
        return {"available": False}

    return {
        "available": True,
        "method": calibration.get("method", "UNKNOWN"),
        "expected_calibration_error": calibration.get("expected_calibration_error"),
        "maximum_calibration_error": calibration.get("maximum_calibration_error"),
        "calibration_curve": calibration.get("calibration_curve", []),
        "bin_statistics": calibration.get("bin_statistics", []),
    }


def _build_threshold_section(
    threshold_table: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build the threshold performance section."""
    if threshold_table is None:
        return []

    return [
        {
            "threshold": row.get("threshold"),
            "precision": row.get("precision"),
            "recall": row.get("recall"),
            "f1": row.get("f1"),
            "support": row.get("support"),
        }
        for row in threshold_table
    ]


def _collect_warnings(
    metrics: dict[str, Any],
    calibration: dict[str, Any] | None,
) -> list[str]:
    """Collect warnings from metrics and calibration data."""
    warnings: list[str] = []

    # Check for low overall accuracy.
    accuracy = metrics.get("accuracy")
    if accuracy is not None and isinstance(accuracy, int | float) and accuracy < 0.6:
        warnings.append(f"Low overall accuracy: {accuracy:.3f}")

    # Check for high log loss.
    log_loss = metrics.get("log_loss")
    if log_loss is not None and isinstance(log_loss, int | float) and log_loss > 1.0:
        warnings.append(f"High log loss: {log_loss:.3f}")

    # Check for poor calibration.
    if calibration is not None:
        ece = calibration.get("expected_calibration_error")
        if ece is not None and isinstance(ece, int | float) and ece > 0.1:
            warnings.append(f"High expected calibration error: {ece:.3f}")

    return warnings
