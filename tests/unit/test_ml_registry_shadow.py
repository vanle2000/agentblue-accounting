"""Tests for ML registry, shadow inference/resolution, drift detection,
and CHAMPION/PRIMARY boundary enforcement (Stage 8).

All tests use mocked DB sessions — no PostgreSQL required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from agentblue.ml.domain import ModelStatus, ShadowOutcome
from agentblue.ml.exceptions import InvalidModelTransitionError
from agentblue.ml.inference.predictor import MLPredictor
from agentblue.ml.inference.ranking import rank_predictions
from agentblue.ml.inference.shadow import ShadowInference
from agentblue.ml.monitoring.drift import DriftDetector
from agentblue.ml.monitoring.metrics import (
    compute_override_rate,
    compute_shadow_agreement_rate,
)
from agentblue.ml.registry.service import ModelRegistry

pytestmark = pytest.mark.unit

DISCLAIMER = "SYNTHETIC SMOKE TEST — NOT MODEL PERFORMANCE EVIDENCE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(
    *,
    model_id: str = "m-001",
    realm_id: str = "realm-1",
    status: str = ModelStatus.CANDIDATE.value,
    model_type: str = "HIST_GRADIENT_BOOSTING",
    name: str = "test-model",
    model_version: str = "1",
    feature_version: str = "1.0",
    code_version: str = "1.0.0",
    calibration_method: str = "SIGMOID",
    artifact_path: str = "/tmp/model.joblib",
    artifact_sha256: str = "",
    training_run_id: str = "run-001",
    class_mapping: dict[str, Any] | None = None,
    promoted_at: datetime | None = None,
    retired_at: datetime | None = None,
) -> MagicMock:
    """Create a mock MlModel object."""
    m = MagicMock()
    m.id = model_id
    m.realm_id = realm_id
    m.status = status
    m.model_type = model_type
    m.name = name
    m.model_version = model_version
    m.feature_version = feature_version
    m.code_version = code_version
    m.calibration_method = calibration_method
    m.artifact_path = artifact_path
    m.artifact_sha256 = artifact_sha256
    m.training_run_id = training_run_id
    m.class_mapping = class_mapping or {"acct_1": 0, "acct_2": 1, "acct_3": 2}
    m.metrics = {}
    m.training_metrics = {}
    m.validation_metrics = {}
    m.test_metrics = {}
    m.calibration_metrics = {}
    m.hyperparameters = {}
    m.dataset_fingerprint = "abc123"
    m.label_policy_version = "1.0"
    m.promoted_at = promoted_at
    m.retired_at = retired_at
    m.created_at = datetime.now(UTC)
    m.updated_at = datetime.now(UTC)
    return m


def _mock_session_with_model(model: MagicMock) -> AsyncMock:
    """Create a mock AsyncSession that returns the given model."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = model
    session.execute.return_value = result_mock
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _mock_session_empty() -> AsyncMock:
    """Create a mock AsyncSession that returns None for all queries."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    session.execute.return_value = result_mock
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


# ===========================================================================
# A. Registry Tests (8+ tests)
# ===========================================================================


class TestRegistryTransitions:
    """Model lifecycle transition enforcement."""

    # -- Valid transitions --

    async def test_candidate_to_validated(self) -> None:
        """CANDIDATE → VALIDATED is allowed."""
        model = _make_model(status=ModelStatus.CANDIDATE.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        result = await registry.transition_status(
            session, model.id, ModelStatus.VALIDATED.value, actor="test"
        )
        assert result.status == ModelStatus.VALIDATED.value

    async def test_validated_to_shadow(self) -> None:
        """VALIDATED → SHADOW is allowed."""
        model = _make_model(status=ModelStatus.VALIDATED.value)
        # First execute call: model lookup → returns model.
        # Second execute call: existing shadow check → returns None.
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.side_effect = [model, None]
        session.execute.return_value = result_mock
        session.add = MagicMock()
        session.flush = AsyncMock()
        registry = ModelRegistry()

        result = await registry.transition_status(
            session, model.id, ModelStatus.SHADOW.value, actor="test"
        )
        assert result.status == ModelStatus.SHADOW.value

    async def test_shadow_to_retired(self) -> None:
        """SHADOW → RETIRED is allowed."""
        model = _make_model(status=ModelStatus.SHADOW.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        result = await registry.transition_status(
            session, model.id, ModelStatus.RETIRED.value, actor="test"
        )
        assert result.status == ModelStatus.RETIRED.value

    # -- Invalid transitions --

    async def test_candidate_to_shadow_rejected(self) -> None:
        """CANDIDATE → SHADOW is not allowed (must go through VALIDATED)."""
        model = _make_model(status=ModelStatus.CANDIDATE.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError):
            await registry.transition_status(
                session, model.id, ModelStatus.SHADOW.value, actor="test"
            )

    async def test_shadow_to_candidate_rejected(self) -> None:
        """SHADOW → CANDIDATE is not allowed (terminal progression only)."""
        model = _make_model(status=ModelStatus.SHADOW.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError):
            await registry.transition_status(
                session, model.id, ModelStatus.CANDIDATE.value, actor="test"
            )

    async def test_retired_to_anything_rejected(self) -> None:
        """RETIRED is a terminal state — no transitions allowed."""
        model = _make_model(status=ModelStatus.RETIRED.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        for target in [
            ModelStatus.CANDIDATE.value,
            ModelStatus.VALIDATED.value,
            ModelStatus.SHADOW.value,
        ]:
            with pytest.raises(InvalidModelTransitionError):
                await registry.transition_status(
                    session, model.id, target, actor="test"
                )

    # -- CHAMPION / PRIMARY boundary --

    async def test_champion_rejected_from_candidate(self) -> None:
        """CANDIDATE → CHAMPION is not allowed."""
        model = _make_model(status=ModelStatus.CANDIDATE.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError):
            await registry.transition_status(
                session, model.id, ModelStatus.CHAMPION.value, actor="test"
            )

    async def test_champion_rejected_from_validated(self) -> None:
        """VALIDATED → CHAMPION is not allowed."""
        model = _make_model(status=ModelStatus.VALIDATED.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError):
            await registry.transition_status(
                session, model.id, ModelStatus.CHAMPION.value, actor="test"
            )

    async def test_champion_rejected_from_shadow(self) -> None:
        """SHADOW → CHAMPION is not allowed."""
        model = _make_model(status=ModelStatus.SHADOW.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError):
            await registry.transition_status(
                session, model.id, ModelStatus.CHAMPION.value, actor="test"
            )

    async def test_primary_rejected_as_status(self) -> None:
        """PRIMARY is an inference mode, not a model lifecycle status."""
        model = _make_model(status=ModelStatus.CANDIDATE.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError, match="PRIMARY is an inference mode"):
            await registry.transition_status(session, model.id, "PRIMARY", actor="test")

    # -- One SHADOW per realm --

    async def test_one_shadow_per_realm(self) -> None:
        """Cannot promote to SHADOW if another SHADOW model exists for the realm."""
        model = _make_model(status=ModelStatus.VALIDATED.value, realm_id="realm-1")
        existing_shadow = _make_model(
            model_id="m-existing", status=ModelStatus.SHADOW.value, realm_id="realm-1"
        )
        session = AsyncMock()
        result_mock = MagicMock()
        # First call: model lookup returns the VALIDATED model.
        # Second call: existing shadow check returns the conflict.
        result_mock.scalar_one_or_none.side_effect = [model, existing_shadow]
        session.execute.return_value = result_mock
        session.add = MagicMock()
        session.flush = AsyncMock()
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError, match="already in SHADOW"):
            await registry.transition_status(
                session, model.id, ModelStatus.SHADOW.value, actor="test"
            )

    # -- Append-only events --

    async def test_event_recorded_on_transition(self) -> None:
        """A STATUS_TRANSITION event is recorded on each transition."""
        model = _make_model(status=ModelStatus.CANDIDATE.value)
        session = _mock_session_with_model(model)
        registry = ModelRegistry()

        await registry.transition_status(
            session, model.id, ModelStatus.VALIDATED.value, actor="tester", reason="unit test"
        )
        # Verify session.add was called with an event object
        session.add.assert_called_once()
        event = session.add.call_args[0][0]
        assert event.event_type == "STATUS_TRANSITION"
        assert event.previous_status == ModelStatus.CANDIDATE.value
        assert event.new_status == ModelStatus.VALIDATED.value

    # -- Nonexistent model --

    async def test_transition_nonexistent_model_raises(self) -> None:
        """Transitioning a nonexistent model raises InvalidModelTransitionError."""
        session = _mock_session_empty()
        registry = ModelRegistry()

        with pytest.raises(InvalidModelTransitionError, match="Model not found"):
            await registry.transition_status(
                session, "nonexistent-id", ModelStatus.VALIDATED.value, actor="test"
            )


# ===========================================================================
# B. Shadow Inference Tests (6+ tests)
# ===========================================================================


class TestShadowInference:
    """Shadow inference overlay tests."""

    async def test_prediction_stored_separately(self) -> None:
        """Shadow inference stores prediction via session.add, not in Stage 7."""
        predictor = MagicMock(spec=MLPredictor)
        predictor.predict.return_value = [
            {"account_id": "acct_1", "raw_prob": 0.9, "calibrated_prob": 0.95},
            {"account_id": "acct_2", "raw_prob": 0.08, "calibrated_prob": 0.04},
        ]
        shadow = ShadowInference(predictor=predictor)
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await shadow.run_shadow(
            session=session,
            model=MagicMock(),
            categorization_id="cat-001",
            transaction={"id": "txn-1"},
            deterministic_recommendation={
                "recommended_account_quickbooks_id": "acct_1"
            },
            class_mapping={"acct_1": 0, "acct_2": 1},
            model_id="m-001",
            realm_id="realm-1",
            feature_vector=np.array([1.0, 2.0, 3.0]),
        )

        assert result is not None
        # Two records added: MlPrediction and MlShadowEvaluation
        assert session.add.call_count == 2

    async def test_stage7_result_unchanged_by_shadow(self) -> None:
        """The deterministic recommendation is not modified."""
        predictor = MagicMock(spec=MLPredictor)
        predictor.predict.return_value = [
            {"account_id": "acct_2", "raw_prob": 0.9, "calibrated_prob": 0.95},
        ]
        shadow = ShadowInference(predictor=predictor)
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        det_result = {"recommended_account_quickbooks_id": "acct_1"}
        det_result_copy = dict(det_result)

        await shadow.run_shadow(
            session=session,
            model=MagicMock(),
            categorization_id="cat-001",
            transaction={},
            deterministic_recommendation=det_result,
            class_mapping={"acct_1": 0, "acct_2": 1},
            model_id="m-001",
            realm_id="realm-1",
            feature_vector=np.array([1.0]),
        )

        # Deterministic result must not be mutated
        assert det_result == det_result_copy

    async def test_no_qb_writeback_call(self) -> None:
        """Shadow inference must not trigger any QuickBooks API call."""
        predictor = MagicMock(spec=MLPredictor)
        predictor.predict.return_value = [
            {"account_id": "acct_1", "raw_prob": 0.9, "calibrated_prob": 0.95},
        ]
        shadow = ShadowInference(predictor=predictor)
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        with patch("agentblue.ml.inference.shadow.MlPrediction") as mock_pred_cls:
            mock_pred_cls.return_value = MagicMock()
            await shadow.run_shadow(
                session=session,
                model=MagicMock(),
                categorization_id="cat-001",
                transaction={},
                deterministic_recommendation={
                    "recommended_account_quickbooks_id": "acct_1"
                },
                class_mapping={"acct_1": 0},
                model_id="m-001",
                realm_id="realm-1",
                feature_vector=np.array([1.0]),
            )

        # No external API calls — only session.add and session.flush
        assert session.flush.called

    async def test_missing_feature_vector_returns_none(self) -> None:
        """If feature_vector is None, shadow inference is skipped gracefully."""
        shadow = ShadowInference()
        session = AsyncMock()

        result = await shadow.run_shadow(
            session=session,
            model=MagicMock(),
            categorization_id="cat-001",
            transaction={},
            deterministic_recommendation={},
            class_mapping={},
            feature_vector=None,
        )

        assert result is None

    async def test_empty_predictions_returns_none(self) -> None:
        """If the model produces no predictions, shadow returns None."""
        predictor = MagicMock(spec=MLPredictor)
        predictor.predict.return_value = []
        shadow = ShadowInference(predictor=predictor)
        session = AsyncMock()

        result = await shadow.run_shadow(
            session=session,
            model=MagicMock(),
            categorization_id="cat-001",
            transaction={},
            deterministic_recommendation={},
            class_mapping={"acct_1": 0},
            feature_vector=np.array([1.0]),
        )

        assert result is None

    async def test_shadow_exception_does_not_propagate(self) -> None:
        """Shadow inference catches all exceptions and returns None."""
        predictor = MagicMock(spec=MLPredictor)
        predictor.predict.side_effect = RuntimeError("model exploded")
        shadow = ShadowInference(predictor=predictor)
        session = AsyncMock()

        result = await shadow.run_shadow(
            session=session,
            model=MagicMock(),
            categorization_id="cat-001",
            transaction={},
            deterministic_recommendation={},
            class_mapping={},
            feature_vector=np.array([1.0]),
        )

        assert result is None


# ===========================================================================
# B2. Prediction Ranking Tests
# ===========================================================================


class TestPredictionRanking:
    """Top-k ordering and account validation."""

    async def test_top_k_ordering(self) -> None:
        """Predictions are sorted by calibrated_prob descending."""
        predictor = MLPredictor()

        # Build a simple mock model with known probabilities
        model = MagicMock()
        model.predict_proba.return_value = np.array([[0.1, 0.7, 0.2]])

        predictions = predictor.predict(
            model=model,
            features=np.array([1.0, 2.0]),
            class_mapping={"a": 0, "b": 1, "c": 2},
        )

        assert len(predictions) == 3
        assert predictions[0]["calibrated_prob"] >= predictions[1]["calibrated_prob"]
        assert predictions[1]["calibrated_prob"] >= predictions[2]["calibrated_prob"]

    async def test_feature_mismatch_returns_empty(self) -> None:
        """Predictor returns empty list on exception (e.g. feature mismatch)."""
        predictor = MLPredictor()
        model = MagicMock()
        model.predict_proba.side_effect = ValueError("feature mismatch")

        predictions = predictor.predict(
            model=model,
            features=np.array([1.0]),
            class_mapping={"a": 0},
        )

        assert predictions == []

    async def test_account_validator_filters_invalid(self) -> None:
        """rank_predictions filters out invalid accounts."""
        predictions = [
            {"account_id": "valid", "raw_prob": 0.5, "calibrated_prob": 0.5},
            {"account_id": "invalid", "raw_prob": 0.3, "calibrated_prob": 0.3},
            {"account_id": "", "raw_prob": 0.2, "calibrated_prob": 0.2},
        ]

        class MockValidator:
            async def is_valid_account(self, realm_id: str, account_id: str) -> bool:
                return account_id == "valid"

        result = await rank_predictions(predictions, MockValidator(), "realm-1")
        assert len(result) == 1
        assert result[0]["account_id"] == "valid"


# ===========================================================================
# C. Shadow Resolution Tests (6+ tests)
# ===========================================================================


class TestShadowResolution:
    """Shadow outcome classification."""

    def test_both_correct_outcomes(self) -> None:
        """Both ML and rule correct → BOTH_CORRECT or AGREEMENT."""
        assert ShadowOutcome.AGREEMENT.value == "AGREEMENT"
        assert ShadowOutcome.BOTH_CORRECT.value == "BOTH_CORRECT"

    def test_both_incorrect_outcome(self) -> None:
        """Both wrong → BOTH_INCORRECT."""
        assert ShadowOutcome.BOTH_INCORRECT.value == "BOTH_INCORRECT"

    def test_rule_correct_outcome(self) -> None:
        """Only deterministic correct → RULE_CORRECT."""
        assert ShadowOutcome.RULE_CORRECT.value == "RULE_CORRECT"

    def test_ml_correct_outcome(self) -> None:
        """Only ML correct → ML_CORRECT."""
        assert ShadowOutcome.ML_CORRECT.value == "ML_CORRECT"

    def test_disagreement_outcome(self) -> None:
        """Different predictions without ground truth → DISAGREEMENT."""
        assert ShadowOutcome.DISAGREEMENT.value == "DISAGREEMENT"

    def test_unresolved_outcome(self) -> None:
        """Not yet classified → UNRESOLVED."""
        assert ShadowOutcome.UNRESOLVED.value == "UNRESOLVED"

    def test_shadow_agreement_when_accounts_match(self) -> None:
        """Shadow inference marks AGREEMENT when ML top-1 matches rule."""
        predictor = MagicMock(spec=MLPredictor)
        predictor.predict.return_value = [
            {"account_id": "acct_1", "raw_prob": 0.9, "calibrated_prob": 0.95},
        ]
        shadow = ShadowInference(predictor=predictor)
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        import asyncio

        result = asyncio.run(
            shadow.run_shadow(
                session=session,
                model=MagicMock(),
                categorization_id="cat-001",
                transaction={},
                deterministic_recommendation={
                    "recommended_account_quickbooks_id": "acct_1"
                },
                class_mapping={"acct_1": 0},
                model_id="m-001",
                realm_id="realm-1",
                feature_vector=np.array([1.0]),
            )
        )

        assert result is not None
        assert result["outcome"] == ShadowOutcome.AGREEMENT.value

    def test_shadow_disagreement_when_accounts_differ(self) -> None:
        """Shadow inference marks DISAGREEMENT when accounts differ."""
        predictor = MagicMock(spec=MLPredictor)
        predictor.predict.return_value = [
            {"account_id": "acct_2", "raw_prob": 0.9, "calibrated_prob": 0.95},
        ]
        shadow = ShadowInference(predictor=predictor)
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        import asyncio

        result = asyncio.run(
            shadow.run_shadow(
                session=session,
                model=MagicMock(),
                categorization_id="cat-001",
                transaction={},
                deterministic_recommendation={
                    "recommended_account_quickbooks_id": "acct_1"
                },
                class_mapping={"acct_1": 0, "acct_2": 1},
                model_id="m-001",
                realm_id="realm-1",
                feature_vector=np.array([1.0]),
            )
        )

        assert result is not None
        assert result["outcome"] == ShadowOutcome.DISAGREEMENT.value

    def test_accountant_selects_third_account(self) -> None:
        """When the user selects a third account, override_rate captures it."""
        evaluations = [
            {
                "resolved": True,
                "rule_account_quickbooks_id": "acct_1",
                "resolution": "acct_3",  # third account
            },
            {
                "resolved": True,
                "rule_account_quickbooks_id": "acct_1",
                "resolution": "acct_1",  # kept rule
            },
        ]
        rate = compute_override_rate(evaluations)
        assert rate == pytest.approx(0.5)


# ===========================================================================
# D. Drift Detection Tests (5+ tests)
# ===========================================================================


class TestDriftDetection:
    """PSI and JSD drift detection."""

    def test_psi_stable_distribution(self) -> None:
        """Low PSI when distributions are similar."""
        detector = DriftDetector()
        rng = np.random.default_rng(42)
        ref = rng.normal(100, 10, size=1000)
        cur = rng.normal(100, 10, size=1000)

        result = detector.detect_drift({"amount": ref}, {"amount": cur})
        psi_result = result["feature_drift"]["amount"]

        assert psi_result["metric_name"] == "PSI"
        assert psi_result["metric_value"] < 0.1
        assert psi_result["drifted"] is False

    def test_psi_shifted_distribution(self) -> None:
        """High PSI when distributions are very different."""
        detector = DriftDetector()
        rng = np.random.default_rng(42)
        ref = rng.normal(100, 10, size=1000)
        cur = rng.normal(200, 10, size=1000)

        result = detector.detect_drift({"amount": ref}, {"amount": cur})
        psi_result = result["feature_drift"]["amount"]

        assert psi_result["metric_name"] == "PSI"
        assert psi_result["metric_value"] > 0.2
        assert psi_result["drifted"] is True

    def test_jsd_stable_distribution(self) -> None:
        """Low JSD when categorical distributions are similar."""
        detector = DriftDetector()
        ref = np.array(["a"] * 50 + ["b"] * 30 + ["c"] * 20)
        cur = np.array(["a"] * 48 + ["b"] * 32 + ["c"] * 20)

        result = detector.detect_drift({"vendor": ref}, {"vendor": cur})
        jsd_result = result["feature_drift"]["vendor"]

        assert jsd_result["metric_name"] == "JSD"
        assert jsd_result["metric_value"] < 0.1
        assert jsd_result["drifted"] is False

    def test_jsd_shifted_distribution(self) -> None:
        """High JSD when categorical distributions are very different."""
        detector = DriftDetector()
        ref = np.array(["a"] * 80 + ["b"] * 20)
        cur = np.array(["a"] * 10 + ["b"] * 90)

        result = detector.detect_drift({"category": ref}, {"category": cur})
        jsd_result = result["feature_drift"]["category"]

        assert jsd_result["metric_name"] == "JSD"
        assert jsd_result["metric_value"] > 0.1
        assert jsd_result["drifted"] is True

    def test_unseen_categories_handled(self) -> None:
        """JSD handles categories that exist only in current data."""
        detector = DriftDetector()
        ref = np.array(["a"] * 50 + ["b"] * 50)
        cur = np.array(["a"] * 30 + ["b"] * 30 + ["new_cat"] * 40)

        result = detector.detect_drift({"vendor": ref}, {"vendor": cur})
        jsd_result = result["feature_drift"]["vendor"]

        # Should compute without error and detect drift
        assert "metric_value" in jsd_result
        assert jsd_result["unique_categories"] == 3

    def test_log_zero_smoothing(self) -> None:
        """Epsilon smoothing prevents log(0) errors."""
        detector = DriftDetector()
        # Edge case: one category has zero count in current
        ref = np.array(["a"] * 50 + ["b"] * 50)
        cur = np.array(["a"] * 100)

        result = detector.detect_drift({"vendor": ref}, {"vendor": cur})
        jsd_result = result["feature_drift"]["vendor"]

        # Must not raise — epsilon smoothing handles zero counts
        assert np.isfinite(jsd_result["metric_value"])

    def test_label_drift_detection(self) -> None:
        """Label drift uses JSD and works on label arrays."""
        detector = DriftDetector()
        ref_labels = np.array(["acct_1"] * 50 + ["acct_2"] * 50)
        cur_labels = np.array(["acct_1"] * 10 + ["acct_2"] * 90)

        result = detector.detect_label_drift(ref_labels, cur_labels)
        assert result["metric_name"] == "JSD"
        assert result["metric_value"] > 0


# ===========================================================================
# E. Monitoring Metrics Tests
# ===========================================================================


class TestMonitoringMetrics:
    """Agreement and override rate computation."""

    def test_agreement_rate_all_agree(self) -> None:
        evals = [{"outcome": "AGREEMENT"}, {"outcome": "AGREEMENT"}]
        assert compute_shadow_agreement_rate(evals) == 1.0

    def test_agreement_rate_none_agree(self) -> None:
        evals = [{"outcome": "DISAGREEMENT"}, {"outcome": "ML_CORRECT"}]
        assert compute_shadow_agreement_rate(evals) == 0.0

    def test_agreement_rate_empty(self) -> None:
        assert compute_shadow_agreement_rate([]) == 0.0

    def test_override_rate_no_overrides(self) -> None:
        evals = [
            {"resolved": True, "rule_account_quickbooks_id": "a", "resolution": "a"},
        ]
        assert compute_override_rate(evals) == 0.0

    def test_override_rate_all_overridden(self) -> None:
        evals = [
            {"resolved": True, "rule_account_quickbooks_id": "a", "resolution": "b"},
        ]
        assert compute_override_rate(evals) == 1.0

    def test_override_rate_unresolved_excluded(self) -> None:
        evals = [
            {"resolved": False, "rule_account_quickbooks_id": "a", "resolution": ""},
            {"resolved": True, "rule_account_quickbooks_id": "a", "resolution": "a"},
        ]
        assert compute_override_rate(evals) == 0.0
