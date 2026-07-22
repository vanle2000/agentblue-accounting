"""QuickBooks sync domain models — enums, DTOs, and value objects.

Transport-agnostic domain types for the synchronization subsystem.
No ORM dependencies. No network dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class SyncMode(str, Enum):
    """Supported synchronization modes."""

    BACKFILL = "backfill"
    INCREMENTAL = "incremental"


class SyncStatus(str, Enum):
    """Sync run lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RecordOutcome(str, Enum):
    """Per-record persistence outcome classification."""

    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    MARKED_DELETED = "marked_deleted"
    FAILED = "failed"


class SourceEntityStatus(str, Enum):
    """Status of a QuickBooks source entity."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"
    ARCHIVED = "archived"


class EntityType(str, Enum):
    """Supported QuickBooks entity types.

    Uses exact QuickBooks API entity names as values.
    Includes Account for Chart of Accounts synchronization.
    """

    PURCHASE = "Purchase"
    DEPOSIT = "Deposit"
    TRANSFER = "Transfer"
    JOURNAL_ENTRY = "JournalEntry"
    BILL = "Bill"
    BILL_PAYMENT = "BillPayment"
    PAYMENT = "Payment"
    SALES_RECEIPT = "SalesReceipt"
    REFUND_RECEIPT = "RefundReceipt"
    CREDIT_MEMO = "CreditMemo"
    VENDOR_CREDIT = "VendorCredit"
    INVOICE = "Invoice"
    ACCOUNT = "Account"


# --- Value objects ---


@dataclass(frozen=True)
class SyncWindow:
    """A time-bounded sync window with UTC timestamps."""

    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        if self.start_at > self.end_at:
            raise ValueError(f"Window start ({self.start_at}) must not exceed end ({self.end_at})")

    @property
    def duration_seconds(self) -> float:
        """Return the window duration in seconds."""
        return (self.end_at - self.start_at).total_seconds()

    def split(self) -> tuple[SyncWindow, SyncWindow]:
        """Split this window into two equal halves."""
        delta = (self.end_at - self.start_at) / 2
        mid = self.start_at + delta
        return (
            SyncWindow(start_at=self.start_at, end_at=mid),
            SyncWindow(start_at=mid, end_at=self.end_at),
        )


@dataclass(frozen=True)
class Checkpoint:
    """Represents synchronization progress for one realm/entity/mode."""

    realm_id: str
    entity_type: EntityType
    sync_mode: SyncMode
    last_successful_source_timestamp: datetime | None = None
    last_successful_completed_at: datetime | None = None
    checkpoint_version: int = 0


# --- Sync request and result DTOs ---


@dataclass(frozen=True)
class SyncRequest:
    """Input parameters for a sync operation."""

    realm_id: str
    entity_types: list[EntityType]
    mode: SyncMode
    start_at: datetime | None = None
    end_at: datetime | None = None
    page_size: int = 100


@dataclass
class EntitySyncResult:
    """Metrics for one entity type within a sync run."""

    entity_type: EntityType
    status: SyncStatus = SyncStatus.PENDING
    window_start: datetime | None = None
    window_end: datetime | None = None
    pages_processed: int = 0
    records_fetched: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_unchanged: int = 0
    records_marked_deleted: int = 0
    records_failed: int = 0
    safe_error_code: str = ""
    safe_error_message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class SyncRunResult:
    """Aggregate metrics for a complete sync run."""

    sync_run_id: str
    realm_id: str
    mode: SyncMode
    status: SyncStatus = SyncStatus.PENDING
    entity_results: list[EntitySyncResult] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def records_fetched(self) -> int:
        return sum(r.records_fetched for r in self.entity_results)

    @property
    def records_inserted(self) -> int:
        return sum(r.records_inserted for r in self.entity_results)

    @property
    def records_updated(self) -> int:
        return sum(r.records_updated for r in self.entity_results)

    @property
    def records_unchanged(self) -> int:
        return sum(r.records_unchanged for r in self.entity_results)

    @property
    def records_marked_deleted(self) -> int:
        return sum(r.records_marked_deleted for r in self.entity_results)

    @property
    def records_failed(self) -> int:
        return sum(r.records_failed for r in self.entity_results)


# --- Normalized transaction models ---


@dataclass
class NormalizedTransactionLine:
    """A normalized transaction line item."""

    source_line_id: str
    line_number: int
    description: str = ""
    amount: Decimal = Decimal("0")
    detail_type: str = ""
    posting_type: str = ""
    account_quickbooks_id: str = ""
    account_name_snapshot: str = ""
    item_quickbooks_id: str = ""
    item_name_snapshot: str = ""
    customer_quickbooks_id: str = ""
    customer_name_snapshot: str = ""
    vendor_quickbooks_id: str = ""
    vendor_name_snapshot: str = ""
    class_quickbooks_id: str = ""
    class_name_snapshot: str = ""
    department_quickbooks_id: str = ""
    department_name_snapshot: str = ""
    billable_status: str = ""
    tax_code_quickbooks_id: str = ""
    raw_line_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedTransaction:
    """A normalized accounting transaction header + lines."""

    realm_id: str
    entity_type: EntityType
    quickbooks_id: str
    sync_token: int = 0
    transaction_date: str = ""
    document_number: str = ""
    private_note: str = ""
    currency_code: str = ""
    exchange_rate: Decimal | None = None
    total_amount: Decimal = Decimal("0")
    balance_amount: Decimal | None = None
    counterparty_type: str = ""
    counterparty_quickbooks_id: str = ""
    counterparty_name_snapshot: str = ""
    account_quickbooks_id: str = ""
    account_name_snapshot: str = ""
    payment_type: str = ""
    transaction_status: str = ""
    source_created_at: str = ""
    source_updated_at: str = ""
    source_deleted_at: str = ""
    lines: list[NormalizedTransactionLine] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
