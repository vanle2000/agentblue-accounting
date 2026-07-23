"""Deterministic dataset fingerprinting."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_dataset_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Compute a SHA-256 fingerprint of the dataset content.

    The fingerprint is deterministic — the same rows in the same order
    always produce the same hash.  It captures only the fields relevant
    for training (label, features, transaction id) so that metadata
    changes (e.g. ``approved_at`` microseconds) don't alter the hash.

    Parameters
    ----------
    rows:
        List of dicts as returned by :class:`DatasetExtractor`.

    Returns
    -------
    str
        Hex-encoded SHA-256 digest.
    """
    canonical_records: list[str] = []
    for row in rows:
        record = {
            "transaction_id": row.get("transaction_id", ""),
            "account_quickbooks_id": row.get("account_quickbooks_id", ""),
            "label_source": row.get("label_source", ""),
            "feature_snapshot": row.get("feature_snapshot", {}),
        }
        # json.dumps with sort_keys + separators produces a canonical form
        canonical_records.append(json.dumps(record, sort_keys=True, separators=(",", ":")))

    payload = "\n".join(canonical_records)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
