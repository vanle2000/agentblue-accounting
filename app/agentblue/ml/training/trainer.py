"""Training orchestration for categorisation models."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import structlog

from agentblue.ml.data.fingerprint import compute_dataset_fingerprint
from agentblue.ml.data.splitting import TemporalSplitter
from agentblue.ml.data.validation import DatasetValidator
from agentblue.ml.features.transformers import FeatureTransformer
from agentblue.ml.training.baselines import (
    train_dummy_classifier,
    train_logistic_regression,
)
from agentblue.ml.training.calibration import ProbabilityCalibrator
from agentblue.ml.training.candidates import train_hist_gradient_boosting
from agentblue.ml.training.evaluation import compute_metrics

logger = structlog.get_logger(__name__)


@dataclass
class TrainingRunResult:
    """Captures the outcome of a single training run."""

    run_id: str
    model_type: str
    seed: int
    dataset_fingerprint: str
    split_boundaries: dict[str, str]
    metrics: dict[str, Any]
    calibration_method: str | None
    duration_seconds: float
    artifact_path: str | None = None
    hyperparams: dict[str, Any] = field(default_factory=dict)


# Registry of model training functions
_MODEL_REGISTRY: dict[str, Any] = {
    "dummy": lambda X, y, **kw: train_dummy_classifier(
        X, y, strategy=kw.get("strategy", "most_frequent")
    ),
    "logistic_regression": lambda X, y, **kw: train_logistic_regression(X, y, **kw),
    "hist_gradient_boosting": lambda X, y, **kw: train_hist_gradient_boosting(X, y, **kw),
}


class ModelTrainer:
    """Orchestrates the full training pipeline.

    Steps:
        1. Validate dataset
        2. Fingerprint dataset
        3. Temporal split → train / valid / test
        4. Feature transform (fit on train only)
        5. Train model
        6. Evaluate on all splits
        7. Calibrate on validation set
        8. Return :class:`TrainingRunResult`
    """

    def __init__(self) -> None:
        self._validator = DatasetValidator()
        self._splitter = TemporalSplitter()
        self._transformer = FeatureTransformer()

    def train(
        self,
        dataset: list[dict[str, Any]],
        *,
        model_type: str = "hist_gradient_boosting",
        hyperparams: dict[str, Any] | None = None,
        seed: int = 42,
    ) -> TrainingRunResult:
        """Run the full training pipeline and return a result summary.

        Parameters
        ----------
        dataset:
            Labelled rows from :class:`DatasetExtractor`.
        model_type:
            One of ``"dummy"``, ``"logistic_regression"``, or
            ``"hist_gradient_boosting"``.
        hyperparams:
            Model-specific hyper-parameters passed through to the
            training function.
        seed:
            Random seed for reproducibility.

        Returns
        -------
        TrainingRunResult
            Structured training outcome including metrics on all splits.
        """
        t0 = time.monotonic()
        hyperparams = dict(hyperparams or {})

        run_id = _generate_run_id(dataset, model_type, seed)

        logger.info("training_run_started", run_id=run_id, model_type=model_type, seed=seed)

        # 1. Validate
        quality = self._validator.validate(dataset)
        if not quality["valid"]:
            raise ValueError(f"Dataset quality check failed: {quality['errors']}")

        # 2. Fingerprint
        fingerprint = compute_dataset_fingerprint(dataset)

        # 3. Split
        train_rows, valid_rows, test_rows = self._splitter.split(dataset)

        # 4. Extract labels and build feature matrices
        y_train = self._extract_labels(train_rows)
        y_valid = self._extract_labels(valid_rows)
        y_test = self._extract_labels(test_rows)

        X_train = _to_dense(self._transformer.fit_transform(train_rows))
        X_valid = _to_dense(self._transformer.transform(valid_rows))
        X_test = _to_dense(self._transformer.transform(test_rows))

        # 5. Train
        if model_type not in _MODEL_REGISTRY:
            raise ValueError(
                f"Unknown model_type '{model_type}'. Choose from {list(_MODEL_REGISTRY)}"
            )

        model_fn = _MODEL_REGISTRY[model_type]
        model = model_fn(X_train, y_train, random_state=seed, **hyperparams)

        # 6. Evaluate on all splits
        y_pred_test = model.predict(X_test)
        y_proba_test = _predict_proba_safe(model, X_test)
        test_metrics = compute_metrics(y_test, y_pred_test, y_proba_test)

        y_pred_train = model.predict(X_train)
        y_proba_train = _predict_proba_safe(model, X_train)
        train_metrics = compute_metrics(y_train, y_pred_train, y_proba_train)

        # 7. Calibrate on validation set
        calibrator = ProbabilityCalibrator()
        calibration_method: str | None = "sigmoid"
        try:
            if calibration_method:
                calibrator.fit(model, X_valid, y_valid, method=calibration_method)
        except Exception as exc:
            logger.warning("calibration_failed", error=str(exc))
            calibration_method = None

        # Re-evaluate with calibrated model if calibration succeeded
        if calibrator.is_fitted:
            y_proba_cal = calibrator.predict_proba(X_test)
            y_pred_cal = np.argmax(y_proba_cal, axis=1)
            test_metrics = compute_metrics(y_test, y_pred_cal, y_proba_cal)

        duration = time.monotonic() - t0

        # Split boundaries for auditability
        split_boundaries = {}
        if valid_rows:
            split_boundaries["train_end"] = (
                train_rows[-1].get("transaction_date", "") if train_rows else ""
            )
            split_boundaries["valid_end"] = (
                valid_rows[-1].get("transaction_date", "") if valid_rows else ""
            )

        result = TrainingRunResult(
            run_id=run_id,
            model_type=model_type,
            seed=seed,
            dataset_fingerprint=fingerprint,
            split_boundaries=split_boundaries,
            metrics={
                "train": train_metrics,
                "test": test_metrics,
            },
            calibration_method=calibration_method,
            duration_seconds=round(duration, 3),
            hyperparams=hyperparams,
        )

        logger.info(
            "training_run_completed",
            run_id=run_id,
            model_type=model_type,
            test_accuracy=test_metrics.get("accuracy"),
            test_macro_f1=test_metrics.get("macro_f1"),
            duration_seconds=result.duration_seconds,
        )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_labels(rows: list[dict[str, Any]]) -> np.ndarray:
        """Extract label array from rows."""
        return np.array([r["account_quickbooks_id"] for r in rows])


def _to_dense(X: Any) -> np.ndarray:
    """Convert sparse matrix to dense if needed."""
    if hasattr(X, "toarray"):
        return X.toarray()
    return np.asarray(X)


def _predict_proba_safe(model: Any, X: np.ndarray) -> np.ndarray | None:
    """Return probability predictions if available."""
    if hasattr(model, "predict_proba"):
        return model.predict_proba(X)
    return None


def _generate_run_id(
    dataset: list[dict[str, Any]],
    model_type: str,
    seed: int,
) -> str:
    """Generate a deterministic, traceable run ID."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    content = f"{len(dataset)}:{model_type}:{seed}:{ts}"
    short_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
    return f"run_{ts}_{model_type}_{short_hash}"
