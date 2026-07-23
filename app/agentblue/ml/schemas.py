"""Pydantic schemas for ML domain (Stage 8)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

# ---- Dataset ----


class DatasetRequest(BaseModel):
    """Request to build a new dataset."""

    realm_id: str
    feature_version: str = "1.0"
    min_rows: int = 500
    min_class_support: int = 20


class DatasetResponse(BaseModel):
    """Dataset summary."""

    id: str
    realm_id: str
    name: str = ""
    status: str
    feature_version: str
    dataset_fingerprint: str = ""
    label_policy_version: str = "1.0"
    row_count: int = 0
    excluded_row_count: int = 0
    class_count: int = 0
    split_summary: dict[str, Any] = Field(default_factory=dict)
    source_start_at: datetime | None = None
    source_end_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DatasetQualityReport(BaseModel):
    """Quality diagnostics for a built dataset."""

    dataset_id: str
    total_rows: int = 0
    eligible_rows: int = 0
    excluded_rows: int = 0
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)
    class_distribution: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


# ---- Training Run ----


class TrainingRunRequest(BaseModel):
    """Request to start a training run."""

    realm_id: str
    dataset_id: str
    model_type: str = "HIST_GRADIENT_BOOSTING"
    calibration_method: str = "ISOTONIC"
    hyperparameters: dict[str, Any] = Field(default_factory=dict)


class TrainingRunResponse(BaseModel):
    """Training run summary."""

    id: str
    realm_id: str
    dataset_id: str
    status: str
    model_type: str
    calibration_method: str
    random_seed: int = 42
    feature_version: str = "1.0"
    code_version: str = "1.0.0"
    training_row_count: int | None = None
    validation_row_count: int | None = None
    test_row_count: int | None = None
    class_count: int | None = None
    artifact_uri: str | None = None
    artifact_sha256: str | None = None
    model_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_summary: str | None = None


# ---- Model ----


class ModelResponse(BaseModel):
    """Model summary."""

    id: str
    realm_id: str
    name: str = ""
    model_version: str = "1"
    model_type: str
    status: str
    feature_version: str
    label_policy_version: str = "1.0"
    code_version: str
    calibration_method: str
    dataset_fingerprint: str = ""
    artifact_path: str | None = None
    artifact_uri: str | None = None
    artifact_sha256: str | None = None
    training_run_id: str | None = None
    promoted_at: datetime | None = None
    retired_at: datetime | None = None
    created_at: datetime | None = None


class ModelMetricsResponse(BaseModel):
    """Evaluation metrics for a model."""

    model_id: str
    accuracy: Decimal | None = None
    macro_f1: Decimal | None = None
    weighted_f1: Decimal | None = None
    log_loss: Decimal | None = None
    brier_score: Decimal | None = None
    per_class_metrics: dict[str, Any] = Field(default_factory=dict)
    confusion_matrix: list[list[int]] = Field(default_factory=list)
    training_metrics: dict[str, Any] = Field(default_factory=dict)
    validation_metrics: dict[str, Any] = Field(default_factory=dict)
    test_metrics: dict[str, Any] = Field(default_factory=dict)
    calibration_metrics: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime | None = None


# ---- Prediction / Shadow ----


class PredictionResponse(BaseModel):
    """Single prediction result."""

    id: str
    transaction_id: str
    categorization_id: str = ""
    source_transaction_hash: str = ""
    model_id: str
    predicted_account_quickbooks_id: str = ""
    raw_probability: Decimal | None = None
    calibrated_probability: Decimal | None = None
    rank: int = 1
    prediction_fingerprint: str = ""
    top_predictions: list[dict[str, Any]] = Field(default_factory=list)
    inference_mode: str
    latency_ms: int | None = None
    created_at: datetime | None = None


class ShadowEvaluationResponse(BaseModel):
    """Shadow evaluation summary."""

    id: str
    transaction_id: str
    model_id: str
    ml_account_id: str | None = None
    rule_account_id: str | None = None
    deterministic_account_quickbooks_id: str | None = None
    outcome: str
    ml_was_correct: bool | None = None
    deterministic_was_correct: bool | None = None
    resolved: bool = False
    created_at: datetime | None = None


# ---- Drift ----


class DriftReportRequest(BaseModel):
    """Request to generate a drift report."""

    realm_id: str
    model_id: str
    window_days: int = 30


class DriftReportResponse(BaseModel):
    """Drift report summary."""

    id: str
    realm_id: str
    model_id: str
    feature_drift: dict[str, Any] = Field(default_factory=dict)
    label_drift: dict[str, Any] = Field(default_factory=dict)
    prediction_drift: dict[str, Any] = Field(default_factory=dict)
    class_distribution: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    warning_count: int = 0
    status: str = "PENDING"
    created_at: datetime | None = None


# ---- ML Config ----


class MLConfigResponse(BaseModel):
    """Current ML configuration snapshot."""

    enabled: bool = False
    inference_mode: str = "disabled"
    feature_version: str = "1.0"
    code_version: str = "1.0.0"
    top_k: int = 3
    inference_timeout_ms: int = 5000
    champion_model_id: str | None = None
