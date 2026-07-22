"""Accounting context domain models.

Transport-agnostic domain types for Chart of Accounts synchronization.
No ORM dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class AccountClassification(str, Enum):
    """High-level account classification per QuickBooks."""

    ASSET = "Asset"
    LIABILITY = "Liability"
    EQUITY = "Equity"
    REVENUE = "Revenue"
    EXPENSE = "Expense"


class ValidationStatus(str, Enum):
    """Account validation result status."""

    VALID = "VALID"
    NOT_FOUND = "NOT_FOUND"
    INACTIVE = "INACTIVE"
    SOURCE_DELETED = "SOURCE_DELETED"
    TYPE_NOT_ALLOWED = "TYPE_NOT_ALLOWED"
    CLASSIFICATION_NOT_ALLOWED = "CLASSIFICATION_NOT_ALLOWED"
    REALM_MISMATCH = "REALM_MISMATCH"


class ProposedUsage(str, Enum):
    """Proposed accounting usage for account compatibility checks."""

    EXPENSE = "expense"
    INCOME = "income"
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    BANK = "bank"
    ACCOUNTS_PAYABLE = "accounts_payable"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"


@dataclass(frozen=True)
class NormalizedAccount:
    """A normalized QuickBooks account."""

    realm_id: str
    quickbooks_id: str
    sync_token: int = 0
    name: str = ""
    fully_qualified_name: str = ""
    description: str = ""
    classification: str = ""
    account_type: str = ""
    account_subtype: str = ""
    active: bool = True
    subaccount: bool = False
    parent_quickbooks_id: str = ""
    account_number: str = ""
    currency_code: str = ""
    current_balance: Decimal = Decimal("0")
    current_balance_with_subaccounts: Decimal = Decimal("0")
    taxable: bool = False
    source_created_at: str = ""
    source_updated_at: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Structured result of account validation."""

    valid: bool
    reason_code: ValidationStatus
    message: str = ""
    account_quickbooks_id: str = ""
    active: bool = True
    source_deleted: bool = False
    account_type: str = ""
    account_subtype: str = ""
    classification: str = ""


@dataclass
class CandidateFilter:
    """Filters for account candidate queries."""

    realm_id: str
    active_only: bool = True
    account_type: str = ""
    account_subtype: str = ""
    classification: str = ""
    parent_quickbooks_id: str = ""
    name_search: str = ""
    include_subaccounts: bool = True
    max_results: int = 100


@dataclass
class UsageEvaluation:
    """Result of evaluating account suitability for a proposed usage."""

    allowed: bool
    confidence: str = "high"
    reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class HierarchyNode:
    """An account in the hierarchy tree."""

    quickbooks_id: str
    name: str
    fully_qualified_name: str
    account_type: str
    classification: str
    active: bool
    depth: int
    parent_quickbooks_id: str = ""
    children: list[HierarchyNode] = field(default_factory=list)


@dataclass
class TransactionAccountRef:
    """Resolved account reference for a transaction or line."""

    quickbooks_account_id: str
    account_id: str = ""
    account_name: str = ""
    classification: str = ""
    account_type: str = ""
    active: bool = True
    source_deleted: bool = False
    resolved: bool = False
    reason_code: str = ""
    reference_role: str = ""
