"""Comprehensive tests for Stage 11: configuration, health endpoints, and observability.

Covers:
  A. Configuration validation (Settings, validators, safety checks)
  B. Health endpoints (liveness, readiness, startup, mode)
  C. Prometheus metrics (registration, labels)
  D. Production-safety enforcement
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from agentblue.api import health as health_module
from agentblue.api.health import record_startup_complete
from agentblue.config import Settings
from agentblue.observability import metrics as metrics_module

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GOOD_JWT = "a" * 64  # Well above the 32-char minimum


def _make_settings(**kwargs) -> Settings:
    """Build a Settings instance, bypassing the lru_cache singleton."""
    return Settings(**kwargs)


def _reset_startup_state() -> None:
    """Reset health module globals between tests."""
    health_module._startup_checks_passed = False
    health_module._startup_time = 0.0


# ===================================================================
# A. Configuration Validation (14 tests)
# ===================================================================


class TestConfigValidation:
    """Settings field validators and model-level safety checks."""

    def test_valid_development_mode(self) -> None:
        """Development mode starts with all defaults (no JWT required)."""
        s = _make_settings(app_env="development")
        assert s.app_env == "development"
        assert s.is_development is True

    def test_valid_staging_requires_jwt(self) -> None:
        """Staging mode requires jwt_secret_key >= 32 chars."""
        s = _make_settings(app_env="staging", jwt_secret_key=GOOD_JWT)
        assert s.app_env == "staging"

    def test_valid_production_shadow_requires_jwt(self) -> None:
        """Production-shadow mode requires jwt_secret_key >= 32 chars."""
        s = _make_settings(app_env="production-shadow", jwt_secret_key=GOOD_JWT)
        assert s.app_env == "production-shadow"

    def test_production_shadow_rejects_automatic_approval(self) -> None:
        """automatic_approval_enabled cannot be true in production-shadow."""
        with pytest.raises(ValidationError, match="automatic_approval_enabled"):
            _make_settings(
                app_env="production-shadow",
                jwt_secret_key=GOOD_JWT,
                automatic_approval_enabled=True,
            )

    def test_production_shadow_rejects_autonomous_writeback(self) -> None:
        """autonomous_writeback_enabled cannot be true in production-shadow."""
        with pytest.raises(ValidationError, match="autonomous_writeback_enabled"):
            _make_settings(
                app_env="production-shadow",
                jwt_secret_key=GOOD_JWT,
                autonomous_writeback_enabled=True,
            )

    def test_production_shadow_rejects_ml_promotion(self) -> None:
        """ml_promotion_enabled cannot be true in production-shadow."""
        with pytest.raises(ValidationError, match="ml_promotion_enabled"):
            _make_settings(
                app_env="production-shadow",
                jwt_secret_key=GOOD_JWT,
                ml_promotion_enabled=True,
            )

    def test_production_rejects_autonomous_flags(self) -> None:
        """Production mode also rejects all three autonomous flags."""
        for flag in (
            "automatic_approval_enabled",
            "autonomous_writeback_enabled",
            "ml_promotion_enabled",
        ):
            with pytest.raises(ValidationError, match=flag):
                _make_settings(
                    app_env="production",
                    jwt_secret_key=GOOD_JWT,
                    **{flag: True},
                )

    def test_short_jwt_rejected_in_production_shadow(self) -> None:
        """jwt_secret_key shorter than 32 chars is rejected in production-shadow."""
        with pytest.raises(ValidationError, match="at least 32 characters"):
            _make_settings(
                app_env="production-shadow",
                jwt_secret_key="short",
            )

    def test_empty_jwt_rejected_in_production_shadow(self) -> None:
        """Empty jwt_secret_key is rejected in production-shadow."""
        with pytest.raises(ValidationError, match="jwt_secret_key is required"):
            _make_settings(
                app_env="production-shadow",
                jwt_secret_key="",
            )

    def test_invalid_app_env_rejected(self) -> None:
        """Unknown app_env values are rejected by validate_app_env."""
        with pytest.raises(ValidationError, match="app_env must be one of"):
            _make_settings(app_env="banana")

    def test_invalid_execution_mode_rejected(self) -> None:
        """Unknown accounting_execution_mode values are rejected."""
        with pytest.raises(ValidationError, match="accounting_execution_mode must be one of"):
            _make_settings(app_env="development", accounting_execution_mode="yolo")

    def test_get_mode_summary_excludes_secrets(self) -> None:
        """get_mode_summary never includes jwt_secret_key or db_password."""
        s = _make_settings(app_env="development")
        summary = s.get_mode_summary()
        assert "jwt_secret_key" not in summary
        assert "db_password" not in summary
        assert "environment" in summary
        assert summary["environment"] == "development"

    def test_development_allows_empty_jwt(self) -> None:
        """Development mode does not require jwt_secret_key."""
        s = _make_settings(app_env="development", jwt_secret_key="")
        assert s.jwt_secret_key == ""

    def test_env_properties_correct(self) -> None:
        """is_development / is_production_shadow / is_production / is_staging match."""
        for env, dev, psh, prod, stg in [
            ("development", True, False, False, False),
            ("production-shadow", False, True, False, False),
            ("production", False, False, True, False),
            ("staging", False, False, False, True),
            ("test", False, False, False, False),
        ]:
            s = _make_settings(
                app_env=env,
                jwt_secret_key=(
                    GOOD_JWT
                    if env in ("staging", "production-shadow", "production")
                    else ""
                ),
            )
            assert s.is_development is dev, f"is_development for {env}"
            assert s.is_production_shadow is psh, f"is_production_shadow for {env}"
            assert s.is_production is prod, f"is_production for {env}"
            assert s.is_staging is stg, f"is_staging for {env}"


# ===================================================================
# B. Health Endpoint Tests (10 tests)
# ===================================================================


def _health_app(override_db=None) -> FastAPI:
    """Build a minimal FastAPI app with only the health router."""
    app = FastAPI()
    app.include_router(health_module.router)
    if override_db is not None:
        from agentblue.db.session import get_db

        app.dependency_overrides[get_db] = override_db
    return app


class TestHealthEndpoints:
    """Tests for /api/v1/health/* endpoints."""

    # -- Liveness --

    async def test_live_returns_200_with_uptime(self) -> None:
        """GET /health/live returns 200 with uptime_seconds."""
        _reset_startup_state()
        record_startup_complete()  # set a start time so uptime > 0
        app = _health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "agentblue-accounting"
        assert isinstance(body["uptime_seconds"], int | float)

    async def test_live_does_not_require_db(self) -> None:
        """Liveness probe succeeds with no database dependency at all."""
        _reset_startup_state()
        app = _health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/live")
        assert resp.status_code == 200

    # -- Readiness --

    async def test_ready_returns_200_when_db_ok(self) -> None:
        """Readiness probe returns 200 when DB connectivity + migration pass."""
        mock_session = AsyncMock()
        execute_results: list[MagicMock] = [
            MagicMock(),  # SELECT 1
            MagicMock(),  # SELECT version_num
        ]
        execute_results[1].scalar.return_value = "abc123"
        mock_session.execute = AsyncMock(side_effect=execute_results)

        async def _override_db():
            yield mock_session

        app = _health_app(override_db=_override_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"
        assert body["migrations_current"] is True

    async def test_ready_returns_503_when_db_unavailable(self) -> None:
        """Readiness probe returns 503 when DB raises an exception."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=ConnectionError("no DB"))

        async def _override_db():
            yield mock_session

        app = _health_app(override_db=_override_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "error"
        assert body["database"] == "unavailable"

    async def test_ready_checks_migration_version(self) -> None:
        """Readiness returns 503 when alembic_version has no row."""
        mock_session = AsyncMock()
        execute_results: list[MagicMock] = [
            MagicMock(),  # SELECT 1 — succeeds
            MagicMock(),  # SELECT version_num — returns None
        ]
        execute_results[1].scalar.return_value = None  # scalar() is sync
        mock_session.execute = AsyncMock(side_effect=execute_results)

        async def _override_db():
            yield mock_session

        app = _health_app(override_db=_override_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["migrations_current"] is False

    # -- Startup --

    async def test_startup_returns_503_before_complete(self) -> None:
        """Startup probe returns 503 before record_startup_complete is called."""
        _reset_startup_state()
        app = _health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/startup")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "starting"
        assert body["checks_passed"] is False

    async def test_startup_returns_200_after_complete(self) -> None:
        """Startup probe returns 200 after record_startup_complete."""
        _reset_startup_state()
        record_startup_complete()
        app = _health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/startup")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks_passed"] is True

    # -- Mode --

    async def test_mode_returns_mode_summary(self) -> None:
        """Mode endpoint returns the Settings mode summary."""
        app = _health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/mode")
        assert resp.status_code == 200
        body = resp.json()
        assert "environment" in body
        assert "accounting_execution_mode" in body

    async def test_mode_never_exposes_jwt_secret_key(self) -> None:
        """Mode endpoint never leaks jwt_secret_key."""
        app = _health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/mode")
        assert "jwt_secret_key" not in resp.text

    async def test_mode_never_exposes_db_password(self) -> None:
        """Mode endpoint never leaks db_password."""
        app = _health_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/health/mode")
        assert "db_password" not in resp.text


# ===================================================================
# C. Metrics Tests (6 tests)
# ===================================================================


class TestMetrics:
    """Prometheus metric registration and label safety."""

    def test_metrics_endpoint_exists(self) -> None:
        """The /metrics mount point is registered on the app."""
        from agentblue.main import create_app

        app = create_app()
        route_paths = [r.path for r in app.routes]
        # make_asgi_app mounts at /metrics
        assert any("/metrics" in p for p in route_paths)

    def test_app_info_registered(self) -> None:
        """APP_INFO Info metric is importable and has the right name."""
        assert metrics_module.APP_INFO._name == "agentblue"

    def test_http_request_counter_registered(self) -> None:
        """HTTP_REQUESTS Counter is registered with correct name."""
        # prometheus_client strips _total suffix from _name
        assert metrics_module.HTTP_REQUESTS._name == "agentblue_http_requests"

    def test_workflow_transition_counter_registered(self) -> None:
        """TRANSITIONS Counter is registered with correct name."""
        assert metrics_module.TRANSITIONS._name == "agentblue_workflow_transitions"

    def test_security_failure_counter_registered(self) -> None:
        """AUTH_FAILURES Counter is registered with correct name."""
        assert metrics_module.AUTH_FAILURES._name == "agentblue_auth_failures"

    def test_all_metric_labels_are_strings(self) -> None:
        """All label names across all metrics are strings, no PII."""
        pii_like = {"email", "user_id", "ssn", "password", "secret", "token"}
        for name in dir(metrics_module):
            obj = getattr(metrics_module, name)
            if hasattr(obj, "_labelnames"):
                labels = obj._labelnames
                for label in labels:
                    assert isinstance(label, str), f"{name} label {label!r} is not a string"
                    assert label.lower() not in pii_like, (
                        f"{name} has PII-like label {label!r}"
                    )


# ===================================================================
# D. Production-Safety Tests (6 tests)
# ===================================================================


class TestProductionSafety:
    """Runtime enforcement of production and production-shadow safety."""

    def test_production_refuses_automatic_approval(self) -> None:
        """Settings creation fails for production + automatic_approval."""
        with pytest.raises(ValidationError, match="automatic_approval_enabled"):
            _make_settings(
                app_env="production",
                jwt_secret_key=GOOD_JWT,
                automatic_approval_enabled=True,
            )

    def test_production_refuses_autonomous_writeback(self) -> None:
        """Settings creation fails for production + autonomous_writeback."""
        with pytest.raises(ValidationError, match="autonomous_writeback_enabled"):
            _make_settings(
                app_env="production",
                jwt_secret_key=GOOD_JWT,
                autonomous_writeback_enabled=True,
            )

    def test_production_refuses_ml_promotion(self) -> None:
        """Settings creation fails for production + ml_promotion."""
        with pytest.raises(ValidationError, match="ml_promotion_enabled"):
            _make_settings(
                app_env="production",
                jwt_secret_key=GOOD_JWT,
                ml_promotion_enabled=True,
            )

    def test_shadow_refuses_automatic_approval(self) -> None:
        """Settings creation fails for production-shadow + automatic_approval."""
        with pytest.raises(ValidationError, match="automatic_approval_enabled"):
            _make_settings(
                app_env="production-shadow",
                jwt_secret_key=GOOD_JWT,
                automatic_approval_enabled=True,
            )

    def test_record_startup_complete_sets_state(self) -> None:
        """record_startup_complete flips the module global to True."""
        _reset_startup_state()
        assert health_module._startup_checks_passed is False
        record_startup_complete()
        assert health_module._startup_checks_passed is True

    def test_startup_probe_reflects_state(self) -> None:
        """Startup probe returns the current startup state."""
        _reset_startup_state()

        # Before startup
        assert health_module._startup_checks_passed is False

        # After startup
        record_startup_complete()
        assert health_module._startup_checks_passed is True
        # Also confirm monotonic time was captured
        assert health_module._startup_time > 0
