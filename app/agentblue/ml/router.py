"""ML FastAPI router (Stage 8).

Provides read-only and admin endpoints for ML datasets, training runs,
models, predictions, shadow evaluations, monitoring, and drift reports.
No QuickBooks write endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.db.session import get_db
from agentblue.ml.constants import (
    CODE_VERSION,
    FEATURE_VERSION,
    ML_ENABLED,
    ML_INFERENCE_MODE,
    ML_INFERENCE_TIMEOUT_MS,
    ML_TOP_K,
)
from agentblue.ml.models import (
    MlDataset,
    MlDriftReport,
    MlPrediction,
    MlShadowEvaluation,
    MlTrainingRun,
)
from agentblue.ml.monitoring.metrics import (
    compute_override_rate,
    compute_shadow_agreement_rate,
)
from agentblue.ml.registry.service import ModelRegistry
from agentblue.ml.schemas import (
    DatasetQualityReport,
    DatasetResponse,
    DriftReportRequest,
    DriftReportResponse,
    MLConfigResponse,
    ModelMetricsResponse,
    ModelResponse,
    PredictionResponse,
    ShadowEvaluationResponse,
    TrainingRunResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/ml",
    tags=["ml"],
)

_registry = ModelRegistry()


# ---- Datasets ----


@router.get("/datasets", response_model=list[DatasetResponse])
async def list_datasets(
    realm_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[DatasetResponse]:
    """List ML datasets, optionally filtered by realm."""
    stmt = select(MlDataset)
    if realm_id:
        stmt = stmt.where(MlDataset.realm_id == realm_id)
    stmt = stmt.order_by(MlDataset.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        DatasetResponse(
            id=d.id,
            realm_id=d.realm_id,
            status=d.status,
            feature_version=d.feature_version,
            row_count=d.row_count,
            class_count=d.class_count,
            split_summary=d.split_summary or {},
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in rows
    ]


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(
    dataset_id: str,
    db: AsyncSession = Depends(get_db),
) -> DatasetResponse:
    """Get a single dataset by ID."""
    result = await db.execute(select(MlDataset).where(MlDataset.id == dataset_id))
    d = result.scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Dataset not found.")
    return DatasetResponse(
        id=d.id,
        realm_id=d.realm_id,
        status=d.status,
        feature_version=d.feature_version,
        row_count=d.row_count,
        class_count=d.class_count,
        split_summary=d.split_summary or {},
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@router.get("/datasets/{dataset_id}/quality-report", response_model=DatasetQualityReport)
async def get_dataset_quality_report(
    dataset_id: str,
    db: AsyncSession = Depends(get_db),
) -> DatasetQualityReport:
    """Get the quality report for a dataset."""
    result = await db.execute(select(MlDataset).where(MlDataset.id == dataset_id))
    d = result.scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Dataset not found.")
    qr = d.quality_report or {}
    return DatasetQualityReport(
        dataset_id=d.id,
        total_rows=qr.get("total_rows", d.row_count),
        eligible_rows=qr.get("eligible_rows", d.row_count),
        excluded_rows=qr.get("excluded_rows", 0),
        exclusion_reasons=qr.get("exclusion_reasons", {}),
        class_distribution=qr.get("class_distribution", {}),
        warnings=qr.get("warnings", []),
    )


# ---- Training Runs ----


@router.get("/training-runs", response_model=list[TrainingRunResponse])
async def list_training_runs(
    realm_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[TrainingRunResponse]:
    """List training runs, optionally filtered by realm."""
    stmt = select(MlTrainingRun)
    if realm_id:
        stmt = stmt.where(MlTrainingRun.realm_id == realm_id)
    stmt = stmt.order_by(MlTrainingRun.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        TrainingRunResponse(
            id=t.id,
            realm_id=t.realm_id,
            dataset_id=t.dataset_id,
            status=t.status,
            model_type=t.model_type,
            calibration_method=t.calibration_method,
            model_id=t.model_id,
            metrics=t.metrics or {},
            started_at=t.started_at,
            completed_at=t.completed_at,
            error_summary=t.error_summary,
        )
        for t in rows
    ]


@router.get("/training-runs/{run_id}", response_model=TrainingRunResponse)
async def get_training_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> TrainingRunResponse:
    """Get a single training run by ID."""
    result = await db.execute(select(MlTrainingRun).where(MlTrainingRun.id == run_id))
    t = result.scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Training run not found.")
    return TrainingRunResponse(
        id=t.id,
        realm_id=t.realm_id,
        dataset_id=t.dataset_id,
        status=t.status,
        model_type=t.model_type,
        calibration_method=t.calibration_method,
        model_id=t.model_id,
        metrics=t.metrics or {},
        started_at=t.started_at,
        completed_at=t.completed_at,
        error_summary=t.error_summary,
    )


# ---- Models ----


@router.get("/models", response_model=list[ModelResponse])
async def list_models(
    realm_id: str = Query(default=""),
    status: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[ModelResponse]:
    """List ML models with optional filters."""
    models = await _registry.list_models(
        db, realm_id=realm_id or None, status=status or None, limit=limit
    )
    return [
        ModelResponse(
            id=m.id,
            realm_id=m.realm_id,
            model_type=m.model_type,
            status=m.status,
            feature_version=m.feature_version,
            code_version=m.code_version,
            calibration_method=m.calibration_method,
            artifact_path=m.artifact_path,
            artifact_sha256=m.artifact_sha256,
            training_run_id=m.training_run_id,
            promoted_at=m.promoted_at,
            retired_at=m.retired_at,
            created_at=m.created_at,
        )
        for m in models
    ]


@router.get("/models/{model_id}", response_model=ModelResponse)
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> ModelResponse:
    """Get a single model by ID."""
    model = await _registry.get_model(db, model_id)
    if model is None:
        raise HTTPException(404, "Model not found.")
    return ModelResponse(
        id=model.id,
        realm_id=model.realm_id,
        model_type=model.model_type,
        status=model.status,
        feature_version=model.feature_version,
        code_version=model.code_version,
        calibration_method=model.calibration_method,
        artifact_path=model.artifact_path,
        artifact_sha256=model.artifact_sha256,
        training_run_id=model.training_run_id,
        promoted_at=model.promoted_at,
        retired_at=model.retired_at,
        created_at=model.created_at,
    )


@router.post("/models/{model_id}/validate")
async def validate_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Transition a model from CANDIDATE to VALIDATED."""
    model = await _registry.transition_status(
        db, model_id, "VALIDATED", actor="api", reason="Validated via API"
    )
    return {"model_id": model.id, "status": model.status}


