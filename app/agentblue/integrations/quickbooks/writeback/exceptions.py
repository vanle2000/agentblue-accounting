"""Write-back exceptions."""

from __future__ import annotations


class WriteBackError(Exception):
    """Base write-back exception."""


class StaleSyncTokenError(WriteBackError):
    """SyncToken mismatch — entity changed since review."""


class UnsupportedEntityTypeError(WriteBackError):
    """Entity type not supported for write-back."""


class TargetAccountInvalidError(WriteBackError):
    """Target account is inactive or deleted."""


class QuickBooksUpdateFailedError(WriteBackError):
    """QuickBooks API rejected the update."""


class VerificationFailedError(WriteBackError):
    """Post-update verification failed."""


class IdempotencyConflictError(WriteBackError):
    """Duplicate application detected."""
