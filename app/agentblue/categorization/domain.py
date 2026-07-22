"""Categorization domain enums and value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class CategorizationStatus(str, Enum):
    PENDING = "PENDING"
    RECOMMENDED = "RECOMMENDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class RecommendationSource(str, Enum):
    USER_RULE = "USER_RULE"
    APPROVED_HISTORY = "APPROVED_HISTORY"
    SYSTEM_RULE = "SYSTEM_RULE"
    VENDOR_MAPPING = "VENDOR_MAPPING"
    KEYWORD_RULE = "KEYWORD_RULE"
    TRANSACTION_HEURISTIC = "TRANSACTION_HEURISTIC"
    FEATURE_RANKING = "FEATURE_RANKING"
    MANUAL_SELECTION = "MANUAL_SELECTION"


class RuleType(str, Enum):
    EXACT_VENDOR = "EXACT_VENDOR"
    NORMALIZED_VENDOR = "NORMALIZED_VENDOR"
    DESCRIPTION_CONTAINS = "DESCRIPTION_CONTAINS"
    MEMO_CONTAINS = "MEMO_CONTAINS"
    AMOUNT_RANGE = "AMOUNT_RANGE"
    TRANSACTION_TYPE = "TRANSACTION_TYPE"
    COMPOSITE = "COMPOSITE"


class RuleStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DRAFT = "DRAFT"
    ARCHIVED = "ARCHIVED"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CHANGE_ACCOUNT = "CHANGE_ACCOUNT"
    DEFER = "DEFER"


class ConfidenceBand(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class ReasonCode(str, Enum):
    EXACT_APPROVED_VENDOR_MATCH = "EXACT_APPROVED_VENDOR_MATCH"
    EXACT_APPROVED_DESCRIPTION_MATCH = "EXACT_APPROVED_DESCRIPTION_MATCH"
    USER_RULE_MATCH = "USER_RULE_MATCH"
    SYSTEM_RULE_MATCH = "SYSTEM_RULE_MATCH"
    KEYWORD_MATCH = "KEYWORD_MATCH"
    TRANSACTION_TYPE_MATCH = "TRANSACTION_TYPE_MATCH"
    HISTORICAL_ACCOUNT_MATCH = "HISTORICAL_ACCOUNT_MATCH"
    ACCOUNT_TYPE_COMPATIBLE = "ACCOUNT_TYPE_COMPATIBLE"
    ACCOUNT_CLASSIFICATION_COMPATIBLE = "ACCOUNT_CLASSIFICATION_COMPATIBLE"
    NO_VALID_CANDIDATE = "NO_VALID_CANDIDATE"
    MULTIPLE_CLOSE_CANDIDATES = "MULTIPLE_CLOSE_CANDIDATES"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_RULES = "CONFLICTING_RULES"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


@dataclass(frozen=True)
class TransactionFeature:
    """Extracted features from a transaction for categorization."""

    realm_id: str
    transaction_id: str
    transaction_quickbooks_id: str
    transaction_type: str
    normalized_vendor: str
    normalized_description: str
    normalized_memo: str
    amount: Decimal
    absolute_amount: Decimal
    currency: str
    transaction_date: str
    line_count: int
    existing_account_ids: list[str] = field(default_factory=list)
    feature_version: str = "1.0"


@dataclass
class RecommendationCandidate:
    """A ranked account recommendation candidate."""

    account_quickbooks_id: str
    account_id: str
    rank: int
    score: Decimal
    confidence_band: ConfidenceBand
    source: RecommendationSource
    explanation: dict[str, Any] = field(default_factory=dict)
    rule_id: str = ""


@dataclass
class CategorizationResult:
    """Result of a categorization run for one transaction."""

    transaction_id: str
    status: CategorizationStatus
    recommended_account_quickbooks_id: str = ""
    confidence_score: Decimal = Decimal("0")
    confidence_band: ConfidenceBand = ConfidenceBand.NONE
    source: RecommendationSource = RecommendationSource.FEATURE_RANKING
    candidates: list[RecommendationCandidate] = field(default_factory=list)
    explanation: dict[str, Any] = field(default_factory=dict)
    requires_review: bool = True
