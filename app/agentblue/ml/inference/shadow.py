"""Shadow inference runner.

Executes ML predictions alongside the deterministic rule engine without
altering Stage 7 output.  Stores predictions and comparison results for
later evaluation.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.ml.domain import ShadowOutcome
from agentblue.ml.inference.predictor import MLPredictor
from agentblue.ml.inference.ranking import rank_predictions
from agentblue.ml.models import MlPrediction, MlShadowEvaluation

logger = structlog.get_logger(__name__)


class ShadowInference:
    """Runs ML predictions in shadow mode alongside deterministic rules."""

    def __init__(self, predictor: MLPredictor | None = None) -> None:
        self._predictor = predictor or MLPredictor()

    async def run_shadow(
        self,
        session: AsyncSession,
        model: Any,
        categorization_id: str,
        transaction: dict[str, Any],
        deterministic_recommendation: dict[str, Any],
        class_mapping: dict[str, int],
        calibrator: Any | None = None,
        model_id: str = "",
        realm_id: str = "",
        feature_vector: np.ndarray[Any, Any] | None = None,
        account_validator: Any | None = None,
    ) -> dict[str, Any] | None:
        """Run a shadow prediction and store the comparison.

        This method does NOT alter Stage 7 output.  It runs the ML model
        on the same transaction, stores the prediction, and compares the
        ML top-1 account with the deterministic recommendation.

        A failure in this method does not fail Stage 7 -- all exceptions
        are caught and logged.

        Args:
            session: Async database session.
            model: Loaded ML model object.
            categorization_id: ID of the transaction categorization record.
            transaction: Transaction feature dict.
            deterministic_recommendation: The Stage 7 result dict.
            class_mapping: Label-to-index mapping.
            calibrator: Optional probability calibrator.
            model_id: ID of the MlModel record.
            realm_id: QuickBooks realm ID.
            feature_vector: Pre-computed feature vector.  If None, shadow
                inference is skipped.
            account_validator: Optional AccountValidator for filtering.

        Returns:
            Shadow result dict with keys: ml_top_account, ml_top_prob,
            outcome, predictions.  Returns None on failure.
        """
        if feature_vector is None:
            logger.debug("shadow_skipped_no_features", categorization_id=categorization_id)
            return None

        start = time.monotonic()

        try:
            # Generate predictions.
            predictions = self._predictor.predict(
                model=model,
                features=feature_vector,
                class_mapping=class_mapping,
                calibrator=calibrator,
            )

            if not predictions:
                logger.debug("shadow_no_predictions", categorization_id=categorization_id)
                return None

            # Filter by account validity if validator provided.
            if account_validator is not None:
                predictions = await rank_predictions(predictions, account_validator, realm_id)

            if not predictions:
                return None

            elapsed_ms = int((time.monotonic() - start) * 1000)

            ml_top = predictions[0]
            ml_top_account = ml_top["account_id"]
            ml_top_prob = ml_top["calibrated_prob"]

            # Compare with deterministic recommendation.
            rule_account = deterministic_recommendation.get(
                "recommended_account_quickbooks_id", ""
            )
            outcome = (
                ShadowOutcome.AGREEMENT
                if ml_top_account == rule_account
                else ShadowOutcome.DISAGREEMENT
            )

            # Store ML prediction record.
            pred_record = MlPrediction(
                transaction_id=categorization_id,
                realm_id=realm_id,
                model_id=model_id,
                inference_mode="SHADOW",
                top_predictions={
                    "predictions": [
                        {
                            "account_id": p["account_id"],
                            "raw_prob": p["raw_prob"],
                            "calibrated_prob": p["calibrated_prob"],
                        }
                        for p in predictions
                    ]
                },
                latency_ms=elapsed_ms,
                feature_version="1.0",
            )
            session.add(pred_record)
            await session.flush()

            # Store shadow evaluation record.
            eval_record = MlShadowEvaluation(
                transaction_id=categorization_id,
                realm_id=realm_id,
                model_id=model_id,
                prediction_id=pred_record.id,
                ml_account_quickbooks_id=ml_top_account,
                rule_account_quickbooks_id=rule_account or None,
                outcome=outcome.value,
                resolved=False,
            )
            session.add(eval_record)

            result: dict[str, Any] = {
                "ml_top_account": ml_top_account,
                "ml_top_prob": ml_top_prob,
                "outcome": outcome.value,
                "predictions": predictions,
                "latency_ms": elapsed_ms,
            }

            logger.debug(
                "shadow_inference_complete",
                categorization_id=categorization_id,
                outcome=outcome.value,
                latency_ms=elapsed_ms,
            )

            return result

        except Exception as exc:
            # Shadow inference failure must NOT fail Stage 7.
            logger.warning(
                "shadow_inference_failed",
                categorization_id=categorization_id,
                error=str(exc)[:200],
            )
            return None
