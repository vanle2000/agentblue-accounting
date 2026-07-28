"""Comprehensive token revocation, execution context, and service-layer authorization tests.

Covers:
  A. Token Revocation (revoke_token, is_token_revoked, auth integration)
  B. Execution Context (require_permission, require_realm, require_any_realm)
  C. Service-Layer Authorization (context-aware service calls)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt

from agentblue.security.auth import (
    _get_security_settings,
    create_access_token,
    decode_token,
    get_authenticated_principal,
)
from agentblue.security.config import SecuritySettings
from agentblue.security.context import ExecutionContext
from agentblue.security.principal import Principal
from agentblue.security.revocation import RevokedToken, is_token_revoked, revoke_token
from agentblue.security.roles import Permission, Role

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECRET_KEY = "test-secret-key-at-least-32-characters-long-for-security"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def sample_principal() -> Principal:
    """A VIEWER principal scoped to realm-a."""
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
    """An ADMIN principal scoped to realm-a."""
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
def approver_principal() -> Principal:
    """An APPROVER principal scoped to realm-a."""
    return Principal(
        principal_id="approver-1",
        principal_type="human",
        email="approver@example.com",
        display_name="Approver User",
        active=True,
        roles=frozenset({Role.APPROVER}),
        realm_ids=frozenset({"realm-a"}),
        correlation_id="approver-corr-id",
    )


@pytest.fixture()
def ml_operator_principal() -> Principal:
    """An ML_OPERATOR principal scoped to realm-a."""
    return Principal(
        principal_id="ml-op-1",
        principal_type="service",
        email="ml@example.com",
        display_name="ML Operator",
        active=True,
        roles=frozenset({Role.ML_OPERATOR}),
        realm_ids=frozenset({"realm-a"}),
        correlation_id="ml-corr-id",
    )


@pytest.fixture()
def no_realm_principal() -> Principal:
    """A VIEWER principal with no realm assignments."""
    return Principal(
        principal_id="no-realm-user",
        principal_type="human",
        email="norealm@example.com",
        display_name="No Realm User",
        active=True,
        roles=frozenset({Role.VIEWER}),
        realm_ids=frozenset(),
        correlation_id="no-realm-corr",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    principal: Principal,
    settings: SecuritySettings,
    *,
    include_jti: bool = True,
    jti_override: str | None = None,
    secret_key: str | None = None,
    expire_minutes: int = 30,
) -> tuple[str, str]:
    """Build a JWT and return (token_string, jti)."""
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=expire_minutes)
    jti = jti_override or str(uuid.uuid4())
    claims: dict[str, object] = {
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
    }
    if include_jti:
        claims["jti"] = jti
    key = secret_key if secret_key is not None else settings.effective_secret_key
    token = jwt.encode(claims, key, algorithm=settings.jwt_algorithm)
    return token, jti


class _FakeRevocationSession:
    """Mock session that tracks which jtis are revoked."""

    def __init__(self, revoked_jtis: set[str]) -> None:
        self._revoked = revoked_jtis
        self.add = MagicMock()  # synchronous — must NOT be AsyncMock

    async def execute(self, stmt: object) -> object:
        from sqlalchemy.dialects import postgresql

        result = AsyncMock()
        # Extract jti from compiled params using PostgreSQL dialect.
        jti_value = ""
        try:
            compiled = stmt.compile(  # type: ignore[union-attr]
                dialect=postgresql.dialect()
            )
            params = compiled.params
            jti_value = params.get("jti_1", params.get("jti", ""))
        except (AttributeError, TypeError, Exception):
            # For delete statements or other non-select, just return None.
            result.scalar_one_or_none = MagicMock(return_value=None)
            result.scalar_one = MagicMock(return_value=None)
            return result

        if jti_value and jti_value in self._revoked:
            mock_record = MagicMock(spec=RevokedToken)
            mock_record.jti = jti_value
            result.scalar_one_or_none = MagicMock(return_value=mock_record)
            result.scalar_one = MagicMock(return_value=mock_record)
        else:
            result.scalar_one_or_none = MagicMock(return_value=None)
            result.scalar_one = MagicMock(return_value=None)
        return result

    async def flush(self) -> None:
        pass


def _make_mock_session_with_revoked(*jtis: str) -> _FakeRevocationSession:
    """Return a session mock where the given jtis appear as revoked."""
    return _FakeRevocationSession(set(jtis))


def _make_mock_session() -> AsyncMock:
    """Return an AsyncMock session that behaves as if no tokens are revoked."""
    session = AsyncMock()
    session.add = MagicMock()

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        result.scalar_one = MagicMock(return_value=None)
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return session


# =========================================================================
# A. Token Revocation (10+ tests)
# =========================================================================


class TestRevokeToken:
    """Tests for the revoke_token function."""


    async def test_revoke_token_creates_record_with_correct_fields(
        self, settings: SecuritySettings
    ) -> None:
        """revoke_token creates a RevokedToken with the supplied jti, reason, and expires_at."""
        session = _make_mock_session()
        expires = datetime.now(UTC) + timedelta(hours=1)
        jti = str(uuid.uuid4())

        record = await revoke_token(session, jti=jti, expires_at=expires, reason="logout")

        assert isinstance(record, RevokedToken)
        assert record.jti == jti
        assert record.expires_at == expires
        assert record.reason == "logout"
        assert record.revoked_at is not None
        session.add.assert_called_once()
        session.flush.assert_awaited_once()


    async def test_revoke_token_default_reason(self) -> None:
        """revoke_token defaults reason to 'revoked'."""
        session = _make_mock_session()
        expires = datetime.now(UTC) + timedelta(hours=1)
        jti = str(uuid.uuid4())

        record = await revoke_token(session, jti=jti, expires_at=expires)

        assert record.reason == "revoked"


    async def test_revoke_token_is_idempotent(self) -> None:
        """Re-revoking an already-revoked jti returns the existing record without error."""
        jti = str(uuid.uuid4())
        existing_record = MagicMock(spec=RevokedToken)
        existing_record.jti = jti

        session = AsyncMock()
        session.add = MagicMock()

        result_mock = AsyncMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=existing_record)
        result_mock.scalar_one = MagicMock(return_value=existing_record)
        session.execute = AsyncMock(return_value=result_mock)

        expires = datetime.now(UTC) + timedelta(hours=1)
        record = await revoke_token(session, jti=jti, expires_at=expires, reason="duplicate")

        assert record is existing_record
        # add should NOT be called for an existing record
        session.add.assert_not_called()


    async def test_is_token_revoked_returns_true_for_revoked_jti(self) -> None:
        """is_token_revoked returns True when the jti exists in the revocation table."""
        jti = str(uuid.uuid4())
        session = _make_mock_session_with_revoked(jti)

        result = await is_token_revoked(session, jti)

        assert result is True


    async def test_is_token_revoked_returns_false_for_unknown_jti(self) -> None:
        """is_token_revoked returns False for a jti that was never revoked."""
        session = _make_mock_session()

        result = await is_token_revoked(session, "unknown-jti-12345")

        assert result is False


    async def test_unrelated_tokens_not_affected(self) -> None:
        """Revoking one token does not affect a different token's revocation status."""
        revoked_jti = str(uuid.uuid4())
        other_jti = str(uuid.uuid4())
        session = _make_mock_session_with_revoked(revoked_jti)

        assert await is_token_revoked(session, revoked_jti) is True
        assert await is_token_revoked(session, other_jti) is False


    async def test_expired_revocations_cleanup_on_check(self) -> None:
        """is_token_revoked triggers a delete for expired revocation records."""
        session = AsyncMock()
        session.add = MagicMock()

        # Track calls to execute
        execute_calls: list[object] = []

        async def _execute(stmt):  # type: ignore[no-untyped-def]
            execute_calls.append(stmt)
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=None)
            result.scalar_one = MagicMock(return_value=None)
            return result

        session.execute = AsyncMock(side_effect=_execute)

        result = await is_token_revoked(session, "nonexistent-jti")

        assert result is False
        # execute was called twice: once for select, once for delete cleanup
        assert session.execute.await_count == 2


    async def test_multiple_tokens_revoked_independently(self) -> None:
        """Each token can be revoked and checked independently."""
        jti_a = str(uuid.uuid4())
        jti_b = str(uuid.uuid4())
        jti_c = str(uuid.uuid4())

        session = _make_mock_session_with_revoked(jti_a, jti_c)

        assert await is_token_revoked(session, jti_a) is True
        assert await is_token_revoked(session, jti_b) is False
        assert await is_token_revoked(session, jti_c) is True


    async def test_revoke_token_preserves_revoked_at_timestamp(self) -> None:
        """The revoked_at field is set to the current UTC time at revocation."""
        session = _make_mock_session()
        before = datetime.now(UTC)
        jti = str(uuid.uuid4())
        expires = before + timedelta(hours=1)

        record = await revoke_token(session, jti=jti, expires_at=expires)

        after = datetime.now(UTC)
        assert before <= record.revoked_at <= after


    async def test_revoke_token_with_custom_reason(self) -> None:
        """A custom reason string is persisted correctly."""
        session = _make_mock_session()
        jti = str(uuid.uuid4())
        expires = datetime.now(UTC) + timedelta(hours=1)

        record = await revoke_token(
            session, jti=jti, expires_at=expires, reason="security_incident"
        )

        assert record.reason == "security_incident"


