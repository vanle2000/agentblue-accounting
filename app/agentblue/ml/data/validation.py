"""Dataset quality validation."""

from __future__ import annotations

from collections import Counter
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DatasetValidator:
    """Validates a labelled dataset meets minimum quality standards.

    Call :meth:`validate` with the rows returned by
    :class:`~agentblue.ml.data.extraction.DatasetExtractor` to obtain
    a structured quality report before proceeding with training.
    """

    def validate(
        self,
        rows: list[dict[str, Any]],
        *,
        min_rows: int = 100,
        min_classes: int = 2,
        min_per_class: int = 5,
    ) -> dict[str, Any]:
        """Return a structured quality report.

        Parameters
        ----------
        rows:
            List of dicts as returned by ``DatasetExtractor.extract_dataset``.
        min_rows:
            Minimum number of rows required.
        min_classes:
            Minimum number of unique label classes.
        min_per_class:
            Minimum examples per class.

        Returns
        -------
        dict
            Keys: ``valid``, ``errors``, ``warnings``, ``stats``.
        """
        errors: list[str] = []
        warnings: list[str] = []

        total = len(rows)

        # --- Class distribution ---
        class_counts: Counter[str] = Counter()
        for row in rows:
            label = row.get("account_quickbooks_id", "")
            class_counts[label] += 1

        num_classes = len(class_counts)

        # --- Row count ---
        if total < min_rows:
            errors.append(f"Insufficient rows: {total} < required minimum {min_rows}")

        # --- Class count ---
        if num_classes < min_classes:
            errors.append(f"Insufficient classes: {num_classes} < required minimum {min_classes}")

        # --- Per-class minimum ---
        underrepresented: list[str] = []
        for cls, count in sorted(class_counts.items()):
            if count < min_per_class:
                underrepresented.append(f"{cls} ({count})")

        if underrepresented:
            errors.append(
                f"Under-represented classes (< {min_per_class} examples): "
                + ", ".join(underrepresented)
            )

        # --- Warnings (non-fatal) ---
        if total < min_rows * 2:
            warnings.append(f"Small dataset ({total} rows). Consider collecting more labels.")

        # Class imbalance warning
        if num_classes > 1:
            max_count = max(class_counts.values())
            min_count = min(class_counts.values())
            imbalance_ratio = max_count / max(min_count, 1)
            if imbalance_ratio > 10.0:
                warnings.append(
                    f"Severe class imbalance: ratio {imbalance_ratio:.1f}x "
                    f"({max_count} vs {min_count} examples)."
                )

        valid = len(errors) == 0

        report: dict[str, Any] = {
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
            "stats": {
                "total_rows": total,
                "num_classes": num_classes,
                "class_distribution": dict(class_counts.most_common()),
                "min_per_class_actual": min(class_counts.values()) if class_counts else 0,
                "max_per_class_actual": max(class_counts.values()) if class_counts else 0,
            },
        }

        logger.info(
            "dataset_validated",
            valid=valid,
            errors=len(errors),
            warnings=len(warnings),
            total_rows=total,
            num_classes=num_classes,
        )

        return report
