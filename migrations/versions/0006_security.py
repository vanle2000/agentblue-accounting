"""Security tables — Stage 9.

Revision ID: 0006_security
Revises: 0005_ml_tables_completion
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_security"
down_revision = "0005_ml_tables_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A. Audit event (append-only)
    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        # Actor identity
        sa.Column("actor_principal_id", sa.String(100), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False, server_default="human"),
        sa.Column("actor_email", sa.String(200), nullable=False, server_default=""),
        sa.Column("actor_roles", postgresql.JSONB, nullable=False, server_default="[]"),
        # Request context
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        # Action
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False, server_default=""),
        sa.Column("resource_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("realm_id", sa.String(50), nullable=False, server_default=""),
        # Outcome
        sa.Column("success", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("failure_category", sa.String(50), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        # Context (sanitized)
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_timestamp", "audit_event", ["timestamp"])
    op.create_index("ix_audit_actor", "audit_event", ["actor_principal_id"])
    op.create_index("ix_audit_realm", "audit_event", ["realm_id"])
    op.create_index("ix_audit_action", "audit_event", ["action"])
    op.create_index("ix_audit_correlation", "audit_event", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("audit_event")
