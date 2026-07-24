"""ML tables -- Stage 8 Level 1.

Revision ID: 0004_ml_tables
Revises: 0003_categorization
Create Date: 2025-07-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_ml_tables"
down_revision = "0003_categorization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A. ML dataset
    op.create_table(
        "ml_dataset",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("feature_version", sa.String(20), nullable=False),
        sa.Column("code_version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("class_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("split_summary", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("quality_report", postgresql.JSONB, nullable=True),
        sa.Column("superseded_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dataset_realm_status", "ml_dataset", ["realm_id", "status"])

    # B. ML dataset row
    op.create_table(
        "ml_dataset_row",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("ml_dataset.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column(
            "training_label_id",
            sa.String(36),
            sa.ForeignKey("categorization_training_label.id"),
            nullable=False,
        ),
        sa.Column("target_account_quickbooks_id", sa.String(50), nullable=False),
        sa.Column("split", sa.String(10), nullable=False),
        sa.Column("disposition", sa.String(40), nullable=False, server_default="ELIGIBLE"),
        sa.Column("feature_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_id", "transaction_id", name="uq_dataset_row_txn"),
    )
    op.create_index("ix_datasetrow_dataset_split", "ml_dataset_row", ["dataset_id", "split"])
    op.create_index("ix_datasetrow_realm_txn", "ml_dataset_row", ["realm_id", "transaction_id"])

    # C. ML model (created before training_run to break circular FK)
    op.create_table(
        "ml_model",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("model_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="CANDIDATE"),
        sa.Column("feature_version", sa.String(20), nullable=False),
        sa.Column("code_version", sa.String(20), nullable=False),
        sa.Column("calibration_method", sa.String(20), nullable=False, server_default="NONE"),
        sa.Column("artifact_path", sa.String(500), nullable=True),
        sa.Column("artifact_sha256", sa.String(64), nullable=True),
        sa.Column("hyperparameters", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_realm_status", "ml_model", ["realm_id", "status"])
    op.create_index("ix_model_realm_type", "ml_model", ["realm_id", "model_type"])

    # D. ML training run
    op.create_table(
        "ml_training_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("ml_dataset.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("model_type", sa.String(50), nullable=False),
        sa.Column("calibration_method", sa.String(20), nullable=False, server_default="NONE"),
        sa.Column("hyperparameters", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ml_model.id"), nullable=True),
        sa.Column("error_summary", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trainingrun_realm_status", "ml_training_run", ["realm_id", "status"])

    # D2. Add back-reference from model to training run (deferred FK)
    op.add_column(
        "ml_model",
        sa.Column(
            "training_run_id",
            sa.String(36),
            sa.ForeignKey("ml_training_run.id"),
            nullable=True,
        ),
    )

    # E. ML prediction
    op.create_table(
        "ml_prediction",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ml_model.id"), nullable=False),
        sa.Column("top_predictions", postgresql.JSONB, nullable=False),
        sa.Column("inference_mode", sa.String(20), nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("feature_version", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("transaction_id", "model_id", name="uq_prediction_txn_model"),
    )
    op.create_index("ix_prediction_realm_txn", "ml_prediction", ["realm_id", "transaction_id"])

    # F. ML shadow evaluation
    op.create_table(
        "ml_shadow_evaluation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column(
            "prediction_id", sa.String(36), sa.ForeignKey("ml_prediction.id"), nullable=True
        ),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ml_model.id"), nullable=False),
        sa.Column("ml_account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("rule_account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False, server_default="UNRESOLVED"),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("transaction_id", "model_id", name="uq_shadow_txn_model"),
    )
    op.create_index("ix_shadow_realm_outcome", "ml_shadow_evaluation", ["realm_id", "outcome"])

    # G. ML drift report
    op.create_table(
        "ml_drift_report",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ml_model.id"), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_drift", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("label_drift", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("warnings", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_drift_realm_model", "ml_drift_report", ["realm_id", "model_id"])

    # H. ML model event (append-only audit)
    op.create_table(
        "ml_model_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ml_model.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=True),
        sa.Column("new_status", sa.String(20), nullable=True),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_modelevent_model", "ml_model_event", ["model_id"])
    op.create_index("ix_modelevent_realm", "ml_model_event", ["realm_id"])


def downgrade() -> None:
    op.drop_table("ml_model_event")
    op.drop_table("ml_drift_report")
    op.drop_table("ml_shadow_evaluation")
    op.drop_table("ml_prediction")
    op.drop_column("ml_model", "training_run_id")
    op.drop_table("ml_training_run")
    op.drop_table("ml_model")
    op.drop_table("ml_dataset_row")
    op.drop_table("ml_dataset")
