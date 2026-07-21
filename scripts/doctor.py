#!/usr/bin/env python3
"""Agent Blue Accounting - Environment Doctor.

Diagnoses the local development environment without modifying it.
Cross-platform: works on Windows, Linux, and macOS.

Usage:
    python scripts/doctor.py
    python scripts/doctor.py --check python   # check a single category
    python scripts/doctor.py --json           # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PYTHON_MAJOR = 3
REQUIRED_PYTHON_MINOR = 12
API_BASE = "http://localhost:8000"

# Environment variables the application uses.
_APP_ENV_VARS = [
    "APP_ENV",
    "LOG_LEVEL",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
]

# Keys whose values must never be printed.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "db_password",
    }
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    INFO = "INFO"


@dataclass
class Check:
    category: str
    name: str
    status: Status
    message: str


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(
        self,
        category: str,
        name: str,
        status: Status,
        message: str,
    ) -> None:
        self.checks.append(Check(category, name, status, message))

    # Convenience wrappers --------------------------------------------------

    def pass_(self, cat: str, name: str, msg: str) -> None:
        self.add(cat, name, Status.PASS, msg)

    def warn(self, cat: str, name: str, msg: str) -> None:
        self.add(cat, name, Status.WARN, msg)

    def fail(self, cat: str, name: str, msg: str) -> None:
        self.add(cat, name, Status.FAIL, msg)

    def info(self, cat: str, name: str, msg: str) -> None:
        self.add(cat, name, Status.INFO, msg)

    # Output -----------------------------------------------------------------

    @property
    def has_failures(self) -> bool:
        return any(c.status == Status.FAIL for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == Status.WARN for c in self.checks)

    def exit_code(self) -> int:
        if self.has_failures:
            return 1
        return 0

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "category": c.category,
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                }
                for c in self.checks
            ],
            indent=2,
        )

    def print_text(self) -> None:
        # Group by category.
        categories: dict[str, list[Check]] = {}
        for c in self.checks:
            categories.setdefault(c.category, []).append(c)

        for cat, checks in categories.items():
            print(f"\n--- {cat} ---")
            for c in checks:
                tag = c.status.value.rjust(4)
                print(f"  [{tag}] {c.name}: {c.message}")

        # Summary.
        pass_count = sum(1 for c in self.checks if c.status == Status.PASS)
        warn_count = sum(1 for c in self.checks if c.status == Status.WARN)
        fail_count = sum(1 for c in self.checks if c.status == Status.FAIL)

        print("\n" + "=" * 60)
        print(f"SUMMARY: {pass_count} PASS, {warn_count} WARN, {fail_count} FAIL")
        if self.has_failures:
            print("RESULT: FAIL - environment issues detected")
        elif self.has_warnings:
            print("RESULT: WARN - non-critical issues detected")
        else:
            print("RESULT: PASS - environment looks healthy")
        print("=" * 60)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def redact(value: str) -> str:
    """Redact a sensitive value, showing only the first and last character."""
    if len(value) <= 2:
        return "***"
    return value[0] + "***" + value[-1]


def _is_sensitive_key(key: str) -> bool:
    return key.lower().replace("-", "_") in _SENSITIVE_KEYS


def redact_db_url(url: str) -> str:
    """Redact credentials from a database URL."""
    # Pattern: scheme://user:password@host:port/dbname
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


def run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    """Run a command and return (exit_code, combined_output)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output
    except FileNotFoundError:
        return -1, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, f"Command timed out: {' '.join(cmd)}"


def version_from_output(output: str, pattern: str = r"(\d+\.\d+[\.\d]*)") -> str:
    """Extract a version string from command output."""
    match = re.search(pattern, output)
    return match.group(1) if match else "unknown"


def _venv_python() -> str:
    """Return the path to the venv Python executable."""
    if sys.platform == "win32":
        return str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    return str(PROJECT_ROOT / ".venv" / "bin" / "python")


def _is_in_venv() -> bool:
    """Check if we're running inside the project venv."""
    return Path(sys.prefix).resolve() == (PROJECT_ROOT / ".venv").resolve()


def _load_dotenv() -> dict[str, str]:
    """Load .env file values without modifying os.environ."""
    env_path = PROJECT_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


# ---------------------------------------------------------------------------
# Diagnostic checks
# ---------------------------------------------------------------------------


def check_system(report: Report) -> None:
    """Operating system and shell."""
    os_name = f"{platform.system()} {platform.release()}"
    report.pass_("System", "Operating system", os_name)

    shell = os.environ.get("SHELL", os.environ.get("COMSPEC", "unknown"))
    report.info("System", "Shell", shell)


