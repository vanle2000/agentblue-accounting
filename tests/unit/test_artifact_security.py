"""Security tests for ArtifactManager path traversal prevention.

Validates that artifacts outside the artifact root cannot be opened,
deserialized, or verified regardless of attack vector.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# A simple picklable object to use as a mock model.
class _FakeModel:
    def __init__(self, value: str = 'fake') -> None:
        self.value = value
    def __repr__(self) -> str:
        return f'_FakeModel({self.value!r})'

import pytest

from agentblue.ml.exceptions import ArtifactError, ArtifactHashMismatchError
from agentblue.ml.registry.artifacts import ArtifactManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    """Create a temporary artifact root directory."""
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


@pytest.fixture
def manager(artifact_root: Path) -> ArtifactManager:
    """Create an ArtifactManager with a temporary root."""
    return ArtifactManager(str(artifact_root))


@pytest.fixture
def saved_artifact(manager: ArtifactManager, artifact_root: Path) -> tuple[str, str]:
    """Save a valid artifact and return (uri, sha256)."""
    model = _FakeModel()
    return manager.save_artifact(model, "valid/model.joblib")


# ---------------------------------------------------------------------------
# Valid artifact tests
# ---------------------------------------------------------------------------


class TestValidArtifacts:
    """Verify that valid artifacts within the root still work."""

    def test_valid_artifact_loads(
        self, manager: ArtifactManager, saved_artifact: tuple[str, str]
    ) -> None:
        """A valid artifact within the root loads successfully."""
        uri, sha256 = saved_artifact
        result = manager.load_artifact(uri, expected_sha256=sha256)
        assert result is not None

    def test_valid_nested_artifact_loads(
        self, manager: ArtifactManager, artifact_root: Path
    ) -> None:
        """A deeply nested artifact within the root loads successfully."""
        model = _FakeModel()
        uri, sha256 = manager.save_artifact(model, "a/b/c/deep/model.joblib")
        result = manager.load_artifact(uri, expected_sha256=sha256)
        assert result is not None

    def test_valid_artifact_verify_hash(
        self, manager: ArtifactManager, saved_artifact: tuple[str, str]
    ) -> None:
        """Hash verification works for valid artifacts."""
        uri, sha256 = saved_artifact
        assert manager.verify_hash(uri, sha256) is True

    def test_duplicate_artifact_registration(
        self, manager: ArtifactManager, artifact_root: Path
    ) -> None:
        """Saving to the same path overwrites and produces a new hash."""
        model1 = _FakeModel("v1")
        model2 = _FakeModel("v2")
        uri1, sha1 = manager.save_artifact(model1, "dup/model.joblib")
        uri2, sha256_2 = manager.save_artifact(model2, "dup/model.joblib")
        # Same path, potentially different hash (depends on serialization).
        assert uri1 == uri2
        # Both should load.
        result = manager.load_artifact(uri2, expected_sha256=sha256_2)
        assert result is not None


# ---------------------------------------------------------------------------
# Path traversal tests
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """Reject path traversal attacks."""

    def test_dot_dot_traversal_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """../../secret.joblib is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("../../secret.joblib")

    def test_repeated_traversal_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """../../../artifact.pkl is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("../../../artifact.pkl")

    def test_nested_traversal_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """subdir/../../outside.pkl is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("subdir/../../outside.pkl")

    def test_normalized_escape_rejected(
        self, manager: ArtifactManager, artifact_root: Path
    ) -> None:
        """subdir/../../../outside.pkl is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("subdir/../../../outside.pkl")

    def test_single_dot_dot_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """../models.pkl is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("../models.pkl")

    def test_dot_dot_at_root_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """../model.pkl at root level is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("../model.pkl")


# ---------------------------------------------------------------------------
# Absolute path tests
# ---------------------------------------------------------------------------


class TestAbsolutePath:
    """Reject absolute paths outside the artifact root."""

    def test_absolute_unix_path_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """/etc/passwd is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("/etc/passwd")

    def test_absolute_path_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """An absolute path outside the root is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("/tmp/outside/model.pkl")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
    def test_windows_drive_path_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """C:\\Windows\\System32\\config\\SAM is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("C:\\Windows\\System32\\config\\SAM")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
    def test_different_drive_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """A path on a different drive letter is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("D:\\models\\artifact.pkl")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
    def test_unc_path_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """\\\\server\\share\\file.pkl is rejected."""
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact("\\\\server\\share\\file.pkl")


# ---------------------------------------------------------------------------
# Symlink tests
# ---------------------------------------------------------------------------


class TestSymlinkEscape:
    """Reject symlinks that escape the artifact root."""

    @pytest.mark.skipif(
        sys.platform == "win32" and not os.environ.get("CI"),
        reason="Symlink creation may require privileges on Windows",
    )
    def test_symlink_escape_rejected(
        self, manager: ArtifactManager, artifact_root: Path, tmp_path: Path
    ) -> None:
        """A symlink pointing outside the root is rejected."""
        # Create a secret file outside the artifact root.
        secret = tmp_path / "secret.joblib"
        secret.write_bytes(b"secret data")

        # Create a symlink inside the artifact root pointing to the secret.
        link = artifact_root / "link.joblib"
        try:
            link.symlink_to(secret)
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact(str(link))

    @pytest.mark.skipif(
        sys.platform == "win32" and not os.environ.get("CI"),
        reason="Symlink creation may require privileges on Windows",
    )
    def test_nested_symlink_escape_rejected(
        self, manager: ArtifactManager, artifact_root: Path, tmp_path: Path
    ) -> None:
        """A nested symlink chain escaping the root is rejected."""
        secret = tmp_path / "secret.pkl"
        secret.write_bytes(b"secret")

        subdir = artifact_root / "models"
        subdir.mkdir()

        link = subdir / "escape.pkl"
        try:
            link.symlink_to(secret)
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.load_artifact(str(link))


# ---------------------------------------------------------------------------
# SHA-256 verification tests
# ---------------------------------------------------------------------------


class TestShaVerification:
    """Verify SHA-256 integrity checks."""

    def test_sha_mismatch_rejected(
        self, manager: ArtifactManager, saved_artifact: tuple[str, str]
    ) -> None:
        """A hash mismatch raises ArtifactHashMismatchError."""
        uri, _ = saved_artifact
        with pytest.raises(ArtifactHashMismatchError, match="Hash mismatch"):
            manager.load_artifact(uri, expected_sha256="0" * 64)

    def test_corrupted_artifact_detected(
        self, manager: ArtifactManager, artifact_root: Path
    ) -> None:
        """A corrupted file is detected via hash mismatch."""
        model = _FakeModel()
        uri, sha256 = manager.save_artifact(model, "corrupt/model.joblib")

        # Corrupt the file.
        with open(uri, "ab") as f:
            f.write(b"CORRUPTED")

        with pytest.raises(ArtifactHashMismatchError):
            manager.load_artifact(uri, expected_sha256=sha256)


# ---------------------------------------------------------------------------
# Missing and invalid artifact tests
# ---------------------------------------------------------------------------


class TestMissingArtifact:
    """Handle missing and invalid artifacts."""

    def test_missing_artifact_raises(
        self, manager: ArtifactManager, artifact_root: Path
    ) -> None:
        """Loading a nonexistent artifact raises ArtifactError."""
        uri = str(artifact_root / "nonexistent.joblib")
        with pytest.raises(ArtifactError, match="Artifact not found"):
            manager.load_artifact(uri)

    def test_verify_hash_missing_returns_false(
        self, manager: ArtifactManager, artifact_root: Path
    ) -> None:
        """verify_hash returns False for a missing artifact."""
        uri = str(artifact_root / "nonexistent.joblib")
        assert manager.verify_hash(uri, "abc123") is False

    def test_verify_hash_traversal_returns_false(
        self, manager: ArtifactManager
    ) -> None:
        """verify_hash returns False for a traversal path (no exception)."""
        assert manager.verify_hash("../../secret.pkl", "abc123") is False


# ---------------------------------------------------------------------------
# Save path validation tests
# ---------------------------------------------------------------------------


class TestSavePathValidation:
    """Verify that save_artifact also rejects traversal paths."""

    def test_save_traversal_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """Saving to ../../secret.joblib is rejected."""
        model = _FakeModel()
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.save_artifact(model, "../../secret.joblib")

    def test_save_absolute_path_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """Saving to an absolute external path is rejected."""
        model = _FakeModel()
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.save_artifact(model, "/tmp/external.joblib")

    def test_save_repeated_traversal_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """Saving to ../../../artifact.pkl is rejected."""
        model = _FakeModel()
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.save_artifact(model, "../../../artifact.pkl")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
    def test_save_windows_drive_rejected(
        self, manager: ArtifactManager
    ) -> None:
        """Saving to C:\\ outside root is rejected."""
        model = _FakeModel()
        with pytest.raises(ArtifactError, match="escapes the artifact root"):
            manager.save_artifact(model, "C:\\Windows\\evil.pkl")


# ---------------------------------------------------------------------------
# Existing Stage 8 regression tests
# ---------------------------------------------------------------------------


class TestExistingBehavior:
    """Ensure existing valid artifact operations still work."""

    def test_artifact_root_property(
        self, manager: ArtifactManager, artifact_root: Path
    ) -> None:
        """artifact_root property returns the configured root."""
        assert manager.artifact_root == artifact_root

    def test_save_and_load_roundtrip(
        self, manager: ArtifactManager
    ) -> None:
        """Save and load a model artifact roundtrip."""
        model = _FakeModel()
        uri, sha256 = manager.save_artifact(model, "roundtrip/model.joblib")
        loaded = manager.load_artifact(uri, expected_sha256=sha256)
        assert loaded is not None

    def test_save_with_metadata(
        self, manager: ArtifactManager
    ) -> None:
        """Metadata is bundled with the artifact."""
        model = _FakeModel()
        metadata = {"version": "1.0", "type": "test"}
        uri, sha256 = manager.save_artifact(
            model, "meta/model.joblib", metadata=metadata
        )
        # Load should succeed (metadata doesn't affect model extraction).
        loaded = manager.load_artifact(uri, expected_sha256=sha256)
        assert loaded is not None

    def test_verify_hash_true_for_valid(
        self, manager: ArtifactManager, saved_artifact: tuple[str, str]
    ) -> None:
        """verify_hash returns True for a valid artifact."""
        uri, sha256 = saved_artifact
        assert manager.verify_hash(uri, sha256) is True

    def test_verify_hash_false_for_wrong_hash(
        self, manager: ArtifactManager, saved_artifact: tuple[str, str]
    ) -> None:
        """verify_hash returns False for a wrong hash."""
        uri, _ = saved_artifact
        assert manager.verify_hash(uri, "wrong_hash_value_1234567890abcdef") is False
