"""ML dataset extraction from Stage 7 categorization tables."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentblue.categorization.domain import CategorizationStatus

logger = structlog.get_logger(__name__)

# Dispositions that indicate the label is unsuitable for training.
_EXCLUDED_DISPOSITIONS: frozenset[str] = frozenset(
    {
        CategorizationStatus.REJECTED.value,
        CategorizationStatus.DEFERRED.value,
        CategorizationStatus.STALE.value,
        CategorizationStatus.SUPERSEDED.value,
        CategorizationStatus.APPLY_FAILED.value,
    }
)


class DatasetExtractor:
    """Extracts labelled training rows from Stage 7 categorization data.

    Joins ``categorization_decision`` (human judgements) with
    ``categorization_training_label`` (approved labels) and
    ``transaction_categorization`` (transaction context) to produce a
    flat, deterministic list of dicts suitable for feature engineering.

    Only rows whose disposition is *not* in ``_EXCLUDED_DISPOSITIONS``
    are included — this filters out rejected, deferred, stale, and
    superseded labels so the training set is clean.
    """

    def __init__(
        self,
        *,
        excluded_dispositions: frozenset[str] | None = None,
    ) -> None:
        self._excluded = excluded_dispositions or _EXCLUDED_DISPOSITIONS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_dataset(
        self,
        session: Session,
        *,
        realm_id: str,
        label_policy_version: str | None = None,
        feature_version: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a deterministic, ordered list of labelled row dicts.

        Each dict contains at minimum:
            categorization_id, transaction_id, transaction_quickbooks_id,
            account_quickbooks_id, label_source, feature_snapshot,
            transaction_type, status, approved_at, engine_version.
        """
        from agentblue.categorization.models import (
            CategorizationTrainingLabel,
            TransactionCategorization,
        )

        # Build the join: training_label ← categorization ← decision
        stmt = (
            select(
                CategorizationTrainingLabel.id.label("label_id"),
                CategorizationTrainingLabel.transaction_id,
                CategorizationTrainingLabel.transaction_quickbooks_id,
                CategorizationTrainingLabel.selected_account_quickbooks_id.label(
                    "account_quickbooks_id"
                ),
                CategorizationTrainingLabel.label_source,
                CategorizationTrainingLabel.feature_snapshot,
                CategorizationTrainingLabel.engine_version,
                CategorizationTrainingLabel.approved_at,
                TransactionCategorization.id.label("categorization_id"),
                TransactionCategorization.transaction_type,
                TransactionCategorization.status,
            )
            .join(
                TransactionCategorization,
                TransactionCategorization.transaction_id
                == CategorizationTrainingLabel.transaction_id,
            )
            .where(CategorizationTrainingLabel.realm_id == realm_id)
            .where(TransactionCategorization.realm_id == realm_id)
        )

        if feature_version is not None:
            stmt = stmt.where(CategorizationTrainingLabel.engine_version == feature_version)

        # Deterministic ordering: transaction_id, then approved_at
        stmt = stmt.order_by(
            CategorizationTrainingLabel.transaction_id,
            CategorizationTrainingLabel.approved_at,
        )

        result = session.execute(stmt)
        rows: list[dict[str, Any]] = []

        for row in result.mappings():
            status = row["status"]
            if status in self._excluded:
                continue

            rows.append(
                {
                    "label_id": row["label_id"],
                    "categorization_id": row["categorization_id"],
                    "transaction_id": row["transaction_id"],
                    "transaction_quickbooks_id": row["transaction_quickbooks_id"],
                    "account_quickbooks_id": row["account_quickbooks_id"],
                    "label_source": row["label_source"],
                    "feature_snapshot": row["feature_snapshot"] or {},
                    "transaction_type": row["transaction_type"],
                    "status": status,
                    "approved_at": row["approved_at"],
                    "engine_version": row["engine_version"],
                }
            )

        logger.info(
            "dataset_extracted",
            realm_id=realm_id,
            total_labels=session.execute(
                select(CategorizationTrainingLabel.id).where(
                    CategorizationTrainingLabel.realm_id == realm_id
                )
            )
            .all()
            .__len__(),
            usable_rows=len(rows),
            excluded=len(self._excluded),
        )

        return rows
