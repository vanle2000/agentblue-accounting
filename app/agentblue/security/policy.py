"""Permission enforcement dependencies for FastAPI.

Provides require_permission() factory that returns a dependency
checking whether the authenticated principal has a specific permission.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, status

from agentblue.security.auth import get_authenticated_principal
from agentblue.security.principal import Principal  # noqa: TC001
from agentblue.security.roles import Permission, has_permission

logger = structlog.get_logger(__name__)


def require_permission(permission: Permission) -> Any:
    """Create a FastAPI dependency that enforces a specific permission.

    Usage::

        @router.get("/protected")
        async def protected(
            principal: Annotated[
                Principal, Depends(require_permission(Permission.ACCOUNTING_READ))
            ],
        ) -> dict:
            ...

    Args:
        permission: The required permission.

    Returns:
        A FastAPI dependency function.
    """

    async def _check_permission(
        principal: Annotated[Principal, Depends(get_authenticated_principal)],
    ) -> Principal:
        if not has_permission(principal.roles, permission):
            logger.warning(
                "permission_denied",
                principal_id=principal.principal_id,
                required_permission=permission.value,
                principal_roles=[r.value for r in principal.roles],
                correlation_id=principal.correlation_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission.value}",
            )
        return principal

    return _check_permission


# Pre-built dependencies for common permissions.
# Usage: principal: Annotated[Principal, Depends(require_accounting_read)]
require_accounting_read = require_permission(Permission.ACCOUNTING_READ)
require_accounting_review = require_permission(Permission.ACCOUNTING_REVIEW)
require_accounting_approve = require_permission(Permission.ACCOUNTING_APPROVE)
require_accounting_writeback = require_permission(Permission.ACCOUNTING_WRITEBACK)
require_quickbooks_read = require_permission(Permission.QUICKBOOKS_READ)
require_ml_read = require_permission(Permission.ML_READ)
require_ml_dataset_create = require_permission(Permission.ML_DATASET_CREATE)
require_ml_train = require_permission(Permission.ML_TRAIN)
require_ml_shadow_activate = require_permission(Permission.ML_SHADOW_ACTIVATE)
require_audit_read = require_permission(Permission.AUDIT_READ)
require_identity_manage = require_permission(Permission.IDENTITY_MANAGE)