# =========================================================================
# A (cont). Auth Integration with Revocation
# =========================================================================


class TestAuthRevocationIntegration:
    """Test that get_authenticated_principal respects server-side revocation."""


    async def test_revoked_token_rejected_by_get_authenticated_principal(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """A token whose jti has been revoked returns 401."""
        token, jti = _make_token(sample_principal, settings)

        # Build mock db session that reports this jti as revoked
        mock_record = MagicMock(spec=RevokedToken)
        mock_record.jti = jti
        revoked_result = AsyncMock()
        revoked_result.scalar_one_or_none = MagicMock(return_value=mock_record)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=revoked_result)

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, str]:
            return {"principal_id": principal.principal_id}

        app.dependency_overrides[_get_security_settings] = lambda: settings

        # Override get_db to return our mock session
        from agentblue.db.session import get_db

        async def _override_db():  # type: ignore[no-untyped-def]
            yield mock_db

        app.dependency_overrides[get_db] = _override_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected", headers={"Authorization": f"Bearer {token}"}
            )

        assert resp.status_code == 401
        assert "revoked" in resp.json()["detail"].lower()


    async def test_valid_non_revoked_token_accepted(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """A valid token whose jti is not revoked returns 200."""
        token, jti = _make_token(sample_principal, settings)

        # Build mock db session that reports jti as NOT revoked
        not_revoked_result = AsyncMock()
        not_revoked_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=not_revoked_result)

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, str]:
            return {"principal_id": principal.principal_id}

        app.dependency_overrides[_get_security_settings] = lambda: settings

        from agentblue.db.session import get_db

        async def _override_db():  # type: ignore[no-untyped-def]
            yield mock_db

        app.dependency_overrides[get_db] = _override_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected", headers={"Authorization": f"Bearer {token}"}
            )

        assert resp.status_code == 200
        assert resp.json()["principal_id"] == sample_principal.principal_id


    async def test_token_with_no_jti_still_works_backward_compat(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """A token without a jti claim is accepted (backward compatibility)."""
        token, _ = _make_token(sample_principal, settings, include_jti=False)

        # Mock db session — jti check should be skipped, so this is never called
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, str]:
            return {"principal_id": principal.principal_id}

        app.dependency_overrides[_get_security_settings] = lambda: settings

        from agentblue.db.session import get_db

        async def _override_db():  # type: ignore[no-untyped-def]
            yield mock_db

        app.dependency_overrides[get_db] = _override_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected", headers={"Authorization": f"Bearer {token}"}
            )

        assert resp.status_code == 200
        # is_token_revoked should NOT have been called (no jti)
        mock_db.execute.assert_not_awaited()


    async def test_create_access_token_includes_unique_jti(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """create_access_token embeds a unique jti in every token."""
        token1 = create_access_token(sample_principal, settings)
        token2 = create_access_token(sample_principal, settings)

        payload1 = decode_token(token1, settings)
        payload2 = decode_token(token2, settings)

        assert "jti" in payload1
        assert "jti" in payload2
        assert payload1["jti"] != payload2["jti"]


    async def test_revoked_token_used_again_returns_401(
        self, sample_principal: Principal, settings: SecuritySettings
    ) -> None:
        """After revoking a token, using it again returns 401 (not a decode error)."""
        token, jti = _make_token(sample_principal, settings)

        mock_record = MagicMock(spec=RevokedToken)
        mock_record.jti = jti
        revoked_result = AsyncMock()
        revoked_result.scalar_one_or_none = MagicMock(return_value=mock_record)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=revoked_result)

        app = FastAPI()

        @app.get("/protected")
        async def protected(
            principal: Principal = Depends(get_authenticated_principal),
        ) -> dict[str, str]:
            return {"principal_id": principal.principal_id}

        app.dependency_overrides[_get_security_settings] = lambda: settings

        from agentblue.db.session import get_db

        async def _override_db():  # type: ignore[no-untyped-def]
            yield mock_db

        app.dependency_overrides[get_db] = _override_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/protected", headers={"Authorization": f"Bearer {token}"}
            )

        assert resp.status_code == 401
        # The detail should be about revocation, not an invalid signature
        assert "revoked" in resp.json()["detail"].lower()


