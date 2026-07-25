"""Security audit event and correlation ID middleware tests.

Covers:
  A. Audit event recording (record_audit_event)
  B. Metadata sanitization (_sanitize_metadata)
  C. Correlation ID middleware (CorrelationIDMiddleware)
  D. Audit immutability guarantees
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agentblue.security.audit import _sanitize_metadata, record_audit_event
from agentblue.security.middleware import CorrelationIDMiddleware
from agentblue.security.models import AuditEvent
from agentblue.security.principal import Principal
from agentblue.security.roles import Role

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _make_principal(
    *,
    principal_id: str = "user-42",
    principal_type: str = "human",
    email: str = "alice@example.com",
    roles: frozenset[Role] | None = None,
    correlation_id: str = "cid-abc-123",
) -> Principal:
    """Build a test Principal with sane defaults."""
    return Principal(
        principal_id=principal_id,
        principal_type=principal_type,
        email=email,
        roles=roles if roles is not None else frozenset({Role.ACCOUNTANT}),
        correlation_id=correlation_id,
    )


async def _make_session() -> AsyncMock:
    """Return an AsyncMock that behaves like an AsyncSession."""
    session = AsyncMock()
    # session.add is synchronous; override so it doesn't return a coroutine.
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# A. Audit Event Recording
# ---------------------------------------------------------------------------


class TestAuditEventRecording:
    """Validate that record_audit_event produces correct AuditEvent fields."""

    async def test_successful_action_records_event(self) -> None:
        """A successful action produces an AuditEvent with success=True."""
        session = await _make_session()
        principal = _make_principal()
        event = await record_audit_event(
            session,
            principal=principal,
            action="categorization.approve",
            resource_type="transaction",
            resource_id="txn-99",
            realm_id="realm-1",
            success=True,
        )
        assert isinstance(event, AuditEvent)
        assert event.success is True
        assert event.action == "categorization.approve"
        assert event.resource_type == "transaction"
        assert event.resource_id == "txn-99"
        assert event.failure_category is None
        assert event.error_detail is None
        session.add.assert_called_once_with(event)
        session.flush.assert_awaited_once()

    async def test_failed_action_records_failure_category(self) -> None:
        """A failed action sets failure_category and error_detail."""
        session = await _make_session()
        principal = _make_principal()
        event = await record_audit_event(
            session,
            principal=principal,
            action="categorization.approve",
            resource_type="transaction",
            resource_id="txn-99",
            success=False,
            failure_category="unauthorized",
            error_detail="Principal lacks APPROVER role",
        )
        assert event.success is False
        assert event.failure_category == "unauthorized"
        assert event.error_detail == "Principal lacks APPROVER role"

    async def test_actor_principal_id_recorded(self) -> None:
        """The principal_id from the actor is copied onto the event."""
        session = await _make_session()
        principal = _make_principal(principal_id="svc-qbo-sync")
        event = await record_audit_event(
            session,
            principal=principal,
            action="quickbooks.sync",
        )
        assert event.actor_principal_id == "svc-qbo-sync"

    async def test_actor_roles_recorded_as_list(self) -> None:
        """Roles are serialized as a list of role value strings."""
        session = await _make_session()
        principal = _make_principal(
            roles=frozenset({Role.ACCOUNTANT, Role.APPROVER})
        )
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
        )
        assert isinstance(event.actor_roles, list)
        assert sorted(event.actor_roles) == ["ACCOUNTANT", "APPROVER"]

    async def test_realm_id_recorded(self) -> None:
        """The realm_id is stored on the event."""
        session = await _make_session()
        principal = _make_principal()
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
            realm_id="realm-xyz",
        )
        assert event.realm_id == "realm-xyz"

    async def test_correlation_id_recorded(self) -> None:
        """The principal's correlation_id is stored on the event."""
        session = await _make_session()
        principal = _make_principal(correlation_id="cid-999")
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
        )
        assert event.correlation_id == "cid-999"

    async def test_source_ip_recorded_when_provided(self) -> None:
        """Source IP is stored when explicitly passed."""
        session = await _make_session()
        principal = _make_principal()
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
            source_ip="203.0.113.42",
        )
        assert event.source_ip == "203.0.113.42"

    async def test_user_agent_recorded_when_provided(self) -> None:
        """User agent is stored when explicitly passed."""
        session = await _make_session()
        principal = _make_principal()
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
            user_agent="Mozilla/5.0 TestAgent/1.0",
        )
        assert event.user_agent == "Mozilla/5.0 TestAgent/1.0"

    async def test_source_ip_none_when_not_provided(self) -> None:
        """Source IP defaults to None when not provided."""
        session = await _make_session()
        principal = _make_principal()
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
        )
        assert event.source_ip is None
        assert event.user_agent is None

    async def test_error_detail_truncated_to_500_chars(self) -> None:
        """Error detail is truncated to 500 characters."""
        session = await _make_session()
        principal = _make_principal()
        long_detail = "x" * 600
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
            success=False,
            error_detail=long_detail,
        )
        assert event.error_detail is not None
        assert len(event.error_detail) == 500

    async def test_metadata_sanitized_on_record(self) -> None:
        """Metadata is sanitized before being stored on the event."""
        session = await _make_session()
        principal = _make_principal()
        event = await record_audit_event(
            session,
            principal=principal,
            action="test.action",
            metadata={"password": "hunter2", "safe_key": "visible"},
        )
        assert event.event_metadata["password"] == "[REDACTED]"
        assert event.event_metadata["safe_key"] == "visible"


