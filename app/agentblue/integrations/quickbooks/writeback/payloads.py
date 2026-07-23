"""QuickBooks entity-specific update payload construction.

Only Purchase is fully supported in Stage 7.
Other entity types are documented as deferred.
"""

from __future__ import annotations

from typing import Any

import structlog

from agentblue.categorization.constants import SUPPORTED_WRITEBACK_TYPES
from agentblue.integrations.quickbooks.writeback.exceptions import (
    UnsupportedEntityTypeError,
)

logger = structlog.get_logger(__name__)


def build_purchase_update(
    current_entity: dict[str, Any],
    selected_account_quickbooks_id: str,
    line_id: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Build a sparse Purchase update payload.

    Changes only the account reference on the specified line.
    Preserves all other fields and lines.

    QuickBooks sparse update: only supplied fields change.
    """
    entity_id = str(current_entity.get("Id", ""))
    sync_token = str(current_entity.get("SyncToken", "0"))

    # Build minimal update payload
    payload: dict[str, Any] = {
        "Id": entity_id,
        "SyncToken": sync_token,
        "sparse": True,
    }

    # Update line-level account reference
    lines = current_entity.get("Line", [])
    if lines:
        updated_lines = []
        for line in lines:
            line_dict: dict[str, Any] = {
                "Id": line.get("Id", ""),
                "DetailType": line.get("DetailType", ""),
            }

            # Preserve existing detail and change account
            detail_key = line.get("DetailType", "")
            if detail_key == "AccountBasedExpenseLineDetail":
                detail = dict(line.get("AccountBasedExpenseLineDetail", {}))
                detail["AccountRef"] = {"value": selected_account_quickbooks_id}
                line_dict["AccountBasedExpenseLineDetail"] = detail
            elif detail_key == "ItemBasedExpenseLineDetail":
                detail = dict(line.get("ItemBasedExpenseLineDetail", {}))
                detail["AccountRef"] = {"value": selected_account_quickbooks_id}
                line_dict["ItemBasedExpenseLineDetail"] = detail
            else:
                # Preserve line as-is for unsupported detail types
                line_dict = dict(line)

            # Preserve amount and description
            if "Amount" in line:
                line_dict["Amount"] = line["Amount"]
            if "Description" in line:
                line_dict["Description"] = line["Description"]

            updated_lines.append(line_dict)

        payload["Line"] = updated_lines

    return payload


def build_update_payload(
    transaction_type: str,
    current_entity: dict[str, Any],
    selected_account_quickbooks_id: str,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Build entity-specific update payload.

    Only supported types are accepted.
    """
    if transaction_type not in SUPPORTED_WRITEBACK_TYPES:
        raise UnsupportedEntityTypeError(
            f"Write-back not supported for {transaction_type}. "
            f"Supported: {', '.join(sorted(SUPPORTED_WRITEBACK_TYPES))}"
        )

    if transaction_type == "Purchase":
        return build_purchase_update(
            current_entity,
            selected_account_quickbooks_id,
            idempotency_key=idempotency_key,
        )

    if transaction_type == "Bill":
        # Bill uses same line structure as Purchase for expense lines
        return build_purchase_update(
            current_entity,
            selected_account_quickbooks_id,
            idempotency_key=idempotency_key,
        )

    raise UnsupportedEntityTypeError(f"No payload builder for {transaction_type}")
