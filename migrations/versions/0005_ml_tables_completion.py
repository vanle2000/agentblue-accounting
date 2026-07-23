"""Complete ML persistence -- Stage 8 corrections.

Revision ID: 0005_ml_tables_completion
Revises: 0004_ml_tables
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_ml_tables_completion"
down_revision = "0004_ml_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. ml_dataset: add name, fingerprint, excluded_row_count,
    #    label_policy_version, source_start_at, source_end_at
    # ------------------------------------------------------------------
    op.add_column("ml_dataset", sa.Column("name", sa.String(200), nullable=False, server_default=""))
    op.add_column(
        "ml_dataset",
        sa.Column("dataset_fingerprint", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "ml_dataset",
        sa.Column("excluded_row_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "ml_dataset",
        sa.Column("label_policy_version", sa.String(20), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "ml_dataset",
        sa.Column("source_start_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ml_dataset",
        sa.Column("source_end_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # 2. ml_dataset_row: add row_fingerprint, update unique constraints
    # ------------------------------------------------------------------
    op.add_column(
        "ml_dataset_row",
        sa.Column("row_fingerprint", sa.String(64), nullable=False, server_default=""),
    )
    # Drop old unique constraint on (dataset_id, transaction_id).
    op.drop_constraint("uq_dataset_row_txn", "ml_dataset_row", type_="unique")
    # Add new unique constraints.
    op.create_unique_constraint(
        "uq_dataset_row_label", "ml_dataset_row", ["dataset_id", "training_label_id"]
    )
    op.create_unique_constraint(
        "uq_dataset_row_fingerprint", "ml_dataset_row", ["dataset_id", "row_fingerprint"]
    )

    # ------------------------------------------------------------------
    # 3. ml_training_run: add random_seed, feature_version, code_version,
    #    row counts, artifact fields
    # ------------------------------------------------------------------
    op.add_column(
        "ml_training_run",
        sa.Column("random_seed", sa.Integer, nullable=False, server_default="42"),
    )
    op.add_column(
        "ml_training_run",
        sa.Column("feature_version", sa.String(20), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "ml_training_run",
        sa.Column("code_version", sa.String(20), nullable=False, server_default="1.0.0"),
    )
    op.add_column("ml_training_run", sa.Column("training_row_count", sa.Integer, nullable=True))
    op.add_column("ml_training_run", sa.Column("validation_row_count", sa.Integer, nullable=True))
    op.add_column("ml_training_run", sa.Column("test_row_count", sa.Integer, nullable=True))
    op.add_column("ml_training_run", sa.Column("class_count", sa.Integer, nullable=True))
    op.add_column("ml_training_run", sa.Column("artifact_uri", sa.String(500), nullable=True))
    op.add_column("ml_training_run", sa.Column("artifact_sha256", sa.String(64), nullable=True))

    # ------------------------------------------------------------------
    # 4. ml_model: add name, model_version, label_policy_version,
    #    dataset_fingerprint, artifact_uri, class_mapping,
    #    training/validation/test/calibration metrics,
    #    unique (name, model_version), partial unique SHADOW index
    # ------------------------------------------------------------------
    op.add_column("ml_model", sa.Column("name", sa.String(200), nullable=False, server_default=""))
    op.add_column(
        "ml_model",
        sa.Column("model_version", sa.String(50), nullable=False, server_default="1"),
    )
    op.add_column(
        "ml_model",
        sa.Column("label_policy_version", sa.String(20), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "ml_model",
        sa.Column("dataset_fingerprint", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column("ml_model", sa.Column("artifact_uri", sa.String(500), nullable=True))
    op.add_column(
        "ml_model",
        sa.Column("class_mapping", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "ml_model",
        sa.Column("training_metrics", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "ml_model",
        sa.Column("validation_metrics", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "ml_model",
        sa.Column("test_metrics", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "ml_model",
        sa.Column("calibration_metrics", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    # Unique constraint: (name, model_version).
    op.create_unique_constraint("uq_model_name_version", "ml_model", ["name", "model_version"])
    # Partial unique index: at most one SHADOW model per realm.
    op.execute(
        "CREATE UNIQUE INDEX uq_model_shadow_per_realm "
        "ON ml_model (realm_id) WHERE status = 'SHADOW'"
    )

    # ------------------------------------------------------------------
    # 5. ml_prediction: add categorization_id, source_transaction_hash,
    #    predicted_account, probabilities, rank, fingerprint,
    #    constraints, checks
    # ------------------------------------------------------------------
    op.add_column(
        "ml_prediction",
        sa.Column("categorization_id", sa.String(36), nullable=False, server_default=""),
    )
    op.add_column(
        "ml_prediction",
        sa.Column("source_transaction_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "ml_prediction",
        sa.Column(
            "predicted_account_quickbooks_id", sa.String(50), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "ml_prediction",
        sa.Column(
            "raw_probability",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ml_prediction",
        sa.Column(
            "calibrated_probability",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ml_prediction",
        sa.Column("rank", sa.Integer, nullable=False, server_default="1"),
    )
    op.add_column(
        "ml_prediction",
        sa.Column("prediction_fingerprint", sa.String(64), nullable=False, server_default=""),
    )
    # Drop old unique constraint on (transaction_id, model_id).
    op.drop_constraint("uq_prediction_txn_model", "ml_prediction", type_="unique")
    # New unique constraint: (model_id, categorization_id, source_transaction_hash).
    op.create_unique_constraint(
        "uq_prediction_identity",
        "ml_prediction",
        ["model_id", "categorization_id", "source_transaction_hash"],
    )
    # Check constraints for probability ranges and rank.
    op.create_check_constraint(
        "ck_prediction_raw_prob_range",
        "ml_prediction",
        "raw_probability >= 0 AND raw_probability <= 1",
    )
    op.create_check_constraint(
        "ck_prediction_cal_prob_range",
        "ml_prediction",
        "calibrated_probability >= 0 AND calibrated_probability <= 1",
    )
    op.create_check_constraint(
        "ck_prediction_rank_positive",
        "ml_prediction",
        "rank > 0",
    )

    # ------------------------------------------------------------------
    # 6. ml_shadow_evaluation: add deterministic_account,
    #    ml_was_correct, deterministic_was_correct
    # ------------------------------------------------------------------
    op.add_column(
        "ml_shadow_evaluation",
        sa.Column("deterministic_account_quickbooks_id", sa.String(50), nullable=True),
    )
    op.add_column(
        "ml_shadow_evaluation",
        sa.Column("ml_was_correct", sa.Boolean, nullable=True),
    )
    op.add_column(
        "ml_shadow_evaluation",
        sa.Column("deterministic_was_correct", sa.Boolean, nullable=True),
    )

    # ------------------------------------------------------------------
    # 7. ml_drift_report: add prediction_drift, class_distribution,
    #    warning_count, status
    # ------------------------------------------------------------------
    op.add_column(
        "ml_drift_report",
        sa.Column(
            "prediction_drift", postgresql.JSONB, nullable=False, server_default="{}"
        ),
    )
    op.add_column(
        "ml_drift_report",
        sa.Column(
            "class_distribution", postgresql.JSONB, nullable=False, server_default="{}"
        ),
    )
    op.add_column(
        "ml_drift_report",
        sa.Column("warning_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "ml_drift_report",
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
    )


def downgrade() -> None:
    # 7. ml_drift_report
    op.drop_column("ml_drift_report", "status")
    op.drop_column("ml_drift_report", "warning_count")
    op.drop_column("ml_drift_report", "class_distribution")
    op.drop_column("ml_drift_report", "prediction_drift")

    # 6. ml_shadow_evaluation
    op.drop_column("ml_shadow_evaluation", "deterministic_was_correct")
    op.drop_column("ml_shadow_evaluation", "ml_was_correct")
    op.drop_column("ml_shadow_evaluation", "deterministic_account_quickbooks_id")

    # 5. ml_prediction
    op.drop_constraint("ck_prediction_rank_positive", "ml_prediction", type_="check")
    op.drop_constraint("ck_prediction_cal_prob_range", "ml_prediction", type_="check")
    op.drop_constraint("ck_prediction_raw_prob_range", "ml_prediction", type_="check")
    op.drop_constraint("uq_prediction_identity", "ml_prediction", type_="unique")
    op.create_unique_constraint(
        "uq_prediction_txn_model", "ml_prediction", ["transaction_id", "model_id"]
    )
    op.drop_column("ml_prediction", "prediction_fingerprint")
    op.drop_column("ml_prediction", "rank")
    op.drop_column("ml_prediction", "calibrated_probability")
    op.drop_column("ml_prediction", "raw_probability")
    op.drop_column("ml_prediction", "predicted_account_quickbooks_id")
    op.drop_column("ml_prediction", "source_transaction_hash")
    op.drop_column("ml_prediction", "categorization_id")

    # 4. ml_model
    op.execute("DROP INDEX IF EXISTS uq_model_shadow_per_realm")
    op.drop_constraint("uq_model_name_version", "ml_model", type_="unique")
    op.drop_column("ml_model", "calibration_metrics")
    op.drop_column("ml_model", "test_metrics")
    op.drop_column("ml_model", "validation_metrics")
    op.drop_column("ml_model", "training_metrics")
    op.drop_column("ml_model", "class_mapping")
    op.drop_column("ml_model", "artifact_uri")
    op.drop_column("ml_model", "dataset_fingerprint")
    op.drop_column("ml_model", "label_policy_version")
    op.drop_column("ml_model", "model_version")
    op.drop_column("ml_model", "name")

    # 3. ml_training_run
    op.drop_column("ml_training_run", "artifact_sha256")
    op.drop_column("ml_training_run", "artifact_uri")
    op.drop_column("ml_training_run", "class_count")
    op.drop_column("ml_training_run", "test_row_count")
    op.drop_column("ml_training_run", "validation_row_count")
    op.drop_column("ml_training_run", "training_row_count")
    op.drop_column("ml_training_run", "code_version")
    op.drop_column("ml_training_run", "feature_version")
    op.drop_column("ml_training_run", "random_seed")

    # 2. ml_dataset_row
    op.drop_constraint("uq_dataset_row_fingerprint", "ml_dataset_row", type_="unique")
    op.drop_constraint("uq_dataset_row_label", "ml_dataset_row", type_="unique")
    op.create_unique_constraint(
        "uq_dataset_row_txn", "ml_dataset_row", ["dataset_id", "transaction_id"]
    )
    op.drop_column("ml_dataset_row", "row_fingerprint")

    # 1. ml_dataset
    op.drop_column("ml_dataset", "source_end_at")
    op.drop_column("ml_dataset", "source_start_at")
    op.drop_column("ml_dataset", "label_policy_version")
    op.drop_column("ml_dataset", "excluded_row_count")
    op.drop_column("ml_dataset", "dataset_fingerprint")
    op.drop_column("ml_dataset", "name")
