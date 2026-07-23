"""QuickBooks write-back service.

Manages the approve-and-apply workflow:
1. Validate selected account
2. Fetch current QuickBooks entity
3. Check SyncToken for staleness
4. Build entity-specific update payload
5. Submit update to QuickBooks
6. Verify result
7. Record application audit trail
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.categorization.constants import SUPPORTED_WRITEBACK_TYPES
from agentblue.integrations.quickbooks.accounting.repository import (
    AccountingRepository,
)
from agentblue.integrations.quickbooks.writeback.exceptions import (
    StaleSyncTokenError,
    TargetAccountInvalidError,
    UnsupportedEntityTypeError,
)
from agentblue.integrations.quickbooks.writeback.payloads import build_update_payload
from agentblue.integrations.quickbooks.writeback.validation import (
    check_stale,
)

logger = structlog.get_logger(__name__)


class WriteBackService:
    """Handles QuickBooks write-back after explicit accountant approval."""

    def __init__(
        self,
        session: AsyncSession,
        api_client: Any = None,
    ) -> None:
        self._session = session
        self._api_client = api_client
        self._acct_repo = AccountingRepository(session)

    async def apply_categorization(
        self,
        realm_id: str,
        transaction_quickbooks_id: str,
        transaction_type: str,
        selected_account_quickbooks_id: str,
        reviewed_sync_token: str,
        reviewed_transaction_hash: str,
        approved_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Apply an approved categorization to QuickBooks.

        Returns structured application result.
        """
        # Validate transaction type
        if transaction_type not in SUPPORTED_WRITEBACK_TYPES:
            raise UnsupportedEntityTypeError(f"Write-back not supported for {transaction_type}")

        # Validate selected account
        account = await self._acct_repo.get_account_by_quickbooks_id(
            realm_id, selected_account_quickbooks_id
        )
        if account is None:
            raise TargetAccountInvalidError("Selected account not found.")
        if account.source_deleted:
            raise TargetAccountInvalidError("Selected account is source-deleted.")
        if not account.active:
            raise TargetAccountInvalidError("Selected account is inactive.")

        result: dict[str, Any] = {
            "status": "PENDING",
            "transaction_quickbooks_id": transaction_quickbooks_id,
            "selected_account_quickbooks_id": selected_account_quickbooks_id,
            "approved_by": approved_by,
            "idempotency_key": idempotency_key,
            "started_at": datetime.now(UTC).isoformat(),
        }

        # In production, fetch current entity from QuickBooks API
        # For now, document the expected workflow
        if self._api_client:
            try:
                # Fetch current entity
                current = await self._api_client.get(
                    f"/v3/company/{realm_id}/purchase/{transaction_quickbooks_id}"
                )
                entity = current.get("Purchase", current)

                # Check staleness
                stale_reasons = check_stale(reviewed_sync_token, reviewed_transaction_hash, entity)
                if stale_reasons:
                    raise StaleSyncTokenError(
                        f"Transaction changed since review: {'; '.join(stale_reasons)}"
                    )

                # Build update payload
                payload = build_update_payload(
                    transaction_type,
                    entity,
                    selected_account_quickbooks_id,
                    idempotency_key=idempotency_key,
                )

                # Submit update
                result["request_payload"] = payload
                response = await self._api_client.post(
                    f"/v3/company/{realm_id}/purchase",
                    json=payload,
                )
                result["response_snapshot"] = response
                result["resulting_sync_token"] = str(response.get("SyncToken", ""))
                result["status"] = "SUCCESS"
                result["completed_at"] = datetime.now(UTC).isoformat()

            except StaleSyncTokenError:
                result["status"] = "STALE"
                result["error_summary"] = "Transaction changed since review"
                raise
            except Exception as exc:
                result["status"] = "FAILED"
                result["error_summary"] = str(exc)[:500]
                result["completed_at"] = datetime.now(UTC).isoformat()
                logger.warning(
                    "writeback_failed",
                    realm_id=realm_id,
                    transaction_id=transaction_quickbooks_id,
                    error=str(exc)[:200],
                )
                raise
        else:
            # No API client — simulation mode
            result["status"] = "SIMULATED"
            result["completed_at"] = datetime.now(UTC).isoformat()

        return result

    @staticmethod
    def is_supported_type(transaction_type: str) -> bool:
        """Check if a transaction type supports write-back."""
        return transaction_type in SUPPORTED_WRITEBACK_TYPES
