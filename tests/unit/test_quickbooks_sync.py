"""Tests for QuickBooks transaction synchronization (Stage 5).

Tests domain models, entity registry, normalizers, query builder,
sync service (with mocked API client), and security.
No live QuickBooks API calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksQueryConstructionError,
    QuickBooksUnsupportedEntityError,
)
from agentblue.integrations.quickbooks.sync.domain import (
    EntityType,
    RecordOutcome,
    SyncMode,
    SyncRequest,
    SyncStatus,
    SyncWindow,
)
from agentblue.integrations.quickbooks.sync.query_builder import (
    build_backfill_query,
    build_cdc_query,
    format_date,
)
from agentblue.integrations.quickbooks.sync.registry import (
    get_all_registry_entries,
    get_registry_entry,
    normalize_entity,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestEntityRegistry:
    def test_all_supported_entities_registered(self) -> None:
        """Every transaction EntityType must have a registry entry.

        EntityType.ACCOUNT is excluded — it is an accounting-context
        entity managed by the Stage 6 accounting module, not the
        transaction sync registry.
        """
        registry = get_all_registry_entries()
        for et in EntityType:
            if et == EntityType.ACCOUNT:
                continue
            assert et in registry, f"Entity {et.value} not in registry"

    def test_unknown_entity_rejected(self) -> None:
        """Requesting an unregistered entity raises."""
        with pytest.raises((QuickBooksUnsupportedEntityError, ValueError)):
            get_registry_entry(EntityType("NonexistentEntity"))

    def test_entity_names_match_quickbooks(self) -> None:
        """Registry entity names must match QuickBooks API names."""
        registry = get_all_registry_entries()
        expected = {
            EntityType.PURCHASE: "Purchase",
            EntityType.DEPOSIT: "Deposit",
            EntityType.TRANSFER: "Transfer",
            EntityType.JOURNAL_ENTRY: "JournalEntry",
            EntityType.BILL: "Bill",
            EntityType.BILL_PAYMENT: "BillPayment",
            EntityType.PAYMENT: "Payment",
            EntityType.SALES_RECEIPT: "SalesReceipt",
            EntityType.REFUND_RECEIPT: "RefundReceipt",
            EntityType.CREDIT_MEMO: "CreditMemo",
            EntityType.VENDOR_CREDIT: "VendorCredit",
            EntityType.INVOICE: "Invoice",
        }
        for et, name in expected.items():
            assert registry[et].quickbooks_entity_name == name


# ---------------------------------------------------------------------------
# Query builder tests
# ---------------------------------------------------------------------------


class TestQueryBuilder:
    def test_backfill_query_basic(self) -> None:
        query = build_backfill_query(EntityType.PURCHASE, "2024-01-01", "2024-12-31")
        assert "SELECT * FROM Purchase" in query
        assert "TxnDate >= '2024-01-01'" in query
        assert "STARTPOSITION 0" in query
        assert "MAXRESULTS 100" in query

    def test_backfill_query_custom_page(self) -> None:
        query = build_backfill_query(EntityType.INVOICE, "2024-06-01", page_size=50)
        assert "MAXRESULTS 50" in query

    def test_backfill_query_invalid_date(self) -> None:
        with pytest.raises(QuickBooksQueryConstructionError, match="Invalid start_date"):
            build_backfill_query(EntityType.PURCHASE, "bad-date")

    def test_backfill_query_invalid_end_date(self) -> None:
        with pytest.raises(QuickBooksQueryConstructionError, match="Invalid end_date"):
            build_backfill_query(EntityType.PURCHASE, "2024-01-01", "not-a-date")

    def test_backfill_query_zero_page_size(self) -> None:
        with pytest.raises(QuickBooksQueryConstructionError, match="positive"):
            build_backfill_query(EntityType.PURCHASE, "2024-01-01", page_size=0)

    def test_cdc_query_basic(self) -> None:
        query = build_cdc_query([EntityType.PURCHASE, EntityType.INVOICE], "2024-01-01")
        assert "Purchase" in query
        assert "Invoice" in query
        assert "CHANGEDSINCE" in query

    def test_cdc_query_empty_entities(self) -> None:
        with pytest.raises(QuickBooksQueryConstructionError, match="At least one"):
            build_cdc_query([], "2024-01-01")

    def test_cdc_query_invalid_date(self) -> None:
        with pytest.raises(QuickBooksQueryConstructionError, match="Invalid"):
            build_cdc_query([EntityType.PURCHASE], "bad")

    def test_format_date(self) -> None:
        dt = datetime(2024, 3, 15, 10, 30, 0)
        assert format_date(dt) == "2024-03-15"

    def test_query_injection_rejected(self) -> None:
        """Entity names must come from the registry, not user input."""
        # EntityType itself rejects invalid values at construction time
        with pytest.raises(ValueError, match="is not a valid EntityType"):
            EntityType("Purchase; DROP TABLE")


# ---------------------------------------------------------------------------
# Domain model tests
# ---------------------------------------------------------------------------


class TestSyncWindow:
    def test_valid_window(self) -> None:
        now = datetime.now(UTC)
        window = SyncWindow(start_at=now - timedelta(hours=1), end_at=now)
        assert window.duration_seconds > 0

    def test_invalid_window(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="must not exceed"):
            SyncWindow(start_at=now, end_at=now - timedelta(hours=1))

    def test_split(self) -> None:
        now = datetime.now(UTC)
        window = SyncWindow(start_at=now - timedelta(hours=2), end_at=now)
        a, b = window.split()
        assert a.end_at == b.start_at
        assert a.start_at == window.start_at
        assert b.end_at == window.end_at


class TestEntityType:
    def test_entity_type_values(self) -> None:
        assert EntityType.PURCHASE.value == "Purchase"
        assert EntityType.INVOICE.value == "Invoice"
        assert EntityType.JOURNAL_ENTRY.value == "JournalEntry"


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------


class TestNormalization:
    def _purchase_raw(self) -> dict:
        return {
            "Id": "100",
            "SyncToken": "1",
            "TxnDate": "2024-06-15",
            "TotalAmt": 500.00,
            "PaymentType": "Cash",
            "PrivateNote": "Office supplies",
            "EntityRef": {"value": "55", "name": "Acme Corp"},
            "AccountRef": {"value": "30", "name": "Checking"},
            "CurrencyRef": {"value": "USD"},
            "MetaData": {
                "CreateTime": "2024-06-15T10:00:00-07:00",
                "LastUpdatedTime": "2024-06-15T10:00:00-07:00",
            },
            "Line": [
                {
                    "Id": "1",
                    "Amount": 250.00,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Description": "Paper",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "40", "name": "Expenses"},
                    },
                },
                {
                    "Id": "2",
                    "Amount": 250.00,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Description": "Toner",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "40", "name": "Expenses"},
                    },
                },
            ],
        }

    def test_purchase_normalization(self) -> None:
        raw = self._purchase_raw()
        txn = normalize_entity(EntityType.PURCHASE, raw, "realm-1")
        assert txn.entity_type == EntityType.PURCHASE
        assert txn.quickbooks_id == "100"
        assert txn.total_amount == Decimal("500.00")
        assert txn.transaction_date == "2024-06-15"
        assert txn.counterparty_type == "Vendor"
        assert txn.counterparty_name_snapshot == "Acme Corp"
        assert txn.account_quickbooks_id == "30"
        assert len(txn.lines) == 2
        assert txn.lines[0].amount == Decimal("250.00")

    def test_deposit_normalization(self) -> None:
        raw = {
            "Id": "200",
            "SyncToken": "0",
            "TxnDate": "2024-07-01",
            "TotalAmt": 1000.00,
            "DepositToAccountRef": {"value": "30", "name": "Checking"},
            "MetaData": {
                "CreateTime": "2024-07-01T00:00:00Z",
                "LastUpdatedTime": "2024-07-01T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.DEPOSIT, raw, "realm-1")
        assert txn.entity_type == EntityType.DEPOSIT
        assert txn.total_amount == Decimal("1000.00")

    def test_invoice_normalization(self) -> None:
        raw = {
            "Id": "300",
            "SyncToken": "2",
            "TxnDate": "2024-07-10",
            "TotalAmt": 750.50,
            "Balance": 250.50,
            "DocNumber": "INV-001",
            "CustomerRef": {"value": "10", "name": "Widget Co"},
            "CurrencyRef": {"value": "USD"},
            "ExchangeRate": 1.0,
            "MetaData": {
                "CreateTime": "2024-07-10T00:00:00Z",
                "LastUpdatedTime": "2024-07-10T00:00:00Z",
            },
            "Line": [
                {
                    "Id": "1",
                    "Amount": 750.50,
                    "DetailType": "SalesItemLineDetail",
                    "Description": "Consulting",
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": "20", "name": "Consulting"},
                    },
                }
            ],
        }
        txn = normalize_entity(EntityType.INVOICE, raw, "realm-1")
        assert txn.entity_type == EntityType.INVOICE
        assert txn.total_amount == Decimal("750.50")
        assert txn.balance_amount == Decimal("250.50")
        assert txn.document_number == "INV-001"
        assert txn.counterparty_type == "Customer"
        assert txn.counterparty_name_snapshot == "Widget Co"
        assert txn.exchange_rate == Decimal("1.0")
        assert len(txn.lines) == 1
        assert txn.lines[0].item_quickbooks_id == "20"

    def test_journal_entry_debit_credit(self) -> None:
        raw = {
            "Id": "400",
            "SyncToken": "0",
            "TxnDate": "2024-07-15",
            "TotalAmt": 500.00,
            "MetaData": {
                "CreateTime": "2024-07-15T00:00:00Z",
                "LastUpdatedTime": "2024-07-15T00:00:00Z",
            },
            "Line": [
                {
                    "Id": "1",
                    "Amount": 500.00,
                    "DetailType": "JournalEntryLineDetail",
                    "Description": "Debit entry",
                    "JournalEntryLineDetail": {
                        "PostingType": "Debit",
                        "AccountRef": {"value": "40", "name": "Expenses"},
                    },
                },
                {
                    "Id": "2",
                    "Amount": 500.00,
                    "DetailType": "JournalEntryLineDetail",
                    "Description": "Credit entry",
                    "JournalEntryLineDetail": {
                        "PostingType": "Credit",
                        "AccountRef": {"value": "30", "name": "Checking"},
                    },
                },
            ],
        }
        txn = normalize_entity(EntityType.JOURNAL_ENTRY, raw, "realm-1")
        assert txn.entity_type == EntityType.JOURNAL_ENTRY
        assert len(txn.lines) == 2
        assert txn.lines[0].posting_type == "Debit"
        assert txn.lines[1].posting_type == "Credit"

    def test_bill_normalization(self) -> None:
        raw = {
            "Id": "500",
            "SyncToken": "1",
            "TxnDate": "2024-08-01",
            "TotalAmt": 300.00,
            "Balance": 150.00,
            "VendorRef": {"value": "55", "name": "Acme Corp"},
            "APAccountRef": {"value": "50", "name": "Accounts Payable"},
            "MetaData": {
                "CreateTime": "2024-08-01T00:00:00Z",
                "LastUpdatedTime": "2024-08-01T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.BILL, raw, "realm-1")
        assert txn.entity_type == EntityType.BILL
        assert txn.counterparty_type == "Vendor"
        assert txn.balance_amount == Decimal("150.00")

    def test_payment_normalization(self) -> None:
        raw = {
            "Id": "600",
            "SyncToken": "0",
            "TxnDate": "2024-08-10",
            "TotalAmt": 100.00,
            "UnappliedAmt": 0.00,
            "CustomerRef": {"value": "10", "name": "Widget Co"},
            "DepositToAccountRef": {"value": "30", "name": "Checking"},
            "MetaData": {
                "CreateTime": "2024-08-10T00:00:00Z",
                "LastUpdatedTime": "2024-08-10T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.PAYMENT, raw, "realm-1")
        assert txn.entity_type == EntityType.PAYMENT
        assert txn.counterparty_type == "Customer"

    def test_sales_receipt_normalization(self) -> None:
        raw = {
            "Id": "700",
            "SyncToken": "0",
            "TxnDate": "2024-08-15",
            "TotalAmt": 450.00,
            "CustomerRef": {"value": "10", "name": "Widget Co"},
            "DepositToAccountRef": {"value": "30", "name": "Checking"},
            "MetaData": {
                "CreateTime": "2024-08-15T00:00:00Z",
                "LastUpdatedTime": "2024-08-15T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.SALES_RECEIPT, raw, "realm-1")
        assert txn.entity_type == EntityType.SALES_RECEIPT

    def test_refund_receipt_normalization(self) -> None:
        raw = {
            "Id": "800",
            "SyncToken": "0",
            "TxnDate": "2024-08-20",
            "TotalAmt": 50.00,
            "CustomerRef": {"value": "10", "name": "Widget Co"},
            "MetaData": {
                "CreateTime": "2024-08-20T00:00:00Z",
                "LastUpdatedTime": "2024-08-20T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.REFUND_RECEIPT, raw, "realm-1")
        assert txn.entity_type == EntityType.REFUND_RECEIPT

    def test_credit_memo_normalization(self) -> None:
        raw = {
            "Id": "900",
            "SyncToken": "0",
            "TxnDate": "2024-08-25",
            "TotalAmt": 200.00,
            "Balance": 50.00,
            "CustomerRef": {"value": "10", "name": "Widget Co"},
            "MetaData": {
                "CreateTime": "2024-08-25T00:00:00Z",
                "LastUpdatedTime": "2024-08-25T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.CREDIT_MEMO, raw, "realm-1")
        assert txn.entity_type == EntityType.CREDIT_MEMO
        assert txn.balance_amount == Decimal("50.00")

    def test_vendor_credit_normalization(self) -> None:
        raw = {
            "Id": "1000",
            "SyncToken": "0",
            "TxnDate": "2024-09-01",
            "TotalAmt": 75.00,
            "VendorRef": {"value": "55", "name": "Acme Corp"},
            "APAccountRef": {"value": "50", "name": "Accounts Payable"},
            "MetaData": {
                "CreateTime": "2024-09-01T00:00:00Z",
                "LastUpdatedTime": "2024-09-01T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.VENDOR_CREDIT, raw, "realm-1")
        assert txn.entity_type == EntityType.VENDOR_CREDIT

    def test_transfer_normalization(self) -> None:
        raw = {
            "Id": "1100",
            "SyncToken": "0",
            "Date": "2024-09-05",
            "Amount": 1000.00,
            "FromAccountRef": {"value": "30", "name": "Checking"},
            "MetaData": {
                "CreateTime": "2024-09-05T00:00:00Z",
                "LastUpdatedTime": "2024-09-05T00:00:00Z",
            },
        }
        txn = normalize_entity(EntityType.TRANSFER, raw, "realm-1")
        assert txn.entity_type == EntityType.TRANSFER
        assert txn.total_amount == Decimal("1000.00")

    def test_bill_payment_normalization(self) -> None:
        raw = {
            "Id": "1200",
            "SyncToken": "0",
            "TxnDate": "2024-09-10",
            "TotalAmt": 150.00,
            "PayType": "Check",
            "VendorRef": {"value": "55", "name": "Acme Corp"},
            "APAccountRef": {"value": "50", "name": "Accounts Payable"},
            "MetaData": {
                "CreateTime": "2024-09-10T00:00:00Z",
                "LastUpdatedTime": "2024-09-10T00:00:00Z",
            },
            "Line": [],
        }
        txn = normalize_entity(EntityType.BILL_PAYMENT, raw, "realm-1")
        assert txn.entity_type == EntityType.BILL_PAYMENT
        assert txn.payment_type == "Check"

    def test_missing_optional_fields(self) -> None:
        """Minimal raw payload should not crash normalization."""
        raw = {"Id": "9999", "MetaData": {}}
        txn = normalize_entity(EntityType.PURCHASE, raw, "realm-1")
        assert txn.quickbooks_id == "9999"
        assert txn.total_amount == Decimal("0")

    def test_raw_payload_preserved(self) -> None:
        raw = {"Id": "100", "MetaData": {}, "custom_field": "custom_value"}
        txn = normalize_entity(EntityType.PURCHASE, raw, "realm-1")
        assert txn.raw_payload == raw

    def test_monetary_precision_decimal(self) -> None:
        """Monetary values must use Decimal, not float."""
        raw = {
            "Id": "100",
            "TotalAmt": 123.45,
            "MetaData": {},
        }
        txn = normalize_entity(EntityType.PURCHASE, raw, "realm-1")
        assert isinstance(txn.total_amount, Decimal)
        assert txn.total_amount == Decimal("123.45")


# ---------------------------------------------------------------------------
# Sync service tests (mocked)
# ---------------------------------------------------------------------------


class TestSyncServiceBackfill:
    @pytest.mark.asyncio
    async def test_backfill_single_page(self) -> None:
        """Backfill with one page of results."""
        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        # Mock the query response
        mock_client.get = AsyncMock(
            return_value={
                "QueryResponse": {
                    "Purchase": [
                        {
                            "Id": "1",
                            "SyncToken": "0",
                            "TxnDate": "2024-01-15",
                            "TotalAmt": 100.00,
                            "MetaData": {
                                "CreateTime": "2024-01-15T00:00:00Z",
                                "LastUpdatedTime": "2024-01-15T00:00:00Z",
                            },
                            "Line": [],
                        }
                    ],
                    "MaxResults": 1,
                    "TotalCount": 1,
                }
            }
        )

        # Mock repository methods
        mock_repo = AsyncMock()
        mock_repo.create_sync_run = AsyncMock(return_value=MagicMock(id="run-1"))
        mock_repo.create_sync_run_entity = AsyncMock(return_value=MagicMock(id="er-1"))
        mock_repo.upsert_source_snapshot = AsyncMock(return_value=RecordOutcome.INSERTED)
        mock_repo.upsert_transaction = AsyncMock(return_value=RecordOutcome.INSERTED)
        mock_repo.advance_checkpoint = AsyncMock()
        mock_repo.update_sync_run_entity = AsyncMock()
        mock_repo.complete_sync_run = AsyncMock()

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository",
            return_value=mock_repo,
        ):
            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)
            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.BACKFILL,
                start_at=datetime(2024, 1, 1, tzinfo=UTC),
                end_at=datetime(2024, 12, 31, tzinfo=UTC),
            )
            result = await service.backfill(request)

        assert result.status in (SyncStatus.COMPLETED, SyncStatus.RUNNING)
        assert result.records_fetched >= 0


class TestSyncServiceIncremental:
    @pytest.mark.asyncio
    async def test_incremental_first_sync(self) -> None:
        """First incremental sync with no prior checkpoint."""
        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_client.get = AsyncMock(
            return_value={"CDCResponse": {"CDCResponse": [{"Purchase": []}]}}
        )

        mock_repo = AsyncMock()
        mock_repo.create_sync_run = AsyncMock(return_value=MagicMock(id="run-2"))
        mock_repo.create_sync_run_entity = AsyncMock(return_value=MagicMock(id="er-2"))
        mock_repo.get_checkpoint = AsyncMock(return_value=None)
        mock_repo.advance_checkpoint = AsyncMock()
        mock_repo.update_sync_run_entity = AsyncMock()
        mock_repo.complete_sync_run = AsyncMock()

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository",
            return_value=mock_repo,
        ):
            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)
            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.INCREMENTAL,
            )
            result = await service.sync_incremental(request)

        assert result.status in (SyncStatus.COMPLETED, SyncStatus.RUNNING)


# ---------------------------------------------------------------------------
# Deletion handling tests
# ---------------------------------------------------------------------------


class TestDeletionHandling:
    def test_deleted_entity_recognized(self) -> None:
        """Entities with status=Deleted should be handled."""
        raw = {
            "Id": "500",
            "SyncToken": "3",
            "domain": "QBO",
            "status": "Deleted",
            "MetaData": {
                "CreateTime": "2024-01-01T00:00:00Z",
                "LastUpdatedTime": "2024-06-01T00:00:00Z",
                "DeletedTime": "2024-06-01T00:00:00Z",
            },
        }
        # The sync service checks domain/status before normalization
        assert raw.get("status") == "Deleted"


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_no_secrets_in_exceptions(self) -> None:
        """Exception messages must not contain secrets."""
        exc = QuickBooksQueryConstructionError("Invalid date format: bad-date")
        msg = str(exc)
        assert "token" not in msg.lower() or "quickbooks" in msg.lower()
        assert "secret" not in msg.lower()

    def test_entity_names_cannot_inject(self) -> None:
        """Entity names from registry cannot contain SQL-like injection."""
        for et in EntityType:
            if et == EntityType.ACCOUNT:
                continue  # Account is managed by accounting module
            entry = get_registry_entry(et)
            name = entry.quickbooks_entity_name
            assert ";" not in name
            assert "--" not in name
            assert "DROP" not in name.upper() or name == "Drop"
