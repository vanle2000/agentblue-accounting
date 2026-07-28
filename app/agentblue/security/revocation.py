"""Revoked token persistence for jti-based token revocation.

Stores only the jti (token ID) and expiration — never the raw token.
Expired revocations are cleaned up on each validation check.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import DateTime, Index, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agentblue.db.base import Base

logger = structlog.get_logger(__name__)


class RevokedToken(Base):
    """Record of a revoked JWT token ID (jti).

    Only the jti and expiration are stored — raw tokens are never persisted.
    """

    __tablename__ = "revoked_token"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(100), nullable=False, default="revoked")

    __table_args__ = (
        Index("ix_revoked_token_expires", "expires_at"),
    )


async def revoke_token(
    session: AsyncSession,
    *,
    jti: str,
    expires_at: datetime,
    reason: str = "revoked",
) -> RevokedToken:
    """Revoke a token by its jti. Idempotent — re-revoking is a no-op.

    Args:
        session: Database session.
        jti: The token's unique identifier.
        expires_at: When the token would have expired.
        reason: Why the token was revoked.

    Returns:
        The RevokedToken record.
    """
    # Check if already revoked (idempotent).
    existing = await session.execute(
        select(RevokedToken).where(RevokedToken.jti == jti)
    )
    if existing.scalar_one_or_none() is not None:
        logger.debug("token_already_revoked", jti=jti)
        return existing.scalar_one()

    record = RevokedToken(
        jti=jti,
        revoked_at=datetime.now(UTC),
        expires_at=expires_at,
        reason=reason,
    )
    session.add(record)
    await session.flush()

    logger.info("token_revoked", jti=jti, reason=reason)
    return record


async def is_token_revoked(
    session: AsyncSession,
    jti: str,
) -> bool:
    """Check if a token jti has been revoked.

    Also cleans up expired revocation records to bound storage growth.

    Args:
        session: Database session.
        jti: The token's unique identifier.

    Returns:
        True if the token has been revoked.
    """
    result = await session.execute(
        select(RevokedToken).where(RevokedToken.jti == jti)
    )
    record = result.scalar_one_or_none()

    if record is not None:
        return True

    # Cleanup expired revocations (non-blocking, best-effort).
    now = datetime.now(UTC)
    from sqlalchemy import delete

    await session.execute(
        delete(RevokedToken).where(RevokedToken.expires_at < now)
    )

    return False
