"""Preprocessing helpers for building ML feature vectors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_TEXT_TRUNCATE_LENGTH = 500


def build_feature_vector(
    row: dict[str, Any],
    *,
    feature_names: list[str] | None = None,
) -> dict[str, Any]:
    """Build a flat feature dict from a raw extraction row.

    This function enriches the ``feature_snapshot`` with derived
    features (day-of-week, month) and sanitises missing values so
    the downstream transformer receives clean input.

    Parameters
    ----------
    row:
        A single row dict from :class:`DatasetExtractor`.
    feature_names:
        Optional explicit list of feature names to extract.
        If ``None``, all features in the schema are produced.

    Returns
    -------
    dict
        Feature name → value mapping ready for the transformer.
    """
    fs = row.get("feature_snapshot", {})

    # --- Derived temporal features ---
    txn_date = fs.get("transaction_date", row.get("transaction_date", ""))
    day_of_week = 0
    month = 1
    if txn_date:
        try:
            dt = datetime.fromisoformat(str(txn_date).replace("Z", "+00:00"))
            day_of_week = dt.weekday()
            month = dt.month
        except (ValueError, TypeError):
            pass  # Leave defaults

    # --- Amount coercion ---
    try:
        amount = float(fs.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0.0

    try:
        abs_amount = float(fs.get("absolute_amount", abs(amount)))
    except (ValueError, TypeError):
        abs_amount = abs(amount)

    # --- Text truncation ---
    def _safe_text(key: str) -> str:
        val = str(fs.get(key, ""))
        return val[:_TEXT_TRUNCATE_LENGTH]

    features: dict[str, Any] = {
        "amount": amount,
        "absolute_amount": abs_amount,
        "transaction_type": str(fs.get("transaction_type", row.get("transaction_type", ""))),
        "transaction_date_day_of_week": day_of_week,
        "transaction_date_month": month,
        "description_text": _safe_text("normalized_description"),
        "memo_text": _safe_text("normalized_memo"),
        "normalized_vendor": _safe_text("normalized_vendor"),
    }

    if feature_names is not None:
        features = {k: v for k, v in features.items() if k in feature_names}

    return features
