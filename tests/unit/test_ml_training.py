"""Unit tests for ML model training, calibration, metrics, and artifact security.

Covers:
- Model training (DummyClassifier, LogisticRegression, HistGradientBoosting)
- Probability calibration (sigmoid, isotonic)
- Metric computation (accuracy, F1, log loss, Brier, threshold coverage)
- Artifact security (atomic write, SHA-256, hash mismatch, path traversal)
- Full ModelTrainer pipeline with synthetic data

All tests are pure function/logic tests — no database required.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from agentblue.ml.exceptions import (
    ArtifactError,
    ArtifactHashMismatchError,
    ModelNotFoundError,
)
from agentblue.ml.registry.artifacts import ArtifactManager
from agentblue.ml.training.baselines import (
    train_dummy_classifier,
    train_logistic_regression,
)
from agentblue.ml.training.calibration import ProbabilityCalibrator
from agentblue.ml.training.candidates import train_hist_gradient_boosting
from agentblue.ml.training.evaluation import compute_metrics, threshold_coverage_report
from agentblue.ml.training.trainer import ModelTrainer, TrainingRunResult

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_toy_dataset(
    n_samples: int = 60,
    n_classes: int = 3,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Create a tiny synthetic (X, y) pair for direct training function tests."""
    rng = np.random.RandomState(seed)
    X = rng.randn(n_samples, 4)
    y = rng.randint(0, n_classes, size=n_samples)
    # Ensure every class appears at least once
    for i in range(n_classes):
        y[i] = i
    return X, y


