"""Top-k prediction ranking and filtering.

Filters raw model predictions against account validity rules:
active status, correct realm, not deleted.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


class AccountValidator(Protocol):
    """Protocol for checking account validity."""

    async def is_valid_account(
        self,
        realm_id: str,
        account_quickbooks_id: str,
    ) -> bool:
        """Return True if the account is active, not deleted, and in the given realm."""
        ...


async def rank_predictions(
    predictions: list[dict[str, Any]],
    account_validator: AccountValidator,
    realm_id: str,
) -> list[dict[str, Any]]:
    """Filter and rank predictions by account validity.

    Removes predictions for accounts that are inactive, deleted, or
    belong to a different realm.  Preserves the original ranking order
    among valid predictions.

    Args:
        predictions: Raw predictions from MLPredictor.predict().
            Each dict has keys: account_id, raw_prob, calibrated_prob.
        account_validator: Object implementing the AccountValidator protocol.
        realm_id: The QuickBooks realm ID to validate against.

    Returns:
        Filtered list of predictions with valid accounts only.
    """
    valid: list[dict[str, Any]] = []
    filtered_count = 0

    for pred in predictions:
        account_id = pred.get("account_id", "")
        if not account_id:
            filtered_count += 1
            continue

        try:
            is_valid = await account_validator.is_valid_account(realm_id, account_id)
        except Exception as exc:
            logger.warning(
                "account_validation_error",
                account_id=account_id,
                error=str(exc)[:200],
            )
            filtered_count += 1
            continue

        if is_valid:
            valid.append(pred)
        else:
            filtered_count += 1
            logger.debug(
                "prediction_filtered",
                account_id=account_id,
                reason="account_invalid",
            )

    if filtered_count > 0:
        logger.info(
            "predictions_filtered",
            original_count=len(predictions),
            valid_count=len(valid),
            filtered_count=filtered_count,
        )

    return valid
