"""Categorization exceptions."""

from __future__ import annotations


class CategorizationError(Exception):
    """Base categorization exception."""


class CategorizationNotFoundError(CategorizationError):
    """Categorization record not found."""


class TransactionNotEligibleError(CategorizationError):
    """Transaction is not eligible for categorization."""


class NoValidCandidateError(CategorizationError):
    """No valid account candidates found."""


class InvalidCategorizationStateError(CategorizationError):
    """Invalid state transition attempted."""


class CategorizationConflictError(CategorizationError):
    """Concurrent categorization conflict."""


class RuleValidationError(CategorizationError):
    """Invalid rule configuration."""


class InvalidTargetAccountError(CategorizationError):
    """Target account is invalid or inactive."""


class ReviewConflictError(CategorizationError):
    """Conflicting review action."""