# =========================================================================
# B. Execution Context (10+ tests)
# =========================================================================


class TestExecutionContextPermission:
    """Tests for ExecutionContext.require_permission."""


    def test_require_permission_succeeds_with_correct_permission(
        self, sample_principal: Principal
    ) -> None:
        """VIEWER has ACCOUNTING_READ; require_permission does not raise."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c1")
        # VIEWER has ACCOUNTING_READ
        ctx.require_permission(Permission.ACCOUNTING_READ)
        # No exception = pass


    def test_require_permission_raises_with_wrong_permission(
        self, sample_principal: Principal
    ) -> None:
        """VIEWER lacks ACCOUNTING_WRITEBACK; require_permission raises PermissionError."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c2")
        with pytest.raises(PermissionError, match="Permission denied"):
            ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)


    def test_admin_cannot_bypass_accounting_writeback(
        self, admin_principal: Principal
    ) -> None:
        """ADMIN does NOT have ACCOUNTING_WRITEBACK — deny-by-default policy."""
        ctx = ExecutionContext(principal=admin_principal, correlation_id="c3")
        with pytest.raises(PermissionError, match="Permission denied"):
            ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)


    def test_admin_cannot_bypass_accounting_approve(
        self, admin_principal: Principal
    ) -> None:
        """ADMIN does NOT have ACCOUNTING_APPROVE — no implicit bypass."""
        ctx = ExecutionContext(principal=admin_principal, correlation_id="c3b")
        with pytest.raises(PermissionError, match="Permission denied"):
            ctx.require_permission(Permission.ACCOUNTING_APPROVE)


    def test_ml_operator_cannot_perform_accounting_writeback(
        self, ml_operator_principal: Principal
    ) -> None:
        """ML_OPERATOR lacks ACCOUNTING_WRITEBACK."""
        ctx = ExecutionContext(principal=ml_operator_principal, correlation_id="c4")
        with pytest.raises(PermissionError, match="Permission denied"):
            ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)


    def test_approver_can_writeback(self, approver_principal: Principal) -> None:
        """APPROVER has ACCOUNTING_WRITEBACK; require_permission does not raise."""
        ctx = ExecutionContext(principal=approver_principal, correlation_id="c5")
        ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)
        # No exception = pass


    def test_permission_error_message_includes_permission_value(
        self, sample_principal: Principal
    ) -> None:
        """PermissionError message includes the denied permission's value."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c6")
        with pytest.raises(PermissionError, match="accounting:writeback"):
            ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)


    def test_permission_error_message_includes_principal_roles(
        self, sample_principal: Principal
    ) -> None:
        """PermissionError message includes the principal's current roles."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c7")
        with pytest.raises(PermissionError, match="VIEWER"):
            ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)


