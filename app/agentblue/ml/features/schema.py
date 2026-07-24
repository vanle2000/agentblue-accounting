"""Feature schema definition for ML categorization."""

from __future__ import annotations

from dataclasses import dataclass

FEATURE_SCHEMA_VERSION: str = "1.0"


@dataclass(frozen=True)
class FeatureSpec:
    """Specification for a single feature."""

    name: str
    dtype: str  # "numeric", "text", "categorical"
    description: str


# ---------------------------------------------------------------------------
# Transaction-level features (extracted from the transaction itself)
# ---------------------------------------------------------------------------

TRANSACTION_FEATURES: list[FeatureSpec] = [
    FeatureSpec(
        name="amount",
        dtype="numeric",
        description="Transaction total amount (signed, may be negative for credits).",
    ),
    FeatureSpec(
        name="absolute_amount",
        dtype="numeric",
        description="Absolute value of the transaction amount.",
    ),
    FeatureSpec(
        name="transaction_type",
        dtype="categorical",
        description="QuickBooks entity type (Purchase, Invoice, etc.).",
    ),
    FeatureSpec(
        name="transaction_date_day_of_week",
        dtype="numeric",
        description="Day of week (0=Monday … 6=Sunday) derived from transaction_date.",
    ),
    FeatureSpec(
        name="transaction_date_month",
        dtype="numeric",
        description="Month (1-12) derived from transaction_date.",
    ),
    FeatureSpec(
        name="description_text",
        dtype="text",
        description="Document number / description text, normalised.",
    ),
    FeatureSpec(
        name="memo_text",
        dtype="text",
        description="Private note / memo text, normalised.",
    ),
]

# ---------------------------------------------------------------------------
# Vendor / counterparty features
# ---------------------------------------------------------------------------

VENDOR_FEATURES: list[FeatureSpec] = [
    FeatureSpec(
        name="normalized_vendor",
        dtype="text",
        description="Normalised counterparty / vendor name.",
    ),
]

# ---------------------------------------------------------------------------
# Convenience: all features in schema order
# ---------------------------------------------------------------------------

ALL_FEATURES: list[FeatureSpec] = TRANSACTION_FEATURES + VENDOR_FEATURES
