"""ML application services (Stage 8).

High-level orchestration for dataset building, training, and model
activation.  Wraps lower-level registry and training components.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.ml.constants import CODE_VERSION, FEATURE_VERSION
from agentblue.ml.domain import DatasetStatus, ModelStatus, TrainingRunStatus
from agentblue.ml.exceptions import MLError
from agentblue.ml.models import MlDataset, MlTrainingRun
from agentblue.ml.registry.service import ModelRegistry

logger = structlog.get_logger(__name__)


class MLService:
    """Application-level ML operations."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self._registry = registry or ModelRegistry()

    async def build_dataset(
        self,
        session: AsyncSession,
        realm_id: str,
        feature_version: str = FEATURE_VERSION,
        min_rows: int = 500,
        min_class_support: int = 20,
    ) -> dict[str, Any]:
        """Build a training dataset from approved labels.

        Args:
            session: Async database session.
            realm_id: QuickBooks realm ID.
            feature_version: Feature extraction version.
            min_rows: Minimum dataset rows required.
            min_class_support: Minimum examples per class.

        Returns:
            Dict with dataset_id, status, row_count, class_count.
        """
        logger.info(
            "dataset_build_started",
            realm_id=realm_id,
            feature_version=feature_version,
        )

        dataset = MlDataset(
            realm_id=realm_id,
            status=DatasetStatus.BUILDING.value,
            feature_version=feature_version,
            code_version=CODE_VERSION,
        )
        session.add(dataset)
        await session.flush()

        # Actual dataset construction would query categorization_training_label,
        # extract features, validate, and persist.  For now, mark as ready
        # with placeholder counts -- the full pipeline is in the data module.
        dataset.status = DatasetStatus.READY.value
        dataset.row_count = 0
        dataset.class_count = 0
        await session.flush()

        logger.info(
            "dataset_build_complete",
            dataset_id=dataset.id,
            row_count=dataset.row_count,
        )

        return {
            "dataset_id": dataset.id,
            "status": dataset.status,
            "row_count": dataset.row_count,
            "class_count": dataset.class_count,
        }

    async def start_training(
        self,
        session: AsyncSession,
        dataset_id: str,
        realm_id: str,
        model_type: str = "HIST_GRADIENT_BOOSTING",
        calibration_method: str = "ISOTONIC",
        seed: int = 42,
    ) -> dict[str, Any]:
        """Start a training run on a prepared dataset.

        Args:
            session: Async database session.
            dataset_id: ID of the dataset to train on.
            realm_id: QuickBooks realm ID.
            model_type: Algorithm type.
            calibration_method: Calibration strategy.
            seed: Random seed for reproducibility.

        Returns:
            Dict with training_run_id, status.

        Raises:
            MLError: If the dataset is not found or not ready.
        """
        # Verify dataset exists and is ready.
        result = await session.execute(select(MlDataset).where(MlDataset.id == dataset_id))
        dataset = result.scalar_one_or_none()
        if dataset is None:
            raise MLError(f"Dataset not found: {dataset_id}")
        if dataset.status != DatasetStatus.READY.value:
            raise MLError(f"Dataset {dataset_id} is not READY (status={dataset.status})")

        logger.info(
            "training_started",
            dataset_id=dataset_id,
            model_type=model_type,
        )

        run = MlTrainingRun(
            realm_id=realm_id,
            dataset_id=dataset_id,
            status=TrainingRunStatus.PENDING.value,
            model_type=model_type,
            calibration_method=calibration_method,
        )
        session.add(run)
        await session.flush()

        # Actual training would happen here (or in a background task).
        # For now, return the run ID.
        return {
            "training_run_id": run.id,
            "status": run.status,
        }

    async def activate_shadow(
        self,
        session: AsyncSession,
        model_id: str,
    ) -> dict[str, Any]:
        """Activate a model in shadow mode.

        Validates that the model is in VALIDATED status, then transitions
        to SHADOW.

        Args:
            session: Async database session.
            model_id: ID of the model to activate.

        Returns:
            Dict with model_id, status.
        """
        model = await self._registry.transition_status(
            session,
            model_id,
            ModelStatus.SHADOW.value,
            actor="ml_service",
            reason="Shadow activation via MLService",
        )

        logger.info(
            "shadow_activated",
            model_id=model_id,
            realm_id=model.realm_id,
        )

        return {
            "model_id": model.id,
            "status": model.status,
        }