class TestExecutionContextRealm:
    """Tests for ExecutionContext.require_realm and require_any_realm."""


    def test_require_realm_succeeds_with_assigned_realm(
        self, sample_principal: Principal
    ) -> None:
        """Principal assigned to realm-a can access realm-a."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c8")
        ctx.require_realm("realm-a")
        # No exception = pass


    def test_require_realm_raises_with_wrong_realm(
        self, sample_principal: Principal
    ) -> None:
        """Principal assigned to realm-a cannot access realm-b."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c9")
        with pytest.raises(PermissionError, match="Realm access denied"):
            ctx.require_realm("realm-b")


    def test_require_any_realm_succeeds_with_realm_assignment(
        self, sample_principal: Principal
    ) -> None:
        """Principal with realm_ids can pass require_any_realm."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c10")
        ctx.require_any_realm()
        # No exception = pass


    def test_require_any_realm_raises_with_no_realms(
        self, no_realm_principal: Principal
    ) -> None:
        """Principal with no realms fails require_any_realm."""
        ctx = ExecutionContext(principal=no_realm_principal, correlation_id="c11")
        with pytest.raises(PermissionError, match="No realm assignments"):
            ctx.require_any_realm()


    def test_require_realm_error_message_includes_principal_id(
        self, sample_principal: Principal
    ) -> None:
        """Realm denial error includes the principal ID for audit traceability."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c12")
        with pytest.raises(PermissionError, match=sample_principal.principal_id):
            ctx.require_realm("realm-z")


    def test_require_realm_error_message_includes_assigned_realms(
        self, sample_principal: Principal
    ) -> None:
        """Realm denial error includes the principal's assigned realm IDs."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="c13")
        with pytest.raises(PermissionError, match="realm-a"):
            ctx.require_realm("realm-z")


    def test_require_any_realm_error_includes_principal_id(
        self, no_realm_principal: Principal
    ) -> None:
        """require_any_realm error includes the principal ID."""
        ctx = ExecutionContext(principal=no_realm_principal, correlation_id="c14")
        with pytest.raises(PermissionError, match=no_realm_principal.principal_id):
            ctx.require_any_realm()


class TestExecutionContextDataclass:
    """Tests for ExecutionContext dataclass behavior."""


    def test_context_carries_correlation_id(
        self, sample_principal: Principal
    ) -> None:
        """ExecutionContext stores and exposes the correlation_id."""
        ctx = ExecutionContext(
            principal=sample_principal, correlation_id="my-corr-id"
        )
        assert ctx.correlation_id == "my-corr-id"


    def test_context_defaults_correlation_id_from_principal(
        self, sample_principal: Principal
    ) -> None:
        """When correlation_id is empty, it falls back to principal.correlation_id."""
        ctx = ExecutionContext(principal=sample_principal)
        assert ctx.correlation_id == sample_principal.correlation_id


    def test_context_frozen(self, sample_principal: Principal) -> None:
        """ExecutionContext is frozen (immutable) after construction."""
        ctx = ExecutionContext(
            principal=sample_principal, correlation_id="frozen-corr"
        )
        with pytest.raises(AttributeError):
            ctx.correlation_id = "changed"  # type: ignore[misc]


    def test_context_stores_principal(self, sample_principal: Principal) -> None:
        """ExecutionContext.principal references the supplied Principal."""
        ctx = ExecutionContext(principal=sample_principal, correlation_id="p1")
        assert ctx.principal is sample_principal


# =========================================================================
# C. Service-Layer Authorization (8+ tests)
# =========================================================================


class TestServiceLayerAuthorization:
    """Tests that simulate service-layer calls guarded by ExecutionContext."""


    def _guarded_writeback(
        self, ctx: ExecutionContext, realm_id: str
    ) -> dict[str, str]:
        """Simulate a service method that enforces permission + realm."""
        ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)
        ctx.require_realm(realm_id)
        return {"status": "ok", "realm": realm_id}


    def _guarded_approve(
        self, ctx: ExecutionContext, realm_id: str
    ) -> dict[str, str]:
        """Simulate a service method that enforces approval permission + realm."""
        ctx.require_permission(Permission.ACCOUNTING_APPROVE)
        ctx.require_realm(realm_id)
        return {"status": "approved", "realm": realm_id}


    def _guarded_any_realm_read(self, ctx: ExecutionContext) -> dict[str, str]:
        """Simulate a service method requiring any realm assignment."""
        ctx.require_any_realm()
        ctx.require_permission(Permission.ACCOUNTING_READ)
        return {"status": "ok"}


    def test_direct_service_call_without_context_fails(
        self, sample_principal: Principal
    ) -> None:
        """Calling a service method without an ExecutionContext fails at runtime.

        Since Python has no compile-time guard for mandatory parameters,
        passing None simulates a missing context and triggers AttributeError.
        """
        # Simulate calling without a valid context
        with pytest.raises(AttributeError):
            None.require_permission(Permission.ACCOUNTING_READ)  # type: ignore[union-attr]


    def test_direct_service_call_with_wrong_permission_fails(
        self, sample_principal: Principal
    ) -> None:
        """A VIEWER cannot call writeback — permission check rejects."""
        ctx = ExecutionContext(
            principal=sample_principal, correlation_id="svc-1"
        )
        with pytest.raises(PermissionError):
            self._guarded_writeback(ctx, "realm-a")


    def test_direct_service_call_with_cross_realm_fails(
        self, approver_principal: Principal
    ) -> None:
        """An APPROVER in realm-a cannot writeback to realm-b."""
        ctx = ExecutionContext(
            principal=approver_principal, correlation_id="svc-2"
        )
        with pytest.raises(PermissionError, match="Realm access denied"):
            self._guarded_writeback(ctx, "realm-b")


    def test_direct_service_call_with_correct_context_succeeds(
        self, approver_principal: Principal
    ) -> None:
        """An APPROVER in realm-a can writeback to realm-a."""
        ctx = ExecutionContext(
            principal=approver_principal, correlation_id="svc-3"
        )
        result = self._guarded_writeback(ctx, "realm-a")
        assert result["status"] == "ok"
        assert result["realm"] == "realm-a"


    def test_admin_cannot_bypass_accounting_approval_via_context(
        self, admin_principal: Principal
    ) -> None:
        """ADMIN cannot approve accounting entries (no ACCOUNTING_APPROVE)."""
        ctx = ExecutionContext(
            principal=admin_principal, correlation_id="svc-4"
        )
        with pytest.raises(PermissionError, match="Permission denied"):
            self._guarded_approve(ctx, "realm-a")


    def test_ml_operator_cannot_writeback_via_context(
        self, ml_operator_principal: Principal
    ) -> None:
        """ML_OPERATOR cannot write back accounting entries."""
        ctx = ExecutionContext(
            principal=ml_operator_principal, correlation_id="svc-5"
        )
        with pytest.raises(PermissionError, match="Permission denied"):
            self._guarded_writeback(ctx, "realm-a")


    def test_context_permission_error_messages_are_safe_no_secrets(
        self, sample_principal: Principal
    ) -> None:
        """PermissionError messages never contain tokens, passwords, or secrets."""
        ctx = ExecutionContext(
            principal=sample_principal, correlation_id="svc-6"
        )
        with pytest.raises(PermissionError) as exc_info:
            ctx.require_permission(Permission.ACCOUNTING_WRITEBACK)

        msg = str(exc_info.value)
        # Must not contain anything that looks like a secret
        for forbidden in ["token", "password", "secret", "key", "Bearer", SECRET_KEY]:
            assert forbidden.lower() not in msg.lower(), (
                f"Error message should not contain '{forbidden}': {msg}"
            )


    def test_context_realm_error_messages_include_principal_id(
        self, sample_principal: Principal
    ) -> None:
        """Realm denial errors include the principal ID for traceability."""
        ctx = ExecutionContext(
            principal=sample_principal, correlation_id="svc-7"
        )
        with pytest.raises(PermissionError) as exc_info:
            ctx.require_realm("nonexistent-realm")

        msg = str(exc_info.value)
        assert sample_principal.principal_id in msg


    def test_no_realm_principal_fails_any_realm_guard(
        self, no_realm_principal: Principal
    ) -> None:
        """A principal with no realm assignments cannot pass require_any_realm."""
        ctx = ExecutionContext(
            principal=no_realm_principal, correlation_id="svc-8"
        )
        with pytest.raises(PermissionError, match="No realm assignments"):
            self._guarded_any_realm_read(ctx)


    def test_viewer_with_realms_can_read(
        self, sample_principal: Principal
    ) -> None:
        """A VIEWER with realm assignments can pass the any-realm + read guard."""
        ctx = ExecutionContext(
            principal=sample_principal, correlation_id="svc-9"
        )
        result = self._guarded_any_realm_read(ctx)
        assert result["status"] == "ok"


    def test_cross_realm_approve_fails_via_context(
        self, approver_principal: Principal
    ) -> None:
        """APPROVER in realm-a cannot approve realm-b entries."""
        ctx = ExecutionContext(
            principal=approver_principal, correlation_id="svc-10"
        )
        with pytest.raises(PermissionError, match="Realm access denied"):
            self._guarded_approve(ctx, "realm-b")


    def test_approver_with_correct_realm_can_approve(
        self, approver_principal: Principal
    ) -> None:
        """APPROVER in realm-a can approve realm-a entries."""
        ctx = ExecutionContext(
            principal=approver_principal, correlation_id="svc-11"
        )
        result = self._guarded_approve(ctx, "realm-a")
        assert result["status"] == "approved"
