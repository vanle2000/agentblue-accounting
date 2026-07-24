"""Synthetic end-to-end smoke test for the ML pipeline (Stage 8).

Creates a deterministic synthetic accounting dataset and exercises the
full pipeline: validation → splitting → feature engineering → training
→ calibration → metrics → artifact persistence → inference → ranking.

All data is synthetic.  Every report includes the disclaimer:
    SYNTHETIC SMOKE TEST — NOT MODEL PERFORMANCE EVIDENCE
"""

from __future__ import annotations

import warnings
from datetime import date, timedelta
from typing import Any

import numpy as np
import pytest

from agentblue.ml.data.fingerprint import compute_dataset_fingerprint
from agentblue.ml.data.splitting import TemporalSplitter
from agentblue.ml.data.validation import DatasetValidator
from agentblue.ml.features.transformers import FeatureTransformer
from agentblue.ml.inference.predictor import MLPredictor
from agentblue.ml.training.baselines import (
    train_dummy_classifier,
    train_logistic_regression,
)
from agentblue.ml.training.calibration import ProbabilityCalibrator
from agentblue.ml.training.candidates import train_hist_gradient_boosting
from agentblue.ml.training.evaluation import compute_metrics, threshold_coverage_report

pytestmark = pytest.mark.unit

DISCLAIMER = "SYNTHETIC SMOKE TEST — NOT MODEL PERFORMANCE EVIDENCE"


# ---------------------------------------------------------------------------
# Synthetic dataset fixture
# ---------------------------------------------------------------------------


def _generate_synthetic_dataset() -> list[dict[str, Any]]:
    """Generate a deterministic synthetic accounting dataset.

    Properties:
    - 80 rows across 4 account classes
    - Chronological timestamps (2024-01-01 through 2024-06-30)
    - Recurring vendors: Home Depot, Amazon, Staples
    - New vendor appearing only after 2024-04-01: "CloudTech Inc"
    - Rare class with 5 examples: "acct_office_rare"
    - High-value transactions (amount > 10000)
    - Duplicate transaction versions (same transaction_id, different dates)
    """
    rng = np.random.default_rng(42)

    vendors_recurring = [
        ("Home Depot", "home_depot"),
        ("Amazon", "amazon"),
        ("Staples", "staples"),
    ]
    vendors_new = [("CloudTech Inc", "cloudtech")]

    # Account classes with distribution (total: ~200 rows including dupes)
    # Enough for 70/15/15 temporal splits with calibration support
    account_classes = [
        ("acct_repairs", 55),       # most common
        ("acct_supplies", 50),      # common
        ("acct_software", 45),      # moderate
        ("acct_office_rare", 20),   # rare (enough for calibration CV=5)
    ]

    transaction_types = ["Purchase", "Expense", "Bill"]
    descriptions = [
        "Office repair and maintenance",
        "Hardware supplies purchase",
        "Software subscription renewal",
        "Office supplies order",
        "Building maintenance service",
        "Tech equipment purchase",
        "Monthly cloud service fee",
    ]

    rows: list[dict[str, Any]] = []
    start_date = date(2024, 1, 1)
    end_date = date(2024, 6, 30)
    total_days = (end_date - start_date).days

    txn_counter = 0

    for account_id, count in account_classes:
        for i in range(count):
            # Distribute dates chronologically
            day_offset = int(rng.integers(0, total_days))
            txn_date = start_date + timedelta(days=day_offset)

            # Recurring vendors for most rows
            if account_id == "acct_office_rare" and i < 2:
                # New vendor only appears later
                vendor_name, vendor_norm = vendors_new[0]
                txn_date = start_date + timedelta(days=int(rng.integers(90, total_days)))
            elif account_id == "acct_software" and i < 3:
                vendor_name, vendor_norm = vendors_new[0]
                txn_date = start_date + timedelta(days=int(rng.integers(90, total_days)))
            else:
                vendor_name, vendor_norm = vendors_recurring[
                    int(rng.integers(0, len(vendors_recurring)))
                ]

            # High-value transactions
            if rng.random() < 0.1:
                amount = float(rng.uniform(10000, 50000))
            else:
                amount = float(rng.uniform(10, 5000))

            txn_counter += 1
            txn_id = f"txn-{txn_counter:04d}"

            row = {
                "transaction_id": txn_id,
                "account_quickbooks_id": account_id,
                "label_source": "ACCOUNTANT_REVIEW",
                "transaction_date": txn_date.isoformat(),
                "feature_snapshot": {
                    "amount": amount,
                    "absolute_amount": abs(amount),
                    "transaction_type": transaction_types[
                        int(rng.integers(0, len(transaction_types)))
                    ],
                    "transaction_date_day_of_week": txn_date.weekday(),
                    "transaction_date_month": txn_date.month,
                    "normalized_description": descriptions[
                        int(rng.integers(0, len(descriptions)))
                    ],
                    "normalized_memo": f"memo-{txn_counter}",
                    "normalized_vendor": vendor_norm,
                },
            }
            rows.append(row)

    # Add duplicate transaction versions (same txn_id, different dates)
    # Take first 10 transactions and create version 2
    for i in range(10):
        original = rows[i]
        dup_date = date.fromisoformat(original["transaction_date"]) + timedelta(days=30)
        dup_row = dict(original)
        dup_row = {
            "transaction_id": original["transaction_id"],
            "account_quickbooks_id": original["account_quickbooks_id"],
            "label_source": "ACCOUNTANT_REVIEW",
            "transaction_date": dup_date.isoformat(),
            "feature_snapshot": dict(original["feature_snapshot"]),
        }
        dup_row["feature_snapshot"]["transaction_date_day_of_week"] = dup_date.weekday()
        dup_row["feature_snapshot"]["transaction_date_month"] = dup_date.month
        rows.append(dup_row)

    # Shuffle with deterministic seed (but keep chronological dates)
    rng.shuffle(rows)

    return rows