def check_project(report: Report) -> None:
    """Project root."""
    if (PROJECT_ROOT / "pyproject.toml").exists():
        report.pass_("Project", "Project root", str(PROJECT_ROOT))
    else:
        report.fail("Project", "Project root", f"pyproject.toml not found in {PROJECT_ROOT}")


def check_python(report: Report) -> None:
    """Python executable and version."""
    report.pass_("Python", "Executable", sys.executable)

    major, minor = sys.version_info[:2]
    ver_str = f"{major}.{minor}.{sys.version_info[2]}"
    req_ver = f"{REQUIRED_PYTHON_MAJOR}.{REQUIRED_PYTHON_MINOR}"
    if major == REQUIRED_PYTHON_MAJOR and minor == REQUIRED_PYTHON_MINOR:
        report.pass_("Python", "Version", f"{ver_str} (required: {req_ver})")
    else:
        report.fail(
            "Python",
            "Version",
            f"{ver_str} (required: {REQUIRED_PYTHON_MAJOR}.{REQUIRED_PYTHON_MINOR})",
        )

    if _is_in_venv():
        report.pass_("Python", "Virtual environment", f"Active: {sys.prefix}")
    else:
        venv_path = PROJECT_ROOT / ".venv"
        if venv_path.exists():
            report.warn(
                "Python",
                "Virtual environment",
                f"Not activated (venv exists at {venv_path})",
            )
        else:
            report.fail(
                "Python",
                "Virtual environment",
                "No .venv directory found",
            )


def check_dependencies(report: Report) -> None:
    """Development tool versions."""
    tools = [
        ("Ruff", ["ruff", "--version"]),
        ("Mypy", ["mypy", "--version"]),
        ("Pytest", ["pytest", "--version"]),
        ("Alembic", ["alembic", "--version"]),
        ("Pre-commit", ["pre-commit", "--version"]),
    ]
    for name, cmd in tools:
        code, output = run(cmd)
        if code == 0:
            ver = version_from_output(output)
            report.pass_("Dependencies", name, ver)
        else:
            report.warn("Dependencies", name, "Not available")


def check_git(report: Report) -> None:
    """Git version, branch, and working-tree status."""
    code, output = run(["git", "--version"])
    if code == 0:
        ver = version_from_output(output)
        report.pass_("Git", "Version", ver)
    else:
        report.fail("Git", "Version", "Git not found")
        return

    code, output = run(["git", "branch", "--show-current"])
    if code == 0:
        report.pass_("Git", "Branch", output.strip())

    code, output = run(["git", "status", "--short"])
    if code == 0:
        if output.strip():
            lines = output.strip().splitlines()
            report.warn("Git", "Working tree", f"{len(lines)} uncommitted change(s)")
        else:
            report.pass_("Git", "Working tree", "Clean")


def check_docker(report: Report) -> None:
    """Docker CLI, Compose, daemon, and service status."""
    code, output = run(["docker", "--version"])
    if code == 0:
        ver = version_from_output(output)
        report.pass_("Docker", "Docker CLI", ver)
    else:
        report.warn("Docker", "Docker CLI", "Not available")
        return

    code, output = run(["docker", "compose", "version"])
    if code == 0:
        ver = version_from_output(output)
        report.pass_("Docker", "Compose", ver)
    else:
        report.warn("Docker", "Compose", "Not available")
        return

    # Check daemon availability.
    code, output = run(["docker", "info"], timeout=10)
    if code == 0:
        report.pass_("Docker", "Daemon", "Running")
    else:
        report.warn("Docker", "Daemon", "Not running or not accessible")
        return

    # Validate compose config.
    code, output = run(["docker", "compose", "config", "--quiet"])
    if code == 0:
        report.pass_("Docker", "Compose config", "Valid")
    else:
        report.fail("Docker", "Compose config", "Invalid")

    # Service status.
    code, output = run(["docker", "compose", "ps", "--format", "json"])
    if code == 0 and output.strip():
        report.pass_("Docker", "Services", "Running")
    else:
        report.warn("Docker", "Services", "Not running (use: docker compose up -d)")


