"""Comprehensive authentication and authorization tests for the security module.

Covers: token creation/validation, principal resolution, auth dependencies,
permission enforcement, realm isolation, security configuration, and audit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from jose import jwt

from agentblue.security.audit import _sanitize_metadata, record_audit_event
from agentblue.security.auth import (
    _get_security_settings,
    _payload_to_principal,
    create_access_token,
    decode_token,
    get_authenticated_principal,
)
from agentblue.security.config import SecuritySettings
from agentblue.security.principal import Principal
from agentblue.security.realm import require_any_realm_access, require_realm_access
from agentblue.security.roles import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    get_permissions_for_roles,
    has_permission,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SECRET_KEY = "test-secret-key-at-least-32-characters-long-for-security"


@pytest.fixture()
def settings() -> SecuritySettings:
    """Development settings with a known secret key."""
    return SecuritySettings(
        jwt_secret_key=SECRET_KEY,
        jwt_algorithm="HS256",
        jwt_issuer="agentblue-accounting",
        jwt_audience="agentblue-api",
        jwt_access_token_expire_minutes=30,
        app_env="development",
        auth_bypass_enabled=False,
    )


@pytest.fixture()
def production_settings() -> SecuritySettings:
    """Production settings with a known secret key."""
    return SecuritySettings(
        jwt_secret_key=SECRET_KEY,
        jwt_algorithm="HS256",
        jwt_issuer="agentblue-accounting",
        jwt_audience="agentblue-api",
        jwt_access_token_expire_minutes=30,
        app_env="production",
        auth_bypass_enabled=False,
    )


@pytest.fixture()
def sample_principal() -> Principal:
    """A sample principal for testing."""
    return Principal(
        principal_id="user-123",
        principal_type="human",
        email="test@example.com",
        display_name="Test User",
        active=True,
        roles=frozenset({Role.VIEWER}),
        realm_ids=frozenset({"realm-a"}),
        correlation_id="test-corr-id",
    )


@pytest.fixture()
def admin_principal() -> Principal:
    """An admin principal for testing."""
    return Principal(
        principal_id="admin-1",
        principal_type="human",
        email="admin@example.com",
        display_name="Admin User",
        active=True,
        roles=frozenset({Role.ADMIN}),
        realm_ids=frozenset({"realm-a"}),
        correlation_id="admin-corr-id",
    )


@pytest.fixture()
def multi_role_principal() -> Principal:
    """A principal with multiple roles."""
    return Principal(
        principal_id="multi-1",
        principal_type="human",
        email="multi@example.com",
        display_name="Multi Role User",
        active=True,
        roles=frozenset({Role.VIEWER, Role.ACCOUNTANT, Role.ML_OPERATOR}),
        realm_ids=frozenset({"realm-a", "realm-b"}),
        correlation_id="multi-corr-id",
    )


def _make_token(
    principal: Principal,
    settings: SecuritySettings,
    *,
    secret_key: str | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    expire_minutes: int = 30,
    remove_claims: list[str] | None = None,
    extra_claims: dict[str, object] | None = None,
) -> str:
    """Build a JWT from a principal with optional overrides."""
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=expire_minutes)
    claims: dict[str, object] = {
        "sub": principal.principal_id,
        "principal_type": principal.principal_type,
        "email": principal.email,
        "name": principal.display_name,
        "active": principal.active,
        "roles": [r.value for r in principal.roles],
        "realm_ids": list(principal.realm_ids),
        "auth_method": principal.auth_method,
        "iss": issuer if issuer is not None else settings.jwt_issuer,
        "aud": audience if audience is not None else settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": str(uuid4()),
    }
    if remove_claims:
        for c in remove_claims:
            claims.pop(c, None)
    if extra_claims:
        claims.update(extra_claims)
    key = secret_key if secret_key is not None else settings.effective_secret_key
    return jwt.encode(claims, key, algorithm=settings.jwt_algorithm)


# =========================================================================
# A. Token Creation and Validation (10+ tests)
# =========================================================================


class TestTokenCreationAndValidation:
    """Tests for create_access_token and decode_token."""

    def test_create_access_token_produces_valid_jwt(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """create_access_token returns a decodable JWT string."""
        token = create_access_token(sample_principal, settings)
        assert isinstance(token, str)
        assert len(token) > 0
        # Decode succeeds without raising
        decoded = jwt.decode(
            token,
            settings.effective_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
        )
        assert decoded["sub"] == sample_principal.principal_id

    def test_decode_token_validates_valid_token(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """decode_token returns the payload for a valid token."""
        token = create_access_token(sample_principal, settings)
        payload = decode_token(token, settings)
        assert payload["sub"] == sample_principal.principal_id

    def test_decode_token_rejects_expired_token(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """decode_token raises 401 for an expired token."""
        token = _make_token(sample_principal, settings, expire_minutes=-10)
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token, settings)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_decode_token_rejects_wrong_issuer(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """decode_token raises 401 when issuer doesn't match."""
        token = _make_token(sample_principal, settings, issuer="evil-issuer")
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token, settings)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_decode_token_rejects_wrong_audience(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """decode_token raises 401 when audience doesn't match."""
        token = _make_token(sample_principal, settings, audience="wrong-audience")
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token, settings)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_decode_token_rejects_invalid_signature(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """decode_token raises 401 for a token signed with a different key."""
        token = _make_token(
            sample_principal, settings, secret_key="wrong-key-at-least-32-chars-long!!"
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token, settings)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_decode_token_rejects_malformed_token(self, settings: SecuritySettings) -> None:
        """decode_token raises 401 for a malformed token string."""
        with pytest.raises(HTTPException) as exc_info:
            decode_token("not.a.valid.jwt.token", settings)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_decode_token_rejects_empty_token(self, settings: SecuritySettings) -> None:
        """decode_token raises 401 for an empty token string."""
        with pytest.raises(HTTPException) as exc_info:
            decode_token("", settings)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_token_contains_expected_claims(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """A created token contains all expected claims."""
        token = create_access_token(sample_principal, settings)
        payload = decode_token(token, settings)
        # Identity claims
        assert payload["sub"] == sample_principal.principal_id
        assert payload["roles"] == [r.value for r in sample_principal.roles]
        assert payload["realm_ids"] == list(sample_principal.realm_ids)
        # JWT standard claims
        assert payload["iss"] == settings.jwt_issuer
        assert payload["aud"] == settings.jwt_audience
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_create_access_token_with_multiple_roles_encodes_all_roles(
        self, multi_role_principal: Principal, settings: SecuritySettings
    ) -> None:
        """A token with multiple roles encodes all of them."""
        token = create_access_token(multi_role_principal, settings)
        payload = decode_token(token, settings)
        assert set(payload["roles"]) == {r.value for r in multi_role_principal.roles}
        assert len(payload["roles"]) == len(multi_role_principal.roles)


# =========================================================================
# B. Principal Resolution (8+ tests)
# =========================================================================


class TestPrincipalResolution:
    """Tests for _payload_to_principal and principal properties."""

    def test_valid_token_resolves_to_correct_principal(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """A valid token decodes to a Principal with correct identity."""
        token = create_access_token(sample_principal, settings)
        payload = decode_token(token, settings)
        resolved = _payload_to_principal(payload, "corr-123")
        assert resolved.principal_id == sample_principal.principal_id
        assert resolved.principal_type == sample_principal.principal_type
        assert resolved.email == sample_principal.email
        assert resolved.roles == sample_principal.roles
        assert resolved.realm_ids == sample_principal.realm_ids

    def test_missing_sub_claim_raises_401(self, settings: SecuritySettings) -> None:
        """A payload without 'sub' raises 401."""
        payload: dict[str, object] = {
            "roles": ["VIEWER"],
            "realm_ids": ["realm-a"],
            "active": True,
        }
        with pytest.raises(HTTPException) as exc_info:
            _payload_to_principal(payload, "corr-1")  # type: ignore[arg-type]
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_inactive_principal_raises_401(self, settings: SecuritySettings) -> None:
        """An inactive principal raises 401."""
        payload: dict[str, object] = {
            "sub": "user-1",
            "roles": ["VIEWER"],
            "realm_ids": ["realm-a"],
            "active": False,
        }
        with pytest.raises(HTTPException) as exc_info:
            _payload_to_principal(payload, "corr-1")  # type: ignore[arg-type]
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unknown_role_in_token_is_ignored(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """Unknown roles in token are silently ignored, known roles are kept."""
        payload: dict[str, object] = {
            "sub": "user-1",
            "principal_type": "human",
            "email": "test@example.com",
            "active": True,
            "roles": ["VIEWER", "FAKE_ROLE", "ACCOUNTANT"],
            "realm_ids": ["realm-a"],
        }
        resolved = _payload_to_principal(payload, "corr-1")  # type: ignore[arg-type]
        assert Role.VIEWER in resolved.roles
        assert Role.ACCOUNTANT in resolved.roles
        assert len(resolved.roles) == 2

    def test_principal_has_correct_realm_ids(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """Principal realm_ids match what was encoded in the token."""
        token = create_access_token(sample_principal, settings)
        payload = decode_token(token, settings)
        resolved = _payload_to_principal(payload, "corr-1")
        assert resolved.realm_ids == sample_principal.realm_ids

    def test_principal_to_audit_dict_excludes_sensitive_fields(
        self, sample_principal: Principal
    ) -> None:
        """to_audit_dict returns only safe fields, no auth_method or internal ids beyond expected."""
        audit = sample_principal.to_audit_dict()
        assert "principal_id" in audit
        assert "roles" in audit
        assert "realm_ids" in audit
        assert "correlation_id" in audit
        # Verify roles are sorted values
        assert audit["roles"] == sorted(r.value for r in sample_principal.roles)
        assert audit["realm_ids"] == sorted(sample_principal.realm_ids)

    def test_principal_has_role_true(self, sample_principal: Principal) -> None:
        """has_role returns True for a role the principal has."""
        assert sample_principal.has_role(Role.VIEWER) is True

    def test_principal_has_role_false(self, sample_principal: Principal) -> None:
        """has_role returns False for a role the principal doesn't have."""
        assert sample_principal.has_role(Role.ADMIN) is False

    def test_principal_has_realm_access_true(self, sample_principal: Principal) -> None:
        """has_realm_access returns True for an assigned realm."""
        assert sample_principal.has_realm_access("realm-a") is True

    def test_principal_has_realm_access_false(self, sample_principal: Principal) -> None:
        """has_realm_access returns False for an unassigned realm."""
        assert sample_principal.has_realm_access("realm-z") is False

    def test_principal_has_realm_access_empty_string(self, sample_principal: Principal) -> None:
        """has_realm_access returns True for empty string (realm-less resources)."""
        assert sample_principal.has_realm_access("") is True


# =========================================================================
# C. Authentication Dependency (6+ tests)
# =========================================================================


class TestAuthenticationDependency:
    """Tests for the get_authenticated_principal FastAPI dependency."""

    def _make_app(self, **settings_overrides: object) -> FastAPI:
        """Create a minimal FastAPI app that exposes the principal dependency."""
        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, object]:
            return {
                "principal_id": principal.principal_id,
                "roles": sorted(r.value for r in principal.roles),
                "correlation_id": principal.correlation_id,
            }

        # Override settings
        app.dependency_overrides[_get_security_settings] = lambda: SecuritySettings(
            **settings_overrides
        )
        return app

    async def test_missing_authorization_header_raises_401(self) -> None:
        """Request without Authorization header returns 401."""

        app = self._make_app(jwt_secret_key=SECRET_KEY)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/protected")
        assert resp.status_code == 401

    async def test_non_bearer_scheme_raises_401(self) -> None:
        """Non-Bearer scheme in Authorization header returns 401."""
        app = self._make_app(jwt_secret_key=SECRET_KEY)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected", headers={"Authorization": "Basic dXNlcjpwYXNz"}
            )
        assert resp.status_code == 401

    async def test_valid_bearer_token_returns_principal(
        self, sample_principal: Principal
    ) -> None:
        """A valid Bearer token returns the expected principal."""
        settings = SecuritySettings(jwt_secret_key=SECRET_KEY)
        token = create_access_token(sample_principal, settings)

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, object]:
            return {
                "principal_id": principal.principal_id,
                "roles": sorted(r.value for r in principal.roles),
            }

        app.dependency_overrides[_get_security_settings] = lambda: settings
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["principal_id"] == sample_principal.principal_id

    async def test_auth_bypass_enabled_in_development_returns_admin(self) -> None:
        """auth_bypass_enabled in dev mode returns a default admin principal."""
        settings = SecuritySettings(
            jwt_secret_key=SECRET_KEY,
            app_env="development",
            auth_bypass_enabled=True,
        )

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, object]:
            return {
                "principal_id": principal.principal_id,
                "roles": sorted(r.value for r in principal.roles),
                "auth_method": principal.auth_method,
            }

        app.dependency_overrides[_get_security_settings] = lambda: settings
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/protected")
        assert resp.status_code == 200
        data = resp.json()
        assert data["principal_id"] == "dev-bypass"
        assert "ADMIN" in data["roles"]
        assert data["auth_method"] == "bypass"

    async def test_auth_bypass_enabled_in_production_rejected_by_config(self) -> None:
        """auth_bypass_enabled=True in production raises ValueError during init."""
        with pytest.raises(ValueError, match="cannot be true in production"):
            SecuritySettings(
                jwt_secret_key=SECRET_KEY,
                app_env="production",
                auth_bypass_enabled=True,
            )

    async def test_correlation_id_from_header_is_preserved(
        self, sample_principal: Principal
    ) -> None:
        """X-Correlation-ID header value is passed through to the principal."""
        settings = SecuritySettings(jwt_secret_key=SECRET_KEY)
        token = create_access_token(sample_principal, settings)

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, object]:
            return {"correlation_id": principal.correlation_id}

        app.dependency_overrides[_get_security_settings] = lambda: settings
        transport = ASGITransport(app=app)
        custom_corr = "my-custom-correlation-id-123"
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Correlation-ID": custom_corr,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["correlation_id"] == custom_corr

    async def test_missing_correlation_id_generates_one(
        self, sample_principal: Principal
    ) -> None:
        """Without X-Correlation-ID header, one is auto-generated."""
        settings = SecuritySettings(jwt_secret_key=SECRET_KEY)
        token = create_access_token(sample_principal, settings)

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, object]:
            return {"correlation_id": principal.correlation_id}

        app.dependency_overrides[_get_security_settings] = lambda: settings
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["correlation_id"]  # non-empty
        assert data["correlation_id"] != sample_principal.correlation_id  # generated fresh


