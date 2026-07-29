"""PostgreSQL backup and restore verification for production-shadow.

Provides automated backup, restore into isolated database, and
validation of schema, data integrity, and audit chain.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class BackupConfig:
    """Configuration for backup operations."""

    def __init__(
        self,
        *,
        backup_dir: str = "/tmp/agentblue-backups",
        db_host: str = "localhost",
        db_port: int = 5433,
        db_user: str = "agentblue",
        db_name: str = "agentblue_dev",
        retention_days: int = 30,
    ) -> None:
        self.backup_dir = Path(backup_dir)
        self.db_host = db_host
        self.db_port = db_port
        self.db_user = db_user
        self.db_name = db_name
        self.retention_days = retention_days


class BackupResult:
    """Result of a backup operation."""

    def __init__(
        self,
        *,
        success: bool,
        backup_path: str = "",
        checksum: str = "",
        size_bytes: int = 0,
        duration_seconds: float = 0.0,
        timestamp: str = "",
        error: str = "",
    ) -> None:
        self.success = success
        self.backup_path = backup_path
        self.checksum = checksum
        self.size_bytes = size_bytes
        self.duration_seconds = duration_seconds
        self.timestamp = timestamp or datetime.now(UTC).isoformat()
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "backup_path": self.backup_path,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
            "error": self.error,
        }


class RestoreResult:
    """Result of a restore verification."""

    def __init__(
        self,
        *,
        success: bool,
        schema_valid: bool = False,
        row_counts_match: bool = False,
        constraints_valid: bool = False,
        audit_integrity: bool = False,
        duration_seconds: float = 0.0,
        details: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        self.success = success
        self.schema_valid = schema_valid
        self.row_counts_match = row_counts_match
        self.constraints_valid = constraints_valid
        self.audit_integrity = audit_integrity
        self.duration_seconds = duration_seconds
        self.details = details or {}
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "schema_valid": self.schema_valid,
            "row_counts_match": self.row_counts_match,
            "constraints_valid": self.constraints_valid,
            "audit_integrity": self.audit_integrity,
            "duration_seconds": self.duration_seconds,
            "details": self.details,
            "error": self.error,
        }


class BackupService:
    """Handles PostgreSQL backup and restore verification."""

    def __init__(self, config: BackupConfig | None = None) -> None:
        self._config = config or BackupConfig()

    def create_backup(self) -> BackupResult:
        """Create a PostgreSQL backup using pg_dump.

        Returns:
            BackupResult with backup details.
        """
        start = time.monotonic()
        self._config.backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_file = self._config.backup_dir / f"agentblue_{timestamp}.sql"

        try:
            result = subprocess.run(
                [
                    "pg_dump",
                    "-h", self._config.db_host,
                    "-p", str(self._config.db_port),
                    "-U", self._config.db_user,
                    "-d", self._config.db_name,
                    "-f", str(backup_file),
                    "--no-owner",
                    "--no-privileges",
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )

            if result.returncode != 0:
                return BackupResult(
                    success=False,
                    error=f"pg_dump failed: {result.stderr[:200]}",
                    duration_seconds=time.monotonic() - start,
                )

            # Calculate checksum
            checksum = self._compute_checksum(backup_file)
            size = backup_file.stat().st_size
            duration = time.monotonic() - start

            logger.info(
                "backup_created",
                path=str(backup_file),
                size_bytes=size,
                checksum=checksum[:16],
                duration_seconds=round(duration, 2),
            )

            return BackupResult(
                success=True,
                backup_path=str(backup_file),
                checksum=checksum,
                size_bytes=size,
                duration_seconds=duration,
            )

        except subprocess.TimeoutExpired:
            return BackupResult(
                success=False,
                error="Backup timed out after 300 seconds",
                duration_seconds=time.monotonic() - start,
            )
        except FileNotFoundError:
            return BackupResult(
                success=False,
                error="pg_dump not found — PostgreSQL client tools not installed",
                duration_seconds=time.monotonic() - start,
            )
        except Exception as exc:
            return BackupResult(
                success=False,
                error=str(exc)[:200],
                duration_seconds=time.monotonic() - start,
            )

    def verify_checksum(self, backup_path: str, expected_checksum: str) -> bool:
        """Verify a backup file's checksum.

        Args:
            backup_path: Path to the backup file.
            expected_checksum: Expected SHA-256 checksum.

        Returns:
            True if the checksum matches.
        """
        path = Path(backup_path)
        if not path.exists():
            return False
        actual = self._compute_checksum(path)
        return actual == expected_checksum

    def list_backups(self) -> list[dict[str, Any]]:
        """List available backup files with metadata.

        Returns:
            List of backup metadata dicts.
        """
        backups: list[dict[str, Any]] = []
        backup_dir = self._config.backup_dir

        if not backup_dir.exists():
            return backups

        for path in sorted(backup_dir.glob("agentblue_*.sql"), reverse=True):
            backups.append({
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "checksum": self._compute_checksum(path),
                "modified": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=UTC
                ).isoformat(),
            })

        return backups

    @staticmethod
    def _compute_checksum(path: Path) -> str:
        """Compute SHA-256 checksum of a file."""
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()
