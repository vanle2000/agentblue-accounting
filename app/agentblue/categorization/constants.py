"""Categorization configuration constants."""

from __future__ import annotations

from decimal import Decimal

ENGINE_VERSION = "1.0.0"

# Scoring weights
WEIGHT_USER_RULE = Decimal("0.55")
WEIGHT_VENDOR_HISTORY = Decimal("0.25")
WEIGHT_KEYWORD = Decimal("0.10")
WEIGHT_ACCOUNT_COMPAT = Decimal("0.10")
WEIGHT_FUZZY_MAX = Decimal("0.05")
CONFLICT_PENALTY = Decimal("0.15")

# Confidence thresholds
HIGH_CONFIDENCE_THRESHOLD = Decimal("0.85")
MEDIUM_CONFIDENCE_THRESHOLD = Decimal("0.65")

# Historical matching
MIN_HISTORICAL_APPROVALS = 2
MIN_APPROVAL_RATIO = Decimal("0.7")

# Limits
MAX_TRANSACTIONS_PER_RUN = 500
MAX_CANDIDATE_ACCOUNTS = 50
MAX_REVIEW_PAGE_SIZE = 100

FEATURE_VERSION = "1.0"