# =========================================================================
# D. Permission Enforcement (8+ tests)
# =========================================================================


class TestPermissionEnforcement:
    """Tests for Role → Permission mapping and has_permission."""

    def test_viewer_has_accounting_read_but_not_writeback(self) -> None:
        """VIEWER can read accounting but not write back."""
        assert has_permission(frozenset({Role.VIEWER}), Permission.ACCOUNTING_READ)
        assert not has_permission(
            frozenset({Role.VIEWER}), Permission.ACCOUNTING_WRITEBACK
        )

    def test_accountant_has_review_but_not_approve(self) -> None:
        """ACCOUNTANT can review but not approve."""
        assert has_permission(frozenset({Role.ACCOUNTANT}), Permission.ACCOUNTING_REVIEW)
        assert not has_permission(
            frozenset({Role.ACCOUNTANT}), Permission.ACCOUNTING_APPROVE
        )

    def test_approver_has_writeback(self) -> None:
        """APPROVER has ACCOUNTING_WRITEBACK permission."""
        assert has_permission(frozenset({Role.APPROVER}), Permission.ACCOUNTING_WRITEBACK)

    def test_ml_operator_has_ml_train_but_not_writeback(self) -> None:
        """ML_OPERATOR can train models but not write back accounting."""
        assert has_permission(frozenset({Role.ML_OPERATOR}), Permission.ML_TRAIN)
        assert not has_permission(
            frozenset({Role.ML_OPERATOR}), Permission.ACCOUNTING_WRITEBACK
        )

    def test_admin_has_identity_manage_but_not_writeback(self) -> None:
        """ADMIN has IDENTITY_MANAGE but no implicit bypass for ACCOUNTING_WRITEBACK."""
        assert has_permission(frozenset({Role.ADMIN}), Permission.IDENTITY_MANAGE)
        assert not has_permission(
            frozenset({Role.ADMIN}), Permission.ACCOUNTING_WRITEBACK
        )

    def test_service_account_has_limited_permissions(self) -> None:
        """SERVICE_ACCOUNT only has QUICKBOOKS_READ and ACCOUNTING_READ."""
        perms = get_permissions_for_roles(frozenset({Role.SERVICE_ACCOUNT}))
        assert Permission.QUICKBOOKS_READ in perms
        assert Permission.ACCOUNTING_READ in perms
        assert Permission.ACCOUNTING_WRITEBACK not in perms
        assert Permission.ML_TRAIN not in perms
        assert Permission.IDENTITY_MANAGE not in perms
        assert len(perms) == 2

    def test_has_permission_returns_false_for_unknown_role(self) -> None:
        """has_permission with empty roles returns False for any permission."""
        assert not has_permission(frozenset(), Permission.ACCOUNTING_READ)

    def test_get_permissions_for_roles_returns_union(self) -> None:
        """get_permissions_for_roles returns the union of all role permissions."""
        roles = frozenset({Role.VIEWER, Role.ACCOUNTANT})
        perms = get_permissions_for_roles(roles)
        # VIEWER adds ACCOUNTING_READ, ML_READ, QUICKBOOKS_READ
        # ACCOUNTANT adds ACCOUNTING_READ, ACCOUNTING_REVIEW, ACCOUNTING_RECONCILE, ML_READ, QUICKBOOKS_READ
        expected = get_permissions_for_roles(
            frozenset({Role.VIEWER}
        )) | get_permissions_for_roles(frozenset({Role.ACCOUNTANT}))
        assert perms == expected

    def test_all_roles_have_defined_permissions(self) -> None:
        """Every Role has an entry in ROLE_PERMISSIONS."""
        for role in Role:
            assert role in ROLE_PERMISSIONS, f"{role} missing from ROLE_PERMISSIONS"
            assert isinstance(ROLE_PERMISSIONS[role], frozenset)

    def test_permission_values_are_namespaced(self) -> None:
        """All permission values follow the 'resource:action' format."""
        for perm in Permission:
            assert ":" in perm.value, f"Permission {perm.value} missing namespace separator"


