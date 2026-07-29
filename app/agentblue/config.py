"""Application configuration via pydantic-settings.

Validates environment-specific requirements. Production and
production-shadow modes fail fast on insecure defaults.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    log_level: str = "INFO"

    # PostgreSQL connection components
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "agentblue"
    db_password: str = "agentblue"
    db_name: str = "agentblue_dev"

    # Production-shadow safety flags
    accounting_execution_mode: str = "shadow"
    automatic_approval_enabled: bool = False
    autonomous_writeback_enabled: bool = False
    ml_promotion_enabled: bool = False

    # JWT configuration
    jwt_secret_key: str = ""

    # Rate limiting
    rate_limit_enabled: bool = True

    # Worker configuration
    worker_enabled: bool = False
    worker_heartbeat_interval_seconds: int = 30
    worker_lease_duration_seconds: int = 300
    worker_max_batch_size: int = 50

    @property
    def database_url(self) -> str:
        """Compose the async database URL from individual components."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.app_env == "development"

    @property
    def is_production_shadow(self) -> bool:
        """Check if running in production-shadow mode."""
        return self.app_env == "production-shadow"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.app_env == "production"

    @property
    def is_staging(self) -> bool:
        """Check if running in staging mode."""
        return self.app_env == "staging"

    @property
    def requires_strict_validation(self) -> bool:
        """Whether the current environment requires strict validation."""
        return self.app_env in ("production", "production-shadow", "staging")

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        allowed = {"development", "test", "staging", "production-shadow", "production"}
        if v not in allowed:
            raise ValueError(f"app_env must be one of {allowed}, got '{v}'")
        return v

    @field_validator("accounting_execution_mode")
    @classmethod
    def validate_execution_mode(cls, v: str) -> str:
        allowed = {"shadow", "simulated", "approved-only"}
        if v not in allowed:
            raise ValueError(
                f"accounting_execution_mode must be one of {allowed}, got '{v}'"
            )
        return v

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        """Enforce production-shadow safety constraints."""
        if not self.requires_strict_validation:
            return self

        # Production and production-shadow require JWT secret
        if not self.jwt_secret_key:
            raise ValueError(
                "jwt_secret_key is required in production/production-shadow/staging"
            )
        if len(self.jwt_secret_key) < 32:
            raise ValueError("jwt_secret_key must be at least 32 characters")

        # Prohibited capabilities in production-shadow
        if self.is_production_shadow or self.is_production:
            if self.automatic_approval_enabled:
                raise ValueError(
                    "automatic_approval_enabled cannot be true in "
                    f"{self.app_env}"
                )
            if self.autonomous_writeback_enabled:
                raise ValueError(
                    "autonomous_writeback_enabled cannot be true in "
                    f"{self.app_env}"
                )
            if self.ml_promotion_enabled:
                raise ValueError(
                    "ml_promotion_enabled cannot be true in "
                    f"{self.app_env}"
                )

        return self

    def get_mode_summary(self) -> dict[str, object]:
        """Return a safe summary of the current mode (no secrets)."""
        return {
            "environment": self.app_env,
            "accounting_execution_mode": self.accounting_execution_mode,
            "automatic_approval_enabled": self.automatic_approval_enabled,
            "autonomous_writeback_enabled": self.autonomous_writeback_enabled,
            "ml_promotion_enabled": self.ml_promotion_enabled,
            "worker_enabled": self.worker_enabled,
            "rate_limit_enabled": self.rate_limit_enabled,
            "log_level": self.log_level,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
