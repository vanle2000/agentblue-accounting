"""ML domain enums for Agent Blue Accounting (Stage 8)."""

from __future__ import annotations

from enum import Enum


class DatasetStatus(str, Enum):
    """Lifecycle states for an ML dataset."""

    PENDING = "PENDING"
    BUILDING = "BUILDING"
    READY = "READY"
    INVALID = "INVALID"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class TrainingRunStatus(str, Enum):
    """Lifecycle states for a training run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ModelStatus(str, Enum):
    """Promotion ladder for ML models."""

    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    SHADOW = "SHADOW"
    CHAMPION = "CHAMPION"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class ModelType(str, Enum):
    """Supported model algorithms."""

    DUMMY = "DUMMY"
    LOGISTIC_REGRESSION = "LOGISTIC_REGRESSION"
    HIST_GRADIENT_BOOSTING = "HIST_GRADIENT_BOOSTING"


class CalibrationMethod(str, Enum):
    """Probability calibration strategies."""

    NONE = "NONE"
    SIGMOID = "SIGMOID"
    ISOTONIC = "ISOTONIC"
    TEMPERATURE = "TEMPERATURE"


class InferenceMode(str, Enum):
    """How ML predictions participate in categorization."""

    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    PRIMARY = "PRIMARY"


class ShadowOutcome(str, Enum):
    """Comparison result between ML and rule-based predictions."""

    AGREEMENT = "AGREEMENT"
    DISAGREEMENT = "DISAGREEMENT"
    ML_CORRECT = "ML_CORRECT"
    RULE_CORRECT = "RULE_CORRECT"
    BOTH_INCORRECT = "BOTH_INCORRECT"
    BOTH_CORRECT = "BOTH_CORRECT"
    UNRESOLVED = "UNRESOLVED"


class LabelDisposition(str, Enum):
    """Reason a training label was included or excluded."""

    ELIGIBLE = "ELIGIBLE"
    EXCLUDED_FAILED_APPLICATION = "EXCLUDED_FAILED_APPLICATION"
    EXCLUDED_STALE = "EXCLUDED_STALE"
    EXCLUDED_REJECTED = "EXCLUDED_REJECTED"
    EXCLUDED_CONFLICT = "EXCLUDED_CONFLICT"
    EXCLUDED_UNSUPPORTED_ACCOUNT = "EXCLUDED_UNSUPPORTED_ACCOUNT"
    EXCLUDED_INSUFFICIENT_CONTEXT = "EXCLUDED_INSUFFICIENT_CONTEXT"
    EXCLUDED_DUPLICATE = "EXCLUDED_DUPLICATE"
