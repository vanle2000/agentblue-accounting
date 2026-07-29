"""Comprehensive tests for Stage 11 observability, backup, and operational verification.

Covers: logging redaction, tracing, metrics, dashboards, alerts, and backup.
All tests pass without a real database.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agentblue.observability.backup import (
    BackupConfig,
    BackupResult,
    BackupService,
    RestoreResult,
)
from agentblue.observability.dashboards import (
    get_alert_rules,
    get_dashboard_definitions,
    validate_alert_rules,
    validate_dashboard_json,
)
from agentblue.observability.logging import (
    _REDACTED,
    _SENSITIVE_FIELDS,
    redact_dict,
    redact_processor,
    redact_value,
)
from agentblue.observability.metrics import (
    APP_INFO,
    AUTH_FAILURES,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    RECONCILIATIONS,
    TRANSITIONS,
    WORK_ITEMS_CREATED,
    WRITEBACK_JOBS,
)
from agentblue.observability.tracing import (
    _NoOpSpan,
    configure_tracing,
    get_trace_id,
    is_tracing_enabled,
    trace_operation,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# A. Redaction Tests
# ---------------------------------------------------------------------------


class TestRedaction:
    """Tests for sensitive field and value redaction."""

    def test_password_field_redacted(self):
        assert redact_value("password", "hunter2") == _REDACTED

    def test_token_field_redacted(self):
        assert redact_value("token", "abc123") == _REDACTED

    def test_secret_field_redacted(self):
        assert redact_value("secret", "mysecret") == _REDACTED

    def test_authorization_field_redacted(self):
        assert redact_value("authorization", "some-value") == _REDACTED

    def test_access_token_redacted(self):
        assert redact_value("access_token", "tok_value") == _REDACTED

    def test_refresh_token_redacted(self):
        assert redact_value("refresh_token", "ref_value") == _REDACTED

    def test_db_password_redacted(self):
        assert redact_value("db_password", "dbpass") == _REDACTED

    def test_bearer_token_pattern_redacted(self):
        assert redact_value("header", "Bearer eyJhbGciOiJIUzI1NiJ9.abc") == _REDACTED

    def test_db_url_pattern_redacted(self):
        url = "postgresql+asyncpg://user:pass@host:5432/db"
        assert redact_value("database_url", url) == _REDACTED

    def test_jwt_pattern_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        assert redact_value("some_field", jwt) == _REDACTED

    def test_non_sensitive_fields_preserved(self):
        assert redact_value("username", "alice") == "alice"
        assert redact_value("count", 42) == 42

    def test_nested_dict_redacted(self):
        data = {"user": {"password": "secret123", "name": "alice"}}
        result = redact_dict(data)
        assert result["user"]["password"] == _REDACTED
        assert result["user"]["name"] == "alice"

    def test_list_values_redacted(self):
        data = {"password": ["abc", "def"]}
        result = redact_dict(data)
        assert result["password"] == [_REDACTED, _REDACTED]

    def test_empty_dict_returns_empty(self):
        assert redact_dict({}) == {}

    def test_redact_processor_delegates(self):
        event = {"event": "test", "password": "secret", "msg": "hello"}
        result = redact_processor(None, None, event)
        assert result["password"] == _REDACTED
        assert result["msg"] == "hello"

    def test_all_sensitive_fields_in_set(self):
        expected = {
            "password", "secret", "token", "authorization", "access_token",
            "refresh_token", "api_key", "client_secret", "db_password",
            "jwt_secret_key", "jwt", "bearer", "credit_card", "ssn",
            "bank_account", "account_number",
        }
        assert expected == _SENSITIVE_FIELDS


# ---------------------------------------------------------------------------
# B. Tracing Tests
# ---------------------------------------------------------------------------


class TestTracing:
    """Tests for tracing configuration and span management."""

    @pytest.fixture(autouse=True)
    def _reset_tracing(self):
        """Ensure tracing is disabled before and after each test."""
        configure_tracing(enabled=False)
        yield
        configure_tracing(enabled=False)

    def test_tracing_disabled_by_default(self):
        assert is_tracing_enabled() is False

    def test_configure_tracing_disabled_keeps_disabled(self):
        configure_tracing(enabled=False)
        assert is_tracing_enabled() is False

    def test_trace_operation_yields_noop_when_disabled(self):
        with trace_operation("test_op") as span:
            assert isinstance(span, _NoOpSpan)
            # NoOpSpan methods should not raise
            span.set_attribute("k", "v")
            span.set_status(None)
            span.record_exception(RuntimeError("x"))

    def test_is_tracing_enabled_returns_false_when_disabled(self):
        configure_tracing(enabled=False)
        assert is_tracing_enabled() is False

    def test_get_trace_id_returns_empty_when_disabled(self):
        configure_tracing(enabled=False)
        assert get_trace_id() == ""

    def test_tracing_degrades_safely_without_opentelemetry(self):
        """If opentelemetry is not installed, configure_tracing(enabled=True) should not raise."""
        with patch.dict("sys.modules", {"opentelemetry": None, "opentelemetry.trace": None, "opentelemetry.sdk": None, "opentelemetry.sdk.trace": None, "opentelemetry.sdk.trace.export": None}):
            # Should not raise — degrades gracefully
            configure_tracing(enabled=True)
            assert is_tracing_enabled() is False


# ---------------------------------------------------------------------------
# C. Metrics Tests
# ---------------------------------------------------------------------------


class TestMetrics:
    """Tests for Prometheus metric registration."""

    def test_app_info_registered(self):
        assert APP_INFO is not None
        assert "agentblue" in APP_INFO._name

    def test_http_requests_counter_registered(self):
        assert HTTP_REQUESTS is not None
        assert "agentblue_http_requests" in HTTP_REQUESTS._name

    def test_work_items_created_counter_registered(self):
        assert WORK_ITEMS_CREATED is not None
        assert "agentblue_work_items_created" in WORK_ITEMS_CREATED._name

    def test_transitions_counter_registered(self):
        assert TRANSITIONS is not None
        assert "agentblue_workflow_transitions" in TRANSITIONS._name

    def test_auth_failures_counter_registered(self):
        assert AUTH_FAILURES is not None
        assert "agentblue_auth_failures" in AUTH_FAILURES._name

    def test_all_metric_names_start_with_agentblue(self):
        metrics = [
            APP_INFO, HTTP_REQUESTS, HTTP_REQUEST_DURATION,
            WORK_ITEMS_CREATED, TRANSITIONS, AUTH_FAILURES,
            WRITEBACK_JOBS, RECONCILIATIONS,
        ]
        for metric in metrics:
            assert metric._name.startswith("agentblue"), f"{metric._name} does not start with 'agentblue_'"


# ---------------------------------------------------------------------------
# D. Dashboard Tests
# ---------------------------------------------------------------------------


class TestDashboards:
    """Tests for dashboard definitions."""

    def test_get_dashboard_definitions_returns_10(self):
        dashboards = get_dashboard_definitions()
        assert len(dashboards) == 10

    def test_each_dashboard_has_title_and_panels(self):
        dashboards = get_dashboard_definitions()
        for name, dashboard in dashboards.items():
            assert "title" in dashboard, f"Dashboard '{name}' missing title"
            assert "panels" in dashboard, f"Dashboard '{name}' missing panels"

    def test_each_panel_has_title_and_query(self):
        dashboards = get_dashboard_definitions()
        for name, dashboard in dashboards.items():
            for i, panel in enumerate(dashboard["panels"]):
                assert "title" in panel, f"Dashboard '{name}' panel {i} missing title"
                assert "query" in panel, f"Dashboard '{name}' panel {i} missing query"

    def test_validate_dashboard_json_passes_valid(self):
        dashboard = {
            "title": "Test Dashboard",
            "panels": [{"title": "Panel 1", "query": "up"}],
        }
        assert validate_dashboard_json(dashboard) is True

    def test_validate_dashboard_json_fails_missing_title(self):
        dashboard = {
            "panels": [{"title": "Panel 1", "query": "up"}],
        }
        assert validate_dashboard_json(dashboard) is False

    def test_no_pii_in_dashboard_queries(self):
        """Ensure no PII patterns (emails, SSNs, credit cards) appear in queries."""
        import re
        dashboards = get_dashboard_definitions()
        pii_patterns = [
            re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
            re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),  # credit card
            re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),  # email
        ]
        for name, dashboard in dashboards.items():
            for panel in dashboard["panels"]:
                for pattern in pii_patterns:
                    assert not pattern.search(panel["query"]), (
                        f"PII pattern found in dashboard '{name}' panel '{panel['title']}'"
                    )


# ---------------------------------------------------------------------------
# E. Alert Tests
# ---------------------------------------------------------------------------


class TestAlerts:
    """Tests for alert rule definitions."""

    def test_get_alert_rules_returns_groups(self):
        rules = get_alert_rules()
        assert "groups" in rules
        assert isinstance(rules["groups"], list)

    def test_each_group_has_name_and_rules(self):
        rules = get_alert_rules()
        for group in rules["groups"]:
            assert "name" in group
            assert "rules" in group

    def test_each_rule_has_required_fields(self):
        rules = get_alert_rules()
        for group in rules["groups"]:
            for rule in group["rules"]:
                for key in ("alert", "expr", "labels", "annotations"):
                    assert key in rule, f"Rule '{rule.get('alert', '?')}' missing '{key}'"

    def test_validate_alert_rules_passes_valid(self):
        rules = get_alert_rules()
        assert validate_alert_rules(rules) is True

    def test_critical_alerts_include_api_unavailable(self):
        rules = get_alert_rules()
        all_alerts = [
            rule["alert"]
            for group in rules["groups"]
            for rule in group["rules"]
        ]
        assert "AgentBlueAPIUnavailable" in all_alerts

    def test_warning_alerts_include_queue_aging(self):
        rules = get_alert_rules()
        all_alerts = [
            rule["alert"]
            for group in rules["groups"]
            for rule in group["rules"]
        ]
        assert "AgentBlueReviewQueueAging" in all_alerts


# ---------------------------------------------------------------------------
# F. Backup Tests
# ---------------------------------------------------------------------------


class TestBackup:
    """Tests for backup configuration, results, and service."""

    def test_backup_config_defaults(self):
        config = BackupConfig()
        assert config.db_host == "localhost"
        assert config.db_port == 5433
        assert config.db_user == "agentblue"
        assert config.db_name == "agentblue_dev"
        assert config.retention_days == 30

    def test_backup_result_to_dict_includes_all_fields(self):
        result = BackupResult(
            success=True,
            backup_path="/tmp/test.sql",
            checksum="abc123",
            size_bytes=1024,
            duration_seconds=1.5,
            timestamp="2025-01-01T00:00:00+00:00",
            error="",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["backup_path"] == "/tmp/test.sql"
        assert d["checksum"] == "abc123"
        assert d["size_bytes"] == 1024
        assert d["duration_seconds"] == 1.5
        assert d["timestamp"] == "2025-01-01T00:00:00+00:00"
        assert d["error"] == ""

    def test_restore_result_to_dict_includes_all_fields(self):
        result = RestoreResult(
            success=True,
            schema_valid=True,
            row_counts_match=True,
            constraints_valid=True,
            audit_integrity=True,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["schema_valid"] is True
        assert d["row_counts_match"] is True
        assert d["constraints_valid"] is True
        assert d["audit_integrity"] is True

    def test_verify_checksum_returns_false_for_missing_file(self):
        service = BackupService()
        assert service.verify_checksum("/nonexistent/path.sql", "abc") is False

    def test_verify_checksum_returns_true_for_matching(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as f:
            f.write(b"test backup content")
            path = f.name
        try:
            expected = hashlib.sha256(b"test backup content").hexdigest()
            service = BackupService()
            assert service.verify_checksum(path, expected) is True
            assert service.verify_checksum(path, "wrong") is False
        finally:
            Path(path).unlink()

    def test_list_backups_returns_empty_when_no_backups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = BackupConfig(backup_dir=tmpdir)
            service = BackupService(config)
            assert service.list_backups() == []
