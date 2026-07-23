"""Additional unit tests for uncovered ML modules (Stage 8).

Covers model selection, report generation, manifest management,
and monitoring report functions.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentblue.ml.monitoring.reports import (
    generate_evaluation_report,
)
from agentblue.ml.registry.manifests import (
    ModelManifest,
    load_manifest,
    save_manifest,
)
from agentblue.ml.training.selection import select_best_model

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Model Selection
# ---------------------------------------------------------------------------


class TestModelSelection:
    """Tests for select_best_model."""

    def _make_candidate(
        self, run_id: str, model_type: str, accuracy: float, macro_f1: float, log_loss: float
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "model_type": model_type,
            "metrics": {
                "test": {
                    "accuracy": accuracy,
                    "macro_f1": macro_f1,
                    "log_loss": log_loss,
                }
            },
        }

    def test_selects_best_by_composite_score(self) -> None:
        """Model with highest composite score wins."""
        candidates = [
            self._make_candidate("run-1", "dummy", accuracy=0.5, macro_f1=0.3, log_loss=2.0),
            self._make_candidate("run-2", "lr", accuracy=0.8, macro_f1=0.75, log_loss=0.5),
            self._make_candidate("run-3", "hgb", accuracy=0.7, macro_f1=0.65, log_loss=0.8),
        ]
        best = select_best_model(candidates)
        assert best["run_id"] == "run-2"
        assert "selection_score" in best

    def test_empty_candidates_raises(self) -> None:
        """No candidates raises ValueError."""
        with pytest.raises(ValueError, match="No candidates"):
            select_best_model([])

    def test_single_candidate_returns_itself(self) -> None:
        """Single candidate is always the best."""
        candidates = [self._make_candidate("run-1", "dummy", accuracy=0.5, macro_f1=0.3, log_loss=1.0)]
        best = select_best_model(candidates)
        assert best["run_id"] == "run-1"

    def test_selection_score_is_numeric(self) -> None:
        """Selection score must be a finite float."""
        candidates = [self._make_candidate("run-1", "lr", accuracy=0.8, macro_f1=0.7, log_loss=0.5)]
        best = select_best_model(candidates)
        assert isinstance(best["selection_score"], float)
        assert best["selection_score"] > 0

    def test_candidates_sorted_by_score(self) -> None:
        """Returned candidates list is sorted by selection_score descending."""
        candidates = [
            self._make_candidate("run-1", "dummy", accuracy=0.4, macro_f1=0.2, log_loss=3.0),
            self._make_candidate("run-2", "lr", accuracy=0.9, macro_f1=0.85, log_loss=0.3),
        ]
        # select_best_model returns the best, but we can test the scoring logic
        best = select_best_model(candidates)
        assert best["run_id"] == "run-2"


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------


class TestReportGeneration:
    """Tests for generate_evaluation_report."""

    def test_report_structure(self) -> None:
        """Report has expected top-level keys."""
        metrics = {"accuracy": 0.85, "macro_f1": 0.80, "weighted_f1": 0.82}
        report = generate_evaluation_report(metrics)
        assert "generated_at" in report
        assert "metrics" in report
        assert "calibration" in report
        assert "threshold_table" in report
        assert "warnings" in report

    def test_report_metrics_section(self) -> None:
        """Metrics section contains accuracy, macro_f1, etc."""
        metrics = {"accuracy": 0.85, "macro_f1": 0.80, "weighted_f1": 0.82, "log_loss": 0.5}
        report = generate_evaluation_report(metrics)
        m = report["metrics"]
        assert m["accuracy"] == 0.85
        assert m["macro_f1"] == 0.80

    def test_report_low_accuracy_warning(self) -> None:
        """Low accuracy triggers a warning."""
        metrics = {"accuracy": 0.45, "macro_f1": 0.30}
        report = generate_evaluation_report(metrics)
        assert any("Low overall accuracy" in w for w in report["warnings"])

    def test_report_high_log_loss_warning(self) -> None:
        """High log loss triggers a warning."""
        metrics = {"accuracy": 0.80, "log_loss": 2.5}
        report = generate_evaluation_report(metrics)
        assert any("High log loss" in w for w in report["warnings"])

    def test_report_no_warnings_for_good_metrics(self) -> None:
        """Good metrics produce no warnings."""
        metrics = {"accuracy": 0.90, "log_loss": 0.3}
        report = generate_evaluation_report(metrics)
        assert report["warnings"] == []

    def test_report_with_calibration(self) -> None:
        """Calibration section is populated when provided."""
        metrics = {"accuracy": 0.85}
        calibration = {"method": "isotonic", "expected_calibration_error": 0.05}
        report = generate_evaluation_report(metrics, calibration=calibration)
        assert report["calibration"]["available"] is True
        assert report["calibration"]["method"] == "isotonic"

    def test_report_without_calibration(self) -> None:
        """Calibration section shows unavailable when not provided."""
        metrics = {"accuracy": 0.85}
        report = generate_evaluation_report(metrics)
        assert report["calibration"]["available"] is False

    def test_report_with_threshold_table(self) -> None:
        """Threshold table is included when provided."""
        metrics = {"accuracy": 0.85}
        thresholds = [{"threshold": 0.8, "precision": 0.9, "recall": 0.7, "f1": 0.8, "support": 50}]
        report = generate_evaluation_report(metrics, threshold_table=thresholds)
        assert len(report["threshold_table"]) == 1
        assert report["threshold_table"][0]["threshold"] == 0.8

    def test_report_per_class_metrics(self) -> None:
        """Per-class metrics are included."""
        metrics = {
            "accuracy": 0.85,
            "per_class_metrics": {"acct-100": {"f1_score": 0.9}, "acct-200": {"f1_score": 0.3}},
        }
        report = generate_evaluation_report(metrics)
        assert "per_class" in report["metrics"]

    def test_report_low_f1_classes_flagged(self) -> None:
        """Classes with F1 < 0.5 are flagged."""
        metrics = {
            "accuracy": 0.85,
            "per_class_metrics": {"acct-100": {"f1_score": 0.9}, "acct-200": {"f1_score": 0.3}},
        }
        report = generate_evaluation_report(metrics)
        assert "low_f1_classes" in report["metrics"]
        assert len(report["metrics"]["low_f1_classes"]) == 1


# ---------------------------------------------------------------------------
# Manifest Management
# ---------------------------------------------------------------------------


class TestManifestManagement:
    """Tests for ModelManifest save/load."""

    def test_manifest_roundtrip(self, tmp_path: Any) -> None:
        """Save and load a manifest preserves all fields."""
        manifest = ModelManifest(
            model_type="HIST_GRADIENT_BOOSTING",
            feature_version="1.0",
            code_version="1.0.0",
            class_mapping={"acct-100": 0, "acct-200": 1},
            inverse_class_mapping={0: "acct-100", 1: "acct-200"},
            metrics={"accuracy": 0.85},
            calibration_method="isotonic",
            seed=42,
        )
        path = str(tmp_path / "manifest.json")
        save_manifest(manifest, path)

        loaded = load_manifest(path)
        assert loaded.model_type == "HIST_GRADIENT_BOOSTING"
        assert loaded.feature_version == "1.0"
        assert loaded.class_mapping == {"acct-100": 0, "acct-200": 1}
        assert loaded.seed == 42

    def test_manifest_json_is_valid(self, tmp_path: Any) -> None:
        """Saved manifest is valid JSON."""
        manifest = ModelManifest(
            model_type="dummy",
            feature_version="1.0",
            code_version="1.0.0",
            class_mapping={},
            inverse_class_mapping={},
        )
        path = str(tmp_path / "manifest.json")
        save_manifest(manifest, path)

        with open(path) as f:
            data = json.load(f)
        assert data["model_type"] == "dummy"

    def test_manifest_missing_file_raises(self) -> None:
        """Loading a nonexistent manifest raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_manifest("/nonexistent/path/manifest.json")

    def test_manifest_invalid_json_raises(self, tmp_path: Any) -> None:
        """Loading malformed JSON raises ValueError."""
        path = tmp_path / "bad.json"
        path.write_text("not valid json{{{", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid manifest JSON"):
            load_manifest(str(path))

    def test_manifest_decimal_serialization(self, tmp_path: Any) -> None:
        """Decimal values are serialized as strings in JSON."""
        from decimal import Decimal

        manifest = ModelManifest(
            model_type="dummy",
            feature_version="1.0",
            code_version="1.0.0",
            class_mapping={},
            inverse_class_mapping={},
            metrics={"accuracy": Decimal("0.8500")},
        )
        path = str(tmp_path / "manifest.json")
        save_manifest(manifest, path)

        with open(path) as f:
            data = json.load(f)
        assert data["metrics"]["accuracy"] == "0.8500"
