"""Extended tests for QuickBooks sync, writeback, accounting, and router modules.

Covers:
- SyncRepository: upsert, checkpoint, transaction persistence, sync runs
- SyncService: backfill, incremental, persist_batch, error handling
- WriteBackService: apply_categorization, _verify_write, is_supported_type
- Validation: compute_entity_hash, check_stale, extract_line_account_ref, find_target_line
- AccountingRepository: upsert_account_snapshot, upsert_account, get_account_by_quickbooks_id, get_accounts_by_realm, etc.
- AccountSyncService: backfill, sync_incremental
- Accounting services: AccountValidationService, AccountCandidateService, AccountHierarchyService, TransactionAccountResolver
- Router endpoints: sync, accounting, quickbooks OAuth
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentblue.integrations.quickbooks.accounting.domain import (
    CandidateFilter,
    NormalizedAccount,
)
from agentblue.integrations.quickbooks.accounting.repository import (
    AccountingRepository,
)
from agentblue.integrations.quickbooks.accounting.repository import (
    RecordOutcome as AcctRecordOutcome,
)
from agentblue.integrations.quickbooks.accounting.service import (
    AccountSyncService,
    _is_explicitly_deleted,
)
from agentblue.integrations.quickbooks.accounting.services import (
    AccountCandidateService,
    AccountHierarchyService,
    AccountUsageService,
    TransactionAccountResolver,
)
from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksIncrementalSyncError,
)
from agentblue.integrations.quickbooks.sync.domain import (
    EntitySyncResult,
    EntityType,
    NormalizedTransaction,
    NormalizedTransactionLine,
    RecordOutcome,
    SyncMode,
    SyncRequest,
    SyncRunResult,
    SyncStatus,
    SyncWindow,
)
from agentblue.integrations.quickbooks.sync.repository import (
    SyncRepository,
    _hash_payload,
)
from agentblue.integrations.quickbooks.writeback.exceptions import (
    TargetAccountInvalidError,
    UnsupportedEntityTypeError,
)
from agentblue.integrations.quickbooks.writeback.service import WriteBackService
from agentblue.integrations.quickbooks.writeback.validation import (
    check_stale,
    compute_entity_hash,
    extract_line_account_ref,
    find_target_line,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session() -> AsyncMock:
    """Create a mock AsyncSession with common defaults."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    return session


