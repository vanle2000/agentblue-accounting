"""ML predictor for transaction categorization.

Generates calibrated probability predictions from a trained model,
returning top-k account candidates with raw and calibrated scores.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import structlog

from agentblue.ml.constants import ML_TOP_K

logger = structlog.get_logger(__name__)


class MLPredictor:
    """Generates predictions from a trained classification model."""

    def predict(
        self,
        model: Any,
        features: np.ndarray[Any, Any],
        class_mapping: dict[str, int],
        inverse_class_mapping: dict[int, str] | None = None,
        calibrator: Any | None = None,
        top_k: int = ML_TOP_K,
    ) -> list[dict[str, Any]]:
        """Run inference and return top-k predictions.

        Args:
            model: A fitted scikit-learn-compatible classifier.
            features: Feature vector of shape (1, n_features).
            class_mapping: Mapping of label string -> integer index.
            inverse_class_mapping: Mapping of integer index -> label string.
                Built from class_mapping if not provided.
            calibrator: Optional fitted calibrator (e.g. CalibratedClassifierCV).
                If None, raw probabilities are used as calibrated.
            top_k: Number of top predictions to return.

        Returns:
            A list of dicts with keys: account_id, raw_prob, calibrated_prob.
            Sorted by calibrated_prob descending.  Handles unseen classes
            gracefully by returning empty predictions if the model cannot
            produce probabilities.
        """
        if inverse_class_mapping is None:
            inverse_class_mapping = {v: k for k, v in class_mapping.items()}

        start = time.monotonic()

        try:
            # Reshape for single-sample prediction.
            x = features.reshape(1, -1) if features.ndim == 1 else features

            # Get raw probabilities.
            if hasattr(model, "predict_proba"):
                raw_probs = model.predict_proba(x)[0]
            else:
                # Fallback: use decision_function and softmax.
                if hasattr(model, "decision_function"):
                    scores = model.decision_function(x)
                    if scores.ndim == 1:
                        scores = scores.reshape(1, -1)
                    exp_scores = np.exp(scores - np.max(scores, axis=1, keepdims=True))
                    raw_probs = exp_scores[0] / exp_scores[0].sum()
                else:
                    logger.warning("model_has_no_probability_support")
                    return []

            # Get calibrated probabilities.
            if calibrator is not None and hasattr(calibrator, "predict_proba"):
                cal_probs = calibrator.predict_proba(x)[0]
            else:
                cal_probs = raw_probs

        except Exception as exc:
            logger.warning(
                "prediction_failed",
                error=str(exc)[:200],
            )
            return []

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Build predictions, handling unseen class indices gracefully.
        predictions: list[dict[str, Any]] = []
        for idx in range(len(raw_probs)):
            account_id = inverse_class_mapping.get(idx)
            if account_id is None:
                # Unseen class index -- skip gracefully.
                continue
            predictions.append(
                {
                    "account_id": account_id,
                    "raw_prob": float(raw_probs[idx]),
                    "calibrated_prob": float(cal_probs[idx]),
                }
            )

        # Sort by calibrated probability descending and take top-k.
        predictions.sort(key=lambda p: p["calibrated_prob"], reverse=True)
        top = predictions[:top_k]

        logger.debug(
            "prediction_complete",
            top_k=len(top),
            latency_ms=elapsed_ms,
        )
        return top
