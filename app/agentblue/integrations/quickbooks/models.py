"""QuickBooks OAuth token response models.

Provides typed, validated models for Intuit token responses with
expiration tracking and secret protection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, SecretStr, model_validator

from agentblue.integrations.quickbooks.exceptions import QuickBooksTokenExchangeError


class TokenResponse(BaseModel):
    """Parsed Intuit OAuth token response.

    Sensitive fields use SecretStr to prevent accidental exposure.
    Timestamps are timezone-aware UTC.
    """

    access_token: SecretStr
    refresh_token: SecretStr
    expires_in: int = Field(gt=0)
    x_refresh_token_expires_in: int = Field(gt=0)
    token_type: str = ""
    id_token: SecretStr | None = None
    realm_id: str = ""
    issued_at: datetime | None = None
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="before")
    @classmethod
    def compute_expirations(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Calculate expiration timestamps from issued_at and expires_in."""
        if isinstance(data, dict):
            issued_at = data.get("issued_at")
            expires_in = data.get("expires_in")
            refresh_expires_in = data.get("x_refresh_token_expires_in")

            if issued_at is not None and isinstance(issued_at, int | float):
                issued_dt = datetime.fromtimestamp(issued_at, tz=UTC)
                data["issued_at"] = issued_dt
                if expires_in and isinstance(expires_in, int) and expires_in > 0:
                    data["access_token_expires_at"] = issued_dt + timedelta(seconds=expires_in)
                if (
                    refresh_expires_in
                    and isinstance(refresh_expires_in, int)
                    and refresh_expires_in > 0
                ):
                    data["refresh_token_expires_at"] = issued_dt + timedelta(
                        seconds=refresh_expires_in
                    )

        return data

    @property
    def is_access_token_expired(self) -> bool:
        """Check whether the access token has expired."""
        if self.access_token_expires_at is None:
            return False
        return datetime.now(UTC) >= self.access_token_expires_at

    @property
    def is_refresh_token_expired(self) -> bool:
        """Check whether the refresh token has expired."""
        if self.refresh_token_expires_at is None:
            return False
        return datetime.now(UTC) >= self.refresh_token_expires_at

    def is_access_token_expiring_soon(self, margin_seconds: int = 300) -> bool:
        """Check whether the access token expires within the given margin."""
        if self.access_token_expires_at is None:
            return False
        threshold = datetime.now(UTC) + timedelta(seconds=margin_seconds)
        return threshold >= self.access_token_expires_at

    def __repr__(self) -> str:
        """Redact sensitive fields in repr."""
        return (
            f"TokenResponse("
            f"token_type={self.token_type!r}, "
            f"expires_in={self.expires_in}, "
            f"realm_id={self.realm_id!r})"
        )


def parse_token_response(data: dict[str, Any]) -> TokenResponse:
    """Parse a raw Intuit token response dict into a TokenResponse.

    Raises QuickBooksTokenExchangeError on validation failure.
    """
    try:
        return TokenResponse.model_validate(data)
    except Exception as exc:
        raise QuickBooksTokenExchangeError(
            "Failed to parse token response: invalid or missing fields."
        ) from exc
