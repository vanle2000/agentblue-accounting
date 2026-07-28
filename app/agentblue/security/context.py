"""Typed execution context for service-layer authorization.

Every privileged service operation must receive an ExecutionContext
containing the authenticated principal and correlation ID. This
prevents service methods from being invoked without authorization
context, even when called directly (not through a FastAPI route).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentblue.security.roles import Permission, has_permission

if TYPE_CHECKING:
    from agentblue.security.principal import Principal


@dataclass(frozen=True)
class ExecutionContext:
    """Authenticated context for a privileged operation.

    Carries the authenticated principal and request correlation ID.
    Service methods must accept this as a parameter and validate
    permissions/realm before performing the operation.
    """

    principal: Principal
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not self.correlation_id:
            object.__setattr__(self, "correlation_id", self.principal.correlation_id)

    def require_permission(self, permission: Permission) -> None:
        """Verify the principal has the specified permission.

        Args:
            permission: The required permission.

        Raises:
            PermissionError: If the principal lacks the permission.
        """
        if not has_permission(self.principal.roles, permission):
            raise PermissionError(
                f"Permission denied: {permission.value}. "
                f"Principal {self.principal.principal_id} has roles: "
                f"{sorted(r.value for r in self.principal.roles)}"
            )

    def require_realm(self, realm_id: str) -> None:
        """Verify the principal has access to the specified realm.

        Args:
            realm_id: The realm to check.

        Raises:
            PermissionError: If the principal lacks realm access.
        """
        if not self.principal.has_realm_access(realm_id):
            raise PermissionError(
                f"Realm access denied: {realm_id}. "
                f"Principal {self.principal.principal_id} has realms: "
                f"{sorted(self.principal.realm_ids)}"
            )

    def require_any_realm(self) -> None:
        """Verify the principal has at least one realm assignment.

        Raises:
            PermissionError: If the principal has no realm assignments.
        """
        if not self.principal.realm_ids:
            raise PermissionError(
                f"No realm assignments for principal {self.principal.principal_id}."
            )
