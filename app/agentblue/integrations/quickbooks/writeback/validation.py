"""SyncToken and stale-state validation for write-back."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_entity_hash(entity: dict[str, Any]) -> str:
    """Compute a stable hash of material transaction fields.

    Includes line IDs and structure for stale-state detection.
    """
    lines = entity.get("Line", [])
    line_fingerprints = []
    for line in lines:
        line_fp = {
            "id": line.get("Id", ""),
            "detail_type": line.get("DetailType", ""),
            "amount": str(line.get("Amount", "")),
        }
        # Include account ref from line detail
        detail_key = line.get("DetailType", "")
        detail = line.get(detail_key, {})
        if isinstance(detail, dict):
            acct_ref = detail.get("AccountRef", {})
            line_fp["account_ref"] = str(acct_ref.get("value", ""))
        line_fingerprints.append(line_fp)

    snapshot = {
        "Id": entity.get("Id"),
        "SyncToken": entity.get("SyncToken"),
        "TotalAmt": entity.get("TotalAmt"),
        "TxnDate": entity.get("TxnDate"),
        "CurrencyRef": str(entity.get("CurrencyRef", "")),
        "line_count": len(lines),
        "lines": line_fingerprints,
    }
    raw = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_stale(
    reviewed_sync_token: str,
    reviewed_hash: str,
    current_entity: dict[str, Any],
) -> list[str]:
    """Check if entity has changed since review.

    Returns list of staleness reasons (empty = not stale).
    """
    reasons: list[str] = []

    current_token = str(current_entity.get("SyncToken", ""))
    if current_token != reviewed_sync_token:
        reasons.append(
            f"sync_token_changed: reviewed={reviewed_sync_token}, current={current_token}"
        )

    current_hash = compute_entity_hash(current_entity)
    if current_hash != reviewed_hash:
        reasons.append("transaction_hash_changed")

    return reasons


def find_target_line(
    entity: dict[str, Any],
    target_line_id: str,
) -> dict[str, Any] | None:
    """Find a specific line by ID in the entity."""
    for line in entity.get("Line", []):
        if str(line.get("Id", "")) == target_line_id:
            return dict(line)
    return None


def extract_line_account_ref(line: dict[str, Any]) -> str:
    """Extract the account reference from a line."""
    detail_key = line.get("DetailType", "")
    detail = line.get(detail_key, {})
    if isinstance(detail, dict):
        ref = detail.get("AccountRef", {})
        return str(ref.get("value", ""))
    return ""
