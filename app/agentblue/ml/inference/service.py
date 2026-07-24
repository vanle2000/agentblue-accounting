"""Inference service for ML categorization.

Provides the application-level entry point for running ML inference
alongside the deterministic categorization engine.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agentblue.ml.constants import ML_ENABLED
from agentblue.ml.inference.shadow import ShadowInference
from agentblue.ml.registry.loading import load_registered_model
from agentblue.ml.registry.service import ModelRegistry

logger = structlog.get_logger(__name__)


class InferenceService:
    """Application-level ML inference service."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        shadow: ShadowInference | None = None,
    ) -> None:
        self._registry = registry or ModelRegistry()
        self._shadow = shadow or ShadowInference()

    async def categorize_with_shadow(
        self,
        session: AsyncSession,
        realm_id: str,
        categorization_id: str,
        transaction: dict[str, Any],
        deterministic_result: dict[str, Any],
        feature_vector: Any | None = None,
        account_validator: Any | None = None,
    ) -> dict[str, Any]:
        """Run deterministic categorization with optional ML shadow overlay.

        If ML_ENABLED is True and an active shadow model exists for the
        realm, runs the ML model in shadow mode.  The deterministic result
        is always returned unmodified; shadow data is attached under a
        ``shadow`` key when available.

        Args:
            session: Async database session.
            realm_id: QuickBooks realm ID.
            categorization_id: ID of the categorization record.
            transaction: Transaction data dict.
            deterministic_result: The Stage 7 categorization result.
            feature_vector: Pre-computed ML feature vector.
            account_validator: Optional account validity checker.

        Returns:
            The deterministic result dict, optionally enriched with a
            ``shadow`` key containing ML prediction data.
        """
        result = dict(deterministic_result)

        if not ML_ENABLED:
            result["shadow"] = None
            return result

        # Find active shadow model.
        shadow_model = await self._registry.get_active_shadow(session, realm_id)
        if shadow_model is None:
            logger.debug("no_shadow_model", realm_id=realm_id)
            result["shadow"] = None
            return result

        try:
            model_obj, calibrator_params, class_mapping = await load_registered_model(
                session, shadow_model.id
            )

            # Build a minimal calibrator if params are available.
            # (In production, this would be a proper sklearn calibrator.)
            calibrator = None  # Placeholder for calibrator reconstruction.

            shadow_result = await self._shadow.run_shadow(
                session=session,
                model=model_obj,
                categorization_id=categorization_id,
                transaction=transaction,
                deterministic_recommendation=deterministic_result,
                class_mapping=class_mapping,
                calibrator=calibrator,
                model_id=shadow_model.id,
                realm_id=realm_id,
                feature_vector=feature_vector,
                account_validator=account_validator,
            )

            result["shadow"] = shadow_result

        except Exception as exc:
            # Shadow failure must not affect deterministic output.
            logger.warning(
                "shadow_overlay_failed",
                categorization_id=categorization_id,
                error=str(exc)[:200],
            )
            result["shadow"] = None

        return result
