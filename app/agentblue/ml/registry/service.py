"""Model registry service.

Manages the ML model lifecycle: registration, status transitions,
and active shadow model queries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.ml.domain import ModelStatus
from agentblue.ml.exceptions import InvalidModelTransitionError
from agentblue.ml.models import MlModel, MlModelEvent

logger = structlog.get_logger(__name__)

# Allowed transitions: each key can transition to the set of values.
_VALID_TRANSITIONS: dict[str, set[str]] = {
    ModelStatus.CANDIDATE.value: {ModelStatus.VALIDATED.value, ModelStatus.REJECTED.value},
    ModelStatus.VALIDATED.value: {ModelStatus.SHADOW.value, ModelStatus.REJECTED.value},
    ModelStatus.SHADOW.value: {ModelStatus.RETIRED.value, ModelStatus.CHAMPION.value},
    ModelStatus.CHAMPION.value: {ModelStatus.RETIRED.value},
    ModelStatus.REJECTED.value: set(),
    ModelStatus.RETIRED.value: set(),
}

# Statuses that allow only one active model per realm.
_SINGLE_ACTIVE_STATUSES = {ModelStatus.SHADOW.value, ModelStatus.CHAMPION.value}


class ModelRegistry:
    """Manages ML model lifecycle and queries."""

    async def register_model(
        self,
        session: AsyncSession,
        training_run_id: str,
        realm_id: str,
        model_type: str,
        feature_version: str,
        code_version: str,
        calibration_method: str,
        artifact_path: str | None = None,
        artifact_uri: str | None = None,
        artifact_sha256: str | None = None,
        metrics: dict[str, Any] | None = None,
        hyperparameters: dict[str, Any] | None = None,
        name: str = "",
        model_version: str = "1",
        label_policy_version: str = "1.0",
        dataset_fingerprint: str = "",
        class_mapping: dict[str, Any] | None = None,
        training_metrics: dict[str, Any] | None = None,
        validation_metrics: dict[str, Any] | None = None,
        test_metrics: dict[str, Any] | None = None,
        calibration_metrics: dict[str, Any] | None = None,
    ) -> MlModel:
        """Register a new model in CANDIDATE status.

        Args:
            session: Async database session.
            training_run_id: ID of the training run that produced this model.
            realm_id: QuickBooks realm ID.
            model_type: Algorithm type (e.g. HIST_GRADIENT_BOOSTING).
            feature_version: Feature extraction version used during training.
            code_version: Code version that produced this model.
            calibration_method: Calibration strategy applied.
            artifact_path: Path to the serialized model artifact.
            artifact_uri: URI to the serialized model artifact.
            artifact_sha256: SHA-256 hash of the artifact file.
            metrics: Evaluation metrics dict.
            hyperparameters: Hyperparameters used for training.
            name: Human-readable model name.
            model_version: Version string for the model.
            label_policy_version: Label policy version used.
            dataset_fingerprint: Fingerprint of the training dataset.
            class_mapping: Label-to-index mapping.
            training_metrics: Training split metrics.
            validation_metrics: Validation split metrics.
            test_metrics: Test split metrics.
            calibration_metrics: Calibration diagnostics.

        Returns:
            The newly created MlModel ORM instance.
        """
        model = MlModel(
            realm_id=realm_id,
            name=name,
            model_version=model_version,
            model_type=model_type,
            status=ModelStatus.CANDIDATE.value,
            feature_version=feature_version,
            label_policy_version=label_policy_version,
            code_version=code_version,
            calibration_method=calibration_method,
            dataset_fingerprint=dataset_fingerprint,
            artifact_path=artifact_path,
            artifact_uri=artifact_uri,
            artifact_sha256=artifact_sha256,
            training_run_id=training_run_id,
            class_mapping=class_mapping or {},
            hyperparameters=hyperparameters or {},
            metrics=metrics or {},
            training_metrics=training_metrics or {},
            validation_metrics=validation_metrics or {},
            test_metrics=test_metrics or {},
            calibration_metrics=calibration_metrics or {},
        )
        session.add(model)
        await session.flush()

        event = MlModelEvent(
            model_id=model.id,
            realm_id=realm_id,
            event_type="REGISTERED",
            previous_status=None,
            new_status=ModelStatus.CANDIDATE.value,
            detail={"training_run_id": training_run_id},
            actor="system",
        )
        session.add(event)
        await session.flush()

        logger.info(
            "model_registered",
            model_id=model.id,
            model_type=model_type,
            realm_id=realm_id,
        )
        return model

    async def transition_status(
        self,
        session: AsyncSession,
        model_id: str,
        new_status: str,
        actor: str,
        reason: str = "",
    ) -> MlModel:
        """Transition a model to a new status.

        Validates the transition against the allowed state machine.  Rejects
        PRIMARY mode (not yet supported).  Prevents multiple SHADOW models
        per realm.

        Args:
            session: Async database session.
            model_id: ID of the model to transition.
            new_status: Target status string.
            actor: Who initiated the transition.
            reason: Human-readable reason for the transition.

        Returns:
            The updated MlModel.

        Raises:
            InvalidModelTransitionError: If the transition is not allowed.
        """
        # PRIMARY is an inference mode, not a model lifecycle status.
        # It must be rejected at the inference layer, not here.
        # CHAMPION is a valid future lifecycle status reserved for
        # governance -- do not conflate the two concepts.
        if new_status == "PRIMARY":
            raise InvalidModelTransitionError(
                "PRIMARY is an inference mode, not a model lifecycle status. "
                "Use InferenceMode.PRIMARY at the inference layer."
            )

        result = await session.execute(select(MlModel).where(MlModel.id == model_id))
        model = result.scalar_one_or_none()
        if model is None:
            raise InvalidModelTransitionError(f"Model not found: {model_id}")

        current = model.status
        allowed = _VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise InvalidModelTransitionError(
                f"Invalid transition: {current} -> {new_status}. Allowed: {sorted(allowed)}"
            )

        # Prevent multiple active SHADOW models per realm.
        if new_status in _SINGLE_ACTIVE_STATUSES:
            existing = await session.execute(
                select(MlModel).where(
                    MlModel.realm_id == model.realm_id,
                    MlModel.status == new_status,
                    MlModel.id != model_id,
                )
            )
            conflict = existing.scalar_one_or_none()
            if conflict is not None:
                raise InvalidModelTransitionError(
                    f"Model {conflict.id} is already in {new_status} status "
                    f"for realm {model.realm_id}. Retire it first."
                )

        previous_status = model.status
        model.status = new_status
        now = datetime.now(UTC)

        if new_status == ModelStatus.SHADOW.value:
            model.promoted_at = now
        elif new_status == ModelStatus.RETIRED.value:
            model.retired_at = now

        event = MlModelEvent(
            model_id=model_id,
            realm_id=model.realm_id,
            event_type="STATUS_TRANSITION",
            previous_status=previous_status,
            new_status=new_status,
            detail={"reason": reason},
            actor=actor,
        )
        session.add(event)
        await session.flush()

        logger.info(
            "model_status_transition",
            model_id=model_id,
            previous=previous_status,
            new=new_status,
            actor=actor,
        )
        return model

    async def get_active_shadow(
        self,
        session: AsyncSession,
        realm_id: str,
    ) -> MlModel | None:
        """Return the active SHADOW model for a realm, if any.

        Args:
            session: Async database session.
            realm_id: QuickBooks realm ID.

        Returns:
            The MlModel in SHADOW status, or None.
        """
        result = await session.execute(
            select(MlModel).where(
                MlModel.realm_id == realm_id,
                MlModel.status == ModelStatus.SHADOW.value,
            )
        )
        return result.scalar_one_or_none()

    async def get_model(
        self,
        session: AsyncSession,
        model_id: str,
    ) -> MlModel | None:
        """Return a model by ID.

        Args:
            session: Async database session.
            model_id: ID of the model.

        Returns:
            The MlModel, or None if not found.
        """
        result = await session.execute(select(MlModel).where(MlModel.id == model_id))
        return result.scalar_one_or_none()

    async def list_models(
        self,
        session: AsyncSession,
        realm_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[MlModel]:
        """List models with optional filters.

        Args:
            session: Async database session.
            realm_id: Optional realm filter.
            status: Optional status filter.
            limit: Maximum results.

        Returns:
            List of MlModel instances.
        """
        stmt = select(MlModel)
        if realm_id is not None:
            stmt = stmt.where(MlModel.realm_id == realm_id)
        if status is not None:
            stmt = stmt.where(MlModel.status == status)
        stmt = stmt.order_by(MlModel.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())