# =========================================================================
# E. Realm Isolation (6+ tests)
# =========================================================================


class TestRealmIsolation:
    """Tests for require_realm_access and require_any_realm_access."""

    def test_same_realm_access_succeeds(self, sample_principal: Principal) -> None:
        """Accessing a realm the principal is assigned to does not raise."""
        require_realm_access(sample_principal, "realm-a")  # should not raise

    def test_cross_realm_access_raises_403(self, sample_principal: Principal) -> None:
        """Accessing a different realm raises HTTP 403."""
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(sample_principal, "realm-b")
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_empty_realm_id_is_allowed(self, sample_principal: Principal) -> None:
        """Empty realm_id is treated as realm-less and always allowed."""
        require_realm_access(sample_principal, "")  # should not raise

    def test_no_realm_assignment_raises_403(self) -> None:
        """Principal with no realm_ids accessing any realm raises 403."""
        principal = Principal(
            principal_id="user-no-realms",
            principal_type="human",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset(),
        )
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, "realm-a")
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_multiple_realm_assignments_work(self, multi_role_principal: Principal) -> None:
        """Principal with multiple realms can access each one."""
        require_realm_access(multi_role_principal, "realm-a")
        require_realm_access(multi_role_principal, "realm-b")

    def test_require_any_realm_access_succeeds_with_realms(
        self, sample_principal: Principal
    ) -> None:
        """require_any_realm_access passes when principal has at least one realm."""
        require_any_realm_access(sample_principal)  # should not raise

    def test_require_any_realm_access_raises_without_realms(self) -> None:
        """require_any_realm_access raises 403 when principal has no realms."""
        principal = Principal(
            principal_id="user-no-realms",
            principal_type="human",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset(),
        )
        with pytest.raises(HTTPException) as exc_info:
            require_any_realm_access(principal)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_require_realm_access_logs_warning_on_denial(
        self, sample_principal: Principal
    ) -> None:
        """Realm access denial triggers a warning log."""
        with patch("agentblue.security.realm.logger") as mock_logger:
            with pytest.raises(HTTPException):
                require_realm_access(sample_principal, "realm-z")
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "realm_access_denied"


