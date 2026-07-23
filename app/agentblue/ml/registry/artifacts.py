"""Artifact management for ML models.

Handles saving, loading, and verifying model artifacts using joblib
serialization with SHA-256 integrity checks and atomic writes.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import joblib
import structlog

from agentblue.ml.constants import ML_ARTIFACT_ROOT
from agentblue.ml.exceptions import ArtifactError, ArtifactHashMismatchError

logger = structlog.get_logger(__name__)

# 64 KB read buffer for hashing
_HASH_BUFFER_SIZE = 65536


class ArtifactManager:
    """Manages ML model artifact persistence and integrity verification."""

    def __init__(self, artifact_root: str | None = None) -> None:
        self._root = Path(artifact_root or ML_ARTIFACT_ROOT)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def artifact_root(self) -> Path:
        """Return the artifact storage root directory."""
        return self._root

    def save_artifact(
        self,
        model: Any,
        path: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Save a model artifact atomically and return (uri, sha256).

        Writes to a temporary file first, then renames atomically so that
        a crash mid-write never leaves a partial artifact on disk.

        Args:
            model: The model object to serialize (must be joblib-compatible).
            path: Relative path under the artifact root.
            metadata: Optional metadata dict bundled with the artifact.

        Returns:
            A tuple of (absolute_uri, sha256_hex_digest).
        """
        target = self._root / path
        target.parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, Any] = {
            "model": model,
            "metadata": metadata or {},
        }

        # Atomic write: write to temp file in same directory, then rename.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent),
            suffix=".tmp",
            prefix=".artifact_",
        )
        try:
            os.close(fd)
            joblib.dump(payload, tmp_path, compress=3)
            sha256 = self._compute_file_hash(tmp_path)
            os.replace(tmp_path, str(target))
            logger.info(
                "artifact_saved",
                path=str(target),
                sha256=sha256,
            )
            return str(target.resolve()), sha256
        except Exception:
            # Clean up temp file on failure.
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def load_artifact(
        self,
        uri: str,
        expected_sha256: str | None = None,
    ) -> Any:
        """Load a model artifact, optionally verifying its hash.

        Args:
            uri: Absolute path to the artifact file.
            expected_sha256: If provided, verify the file hash before loading.

        Returns:
            The deserialized model object.

        Raises:
            ArtifactError: If the file does not exist or cannot be read.
            ArtifactHashMismatchError: If the hash verification fails.
        """
        path = Path(uri)
        if not path.exists():
            raise ArtifactError(f"Artifact not found: {uri}")

        if expected_sha256 is not None and not self.verify_hash(uri, expected_sha256):
            raise ArtifactHashMismatchError(
                f"Hash mismatch for {uri}: "
                f"expected={expected_sha256}, "
                f"computed={self._compute_file_hash(uri)}"
            )

        try:
            payload = joblib.load(uri)
        except Exception as exc:
            raise ArtifactError(f"Failed to load artifact {uri}: {exc}") from exc

        if isinstance(payload, dict) and "model" in payload:
            return payload["model"]
        # Backward compat: bare model object
        return payload

    def verify_hash(self, uri: str, expected_sha256: str) -> bool:
        """Verify that a file's SHA-256 matches the expected digest.

        Args:
            uri: Absolute path to the file.
            expected_sha256: Expected SHA-256 hex digest.

        Returns:
            True if the hash matches, False otherwise.
        """
        path = Path(uri)
        if not path.exists():
            return False
        actual = self._compute_file_hash(uri)
        return actual == expected_sha256

    @staticmethod
    def _compute_file_hash(path: str) -> str:
        """Compute SHA-256 hex digest of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_HASH_BUFFER_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
