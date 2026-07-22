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


# --- Stage 4: API client exceptions ---


class QuickBooksApiError(QuickBooksError):
    """Raised when a QuickBooks API request fails.

    Attributes:
        status_code: HTTP status code from the response.
        intuit_tid: Intuit transaction ID for support correlation.
        fault_message: Intuit fault detail message.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        intuit_tid: str = "",
        fault_message: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.intuit_tid = intuit_tid
        self.fault_message = fault_message


class QuickBooksAuthenticationError(QuickBooksApiError):
    """Raised on 401 Unauthorized from the API."""


class QuickBooksPermissionError(QuickBooksApiError):
    """Raised on 403 Forbidden from the API."""


class QuickBooksResourceNotFoundError(QuickBooksApiError):
    """Raised on 404 Not Found from the API."""


class QuickBooksValidationError(QuickBooksApiError):
    """Raised on 400/422 validation errors from the API."""


class QuickBooksRateLimitError(QuickBooksApiError):
    """Raised on 429 Too Many Requests from the API.

    Attributes:
        retry_after: Seconds to wait before retrying, if provided.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float = 0.0,
        status_code: int = 429,
        intuit_tid: str = "",
        fault_message: str = "",
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            intuit_tid=intuit_tid,
            fault_message=fault_message,
        )
        self.retry_after = retry_after


class QuickBooksServerError(QuickBooksApiError):
    """Raised on 5xx server errors from the API."""


class QuickBooksTransportError(QuickBooksApiError):
    """Raised on network/timeout errors reaching the API."""