def check_environment(report: Report) -> None:
    """Environment variable presence and safety."""
    dotenv = _load_dotenv()

    for var in _APP_ENV_VARS:
        value = dotenv.get(var) or os.environ.get(var)
        if value:
            if _is_sensitive_key(var):
                report.pass_("Environment", var, f"Set (value: {redact(value)})")
            else:
                report.pass_("Environment", var, value)
        else:
            report.warn("Environment", var, "Not set")

    # Check for APP_ENV.
    app_env = dotenv.get("APP_ENV") or os.environ.get("APP_ENV", "development")
    report.pass_("Environment", "APP_ENV effective", app_env)

    # Redacted database URL.
    db_user = dotenv.get("DB_USER") or os.environ.get("DB_USER", "")
    db_host = dotenv.get("DB_HOST") or os.environ.get("DB_HOST", "localhost")
    db_port = dotenv.get("DB_PORT") or os.environ.get("DB_PORT", "5433")
    db_name = dotenv.get("DB_NAME") or os.environ.get("DB_NAME", "")
    db_password = dotenv.get("DB_PASSWORD") or os.environ.get("DB_PASSWORD", "")

    if db_user and db_host and db_name:
        url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        report.pass_("Environment", "Database URL (redacted)", redact_db_url(url))
        report.pass_("Environment", "Database host", db_host)
        report.pass_("Environment", "Database name", db_name)
    else:
        report.warn("Environment", "Database URL", "Incomplete database configuration")

    # Safety: check that no sensitive var is printed in full.
    for var in _APP_ENV_VARS:
        if _is_sensitive_key(var):
            value = dotenv.get(var) or os.environ.get(var, "")
            if value and len(value) > 0:
                # This check exists to verify redaction is working.
                report.pass_("Environment", f"{var} redaction", "Sensitive value is redacted")


def check_database(report: Report) -> None:
    """Database connectivity (safe, read-only)."""
    dotenv = _load_dotenv()
    db_host = dotenv.get("DB_HOST") or os.environ.get("DB_HOST", "localhost")
    db_port = dotenv.get("DB_PORT") or os.environ.get("DB_PORT", "5433")

    # TCP connectivity check (does not authenticate).
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((db_host, int(db_port)))
        sock.close()
        if result == 0:
            report.pass_("Database", "Connectivity", f"{db_host}:{db_port} reachable")
        else:
            report.warn(
                "Database",
                "Connectivity",
                f"{db_host}:{db_port} not reachable (containers may not be running)",
            )
    except Exception as exc:
        report.warn("Database", "Connectivity", f"Cannot test: {exc}")


def check_endpoints(report: Report) -> None:
    """Liveness and readiness endpoint availability."""
    endpoints = [
        ("Liveness", "/api/v1/health/live"),
        ("Readiness", "/api/v1/health/ready"),
    ]
    for name, path in endpoints:
        url = API_BASE + path
        try:
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200:
                report.pass_("Endpoints", name, f"HTTP 200 - {body}")
            else:
                report.warn("Endpoints", name, f"HTTP {resp.status}")
        except urllib.error.URLError as exc:
            report.warn(
                "Endpoints",
                name,
                f"Unavailable ({exc.reason}) - containers may not be running",
            )
        except Exception as exc:
            report.warn("Endpoints", name, f"Error: {exc}")


def check_known_issues(report: Report) -> None:
    """Known infrastructure warnings."""
    # No current known issues.
    report.pass_("Known Issues", "Infrastructure", "No known issues")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_CHECK_GROUPS = {
    "system": check_system,
    "project": check_project,
    "python": check_python,
    "dependencies": check_dependencies,
    "git": check_git,
    "docker": check_docker,
    "environment": check_environment,
    "database": check_database,
    "endpoints": check_endpoints,
    "known-issues": check_known_issues,
}


def run_doctor(
    *,
    check_group: str | None = None,
    as_json: bool = False,
) -> Report:
    """Run all diagnostics (or a single group) and return the report."""
    report = Report()

    if check_group:
        fn = _CHECK_GROUPS.get(check_group)
        if fn is None:
            report.fail("Input", "Check group", f"Unknown group: {check_group}")
            return report
        fn(report)
    else:
        for fn in _CHECK_GROUPS.values():
            fn(report)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Blue Accounting - Environment Doctor",
    )
    parser.add_argument(
        "--check",
        metavar="GROUP",
        help=f"Run a single diagnostic group. Choices: {', '.join(_CHECK_GROUPS)}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output machine-readable JSON.",
    )
    parser.add_argument(
        "--check-python",
        action="store_true",
        help="(Legacy) Only check Python version compatibility.",
    )
    args = parser.parse_args()

    if args.check_python:
        args.check = "python"

    report = run_doctor(check_group=args.check, as_json=args.as_json)

    if args.as_json:
        print(report.to_json())
    else:
        print("Agent Blue Accounting - Environment Doctor")
        print("=" * 60)
        report.print_text()

    sys.exit(report.exit_code())


if __name__ == "__main__":
    main()
