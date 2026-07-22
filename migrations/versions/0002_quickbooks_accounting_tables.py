"""QuickBooks accounting context tables — Stage 6.

Revision ID: 0002_qb_accounting
Revises: 0001_qb_sync
Create Date: 2025-07-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0002_qb_accounting"
down_revision = "0001_qb_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A. Account source snapshot
    op.create_table(
        "qb_account_source_snapshot",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("quickbooks_id", sa.String(50), nullable=False),
        sa.Column("sync_token", sa.Integer, nullable=False, server_default="0"),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("raw_payload_hash", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("source_deleted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("source_created_at", sa.String(50), nullable=True),
        sa.Column("source_updated_at", sa.String(50), nullable=True),
        sa.Column("source_last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("realm_id", "quickbooks_id", name="uq_account_snapshot_identity"),
    )
    op.create_index("ix_account_snapshot_realm", "qb_account_source_snapshot", ["realm_id"])

    # B. Canonical account
    op.create_table(
        "qb_account",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("quickbooks_id", sa.String(50), nullable=False),
        sa.Column("sync_token", sa.Integer, nullable=False, server_default="0"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("fully_qualified_name", sa.String(500), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("classification", sa.String(50), nullable=True),
        sa.Column("account_type", sa.String(100), nullable=True),
        sa.Column("account_subtype", sa.String(100), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("subaccount", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("parent_quickbooks_id", sa.String(50), nullable=True),
        sa.Column(
            "parent_account_id",
            sa.String(36),
            sa.ForeignKey("qb_account.id"),
            nullable=True,
        ),
        sa.Column("account_number", sa.String(50), nullable=True),
        sa.Column("currency_code", sa.String(10), nullable=True),
        sa.Column(
            "current_balance",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "current_balance_with_subaccounts",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("taxable", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("source_deleted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("source_created_at", sa.String(50), nullable=True),
        sa.Column("source_updated_at", sa.String(50), nullable=True),
        sa.Column("source_last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("realm_id", "quickbooks_id", name="uq_account_identity"),
    )
    op.create_index("ix_account_realm_active", "qb_account", ["realm_id", "active"])
    op.create_index("ix_account_realm_deleted", "qb_account", ["realm_id", "source_deleted"])
    op.create_index("ix_account_realm_type", "qb_account", ["realm_id", "account_type"])
    op.create_index(
        "ix_account_realm_classification",
        "qb_account",
        ["realm_id", "classification"],
    )
    op.create_index("ix_account_realm_parent", "qb_account", ["realm_id", "parent_quickbooks_id"])
    op.create_index("ix_account_realm_fqn", "qb_account", ["realm_id", "fully_qualified_name"])

    # C. Transaction account references
    op.create_table(
        "qb_transaction_account_ref",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("realm_id", sa.String(50), nullable=False),
        sa.Column("quickbooks_account_id", sa.String(50), nullable=False),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("qb_account.id"),
            nullable=True,
        ),
        sa.Column("reference_role", sa.String(30), nullable=False),
        sa.Column("source_line_id", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_txn_account_ref_txn", "qb_transaction_account_ref", ["transaction_id"])
    op.create_index(
        "ix_txn_account_ref_realm_account",
        "qb_transaction_account_ref",
        ["realm_id", "quickbooks_account_id"],
    )


def downgrade() -> None:
    op.drop_table("qb_transaction_account_ref")
    op.drop_table("qb_account")
    op.drop_table("qb_account_source_snapshot")
