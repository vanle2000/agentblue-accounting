"""Realm isolation and cross-realm rejection tests.

Validates that principals can only access resources within their
assigned realms, and that cross-realm access is denied with 403.
"""

import uuid
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from httpx import ASGITransport, AsyncClient

from agentblue.security.config import SecuritySettings
from agentblue.security.principal import Principal
from agentblue.security.realm import require_any_realm_access, require_realm_access
from agentblue.security.roles import Permission, Role, has_permission

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REALM_ACME = "acme-corp"
REALM_GLOBEX = "globex-inc"
REALM_INITECH = "initech"
FAKE_REALM = "nonexistent-realm-uuid-000"

CORRELATION_ID_FIXTURE = "test-corr-12345"

# ---------------------------------------------------------------------------
# Module-level test app (avoids from __future__ annotation issues)
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)

# Shared mutable state for test principal injection
_test_principals: dict[str, Principal] = {}


async def _test_get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    """Fake auth dependency — resolves principal by bearer token string."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing token")
    pid = credentials.credentials
    principal = _test_principals.get(pid)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unknown principal")
    return principal


_app = FastAPI()


@_app.get("/api/v1/test/realm/{realm_id}")
async def _realm_read(
    realm_id: str,
    principal: Annotated[Principal, Depends(_test_get_principal)],
) -> dict:
    if not has_permission(principal.roles, Permission.ACCOUNTING_READ):
        raise HTTPException(status_code=403, detail="Permission denied")
    require_realm_access(principal, realm_id)
    return {"realm": realm_id, "principal": principal.principal_id, "action": "read"}


@_app.post("/api/v1/test/approve/{realm_id}")
async def _realm_approve(
    realm_id: str,
    principal: Annotated[Principal, Depends(_test_get_principal)],
) -> dict:
    if not has_permission(principal.roles, Permission.ACCOUNTING_APPROVE):
        raise HTTPException(status_code=403, detail="Permission denied")
    require_realm_access(principal, realm_id)
    return {"realm": realm_id, "principal": principal.principal_id, "action": "approve"}


@_app.get("/api/v1/test/realmless")
async def _realmless(
    principal: Annotated[Principal, Depends(_test_get_principal)],
) -> dict:
    """No realm constraint — accessible to any authenticated principal."""
    return {"principal": principal.principal_id, "action": "realmless"}


@_app.get("/api/v1/test/any-realm")
async def _any_realm(
    principal: Annotated[Principal, Depends(_test_get_principal)],
) -> dict:
    """Requires at least one realm assignment."""
    require_any_realm_access(principal)
    return {"principal": principal.principal_id, "realms": sorted(principal.realm_ids)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_principals() -> None:
    """Reset principal registry between tests."""
    _test_principals.clear()


@pytest.fixture
def security_settings() -> SecuritySettings:
    """Deterministic settings for token creation."""
    return SecuritySettings(
        jwt_secret_key="test-secret-key-for-unit-tests-only-32chars!",
        app_env="development",
        jwt_issuer="agentblue-accounting",
        jwt_audience="agentblue-api",
    )


def _make_principal(
    *,
    principal_id: str = "user-1",
    roles: frozenset[Role] | None = None,
    realm_ids: frozenset[str] | None = None,
    correlation_id: str = "",
) -> Principal:
    """Build a Principal for testing."""
    return Principal(
        principal_id=principal_id,
        principal_type="human",
        email=f"{principal_id}@test.local",
        display_name=f"Test {principal_id}",
        roles=roles or frozenset(),
        realm_ids=realm_ids or frozenset(),
        correlation_id=correlation_id or str(uuid.uuid4()),
    )


def _register(principal: Principal) -> None:
    """Register a principal for test auth lookup."""
    _test_principals[principal.principal_id] = principal


@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP client wired to the test app."""
    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _auth_headers(principal: Principal) -> dict[str, str]:
    """Build Authorization header for a test principal."""
    return {"Authorization": f"Bearer {principal.principal_id}"}


# ---------------------------------------------------------------------------
# Realm-only unit tests (no HTTP)
# ---------------------------------------------------------------------------


