"""JWT token validation and FastAPI authentication dependencies.

Provides get_authenticated_principal as a FastAPI dependency.
Validates token signature, expiration, issuer, audience.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from agentblue.security.config import SecuritySettings
from agentblue.security.principal import Principal
from agentblue.security.roles import Role

logger = structlog.get_logger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


def _get_security_settings() -> SecuritySettings:
    """Return security settings from environment."""
    return SecuritySettings()


def decode_token(
    token: str,
    settings: SecuritySettings,
) -> dict[str, Any]:
    """Decode and validate a JWT token.

    Args:
        token: The raw JWT string.
        settings: Security configuration.

    Returns:
        The decoded payload dict.

    Raises:
        HTTPException: 401 if the token is invalid.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.effective_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
                "verify_iat": True,
                "leeway": settings.jwt_allowed_clock_skew_seconds,
            },
        )
    except JWTError as exc:
        logger.warning("token_validation_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return payload


def _payload_to_principal(
    payload: dict[str, Any],
    correlation_id: str,
) -> Principal:
    """Convert a decoded JWT payload to a Principal.

    Args:
        payload: Decoded JWT claims.
        correlation_id: Request correlation ID.

    Returns:
        A Principal instance.

    Raises:
        HTTPException: 401 if required claims are missing.
    """
    principal_id = payload.get("sub")
    if not principal_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required 'sub' claim.",
        )

    principal_type = payload.get("principal_type", "human")
    email = payload.get("email", "")
    display_name = payload.get("name", "")
    active = payload.get("active", True)

    if not active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive.",
        )

    # Parse roles from token claims.
    raw_roles = payload.get("roles", [])
    roles: set[Role] = set()
    for r in raw_roles:
        try:
            roles.add(Role(r))
        except ValueError:
            logger.warning("unknown_role_in_token", role=r)

    # Parse realm assignments.
    realm_ids: frozenset[str] = frozenset(payload.get("realm_ids", []))

    auth_method = payload.get("auth_method", "jwt")

    return Principal(
        principal_id=principal_id,
        principal_type=principal_type,
        email=email,
        display_name=display_name,
        active=active,
        roles=frozenset(roles),
        realm_ids=realm_ids,
        auth_method=auth_method,
        correlation_id=correlation_id,
    )


async def get_authenticated_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: SecuritySettings = Depends(_get_security_settings),
) -> Principal:
    """FastAPI dependency that resolves the authenticated principal.

    In development mode with auth_bypass_enabled, returns a default
    admin principal.  In production, always requires a valid token.

    Args:
        request: The incoming HTTP request.
        credentials: Bearer token from the Authorization header.
        settings: Security configuration.

    Returns:
        The authenticated Principal.

    Raises:
        HTTPException: 401 if authentication fails.
    """
    # Get or generate correlation ID.
    correlation_id = request.headers.get("X-Correlation-ID", "")
    if not correlation_id or len(correlation_id) > 128:
        correlation_id = str(uuid.uuid4())

    # Development bypass (must be explicitly enabled).
    if settings.auth_bypass_enabled and settings.is_development:
        logger.warning(
            "auth_bypass_used",
            correlation_id=correlation_id,
            path=str(request.url.path),
        )
        return Principal(
            principal_id="dev-bypass",
            principal_type="human",
            email="dev@agentblue.local",
            display_name="Development Bypass",
            roles=frozenset({Role.ADMIN}),
            realm_ids=frozenset({"dev-realm"}),
            auth_method="bypass",
            correlation_id=correlation_id,
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_token(token, settings)
    principal = _payload_to_principal(payload, correlation_id)

    logger.info(
        "authenticated",
        principal_id=principal.principal_id,
        principal_type=principal.principal_type,
        correlation_id=correlation_id,
    )

    return principal


def create_access_token(
    principal: Principal,
    settings: SecuritySettings,
) -> str:
    """Create a signed JWT access token for a principal.

    Args:
        principal: The principal to encode.
        settings: Security configuration.

    Returns:
        A signed JWT string.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)

    payload = {
        "sub": principal.principal_id,
        "principal_type": principal.principal_type,
        "email": principal.email,
        "name": principal.display_name,
        "active": principal.active,
        "roles": [r.value for r in principal.roles],
        "realm_ids": list(principal.realm_ids),
        "auth_method": principal.auth_method,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": str(uuid.uuid4()),
    }

    return jwt.encode(  # type: ignore[no-any-return]
        payload,
        settings.effective_secret_key,
        algorithm=settings.jwt_algorithm,
    )
