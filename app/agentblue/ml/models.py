"""SQLAlchemy ORM models for ML domain (Stage 8)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False)
    code_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    class_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    split_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    quality_report: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
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
    split: Mapped[str] = mapped_column(String(10), nullable=False)
    disposition: Mapped[str] = mapped_column(String(40), nullable=False, default="ELIGIBLE")
    feature_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("dataset_id", "transaction_id", name="uq_dataset_row_txn"),
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
    hyperparameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
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
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CANDIDATE")
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False)
    code_version: Mapped[str] = mapped_column(String(20), nullable=False)
    calibration_method: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE")
    training_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ml_training_run.id"), nullable=True
    )
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hyperparameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_model_realm_status", "realm_id", "status"),
        Index("ix_model_realm_type", "realm_id", "model_type"),
    )


class MlPrediction(Base):
    """A single ML inference result cached per transaction."""

    __tablename__ = "ml_prediction"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_id: Mapped[str] = mapped_column(String(36), ForeignKey("ml_model.id"), nullable=False)
    top_predictions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    inference_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("transaction_id", "model_id", name="uq_prediction_txn_model"),
        Index("ix_prediction_realm_txn", "realm_id", "transaction_id"),
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
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, default="UNRESOLVED")
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
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
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
