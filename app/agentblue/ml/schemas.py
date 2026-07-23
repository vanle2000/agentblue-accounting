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
    status: str
    feature_version: str
    row_count: int = 0
    class_count: int = 0
    split_summary: dict[str, Any] = Field(default_factory=dict)
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
    model_type: str
    status: str
    feature_version: str
    code_version: str
    calibration_method: str
    artifact_path: str | None = None
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
    evaluated_at: datetime | None = None


# ---- Prediction / Shadow ----


class PredictionResponse(BaseModel):
    """Single prediction result."""

    id: str
    transaction_id: str
    model_id: str
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
    outcome: str
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
    warnings: list[str] = Field(default_factory=list)
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