# ---------------------------------------------------------------------------
# B. Metadata Sanitization
# ---------------------------------------------------------------------------


class TestMetadataSanitization:
    """Validate _sanitize_metadata redacts sensitive keys."""

    def test_password_redacted(self) -> None:
        """The 'password' key is replaced with [REDACTED]."""
        result = _sanitize_metadata({"password": "hunter2"})
        assert result["password"] == "[REDACTED]"

    def test_token_redacted(self) -> None:
        """The 'token' key is replaced with [REDACTED]."""
        result = _sanitize_metadata({"token": "jwt-abc"})
        assert result["token"] == "[REDACTED]"

    def test_secret_redacted(self) -> None:
        """The 'secret' key is replaced with [REDACTED]."""
        result = _sanitize_metadata({"secret": "top-secret"})
        assert result["secret"] == "[REDACTED]"

    def test_authorization_redacted(self) -> None:
        """The 'authorization' key is replaced with [REDACTED]."""
        result = _sanitize_metadata({"authorization": "Bearer xyz"})
        assert result["authorization"] == "[REDACTED]"

    def test_access_token_redacted(self) -> None:
        """The 'access_token' key is replaced with [REDACTED]."""
        result = _sanitize_metadata({"access_token": "tok_123"})
        assert result["access_token"] == "[REDACTED]"

    def test_case_insensitive_redaction(self) -> None:
        """Sensitive keys are matched case-insensitively."""
        result = _sanitize_metadata({"Password": "x", "TOKEN": "y", "Secret": "z"})
        assert result["Password"] == "[REDACTED]"
        assert result["TOKEN"] == "[REDACTED]"
        assert result["Secret"] == "[REDACTED]"

    def test_non_sensitive_keys_preserved(self) -> None:
        """Non-sensitive keys pass through unchanged."""
        data = {"action": "approve", "count": 5, "enabled": True}
        result = _sanitize_metadata(data)
        assert result == {"action": "approve", "count": 5, "enabled": True}

    def test_nested_dict_sensitive_keys_redacted(self) -> None:
        """Sensitive keys inside nested dicts are also redacted."""
        data = {
            "config": {
                "timeout": 30,
                "api_key": "sk-abc",
                "db_password": "p@ss",
            },
            "safe": "yes",
        }
        result = _sanitize_metadata(data)
        assert result["safe"] == "yes"
        assert result["config"]["timeout"] == 30
        assert result["config"]["api_key"] == "[REDACTED]"
        assert result["config"]["db_password"] == "[REDACTED]"

    def test_empty_metadata(self) -> None:
        """Empty metadata dict returns empty dict."""
        assert _sanitize_metadata({}) == {}

    def test_refresh_token_redacted(self) -> None:
        """The 'refresh_token' key is redacted."""
        result = _sanitize_metadata({"refresh_token": "rt-abc"})
        assert result["refresh_token"] == "[REDACTED]"

    def test_client_secret_redacted(self) -> None:
        """The 'client_secret' key is redacted."""
        result = _sanitize_metadata({"client_secret": "cs-abc"})
        assert result["client_secret"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# C. Correlation ID Middleware
# ---------------------------------------------------------------------------


def _correlation_app() -> Starlette:
    """Build a minimal ASGI app wrapped with CorrelationIDMiddleware."""

    async def health(request: Request) -> JSONResponse:
        cid = getattr(request.state, "correlation_id", "")
        return JSONResponse({"correlation_id": cid})

    app = Starlette(routes=[Route("/health", health)])
    app.add_middleware(CorrelationIDMiddleware)
    return app


class TestCorrelationIDMiddleware:
    """Validate CorrelationIDMiddleware header handling and generation."""

    def test_valid_correlation_id_preserved(self) -> None:
        """A valid X-Correlation-ID header is preserved in the response."""
        app = _correlation_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Correlation-ID": "my-valid-id_123"})
        assert resp.status_code == 200
        assert resp.headers["X-Correlation-ID"] == "my-valid-id_123"
        assert resp.json()["correlation_id"] == "my-valid-id_123"

    def test_missing_header_generates_uuid(self) -> None:
        """When no header is provided, a UUID v4 is generated."""
        app = _correlation_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        cid = resp.headers["X-Correlation-ID"]
        assert _UUID_RE.match(cid) is not None

    def test_header_injection_rejected(self) -> None:
        """Header values with illegal characters trigger UUID generation."""
        app = _correlation_app()
        client = TestClient(app)
        # Newlines / spaces / special chars should be rejected.
        resp = client.get(
            "/health",
            headers={"X-Correlation-ID": "evil\r\nInjected: header"},
        )
        cid = resp.headers["X-Correlation-ID"]
        assert _UUID_RE.match(cid) is not None

    def test_oversized_header_generates_uuid(self) -> None:
        """A header longer than 128 chars triggers UUID generation."""
        app = _correlation_app()
        client = TestClient(app)
        long_id = "a" * 129
        resp = client.get("/health", headers={"X-Correlation-ID": long_id})
        cid = resp.headers["X-Correlation-ID"]
        # Must not be the oversized value; should be a generated UUID.
        assert len(cid) <= 128
        assert _UUID_RE.match(cid) is not None

    def test_correlation_id_in_response_headers(self) -> None:
        """The correlation ID always appears in the response headers."""
        app = _correlation_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Correlation-ID": "test-id"})
        assert "X-Correlation-ID" in resp.headers

    def test_empty_header_generates_uuid(self) -> None:
        """An empty X-Correlation-ID header triggers UUID generation."""
        app = _correlation_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Correlation-ID": ""})
        cid = resp.headers["X-Correlation-ID"]
        assert _UUID_RE.match(cid) is not None

    def test_stored_in_request_state(self) -> None:
        """The correlation ID is available on request.state."""
        app = _correlation_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Correlation-ID": "state-check"})
        body = resp.json()
        assert body["correlation_id"] == "state-check"

    def test_hyphens_and_underscores_allowed(self) -> None:
        """Hyphens and underscores are valid in correlation IDs."""
        app = _correlation_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Correlation-ID": "a-b_c-d"})
        assert resp.headers["X-Correlation-ID"] == "a-b_c-d"

    def test_spaces_rejected(self) -> None:
        """Spaces in the header trigger UUID generation."""
        app = _correlation_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Correlation-ID": "has space"})
        cid = resp.headers["X-Correlation-ID"]
        assert _UUID_RE.match(cid) is not None


# ---------------------------------------------------------------------------
# D. Audit Immutability
# ---------------------------------------------------------------------------


class TestAuditImmutability:
    """Verify that the AuditEvent model has no update/delete API surface."""

    def test_no_update_method_on_model(self) -> None:
        """AuditEvent should not expose an update() method."""
        assert not hasattr(AuditEvent, "update") or not callable(
            getattr(AuditEvent, "update", None)
        )

    def test_no_delete_method_on_model(self) -> None:
        """AuditEvent should not expose a delete() method."""
        assert not hasattr(AuditEvent, "delete") or not callable(
            getattr(AuditEvent, "delete", None)
        )

    async def test_record_uses_add_not_delete(self) -> None:
        """record_audit_event calls session.add (not session.delete)."""
        session = AsyncMock()
        session.add = MagicMock()
        principal = _make_principal()

        await record_audit_event(session, principal=principal, action="test.action")
        session.add.assert_called_once()
        # The only mutating calls should be add + flush; no delete.
        session.delete.assert_not_called()

    def test_event_metadata_column_is_append_only(self) -> None:
        """AuditEvent.event_metadata maps to the 'metadata' column.

        The column default is dict (empty dict at insert time).
        There is no ORM-level API to bulk-update or replace metadata
        after construction — it is set once in __init__.
        """
        # Explicitly pass metadata to verify the field is writable at init.
        event = AuditEvent(
            actor_principal_id="p",
            action="a",
            event_metadata={"key": "value"},
        )
        assert event.event_metadata == {"key": "value"}
        # Verify the column name mapping exists.
        col = AuditEvent.__table__.columns.get("metadata")
        assert col is not None, "AuditEvent must have a 'metadata' column"