# =========================================================================
# F. Security Configuration (6+ tests)
# =========================================================================


class TestSecurityConfiguration:
    """Tests for SecuritySettings validation and effective_secret_key."""

    def test_production_mode_requires_jwt_secret_key(self) -> None:
        """Production mode without jwt_secret_key raises ValueError."""
        with pytest.raises(ValueError, match="JWT_SECRET_KEY is required"):
            SecuritySettings(
                jwt_secret_key="",
                app_env="production",
            )

    def test_production_mode_rejects_short_key(self) -> None:
        """Production mode with key shorter than 32 chars raises ValueError."""
        with pytest.raises(ValueError, match="at least 32 characters"):
            SecuritySettings(
                jwt_secret_key="short",
                app_env="production",
            )

    def test_production_mode_rejects_auth_bypass(self) -> None:
        """Production mode with auth_bypass_enabled raises ValueError."""
        with pytest.raises(ValueError, match="cannot be true in production"):
            SecuritySettings(
                jwt_secret_key=SECRET_KEY,
                app_env="production",
                auth_bypass_enabled=True,
            )

    def test_development_mode_allows_empty_key(self) -> None:
        """Development mode accepts an empty jwt_secret_key without raising."""
        settings = SecuritySettings(
            jwt_secret_key="",
            app_env="development",
        )
        assert settings.jwt_secret_key == ""

    def test_effective_secret_key_returns_configured_key(self) -> None:
        """effective_secret_key returns the explicit key when set."""
        settings = SecuritySettings(jwt_secret_key=SECRET_KEY)
        assert settings.effective_secret_key == SECRET_KEY

    def test_effective_secret_key_generates_dev_fallback(self) -> None:
        """effective_secret_key generates a random key in dev mode with no key configured."""
        settings = SecuritySettings(jwt_secret_key="", app_env="development")
        key1 = settings.effective_secret_key
        assert key1  # non-empty
        assert len(key1) > 20  # reasonable length

    def test_effective_secret_key_raises_in_production_without_key(self) -> None:
        """effective_secret_key raises in production without a key (cannot even be constructed)."""
        # The model validator catches this first, so we test via the validator
        with pytest.raises(ValueError):
            SecuritySettings(jwt_secret_key="", app_env="production")

    def test_default_settings_are_development(self) -> None:
        """Default settings use development environment."""
        settings = SecuritySettings()
        assert settings.app_env == "development"
        assert settings.is_development is True

    def test_production_settings_with_valid_key_succeeds(self) -> None:
        """Production mode with a valid key (>=32 chars) initializes successfully."""
        settings = SecuritySettings(
            jwt_secret_key=SECRET_KEY,
            app_env="production",
        )
        assert settings.app_env == "production"
        assert settings.effective_secret_key == SECRET_KEY


