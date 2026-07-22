"""QuickBooks OAuth configuration.

Loads QuickBooks-specific settings from environment variables using the
same pydantic-settings pattern as the main application configuration.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentblue.integrations.quickbooks.exceptions import (
    QuickBooksConfigurationError,
)


class QuickBooksEnvironment(str, Enum):
    """Supported QuickBooks environments."""

    SANDBOX = "sandbox"
    PRODUCTION = "production"


# Intuit OAuth endpoints per environment.
_INTUIT_ENDPOINTS: dict[QuickBooksEnvironment, dict[str, str]] = {
    QuickBooksEnvironment.SANDBOX: {
        "authorization": "https://appcenter.intuit.com/connect/oauth2",
        "token": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
    },
    QuickBooksEnvironment.PRODUCTION: {
        "authorization": "https://appcenter.intuit.com/connect/oauth2",
        "token": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
    },
}

_DEFAULT_SCOPES = "com.intuit.quickbooks.accounting"
_ALLOWED_SCHEMES = frozenset({"http", "https"})


class QuickBooksOAuthSettings(BaseSettings):
    """Typed QuickBooks OAuth settings loaded from environment variables.

    Sensitive fields use SecretStr to prevent accidental exposure in logs,
    repr output, or error messages.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="QUICKBOOKS_",
    )

    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    redirect_uri: str = ""
    environment: QuickBooksEnvironment = QuickBooksEnvironment.SANDBOX
    scopes: str = _DEFAULT_SCOPES

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, v: str) -> str:
        """Validate redirect URI using structured URL parsing.

        Requires a scheme (http or https) and a hostname.
        Rejects unsupported schemes, whitespace-only, and malformed values.
        Allows http://localhost for development.
        """
        if not v:
            return v
        if not v.strip():
            raise ValueError("redirect_uri must not be whitespace-only")
        parsed = urlparse(v)
        if not parsed.scheme:
            raise ValueError("redirect_uri must include a scheme (http:// or https://)")
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ValueError(f"redirect_uri scheme must be http or https, got '{parsed.scheme}'")
        if not parsed.hostname:
            raise ValueError("redirect_uri must include a hostname")
        if v != v.strip():
            raise ValueError("redirect_uri must not contain leading or trailing whitespace")
        return v

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, v: str) -> str:
        """Normalize scopes: split on commas or whitespace, strip, deduplicate, sort."""
        if not v:
            return _DEFAULT_SCOPES
        parts: list[str] = []
        for token in v.replace(",", " ").split():
            stripped = token.strip()
            if stripped:
                parts.append(stripped)
        if not parts:
            return _DEFAULT_SCOPES
        return " ".join(sorted(set(parts)))

    def validate_for_oauth(self) -> None:
        """Validate that all required fields are set for OAuth operations.

        Raises QuickBooksConfigurationError with an actionable message
        identifying the missing setting. Never exposes secret values.
        """
        missing: list[str] = []
        if not self.client_id:
            missing.append("QUICKBOOKS_CLIENT_ID")
        if not self.client_secret.get_secret_value():
            missing.append("QUICKBOOKS_CLIENT_SECRET")
        if not self.redirect_uri:
            missing.append("QUICKBOOKS_REDIRECT_URI")
        if missing:
            raise QuickBooksConfigurationError(
                f"Missing required QuickBooks configuration: "
                f"{', '.join(missing)}. "
                f"Set these environment variables before using "
                f"QuickBooks OAuth functionality."
            )
        # Reject explicitly empty scopes after normalization.
        if not self.scopes.strip():
            raise QuickBooksConfigurationError(
                "QuickBooks OAuth scopes must not be empty. "
                "Set QUICKBOOKS_SCOPES to at least one scope."
            )

    @property
    def authorization_endpoint(self) -> str:
        """Return the Intuit authorization endpoint for the current environment."""
        return _INTUIT_ENDPOINTS[self.environment]["authorization"]

    @property
    def token_endpoint(self) -> str:
        """Return the Intuit token endpoint for the current environment."""
        return _INTUIT_ENDPOINTS[self.environment]["token"]

    @property
    def normalized_scopes(self) -> list[str]:
        """Return scopes as a sorted list."""
        return self.scopes.split()


@lru_cache(maxsize=1)
def get_quickbooks_settings() -> QuickBooksOAuthSettings:
    """Return cached QuickBooks OAuth settings singleton."""
    return QuickBooksOAuthSettings()
