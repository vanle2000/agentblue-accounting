"""Probability calibration for ML classifiers."""

from __future__ import annotations

import numpy as np
import structlog
from sklearn.base import BaseEstimator
from sklearn.calibration import CalibratedClassifierCV

logger = structlog.get_logger(__name__)


class ProbabilityCalibrator:
    """Wraps a fitted classifier with calibrated probability estimates.

    Two calibration methods are supported:

    * **sigmoid** (Platt scaling) — works well for linear models.
    * **isotonic** — non-parametric, better for larger calibration sets.
    """

    def __init__(self) -> None:
        self._calibrator: CalibratedClassifierCV | None = None
        self._method: str = "sigmoid"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def calibrate_sigmoid(
        model: BaseEstimator,
        X_cal: np.ndarray,
        y_cal: np.ndarray,
        *,
        cv: int = 5,
    ) -> CalibratedClassifierCV:
        """Calibrate using Platt (sigmoid) scaling.

        Parameters
        ----------
        model:
            A fitted classifier with a ``predict_proba`` method.
        X_cal:
            Calibration feature matrix.
        y_cal:
            Calibration labels.
        cv:
            Number of cross-validation folds for calibration.

        Returns
        -------
        CalibratedClassifierCV
            Calibrated wrapper.
        """
        return ProbabilityCalibrator.calibrate(
            model,
            X_cal,
            y_cal,
            method="sigmoid",
            cv=cv,
        )

    @staticmethod
    def calibrate(
        model: BaseEstimator,
        X_cal: np.ndarray,
        y_cal: np.ndarray,
        *,
        method: str = "sigmoid",
        cv: int = 5,
    ) -> CalibratedClassifierCV:
        """Calibrate probability estimates of *model*.

        Parameters
        ----------
        model:
            Fitted classifier.
        X_cal:
            Calibration feature matrix.
        y_cal:
            Calibration labels.
        method:
            ``"sigmoid"`` or ``"isotonic"``.
        cv:
            Number of cross-validation folds.

        Returns
        -------
        CalibratedClassifierCV
            Calibrated wrapper (already fitted on the calibration data).
        """
        cal = CalibratedClassifierCV(
            estimator=model,
            method=method,
            cv=cv,
        )
        cal.fit(X_cal, y_cal)

        logger.info(
            "probability_calibrated",
            method=method,
            cv=cv,
            n_cal_samples=len(y_cal),
            n_classes=len(np.unique(y_cal)),
        )
        return cal

    def fit(
        self,
        model: BaseEstimator,
        X_cal: np.ndarray,
        y_cal: np.ndarray,
        *,
        method: str = "sigmoid",
        cv: int = 5,
    ) -> None:
        """Fit and store the calibrator (instance method variant)."""
        self._method = method
        self._calibrator = self.calibrate(model, X_cal, y_cal, method=method, cv=cv)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Predict calibrated probabilities."""
        if self._calibrator is None:
            raise RuntimeError("Calibrator not fitted. Call fit() first.")
        return self._calibrator.predict_proba(X)

    @property
    def is_fitted(self) -> bool:
        return self._calibrator is not None

    @property
    def calibrator(self) -> CalibratedClassifierCV | None:
        return self._calibrator
