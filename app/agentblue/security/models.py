"""Audit event ORM model.

Append-only record of every sensitive action.  No API may modify
or delete audit history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentblue.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class AuditEvent(Base):
    """Append-only audit record for sensitive actions."""

    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Actor identity
    actor_principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, default="human")
    actor_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    actor_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # Request context
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Action
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    realm_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # Outcome
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failure_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Context (sanitized — no secrets)
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        Index("ix_audit_timestamp", "timestamp"),
        Index("ix_audit_actor", "actor_principal_id"),
        Index("ix_audit_realm", "realm_id"),
        Index("ix_audit_action", "action"),
        Index("ix_audit_correlation", "correlation_id"),
    )
