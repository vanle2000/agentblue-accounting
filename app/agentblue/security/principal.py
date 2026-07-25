"""Authenticated principal model.

Resolved from a valid JWT token. Contains identity, roles, realm
assignments, and request context. Never trusts client-supplied values
without verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from agentblue.security.roles import Role


@dataclass(frozen=True)
class Principal:
    """Authenticated identity resolved from a validated token."""

    principal_id: str
    principal_type: str  # "human" or "service"
    email: str = ""
    display_name: str = ""
    active: bool = True
    roles: frozenset[Role] = field(default_factory=frozenset)
    realm_ids: frozenset[str] = field(default_factory=frozenset)
    auth_method: str = "jwt"
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

    def has_role(self, role: Role) -> bool:
        """Check if the principal has a specific role."""
        return role in self.roles

    def has_realm_access(self, realm_id: str) -> bool:
        """Check if the principal has access to a specific realm."""
        if not realm_id:
            return True  # Realm-less resources are accessible
        return realm_id in self.realm_ids

    def to_audit_dict(self) -> dict[str, object]:
        """Return a safe representation for audit logging.

        Excludes sensitive fields.
        """
        return {
            "principal_id": self.principal_id,
            "principal_type": self.principal_type,
            "email": self.email,
            "roles": sorted(r.value for r in self.roles),
            "realm_ids": sorted(self.realm_ids),
            "auth_method": self.auth_method,
            "correlation_id": self.correlation_id,
        }
