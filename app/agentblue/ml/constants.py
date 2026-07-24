"""ML constants for Agent Blue Accounting (Stage 8)."""

from __future__ import annotations

from decimal import Decimal

# --- feature / code identity ---
FEATURE_VERSION: str = "1.0"
CODE_VERSION: str = "1.0.0"

# --- global ML switches ---
ML_ENABLED: bool = False
ML_INFERENCE_MODE: str = "disabled"

# --- inference ---
ML_TOP_K: int = 3
ML_INFERENCE_TIMEOUT_MS: int = 5000

# --- dataset thresholds ---
ML_MIN_CLASS_SUPPORT: int = 20
ML_MIN_DATASET_ROWS: int = 500
ML_MIN_CALIBRATION_ROWS: int = 100
ML_MIN_TEST_ROWS: int = 100

# --- training thresholds ---
MIN_EXAMPLES_PER_CLASS: int = 20
MIN_CLASSES: int = 3

# --- train / valid / test split ratios ---
SPLIT_TRAIN_RATIO: Decimal = Decimal("0.70")
SPLIT_VALID_RATIO: Decimal = Decimal("0.15")
SPLIT_TEST_RATIO: Decimal = Decimal("0.15")

# --- artifact storage ---
ML_ARTIFACT_ROOT: str = "artifacts"

# --- feature extraction ---
ML_MAX_TEXT_LENGTH: int = 500

# --- monitoring ---
DRIFT_WARNING_THRESHOLD: Decimal = Decimal("0.1")
