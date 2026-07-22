"""QuickBooks Account normalizer.

Extracts and normalizes account fields from raw QuickBooks API payloads.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from agentblue.integrations.quickbooks.accounting.domain import NormalizedAccount

logger = structlog.get_logger(__name__)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _safe_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _extract_ref(ref: dict[str, Any]) -> str:
    """Extract value from a QuickBooks ReferenceType dict."""
    return _safe_str(ref.get("value"))


def normalize_account(raw: dict[str, Any], realm_id: str) -> NormalizedAccount:
    """Normalize a raw QuickBooks Account entity.

    Missing optional fields produce empty strings or zero decimals.
    Unknown account types/subtypes are preserved as raw strings.
    """
    parent_ref = raw.get("ParentRef", {})

    return NormalizedAccount(
        realm_id=realm_id,
        quickbooks_id=_safe_str(raw.get("Id")),
        sync_token=_safe_int(raw.get("SyncToken")),
        name=_safe_str(raw.get("Name")),
        fully_qualified_name=_safe_str(raw.get("FullyQualifiedName")),
        description=_safe_str(raw.get("Description")),
        classification=_safe_str(raw.get("Classification")),
        account_type=_safe_str(raw.get("AccountType")),
        account_subtype=_safe_str(raw.get("AccountSubType")),
        active=bool(raw.get("Active", True)),
        subaccount=bool(raw.get("SubAccount", False)),
        parent_quickbooks_id=_extract_ref(parent_ref),
        account_number=_safe_str(raw.get("AcctNum")),
        currency_code=_safe_str(raw.get("CurrencyRef", {}).get("value")),
        current_balance=_safe_decimal(raw.get("CurrentBalance")),
        current_balance_with_subaccounts=_safe_decimal(raw.get("CurrentBalanceWithSubAccounts")),
        taxable=bool(raw.get("TaxAccount", False)),
        source_created_at=_safe_str(raw.get("MetaData", {}).get("CreateTime")),
        source_updated_at=_safe_str(raw.get("MetaData", {}).get("LastUpdatedTime")),
        raw_payload=raw,
    )
