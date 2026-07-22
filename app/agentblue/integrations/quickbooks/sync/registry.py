"""QuickBooks entity registry — central configuration for supported entities.

Each entity type is described by a registry entry that defines its
QuickBooks API name, query behavior, field mappings, and normalization
strategy. This avoids uncontrolled if/elif chains and makes adding
new entities straightforward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import structlog

from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksUnsupportedEntityError,
)
from agentblue.integrations.quickbooks.sync.domain import (
    EntityType,
    NormalizedTransaction,
    NormalizedTransactionLine,
)

logger = structlog.get_logger(__name__)


# --- Normalization protocol ---


class EntityNormalizer(Protocol):
    """Protocol for entity-specific normalization adapters."""

    def normalize(
        self,
        raw: dict[str, Any],
        realm_id: str,
    ) -> NormalizedTransaction:
        """Normalize a raw QuickBooks entity into a canonical transaction."""
        ...


# --- Helper functions ---


def _safe_decimal(value: Any, default: str = "0") -> Decimal:
    """Safely convert a value to Decimal."""
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _safe_str(value: Any) -> str:
    """Safely convert a value to string, returning empty for None."""
    if value is None:
        return ""
    return str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert to int."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _extract_ref(ref: dict[str, Any]) -> tuple[str, str]:
    """Extract (value, name) from a QuickBooks ReferenceType dict."""
    return _safe_str(ref.get("value")), _safe_str(ref.get("name"))


def _extract_lines(raw: dict[str, Any], entity_type: str) -> list[NormalizedTransactionLine]:
    """Extract and normalize transaction lines from raw payload."""
    raw_lines = raw.get("Line", [])
    lines: list[NormalizedTransactionLine] = []
    for i, line in enumerate(raw_lines):
        line_id = _safe_str(line.get("Id", str(i + 1)))
        detail_type = _safe_str(line.get("DetailType"))
        amount = _safe_decimal(line.get("Amount"))

        line_obj = NormalizedTransactionLine(
            source_line_id=line_id,
            line_number=i + 1,
            description=_safe_str(line.get("Description")),
            amount=amount,
            detail_type=detail_type,
            raw_line_payload=line,
        )

        # Extract detail-specific fields
        detail = line.get(detail_type, {}) if detail_type else {}
        if isinstance(detail, dict):
            line_obj.account_quickbooks_id, line_obj.account_name_snapshot = _extract_ref(
                detail.get("AccountRef", {})
            )
            line_obj.item_quickbooks_id, line_obj.item_name_snapshot = _extract_ref(
                detail.get("ItemRef", {})
            )
            line_obj.customer_quickbooks_id, line_obj.customer_name_snapshot = _extract_ref(
                detail.get("CustomerRef", {})
            )
            line_obj.vendor_quickbooks_id, line_obj.vendor_name_snapshot = _extract_ref(
                detail.get("VendorRef", {})
            )
            line_obj.class_quickbooks_id, line_obj.class_name_snapshot = _extract_ref(
                detail.get("ClassRef", {})
            )
            line_obj.department_quickbooks_id, line_obj.department_name_snapshot = _extract_ref(
                detail.get("DepartmentRef", {})
            )
            line_obj.billable_status = _safe_str(detail.get("BillableStatus"))
            line_obj.tax_code_quickbooks_id = _safe_str(detail.get("TaxCodeRef", {}).get("value"))

            # JournalEntry posting type
            if detail_type == "JournalEntryLineDetail":
                line_obj.posting_type = _safe_str(detail.get("PostingType"))

        lines.append(line_obj)
    return lines


def _extract_transaction_header(
    raw: dict[str, Any],
    realm_id: str,
    entity_type: EntityType,
    date_field: str = "TxnDate",
    amount_field: str = "TotalAmt",
    balance_field: str | None = None,
    counterparty_type: str = "",
    counterparty_ref_field: str = "",
    account_ref_field: str = "",
    payment_type_field: str | None = None,
    status_field: str | None = None,
) -> NormalizedTransaction:
    """Extract common transaction header fields."""
    txn_date = _safe_str(raw.get(date_field))
    total = _safe_decimal(raw.get(amount_field))
    balance = _safe_decimal(raw.get(balance_field)) if balance_field else None

    cp_id, cp_name = "", ""
    if counterparty_ref_field:
        cp_id, cp_name = _extract_ref(raw.get(counterparty_ref_field, {}))

    acct_id, acct_name = "", ""
    if account_ref_field:
        acct_id, acct_name = _extract_ref(raw.get(account_ref_field, {}))

    return NormalizedTransaction(
        realm_id=realm_id,
        entity_type=entity_type,
        quickbooks_id=_safe_str(raw.get("Id")),
        sync_token=_safe_int(raw.get("SyncToken")),
        transaction_date=txn_date,
        document_number=_safe_str(raw.get("DocNum") or raw.get("DocNumber")),
        private_note=_safe_str(raw.get("PrivateNote")),
        currency_code=_safe_str(raw.get("CurrencyRef", {}).get("value")),
        exchange_rate=_safe_decimal(raw.get("ExchangeRate")) if raw.get("ExchangeRate") else None,
        total_amount=total,
        balance_amount=balance if balance_field else None,
        counterparty_type=counterparty_type,
        counterparty_quickbooks_id=cp_id,
        counterparty_name_snapshot=cp_name,
        account_quickbooks_id=acct_id,
        account_name_snapshot=acct_name,
        payment_type=_safe_str(raw.get(payment_type_field)) if payment_type_field else "",
        transaction_status=_safe_str(raw.get(status_field)) if status_field else "",
        source_created_at=_safe_str(raw.get("MetaData", {}).get("CreateTime")),
        source_updated_at=_safe_str(raw.get("MetaData", {}).get("LastUpdatedTime")),
        source_deleted_at=_safe_str(raw.get("MetaData", {}).get("DeletedTime", "")),
        raw_payload=raw,
    )


# --- Concrete normalizers ---


class PurchaseNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.PURCHASE,
            amount_field="TotalAmt",
            counterparty_type="Vendor",
            counterparty_ref_field="EntityRef",
            account_ref_field="AccountRef",
            payment_type_field="PaymentType",
        )
        txn.lines = _extract_lines(raw, "Purchase")
        return txn


class DepositNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.DEPOSIT,
            amount_field="TotalAmt",
            account_ref_field="DepositToAccountRef",
        )
        txn.lines = _extract_lines(raw, "Deposit")
        return txn


class TransferNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.TRANSFER,
            amount_field="Amount",
            account_ref_field="FromAccountRef",
        )
        txn.account_quickbooks_id = _safe_str(raw.get("FromAccountRef", {}).get("value"))
        txn.account_name_snapshot = _safe_str(raw.get("FromAccountRef", {}).get("name"))
        return txn


class JournalEntryNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.JOURNAL_ENTRY,
            amount_field="TotalAmt",
        )
        txn.lines = _extract_lines(raw, "JournalEntry")
        return txn


class BillNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.BILL,
            amount_field="TotalAmt",
            balance_field="Balance",
            counterparty_type="Vendor",
            counterparty_ref_field="VendorRef",
            account_ref_field="APAccountRef",
        )
        txn.lines = _extract_lines(raw, "Bill")
        return txn


class BillPaymentNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.BILL_PAYMENT,
            amount_field="TotalAmt",
            counterparty_type="Vendor",
            counterparty_ref_field="VendorRef",
            account_ref_field="APAccountRef",
            payment_type_field="PayType",
        )
        txn.lines = _extract_lines(raw, "BillPayment")
        return txn


class PaymentNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.PAYMENT,
            amount_field="TotalAmt",
            balance_field="UnappliedAmt",
            counterparty_type="Customer",
            counterparty_ref_field="CustomerRef",
            account_ref_field="DepositToAccountRef",
        )
        txn.lines = _extract_lines(raw, "Payment")
        return txn


class SalesReceiptNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.SALES_RECEIPT,
            amount_field="TotalAmt",
            counterparty_type="Customer",
            counterparty_ref_field="CustomerRef",
            account_ref_field="DepositToAccountRef",
        )
        txn.lines = _extract_lines(raw, "SalesReceipt")
        return txn


class RefundReceiptNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.REFUND_RECEIPT,
            amount_field="TotalAmt",
            counterparty_type="Customer",
            counterparty_ref_field="CustomerRef",
            account_ref_field="DepositToAccountRef",
        )
        txn.lines = _extract_lines(raw, "RefundReceipt")
        return txn


class CreditMemoNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.CREDIT_MEMO,
            amount_field="TotalAmt",
            balance_field="Balance",
            counterparty_type="Customer",
            counterparty_ref_field="CustomerRef",
        )
        txn.lines = _extract_lines(raw, "CreditMemo")
        return txn


class VendorCreditNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.VENDOR_CREDIT,
            amount_field="TotalAmt",
            counterparty_type="Vendor",
            counterparty_ref_field="VendorRef",
            account_ref_field="APAccountRef",
        )
        txn.lines = _extract_lines(raw, "VendorCredit")
        return txn


class InvoiceNormalizer:
    def normalize(self, raw: dict[str, Any], realm_id: str) -> NormalizedTransaction:
        txn = _extract_transaction_header(
            raw,
            realm_id,
            EntityType.INVOICE,
            amount_field="TotalAmt",
            balance_field="Balance",
            counterparty_type="Customer",
            counterparty_ref_field="CustomerRef",
        )
        txn.lines = _extract_lines(raw, "Invoice")
        return txn


# --- Registry entry ---


@dataclass(frozen=True)
class EntityRegistryEntry:
    """Configuration for a supported QuickBooks entity type."""

    entity_type: EntityType
    quickbooks_entity_name: str
    query_support: bool = True
    cdc_support: bool = True
    date_field: str = "TxnDate"
    created_field: str = "MetaData.CreateTime"
    updated_field: str = "MetaData.LastUpdatedTime"
    normalizer: EntityNormalizer = field(default_factory=lambda: PurchaseNormalizer())
    where_clause: str = ""  # additional WHERE for backfill queries


# --- Central registry ---


_REGISTRY: dict[EntityType, EntityRegistryEntry] = {
    EntityType.PURCHASE: EntityRegistryEntry(
        entity_type=EntityType.PURCHASE,
        quickbooks_entity_name="Purchase",
        normalizer=PurchaseNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.DEPOSIT: EntityRegistryEntry(
        entity_type=EntityType.DEPOSIT,
        quickbooks_entity_name="Deposit",
        normalizer=DepositNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.TRANSFER: EntityRegistryEntry(
        entity_type=EntityType.TRANSFER,
        quickbooks_entity_name="Transfer",
        normalizer=TransferNormalizer(),
        where_clause="Date >= '{start}' AND Date <= '{end}'",
    ),
    EntityType.JOURNAL_ENTRY: EntityRegistryEntry(
        entity_type=EntityType.JOURNAL_ENTRY,
        quickbooks_entity_name="JournalEntry",
        normalizer=JournalEntryNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.BILL: EntityRegistryEntry(
        entity_type=EntityType.BILL,
        quickbooks_entity_name="Bill",
        normalizer=BillNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.BILL_PAYMENT: EntityRegistryEntry(
        entity_type=EntityType.BILL_PAYMENT,
        quickbooks_entity_name="BillPayment",
        normalizer=BillPaymentNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.PAYMENT: EntityRegistryEntry(
        entity_type=EntityType.PAYMENT,
        quickbooks_entity_name="Payment",
        normalizer=PaymentNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.SALES_RECEIPT: EntityRegistryEntry(
        entity_type=EntityType.SALES_RECEIPT,
        quickbooks_entity_name="SalesReceipt",
        normalizer=SalesReceiptNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.REFUND_RECEIPT: EntityRegistryEntry(
        entity_type=EntityType.REFUND_RECEIPT,
        quickbooks_entity_name="RefundReceipt",
        normalizer=RefundReceiptNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.CREDIT_MEMO: EntityRegistryEntry(
        entity_type=EntityType.CREDIT_MEMO,
        quickbooks_entity_name="CreditMemo",
        normalizer=CreditMemoNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.VENDOR_CREDIT: EntityRegistryEntry(
        entity_type=EntityType.VENDOR_CREDIT,
        quickbooks_entity_name="VendorCredit",
        normalizer=VendorCreditNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
    EntityType.INVOICE: EntityRegistryEntry(
        entity_type=EntityType.INVOICE,
        quickbooks_entity_name="Invoice",
        normalizer=InvoiceNormalizer(),
        where_clause="TxnDate >= '{start}' AND TxnDate <= '{end}'",
    ),
}


def get_registry_entry(entity_type: EntityType) -> EntityRegistryEntry:
    """Look up a registry entry by entity type.

    Raises QuickBooksUnsupportedEntityError if the entity is not registered.
    """
    entry = _REGISTRY.get(entity_type)
    if entry is None:
        raise QuickBooksUnsupportedEntityError(
            f"Entity type {entity_type.value!r} is not registered. "
            f"Supported: {[e.value for e in _REGISTRY]}"
        )
    return entry


def get_all_registry_entries() -> dict[EntityType, EntityRegistryEntry]:
    """Return the complete registry (read-only copy)."""
    return dict(_REGISTRY)


def normalize_entity(
    entity_type: EntityType,
    raw: dict[str, Any],
    realm_id: str,
) -> NormalizedTransaction:
    """Normalize a raw QuickBooks entity using the registered adapter."""
    entry = get_registry_entry(entity_type)
    return entry.normalizer.normalize(raw, realm_id)
