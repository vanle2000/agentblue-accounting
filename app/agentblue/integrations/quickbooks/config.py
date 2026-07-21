"""QuickBooks OAuth configuration.

Loads QuickBooks-specific settings from environment variables using the
same pydantic-settings pattern as the main application configuration.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

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
    state_secret: SecretStr = SecretStr("")

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, v: str) -> str:
        """Validate redirect URI format when provided."""
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("redirect_uri must start with http:// or https://")
        return v

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, v: str) -> str:
        """Normalize scopes: strip whitespace, deduplicate, sort."""
        if not v:
            return _DEFAULT_SCOPES
        parts = [s.strip() for s in v.split(",") if s.strip()]
        if not parts:
            return _DEFAULT_SCOPES
        return ",".join(sorted(set(parts)))

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
        if not self.state_secret.get_secret_value():
            missing.append("QUICKBOOKS_STATE_SECRET")
        if missing:
            raise QuickBooksConfigurationError(
                f"Missing required QuickBooks configuration: "
                f"{', '.join(missing)}. "
                f"Set these environment variables before using "
                f"QuickBooks OAuth functionality."
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
        return self.scopes.split(",")


@lru_cache(maxsize=1)
def get_quickbooks_settings() -> QuickBooksOAuthSettings:
    """Return cached QuickBooks OAuth settings singleton."""
    return QuickBooksOAuthSettings()