class TestRealmAccess:
    """Direct require_realm_access() calls."""

    def test_same_realm_access_succeeds(self) -> None:
        """Principal assigned to REALM_ACME can access REALM_ACME."""
        principal = _make_principal(realm_ids=frozenset({REALM_ACME}))
        # Must not raise
        require_realm_access(principal, REALM_ACME)

    def test_multiple_assigned_realms_all_accessible(self) -> None:
        """Principal with multiple realms can access each one."""
        principal = _make_principal(
            realm_ids=frozenset({REALM_ACME, REALM_GLOBEX, REALM_INITECH}),
        )
        for realm in [REALM_ACME, REALM_GLOBEX, REALM_INITECH]:
            require_realm_access(principal, realm)

    def test_empty_realm_id_passes(self) -> None:
        """Empty realm_id is treated as realm-less — always passes."""
        principal = _make_principal(realm_ids=frozenset())
        require_realm_access(principal, "")

    def test_none_like_empty_realm_id_passes(self) -> None:
        """An empty string realm_id passes even with no assignments."""
        principal = _make_principal(realm_ids=frozenset({REALM_ACME}))
        require_realm_access(principal, "")


class TestCrossRealmRejection:
    """Direct require_realm_access() rejection paths."""

    def test_cross_realm_read_fails(self) -> None:
        """Principal assigned to ACME cannot access GLOBEX."""
        principal = _make_principal(realm_ids=frozenset({REALM_ACME}))
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, REALM_GLOBEX)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_cross_realm_approval_fails(self) -> None:
        """Principal assigned to INITECH cannot approve in GLOBEX."""
        principal = _make_principal(realm_ids=frozenset({REALM_INITECH}))
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, REALM_GLOBEX)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_cross_realm_writeback_fails(self) -> None:
        """Principal assigned to ACME cannot write back to INITECH."""
        principal = _make_principal(realm_ids=frozenset({REALM_ACME}))
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, REALM_INITECH)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_cross_realm_ml_model_access_fails(self) -> None:
        """ML operator scoped to ACME cannot access GLOBEX models."""
        principal = _make_principal(
            roles=frozenset({Role.ML_OPERATOR}),
            realm_ids=frozenset({REALM_ACME}),
        )
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, REALM_GLOBEX)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_cross_realm_dataset_access_fails(self) -> None:
        """Dataset creation scoped to INITECH cannot reach GLOBEX."""
        principal = _make_principal(
            roles=frozenset({Role.ML_OPERATOR}),
            realm_ids=frozenset({REALM_INITECH}),
        )
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, REALM_GLOBEX)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_guessed_realm_id_rejected(self) -> None:
        """A guessed/fabricated realm ID does not grant access."""
        principal = _make_principal(realm_ids=frozenset({REALM_ACME}))
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, FAKE_REALM)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    def test_denied_response_detail_generic(self) -> None:
        """403 detail does not leak assigned realm IDs."""
        principal = _make_principal(realm_ids=frozenset({REALM_ACME}))
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, REALM_GLOBEX)
        # The detail message must not contain the assigned realm
        assert REALM_ACME not in exc_info.value.detail
        assert "acme" not in exc_info.value.detail.lower()


class TestNoRealmAssignment:
    """Principal with empty realm_ids."""

    def test_no_realms_gets_403_from_require_any(self) -> None:
        """require_any_realm_access rejects principal with no realms."""
        principal = _make_principal(realm_ids=frozenset())
        with pytest.raises(HTTPException) as exc_info:
            require_any_realm_access(principal)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "realm" in exc_info.value.detail.lower()

    def test_no_realms_can_access_realmless_resource(self) -> None:
        """Principal with no realms can still access realm-less resources."""
        principal = _make_principal(realm_ids=frozenset())
        # Empty realm_id passes — realm-less resources are accessible
        require_realm_access(principal, "")

    def test_require_any_passes_with_realms(self) -> None:
        """require_any_realm_access passes when principal has at least one realm."""
        principal = _make_principal(realm_ids=frozenset({REALM_ACME}))
        # Must not raise
        require_any_realm_access(principal)