@router.post("/models/{model_id}/activate-shadow")
async def activate_shadow(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Transition a model from VALIDATED to SHADOW."""
    model = await _registry.transition_status(
        db, model_id, "SHADOW", actor="api", reason="Activated shadow via API"
    )
    return {"model_id": model.id, "status": model.status}


@router.post("/models/{model_id}/retire")
async def retire_model(
    model_id: str,
    reason: str = Query(default="Retired via API"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Transition a model to RETIRED."""
    model = await _registry.transition_status(db, model_id, "RETIRED", actor="api", reason=reason)
    return {"model_id": model.id, "status": model.status}


@router.get("/models/{model_id}/metrics", response_model=ModelMetricsResponse)
async def get_model_metrics(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> ModelMetricsResponse:
    """Get evaluation metrics for a model."""
    model = await _registry.get_model(db, model_id)
    if model is None:
        raise HTTPException(404, "Model not found.")
    m = model.metrics or {}
    return ModelMetricsResponse(
        model_id=model.id,
        accuracy=m.get("accuracy"),
        macro_f1=m.get("macro_f1"),
        weighted_f1=m.get("weighted_f1"),
        log_loss=m.get("log_loss"),
        brier_score=m.get("brier_score"),
        per_class_metrics=m.get("per_class_metrics", {}),
        confusion_matrix=m.get("confusion_matrix", []),
        evaluated_at=model.updated_at,
    )


# ---- Predictions ----


@router.get("/predictions", response_model=list[PredictionResponse])
async def list_predictions(
    realm_id: str = Query(default=""),
    model_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[PredictionResponse]:
    """List ML predictions with optional filters."""
    stmt = select(MlPrediction)
    if realm_id:
        stmt = stmt.where(MlPrediction.realm_id == realm_id)
    if model_id:
        stmt = stmt.where(MlPrediction.model_id == model_id)
    stmt = stmt.order_by(MlPrediction.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        PredictionResponse(
            id=p.id,
            transaction_id=p.transaction_id,
            model_id=p.model_id,
            top_predictions=p.top_predictions if isinstance(p.top_predictions, list) else [],
            inference_mode=p.inference_mode,
            latency_ms=p.latency_ms,
            created_at=p.created_at,
        )
        for p in rows
    ]


# ---- Shadow Evaluations ----


@router.get("/shadow-evaluations", response_model=list[ShadowEvaluationResponse])
async def list_shadow_evaluations(
    realm_id: str = Query(default=""),
    model_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[ShadowEvaluationResponse]:
    """List shadow evaluations with optional filters."""
    stmt = select(MlShadowEvaluation)
    if realm_id:
        stmt = stmt.where(MlShadowEvaluation.realm_id == realm_id)
    if model_id:
        stmt = stmt.where(MlShadowEvaluation.model_id == model_id)
    stmt = stmt.order_by(MlShadowEvaluation.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        ShadowEvaluationResponse(
            id=e.id,
            transaction_id=e.transaction_id,
            model_id=e.model_id,
            ml_account_id=e.ml_account_quickbooks_id,
            rule_account_id=e.rule_account_quickbooks_id,
            outcome=e.outcome,
            resolved=e.resolved,
            created_at=e.created_at,
        )
        for e in rows
    ]


# ---- Monitoring ----


@router.get("/monitoring/summary")
async def get_monitoring_summary(
    realm_id: str = Query(default=""),
    model_id: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get aggregated monitoring summary for shadow evaluations."""
    stmt = select(MlShadowEvaluation)
    if realm_id:
        stmt = stmt.where(MlShadowEvaluation.realm_id == realm_id)
    if model_id:
        stmt = stmt.where(MlShadowEvaluation.model_id == model_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    evaluations = [
        {
            "outcome": e.outcome,
            "resolved": e.resolved,
            "rule_account_quickbooks_id": e.rule_account_quickbooks_id,
            "resolution": e.resolved_by if e.resolved else None,
        }
        for e in rows
    ]

    return {
        "total_evaluations": len(evaluations),
        "agreement_rate": compute_shadow_agreement_rate(evaluations),
        "override_rate": compute_override_rate(evaluations),
        "resolved_count": sum(1 for e in evaluations if e["resolved"]),
        "unresolved_count": sum(1 for e in evaluations if not e["resolved"]),
    }


# ---- Drift Reports ----


@router.post("/drift-reports", response_model=DriftReportResponse)
async def create_drift_report(
    body: DriftReportRequest,
    db: AsyncSession = Depends(get_db),
) -> DriftReportResponse:
    """Generate and store a drift report for a model."""
    # Verify model exists.
    model = await _registry.get_model(db, body.model_id)
    if model is None:
        raise HTTPException(404, "Model not found.")

    now = datetime.now(UTC)
    report = MlDriftReport(
        realm_id=body.realm_id,
        model_id=body.model_id,
        window_start=now,
        window_end=now,
        feature_drift={},
        label_drift={},
        warnings=["Drift computation requires feature extraction pipeline."],
    )
    db.add(report)
    await db.flush()

    return DriftReportResponse(
        id=report.id,
        realm_id=report.realm_id,
        model_id=report.model_id,
        feature_drift=report.feature_drift or {},
        label_drift=report.label_drift or {},
        warnings=report.warnings or [],
        created_at=report.created_at,
    )


# ---- ML Config ----


@router.get("/config", response_model=MLConfigResponse)
async def get_ml_config() -> MLConfigResponse:
    """Get current ML configuration snapshot."""
    return MLConfigResponse(
        enabled=ML_ENABLED,
        inference_mode=ML_INFERENCE_MODE,
        feature_version=FEATURE_VERSION,
        code_version=CODE_VERSION,
        top_k=ML_TOP_K,
        inference_timeout_ms=ML_INFERENCE_TIMEOUT_MS,
    )
