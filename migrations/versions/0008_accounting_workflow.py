"""Accounting workflow tables — Stage 10.

Revision ID: 0008_accounting_workflow
Revises: 0007_token_revocation
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_accounting_workflow"
down_revision = "0007_token_revocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AccountingWorkItem
    op.create_table(
        "accounting_work_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("source_system", sa.String(50), nullable=False, server_default="QUICKBOOKS"),
        sa.Column("source_transaction_id", sa.String(50), nullable=False),
        sa.Column("source_transaction_type", sa.String(50), nullable=False, server_default=""),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("vendor_or_payee", sa.String(500), nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("memo", sa.Text, nullable=False, server_default=""),
        sa.Column("current_account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("current_account_name", sa.String(200), nullable=True),
        sa.Column("recommended_account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("recommended_account_name", sa.String(200), nullable=True),
        sa.Column("recommendation_source", sa.String(30), nullable=False, server_default=""),
        sa.Column("recommendation_confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("recommendation_explanation", JSONB, nullable=False, server_default="{}"),
        sa.Column("supporting_evidence", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(30), nullable=False, server_default="INGESTED"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="LOW"),
        sa.Column("assigned_reviewer", sa.String(100), nullable=True),
        sa.Column("assigned_approver", sa.String(100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("writeback_status", sa.String(30), nullable=False, server_default="NOT_STARTED"),
        sa.Column("reconciliation_status", sa.String(30), nullable=False, server_default="NOT_STARTED"),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("source_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("failure_code", sa.String(50), nullable=True),
        sa.Column("failure_details", sa.Text, nullable=True),
        sa.Column("escalation_status", sa.String(20), nullable=False, server_default="NONE"),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approved_account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("correction_reason", sa.Text, nullable=True),
        sa.Column("duplicate_classification", sa.String(30), nullable=False, server_default="NOT_DUPLICATE"),
        sa.Column("duplicate_of_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_work_item_realm_status", "accounting_work_item", ["realm_id", "status"])
    op.create_index("ix_work_item_review_queue", "accounting_work_item", ["realm_id", "status", "priority"])
    op.create_index("ix_work_item_idempotency", "accounting_work_item", ["realm_id", "idempotency_key"])
    op.create_unique_constraint("uq_work_item_source_txn", "accounting_work_item", ["realm_id", "source_transaction_id"])

    # WorkItemTransition
    op.create_table(
        "work_item_transition",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("accounting_work_item.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=False),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("actor_principal_id", sa.String(100), nullable=False),
        sa.Column("actor_roles", JSONB, nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("metadata_snapshot", JSONB, nullable=False, server_default="{}"),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transition_work_item", "work_item_transition", ["work_item_id"])
    op.create_index("ix_transition_realm", "work_item_transition", ["realm_id"])

    # WorkItemCorrection
    op.create_table(
        "work_item_correction",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("accounting_work_item.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("previous_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("corrected_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_correction_work_item", "work_item_correction", ["work_item_id"])

    # WriteBackJob
    op.create_table(
        "write_back_job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("accounting_work_item.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("quickbooks_company_id", sa.String(50), nullable=False, server_default=""),
        sa.Column("target_transaction_id", sa.String(50), nullable=False),
        sa.Column("operation_type", sa.String(50), nullable=False, server_default="UPDATE"),
        sa.Column("expected_sync_token", sa.String(50), nullable=True),
        sa.Column("approved_payload_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("approval_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("approver_principal_id", sa.String(100), nullable=False),
        sa.Column("execution_principal_id", sa.String(100), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quickbooks_response_ref", sa.String(100), nullable=True),
        sa.Column("failure_category", sa.String(50), nullable=True),
        sa.Column("failure_message", sa.Text, nullable=True),
        sa.Column("reconciliation_status", sa.String(30), nullable=False, server_default="NOT_STARTED"),
        sa.Column("request_payload", JSONB, nullable=True),
        sa.Column("response_snapshot", JSONB, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint("uq_writeback_job_idempotency", "write_back_job", ["idempotency_key"])
    op.create_index("ix_wb_job_work_item", "write_back_job", ["work_item_id"])
    op.create_index("ix_wb_job_status", "write_back_job", ["realm_id", "status"])
    op.create_index("ix_wb_job_retry", "write_back_job", ["status", "next_retry_at"])

    # WriteBackAttempt
    op.create_table(
        "write_back_attempt",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("write_back_job.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("failure_category", sa.String(50), nullable=True),
        sa.Column("failure_message", sa.Text, nullable=True),
        sa.Column("request_payload", JSONB, nullable=True),
        sa.Column("response_snapshot", JSONB, nullable=True),
        sa.Column("quickbooks_request_id", sa.String(100), nullable=True),
        sa.Column("execution_principal_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_wb_attempt_job", "write_back_attempt", ["job_id"])
    op.create_index("ix_wb_attempt_realm", "write_back_attempt", ["realm_id"])

    # ReconciliationResult
    op.create_table(
        "reconciliation_result",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("write_back_job.id"), nullable=False),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("accounting_work_item.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("approved_state", JSONB, nullable=False, server_default="{}"),
        sa.Column("observed_state", JSONB, nullable=False, server_default="{}"),
        sa.Column("differences", JSONB, nullable=False, server_default="[]"),
        sa.Column("external_transaction_id", sa.String(50), nullable=False, server_default=""),
        sa.Column("external_sync_token", sa.String(50), nullable=True),
        sa.Column("reconciled_by", sa.String(100), nullable=False, server_default="system"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recon_job", "reconciliation_result", ["job_id"])
    op.create_index("ix_recon_status", "reconciliation_result", ["realm_id", "status"])

    # Escalation
    op.create_table(
        "escalation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("accounting_work_item.id"), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="MEDIUM"),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("supporting_evidence", JSONB, nullable=False, server_default="{}"),
        sa.Column("attempted_actions", JSONB, nullable=False, server_default="[]"),
        sa.Column("failure_history", JSONB, nullable=False, server_default="[]"),
        sa.Column("recommended_next_step", sa.Text, nullable=True),
        sa.Column("assigned_owner", sa.String(100), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_escalation_work_item", "escalation", ["work_item_id"])
    op.create_index("ix_escalation_status", "escalation", ["realm_id", "resolution_status"])

    # BatchOperation
    op.create_table(
        "batch_operation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("operation_type", sa.String(30), nullable=False),
        sa.Column("requested_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("eligible_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("successful_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("actor_principal_id", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batch_realm", "batch_operation", ["realm_id"])
    op.create_index("ix_batch_actor", "batch_operation", ["actor_principal_id"])

    # BatchOperationItem
    op.create_table(
        "batch_operation_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batch_operation.id"), nullable=False),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("accounting_work_item.id"), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batch_item_batch", "batch_operation_item", ["batch_id"])


def downgrade() -> None:
    op.drop_table("batch_operation_item")
    op.drop_table("batch_operation")
    op.drop_table("escalation")
    op.drop_table("reconciliation_result")
    op.drop_table("write_back_attempt")
    op.drop_table("write_back_job")
    op.drop_table("work_item_correction")
    op.drop_table("work_item_transition")
    op.drop_table("accounting_work_item")
