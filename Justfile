# Agent Blue Accounting - Developer Task Runner
# Requires: https://github.com/casey/just
# Usage: just <command>
# Cross-platform: works on Windows, Linux, and macOS.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Default: show available commands.
default:
    @just --list --unsorted

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

# Verify Python version, install dev dependencies, install pre-commit hooks.
setup:
    @python scripts/doctor.py --check python
    pip install -e ".[dev]"
    @-pre-commit install 2>/dev/null || echo "pre-commit not available (optional)"

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

# Run Ruff linter.
lint:
    ruff check .

# Run Ruff safe auto-fixes.
lint-fix:
    ruff check --fix .

# Run Ruff formatter.
format:
    ruff format .

# Check formatting without modifying files.
format-check:
    ruff format --check .

# Run Mypy type checker.
typecheck:
    mypy app/

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

# Run the complete test suite.
test:
    pytest -vv

# Run unit tests only.
test-unit:
    pytest -m unit -vv

# Run integration tests only.
test-integration:
    pytest -m integration -vv

# Run unit tests (fast local feedback).
test-fast:
    pytest -m unit -q

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

# Run the full quality gate: lint, format-check, typecheck, test, compose-check.
verify:
    @echo "=== Ruff Lint ==="
    ruff check .
    @echo "=== Ruff Format Check ==="
    ruff format --check .
    @echo "=== Mypy ==="
    mypy app/
    @echo "=== Pytest ==="
    pytest -vv
    @echo "=== Docker Compose Config ==="
    docker compose config --quiet
    @echo "=== All checks passed ==="

# ---------------------------------------------------------------------------
# Docker Compose
# ---------------------------------------------------------------------------

# Validate docker-compose configuration.
compose-check:
    docker compose config --quiet

# Build Docker services (does not delete volumes).
docker-build:
    docker compose build

# Start Docker services in detached mode.
docker-up:
    docker compose up -d

# Stop Docker services (preserves volumes).
docker-down:
    docker compose down

# Restart Docker services (preserves volumes).
docker-restart:
    docker compose down
    docker compose up -d

# Show Docker service status.
docker-ps:
    docker compose ps

# Follow API logs.
docker-logs:
    docker compose logs -f api

# Follow all service logs.
docker-logs-all:
    docker compose logs -f

# ---------------------------------------------------------------------------
# Health Checks
# ---------------------------------------------------------------------------

# Check liveness endpoint.
health-live:
    @python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health/live'); print(r.read().decode()); sys.exit(0 if r.status==200 else 1)"

# Check readiness endpoint.
health-ready:
    @python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health/ready'); print(r.read().decode()); sys.exit(0 if r.status==200 else 1)"

# Check both liveness and readiness endpoints.
health: health-live health-ready

# ---------------------------------------------------------------------------
# Database (Alembic)
# ---------------------------------------------------------------------------

# Run all pending migrations.
db-upgrade:
    alembic upgrade head

# Downgrade one migration (WARNING: modifies database schema).
db-downgrade:
    @echo "WARNING: This will downgrade the database schema by one revision."
    alembic downgrade -1

# Show current migration revision.
db-current:
    alembic current

# Show migration history.
db-history:
    alembic history

# Create a new migration (requires MSG argument).
db-revision MSG="":
    @if [ -z "{{MSG}}" ]; then echo "ERROR: Migration message required. Usage: just db-revision MSG=\"description\""; exit 1; fi
    alembic revision --autogenerate -m "{{MSG}}"

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

# Run full environment diagnostic.
doctor:
    @python scripts/doctor.py

# ---------------------------------------------------------------------------
# Project Status
# ---------------------------------------------------------------------------

# Show Git branch, status, Docker status, and Alembic revision.
status:
    @echo "=== Git ==="
    @git branch --show-current
    @git status --short
    @echo ""
    @echo "=== Docker Compose ==="
    @docker compose ps 2>/dev/null || echo "Docker Compose not available"
    @echo ""
    @echo "=== Alembic ==="
    @alembic current 2>/dev/null || echo "Alembic not available or database unreachable"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

# Remove generated development artifacts (caches, bytecode).
clean:
    @echo "Removing generated artifacts..."
    @python -c "import shutil, pathlib; [shutil.rmtree(p) for p in [pathlib.Path('__pycache__'), pathlib.Path('.pytest_cache'), pathlib.Path('.mypy_cache'), pathlib.Path('.ruff_cache')] if p.exists()]; [f.unlink() for d in [pathlib.Path('.')] for f in d.rglob('*.pyc')]"
    @echo "Done."

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

# Display available commands with descriptions.
help:
    @echo "Agent Blue Accounting - Developer Commands"
    @echo ""
    @echo "  just setup              Verify Python, install deps, install hooks"
    @echo "  just lint               Run Ruff linter"
    @echo "  just lint-fix           Run Ruff safe auto-fixes"
    @echo "  just format             Run Ruff formatter"
    @echo "  just format-check       Check formatting without modifying files"
    @echo "  just typecheck          Run Mypy type checker"
    @echo "  just test               Run complete test suite"
    @echo "  just test-unit          Run unit tests only"
    @echo "  just test-integration   Run integration tests only"
    @echo "  just test-fast          Run unit tests (fast feedback)"
    @echo "  just verify             Run full quality gate"
    @echo "  just compose-check      Validate Docker Compose config"
    @echo "  just docker-build       Build Docker services"
    @echo "  just docker-up          Start Docker services"
    @echo "  just docker-down        Stop Docker services (preserves volumes)"
    @echo "  just docker-restart     Restart Docker services"
    @echo "  just docker-ps          Show Docker service status"
    @echo "  just docker-logs        Follow API logs"
    @echo "  just docker-logs-all    Follow all service logs"
    @echo "  just health-live        Check liveness endpoint"
    @echo "  just health-ready       Check readiness endpoint"
    @echo "  just health             Check both health endpoints"
    @echo "  just db-upgrade         Run all pending migrations"
    @echo "  just db-downgrade       Downgrade one migration (WARNING)"
    @echo "  just db-current         Show current migration revision"
    @echo "  just db-history         Show migration history"
    @echo "  just db-revision MSG=x  Create a new migration"
    @echo "  just doctor             Run full environment diagnostic"
    @echo "  just status             Show Git, Docker, and Alembic status"
    @echo "  just clean              Remove generated dev artifacts"
    @echo "  just help               This help message"
