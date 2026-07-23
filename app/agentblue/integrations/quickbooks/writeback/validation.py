"""SyncToken and stale-state validation for write-back."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_entity_hash(entity: dict[str, Any]) -> str:
    """Compute a stable hash of key transaction fields.

    Used for stale-state detection.
    """
    snapshot = {
        "Id": entity.get("Id"),
        "SyncToken": entity.get("SyncToken"),
        "TotalAmt": entity.get("TotalAmt"),
        "TxnDate": entity.get("TxnDate"),
        "Line_count": len(entity.get("Line", [])),
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