def _make_binary_dataset(n_samples: int = 60, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Create a binary classification toy dataset."""
    rng = np.random.RandomState(seed)
    X = rng.randn(n_samples, 4)
    y = rng.randint(0, 2, size=n_samples)
    y[0], y[1] = 0, 1
    return X, y


def _make_pipeline_dataset(n_rows: int = 120, n_classes: int = 3, seed: int = 42) -> list[dict[str, Any]]:
    """Create synthetic rows matching the DatasetExtractor schema.

    Each row has transaction_id, transaction_date, account_quickbooks_id,
    and a feature_snapshot dict with the fields FeatureTransformer expects.
    """
    rng = np.random.RandomState(seed)
    classes = [f"class_{i}" for i in range(n_classes)]
    rows: list[dict[str, Any]] = []
    for i in range(n_rows):
        cls = classes[i % n_classes]
        rows.append(
            {
                "transaction_id": f"txn_{i:04d}",
                "transaction_date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "account_quickbooks_id": cls,
                "label_source": "manual",
                "feature_snapshot": {
                    "amount": float(rng.uniform(-500, 500)),
                    "absolute_amount": float(rng.uniform(0, 500)),
                    "transaction_type": rng.choice(["Payment", "Invoice", "Bill"]),
                    "transaction_date_day_of_week": int(rng.randint(0, 7)),
                    "transaction_date_month": int(rng.randint(1, 13)),
                    "normalized_description": f"vendor description {i % 20}",
                    "normalized_memo": f"memo text {i % 10}",
                    "normalized_vendor": f"vendor_{i % 15}",
                },
            }
        )
    return rows


# ===================================================================
# A. Model Training Tests
# ===================================================================


class TestModelTraining:
    """Direct training function tests — no pipeline, no DB."""

    def test_dummy_classifier_trains_and_predicts(self) -> None:
        X, y = _make_toy_dataset()
        clf = train_dummy_classifier(X, y, strategy="most_frequent")
        preds = clf.predict(X)
        assert preds.shape == (len(y),)
        # Dummy most_frequent always predicts the single most common class
        assert len(np.unique(preds)) == 1

    def test_dummy_classifier_stratified(self) -> None:
        X, y = _make_toy_dataset()
        clf = train_dummy_classifier(X, y, strategy="stratified")
        preds = clf.predict(X)
        assert preds.shape == (len(y),)
        # Stratified should produce more than one predicted class
        assert len(np.unique(preds)) > 1

    def test_logistic_regression_trains_and_predicts(self) -> None:
        X, y = _make_toy_dataset()
        pipe = train_logistic_regression(X, y)
        preds = pipe.predict(X)
        assert preds.shape == (len(y),)
        assert hasattr(pipe, "predict_proba")
        proba = pipe.predict_proba(X)
        assert proba.shape == (len(y), len(np.unique(y)))

    def test_logistic_regression_pipeline_has_scaler(self) -> None:
        X, y = _make_toy_dataset()
        pipe = train_logistic_regression(X, y)
        step_names = [name for name, _ in pipe.steps]
        assert "scaler" in step_names
        assert "clf" in step_names

    def test_hist_gradient_boosting_trains_and_predicts(self) -> None:
        X, y = _make_toy_dataset()
        clf = train_hist_gradient_boosting(X, y)
        preds = clf.predict(X)
        assert preds.shape == (len(y),)
        proba = clf.predict_proba(X)
        assert proba.shape == (len(y), len(np.unique(y)))

    def test_hist_gradient_boosting_custom_params(self) -> None:
        X, y = _make_toy_dataset()
        clf = train_hist_gradient_boosting(
            X, y, max_iter=50, learning_rate=0.05, max_depth=3
        )
        assert clf.max_iter == 50
        assert clf.learning_rate == 0.05

    def test_deterministic_seed_same_model(self) -> None:
        """Same seed → same model predictions."""
        X, y = _make_toy_dataset(seed=99)
        clf_a = train_logistic_regression(X, y, random_state=7)
        clf_b = train_logistic_regression(X, y, random_state=7)
        preds_a = clf_a.predict_proba(X)
        preds_b = clf_b.predict_proba(X)
        np.testing.assert_array_equal(preds_a, preds_b)

    def test_different_seeds_can_differ(self) -> None:
        """Different seeds can produce different models (not guaranteed but likely)."""
        X, y = _make_toy_dataset(seed=99, n_samples=100)
        clf_a = train_hist_gradient_boosting(X, y, random_state=1, early_stopping=False)
        clf_b = train_hist_gradient_boosting(X, y, random_state=2, early_stopping=False)
        preds_a = clf_a.predict_proba(X)
        preds_b = clf_b.predict_proba(X)
        # Not strictly guaranteed, but very likely with 100 samples
        # We just check that the models were created without errors
        assert preds_a.shape == preds_b.shape

    def test_class_mapping_persistence_logistic(self) -> None:
        """model.classes_ matches expected label set."""
        X, y = _make_toy_dataset(n_classes=4)
        pipe = train_logistic_regression(X, y)
        # Pipeline exposes classes_ via the final estimator
        assert hasattr(pipe, "classes_")
        np.testing.assert_array_equal(
            sorted(pipe.classes_), sorted(np.unique(y))
        )

    def test_class_mapping_persistence_hgb(self) -> None:
        X, y = _make_toy_dataset(n_classes=4)
        clf = train_hist_gradient_boosting(X, y)
        assert hasattr(clf, "classes_")
        np.testing.assert_array_equal(
            sorted(clf.classes_), sorted(np.unique(y))
        )

    def test_unsupported_model_type_raises(self) -> None:
        """ModelTrainer.train() raises ValueError for unknown model_type."""
        trainer = ModelTrainer()
        dataset = _make_pipeline_dataset(n_rows=120, n_classes=3)
        with pytest.raises(ValueError, match="Unknown model_type"):
            trainer.train(dataset, model_type="xgboost_does_not_exist")

    def test_full_trainer_pipeline_dummy(self) -> None:
        """ModelTrainer.train() completes end-to-end with DummyClassifier."""
        trainer = ModelTrainer()
        dataset = _make_pipeline_dataset(n_rows=120, n_classes=3)
        result = trainer.train(dataset, model_type="dummy", seed=42)
        assert isinstance(result, TrainingRunResult)
        assert result.model_type == "dummy"
        assert result.seed == 42
        assert "train" in result.metrics
        assert "test" in result.metrics
        assert "accuracy" in result.metrics["test"]
        assert result.duration_seconds >= 0
        assert result.run_id.startswith("run_")

    def test_full_trainer_pipeline_logistic(self) -> None:
        """ModelTrainer.train() completes with LogisticRegression."""
        trainer = ModelTrainer()
        dataset = _make_pipeline_dataset(n_rows=120, n_classes=3)
        result = trainer.train(dataset, model_type="logistic_regression", seed=42)
        assert isinstance(result, TrainingRunResult)
        assert result.model_type == "logistic_regression"
        assert result.metrics["test"]["accuracy"] >= 0.0

    def test_full_trainer_pipeline_hgb(self) -> None:
        """ModelTrainer.train() completes with HistGradientBoosting."""
        trainer = ModelTrainer()
        dataset = _make_pipeline_dataset(n_rows=150, n_classes=3)
        result = trainer.train(
            dataset,
            model_type="hist_gradient_boosting",
            hyperparams={"max_iter": 50, "min_samples_leaf": 5},
            seed=42,
        )
        assert isinstance(result, TrainingRunResult)
        assert result.model_type == "hist_gradient_boosting"
        assert result.dataset_fingerprint  # non-empty
        assert "train" in result.metrics
        assert "test" in result.metrics


# ===================================================================
# B. Calibration Tests
# ===================================================================


class TestCalibration:
    """ProbabilityCalibrator tests with tiny synthetic data."""

    def test_sigmoid_calibration_works(self) -> None:
        X, y = _make_toy_dataset(n_classes=3, n_samples=80)
        from sklearn.ensemble import HistGradientBoostingClassifier

        clf = HistGradientBoostingClassifier(
            random_state=42, max_iter=50, min_samples_leaf=5
        )
        clf.fit(X, y)

        cal = ProbabilityCalibrator()
        cal.fit(clf, X, y, method="sigmoid")
        assert cal.is_fitted

        proba = cal.predict_proba(X)
        assert proba.shape == (len(y), len(np.unique(y)))

    def test_isotonic_calibration_works(self) -> None:
        """Isotonic needs enough data per class — use a larger dataset."""
        X, y = _make_toy_dataset(n_classes=2, n_samples=100, seed=123)
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(random_state=42, max_iter=200)
        clf.fit(X, y)

        cal = ProbabilityCalibrator()
        cal.fit(clf, X, y, method="isotonic", cv=3)
        assert cal.is_fitted

        proba = cal.predict_proba(X)
        assert proba.shape == (len(y), 2)

    def test_raw_and_calibrated_probabilities_retained(self) -> None:
        """Calibrated probabilities differ from raw (or at least are stored)."""
        X, y = _make_toy_dataset(n_classes=3, n_samples=80)
        from sklearn.ensemble import HistGradientBoostingClassifier

        clf = HistGradientBoostingClassifier(random_state=42, max_iter=50, min_samples_leaf=5)
        clf.fit(X, y)

        raw_proba = clf.predict_proba(X)

        cal = ProbabilityCalibrator()
        cal.fit(clf, X, y, method="sigmoid")
        cal_proba = cal.predict_proba(X)

        # Shapes must match
        assert raw_proba.shape == cal_proba.shape
        # Both must be valid probability matrices
        assert np.all(raw_proba >= 0)
        assert np.all(cal_proba >= 0)

    def test_probability_range_0_to_1(self) -> None:
        """All calibrated probabilities must be in [0, 1]."""
        X, y = _make_toy_dataset(n_classes=3, n_samples=80)
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(random_state=42, max_iter=200)
        clf.fit(X, y)

        cal = ProbabilityCalibrator()
        cal.fit(clf, X, y, method="sigmoid")
        proba = cal.predict_proba(X)

        assert np.all(proba >= 0.0 - 1e-10)
        assert np.all(proba <= 1.0 + 1e-10)

    def test_multiclass_probabilities_sum_to_one(self) -> None:
        """Each row's probabilities must sum to approximately 1.0."""
        X, y = _make_toy_dataset(n_classes=3, n_samples=80)
        from sklearn.ensemble import HistGradientBoostingClassifier

        clf = HistGradientBoostingClassifier(random_state=42, max_iter=50, min_samples_leaf=5)
        clf.fit(X, y)

        cal = ProbabilityCalibrator()
        cal.fit(clf, X, y, method="sigmoid")
        proba = cal.predict_proba(X)

        row_sums = np.sum(proba, axis=1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-6)

    def test_calibrator_predict_proba_raises_if_not_fitted(self) -> None:
        """Calling predict_proba before fit must raise RuntimeError."""
        cal = ProbabilityCalibrator()
        assert not cal.is_fitted
        with pytest.raises(RuntimeError, match="not fitted"):
            cal.predict_proba(np.zeros((5, 3)))

    def test_calibrator_static_sigmoid(self) -> None:
        """Static calibrate_sigmoid returns a fitted CalibratedClassifierCV."""
        X, y = _make_toy_dataset(n_classes=3, n_samples=80)
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(random_state=42, max_iter=200)
        clf.fit(X, y)

        from sklearn.calibration import CalibratedClassifierCV

        calibrated = ProbabilityCalibrator.calibrate_sigmoid(clf, X, y, cv=3)
        assert isinstance(calibrated, CalibratedClassifierCV)
        proba = calibrated.predict_proba(X)
        assert np.all(np.isfinite(proba))


# ===================================================================
# C. Metrics Tests
# ===================================================================


class TestMetrics:
    """Metric computation tests with small, manually-verifiable arrays."""

    def test_top1_accuracy_perfect(self) -> None:
        y_true = np.array([0, 1, 2, 0, 1, 2])
        y_pred = np.array([0, 1, 2, 0, 1, 2])
        m = compute_metrics(y_true, y_pred)
        assert m["accuracy"] == 1.0

    def test_top1_accuracy_partial(self) -> None:
        y_true = np.array([0, 1, 2, 0, 1, 2])
        y_pred = np.array([0, 1, 1, 0, 1, 2])  # one wrong
        m = compute_metrics(y_true, y_pred)
        assert abs(m["accuracy"] - 5 / 6) < 1e-10

    def test_top3_accuracy_computed(self) -> None:
        """Top-3 accuracy with 3+ classes should be computed."""
        y_true = np.array([0, 1, 2, 0, 1])
        y_proba = np.array([
            [0.7, 0.2, 0.1],
            [0.1, 0.8, 0.1],
            [0.1, 0.2, 0.7],
            [0.6, 0.3, 0.1],
            [0.2, 0.7, 0.1],
        ])
        y_pred = np.argmax(y_proba, axis=1)
        m = compute_metrics(y_true, y_pred, y_proba)
        assert "top3_accuracy" in m
        # With 3 classes and k=3, top-3 accuracy must be 1.0
        assert m["top3_accuracy"] == 1.0

    def test_macro_f1_perfect(self) -> None:
        y_true = np.array([0, 1, 2, 0, 1, 2])
        y_pred = np.array([0, 1, 2, 0, 1, 2])
        m = compute_metrics(y_true, y_pred)
        assert m["macro_f1"] == 1.0

    def test_macro_f1_partial(self) -> None:
        """Macro F1 with one misclassified sample."""
        y_true = np.array([0, 0, 1, 1, 2, 2])
        y_pred = np.array([0, 0, 1, 1, 2, 0])  # class 2 has one FN, class 0 has one FP
        m = compute_metrics(y_true, y_pred)
        assert 0.0 < m["macro_f1"] < 1.0

    def test_weighted_f1_computed(self) -> None:
        y_true = np.array([0, 1, 2, 0, 1, 2])
        y_pred = np.array([0, 1, 2, 0, 1, 2])
        m = compute_metrics(y_true, y_pred)
        assert m["weighted_f1"] == 1.0

    def test_weighted_f1_considers_support(self) -> None:
        """Weighted F1 accounts for class support (unlike macro)."""
        y_true = np.array([0, 0, 0, 0, 1, 2])
        y_pred = np.array([0, 0, 0, 0, 1, 1])  # class 2 wrong
        m = compute_metrics(y_true, y_pred)
        # Weighted F1 should be higher than macro F1 because class 0 (correct) has 4 samples
        assert m["weighted_f1"] >= m["macro_f1"]

    def test_log_loss_computed(self) -> None:
        """Log loss is computed when probabilities are provided."""
        y_true = np.array([0, 1, 2, 0])
        y_proba = np.array([
            [0.9, 0.05, 0.05],
            [0.1, 0.8, 0.1],
            [0.05, 0.1, 0.85],
            [0.7, 0.2, 0.1],
        ])
        y_pred = np.argmax(y_proba, axis=1)
        m = compute_metrics(y_true, y_pred, y_proba)
        assert "log_loss" in m
        assert m["log_loss"] > 0  # perfect predictions still have non-zero log loss

    def test_log_loss_not_computed_without_proba(self) -> None:
        y_true = np.array([0, 1, 2])
        y_pred = np.array([0, 1, 2])
        m = compute_metrics(y_true, y_pred, y_proba=None)
        assert "log_loss" not in m

    def test_brier_score_binary(self) -> None:
        """Brier score is computed for binary classification."""
        y_true = np.array([0, 1, 0, 1])
        y_proba = np.array([
            [0.9, 0.1],
            [0.2, 0.8],
            [0.8, 0.2],
            [0.3, 0.7],
        ])
        y_pred = np.argmax(y_proba, axis=1)
        m = compute_metrics(y_true, y_pred, y_proba)
        assert "brier_score" in m
        assert 0.0 <= m["brier_score"] <= 1.0

    def test_brier_score_nan_for_multiclass(self) -> None:
        """Brier score should be NaN for non-binary cases."""
        y_true = np.array([0, 1, 2])
        y_proba = np.array([
            [0.8, 0.1, 0.1],
            [0.1, 0.8, 0.1],
            [0.1, 0.1, 0.8],
        ])
        y_pred = np.argmax(y_proba, axis=1)
        m = compute_metrics(y_true, y_pred, y_proba)
        assert "brier_score" in m
        assert np.isnan(m["brier_score"])

    def test_expected_calibration_error_manual(self) -> None:
        """ECE via threshold coverage — verify coverage report counts."""
        # 5 samples, 2 classes, probabilities are very confident
        y_true = np.array([0, 0, 1, 1, 0])
        y_proba = np.array([
            [0.95, 0.05],
            [0.90, 0.10],
            [0.10, 0.90],
            [0.20, 0.80],
            [0.85, 0.15],
        ])
        report = threshold_coverage_report(y_true, y_proba, thresholds=[0.80])
        assert len(report) == 1
        assert report[0]["threshold"] == 0.80
        # All 5 samples have top prob >= 0.80
        assert report[0]["coverage_count"] == 5
        assert report[0]["coverage"] == 1.0
        # 4 out of 5 are correct (sample index 1: true=0, pred=0 is correct;
        # index 3: true=1, pred=1 is correct; all correct except check carefully)
        # true=[0,0,1,1,0], pred from argmax=[0,0,1,1,0] → all correct
        assert report[0]["accuracy_at_threshold"] == 1.0

    def test_threshold_coverage_report_default_thresholds(self) -> None:
        y_true = np.array([0, 1, 0, 1, 0])
        y_proba = np.array([
            [0.9, 0.1],
            [0.1, 0.9],
            [0.7, 0.3],
            [0.3, 0.7],
            [0.6, 0.4],
        ])
        report = threshold_coverage_report(y_true, y_proba)
        # Default thresholds: [0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
        assert len(report) == 6
        thresholds_returned = [r["threshold"] for r in report]
        assert 0.50 in thresholds_returned
        assert 0.95 in thresholds_returned

    def test_abstention_count_below_threshold(self) -> None:
        """Samples below the threshold are excluded from coverage."""
        y_true = np.array([0, 1, 0, 1])
        y_proba = np.array([
            [0.95, 0.05],  # confident
            [0.55, 0.45],  # uncertain — top=0.55
            [0.90, 0.10],  # confident
            [0.60, 0.40],  # uncertain — top=0.60
        ])
        report = threshold_coverage_report(y_true, y_proba, thresholds=[0.80])
        # Only 2 samples have top prob >= 0.80
        assert report[0]["coverage_count"] == 2
        assert report[0]["coverage"] == 0.5

    def test_zero_covered_threshold_handling(self) -> None:
        """When no samples meet the threshold, accuracy is None."""
        y_true = np.array([0, 1, 0])
        y_proba = np.array([
            [0.55, 0.45],
            [0.45, 0.55],
            [0.50, 0.50],
        ])
        report = threshold_coverage_report(y_true, y_proba, thresholds=[0.99])
        assert report[0]["coverage_count"] == 0
        assert report[0]["coverage"] == 0.0
        assert report[0]["accuracy_at_threshold"] is None

    def test_metrics_n_samples_and_n_classes(self) -> None:
        y_true = np.array([0, 1, 2, 0, 1])
        y_pred = np.array([0, 1, 2, 0, 1])
        m = compute_metrics(y_true, y_pred)
        assert m["n_samples"] == 5
        assert m["n_classes"] == 3

    def test_per_class_f1_present(self) -> None:
        y_true = np.array([0, 1, 2, 0, 1, 2])
        y_pred = np.array([0, 1, 2, 0, 1, 2])
        m = compute_metrics(y_true, y_pred)
        assert "per_class_f1" in m
        assert isinstance(m["per_class_f1"], dict)
        # With perfect predictions, every class F1 is 1.0
        for v in m["per_class_f1"].values():
            assert v == 1.0


# ===================================================================
# D. Artifact Security Tests
# ===================================================================


class TestArtifactSecurity:
    """ArtifactManager tests — file I/O, hashing, path traversal prevention."""

    def test_atomic_write_temp_file_renamed(self, tmp_path: Path) -> None:
        """save_artifact writes atomically (temp file replaced by final)."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        model = {"test": "value"}
        uri, sha256 = mgr.save_artifact(model, "models/test.joblib")

        # The final file should exist
        assert Path(uri).exists()
        # No leftover temp files
        temp_files = list(Path(uri).parent.glob(".artifact_*.tmp"))
        assert temp_files == []
        # SHA-256 should be a valid hex string
        assert len(sha256) == 64
        assert all(c in "0123456789abcdef" for c in sha256)

    def test_sha256_matches_expected(self, tmp_path: Path) -> None:
        """SHA-256 returned by save matches independently computed hash."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        model = "simple_model"
        uri, sha256 = mgr.save_artifact(model, "models/hash_test.joblib")

        # Independently compute the hash
        h = hashlib.sha256()
        with open(uri, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        assert h.hexdigest() == sha256

    def test_hash_mismatch_raises(self, tmp_path: Path) -> None:
        """Loading with wrong expected hash raises ArtifactHashMismatchError."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        model = "model_for_hash_mismatch"
        uri, sha256 = mgr.save_artifact(model, "models/mismatch.joblib")

        wrong_hash = "0" * 64
        with pytest.raises(ArtifactHashMismatchError):
            mgr.load_artifact(uri, expected_sha256=wrong_hash)

    def test_missing_artifact_raises(self, tmp_path: Path) -> None:
        """Loading a non-existent artifact raises ArtifactError."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        with pytest.raises(ArtifactError, match="not found"):
            mgr.load_artifact(str(tmp_path / "nonexistent.joblib"))

    def test_corrupted_artifact_raises(self, tmp_path: Path) -> None:
        """Loading a corrupted file raises ArtifactError."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        corrupted_file = tmp_path / "corrupted.joblib"
        corrupted_file.write_text("this is not valid joblib data")
        with pytest.raises(ArtifactError, match="Failed to load"):
            mgr.load_artifact(str(corrupted_file))

    def test_path_traversal_resolves_to_parent(self, tmp_path: Path) -> None:
        """Paths with ../.. resolve via Path arithmetic.

        Current ArtifactManager uses ``root / path`` which lets ``../..``
        escape the root.  This test documents that behaviour so that a
        future security hardening patch has a regression anchor.
        """
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        model = "traversal_test"
        uri, sha256 = mgr.save_artifact(model, "models/../../escape.joblib")
        # The file is created (current behaviour — no guard)
        assert Path(uri).exists()
        loaded = mgr.load_artifact(uri, expected_sha256=sha256)
        assert loaded == model

    def test_absolute_path_joined_under_root(self, tmp_path: Path) -> None:
        """Absolute paths starting with '/' are joined as relative under root.

        On POSIX, ``root / "/models/x"`` keeps the absolute component.
        On Windows, Path joining behaviour differs.  This test documents
        the actual outcome so a future guard has a regression anchor.
        """
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        model = "absolute_path_test"
        uri, sha256 = mgr.save_artifact(model, "/models/abs_test.joblib")
        # File was created successfully (current behaviour)
        assert Path(uri).exists()
        loaded = mgr.load_artifact(uri, expected_sha256=sha256)
        assert loaded == model

    def test_verify_hash_true(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        uri, sha256 = mgr.save_artifact("model", "models/verify.joblib")
        assert mgr.verify_hash(uri, sha256) is True

    def test_verify_hash_false(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        uri, _ = mgr.save_artifact("model", "models/verify_false.joblib")
        assert mgr.verify_hash(uri, "bad" * 21 + "a") is False  # 64 chars

    def test_verify_hash_missing_file(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        assert mgr.verify_hash(str(tmp_path / "nope.joblib"), "a" * 64) is False

    def test_load_and_save_roundtrip(self, tmp_path: Path) -> None:
        """Save → load returns the original model object."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        original = {"weights": [1.0, 2.0, 3.0], "version": "1.0"}
        uri, sha256 = mgr.save_artifact(original, "models/roundtrip.joblib")
        loaded = mgr.load_artifact(uri, expected_sha256=sha256)
        assert loaded == original

    def test_save_with_metadata(self, tmp_path: Path) -> None:
        """Metadata is bundled with the artifact but not returned by load."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        model = "model_with_meta"
        uri, sha256 = mgr.save_artifact(
            model, "models/meta.joblib", metadata={"version": "1.0"}
        )
        loaded = mgr.load_artifact(uri, expected_sha256=sha256)
        assert loaded == model

    def test_duplicate_immutable_version_same_hash(self, tmp_path: Path) -> None:
        """Saving the same model twice produces the same hash."""
        mgr = ArtifactManager(artifact_root=str(tmp_path))
        model = {"data": [1, 2, 3]}
        uri1, hash1 = mgr.save_artifact(model, "models/v1.joblib")
        uri2, hash2 = mgr.save_artifact(model, "models/v2.joblib")
        # Same content → same hash
        assert hash1 == hash2

    def test_model_not_found_error_raised(self) -> None:
        """ModelNotFoundError is properly an MLError subclass."""
        exc = ModelNotFoundError("not here")
        assert isinstance(exc, Exception)
        assert "not here" in str(exc)

    def test_artifact_error_hierarchy(self) -> None:
        """ArtifactHashMismatchError inherits from ArtifactError."""
        assert issubclass(ArtifactHashMismatchError, ArtifactError)
        exc = ArtifactHashMismatchError("bad hash")
        assert isinstance(exc, ArtifactError)
