"""Model manifest management.

A ModelManifest captures the full reproducibility context for a trained
model: algorithm type, feature version, class mapping, evaluation metrics,
and calibration details.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ModelManifest:
    """Reproducibility manifest for a trained ML model."""

    model_type: str
    feature_version: str
    code_version: str
    class_mapping: dict[str, int]
    inverse_class_mapping: dict[int, str]
    metrics: dict[str, Any] = field(default_factory=dict)
    calibration_method: str = "NONE"
    calibration_params: dict[str, Any] = field(default_factory=dict)
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    dataset_id: str = ""
    training_run_id: str = ""
    seed: int = 42
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialize manifest to a JSON-compatible dict."""
        d = asdict(self)
        # Ensure Decimal values are converted to strings for JSON compat.
        return _sanitize_for_json(d)


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable values."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def save_manifest(manifest: ModelManifest, path: str) -> None:
    """Save a ModelManifest to a JSON file.

    Args:
        manifest: The manifest to serialize.
        path: Filesystem path to write.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = manifest.to_dict()
    target.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("manifest_saved", path=str(target))


def load_manifest(path: str) -> ModelManifest:
    """Load a ModelManifest from a JSON file.

    Args:
        path: Filesystem path to read.

    Returns:
        The deserialized ModelManifest.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the JSON is malformed or missing required fields.
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid manifest JSON at {path}: {exc}") from exc

    try:
        return ModelManifest(
            model_type=data["model_type"],
            feature_version=data["feature_version"],
            code_version=data["code_version"],
            class_mapping=data["class_mapping"],
            inverse_class_mapping=data["inverse_class_mapping"],
            metrics=data.get("metrics", {}),
            calibration_method=data.get("calibration_method", "NONE"),
            calibration_params=data.get("calibration_params", {}),
            hyperparameters=data.get("hyperparameters", {}),
            dataset_id=data.get("dataset_id", ""),
            training_run_id=data.get("training_run_id", ""),
            seed=data.get("seed", 42),
            created_at=data.get("created_at", ""),
        )
    except KeyError as exc:
        raise ValueError(f"Manifest missing required field: {exc}") from exc
