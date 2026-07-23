"""Categorization feature extraction."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from agentblue.categorization.constants import FEATURE_VERSION
from agentblue.categorization.domain import TransactionFeature
from agentblue.categorization.normalization import normalize_text, normalize_vendor


def extract_features(
    realm_id: str,
    transaction: dict[str, Any],
    transaction_id: str,
) -> TransactionFeature:
    """Extract categorization features from a Stage 5 transaction."""
    vendor = ""
    counterparty = transaction.get("counterparty_name_snapshot", "")
    if counterparty:
        vendor = counterparty

    description = transaction.get("document_number", "")
    memo = transaction.get("private_note", "")
    txn_type = transaction.get("entity_type", "")
    amount = Decimal(str(transaction.get("total_amount", "0")))
    currency = transaction.get("currency_code", "")
    txn_date = transaction.get("transaction_date", "")
    qb_id = transaction.get("quickbooks_id", "")
    acct_id = transaction.get("account_quickbooks_id", "")

    existing_accounts: list[str] = []
    if acct_id:
        existing_accounts.append(acct_id)

    return TransactionFeature(
        realm_id=realm_id,
        transaction_id=transaction_id,
        transaction_quickbooks_id=qb_id,
        transaction_type=txn_type,
        normalized_vendor=normalize_vendor(vendor),
        normalized_description=normalize_text(description),
        normalized_memo=normalize_text(memo),
        amount=amount,
        absolute_amount=abs(amount),
        currency=currency,
        transaction_date=txn_date,
        line_count=1,
        existing_account_ids=existing_accounts,
        feature_version=FEATURE_VERSION,
    )
