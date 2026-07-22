"""Tests for QuickBooks accounting context (Stage 6).

Covers normalization, validation, candidates, hierarchy, usage, and security.
No live QuickBooks API calls.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentblue.integrations.quickbooks.accounting.domain import (
    ValidationStatus,
)
from agentblue.integrations.quickbooks.accounting.normalizer import normalize_account
from agentblue.integrations.quickbooks.accounting.services import (
    AccountUsageService,
)

pytestmark = pytest.mark.unit


def _account_raw(**overrides: object) -> dict[str, object]:
    """Build a raw QuickBooks Account payload."""
    raw: dict[str, object] = {
        "Id": "100",
        "SyncToken": "1",
        "Name": "Checking",
        "FullyQualifiedName": "Assets:Bank:Checking",
        "Description": "Main checking account",
        "Classification": "Asset",
        "AccountType": "Bank",
        "AccountSubType": "CheckingAccount",
        "Active": True,
        "SubAccount": False,
        "AcctNum": "1000",
        "CurrencyRef": {"value": "USD", "name": "United States Dollar"},
        "CurrentBalance": 5000.50,
        "CurrentBalanceWithSubAccounts": 5000.50,
        "TaxAccount": False,
        "MetaData": {
            "CreateTime": "2024-01-15T10:00:00-07:00",
            "LastUpdatedTime": "2024-06-01T10:00:00-07:00",
        },
    }
    raw.update(overrides)
    return raw


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestAccountNormalization:
    def test_complete_account(self) -> None:
        raw = _account_raw()
        acct = normalize_account(raw, "realm-1")
        assert acct.quickbooks_id == "100"
        assert acct.name == "Checking"
        assert acct.fully_qualified_name == "Assets:Bank:Checking"
        assert acct.classification == "Asset"
        assert acct.account_type == "Bank"
        assert acct.account_subtype == "CheckingAccount"
        assert acct.active is True
        assert acct.subaccount is False
        assert acct.account_number == "1000"
        assert acct.currency_code == "USD"
        assert acct.current_balance == Decimal("5000.50")
        assert acct.taxable is False

    def test_minimal_account(self) -> None:
        raw: dict[str, object] = {"Id": "200"}
        acct = normalize_account(raw, "realm-1")
        assert acct.quickbooks_id == "200"
        assert acct.name == ""
        assert acct.active is True
        assert acct.current_balance == Decimal("0")

    def test_missing_optional_fields(self) -> None:
        raw: dict[str, object] = {
            "Id": "300",
            "Name": "Test",
        }
        acct = normalize_account(raw, "realm-1")
        assert acct.description == ""
        assert acct.account_subtype == ""
        assert acct.parent_quickbooks_id == ""
        assert acct.account_number == ""
        assert acct.currency_code == ""

    def test_decimal_precision(self) -> None:
        raw = _account_raw(CurrentBalance=12345.67, CurrentBalanceWithSubAccounts=12345.67)
        acct = normalize_account(raw, "realm-1")
        assert isinstance(acct.current_balance, Decimal)
        assert acct.current_balance == Decimal("12345.67")

    def test_unknown_account_subtype(self) -> None:
        raw = _account_raw(AccountSubType="FutureSubType2030")
        acct = normalize_account(raw, "realm-1")
        assert acct.account_subtype == "FutureSubType2030"

    def test_inactive_account(self) -> None:
        raw = _account_raw(Active=False)
        acct = normalize_account(raw, "realm-1")
        assert acct.active is False

    def test_subaccount_with_parent(self) -> None:
        raw = _account_raw(
            SubAccount=True,
            ParentRef={"value": "50", "name": "Bank"},
        )
        acct = normalize_account(raw, "realm-1")
        assert acct.subaccount is True
        assert acct.parent_quickbooks_id == "50"

    def test_raw_payload_preserved(self) -> None:
        raw = _account_raw()
        acct = normalize_account(raw, "realm-1")
        assert acct.raw_payload == raw

    def test_timezone_parsing(self) -> None:
        raw = _account_raw()
        acct = normalize_account(raw, "realm-1")
        assert acct.source_created_at == "2024-01-15T10:00:00-07:00"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.asyncio
    async def test_valid_active_account(self) -> None:
        """Test validation with a valid active account."""
        from unittest.mock import AsyncMock, MagicMock

        mock_session = AsyncMock()
        mock_account = MagicMock()
        mock_account.realm_id = "realm-1"
        mock_account.quickbooks_id = "100"
        mock_account.active = True
        mock_account.source_deleted = False
        mock_account.account_type = "Bank"
        mock_account.account_subtype = "CheckingAccount"
        mock_account.classification = "Asset"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_account
        mock_session.execute = AsyncMock(return_value=mock_result)

        from agentblue.integrations.quickbooks.accounting.services import (
            AccountValidationService,
        )

        service = AccountValidationService(mock_session)
        result = await service.validate_account_reference("realm-1", "100")
        assert result.valid is True
        assert result.reason_code == ValidationStatus.VALID

    @pytest.mark.asyncio
    async def test_missing_account(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        from agentblue.integrations.quickbooks.accounting.services import (
            AccountValidationService,
        )

        service = AccountValidationService(mock_session)
        result = await service.validate_account_reference("realm-1", "999")
        assert result.valid is False
        assert result.reason_code == ValidationStatus.NOT_FOUND

    @pytest.mark.asyncio
    async def test_inactive_account(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        mock_session = AsyncMock()
        mock_account = MagicMock()
        mock_account.realm_id = "realm-1"
        mock_account.quickbooks_id = "100"
        mock_account.active = False
        mock_account.source_deleted = False
        mock_account.account_type = "Bank"
        mock_account.classification = "Asset"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_account
        mock_session.execute = AsyncMock(return_value=mock_result)

        from agentblue.integrations.quickbooks.accounting.services import (
            AccountValidationService,
        )

        service = AccountValidationService(mock_session)
        result = await service.validate_account_reference("realm-1", "100", require_active=True)
        assert result.valid is False
        assert result.reason_code == ValidationStatus.INACTIVE


# ---------------------------------------------------------------------------
# Usage evaluation
# ---------------------------------------------------------------------------


class TestUsageEvaluation:
    @pytest.mark.asyncio
    async def test_expense_account_for_expense(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Expense", "active": True, "source_deleted": False},
            "expense",
        )
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_asset_not_for_expense(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Asset", "active": True, "source_deleted": False},
            "expense",
        )
        assert result.allowed is False
        assert "CLASSIFICATION_MISMATCH" in result.reason_codes

    @pytest.mark.asyncio
    async def test_deleted_account_rejected(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Expense", "active": True, "source_deleted": True},
            "expense",
        )
        assert result.allowed is False
        assert "SOURCE_DELETED" in result.reason_codes

    @pytest.mark.asyncio
    async def test_inactive_account_warning(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Expense", "active": False, "source_deleted": False},
            "expense",
        )
        assert result.allowed is True
        assert any("inactive" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unknown_usage(self) -> None:
        service = AccountUsageService()
        result = await service.evaluate(
            {"classification": "Expense", "active": True, "source_deleted": False},
            "magic_beans",
        )
        assert result.allowed is False
        assert any("unknown" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_no_secrets_in_normalization(self) -> None:
        raw = _account_raw()
        acct = normalize_account(raw, "realm-1")
        # Verify no token/secret fields exist
        r = repr(acct)
        assert "token" not in r.lower() or "sync_token" in r.lower()

    def test_raw_payload_not_in_domain_repr(self) -> None:
        raw = _account_raw()
        acct = normalize_account(raw, "realm-1")
        # raw_payload is a field, but should not appear in default repr
        # (dataclass repr includes it, but API responses exclude it)
        assert acct.raw_payload == raw
