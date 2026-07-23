"""Categorization tables — Stage 7 Level 2.

Revision ID: 0003_categorization
Revises: 0002_qb_accounting
Create Date: 2025-07-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_categorization"
down_revision = "0002_qb_accounting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A. Categorization rules
    op.create_table(
        "categorization_rule",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("rule_type", sa.String(50), nullable=False),
        sa.Column("rule_status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("precedence", sa.Integer, nullable=False, server_default="100"),
        sa.Column("conditions", postgresql.JSONB, nullable=False),
        sa.Column("target_account_quickbooks_id", sa.String(50), nullable=False),
        sa.Column(
            "target_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column("minimum_confidence", sa.Numeric(3, 2), nullable=False, server_default="0"),
        sa.Column("stop_processing", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_system_rule", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("match_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rule_realm_status", "categorization_rule", ["realm_id", "rule_status"])
    op.create_index("ix_rule_realm_active", "categorization_rule", ["realm_id", "precedence"])

    # B. Categorization run
    op.create_table(
        "categorization_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("run_type", sa.String(20), nullable=False, server_default="BATCH"),
        sa.Column("status", sa.String(20), nullable=False, server_default="RUNNING"),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("recommended_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("preselected_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("needs_review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("approved_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("applied_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("apply_failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_catrun_realm", "categorization_run", ["realm_id"])

    # C. Transaction categorization
    op.create_table(
        "transaction_categorization",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("transaction_quickbooks_id", sa.String(50), nullable=False),
        sa.Column("transaction_type", sa.String(50), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column(
            "recommended_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column("recommended_account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column(
            "approved_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column("approved_account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("current_quickbooks_account_id", sa.String(50), nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("confidence_band", sa.String(10), nullable=False, server_default="NONE"),
        sa.Column(
            "recommendation_source",
            sa.String(30),
            nullable=False,
            server_default="FEATURE_RANKING",
        ),
        sa.Column("engine_version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("feature_version", sa.String(20), nullable=False, server_default="1.0"),
        sa.Column(
            "rule_id", sa.String(36), sa.ForeignKey("categorization_rule.id"), nullable=True
        ),
        sa.Column("explanation_summary", sa.Text, nullable=True),
        sa.Column("requires_review", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("source_transaction_hash", sa.String(64), nullable=True),
        sa.Column("source_sync_token", sa.String(50), nullable=True),
        sa.Column("source_last_updated_time", sa.String(50), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("realm_id", "transaction_id", name="uq_categorization_transaction"),
    )
    op.create_index(
        "ix_cat_txn_realm_status", "transaction_categorization", ["realm_id", "status"]
    )
    op.create_index(
        "ix_cat_txn_review",
        "transaction_categorization",
        ["realm_id", "requires_review", "status"],
    )

    # D. Categorization recommendation
    op.create_table(
        "categorization_recommendation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "categorization_id",
            sa.String(36),
            sa.ForeignKey("transaction_categorization.id"),
            nullable=False,
        ),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True),
        sa.Column("account_quickbooks_id", sa.String(50), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("score", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("confidence_band", sa.String(10), nullable=False, server_default="NONE"),
        sa.Column("recommendation_source", sa.String(30), nullable=False),
        sa.Column("explanation", postgresql.JSONB, nullable=False),
        sa.Column("feature_snapshot", postgresql.JSONB, nullable=False),
        sa.Column(
            "rule_id", sa.String(36), sa.ForeignKey("categorization_rule.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "categorization_id", "account_quickbooks_id", name="uq_rec_categorization_account"
        ),
    )
    op.create_index(
        "ix_rec_categorization_rank",
        "categorization_recommendation",
        ["categorization_id", "rank"],
    )

    # E. Categorization decision (append-only)
    op.create_table(
        "categorization_decision",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "categorization_id",
            sa.String(36),
            sa.ForeignKey("transaction_categorization.id"),
            nullable=False,
        ),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column(
            "previous_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column(
            "selected_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column("categorization_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("recommendation_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_categorization", "categorization_decision", ["categorization_id"])

    # F. Vendor mapping
    op.create_table(
        "vendor_mapping",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("normalized_vendor_name", sa.String(500), nullable=False),
        sa.Column("raw_vendor_example", sa.String(500), nullable=True),
        sa.Column("vendor_quickbooks_id", sa.String(50), nullable=True),
        sa.Column(
            "target_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column("target_account_quickbooks_id", sa.String(50), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("approval_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejection_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "realm_id",
            "normalized_vendor_name",
            "target_account_quickbooks_id",
            name="uq_vendor_mapping",
        ),
    )
    op.create_index(
        "ix_vendor_realm_normalized", "vendor_mapping", ["realm_id", "normalized_vendor_name"]
    )

    # G. Training label
    op.create_table(
        "categorization_training_label",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("transaction_quickbooks_id", sa.String(50), nullable=False),
        sa.Column(
            "selected_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column("selected_account_quickbooks_id", sa.String(50), nullable=False),
        sa.Column("label_source", sa.String(30), nullable=False),
        sa.Column("feature_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_label_realm_txn", "categorization_training_label", ["realm_id", "transaction_id"]
    )

    # H. QuickBooks categorization application
    op.create_table(
        "qb_categorization_application",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "categorization_id",
            sa.String(36),
            sa.ForeignKey("transaction_categorization.id"),
            nullable=False,
        ),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("transaction_quickbooks_id", sa.String(50), nullable=False),
        sa.Column("transaction_type", sa.String(50), nullable=False),
        sa.Column(
            "selected_account_id", sa.String(36), sa.ForeignKey("qb_account.id"), nullable=True
        ),
        sa.Column("selected_account_quickbooks_id", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("source_sync_token", sa.String(50), nullable=True),
        sa.Column("source_transaction_hash", sa.String(64), nullable=True),
        sa.Column("request_payload", postgresql.JSONB, nullable=True),
        sa.Column("response_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("resulting_sync_token", sa.String(50), nullable=True),
        sa.Column("quickbooks_request_id", sa.String(100), nullable=True),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("approved_by", sa.String(100), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_summary", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_application_idempotency_key"),
    )
    op.create_index(
        "ix_app_categorization", "qb_categorization_application", ["categorization_id"]
    )
    op.create_index("ix_app_realm_status", "qb_categorization_application", ["realm_id", "status"])


def downgrade() -> None:
    op.drop_table("qb_categorization_application")
    op.drop_table("categorization_training_label")
    op.drop_table("vendor_mapping")
    op.drop_table("categorization_decision")
    op.drop_table("categorization_recommendation")
    op.drop_table("transaction_categorization")
    op.drop_table("categorization_run")
    op.drop_table("categorization_rule")
