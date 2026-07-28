"""Realm isolation enforcement.

Provides dependencies and helpers for verifying that a principal
has access to a specific realm before allowing resource access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import HTTPException, status

if TYPE_CHECKING:
    from agentblue.security.principal import Principal

logger = structlog.get_logger(__name__)


def require_realm_access(
    principal: Principal,
    realm_id: str,
) -> None:
    """Verify the principal has access to the specified realm.

    Args:
        principal: The authenticated principal.
        realm_id: The realm to check access for.

    Raises:
        HTTPException: 403 if the principal lacks realm access.
    """
    if not realm_id:
        return  # Realm-less resources are accessible

    if not principal.has_realm_access(realm_id):
        logger.warning(
            "realm_access_denied",
            principal_id=principal.principal_id,
            requested_realm=realm_id,
            allowed_realms=sorted(principal.realm_ids),
            correlation_id=principal.correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to the requested realm is not permitted.",
        )


def require_any_realm_access(
    principal: Principal,
) -> None:
    """Verify the principal has at least one realm assignment.

    Args:
        principal: The authenticated principal.

    Raises:
        HTTPException: 403 if the principal has no realm assignments.
    """
    if not principal.realm_ids:
        logger.warning(
            "no_realm_assignment",
            principal_id=principal.principal_id,
            correlation_id=principal.correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No realm assignments. Contact an administrator.",
        )
