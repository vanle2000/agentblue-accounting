"""Token revocation table — Stage 9.

Revision ID: 0007_token_revocation
Revises: 0006_security
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_token_revocation"
down_revision = "0006_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_token",
        sa.Column("jti", sa.String(36), primary_key=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False, server_default="revoked"),
    )
    op.create_index("ix_revoked_token_expires", "revoked_token", ["expires_at"])


def downgrade() -> None:
    op.drop_table("revoked_token")
