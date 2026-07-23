"""Temporal data splitting for train / validation / test sets."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class TemporalSplitter:
    """Splits labelled rows into train / valid / test using *transaction_date*.

    The split is **temporal** — earlier transactions go to training, later
    ones to validation and test — which mirrors the real deployment
    scenario where we predict *future* transactions.

    Duplicate transactions (same ``transaction_id``) are always placed
    in the same split to prevent label leakage.
    """

    def split(
        self,
        rows: list[dict[str, Any]],
        *,
        train_ratio: float = 0.70,
        valid_ratio: float = 0.15,
        test_ratio: float = 0.15,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (train, valid, test) row lists.

        Parameters
        ----------
        rows:
            Labelled rows from :class:`DatasetExtractor`.
        train_ratio:
            Fraction of unique transaction dates used for training.
        valid_ratio:
            Fraction of unique transaction dates used for validation.
        test_ratio:
            Fraction of unique transaction dates used for testing.

        Returns
        -------
        tuple
            ``(train_rows, valid_rows, test_rows)`` — each a list of dicts
            with the same schema as the input.

        Raises
        ------
        ValueError
            If ratios don't sum to ~1.0 or the dataset is empty.
        """
        if not rows:
            raise ValueError("Cannot split an empty dataset")

        abs_sum = abs(train_ratio + valid_ratio + test_ratio - 1.0)
        if abs_sum > 0.001:
            raise ValueError(
                f"Split ratios must sum to 1.0, got {train_ratio + valid_ratio + test_ratio:.4f}"
            )

        # Group rows by transaction_id to keep duplicates together.
        txn_groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            txn_id = row["transaction_id"]
            txn_groups.setdefault(txn_id, []).append(row)

        # Sort transaction groups by the earliest transaction_date in each group.
        def _txn_date_key(group_key: str) -> str:
            return txn_groups[group_key][0].get("transaction_date", "")

        sorted_txn_ids = sorted(txn_groups.keys(), key=_txn_date_key)

        n = len(sorted_txn_ids)
        train_end = int(n * train_ratio)
        valid_end = train_end + int(n * valid_ratio)

        train_ids = sorted_txn_ids[:train_end]
        valid_ids = sorted_txn_ids[train_end:valid_end]
        test_ids = sorted_txn_ids[valid_end:]

        def _collect(ids: list[str]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for tid in ids:
                out.extend(txn_groups[tid])
            return out

        train = _collect(train_ids)
        valid = _collect(valid_ids)
        test = _collect(test_ids)

        # Boundary dates for auditability
        def _boundary_date(ids: list[str]) -> str:
            if not ids:
                return ""
            return txn_groups[ids[0]][0].get("transaction_date", "")

        logger.info(
            "temporal_split_complete",
            total_transactions=n,
            total_rows=len(rows),
            train_txns=len(train_ids),
            valid_txns=len(valid_ids),
            test_txns=len(test_ids),
            train_rows=len(train),
            valid_rows=len(valid),
            test_rows=len(test),
            train_boundary_end=_boundary_date(valid_ids),
            valid_boundary_end=_boundary_date(test_ids),
        )

        return train, valid, test
