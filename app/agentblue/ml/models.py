"""SQLAlchemy ORM models for ML domain (Stage 8)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentblue.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class MlDataset(Base):
    """A materialised training dataset snapshot."""

    __tablename__ = "ml_dataset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False)
    code_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    label_policy_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    class_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    split_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    quality_report: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (Index("ix_dataset_realm_status", "realm_id", "status"),)


class MlDatasetRow(Base):
    """Single row in a materialised dataset (link to training label)."""

    __tablename__ = "ml_dataset_row"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ml_dataset.id"), nullable=False
    )
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    training_label_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("categorization_training_label.id"), nullable=False
    )
    target_account_quickbooks_id: Mapped[str] = mapped_column(String(50), nullable=False)
    row_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    split: Mapped[str] = mapped_column(String(10), nullable=False)
    disposition: Mapped[str] = mapped_column(String(40), nullable=False, default="ELIGIBLE")
    feature_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "training_label_id", name="uq_dataset_row_label"
        ),
        UniqueConstraint("dataset_id", "row_fingerprint", name="uq_dataset_row_fingerprint"),
        Index("ix_datasetrow_dataset_split", "dataset_id", "split"),
        Index("ix_datasetrow_realm_txn", "realm_id", "transaction_id"),
    )


class MlTrainingRun(Base):
    """Records a single model training execution."""

    __tablename__ = "ml_training_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ml_dataset.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    calibration_method: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE")
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    code_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    hyperparameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    training_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    test_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    class_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ml_model.id"), nullable=True
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_trainingrun_realm_status", "realm_id", "status"),)


class MlModel(Base):
    """A trained ML model artifact and its promotion state."""

    __tablename__ = "ml_model"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    model_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1")
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CANDIDATE")
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False)
    label_policy_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    code_version: Mapped[str] = mapped_column(String(20), nullable=False)
    calibration_method: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE")
    training_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ml_training_run.id"), nullable=True
    )
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    class_mapping: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    hyperparameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    training_metrics: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    validation_metrics: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    test_metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    calibration_metrics: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("name", "model_version", name="uq_model_name_version"),
        Index("ix_model_realm_status", "realm_id", "status"),
        Index("ix_model_realm_type", "realm_id", "model_type"),
        # Database-level safeguard: at most one SHADOW model per realm.
        Index(
            "uq_model_shadow_per_realm",
            "realm_id",
            unique=True,
            postgresql_where="status = 'SHADOW'",
        ),
    )


class MlPrediction(Base):
    """A single ML inference result cached per transaction."""

    __tablename__ = "ml_prediction"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    categorization_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    source_transaction_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_id: Mapped[str] = mapped_column(String(36), ForeignKey("ml_model.id"), nullable=False)
    predicted_account_quickbooks_id: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    raw_probability: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0.0
    )
    calibrated_probability: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0.0
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    prediction_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    top_predictions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    inference_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "model_id",
            "categorization_id",
            "source_transaction_hash",
            name="uq_prediction_identity",
        ),
        Index("ix_prediction_realm_txn", "realm_id", "transaction_id"),
        CheckConstraint(
            "raw_probability >= 0 AND raw_probability <= 1",
            name="ck_prediction_raw_prob_range",
        ),
        CheckConstraint(
            "calibrated_probability >= 0 AND calibrated_probability <= 1",
            name="ck_prediction_cal_prob_range",
        ),
        CheckConstraint("rank > 0", name="ck_prediction_rank_positive"),
    )


class MlShadowEvaluation(Base):
    """Comparison between ML and rule-based categorization."""

    __tablename__ = "ml_shadow_evaluation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    prediction_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ml_prediction.id"), nullable=True
    )
    model_id: Mapped[str] = mapped_column(String(36), ForeignKey("ml_model.id"), nullable=False)
    ml_account_quickbooks_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rule_account_quickbooks_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    deterministic_account_quickbooks_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, default="UNRESOLVED")
    ml_was_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deterministic_was_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("transaction_id", "model_id", name="uq_shadow_txn_model"),
        Index("ix_shadow_realm_outcome", "realm_id", "outcome"),
    )


class MlDriftReport(Base):
    """Feature or label distribution drift report."""

    __tablename__ = "ml_drift_report"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(36), ForeignKey("ml_model.id"), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_drift: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    label_drift: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    prediction_drift: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    class_distribution: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_drift_realm_model", "realm_id", "model_id"),)


class MlModelEvent(Base):
    """Append-only lifecycle event for a model (promotion, demotion, etc.)."""

    __tablename__ = "ml_model_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_id: Mapped[str] = mapped_column(String(36), ForeignKey("ml_model.id"), nullable=False)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_modelevent_model", "model_id"),
        Index("ix_modelevent_realm", "realm_id"),
    )