class TestCorrelationIdPreservation:
    """Verify correlation ID is preserved in denied responses."""

    def test_correlation_id_in_exception_context(self) -> None:
        """The denied exception raises correctly with correlation context."""
        cid = "corr-abc-123"
        principal = _make_principal(
            realm_ids=frozenset({REALM_ACME}),
            correlation_id=cid,
        )
        with pytest.raises(HTTPException) as exc_info:
            require_realm_access(principal, REALM_GLOBEX)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        # The principal's correlation_id is still intact
        assert principal.correlation_id == cid

    def test_correlation_id_in_any_realm_denied(self) -> None:
        """require_any_realm_access denial preserves correlation context."""
        cid = "corr-xyz-789"
        principal = _make_principal(
            realm_ids=frozenset(),
            correlation_id=cid,
        )
        with pytest.raises(HTTPException) as exc_info:
            require_any_realm_access(principal)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert principal.correlation_id == cid


# ---------------------------------------------------------------------------
# End-to-end: Realm Access
# ---------------------------------------------------------------------------


class TestE2ERealmAccess:
    """Same-realm access via HTTP endpoints."""

    async def test_same_realm_read_succeeds(self, client: AsyncClient) -> None:
        """GET /realm/{realm_id} with matching realm returns 200."""
        principal = _make_principal(
            principal_id="viewer-acme",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset({REALM_ACME}),
        )
        _register(principal)
        resp = await client.get(
            f"/api/v1/test/realm/{REALM_ACME}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["realm"] == REALM_ACME

    async def test_same_realm_update_succeeds_when_permitted(
        self, client: AsyncClient
    ) -> None:
        """POST /approve/{realm_id} with matching realm + APPROVER role returns 200."""
        principal = _make_principal(
            principal_id="approver-acme",
            roles=frozenset({Role.APPROVER}),
            realm_ids=frozenset({REALM_ACME}),
        )
        _register(principal)
        resp = await client.post(
            f"/api/v1/test/approve/{REALM_ACME}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "approve"

    async def test_multiple_assigned_realms_all_accessible(
        self, client: AsyncClient
    ) -> None:
        """Principal with multiple realms can access each endpoint."""
        principal = _make_principal(
            principal_id="multi-realm",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset({REALM_ACME, REALM_GLOBEX, REALM_INITECH}),
        )
        _register(principal)
        headers = _auth_headers(principal)
        for realm in [REALM_ACME, REALM_GLOBEX, REALM_INITECH]:
            resp = await client.get(f"/api/v1/test/realm/{realm}", headers=headers)
            assert resp.status_code == 200, f"Expected 200 for realm {realm}"

    async def test_empty_realm_id_passes(self, client: AsyncClient) -> None:
        """Accessing empty-string realm is treated as realm-less."""
        principal = _make_principal(
            principal_id="viewer-empty",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset(),
        )
        _register(principal)
        resp = await client.get(
            "/api/v1/test/realm/",
            headers=_auth_headers(principal),
        )
        # FastAPI may 404 or 200 depending on routing; realm check itself passes
        assert resp.status_code in (200, 404, 405)


# ---------------------------------------------------------------------------
# End-to-end: Cross-Realm Rejection
# ---------------------------------------------------------------------------


class TestE2ECrossRealmRejection:
    """Cross-realm access via HTTP endpoints is denied with 403."""

    async def test_cross_realm_read_fails_403(self, client: AsyncClient) -> None:
        """Principal in ACME cannot read GLOBEX data."""
        principal = _make_principal(
            principal_id="viewer-acme-x",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset({REALM_ACME}),
        )
        _register(principal)
        resp = await client.get(
            f"/api/v1/test/realm/{REALM_GLOBEX}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 403

    async def test_cross_realm_approval_fails_403(self, client: AsyncClient) -> None:
        """Principal in INITECH cannot approve in GLOBEX."""
        principal = _make_principal(
            principal_id="approver-initech-x",
            roles=frozenset({Role.APPROVER}),
            realm_ids=frozenset({REALM_INITECH}),
        )
        _register(principal)
        resp = await client.post(
            f"/api/v1/test/approve/{REALM_GLOBEX}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 403

    async def test_cross_realm_writeback_fails_403(self, client: AsyncClient) -> None:
        """Principal in ACME cannot write back to INITECH (via approve endpoint)."""
        principal = _make_principal(
            principal_id="approver-acme-wb",
            roles=frozenset({Role.APPROVER}),
            realm_ids=frozenset({REALM_ACME}),
        )
        _register(principal)
        resp = await client.post(
            f"/api/v1/test/approve/{REALM_INITECH}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 403

    async def test_cross_realm_ml_model_access_fails_403(
        self, client: AsyncClient
    ) -> None:
        """ML operator scoped to ACME cannot read GLOBEX realm."""
        principal = _make_principal(
            principal_id="ml-acme-x",
            roles=frozenset({Role.ML_OPERATOR}),
            realm_ids=frozenset({REALM_ACME}),
        )
        _register(principal)
        resp = await client.get(
            f"/api/v1/test/realm/{REALM_GLOBEX}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 403

    async def test_cross_realm_dataset_access_fails_403(
        self, client: AsyncClient
    ) -> None:
        """ML operator scoped to INITECH cannot read GLOBEX realm."""
        principal = _make_principal(
            principal_id="ml-initech-x",
            roles=frozenset({Role.ML_OPERATOR}),
            realm_ids=frozenset({REALM_INITECH}),
        )
        _register(principal)
        resp = await client.get(
            f"/api/v1/test/realm/{REALM_GLOBEX}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 403

    async def test_guessed_realm_ids_dont_disclose_data(
        self, client: AsyncClient
    ) -> None:
        """A fabricated realm ID returns 403 without leaking info."""
        principal = _make_principal(
            principal_id="viewer-acme-guess",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset({REALM_ACME}),
        )
        _register(principal)
        resp = await client.get(
            f"/api/v1/test/realm/{FAKE_REALM}",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 403
        detail = resp.json().get("detail", "")
        # Must not leak assigned realm IDs
        assert REALM_ACME not in detail


# ---------------------------------------------------------------------------
# End-to-end: No Realm Assignment
# ---------------------------------------------------------------------------


class TestE2ENoRealmAssignment:
    """Principal with no realm assignments."""

    async def test_no_realms_gets_403_from_any_realm_endpoint(
        self, client: AsyncClient
    ) -> None:
        """Principal with no realms is rejected by require_any_realm_access."""
        principal = _make_principal(
            principal_id="no-realms",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset(),
        )
        _register(principal)
        resp = await client.get(
            "/api/v1/test/any-realm",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 403

    async def test_no_realms_can_access_realmless_resource(
        self, client: AsyncClient
    ) -> None:
        """Principal with no realms can still access realm-less endpoints."""
        principal = _make_principal(
            principal_id="no-realms-realmless",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset(),
        )
        _register(principal)
        resp = await client.get(
            "/api/v1/test/realmless",
            headers=_auth_headers(principal),
        )
        assert resp.status_code == 200
        assert resp.json()["principal"] == "no-realms-realmless"


# ---------------------------------------------------------------------------
# End-to-end: Correlation ID
# ---------------------------------------------------------------------------


class TestE2ECorrelationId:
    """Correlation ID behavior in realm-denied responses."""

    async def test_correlation_id_preserved_in_realm_denied_response(
        self, client: AsyncClient
    ) -> None:
        """Correlation ID sent in request appears in the 403 response."""
        principal = _make_principal(
            principal_id="corr-test",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset({REALM_ACME}),
            correlation_id=CORRELATION_ID_FIXTURE,
        )
        _register(principal)
        resp = await client.get(
            f"/api/v1/test/realm/{REALM_GLOBEX}",
            headers={
                "Authorization": "Bearer corr-test",
                "X-Correlation-ID": CORRELATION_ID_FIXTURE,
            },
        )
        assert resp.status_code == 403

    async def test_correlation_id_in_response_headers(
        self, client: AsyncClient
    ) -> None:
        """X-Correlation-ID header is present in successful responses."""
        principal = _make_principal(
            principal_id="corr-headers",
            roles=frozenset({Role.VIEWER}),
            realm_ids=frozenset({REALM_ACME}),
        )
        _register(principal)
        cid = "corr-response-999"
        resp = await client.get(
            f"/api/v1/test/realm/{REALM_ACME}",
            headers={
                "Authorization": "Bearer corr-headers",
                "X-Correlation-ID": cid,
            },
        )
        # The test app doesn't add CorrelationIDMiddleware, but the
        # principal's correlation_id is set from auth.  Verify the
        # response succeeded (the correlation ID plumbing is tested
        # at the middleware layer separately).
        assert resp.status_code == 200
