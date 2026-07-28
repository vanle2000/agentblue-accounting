"""Roles, permissions, and RBAC policy definitions.

Deny-by-default: a role grants a defined set of permissions.
No implicit ADMIN bypass for accounting controls.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Supported principal roles."""

    VIEWER = "VIEWER"
    ACCOUNTANT = "ACCOUNTANT"
    APPROVER = "APPROVER"
    ML_OPERATOR = "ML_OPERATOR"
    ADMIN = "ADMIN"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"


class Permission(str, Enum):
    """Explicit permissions for protected operations."""

    # Accounting
    ACCOUNTING_READ = "accounting:read"
    ACCOUNTING_REVIEW = "accounting:review"
    ACCOUNTING_APPROVE = "accounting:approve"
    ACCOUNTING_WRITEBACK = "accounting:writeback"
    ACCOUNTING_RECONCILE = "accounting:reconcile"

    # QuickBooks
    QUICKBOOKS_READ = "quickbooks:read"
    QUICKBOOKS_WRITE = "quickbooks:write"

    # ML
    ML_READ = "ml:read"
    ML_DATASET_CREATE = "ml:dataset:create"
    ML_TRAIN = "ml:train"
    ML_EVALUATE = "ml:evaluate"
    ML_SHADOW_ACTIVATE = "ml:shadow:activate"

    # Audit
    AUDIT_READ = "audit:read"

    # Identity
    IDENTITY_MANAGE = "identity:manage"

    # Realm
    REALM_MANAGE = "realm:manage"


# Role → set of granted permissions.  Deny by default.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({
        Permission.ACCOUNTING_READ,
        Permission.ML_READ,
        Permission.QUICKBOOKS_READ,
    }),
    Role.ACCOUNTANT: frozenset({
        Permission.ACCOUNTING_READ,
        Permission.ACCOUNTING_REVIEW,
        Permission.ACCOUNTING_RECONCILE,
        Permission.ML_READ,
        Permission.QUICKBOOKS_READ,
    }),
    Role.APPROVER: frozenset({
        Permission.ACCOUNTING_READ,
        Permission.ACCOUNTING_REVIEW,
        Permission.ACCOUNTING_APPROVE,
        Permission.ACCOUNTING_WRITEBACK,
        Permission.ACCOUNTING_RECONCILE,
        Permission.ML_READ,
        Permission.QUICKBOOKS_READ,
    }),
    Role.ML_OPERATOR: frozenset({
        Permission.ACCOUNTING_READ,
        Permission.ML_READ,
        Permission.ML_DATASET_CREATE,
        Permission.ML_TRAIN,
        Permission.ML_EVALUATE,
        Permission.ML_SHADOW_ACTIVATE,
        Permission.QUICKBOOKS_READ,
    }),
    Role.ADMIN: frozenset({
        Permission.ACCOUNTING_READ,
        Permission.ACCOUNTING_REVIEW,
        Permission.ACCOUNTING_RECONCILE,
        Permission.QUICKBOOKS_READ,
        Permission.ML_READ,
        Permission.ML_DATASET_CREATE,
        Permission.ML_TRAIN,
        Permission.ML_EVALUATE,
        Permission.ML_SHADOW_ACTIVATE,
        Permission.AUDIT_READ,
        Permission.IDENTITY_MANAGE,
        Permission.REALM_MANAGE,
    }),
    Role.SERVICE_ACCOUNT: frozenset({
        Permission.QUICKBOOKS_READ,
        Permission.ACCOUNTING_READ,
    }),
}


def get_permissions_for_roles(roles: frozenset[Role]) -> frozenset[Permission]:
    """Return the union of permissions for the given roles."""
    perms: set[Permission] = set()
    for role in roles:
        perms |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(perms)


def has_permission(roles: frozenset[Role], permission: Permission) -> bool:
    """Check whether the given roles grant the specified permission."""
    return permission in get_permissions_for_roles(roles)