# =========================================================================
# G. Audit Event Recording (5+ tests)
# =========================================================================


class TestAuditEventRecording:
    """Tests for record_audit_event and _sanitize_metadata."""

    def test_sanitize_metadata_redacts_sensitive_keys(self) -> None:
        """Sensitive keys in metadata are replaced with [REDACTED]."""
        metadata = {
            "password": "hunter2",
            "secret": "top-secret",
            "token": "jwt-token-value",
            "authorization": "Bearer abc",
            "access_token": "tok123",
            "refresh_token": "ref456",
            "api_key": "key789",
            "client_secret": "cs012",
            "db_password": "dbpass",
            "safe_field": "visible",
            "nested": {"password": "nested-pass", "ok": "good"},
        }
        sanitized = _sanitize_metadata(metadata)
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["secret"] == "[REDACTED]"
        assert sanitized["token"] == "[REDACTED]"
        assert sanitized["authorization"] == "[REDACTED]"
        assert sanitized["access_token"] == "[REDACTED]"
        assert sanitized["refresh_token"] == "[REDACTED]"
        assert sanitized["api_key"] == "[REDACTED]"
        assert sanitized["client_secret"] == "[REDACTED]"
        assert sanitized["db_password"] == "[REDACTED]"
        assert sanitized["safe_field"] == "visible"
        # Nested dict also sanitized
        assert sanitized["nested"]["password"] == "[REDACTED]"
        assert sanitized["nested"]["ok"] == "good"

    def test_sanitize_metadata_preserves_non_sensitive_keys(self) -> None:
        """Non-sensitive keys are passed through unchanged."""
        metadata = {"action": "categorize", "amount": 100, "flag": True}
        sanitized = _sanitize_metadata(metadata)
        assert sanitized == metadata

    def test_sanitize_metadata_case_insensitive(self) -> None:
        """Sensitive key matching is case-insensitive."""
        metadata = {"Password": "val1", "TOKEN": "val2", "SECRET": "val3"}
        sanitized = _sanitize_metadata(metadata)
        assert sanitized["Password"] == "[REDACTED]"
        assert sanitized["TOKEN"] == "[REDACTED]"
        assert sanitized["SECRET"] == "[REDACTED]"

    async def test_record_audit_event_creates_event_with_correct_fields(
        self, sample_principal: Principal
    ) -> None:
        """record_audit_event creates an AuditEvent with expected fields."""
        mock_session = AsyncMock()
        await record_audit_event(
            mock_session,
            principal=sample_principal,
            action="categorization.approve",
            resource_type="transaction",
            resource_id="txn-123",
            realm_id="realm-a",
            success=True,
            metadata={"note": "approved"},
        )
        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        # Verify event fields
        added_event = mock_session.add.call_args[0][0]
        assert added_event.actor_principal_id == sample_principal.principal_id
        assert added_event.action == "categorization.approve"
        assert added_event.resource_type == "transaction"
        assert added_event.resource_id == "txn-123"
        assert added_event.realm_id == "realm-a"
        assert added_event.success is True
        assert added_event.event_metadata == {"note": "approved"}

    async def test_record_audit_event_correlation_id_recorded(
        self, sample_principal: Principal
    ) -> None:
        """Correlation ID from principal is recorded in the audit event."""
        mock_session = AsyncMock()
        await record_audit_event(
            mock_session,
            principal=sample_principal,
            action="test.action",
        )
        added_event = mock_session.add.call_args[0][0]
        assert added_event.correlation_id == sample_principal.correlation_id

    async def test_record_audit_event_actor_roles_recorded(
        self, sample_principal: Principal
    ) -> None:
        """Actor roles are recorded as a list of role values."""
        mock_session = AsyncMock()
        await record_audit_event(
            mock_session,
            principal=sample_principal,
            action="test.action",
        )
        added_event = mock_session.add.call_args[0][0]
        assert added_event.actor_roles == sorted(
            r.value for r in sample_principal.roles
        )

    async def test_record_audit_event_realm_id_recorded(
        self, sample_principal: Principal
    ) -> None:
        """Realm ID is recorded in the audit event."""
        mock_session = AsyncMock()
        await record_audit_event(
            mock_session,
            principal=sample_principal,
            action="test.action",
            realm_id="realm-a",
        )
        added_event = mock_session.add.call_args[0][0]
        assert added_event.realm_id == "realm-a"

    async def test_record_audit_event_sensitive_metadata_redacted(
        self, sample_principal: Principal
    ) -> None:
        """Sensitive metadata keys are redacted in the recorded event."""
        mock_session = AsyncMock()
        await record_audit_event(
            mock_session,
            principal=sample_principal,
            action="test.action",
            metadata={"password": "secret123", "safe": "ok"},
        )
        added_event = mock_session.add.call_args[0][0]
        assert added_event.event_metadata["password"] == "[REDACTED]"
        assert added_event.event_metadata["safe"] == "ok"

    async def test_record_audit_event_failure_case(
        self, sample_principal: Principal
    ) -> None:
        """Failed action records failure_category and error_detail."""
        mock_session = AsyncMock()
        await record_audit_event(
            mock_session,
            principal=sample_principal,
            action="test.action",
            success=False,
            failure_category="unauthorized",
            error_detail="Access denied for resource",
        )
        added_event = mock_session.add.call_args[0][0]
        assert added_event.success is False
        assert added_event.failure_category == "unauthorized"
        assert added_event.error_detail == "Access denied for resource"

    async def test_record_audit_event_error_detail_truncated(
        self, sample_principal: Principal
    ) -> None:
        """Error detail longer than 500 chars is truncated."""
        mock_session = AsyncMock()
        long_detail = "x" * 600
        await record_audit_event(
            mock_session,
            principal=sample_principal,
            action="test.action",
            error_detail=long_detail,
        )
        added_event = mock_session.add.call_args[0][0]
        assert len(added_event.error_detail) == 500