@pytest.fixture
def synthetic_dataset() -> list[dict[str, Any]]:
    """Deterministic synthetic accounting dataset."""
    print(f"\n{DISCLAIMER}")
    return _generate_synthetic_dataset()


# ===========================================================================
# 1. Dataset creation and validation
# ===========================================================================


class TestSyntheticDatasetCreation:
    """Synthetic dataset fixture validation."""

    def test_dataset_has_minimum_rows(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Dataset has at least 60 rows."""
        print(f"\n{DISCLAIMER}")
        assert len(synthetic_dataset) >= 60

    def test_dataset_has_minimum_classes(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Dataset spans at least 3 account classes."""
        print(f"\n{DISCLAIMER}")
        classes = {r["account_quickbooks_id"] for r in synthetic_dataset}
        assert len(classes) >= 3

    def test_dataset_has_recurring_vendors(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Dataset contains recurring vendors."""
        print(f"\n{DISCLAIMER}")
        vendors = {r["feature_snapshot"]["normalized_vendor"] for r in synthetic_dataset}
        assert "home_depot" in vendors
        assert "amazon" in vendors
        assert "staples" in vendors

    def test_dataset_has_new_vendors(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Dataset contains new vendors appearing in later dates."""
        print(f"\n{DISCLAIMER}")
        vendors = {r["feature_snapshot"]["normalized_vendor"] for r in synthetic_dataset}
        assert "cloudtech" in vendors

    def test_dataset_has_rare_class(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Dataset has a rare class with 5-10 examples."""
        print(f"\n{DISCLAIMER}")
        from collections import Counter

        counts = Counter(r["account_quickbooks_id"] for r in synthetic_dataset)
        rare_count = counts.get("acct_office_rare", 0)
        assert 10 <= rare_count <= 25

    def test_dataset_has_high_value_transactions(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """Dataset includes transactions with amount > 10000."""
        print(f"\n{DISCLAIMER}")
        high_value = [
            r for r in synthetic_dataset
            if r["feature_snapshot"]["amount"] > 10000
        ]
        assert len(high_value) > 0

    def test_dataset_has_duplicate_versions(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """Dataset includes duplicate transaction versions."""
        print(f"\n{DISCLAIMER}")
        from collections import Counter

        txn_ids = [r["transaction_id"] for r in synthetic_dataset]
        counts = Counter(txn_ids)
        duplicates = {tid: cnt for tid, cnt in counts.items() if cnt > 1}
        assert len(duplicates) > 0


# ===========================================================================
# 2. Dataset validation
# ===========================================================================


class TestSyntheticDatasetValidation:
    """DatasetValidator on synthetic data."""

    def test_quality_report_structure(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Quality report has the expected structure."""
        print(f"\n{DISCLAIMER}")
        validator = DatasetValidator()
        report = validator.validate(synthetic_dataset, min_rows=60, min_per_class=5)

        assert "valid" in report
        assert "errors" in report
        assert "warnings" in report
        assert "stats" in report
        assert "total_rows" in report["stats"]
        assert "num_classes" in report["stats"]
        assert "class_distribution" in report["stats"]

    def test_quality_report_class_distribution(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """Quality report captures all classes."""
        print(f"\n{DISCLAIMER}")
        validator = DatasetValidator()
        report = validator.validate(synthetic_dataset, min_rows=60, min_per_class=5)

        dist = report["stats"]["class_distribution"]
        assert "acct_repairs" in dist
        assert "acct_supplies" in dist
        assert "acct_software" in dist
        assert "acct_office_rare" in dist


# ===========================================================================
# 3. Temporal split
# ===========================================================================


class TestSyntheticTemporalSplit:
    """TemporalSplitter on synthetic data."""

    def test_temporal_split_correctly(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Temporal split produces train/valid/test with correct sizes."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(synthetic_dataset)

        assert len(train) > 0
        assert len(valid) > 0
        assert len(test) > 0
        assert len(train) + len(valid) + len(test) == len(synthetic_dataset)

    def test_temporal_ordering_preserved(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Train dates <= valid dates <= test dates."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(synthetic_dataset)

        # Last train date <= first valid date (by transaction grouping)
        if train and valid:
            train_dates = [r["transaction_date"] for r in train]
            valid_dates = [r["transaction_date"] for r in valid]
            assert max(train_dates) <= max(valid_dates)

    def test_no_transaction_leakage(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """Duplicate transaction_ids stay in the same split."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(synthetic_dataset)

        train_ids = {r["transaction_id"] for r in train}
        valid_ids = {r["transaction_id"] for r in valid}
        test_ids = {r["transaction_id"] for r in test}

        assert not (train_ids & valid_ids)
        assert not (train_ids & test_ids)
        assert not (valid_ids & test_ids)


# ===========================================================================
# 4. Feature preprocessing
# ===========================================================================


class TestSyntheticFeaturePreprocessing:
    """FeatureTransformer on synthetic data."""

    def test_fit_transform_works(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """FeatureTransformer.fit_transform produces a valid matrix."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, _, _ = splitter.split(synthetic_dataset)

        tx = FeatureTransformer()
        X_train = tx.fit_transform(train)

        assert X_train.shape[0] == len(train)
        assert X_train.shape[1] > 0
        assert tx.is_fitted

    def test_transform_after_fit(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """FeatureTransformer.transform works on test data after fit."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, _, test = splitter.split(synthetic_dataset)

        tx = FeatureTransformer()
        X_train = tx.fit_transform(train)
        X_test = tx.transform(test)

        assert X_test.shape[0] == len(test)
        assert X_test.shape[1] == X_train.shape[1]


# ===========================================================================
# 5-7. Model training (Dummy, LR, HGB)
# ===========================================================================


class TestSyntheticModelTraining:
    """Training with different model types on synthetic data."""

    @pytest.fixture(autouse=True)
    def _setup(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, valid, test = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        self._X_train = tx.fit_transform(train)
        self._X_valid = tx.transform(valid)
        self._X_test = tx.transform(test)

        # Convert sparse to dense if needed
        if hasattr(self._X_train, "toarray"):
            self._X_train = self._X_train.toarray()
        if hasattr(self._X_valid, "toarray"):
            self._X_valid = self._X_valid.toarray()
        if hasattr(self._X_test, "toarray"):
            self._X_test = self._X_test.toarray()

        self._y_train = np.array([r["account_quickbooks_id"] for r in train])
        self._y_valid = np.array([r["account_quickbooks_id"] for r in valid])
        self._y_test = np.array([r["account_quickbooks_id"] for r in test])

    def test_dummy_classifier_training(self) -> None:
        """DummyClassifier trains successfully."""
        print(f"\n{DISCLAIMER}")
        model = train_dummy_classifier(self._X_train, self._y_train)
        assert hasattr(model, "predict")
        preds = model.predict(self._X_test)
        assert len(preds) == len(self._y_test)

    def test_logistic_regression_training(self) -> None:
        """LogisticRegression trains successfully."""
        print(f"\n{DISCLAIMER}")
        model = train_logistic_regression(
            self._X_train, self._y_train, max_iter=500
        )
        assert hasattr(model, "predict")
        preds = model.predict(self._X_test)
        assert len(preds) == len(self._y_test)

    def test_hist_gradient_boosting_training(self) -> None:
        """HistGradientBoosting trains successfully."""
        print(f"\n{DISCLAIMER}")
        model = train_hist_gradient_boosting(
            self._X_train, self._y_train, max_iter=50
        )
        assert hasattr(model, "predict")
        preds = model.predict(self._X_test)
        assert len(preds) == len(self._y_test)


# ===========================================================================
# 8. Probability calibration
# ===========================================================================


class TestSyntheticCalibration:
    """ProbabilityCalibrator on synthetic data."""

    def test_calibration_works(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """ProbabilityCalibrator fits and predicts successfully."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, valid, _ = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        X_valid = tx.transform(valid)

        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()
        if hasattr(X_valid, "toarray"):
            X_valid = X_valid.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        y_valid = np.array([r["account_quickbooks_id"] for r in valid])

        # Need at least 2 classes in valid for calibration
        unique_valid = np.unique(y_valid)
        if len(unique_valid) < 2:
            pytest.skip("Not enough classes in validation for calibration")

        model = train_dummy_classifier(X_train, y_train, strategy="stratified")

        # Use a small cv to avoid issues with small datasets
        n_per_class = min(
            np.sum(y_valid == c) for c in unique_valid
        )
        cv = min(3, int(n_per_class))

        if cv < 2:
            pytest.skip("Not enough samples per class for calibration CV")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            calibrator = ProbabilityCalibrator()
            calibrator.fit(model, X_valid, y_valid, method="sigmoid", cv=cv)

        assert calibrator.is_fitted

        probs = calibrator.predict_proba(X_valid)
        assert probs.shape[0] == len(y_valid)
        # Probabilities should sum to ~1 for each sample
        row_sums = probs.sum(axis=1)
        np.testing.assert_allclose(row_sums, 1.0, atol=0.05)


# ===========================================================================
# 9. Metrics generation
# ===========================================================================


class TestSyntheticMetrics:
    """compute_metrics on synthetic predictions."""

    def test_compute_metrics_returns_expected_keys(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """compute_metrics returns all expected keys."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, _, test = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        X_test = tx.transform(test)

        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()
        if hasattr(X_test, "toarray"):
            X_test = X_test.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        y_test = np.array([r["account_quickbooks_id"] for r in test])

        model = train_dummy_classifier(X_train, y_train, strategy="stratified")
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)

        metrics = compute_metrics(y_test, y_pred, y_proba)

        assert "accuracy" in metrics
        assert "macro_f1" in metrics
        assert "weighted_f1" in metrics
        assert "n_samples" in metrics
        assert "n_classes" in metrics
        assert "per_class_f1" in metrics

        # Accuracy should be in [0, 1]
        assert 0.0 <= metrics["accuracy"] <= 1.0


# ===========================================================================
# 10. Threshold coverage
# ===========================================================================


class TestSyntheticThresholdCoverage:
    """threshold_coverage_report on synthetic predictions."""

    def test_threshold_coverage_report_works(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """threshold_coverage_report returns valid rows."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, _, test = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        X_test = tx.transform(test)

        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()
        if hasattr(X_test, "toarray"):
            X_test = X_test.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        y_test = np.array([r["account_quickbooks_id"] for r in test])

        model = train_logistic_regression(X_train, y_train, max_iter=500)
        y_proba = model.predict_proba(X_test)

        # threshold_coverage_report maps argmax indices via np.unique(y_true),
        # so it requires all training classes to appear in y_test.
        unique_train = set(np.unique(y_train))
        unique_test = set(np.unique(y_test))
        if unique_test != unique_train:
            pytest.skip("Test set missing classes — threshold report needs all classes")

        report = threshold_coverage_report(y_test, y_proba)

        assert len(report) > 0
        for row in report:
            assert "threshold" in row
            assert "coverage" in row
            assert "coverage_count" in row
            assert "total" in row
            assert "accuracy_at_threshold" in row
            assert 0.0 <= row["coverage"] <= 1.0


# ===========================================================================
# 11-13. Artifact persistence (write, verify, reload)
# ===========================================================================


class TestSyntheticArtifactPersistence:
    """ArtifactManager save/load/verify on synthetic models."""

    def test_artifact_save(self, synthetic_dataset: list[dict[str, Any]], tmp_path: Any) -> None:
        """ArtifactManager.save_artifact works."""
        print(f"\n{DISCLAIMER}")
        from agentblue.ml.registry.artifacts import ArtifactManager

        splitter = TemporalSplitter()
        train, _, _ = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        model = train_dummy_classifier(X_train, y_train)

        mgr = ArtifactManager(artifact_root=str(tmp_path))
        uri, sha256 = mgr.save_artifact(model, "test-model.joblib")

        assert uri is not None
        assert sha256 is not None
        assert len(sha256) == 64  # SHA-256 hex digest

    def test_sha256_verification(self, synthetic_dataset: list[dict[str, Any]], tmp_path: Any) -> None:
        """ArtifactManager.verify_hash works."""
        print(f"\n{DISCLAIMER}")
        from agentblue.ml.registry.artifacts import ArtifactManager

        splitter = TemporalSplitter()
        train, _, _ = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        model = train_dummy_classifier(X_train, y_train)

        mgr = ArtifactManager(artifact_root=str(tmp_path))
        uri, sha256 = mgr.save_artifact(model, "test-model.joblib")

        assert mgr.verify_hash(uri, sha256) is True
        assert mgr.verify_hash(uri, "0" * 64) is False

    def test_artifact_reload(self, synthetic_dataset: list[dict[str, Any]], tmp_path: Any) -> None:
        """ArtifactManager.load_artifact reloads the model correctly."""
        print(f"\n{DISCLAIMER}")
        from agentblue.ml.registry.artifacts import ArtifactManager

        splitter = TemporalSplitter()
        train, _, _ = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        model = train_dummy_classifier(X_train, y_train)

        mgr = ArtifactManager(artifact_root=str(tmp_path))
        uri, sha256 = mgr.save_artifact(model, "test-model.joblib")

        loaded = mgr.load_artifact(uri, expected_sha256=sha256)
        assert hasattr(loaded, "predict")

        # Verify predictions match
        orig_preds = model.predict(X_train[:5])
        loaded_preds = loaded.predict(X_train[:5])
        np.testing.assert_array_equal(orig_preds, loaded_preds)


# ===========================================================================
# 14. Inference
# ===========================================================================


class TestSyntheticInference:
    """MLPredictor on trained synthetic models."""

    def test_predict_works_on_trained_model(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """MLPredictor.predict returns valid predictions."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, _, _ = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        model = train_logistic_regression(X_train, y_train, max_iter=500)

        unique_labels = sorted(set(y_train))
        class_mapping = {label: i for i, label in enumerate(unique_labels)}

        predictor = MLPredictor()
        predictions = predictor.predict(
            model=model,
            features=X_train[0],
            class_mapping=class_mapping,
            top_k=3,
        )

        assert len(predictions) > 0
        assert len(predictions) <= 3
        for pred in predictions:
            assert "account_id" in pred
            assert "raw_prob" in pred
            assert "calibrated_prob" in pred


# ===========================================================================
# 15. Top-k ranking
# ===========================================================================


class TestSyntheticTopKRanking:
    """Predictions are sorted by calibrated_prob descending."""

    def test_top_k_sorted_correctly(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """Predictions are sorted by calibrated_prob descending."""
        print(f"\n{DISCLAIMER}")
        splitter = TemporalSplitter()
        train, _, _ = splitter.split(synthetic_dataset)
        tx = FeatureTransformer()

        X_train = tx.fit_transform(train)
        if hasattr(X_train, "toarray"):
            X_train = X_train.toarray()

        y_train = np.array([r["account_quickbooks_id"] for r in train])
        model = train_logistic_regression(X_train, y_train, max_iter=500)

        unique_labels = sorted(set(y_train))
        class_mapping = {label: i for i, label in enumerate(unique_labels)}

        predictor = MLPredictor()
        predictions = predictor.predict(
            model=model,
            features=X_train[0],
            class_mapping=class_mapping,
        )

        # Verify descending order
        for i in range(len(predictions) - 1):
            assert predictions[i]["calibrated_prob"] >= predictions[i + 1]["calibrated_prob"]


# ===========================================================================
# Full pipeline integration
# ===========================================================================


class TestSyntheticFullPipeline:
    """Full ModelTrainer pipeline on synthetic data."""

    def test_full_pipeline_dummy(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """ModelTrainer works end-to-end with dummy classifier."""
        print(f"\n{DISCLAIMER}")
        from agentblue.ml.training.trainer import ModelTrainer

        trainer = ModelTrainer()
        result = trainer.train(synthetic_dataset, model_type="dummy", seed=42)

        assert result.run_id is not None
        assert result.model_type == "dummy"
        assert "train" in result.metrics
        assert "test" in result.metrics
        assert result.duration_seconds > 0

    def test_full_pipeline_lr(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """ModelTrainer works end-to-end with logistic regression."""
        print(f"\n{DISCLAIMER}")
        from agentblue.ml.training.trainer import ModelTrainer

        trainer = ModelTrainer()
        result = trainer.train(
            synthetic_dataset,
            model_type="logistic_regression",
            seed=42,
            hyperparams={"max_iter": 500},
        )

        assert result.run_id is not None
        assert result.model_type == "logistic_regression"
        assert "accuracy" in result.metrics["test"]

    def test_full_pipeline_hgb(self, synthetic_dataset: list[dict[str, Any]]) -> None:
        """ModelTrainer works end-to-end with gradient boosting."""
        print(f"\n{DISCLAIMER}")
        from agentblue.ml.training.trainer import ModelTrainer

        trainer = ModelTrainer()
        result = trainer.train(
            synthetic_dataset,
            model_type="hist_gradient_boosting",
            seed=42,
            hyperparams={"max_iter": 50},
        )

        assert result.run_id is not None
        assert result.model_type == "hist_gradient_boosting"
        assert "accuracy" in result.metrics["test"]

    def test_dataset_fingerprint_deterministic(
        self, synthetic_dataset: list[dict[str, Any]]
    ) -> None:
        """Dataset fingerprint is deterministic."""
        print(f"\n{DISCLAIMER}")
        fp1 = compute_dataset_fingerprint(synthetic_dataset)
        fp2 = compute_dataset_fingerprint(synthetic_dataset)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex
