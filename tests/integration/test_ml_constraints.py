"""Integration tests for ML persistence constraints (Stage 8).

Verifies database-level safeguards:
- Invalid probabilities are rejected by check constraints.
- Duplicate model versions are rejected by unique constraints.
- Duplicate prediction identities are rejected by unique constraints.
- Two SHADOW models per realm are rejected by partial unique index.

Requires PostgreSQL via Docker Compose.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.db.session import get_session_factory
from agentblue.ml.models import (
    MlModel,
    MlPrediction,
)
from agentblue.ml.registry.service import ModelRegistry

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]


@pytest.fixture
async def db_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


def _uid() -> str:
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Probability check constraints
# ---------------------------------------------------------------------------


class TestProbabilityConstraints:
    """Verify that raw_probability and calibrated_probability are in [0, 1]."""

    async def test_raw_probability_above_one_rejected(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Inserting raw_probability > 1 must fail."""
        model = MlModel(
            realm_id=f"realm-{_uid()}",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(model)
        await db_session.flush()

        pred = MlPrediction(
            realm_id=f"realm-{_uid()}",
            transaction_id=_uid(),
            model_id=model.id,
            top_predictions=[],
            inference_mode="SHADOW",
            feature_version="1.0",
            raw_probability=1.5,
            calibrated_probability=0.8,
            rank=1,
        )
        db_session.add(pred)
        with pytest.raises(Exception, match="ck_prediction_raw_prob_range"):
            await db_session.flush()

    async def test_calibrated_probability_negative_rejected(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Inserting calibrated_probability < 0 must fail."""
        model = MlModel(
            realm_id=f"realm-{_uid()}",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(model)
        await db_session.flush()

        pred = MlPrediction(
            realm_id=f"realm-{_uid()}",
            transaction_id=_uid(),
            model_id=model.id,
            top_predictions=[],
            inference_mode="SHADOW",
            feature_version="1.0",
            raw_probability=0.5,
            calibrated_probability=-0.1,
            rank=1,
        )
        db_session.add(pred)
        with pytest.raises(Exception, match="ck_prediction_cal_prob_range"):
            await db_session.flush()

    async def test_rank_zero_rejected(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Inserting rank = 0 must fail."""
        model = MlModel(
            realm_id=f"realm-{_uid()}",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(model)
        await db_session.flush()

        pred = MlPrediction(
            realm_id=f"realm-{_uid()}",
            transaction_id=_uid(),
            model_id=model.id,
            top_predictions=[],
            inference_mode="SHADOW",
            feature_version="1.0",
            raw_probability=0.5,
            calibrated_probability=0.5,
            rank=0,
        )
        db_session.add(pred)
        with pytest.raises(Exception, match="ck_prediction_rank_positive"):
            await db_session.flush()

    async def test_valid_probabilities_accepted(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Inserting valid probabilities in [0, 1] must succeed."""
        model = MlModel(
            realm_id=f"realm-{_uid()}",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(model)
        await db_session.flush()

        pred = MlPrediction(
            realm_id=f"realm-{_uid()}",
            transaction_id=_uid(),
            model_id=model.id,
            top_predictions=[],
            inference_mode="SHADOW",
            feature_version="1.0",
            raw_probability=0.75,
            calibrated_probability=0.80,
            rank=1,
        )
        db_session.add(pred)
        await db_session.flush()
        assert pred.id is not None


# ---------------------------------------------------------------------------
# Model unique constraints
# ---------------------------------------------------------------------------


class TestModelUniqueConstraints:
    """Verify unique constraint on (name, model_version)."""

    async def test_duplicate_model_version_rejected(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Two models with the same name+version must fail."""
        name = f"test-model-{_uid()}"
        m1 = MlModel(
            realm_id=f"realm-{_uid()}",
            name=name,
            model_version="1",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(m1)
        await db_session.flush()

        m2 = MlModel(
            realm_id=f"realm-{_uid()}",
            name=name,
            model_version="1",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(m2)
        with pytest.raises(Exception, match="uq_model_name_version"):
            await db_session.flush()

    async def test_different_version_accepted(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Same name with different version must succeed."""
        name = f"test-model-{_uid()}"
        m1 = MlModel(
            realm_id=f"realm-{_uid()}",
            name=name,
            model_version="1",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(m1)
        await db_session.flush()

        m2 = MlModel(
            realm_id=f"realm-{_uid()}",
            name=name,
            model_version="2",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(m2)
        await db_session.flush()
        assert m2.id is not None


# ---------------------------------------------------------------------------
# Prediction unique constraints
# ---------------------------------------------------------------------------


class TestPredictionUniqueConstraints:
    """Verify unique constraint on (model_id, categorization_id, source_transaction_hash)."""

    async def test_duplicate_prediction_identity_rejected(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Two predictions with same model+categorization+hash must fail."""
        model = MlModel(
            realm_id=f"realm-{_uid()}",
            model_type="DUMMY",
            status="CANDIDATE",
            feature_version="1.0",
            code_version="1.0.0",
        )
        db_session.add(model)
        await db_session.flush()

        cat_id = _uid()
        txn_hash = _uid()

        p1 = MlPrediction(
            realm_id=f"realm-{_uid()}",
            transaction_id=_uid(),
            categorization_id=cat_id,
            source_transaction_hash=txn_hash,
            model_id=model.id,
            top_predictions=[],
            inference_mode="SHADOW",
            feature_version="1.0",
            raw_probability=0.5,
            calibrated_probability=0.5,
            rank=1,
        )
        db_session.add(p1)
        await db_session.flush()

        p2 = MlPrediction(
            realm_id=f"realm-{_uid()}",
            transaction_id=_uid(),
            categorization_id=cat_id,
            source_transaction_hash=txn_hash,
            model_id=model.id,
            top_predictions=[],
            inference_mode="SHADOW",
            feature_version="1.0",
            raw_probability=0.6,
            calibrated_probability=0.6,
            rank=2,
        )
        db_session.add(p2)
        with pytest.raises(Exception, match="uq_prediction_identity"):
            await db_session.flush()


# ---------------------------------------------------------------------------
# SHADOW model per-realm partial unique index
# ---------------------------------------------------------------------------


class TestShadowConcurrency:
    """Verify at most one SHADOW model per realm via partial unique index."""

    async def test_two_shadow_models_same_realm_rejected(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Two models in SHADOW status for the same realm must fail."""
        registry = ModelRegistry()
        realm = f"realm-{_uid()}"

        # Register and promote first model to SHADOW.
        m1 = await registry.register_model(
            session=db_session,
            training_run_id=None,
            realm_id=realm,
            model_type="DUMMY",
            feature_version="1.0",
            code_version="1.0.0",
            calibration_method="NONE",
        )
        # CANDIDATE -> VALIDATED -> SHADOW
        m1 = await registry.transition_status(
            db_session, m1.id, "VALIDATED", actor="test"
        )
        m1 = await registry.transition_status(
            db_session, m1.id, "SHADOW", actor="test"
        )
        assert m1.status == "SHADOW"

        # Register and promote second model (unique name to avoid uq_model_name_version).
        m2 = await registry.register_model(
            session=db_session,
            training_run_id=None,
            realm_id=realm,
            model_type="DUMMY",
            feature_version="1.0",
            code_version="1.0.0",
            calibration_method="NONE",
            name=f"model-{_uid()}",
        )
        m2 = await registry.transition_status(
            db_session, m2.id, "VALIDATED", actor="test"
        )

        # Second SHADOW activation must be rejected (service layer).
        from agentblue.ml.exceptions import InvalidModelTransitionError

        with pytest.raises(InvalidModelTransitionError, match="already in SHADOW"):
            await registry.transition_status(
                db_session, m2.id, "SHADOW", actor="test"
            )

    async def test_shadow_different_realms_allowed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """SHADOW models in different realms must succeed."""
        registry = ModelRegistry()

        for _ in range(2):
            realm = f"realm-{_uid()}"
            m = await registry.register_model(
                session=db_session,
                training_run_id=None,
                realm_id=realm,
                model_type="DUMMY",
                feature_version="1.0",
                code_version="1.0.0",
                calibration_method="NONE",
                name=f"model-{_uid()}",
            )
            m = await registry.transition_status(
                db_session, m.id, "VALIDATED", actor="test"
            )
            m = await registry.transition_status(
                db_session, m.id, "SHADOW", actor="test"
            )
            assert m.status == "SHADOW"


# ---------------------------------------------------------------------------
# CHAMPION / PRIMARY semantics
# ---------------------------------------------------------------------------


class TestChampionPrimarySemantics:
    """Verify CHAMPION and PRIMARY are handled correctly."""

    async def test_champion_rejected_in_stage8(
        self,
        db_session: AsyncSession,
    ) -> None:
        """CHAMPION is reserved for future governance; Stage 8 rejects it."""
        registry = ModelRegistry()
        realm = f"realm-{_uid()}"

        m = await registry.register_model(
            session=db_session,
            training_run_id=None,
            realm_id=realm,
            model_type="DUMMY",
            feature_version="1.0",
            code_version="1.0.0",
            calibration_method="NONE",
        )
        m = await registry.transition_status(
            db_session, m.id, "VALIDATED", actor="test"
        )
        m = await registry.transition_status(
            db_session, m.id, "SHADOW", actor="test"
        )

        from agentblue.ml.exceptions import InvalidModelTransitionError

        # CHAMPION must be rejected from SHADOW.
        with pytest.raises(InvalidModelTransitionError):
            await registry.transition_status(
                db_session, m.id, "CHAMPION", actor="test"
            )

    async def test_primary_rejected_as_status(
        self,
        db_session: AsyncSession,
    ) -> None:
        """PRIMARY is an inference mode, not a lifecycle status."""
        registry = ModelRegistry()
        realm = f"realm-{_uid()}"

        m = await registry.register_model(
            session=db_session,
            training_run_id=None,
            realm_id=realm,
            model_type="DUMMY",
            feature_version="1.0",
            code_version="1.0.0",
            calibration_method="NONE",
        )

        from agentblue.ml.exceptions import InvalidModelTransitionError

        with pytest.raises(InvalidModelTransitionError, match="inference mode"):
            await registry.transition_status(
                db_session, m.id, "PRIMARY", actor="test"
            )
