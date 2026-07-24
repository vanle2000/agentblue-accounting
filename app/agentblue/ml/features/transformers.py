"""Feature transformers for ML training and inference.

Design decisions:
* ``fit`` is called **only** on training data to prevent data leakage.
* Text features use ``TfidfVectorizer`` with bounded vocabulary.
* Numeric features are standard-scaled.
* Categorical features are one-hot encoded via ``OneHotEncoder``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

logger = structlog.get_logger(__name__)

# Default caps — keeps feature dimensions bounded.
_TFIDF_MAX_FEATURES = 500
_TFIDF_NGRAM_RANGE = (1, 2)
_TEXT_MAX_LEN = 500  # characters

_NUMERIC_COLS = [
    "amount",
    "absolute_amount",
    "transaction_date_day_of_week",
    "transaction_date_month",
]
_TEXT_COLS = ["description_text", "memo_text", "normalized_vendor"]
_CATEGORICAL_COLS = ["transaction_type"]


def _to_1d_str(x: np.ndarray) -> np.ndarray:
    """Flatten a 2D column array to 1D string array (for TfidfVectorizer)."""
    return x.ravel().astype(str)


@dataclass
class FeatureTransformer:
    """Wraps a scikit-learn ``ColumnTransformer`` with fit / transform semantics.

    Usage::

        tx = FeatureTransformer()
        X_train = tx.fit_transform(train_rows)
        X_test  = tx.transform(test_rows)
    """

    _column_transformer: ColumnTransformer | None = field(default=None, init=False, repr=False)
    _fitted: bool = field(default=False, init=False)
    _feature_names: list[str] = field(default_factory=list, init=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit_transform(self, rows: list[dict[str, Any]]) -> np.ndarray:
        """Fit on *training* rows and return the transformed feature matrix."""
        df = self._rows_to_dataframe(rows)
        ct = self._build_column_transformer()
        X = ct.fit_transform(df)
        self._column_transformer = ct
        self._fitted = True
        logger.info(
            "feature_transformer_fitted",
            n_rows=X.shape[0],
            n_features=X.shape[1],
        )
        return X

    def transform(self, rows: list[dict[str, Any]]) -> np.ndarray:
        """Transform rows using the already-fitted transformer."""
        if not self._fitted or self._column_transformer is None:
            raise RuntimeError(
                "FeatureTransformer has not been fitted yet. Call fit_transform first."
            )
        df = self._rows_to_dataframe(rows)
        return self._column_transformer.transform(df)

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def feature_names(self) -> list[str]:
        return list(self._feature_names)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rows_to_dataframe(rows: list[dict[str, Any]]) -> Any:
        """Convert row dicts to a pandas DataFrame."""
        import pandas as pd

        records: list[dict[str, Any]] = []
        for row in rows:
            fs = row.get("feature_snapshot", {})
            record = {
                "amount": float(fs.get("amount", 0)),
                "absolute_amount": float(fs.get("absolute_amount", 0)),
                "transaction_type": str(fs.get("transaction_type", "")),
                "transaction_date_day_of_week": int(fs.get("transaction_date_day_of_week", 0)),
                "transaction_date_month": int(fs.get("transaction_date_month", 0)),
                "description_text": str(fs.get("normalized_description", ""))[:_TEXT_MAX_LEN],
                "memo_text": str(fs.get("normalized_memo", ""))[:_TEXT_MAX_LEN],
                "normalized_vendor": str(fs.get("normalized_vendor", ""))[:_TEXT_MAX_LEN],
            }
            records.append(record)

        return pd.DataFrame.from_records(records)

    @staticmethod
    def _build_column_transformer() -> ColumnTransformer:
        """Build the sklearn ``ColumnTransformer`` with bounded pipelines."""

        numeric_pipe = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
            ]
        )

        # Text columns need a 1D string array for TfidfVectorizer,
        # so we use FunctionTransformer to flatten after imputation.
        text_pipes: list[tuple[str, Pipeline, list[str]]] = []
        for col in _TEXT_COLS:
            text_pipes.append(
                (
                    f"tfidf_{col}",
                    Pipeline(
                        [
                            ("imputer", SimpleImputer(strategy="constant", fill_value="")),
                            ("to_1d", FunctionTransformer(_to_1d_str, validate=False)),
                            (
                                "tfidf",
                                TfidfVectorizer(
                                    max_features=_TFIDF_MAX_FEATURES // len(_TEXT_COLS),
                                    ngram_range=_TFIDF_NGRAM_RANGE,
                                    sublinear_tf=True,
                                    strip_accents="unicode",
                                ),
                            ),
                        ]
                    ),
                    [col],
                )
            )

        categorical_pipe = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="constant", fill_value="__MISSING__")),
                ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=True)),
            ]
        )

        transformers: list[tuple[str, Any, list[str]]] = [
            ("numeric", numeric_pipe, _NUMERIC_COLS),
            ("categorical", categorical_pipe, _CATEGORICAL_COLS),
        ]
        transformers.extend(text_pipes)

        return ColumnTransformer(transformers, remainder="drop", sparse_threshold=0.3)
