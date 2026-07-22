"""QuickBooks-specific exceptions."""

from __future__ import annotations


class QuickBooksError(Exception):
    """Base exception for QuickBooks integration."""


class QuickBooksConfigurationError(QuickBooksError):
    """Raised when QuickBooks configuration is missing or invalid."""


class QuickBooksOAuthError(QuickBooksError):
    """Raised when OAuth authorization URL generation fails."""


class QuickBooksCallbackError(QuickBooksError):
    """Raised when OAuth callback parameters are invalid or missing."""


class QuickBooksStateMismatchError(QuickBooksCallbackError):
    """Raised when the callback state does not match the expected state."""


class QuickBooksTokenExchangeError(QuickBooksError):
    """Raised when authorization-code exchange fails."""


class QuickBooksTokenRefreshError(QuickBooksError):
    """Raised when token refresh fails."""


class QuickBooksTokenStorageError(QuickBooksError):
    """Raised when token persistence operations fail."""
