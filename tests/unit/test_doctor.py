"""Tests for scripts/doctor.py - Environment diagnostic tool."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so doctor.py is importable.
_scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from doctor import (  # noqa: E402
    Report,
    _is_sensitive_key,
    redact,
    redact_db_url,
    run_doctor,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


class TestRedact:
    """Test the redact() function."""

    def test_short_value(self) -> None:
        assert redact("ab") == "***"
        assert redact("a") == "***"
        assert redact("") == "***"

    def test_normal_value(self) -> None:
        result = redact("agentblue")
        assert result == "a***e"
        assert "agentblue" not in result

    def test_password_not_exposed(self) -> None:
        secret = "SuperSecretPassword123"
        result = redact(secret)
        assert secret not in result
        assert result.startswith("S")
        assert result.endswith("3")

    def test_single_char_password(self) -> None:
        assert redact("x") == "***"


class TestRedactDbUrl:
    """Test database URL redaction."""

    def test_standard_url(self) -> None:
        url = "postgresql+asyncpg://user:mypass@localhost:5433/db"
        result = redact_db_url(url)
        assert "mypass" not in result
        assert "user" in result
        assert "localhost" in result
        assert "5433" in result
        assert "db" in result
        assert "***" in result

    def test_complex_password(self) -> None:
        url = "postgresql+asyncpg://admin:p@ss!w0rd@db-host:5432/mydb"
        result = redact_db_url(url)
        assert "p@ss!w0rd" not in result
        assert "admin" in result
        assert "db-host" in result

    def test_no_credentials(self) -> None:
        url = "sqlite:///local.db"
        result = redact_db_url(url)
        assert result == url  # unchanged


class TestIsSensitiveKey:
    """Test sensitive key detection."""

    def test_sensitive_keys(self) -> None:
        assert _is_sensitive_key("password") is True
        assert _is_sensitive_key("db_password") is True
        assert _is_sensitive_key("DB_PASSWORD") is True
        assert _is_sensitive_key("api_key") is True
        assert _is_sensitive_key("token") is True
        assert _is_sensitive_key("secret") is True
        assert _is_sensitive_key("access_token") is True

    def test_non_sensitive_keys(self) -> None:
        assert _is_sensitive_key("DB_HOST") is False
        assert _is_sensitive_key("DB_PORT") is False
        assert _is_sensitive_key("APP_ENV") is False
        assert _is_sensitive_key("LOG_LEVEL") is False


# ---------------------------------------------------------------------------
# Report classification
# ---------------------------------------------------------------------------


class TestReport:
    """Test the Report data structure."""

    def test_empty_report(self) -> None:
        report = Report()
        assert report.has_failures is False
        assert report.has_warnings is False
        assert report.exit_code() == 0

    def test_pass_only(self) -> None:
        report = Report()
        report.pass_("Test", "item", "ok")
        assert report.has_failures is False
        assert report.has_warnings is False
        assert report.exit_code() == 0

    def test_warn_only(self) -> None:
        report = Report()
        report.warn("Test", "item", "warning")
        assert report.has_failures is False
        assert report.has_warnings is True
        assert report.exit_code() == 0  # warnings are not failures

    def test_fail(self) -> None:
        report = Report()
        report.fail("Test", "item", "failure")
        assert report.has_failures is True
        assert report.exit_code() == 1

    def test_fail_overrides_warn(self) -> None:
        report = Report()
        report.warn("Test", "item1", "warning")
        report.fail("Test", "item2", "failure")
        assert report.has_failures is True
        assert report.exit_code() == 1

    def test_json_output(self) -> None:
        report = Report()
        report.pass_("Cat", "item", "ok")
        data = json.loads(report.to_json())
        assert len(data) == 1
        assert data[0]["status"] == "PASS"
        assert data[0]["category"] == "Cat"
        assert data[0]["name"] == "item"
        assert data[0]["message"] == "ok"


# ---------------------------------------------------------------------------
# Doctor orchestration
# ---------------------------------------------------------------------------


class TestRunDoctor:
    """Test the run_doctor orchestrator."""

    def test_full_report_has_system_checks(self) -> None:
        report = run_doctor()
        categories = {c.category for c in report.checks}
        assert "System" in categories
        assert "Python" in categories
        assert "Git" in categories

    def test_single_group(self) -> None:
        report = run_doctor(check_group="python")
        categories = {c.category for c in report.checks}
        assert "Python" in categories
        # Other categories should not be present.
        assert "Docker" not in categories

    def test_unknown_group(self) -> None:
        report = run_doctor(check_group="nonexistent")
        assert report.has_failures is True
        assert any("Unknown group" in c.message for c in report.checks)

    def test_json_output_is_valid(self) -> None:
        report = run_doctor(check_group="python")
        data = json.loads(report.to_json())
        assert isinstance(data, list)
        assert len(data) > 0
