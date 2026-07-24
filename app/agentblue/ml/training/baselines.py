"""Baseline models for comparison against candidate learners."""

from __future__ import annotations

import numpy as np
import structlog
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logger = structlog.get_logger(__name__)


def train_dummy_classifier(
    X_train: np.ndarray,
    y_train: np.ndarray,
    *,
    strategy: str = "most_frequent",
) -> DummyClassifier:
    """Train a dummy classifier baseline.

    Parameters
    ----------
    X_train:
        Feature matrix (n_samples, n_features).
    y_train:
        Label array.
    strategy:
        ``"most_frequent"``, ``"stratified"``, ``"uniform"``, or
        ``"prior"``.

    Returns
    -------
    DummyClassifier
        Fitted dummy classifier.
    """
    clf = DummyClassifier(strategy=strategy, random_state=42)
    clf.fit(X_train, y_train)

    logger.info(
        "dummy_classifier_trained",
        strategy=strategy,
        n_samples=len(y_train),
        n_classes=len(np.unique(y_train)),
    )
    return clf


def train_logistic_regression(
    X_train: np.ndarray,
    y_train: np.ndarray,
    *,
    random_state: int = 42,
    max_iter: int = 1000,
    C: float = 1.0,
    solver: str = "lbfgs",
) -> Pipeline:
    """Train a logistic regression inside a scaling pipeline.

    Parameters
    ----------
    X_train:
        Feature matrix.
    y_train:
        Label array.
    random_state:
        Reproducibility seed.
    max_iter:
        Maximum solver iterations.
    C:
        Inverse regularisation strength.
    solver:
        Optimisation algorithm.

    Returns
    -------
    Pipeline
        Fitted pipeline (StandardScaler → LogisticRegression).
    """
    pipe = Pipeline(
        [
            ("scaler", StandardScaler(with_mean=False)),
            (
                "clf",
                LogisticRegression(
                    random_state=random_state,
                    max_iter=max_iter,
                    C=C,
                    solver=solver,
                ),
            ),
        ]
    )

    pipe.fit(X_train, y_train)

    logger.info(
        "logistic_regression_trained",
        n_samples=len(y_train),
        n_features=X_train.shape[1] if hasattr(X_train, "shape") else "unknown",
        n_classes=len(np.unique(y_train)),
        C=C,
        solver=solver,
    )
    return pipe
