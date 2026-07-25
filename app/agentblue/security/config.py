"""Security configuration settings.

Validates signing key, algorithm, issuer, audience, and token lifetime.
Production mode fails closed when required secrets are missing.
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings


class SecuritySettings(BaseSettings):
    """Typed security configuration loaded from environment variables."""

    # JWT signing
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agentblue-accounting"
    jwt_audience: str = "agentblue-api"
    jwt_access_token_expire_minutes: int = 30
    jwt_allowed_clock_skew_seconds: int = 30

    # Environment
    app_env: str = "development"

    # Development bypass (must be explicitly enabled)
    auth_bypass_enabled: bool = False

    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
    }

    @model_validator(mode="after")
    def _validate_production_config(self) -> SecuritySettings:
        """Ensure production mode has required secrets."""
        if self.app_env == "production":
            if not self.jwt_secret_key:
                raise ValueError(
                    "JWT_SECRET_KEY is required in production mode. "
                    "Set it to a cryptographically random string of at least 32 characters."
                )
            if len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be at least 32 characters in production mode."
                )
            if self.auth_bypass_enabled:
                raise ValueError(
                    "AUTH_BYPASS_ENABLED cannot be true in production mode."
                )
        return self

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.app_env == "development"

    @property
    def effective_secret_key(self) -> str:
        """Return the signing key, generating a dev-only fallback if needed.

        In development mode with no key configured, generates a per-process
        random key. In production, the key must be explicitly configured.
        """
        if self.jwt_secret_key:
            return self.jwt_secret_key
        if self.is_development:
            import secrets
            return secrets.token_urlsafe(32)
        raise ValueError("JWT_SECRET_KEY is required")
