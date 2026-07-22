"""QuickBooks-specific exceptions."""

from __future__ import annotations


class QuickBooksError(Exception):
    """Base exception for QuickBooks integration."""


class QuickBooksConfigurationError(QuickBooksError):
    """Raised when QuickBooks configuration is missing or invalid."""


class QuickBooksOAuthError(QuickBooksError):
    """Raised when OAuth authorization URL generation fails."""
