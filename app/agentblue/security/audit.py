"""Audit event recording service.

Records sensitive actions with actor identity, realm, correlation ID,
and sanitized context.  Append-only — no modification or deletion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.security.models import AuditEvent

if TYPE_CHECKING:
    from agentblue.security.principal import Principal

logger = structlog.get_logger(__name__)

# Keys that must never appear in audit metadata.
_SENSITIVE_AUDIT_KEYS = frozenset({
    "password", "secret", "token", "authorization", "access_token",
    "refresh_token", "api_key", "client_secret", "db_password",
})


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive keys from audit metadata."""
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        if key.lower() in _SENSITIVE_AUDIT_KEYS:
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_metadata(value)
        else:
            sanitized[key] = value
    return sanitized


async def record_audit_event(
    session: AsyncSession,
    *,
    principal: Principal,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    realm_id: str = "",
    success: bool = True,
    failure_category: str | None = None,
    error_detail: str | None = None,
    metadata: dict[str, Any] | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
) -> AuditEvent:
    """Record an audit event.

    Args:
        session: Database session.
        principal: The authenticated principal performing the action.
        action: The action being performed (e.g. "categorization.approve").
        resource_type: Type of resource affected.
        resource_id: ID of resource affected.
        realm_id: Realm context.
        success: Whether the action succeeded.
        failure_category: Category of failure (e.g. "unauthorized", "not_found").
        error_detail: Safe error detail (must not contain secrets).
        metadata: Additional context (will be sanitized).
        source_ip: Client IP address.
        user_agent: Client user agent.

    Returns:
        The created AuditEvent.
    """
    event = AuditEvent(
        actor_principal_id=principal.principal_id,
        actor_type=principal.principal_type,
        actor_email=principal.email,
        actor_roles=[r.value for r in principal.roles],
        correlation_id=principal.correlation_id,
        source_ip=source_ip,
        user_agent=user_agent,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        realm_id=realm_id,
        success=success,
        failure_category=failure_category,
        error_detail=error_detail[:500] if error_detail else None,
        event_metadata=_sanitize_metadata(metadata or {}),
    )
    session.add(event)
    await session.flush()

    logger.info(
        "audit_event_recorded",
        action=action,
        actor=principal.principal_id,
        realm_id=realm_id,
        success=success,
        correlation_id=principal.correlation_id,
    )

    return event
