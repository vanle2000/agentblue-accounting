"""Typed exceptions for the ML domain (Stage 8)."""

from __future__ import annotations


class MLError(Exception):
    """Base exception for all ML domain errors."""


class DatasetError(MLError):
    """General dataset construction or validation error."""


class InsufficientDataError(DatasetError):
    """Not enough rows or classes to build a viable dataset."""


class LabelPolicyError(DatasetError):
    """A label failed one of the inclusion policy checks."""


class LeakageDetectedError(DatasetError):
    """Data leakage detected between train / validation / test splits."""


class TrainingError(MLError):
    """Training run failed or produced an invalid model."""


class ModelNotFoundError(MLError):
    """Requested model artifact does not exist."""


class ArtifactError(MLError):
    """Artifact file I/O or integrity error."""


class ArtifactHashMismatchError(ArtifactError):
    """Stored hash does not match the recomputed hash."""


class InvalidModelTransitionError(MLError):
    """Attempted an illegal status transition on a model."""


class InferenceError(MLError):
    """Prediction or scoring failed at inference time."""


class FeatureVersionMismatchError(InferenceError):
    """Feature vector version does not match the model's expected version."""


class UnsupportedInferenceModeError(InferenceError):
    """Requested inference mode is not available or disabled."""
