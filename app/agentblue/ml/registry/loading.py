"""Safe model loading with hash verification.

Loads registered models from the database and verifies artifact integrity
before deserialization.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.ml.exceptions import (
    ArtifactError,
    ModelNotFoundError,
)
from agentblue.ml.registry.artifacts import ArtifactManager
from agentblue.ml.registry.service import ModelRegistry

logger = structlog.get_logger(__name__)


async def load_registered_model(
    session: AsyncSession,
    model_id: str,
    artifact_manager: ArtifactManager | None = None,
) -> tuple[Any, dict[str, Any], dict[str, int]]:
    """Load a registered model with hash verification.

    Retrieves the model record from the database, verifies the artifact
    SHA-256 hash, then deserializes the model object.

    Args:
        session: Async database session.
        model_id: ID of the MlModel to load.
        artifact_manager: Optional ArtifactManager instance. Created with
            defaults if not provided.

    Returns:
        A tuple of (model_object, calibration_params, class_mapping).

    Raises:
        ModelNotFoundError: If the model ID does not exist.
        ArtifactError: If the artifact file is missing or corrupt.
        ArtifactHashMismatchError: If the hash does not match.
    """
    registry = ModelRegistry()
    ml_model = await registry.get_model(session, model_id)
    if ml_model is None:
        raise ModelNotFoundError(f"Model not found: {model_id}")

    if not ml_model.artifact_path:
        raise ArtifactError(f"Model {model_id} has no artifact path")

    if not ml_model.artifact_sha256:
        raise ArtifactError(f"Model {model_id} has no artifact hash recorded")

    mgr = artifact_manager or ArtifactManager()

    logger.info(
        "loading_registered_model",
        model_id=model_id,
        artifact_path=ml_model.artifact_path,
    )

    # Verify and load the model object.
    model_obj = mgr.load_artifact(
        uri=ml_model.artifact_path,
        expected_sha256=ml_model.artifact_sha256,
    )

    # Extract calibration params from metrics (if stored during training).
    calibration_params: dict[str, Any] = {}
    if isinstance(ml_model.metrics, dict):
        raw_cal = ml_model.metrics.get("calibration_params", {})
        if isinstance(raw_cal, dict):
            calibration_params = raw_cal

    # Extract class mapping from metrics (stored during training).
    class_mapping: dict[str, int] = {}
    if isinstance(ml_model.metrics, dict):
        raw_mapping = ml_model.metrics.get("class_mapping", {})
        if isinstance(raw_mapping, dict):
            class_mapping = {str(k): int(v) for k, v in raw_mapping.items()}

    logger.info(
        "registered_model_loaded",
        model_id=model_id,
        class_count=len(class_mapping),
    )

    return model_obj, calibration_params, class_mapping
