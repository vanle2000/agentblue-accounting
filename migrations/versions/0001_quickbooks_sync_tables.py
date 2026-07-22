"""QuickBooks sync tables — Stage 5.

Revision ID: 0001_qb_sync
Revises: (head)
Create Date: 2025-07-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0001_qb_sync"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A. Source snapshot
    op.create_table(
        "qb_source_snapshot",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("quickbooks_id", sa.String(50), nullable=False),
        sa.Column("sync_token", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("source_created_at", sa.String(50), nullable=True),
        sa.Column("source_updated_at", sa.String(50), nullable=True),
        sa.Column("source_deleted_at", sa.String(50), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "realm_id", "entity_type", "quickbooks_id", name="uq_source_snapshot_identity"
        ),
    )
    op.create_index(
        "ix_source_snapshot_realm_entity", "qb_source_snapshot", ["realm_id", "entity_type"]
    )
    op.create_index("ix_source_snapshot_last_seen", "qb_source_snapshot", ["last_seen_at"])

    # B. Transaction
    op.create_table(
        "qb_transaction",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("quickbooks_id", sa.String(50), nullable=False),
        sa.Column("sync_token", sa.Integer, nullable=False, server_default="0"),
        sa.Column("transaction_date", sa.String(20), nullable=True),
        sa.Column("document_number", sa.String(50), nullable=True),
        sa.Column("private_note", sa.Text, nullable=True),
        sa.Column("currency_code", sa.String(10), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(18, 6), nullable=True),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("balance_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("source_entity_id", sa.String(36), nullable=True),
        sa.Column("counterparty_type", sa.String(20), nullable=True),
        sa.Column("counterparty_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("counterparty_name_snapshot", sa.String(255), nullable=True),
        sa.Column("account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("account_name_snapshot", sa.String(255), nullable=True),
        sa.Column("payment_type", sa.String(20), nullable=True),
        sa.Column("transaction_status", sa.String(20), nullable=True),
        sa.Column("source_deleted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("source_created_at", sa.String(50), nullable=True),
        sa.Column("source_updated_at", sa.String(50), nullable=True),
        sa.Column("first_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "realm_id", "entity_type", "quickbooks_id", name="uq_transaction_identity"
        ),
    )
    op.create_index("ix_transaction_realm_entity", "qb_transaction", ["realm_id", "entity_type"])
    op.create_index("ix_transaction_date", "qb_transaction", ["transaction_date"])
    op.create_index("ix_transaction_source_deleted", "qb_transaction", ["source_deleted"])

    # C. Transaction lines
    op.create_table(
        "qb_transaction_line",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "transaction_id", sa.String(36), sa.ForeignKey("qb_transaction.id"), nullable=False
        ),
        sa.Column("source_line_id", sa.String(50), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("detail_type", sa.String(50), nullable=True),
        sa.Column("posting_type", sa.String(10), nullable=True),
        sa.Column("account_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("account_name_snapshot", sa.String(255), nullable=True),
        sa.Column("item_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("item_name_snapshot", sa.String(255), nullable=True),
        sa.Column("customer_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("customer_name_snapshot", sa.String(255), nullable=True),
        sa.Column("vendor_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("vendor_name_snapshot", sa.String(255), nullable=True),
        sa.Column("class_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("class_name_snapshot", sa.String(255), nullable=True),
        sa.Column("department_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("department_name_snapshot", sa.String(255), nullable=True),
        sa.Column("billable_status", sa.String(20), nullable=True),
        sa.Column("tax_code_quickbooks_id", sa.String(50), nullable=True),
        sa.Column("raw_line_payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "transaction_id", "source_line_id", name="uq_transaction_line_identity"
        ),
    )
    op.create_index("ix_transaction_line_txn", "qb_transaction_line", ["transaction_id"])

    # D. Sync checkpoint
    op.create_table(
        "qb_sync_checkpoint",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("sync_mode", sa.String(20), nullable=False),
        sa.Column("last_successful_source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("realm_id", "entity_type", "sync_mode", name="uq_checkpoint_identity"),
    )
    op.create_index(
        "ix_checkpoint_realm_entity_mode",
        "qb_sync_checkpoint",
        ["realm_id", "entity_type", "sync_mode"],
    )

    # E. Sync run
    op.create_table(
        "qb_sync_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("requested_entity_types", sa.Text, nullable=False),
        sa.Column("requested_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_updated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_unchanged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_marked_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("safe_error_code", sa.String(50), nullable=True),
        sa.Column("safe_error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sync_run_realm", "qb_sync_run", ["realm_id"])
    op.create_index("ix_sync_run_status", "qb_sync_run", ["status"])

    # E2. Sync run entity
    op.create_table(
        "qb_sync_run_entity",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sync_run_id", sa.String(36), sa.ForeignKey("qb_sync_run.id"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_updated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_unchanged", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_marked_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("safe_error_code", sa.String(50), nullable=True),
        sa.Column("safe_error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sync_run_entity_run", "qb_sync_run_entity", ["sync_run_id"])
    op.create_index("ix_sync_run_entity_type", "qb_sync_run_entity", ["entity_type"])


def downgrade() -> None:
    op.drop_table("qb_sync_run_entity")
    op.drop_table("qb_sync_run")
    op.drop_table("qb_sync_checkpoint")
    op.drop_table("qb_transaction_line")
    op.drop_table("qb_transaction")
    op.drop_table("qb_source_snapshot")
