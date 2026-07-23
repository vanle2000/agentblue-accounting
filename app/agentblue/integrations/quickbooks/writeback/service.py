"""QuickBooks write-back service.

Manages the approve-and-apply workflow:
1. Validate selected account
2. Fetch current QuickBooks entity
3. Check SyncToken for staleness
4. Build entity-specific update payload
5. Submit update to QuickBooks
6. Verify returned entity matches approved account (line-specific)
7. Persist resulting SyncToken
8. Record application audit trail
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
    VerificationFailedError,
)
from agentblue.integrations.quickbooks.writeback.payloads import (
    build_update_payload,
    get_entity_endpoint,
)
from agentblue.integrations.quickbooks.writeback.validation import (
    check_stale,
    extract_line_account_ref,
    find_target_line,
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
        target_line_id: str = "",
    ) -> dict[str, Any]:
        """Apply an approved categorization to QuickBooks.

        Returns structured application result.
        """
        if transaction_type not in SUPPORTED_WRITEBACK_TYPES:
            raise UnsupportedEntityTypeError(
                f"Write-back not supported for {transaction_type}. "
                f"Supported: {', '.join(sorted(SUPPORTED_WRITEBACK_TYPES))}"
            )

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

        if self._api_client:
            endpoint = get_entity_endpoint(transaction_type, realm_id, transaction_quickbooks_id)
            update_endpoint = get_entity_endpoint(transaction_type, realm_id)

            try:
                # Step 1: Fetch current entity
                current = await self._api_client.get(endpoint)
                entity = current.get(transaction_type, current)

                # Step 2: Check staleness
                stale_reasons = check_stale(reviewed_sync_token, reviewed_transaction_hash, entity)
                if stale_reasons:
                    raise StaleSyncTokenError(
                        f"Transaction changed since review: {'; '.join(stale_reasons)}"
                    )

                # Step 3: Verify target line exists
                if target_line_id:
                    target_line = find_target_line(entity, target_line_id)
                    if target_line is None:
                        raise VerificationFailedError(
                            f"Target line {target_line_id} not found in current entity"
                        )

                # Step 4: Build update payload
                payload = build_update_payload(
                    transaction_type,
                    entity,
                    selected_account_quickbooks_id,
                    idempotency_key=idempotency_key,
                )
                result["request_payload"] = payload

                # Step 5: Submit update
                response = await self._api_client.post(
                    update_endpoint,
                    json=payload,
                )
                result["response_snapshot"] = response
                result["quickbooks_request_id"] = str(response.get("requestId", ""))

                # Step 6: Post-write line-specific verification
                returned_entity = response.get(transaction_type, response)
                verified = self._verify_write(
                    returned_entity,
                    transaction_type,
                    selected_account_quickbooks_id,
                    target_line_id,
                    str(entity.get("TotalAmt", "")),
                )

                if not verified["success"]:
                    result["status"] = "VERIFICATION_FAILED"
                    result["error_summary"] = verified["reason"]
                    raise VerificationFailedError(verified["reason"])

                result["resulting_sync_token"] = str(returned_entity.get("SyncToken", ""))
                result["status"] = "SUCCESS"
                result["completed_at"] = datetime.now(UTC).isoformat()

            except StaleSyncTokenError:
                result["status"] = "STALE"
                result["error_summary"] = "Transaction changed since review"
                raise
            except VerificationFailedError:
                result["status"] = "VERIFICATION_FAILED"
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
            result["status"] = "SIMULATED"
            result["completed_at"] = datetime.now(UTC).isoformat()

        return result

    @staticmethod
    def _verify_write(
        returned_entity: dict[str, Any],
        transaction_type: str,
        approved_account_id: str,
        target_line_id: str,
        original_total: str,
    ) -> dict[str, Any]:
        """Verify the returned entity matches expectations."""
        # Verify total unchanged
        returned_total = str(returned_entity.get("TotalAmt", ""))
        if returned_total != original_total:
            return {
                "success": False,
                "reason": f"Total changed: {original_total} -> {returned_total}",
            }

        # Verify target line has correct account
        if target_line_id:
            target_line = find_target_line(returned_entity, target_line_id)
            if target_line is None:
                return {
                    "success": False,
                    "reason": f"Target line {target_line_id} missing from response",
                }
            actual_account = extract_line_account_ref(target_line)
            if actual_account != approved_account_id:
                return {
                    "success": False,
                    "reason": (
                        f"Line {target_line_id} account mismatch: "
                        f"expected {approved_account_id}, got {actual_account}"
                    ),
                }
        else:
            # Transaction-level verification
            lines = returned_entity.get("Line", [])
            if lines:
                first_account = extract_line_account_ref(lines[0])
                if first_account != approved_account_id:
                    return {
                        "success": False,
                        "reason": (
                            f"Account mismatch: expected {approved_account_id}, "
                            f"got {first_account}"
                        ),
                    }

        return {"success": True, "reason": ""}

    @staticmethod
    def is_supported_type(transaction_type: str) -> bool:
        return transaction_type in SUPPORTED_WRITEBACK_TYPES