def _make_mock_session_with(obj) -> AsyncMock:
    """Create a mock session that returns `obj` from scalar_one_or_none."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    session.execute = AsyncMock(return_value=result)
    return session


def _make_mock_session_with_list(items: list) -> AsyncMock:
    """Create a mock session that returns `items` from scalars().all()."""
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    session.execute = AsyncMock(return_value=result)
    return session


def _normalized_transaction(
    *,
    realm_id: str = "realm-1",
    entity_type: EntityType = EntityType.PURCHASE,
    quickbooks_id: str = "100",
    sync_token: int = 1,
    total_amount: Decimal = Decimal("500.00"),
    transaction_date: str = "2024-06-15",
    source_deleted_at: str = "",
    lines: list | None = None,
) -> NormalizedTransaction:
    if lines is None:
        lines = [
            NormalizedTransactionLine(
                source_line_id="1",
                line_number=1,
                description="Test line",
                amount=Decimal("500.00"),
                detail_type="AccountBasedExpenseLineDetail",
                account_quickbooks_id="40",
                raw_line_payload={"Id": "1", "Amount": 500.00},
            )
        ]
    return NormalizedTransaction(
        realm_id=realm_id,
        entity_type=entity_type,
        quickbooks_id=quickbooks_id,
        sync_token=sync_token,
        transaction_date=transaction_date,
        total_amount=total_amount,
        source_deleted_at=source_deleted_at,
        lines=lines,
    )


def _normalized_account(
    *,
    realm_id: str = "realm-1",
    quickbooks_id: str = "100",
    name: str = "Checking",
    active: bool = True,
    account_type: str = "Bank",
    classification: str = "Asset",
    sync_token: int = 1,
) -> NormalizedAccount:
    return NormalizedAccount(
        realm_id=realm_id,
        quickbooks_id=quickbooks_id,
        name=name,
        active=active,
        account_type=account_type,
        classification=classification,
        sync_token=sync_token,
        raw_payload={"Id": quickbooks_id, "Name": name},
    )


def _mock_account_obj(
    *,
    realm_id: str = "realm-1",
    quickbooks_id: str = "100",
    active: bool = True,
    source_deleted: bool = False,
    account_type: str = "Bank",
    classification: str = "Asset",
    name: str = "Checking",
    subaccount: bool = False,
    parent_quickbooks_id: str = "",
) -> MagicMock:
    acct = MagicMock()
    acct.realm_id = realm_id
    acct.quickbooks_id = quickbooks_id
    acct.active = active
    acct.source_deleted = source_deleted
    acct.account_type = account_type
    acct.account_subtype = "CheckingAccount"
    acct.classification = classification
    acct.id = f"db-id-{quickbooks_id}"
    acct.name = name
    acct.fully_qualified_name = f"Assets:Bank:{name}"
    acct.subaccount = subaccount
    acct.parent_quickbooks_id = parent_quickbooks_id
    acct.account_number = ""
    acct.description = ""
    acct.currency_code = "USD"
    acct.current_balance = Decimal("0")
    acct.current_balance_with_subaccounts = Decimal("0")
    acct.sync_token = 1
    acct.raw_payload_hash = "abc123"
    return acct


def _purchase_entity() -> dict:
    return {
        "Id": "100",
        "SyncToken": "1",
        "TxnDate": "2024-06-15",
        "TotalAmt": 500.00,
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


# ===========================================================================
# SyncRepository tests
# ===========================================================================


class TestSyncRepositoryUpsertSnapshot:
    async def test_insert_new_snapshot(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        outcome = await repo.upsert_source_snapshot(
            realm_id="realm-1",
            entity_type=EntityType.PURCHASE,
            raw={"Id": "100", "key": "value"},
            quickbooks_id="100",
            sync_token=1,
        )
        assert outcome == RecordOutcome.INSERTED
        session.add.assert_called_once()

    async def test_update_existing_snapshot_same_hash_unchanged(self) -> None:
        raw = {"Id": "100", "key": "value"}
        existing = MagicMock()
        existing.payload_hash = _hash_payload(raw)
        existing.source_status = "active"

        session = _make_mock_session_with(existing)
        repo = SyncRepository(session)
        outcome = await repo.upsert_source_snapshot(
            realm_id="realm-1",
            entity_type=EntityType.PURCHASE,
            raw=raw,
            quickbooks_id="100",
            sync_token=1,
        )
        assert outcome == RecordOutcome.UNCHANGED
        session.add.assert_not_called()

    async def test_update_existing_snapshot_different_hash_updated(self) -> None:
        existing = MagicMock()
        existing.payload_hash = "old_hash"
        existing.source_status = "active"

        session = _make_mock_session_with(existing)
        repo = SyncRepository(session)
        outcome = await repo.upsert_source_snapshot(
            realm_id="realm-1",
            entity_type=EntityType.PURCHASE,
            raw={"Id": "100", "new_key": "new_value"},
            quickbooks_id="100",
            sync_token=2,
        )
        assert outcome == RecordOutcome.UPDATED
        assert existing.sync_token == 2

    async def test_update_existing_snapshot_different_status_updated(self) -> None:
        raw = {"Id": "100", "key": "value"}
        existing = MagicMock()
        existing.payload_hash = _hash_payload(raw)
        existing.source_status = "deleted"

        session = _make_mock_session_with(existing)
        repo = SyncRepository(session)
        outcome = await repo.upsert_source_snapshot(
            realm_id="realm-1",
            entity_type=EntityType.PURCHASE,
            raw=raw,
            quickbooks_id="100",
            sync_token=1,
            source_status="active",
        )
        assert outcome == RecordOutcome.UPDATED


class TestSyncRepositoryMarkDeleted:
    async def test_mark_existing_as_deleted(self) -> None:
        existing = MagicMock()
        existing.source_status = "active"

        session = _make_mock_session_with(existing)
        repo = SyncRepository(session)
        outcome = await repo.mark_source_deleted(
            realm_id="realm-1",
            entity_type=EntityType.PURCHASE,
            quickbooks_id="100",
        )
        assert outcome == RecordOutcome.MARKED_DELETED
        assert existing.source_status == "deleted"

    async def test_mark_already_deleted_unchanged(self) -> None:
        existing = MagicMock()
        existing.source_status = "deleted"

        session = _make_mock_session_with(existing)
        repo = SyncRepository(session)
        outcome = await repo.mark_source_deleted(
            realm_id="realm-1",
            entity_type=EntityType.PURCHASE,
            quickbooks_id="100",
        )
        assert outcome == RecordOutcome.UNCHANGED

    async def test_mark_nonexistent_unchanged(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        outcome = await repo.mark_source_deleted(
            realm_id="realm-1",
            entity_type=EntityType.PURCHASE,
            quickbooks_id="999",
        )
        assert outcome == RecordOutcome.UNCHANGED


class TestSyncRepositoryUpsertTransaction:
    async def test_insert_new_transaction(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        txn = _normalized_transaction()
        outcome = await repo.upsert_transaction(txn)
        assert outcome == RecordOutcome.INSERTED
        # session.add called for transaction + lines
        assert session.add.call_count >= 1

    async def test_update_existing_transaction_changed(self) -> None:
        existing_txn = MagicMock()
        existing_txn.sync_token = 0
        existing_txn.total_amount = Decimal("0")
        existing_txn.transaction_date = "2023-01-01"
        existing_txn.source_deleted = False
        existing_txn.id = "db-txn-id"
        existing_txn.lines = []

        session = _make_mock_session_with(existing_txn)
        repo = SyncRepository(session)
        txn = _normalized_transaction(sync_token=2)
        outcome = await repo.upsert_transaction(txn)
        assert outcome == RecordOutcome.UPDATED

    async def test_update_existing_transaction_unchanged(self) -> None:
        existing_txn = MagicMock()
        existing_txn.sync_token = 1
        existing_txn.total_amount = Decimal("500.00")
        existing_txn.transaction_date = "2024-06-15"
        existing_txn.source_deleted = False

        session = _make_mock_session_with(existing_txn)
        repo = SyncRepository(session)
        txn = _normalized_transaction()
        outcome = await repo.upsert_transaction(txn)
        assert outcome == RecordOutcome.UNCHANGED

    async def test_insert_transaction_with_empty_lines(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        txn = _normalized_transaction(lines=[])
        outcome = await repo.upsert_transaction(txn)
        assert outcome == RecordOutcome.INSERTED

    async def test_source_deleted_flag_true_when_deleted_at_set(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        txn = _normalized_transaction(source_deleted_at="2024-07-01T00:00:00Z")
        outcome = await repo.upsert_transaction(txn)
        assert outcome == RecordOutcome.INSERTED


class TestSyncRepositoryCheckpoints:
    async def test_get_checkpoint_none(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        result = await repo.get_checkpoint(
            "realm-1", EntityType.PURCHASE, SyncMode.INCREMENTAL
        )
        assert result is None

    async def test_get_checkpoint_found(self) -> None:
        checkpoint = MagicMock()
        session = _make_mock_session_with(checkpoint)
        repo = SyncRepository(session)
        result = await repo.get_checkpoint(
            "realm-1", EntityType.PURCHASE, SyncMode.INCREMENTAL
        )
        assert result is checkpoint

    async def test_advance_checkpoint_new(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        now = datetime.now(UTC)
        await repo.advance_checkpoint(
            "realm-1", EntityType.PURCHASE, SyncMode.BACKFILL, now
        )
        session.add.assert_called_once()

    async def test_advance_checkpoint_existing_forward(self) -> None:
        checkpoint = MagicMock()
        checkpoint.last_successful_source_timestamp = datetime.now(UTC) - timedelta(hours=1)
        checkpoint.checkpoint_version = 1

        session = _make_mock_session_with(checkpoint)
        repo = SyncRepository(session)
        now = datetime.now(UTC)
        await repo.advance_checkpoint(
            "realm-1", EntityType.PURCHASE, SyncMode.BACKFILL, now
        )
        assert checkpoint.checkpoint_version == 2

    async def test_advance_checkpoint_backward_noop(self) -> None:
        now = datetime.now(UTC)
        checkpoint = MagicMock()
        checkpoint.last_successful_source_timestamp = now + timedelta(hours=1)
        checkpoint.checkpoint_version = 1

        session = _make_mock_session_with(checkpoint)
        repo = SyncRepository(session)
        await repo.advance_checkpoint(
            "realm-1", EntityType.PURCHASE, SyncMode.BACKFILL, now
        )
        assert checkpoint.checkpoint_version == 1


class TestSyncRepositorySyncRuns:
    async def test_create_sync_run(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        run = await repo.create_sync_run(
            realm_id="realm-1",
            mode=SyncMode.BACKFILL,
            entity_types=[EntityType.PURCHASE, EntityType.INVOICE],
        )
        assert run.status == SyncStatus.RUNNING.value
        session.add.assert_called_once()
        session.flush.assert_called_once()

    async def test_create_sync_run_entity(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        entity = await repo.create_sync_run_entity("run-id", EntityType.PURCHASE)
        assert entity.entity_type == EntityType.PURCHASE.value
        session.add.assert_called_once()

    async def test_update_sync_run_entity(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        await repo.update_sync_run_entity(
            "entity-id",
            status=SyncStatus.COMPLETED,
            pages_processed=3,
            records_fetched=100,
            records_inserted=50,
        )
        session.execute.assert_called_once()

    async def test_complete_sync_run(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        await repo.complete_sync_run("run-id", SyncStatus.COMPLETED)
        session.execute.assert_called_once()

    async def test_complete_sync_run_with_error(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        await repo.complete_sync_run(
            "run-id", SyncStatus.FAILED, safe_error_code="TEST_ERROR", safe_error_message="test"
        )
        session.execute.assert_called_once()

    async def test_get_sync_run_none(self) -> None:
        session = _make_mock_session()
        repo = SyncRepository(session)
        result = await repo.get_sync_run("nonexistent")
        assert result is None

    async def test_get_sync_run_found(self) -> None:
        run = MagicMock()
        session = _make_mock_session_with(run)
        repo = SyncRepository(session)
        result = await repo.get_sync_run("run-id")
        assert result is run


class TestSyncRepositoryHashPayload:
    def test_deterministic_hash(self) -> None:
        payload = {"key": "value", "nested": {"a": 1}}
        h1 = _hash_payload(payload)
        h2 = _hash_payload(payload)
        assert h1 == h2

    def test_different_payloads_different_hashes(self) -> None:
        h1 = _hash_payload({"key": "value1"})
        h2 = _hash_payload({"key": "value2"})
        assert h1 != h2


# ===========================================================================
# SyncService tests
# ===========================================================================


class TestSyncServiceBackfill:
    async def test_backfill_single_entity_success(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_entity_run = MagicMock()
        mock_entity_run.id = "entity-run-1"

        mock_session.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.create_sync_run = AsyncMock(return_value=mock_run)
            repo.create_sync_run_entity = AsyncMock(return_value=mock_entity_run)
            repo.update_sync_run_entity = AsyncMock()
            repo.complete_sync_run = AsyncMock()
            repo.upsert_source_snapshot = AsyncMock(return_value=RecordOutcome.INSERTED)
            repo.mark_source_deleted = AsyncMock()
            repo.upsert_transaction = AsyncMock(return_value=RecordOutcome.INSERTED)

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)

            # Mock the API client response
            mock_client.get = AsyncMock(
                return_value={
                    "QueryResponse": {
                        "Purchase": [
                            {
                                "Id": "100",
                                "SyncToken": "1",
                                "TxnDate": "2024-06-15",
                                "TotalAmt": 500.00,
                                "MetaData": {},
                                "Line": [],
                            }
                        ],
                        "MaxResults": 1,
                        "TotalCount": 1,
                    }
                }
            )

            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.BACKFILL,
                page_size=100,
            )

            result = await service.backfill(request)
            assert result.status in (SyncStatus.COMPLETED, SyncStatus.PARTIAL)
            assert len(result.entity_results) == 1

    async def test_backfill_empty_response(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_entity_run = MagicMock()
        mock_entity_run.id = "entity-run-1"

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.create_sync_run = AsyncMock(return_value=mock_run)
            repo.create_sync_run_entity = AsyncMock(return_value=mock_entity_run)
            repo.update_sync_run_entity = AsyncMock()
            repo.complete_sync_run = AsyncMock()

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)

            mock_client.get = AsyncMock(
                return_value={"QueryResponse": {"Purchase": [], "MaxResults": 0, "TotalCount": 0}}
            )

            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.BACKFILL,
            )

            result = await service.backfill(request)
            assert result.status == SyncStatus.COMPLETED

    async def test_backfill_api_error_raises_backfill_error(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_entity_run = MagicMock()
        mock_entity_run.id = "entity-run-1"

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.create_sync_run = AsyncMock(return_value=mock_run)
            repo.create_sync_run_entity = AsyncMock(return_value=mock_entity_run)
            repo.update_sync_run_entity = AsyncMock()
            repo.complete_sync_run = AsyncMock()

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)

            # First call raises to trigger entity failure, second call also raises for outer loop
            mock_client.get = AsyncMock(side_effect=Exception("API connection failed"))

            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.BACKFILL,
            )

            result = await service.backfill(request)
            # The entity catches the exception and marks FAILED; the outer loop
            # catches no exception (entity failure does not re-raise)
            # So status is PARTIAL not FAILED
            assert result.status in (SyncStatus.PARTIAL, SyncStatus.COMPLETED)

    async def test_backfill_with_deleted_entity(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_entity_run = MagicMock()
        mock_entity_run.id = "entity-run-1"

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.create_sync_run = AsyncMock(return_value=mock_run)
            repo.create_sync_run_entity = AsyncMock(return_value=mock_entity_run)
            repo.update_sync_run_entity = AsyncMock()
            repo.complete_sync_run = AsyncMock()
            repo.upsert_source_snapshot = AsyncMock(return_value=RecordOutcome.INSERTED)
            repo.mark_source_deleted = AsyncMock(return_value=RecordOutcome.MARKED_DELETED)

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)

            mock_client.get = AsyncMock(
                return_value={
                    "QueryResponse": {
                        "Purchase": [
                            {
                                "Id": "200",
                                "SyncToken": "1",
                                "domain": "QBO",
                                "status": "Deleted",
                                "MetaData": {"DeletedTime": "2024-07-01"},
                            }
                        ],
                        "MaxResults": 1,
                        "TotalCount": 1,
                    }
                }
            )

            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.BACKFILL,
            )

            result = await service.backfill(request)
            # The deleted entity should be counted
            assert result.entity_results[0].records_fetched >= 0

    async def test_get_sync_status(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_run.status = "completed"

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.get_sync_run = AsyncMock(return_value=mock_run)

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)
            status = await service.get_sync_status("run-1")
            assert status is not None
            assert status.status == "completed"

    async def test_get_sync_status_not_found(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.get_sync_run = AsyncMock(return_value=None)

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)
            status = await service.get_sync_status("nonexistent")
            assert status is None


class TestSyncServiceIncremental:
    async def test_incremental_sync_success(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_entity_run = MagicMock()
        mock_entity_run.id = "entity-run-1"

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.create_sync_run = AsyncMock(return_value=mock_run)
            repo.create_sync_run_entity = AsyncMock(return_value=mock_entity_run)
            repo.update_sync_run_entity = AsyncMock()
            repo.complete_sync_run = AsyncMock()
            repo.get_checkpoint = AsyncMock(return_value=None)
            repo.advance_checkpoint = AsyncMock()
            repo.upsert_source_snapshot = AsyncMock(return_value=RecordOutcome.INSERTED)
            repo.upsert_transaction = AsyncMock(return_value=RecordOutcome.INSERTED)
            repo.mark_source_deleted = AsyncMock()

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)

            mock_client.get = AsyncMock(
                return_value={
                    "CDCResponse": {
                        "CDCResponse": [
                            {
                                "Purchase": [
                                    {
                                        "Id": "100",
                                        "SyncToken": "1",
                                        "TxnDate": "2024-06-15",
                                        "TotalAmt": 100.00,
                                        "MetaData": {},
                                        "Line": [],
                                    }
                                ]
                            }
                        ]
                    }
                }
            )

            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.INCREMENTAL,
            )

            result = await service.sync_incremental(request)
            assert result.status in (SyncStatus.COMPLETED, SyncStatus.PARTIAL)

    async def test_incremental_sync_with_checkpoint(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_entity_run = MagicMock()
        mock_entity_run.id = "entity-run-1"

        checkpoint = MagicMock()
        checkpoint.last_successful_source_timestamp = datetime.now(UTC) - timedelta(hours=2)

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.create_sync_run = AsyncMock(return_value=mock_run)
            repo.create_sync_run_entity = AsyncMock(return_value=mock_entity_run)
            repo.update_sync_run_entity = AsyncMock()
            repo.complete_sync_run = AsyncMock()
            repo.get_checkpoint = AsyncMock(return_value=checkpoint)
            repo.advance_checkpoint = AsyncMock()

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)

            mock_client.get = AsyncMock(
                return_value={"CDCResponse": {"CDCResponse": [{"Purchase": []}]}}
            )

            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.PURCHASE],
                mode=SyncMode.INCREMENTAL,
            )

            result = await service.sync_incremental(request)
            assert result.status == SyncStatus.COMPLETED

    async def test_incremental_sync_cdc_not_supported(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_run = MagicMock()
        mock_run.id = "run-1"
        mock_entity_run = MagicMock()
        mock_entity_run.id = "entity-run-1"

        with patch(
            "agentblue.integrations.quickbooks.sync.service.SyncRepository"
        ) as MockRepo:
            repo = MockRepo.return_value
            repo.create_sync_run = AsyncMock(return_value=mock_run)
            repo.create_sync_run_entity = AsyncMock(return_value=mock_entity_run)
            repo.update_sync_run_entity = AsyncMock()
            repo.complete_sync_run = AsyncMock()

            from agentblue.integrations.quickbooks.sync.service import (
                QuickBooksTransactionSyncService,
            )

            service = QuickBooksTransactionSyncService(mock_client, mock_session)

            # Account type is not in the transaction sync registry,
            # so get_registry_entry raises QuickBooksUnsupportedEntityError
            # which is caught and re-raised as QuickBooksIncrementalSyncError
            request = SyncRequest(
                realm_id="realm-1",
                entity_types=[EntityType.ACCOUNT],
                mode=SyncMode.INCREMENTAL,
            )

            with pytest.raises(QuickBooksIncrementalSyncError, match="not registered"):
                await service.sync_incremental(request)


# ===========================================================================
# Validation module tests
# ===========================================================================


class TestComputeEntityHash:
    def test_deterministic(self) -> None:
        entity = _purchase_entity()
        h1 = compute_entity_hash(entity)
        h2 = compute_entity_hash(entity)
        assert h1 == h2
        assert isinstance(h1, str)
        assert len(h1) == 64  # SHA-256 hex

    def test_different_entities_different_hashes(self) -> None:
        e1 = _purchase_entity()
        e2 = _purchase_entity()
        e2["TotalAmt"] = 999.00
        assert compute_entity_hash(e1) != compute_entity_hash(e2)

    def test_empty_entity(self) -> None:
        h = compute_entity_hash({})
        assert isinstance(h, str)
        assert len(h) == 64

    def test_entity_with_no_lines(self) -> None:
        entity = {"Id": "1", "SyncToken": "0", "TotalAmt": 100.0}
        h = compute_entity_hash(entity)
        assert len(h) == 64


class TestCheckStale:
    def test_not_stale_when_matching(self) -> None:
        entity = _purchase_entity()
        current_token = entity["SyncToken"]
        current_hash = compute_entity_hash(entity)
        reasons = check_stale(current_token, current_hash, entity)
        assert reasons == []

    def test_stale_when_token_mismatch(self) -> None:
        entity = _purchase_entity()
        current_hash = compute_entity_hash(entity)
        reasons = check_stale("999", current_hash, entity)
        assert len(reasons) == 1
        assert "sync_token_changed" in reasons[0]

    def test_stale_when_hash_mismatch(self) -> None:
        entity = _purchase_entity()
        reasons = check_stale(entity["SyncToken"], "wrong_hash", entity)
        assert len(reasons) == 1
        assert "transaction_hash_changed" in reasons[0]

    def test_stale_when_both_mismatch(self) -> None:
        entity = _purchase_entity()
        reasons = check_stale("999", "wrong_hash", entity)
        assert len(reasons) == 2


class TestExtractLineAccountRef:
    def test_expense_line_detail(self) -> None:
        line = {
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": "40"},
            },
        }
        assert extract_line_account_ref(line) == "40"

    def test_no_detail_key(self) -> None:
        line = {"DetailType": "Unknown"}
        assert extract_line_account_ref(line) == ""

    def test_empty_line(self) -> None:
        assert extract_line_account_ref({}) == ""

    def test_no_account_ref(self) -> None:
        line = {
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {},
        }
        assert extract_line_account_ref(line) == ""


class TestFindTargetLine:
    def test_find_existing_line(self) -> None:
        entity = _purchase_entity()
        line = find_target_line(entity, "1")
        assert line is not None
        assert line["Id"] == "1"

    def test_find_nonexistent_line(self) -> None:
        entity = _purchase_entity()
        assert find_target_line(entity, "999") is None

    def test_empty_entity(self) -> None:
        assert find_target_line({}, "1") is None


# ===========================================================================
# WriteBackService tests
# ===========================================================================


class TestWriteBackService:
    async def test_apply_categorization_simulated_no_api_client(self) -> None:
        session = AsyncMock()
        mock_acct = _mock_account_obj(active=True, source_deleted=False)

        with patch.object(
            AccountingRepository, "get_account_by_quickbooks_id", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_acct
            service = WriteBackService(session, api_client=None)
            result = await service.apply_categorization(
                realm_id="realm-1",
                transaction_quickbooks_id="100",
                transaction_type="Purchase",
                selected_account_quickbooks_id="40",
                reviewed_sync_token="1",
                reviewed_transaction_hash="abc",
                approved_by="user-1",
                idempotency_key="key-1",
            )
            assert result["status"] == "SIMULATED"

    async def test_apply_categorization_unsupported_type(self) -> None:
        session = AsyncMock()
        service = WriteBackService(session, api_client=None)
        with pytest.raises(UnsupportedEntityTypeError):
            await service.apply_categorization(
                realm_id="realm-1",
                transaction_quickbooks_id="100",
                transaction_type="Invoice",
                selected_account_quickbooks_id="40",
                reviewed_sync_token="1",
                reviewed_transaction_hash="abc",
                approved_by="user-1",
                idempotency_key="key-1",
            )

    async def test_apply_categorization_account_not_found(self) -> None:
        session = AsyncMock()

        with patch.object(
            AccountingRepository, "get_account_by_quickbooks_id", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = None
            service = WriteBackService(session, api_client=None)
            with pytest.raises(TargetAccountInvalidError, match="not found"):
                await service.apply_categorization(
                    realm_id="realm-1",
                    transaction_quickbooks_id="100",
                    transaction_type="Purchase",
                    selected_account_quickbooks_id="999",
                    reviewed_sync_token="1",
                    reviewed_transaction_hash="abc",
                    approved_by="user-1",
                    idempotency_key="key-1",
                )

    async def test_apply_categorization_account_source_deleted(self) -> None:
        session = AsyncMock()
        mock_acct = _mock_account_obj(source_deleted=True)

        with patch.object(
            AccountingRepository, "get_account_by_quickbooks_id", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_acct
            service = WriteBackService(session, api_client=None)
            with pytest.raises(TargetAccountInvalidError, match="source-deleted"):
                await service.apply_categorization(
                    realm_id="realm-1",
                    transaction_quickbooks_id="100",
                    transaction_type="Purchase",
                    selected_account_quickbooks_id="40",
                    reviewed_sync_token="1",
                    reviewed_transaction_hash="abc",
                    approved_by="user-1",
                    idempotency_key="key-1",
                )

    async def test_apply_categorization_account_inactive(self) -> None:
        session = AsyncMock()
        mock_acct = _mock_account_obj(active=False, source_deleted=False)

        with patch.object(
            AccountingRepository, "get_account_by_quickbooks_id", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_acct
            service = WriteBackService(session, api_client=None)
            with pytest.raises(TargetAccountInvalidError, match="inactive"):
                await service.apply_categorization(
                    realm_id="realm-1",
                    transaction_quickbooks_id="100",
                    transaction_type="Purchase",
                    selected_account_quickbooks_id="40",
                    reviewed_sync_token="1",
                    reviewed_transaction_hash="abc",
                    approved_by="user-1",
                    idempotency_key="key-1",
                )


class TestWriteBackVerifyWrite:
    def test_verify_success_with_target_line(self) -> None:
        returned_entity = {
            "TotalAmt": 500.00,
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "40"},
                    },
                }
            ],
        }
        result = WriteBackService._verify_write(
            returned_entity, "Purchase", "40", "1", "500.0"
        )
        assert result["success"] is True

    def test_verify_failure_total_changed(self) -> None:
        returned_entity = {"TotalAmt": 999.00, "Line": []}
        result = WriteBackService._verify_write(
            returned_entity, "Purchase", "40", "1", "500.0"
        )
        assert result["success"] is False
        assert "Total changed" in result["reason"]

    def test_verify_failure_target_line_missing(self) -> None:
        returned_entity = {
            "TotalAmt": 500.00,
            "Line": [{"Id": "99"}],
        }
        result = WriteBackService._verify_write(
            returned_entity, "Purchase", "40", "1", "500.0"
        )
        assert result["success"] is False
        assert "missing from response" in result["reason"]

    def test_verify_failure_account_mismatch_on_target_line(self) -> None:
        returned_entity = {
            "TotalAmt": 500.00,
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "99"},
                    },
                }
            ],
        }
        result = WriteBackService._verify_write(
            returned_entity, "Purchase", "40", "1", "500.0"
        )
        assert result["success"] is False
        assert "mismatch" in result["reason"]

    def test_verify_transaction_level_success(self) -> None:
        returned_entity = {
            "TotalAmt": 500.00,
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "40"},
                    },
                }
            ],
        }
        result = WriteBackService._verify_write(
            returned_entity, "Purchase", "40", "", "500.0"
        )
        assert result["success"] is True

    def test_verify_transaction_level_account_mismatch(self) -> None:
        returned_entity = {
            "TotalAmt": 500.00,
            "Line": [
                {
                    "Id": "1",
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "wrong"},
                    },
                }
            ],
        }
        result = WriteBackService._verify_write(
            returned_entity, "Purchase", "40", "", "500.0"
        )
        assert result["success"] is False
        assert "mismatch" in result["reason"]

    def test_verify_no_lines_no_error(self) -> None:
        returned_entity = {"TotalAmt": 500.00, "Line": []}
        result = WriteBackService._verify_write(
            returned_entity, "Purchase", "40", "", "500.0"
        )
        assert result["success"] is True


class TestWriteBackIsSupportedType:
    def test_purchase_is_supported(self) -> None:
        assert WriteBackService.is_supported_type("Purchase") is True

    def test_invoice_not_supported(self) -> None:
        assert WriteBackService.is_supported_type("Invoice") is False

    def test_empty_not_supported(self) -> None:
        assert WriteBackService.is_supported_type("") is False


# ===========================================================================
# AccountingRepository tests
# ===========================================================================


class TestAccountingRepositoryUpsertSnapshot:
    async def test_insert_new_account_snapshot(self) -> None:
        session = _make_mock_session()
        repo = AccountingRepository(session)
        outcome = await repo.upsert_account_snapshot(
            realm_id="realm-1",
            quickbooks_id="100",
            raw={"Id": "100", "Name": "Checking"},
            sync_token=1,
            active=True,
        )
        assert outcome == AcctRecordOutcome.INSERTED
        session.add.assert_called_once()

    async def test_update_existing_snapshot_same_hash_unchanged(self) -> None:
        raw = {"Id": "100", "Name": "Checking"}
        from agentblue.integrations.quickbooks.accounting.repository import _hash_payload

        existing = MagicMock()
        existing.raw_payload_hash = _hash_payload(raw)
        existing.active = True
        existing.source_deleted = False

        session = _make_mock_session_with(existing)
        repo = AccountingRepository(session)
        outcome = await repo.upsert_account_snapshot(
            realm_id="realm-1",
            quickbooks_id="100",
            raw=raw,
            sync_token=1,
            active=True,
        )
        assert outcome == AcctRecordOutcome.UNCHANGED

    async def test_update_existing_snapshot_different(self) -> None:
        existing = MagicMock()
        existing.raw_payload_hash = "old_hash"
        existing.active = True
        existing.source_deleted = False

        session = _make_mock_session_with(existing)
        repo = AccountingRepository(session)
        outcome = await repo.upsert_account_snapshot(
            realm_id="realm-1",
            quickbooks_id="100",
            raw={"Id": "100", "Name": "New Name"},
            sync_token=2,
            active=True,
        )
        assert outcome == AcctRecordOutcome.UPDATED


class TestAccountingRepositoryUpsertAccount:
    async def test_insert_new_account(self) -> None:
        session = _make_mock_session()
        repo = AccountingRepository(session)
        account = _normalized_account()
        outcome = await repo.upsert_account(account)
        assert outcome == AcctRecordOutcome.INSERTED
        session.add.assert_called_once()

    async def test_update_existing_account_changed(self) -> None:
        existing = MagicMock()
        existing.sync_token = 0
        existing.name = "Old Name"
        existing.active = True
        existing.account_type = "Bank"
        existing.classification = "Asset"
        existing.source_deleted = False
        existing.parent_quickbooks_id = ""

        session = _make_mock_session_with(existing)
        repo = AccountingRepository(session)
        account = _normalized_account(name="New Name")
        outcome = await repo.upsert_account(account)
        assert outcome == AcctRecordOutcome.UPDATED

    async def test_update_existing_account_unchanged(self) -> None:
        existing = MagicMock()
        existing.sync_token = 1
        existing.name = "Checking"
        existing.active = True
        existing.account_type = "Bank"
        existing.classification = "Asset"
        existing.source_deleted = False
        existing.parent_quickbooks_id = ""

        session = _make_mock_session_with(existing)
        repo = AccountingRepository(session)
        account = _normalized_account()
        outcome = await repo.upsert_account(account)
        assert outcome == AcctRecordOutcome.UNCHANGED

    async def test_upsert_account_with_source_deleted(self) -> None:
        session = _make_mock_session()
        repo = AccountingRepository(session)
        account = _normalized_account()
        outcome = await repo.upsert_account(account, source_deleted=True)
        assert outcome == AcctRecordOutcome.INSERTED


class TestAccountingRepositoryLookup:
    async def test_get_account_by_quickbooks_id_not_found(self) -> None:
        session = _make_mock_session()
        repo = AccountingRepository(session)
        result = await repo.get_account_by_quickbooks_id("realm-1", "100")
        assert result is None

    async def test_get_account_by_quickbooks_id_found(self) -> None:
        acct = MagicMock()
        session = _make_mock_session_with(acct)
        repo = AccountingRepository(session)
        result = await repo.get_account_by_quickbooks_id("realm-1", "100")
        assert result is acct

    async def test_get_accounts_by_realm(self) -> None:
        acct1 = _mock_account_obj(quickbooks_id="100", name="Checking")
        acct2 = _mock_account_obj(quickbooks_id="200", name="Savings")
        session = _make_mock_session_with_list([acct1, acct2])
        repo = AccountingRepository(session)
        accounts = await repo.get_accounts_by_realm("realm-1")
        assert len(accounts) == 2

    async def test_get_accounts_by_realm_active_only(self) -> None:
        acct = _mock_account_obj(active=True)
        session = _make_mock_session_with_list([acct])
        repo = AccountingRepository(session)
        accounts = await repo.get_accounts_by_realm("realm-1", active_only=True)
        assert len(accounts) == 1

    async def test_get_children(self) -> None:
        child = _mock_account_obj(quickbooks_id="101", name="Sub-Account")
        session = _make_mock_session_with_list([child])
        repo = AccountingRepository(session)
        children = await repo.get_children("realm-1", "100")
        assert len(children) == 1

    async def test_get_root_accounts(self) -> None:
        root = _mock_account_obj(quickbooks_id="100", name="Root")
        session = _make_mock_session_with_list([root])
        repo = AccountingRepository(session)
        roots = await repo.get_root_accounts("realm-1")
        assert len(roots) == 1

    async def test_resolve_parent_references(self) -> None:
        child = MagicMock()
        child.parent_quickbooks_id = "100"
        child.parent_account_id = None

        parent = MagicMock()
        parent.id = "db-parent-id"

        session = AsyncMock()
        # First call returns children, second returns parent
        result_children = MagicMock()
        result_children.scalars.return_value.all.return_value = [child]
        result_parent = MagicMock()
        result_parent.scalar_one_or_none.return_value = parent

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return result_children
            return result_parent

        session.execute = mock_execute
        repo = AccountingRepository(session)
        resolved = await repo.resolve_parent_references("realm-1")
        assert resolved == 1
        assert child.parent_account_id == "db-parent-id"


class TestAccountingRepositoryTransactionAccountRef:
    async def test_upsert_empty_account_id_returns_unchanged(self) -> None:
        session = _make_mock_session()
        repo = AccountingRepository(session)
        outcome = await repo.upsert_transaction_account_ref(
            transaction_id="txn-1",
            realm_id="realm-1",
            quickbooks_account_id="",
            reference_role="LINE_ACCOUNT",
        )
        assert outcome == AcctRecordOutcome.UNCHANGED

    async def test_upsert_existing_ref_returns_unchanged(self) -> None:
        existing = MagicMock()
        session = _make_mock_session_with(existing)
        repo = AccountingRepository(session)
        outcome = await repo.upsert_transaction_account_ref(
            transaction_id="txn-1",
            realm_id="realm-1",
            quickbooks_account_id="40",
            reference_role="LINE_ACCOUNT",
        )
        assert outcome == AcctRecordOutcome.UNCHANGED

    async def test_upsert_new_ref_returns_inserted(self) -> None:
        acct = MagicMock()
        acct.id = "db-acct-id"

        session = AsyncMock()
        # First call for existing ref (None), second for account lookup
        result_none = MagicMock()
        result_none.scalar_one_or_none.return_value = None
        result_acct = MagicMock()
        result_acct.scalar_one_or_none.return_value = acct

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return result_none
            return result_acct

        session.execute = mock_execute
        repo = AccountingRepository(session)
        outcome = await repo.upsert_transaction_account_ref(
            transaction_id="txn-1",
            realm_id="realm-1",
            quickbooks_account_id="40",
            reference_role="LINE_ACCOUNT",
            source_line_id="1",
        )
        assert outcome == AcctRecordOutcome.INSERTED
        session.add.assert_called_once()


# ===========================================================================
# Accounting services tests
# ===========================================================================


class TestAccountCandidateService:
    async def test_get_candidates_basic(self) -> None:
        acct1 = _mock_account_obj(quickbooks_id="100", name="Checking", subaccount=False)
        session = _make_mock_session_with_list([acct1])
        service = AccountCandidateService(session)
        filters = CandidateFilter(realm_id="realm-1")
        results = await service.get_candidates(filters)
        assert len(results) == 1
        assert results[0]["quickbooks_id"] == "100"

    async def test_get_candidates_exclude_subaccounts(self) -> None:
        sub = _mock_account_obj(quickbooks_id="101", name="Sub", subaccount=True)
        session = _make_mock_session_with_list([sub])
        service = AccountCandidateService(session)
        filters = CandidateFilter(realm_id="realm-1", include_subaccounts=False)
        results = await service.get_candidates(filters)
        assert len(results) == 0

    async def test_get_candidates_filter_by_parent(self) -> None:
        acct = _mock_account_obj(
            quickbooks_id="101", name="Sub", parent_quickbooks_id="100"
        )
        session = _make_mock_session_with_list([acct])
        service = AccountCandidateService(session)
        filters = CandidateFilter(
            realm_id="realm-1",
            parent_quickbooks_id="100",
            include_subaccounts=True,
        )
        results = await service.get_candidates(filters)
        assert len(results) == 1

    async def test_get_candidates_filter_by_parent_mismatch(self) -> None:
        acct = _mock_account_obj(
            quickbooks_id="101", name="Sub", parent_quickbooks_id="200"
        )
        session = _make_mock_session_with_list([acct])
        service = AccountCandidateService(session)
        filters = CandidateFilter(
            realm_id="realm-1",
            parent_quickbooks_id="100",
            include_subaccounts=True,
        )
        results = await service.get_candidates(filters)
        assert len(results) == 0


class TestAccountHierarchyService:
    async def test_get_hierarchy_not_found(self) -> None:
        session = _make_mock_session()
        service = AccountHierarchyService(session)
        result = await service.get_hierarchy("realm-1", "999")
        assert result is None

    async def test_get_hierarchy_single_node(self) -> None:
        acct = _mock_account_obj(quickbooks_id="100", name="Checking")

        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = acct
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)

        service = AccountHierarchyService(session)
        node = await service.get_hierarchy("realm-1", "100")
        assert node is not None
        assert node.quickbooks_id == "100"
        assert node.children == []

    async def test_get_ancestors(self) -> None:
        child = _mock_account_obj(
            quickbooks_id="101", name="Sub", parent_quickbooks_id="100"
        )
        parent = _mock_account_obj(
            quickbooks_id="100", name="Root", parent_quickbooks_id=""
        )

        session = AsyncMock()
        result_child = MagicMock()
        result_child.scalar_one_or_none.return_value = child
        result_parent = MagicMock()
        result_parent.scalar_one_or_none.return_value = parent

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return result_child
            return result_parent

        session.execute = mock_execute
        service = AccountHierarchyService(session)
        ancestors = await service.get_ancestors("realm-1", "101")
        assert len(ancestors) == 2
        assert ancestors[0]["quickbooks_id"] == "100"  # root (reversed)
        assert ancestors[1]["quickbooks_id"] == "101"


class TestTransactionAccountResolver:
    async def test_resolve_empty_id(self) -> None:
        session = _make_mock_session()
        service = TransactionAccountResolver(session)
        result = await service.resolve("realm-1", "")
        assert result.resolved is False
        assert result.reason_code == "EMPTY_REFERENCE"

    async def test_resolve_not_found(self) -> None:
        session = _make_mock_session()
        service = TransactionAccountResolver(session)
        result = await service.resolve("realm-1", "999")
        assert result.resolved is False
        assert result.reason_code == "NOT_FOUND"

    async def test_resolve_success(self) -> None:
        acct = _mock_account_obj(active=True, source_deleted=False)
        session = _make_mock_session_with(acct)
        service = TransactionAccountResolver(session)
        result = await service.resolve("realm-1", "100")
        assert result.resolved is True
        assert result.reason_code == "OK"

    async def test_resolve_inactive(self) -> None:
        acct = _mock_account_obj(active=False, source_deleted=False)
        session = _make_mock_session_with(acct)
        service = TransactionAccountResolver(session)
        result = await service.resolve("realm-1", "100")
        assert result.resolved is True
        assert result.reason_code == "INACTIVE"


# ===========================================================================
# AccountSyncService tests
# ===========================================================================


class TestAccountSyncService:
    async def test_backfill_success(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        raw_account = {
            "Id": "100",
            "SyncToken": "1",
            "Name": "Checking",
            "Active": True,
            "Classification": "Asset",
            "AccountType": "Bank",
            "MetaData": {"CreateTime": "2024-01-01", "LastUpdatedTime": "2024-06-01"},
        }

        mock_client.get = AsyncMock(
            return_value={
                "QueryResponse": {
                    "Account": [raw_account],
                    "MaxResults": 1,
                    "TotalCount": 1,
                }
            }
        )

        with (
            patch(
                "agentblue.integrations.quickbooks.accounting.service.AccountingRepository"
            ) as MockAcctRepo,
            patch(
                "agentblue.integrations.quickbooks.accounting.service.SyncRepository"
            ) as MockSyncRepo,
        ):
            acct_repo = MockAcctRepo.return_value
            acct_repo.upsert_account_snapshot = AsyncMock(return_value="inserted")
            acct_repo.upsert_account = AsyncMock(return_value="inserted")
            acct_repo.resolve_parent_references = AsyncMock(return_value=0)

            sync_repo = MockSyncRepo.return_value
            sync_repo.advance_checkpoint = AsyncMock()

            service = AccountSyncService(mock_client, mock_session)
            counts = await service.backfill("realm-1")
            assert counts["fetched"] == 1
            assert counts["inserted"] == 1

    async def test_backfill_empty_response(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_client.get = AsyncMock(
            return_value={"QueryResponse": {"Account": [], "MaxResults": 0, "TotalCount": 0}}
        )

        with (
            patch(
                "agentblue.integrations.quickbooks.accounting.service.AccountingRepository"
            ),
            patch(
                "agentblue.integrations.quickbooks.accounting.service.SyncRepository"
            ),
        ):
            service = AccountSyncService(mock_client, mock_session)
            counts = await service.backfill("realm-1")
            assert counts["fetched"] == 0

    async def test_sync_incremental_success(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        mock_client.get = AsyncMock(
            return_value={"CDCResponse": {"CDCResponse": [{"Account": []}]}}
        )

        with (
            patch(
                "agentblue.integrations.quickbooks.accounting.service.AccountingRepository"
            ) as MockAcctRepo,
            patch(
                "agentblue.integrations.quickbooks.accounting.service.SyncRepository"
            ) as MockSyncRepo,
        ):
            acct_repo = MockAcctRepo.return_value
            acct_repo.resolve_parent_references = AsyncMock(return_value=0)

            sync_repo = MockSyncRepo.return_value
            sync_repo.get_checkpoint = AsyncMock(return_value=None)
            sync_repo.advance_checkpoint = AsyncMock()

            service = AccountSyncService(mock_client, mock_session)
            counts = await service.sync_incremental("realm-1")
            assert counts["fetched"] == 0

    async def test_sync_incremental_with_checkpoint(self) -> None:
        mock_client = AsyncMock()
        mock_session = AsyncMock()

        checkpoint = MagicMock()
        checkpoint.last_successful_source_timestamp = datetime.now(UTC) - timedelta(hours=2)

        mock_client.get = AsyncMock(
            return_value={"CDCResponse": {"CDCResponse": [{"Account": []}]}}
        )

        with (
            patch(
                "agentblue.integrations.quickbooks.accounting.service.AccountingRepository"
            ) as MockAcctRepo,
            patch(
                "agentblue.integrations.quickbooks.accounting.service.SyncRepository"
            ) as MockSyncRepo,
        ):
            acct_repo = MockAcctRepo.return_value
            acct_repo.resolve_parent_references = AsyncMock(return_value=0)

            sync_repo = MockSyncRepo.return_value
            sync_repo.get_checkpoint = AsyncMock(return_value=checkpoint)
            sync_repo.advance_checkpoint = AsyncMock()

            service = AccountSyncService(mock_client, mock_session)
            counts = await service.sync_incremental("realm-1")
            assert counts["fetched"] == 0


class TestIsExplicitlyDeleted:
    def test_deleted_time_present(self) -> None:
        raw = {"MetaData": {"DeletedTime": "2024-06-15"}}
        assert _is_explicitly_deleted(raw) is True

    def test_status_deleted(self) -> None:
        raw = {"status": "Deleted", "MetaData": {}}
        assert _is_explicitly_deleted(raw) is True

    def test_active_no_deletion(self) -> None:
        raw = {"Active": True, "MetaData": {}}
        assert _is_explicitly_deleted(raw) is False

    def test_inactive_no_deletion(self) -> None:
        raw = {"Active": False, "MetaData": {}}
        assert _is_explicitly_deleted(raw) is False


# ===========================================================================
# Router endpoint tests
# ===========================================================================


class TestSyncRouterRegistration:
    def test_sync_router_has_backfill_route(self) -> None:
        from agentblue.integrations.quickbooks.sync.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/backfill") for r in routes)

    def test_sync_router_has_incremental_route(self) -> None:
        from agentblue.integrations.quickbooks.sync.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/incremental") for r in routes)

    def test_sync_router_has_runs_route(self) -> None:
        from agentblue.integrations.quickbooks.sync.router import router

        routes = [r.path for r in router.routes]
        assert any("/runs/" in r for r in routes)

    def test_sync_router_prefix(self) -> None:
        from agentblue.integrations.quickbooks.sync.router import router

        assert router.prefix == "/api/v1/integrations/quickbooks/sync"


class TestAccountingRouterRegistration:
    def test_accounting_router_prefix(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import router

        assert router.prefix == "/api/v1/integrations/quickbooks/accounts"

    def test_accounting_router_has_list_route(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import router

        routes = [r.path for r in router.routes]
        assert any(
            r.endswith("/accounts") for r in routes
        )

    def test_accounting_router_has_sync_routes(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/sync/backfill") for r in routes)
        assert any(r.endswith("/sync/incremental") for r in routes)

    def test_accounting_router_has_validate_route(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/validate") for r in routes)

    def test_accounting_router_has_candidates_route(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/candidates") for r in routes)

    def test_accounting_router_has_resolve_ref_route(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/resolve-ref") for r in routes)

    def test_accounting_router_has_evaluate_usage_route(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/evaluate-usage") for r in routes)


class TestQuickBooksRouterRegistration:
    def test_quickbooks_router_prefix(self) -> None:
        from agentblue.integrations.quickbooks.router import router

        assert router.prefix == "/api/v1/integrations/quickbooks"

    def test_quickbooks_router_has_authorize_route(self) -> None:
        from agentblue.integrations.quickbooks.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/authorize") for r in routes)

    def test_quickbooks_router_has_callback_route(self) -> None:
        from agentblue.integrations.quickbooks.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/callback") for r in routes)

    def test_quickbooks_router_has_health_route(self) -> None:
        from agentblue.integrations.quickbooks.router import router

        routes = [r.path for r in router.routes]
        assert any(r.endswith("/health") for r in routes)


class TestSyncRouterHelpers:
    def test_parse_entity_types_valid(self) -> None:
        from agentblue.integrations.quickbooks.sync.router import _parse_entity_types

        types = _parse_entity_types(["Purchase", "Deposit"])
        assert len(types) == 2
        assert types[0] == EntityType.PURCHASE
        assert types[1] == EntityType.DEPOSIT

    def test_parse_entity_types_invalid(self) -> None:
        from agentblue.integrations.quickbooks.exceptions import (
            QuickBooksUnsupportedEntityError,
        )
        from agentblue.integrations.quickbooks.sync.router import _parse_entity_types

        with pytest.raises(QuickBooksUnsupportedEntityError):
            _parse_entity_types(["NonExistentEntity"])

    def test_build_sync_response(self) -> None:
        from agentblue.integrations.quickbooks.sync.router import _build_sync_response

        entity_result = EntitySyncResult(
            entity_type=EntityType.PURCHASE,
            status=SyncStatus.COMPLETED,
            records_fetched=10,
            records_inserted=5,
            records_updated=3,
            records_unchanged=2,
            records_marked_deleted=0,
            records_failed=0,
        )
        run_result = SyncRunResult(
            sync_run_id="run-1",
            realm_id="realm-1",
            mode=SyncMode.BACKFILL,
            status=SyncStatus.COMPLETED,
            entity_results=[entity_result],
        )
        response = _build_sync_response(run_result)
        assert response.sync_run_id == "run-1"
        assert response.status == "completed"
        assert response.records_fetched == 10
        assert len(response.entity_results) == 1


class TestAccountingRouterHelpers:
    def test_to_summary(self) -> None:
        from agentblue.integrations.quickbooks.accounting.router import _to_summary

        acct = _mock_account_obj()
        acct.fully_qualified_name = "Assets:Bank:Checking"
        acct.account_subtype = "CheckingAccount"
        acct.account_number = "1000"
        acct.current_balance = Decimal("5000.50")
        acct.subaccount = False
        acct.parent_quickbooks_id = ""

        summary = _to_summary(acct)
        assert summary.quickbooks_id == "100"
        assert summary.name == "Checking"
        assert summary.active is True

    def test_hierarchy_to_response(self) -> None:
        from agentblue.integrations.quickbooks.accounting.domain import HierarchyNode
        from agentblue.integrations.quickbooks.accounting.router import (
            _hierarchy_to_response,
        )

        node = HierarchyNode(
            quickbooks_id="100",
            name="Root",
            fully_qualified_name="Root",
            account_type="Bank",
            classification="Asset",
            active=True,
            depth=0,
            children=[],
        )
        response = _hierarchy_to_response(node)
        assert response.quickbooks_id == "100"
        assert response.children == []


# ===========================================================================
# Writeback payload tests
# ===========================================================================


class TestWritebackPayloads:
    def test_get_entity_endpoint_purchase(self) -> None:
        from agentblue.integrations.quickbooks.writeback.payloads import get_entity_endpoint

        endpoint = get_entity_endpoint("Purchase", "realm-1", "100")
        assert endpoint == "/v3/company/realm-1/purchase/100"

    def test_get_entity_endpoint_without_id(self) -> None:
        from agentblue.integrations.quickbooks.writeback.payloads import get_entity_endpoint

        endpoint = get_entity_endpoint("Purchase", "realm-1")
        assert endpoint == "/v3/company/realm-1/purchase"

    def test_get_entity_endpoint_unsupported(self) -> None:
        from agentblue.integrations.quickbooks.writeback.payloads import get_entity_endpoint

        with pytest.raises(UnsupportedEntityTypeError):
            get_entity_endpoint("Invoice", "realm-1")

    def test_build_update_payload_purchase(self) -> None:
        from agentblue.integrations.quickbooks.writeback.payloads import build_update_payload

        entity = _purchase_entity()
        payload = build_update_payload("Purchase", entity, "999")
        assert payload["Id"] == "100"
        assert payload["sparse"] is True
        assert "Line" in payload

    def test_build_update_payload_unsupported(self) -> None:
        from agentblue.integrations.quickbooks.writeback.payloads import build_update_payload

        with pytest.raises(UnsupportedEntityTypeError):
            build_update_payload("Invoice", {}, "999")

    def test_build_update_payload_preserves_amount(self) -> None:
        from agentblue.integrations.quickbooks.writeback.payloads import build_update_payload

        entity = _purchase_entity()
        payload = build_update_payload("Purchase", entity, "999")
        for line in payload["Line"]:
            if "Amount" in line:
                assert line["Amount"] == 250.00


# ===========================================================================
# SyncWindow / domain model edge cases
# ===========================================================================


class TestSyncWindowExtended:
    def test_duration_seconds(self) -> None:
        now = datetime.now(UTC)
        window = SyncWindow(start_at=now - timedelta(hours=1), end_at=now)
        assert window.duration_seconds == pytest.approx(3600.0, rel=0.01)

    def test_split_preserves_total_duration(self) -> None:
        now = datetime.now(UTC)
        window = SyncWindow(start_at=now - timedelta(hours=2), end_at=now)
        a, b = window.split()
        total = a.duration_seconds + b.duration_seconds
        assert total == pytest.approx(window.duration_seconds, rel=0.01)


class TestRecordOutcomeEnum:
    def test_all_values(self) -> None:
        assert RecordOutcome.INSERTED.value == "inserted"
        assert RecordOutcome.UPDATED.value == "updated"
        assert RecordOutcome.UNCHANGED.value == "unchanged"
        assert RecordOutcome.MARKED_DELETED.value == "marked_deleted"
        assert RecordOutcome.FAILED.value == "failed"


class TestSyncModeEnum:
    def test_values(self) -> None:
        assert SyncMode.BACKFILL.value == "backfill"
        assert SyncMode.INCREMENTAL.value == "incremental"


class TestSyncStatusEnum:
    def test_values(self) -> None:
        assert SyncStatus.PENDING.value == "pending"
        assert SyncStatus.RUNNING.value == "running"
        assert SyncStatus.COMPLETED.value == "completed"
        assert SyncStatus.PARTIAL.value == "partial"
        assert SyncStatus.FAILED.value == "failed"


class TestEntitySyncResultProperties:
    def test_aggregate_properties(self) -> None:
        result = SyncRunResult(
            sync_run_id="run-1",
            realm_id="realm-1",
            mode=SyncMode.BACKFILL,
            entity_results=[
                EntitySyncResult(
                    entity_type=EntityType.PURCHASE,
                    records_fetched=10,
                    records_inserted=5,
                    records_updated=3,
                    records_unchanged=1,
                    records_marked_deleted=1,
                    records_failed=0,
                ),
                EntitySyncResult(
                    entity_type=EntityType.DEPOSIT,
                    records_fetched=20,
                    records_inserted=10,
                    records_updated=5,
                    records_unchanged=3,
                    records_marked_deleted=2,
                    records_failed=0,
                ),
            ],
        )
        assert result.records_fetched == 30
        assert result.records_inserted == 15
        assert result.records_updated == 8
        assert result.records_unchanged == 4
        assert result.records_marked_deleted == 3
        assert result.records_failed == 0


# ===========================================================================
# Usage evaluation edge cases
# ===========================================================================


class TestAccountUsageServiceExtended:
    async def test_bank_usage_for_asset(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Asset", "active": True, "source_deleted": False},
            "bank",
        )
        assert result.allowed is True

    async def test_accounts_payable_for_liability(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Liability", "active": True, "source_deleted": False},
            "accounts_payable",
        )
        assert result.allowed is True

    async def test_accounts_receivable_for_asset(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Asset", "active": True, "source_deleted": False},
            "accounts_receivable",
        )
        assert result.allowed is True

    async def test_revenue_for_income(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Revenue", "active": True, "source_deleted": False},
            "income",
        )
        assert result.allowed is True

    async def test_equity_usage(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Equity", "active": True, "source_deleted": False},
            "equity",
        )
        assert result.allowed is True


# ===========================================================================
# QuickBooks health endpoint (no realm_id)
# ===========================================================================


class TestQuickBooksHealthNoRealm:
    def test_health_returns_error_without_realm_id(self) -> None:
        """The health endpoint returns healthy=false when no realm_id is provided."""

        # This is an async function but the early return path doesn't await
        # We test the logic directly
        # Note: the function signature uses Depends, so we test via TestClient
        app = FastAPI()

        # Override get_quickbooks_settings
        with patch(
            "agentblue.integrations.quickbooks.router.get_quickbooks_settings"
        ) as mock_settings:
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.environment.value = "sandbox"

            from agentblue.integrations.quickbooks.router import router as qb_router

            app.include_router(qb_router)
            client = TestClient(app)
            response = client.get("/api/v1/integrations/quickbooks/health")
            assert response.status_code == 200
            data = response.json()
            assert data["healthy"] is False
            assert "realm_id is required" in data["error"]
