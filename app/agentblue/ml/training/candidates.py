"""Candidate models for categorisation (gradient boosting family)."""

from __future__ import annotations

import numpy as np
import structlog
from sklearn.ensemble import HistGradientBoostingClassifier

logger = structlog.get_logger(__name__)


def train_hist_gradient_boosting(
    X_train: np.ndarray,
    y_train: np.ndarray,
    *,
    random_state: int = 42,
    max_iter: int = 300,
    learning_rate: float = 0.1,
    max_depth: int | None = 6,
    max_leaf_nodes: int | None = 31,
    min_samples_leaf: int = 20,
    l2_regularization: float = 0.0,
    early_stopping: bool = True,
    validation_fraction: float = 0.1,
    n_iter_no_change: int = 10,
) -> HistGradientBoostingClassifier:
    """Train a histogram-based gradient boosting classifier.

    This is the primary candidate model for the categorisation task.
    ``HistGradientBoostingClassifier`` scales well to large datasets
    and handles missing values natively.

    Parameters
    ----------
    X_train:
        Feature matrix (n_samples, n_features).
    y_train:
        Label array.
    random_state:
        Reproducibility seed.
    max_iter:
        Maximum number of boosting iterations.
    learning_rate:
        Learning rate shrinkage.
    max_depth:
        Maximum depth of individual trees.  ``None`` means unlimited.
    max_leaf_nodes:
        Maximum number of leaf nodes per tree.
    min_samples_leaf:
        Minimum samples required at a leaf node.
    l2_regularization:
        L2 regularisation term on the loss.
    early_stopping:
        Whether to use early stopping on a validation split.
    validation_fraction:
        Fraction of training data held out for early stopping.
    n_iter_no_change:
        Number of iterations with no improvement before stopping.

    Returns
    -------
    HistGradientBoostingClassifier
        Fitted classifier.
    """
    clf = HistGradientBoostingClassifier(
        random_state=random_state,
        max_iter=max_iter,
        learning_rate=learning_rate,
        max_depth=max_depth,
        max_leaf_nodes=max_leaf_nodes,
        min_samples_leaf=min_samples_leaf,
        l2_regularization=l2_regularization,
        early_stopping=early_stopping,
        validation_fraction=validation_fraction,
        n_iter_no_change=n_iter_no_change,
    )

    clf.fit(X_train, y_train)

    logger.info(
        "hist_gradient_boosting_trained",
        n_samples=len(y_train),
        n_features=X_train.shape[1] if hasattr(X_train, "shape") else "unknown",
        n_classes=len(np.unique(y_train)),
        max_iter=max_iter,
        learning_rate=learning_rate,
        n_iter_actual=getattr(clf, "n_iter_", None),
    )
    return clf
