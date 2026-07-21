# Tasks

## Completed

### Stage 1: Project Foundation

- Initialize FastAPI application factory with lifespan management.
- Configure pydantic-settings for environment-based configuration.
- Set up structlog with structured logging and secret redaction.
- Create async SQLAlchemy engine and session with dependency injection.
- Implement liveness endpoint (`GET /api/v1/health/live`).
- Implement readiness endpoint (`GET /api/v1/health/ready`).
- Configure Alembic async migration environment.
- Create Docker Compose with PostgreSQL 16-alpine.
- Create Dockerfile with non-root user and healthcheck.
- Configure Ruff, Mypy (strict), Pytest, and pre-commit.
- Add unit tests for liveness endpoint.
- Add integration tests for readiness endpoint (happy path and failure).
- Write ADR-0001: Initial Architecture.
- Write README.md with setup and usage instructions.
- Configure Docker Compose development workflow with bind mounts and
  Uvicorn hot reload.
- Create engineering documentation: master prompt, project context,
  architecture, workflow, and task tracker.
- Create initial Git commit.

### Stage 1A: Integration Test Repair

- Fix structlog `event` parameter collision in health endpoint.
- Add integration test conftest with Docker PostgreSQL credentials.
- Add structlog regression test.
- Resolve port conflict with native PostgreSQL (remap to 5433).

### Stage 1B: Developer Task Runner

- Create Justfile with all developer commands.
- Create scripts/dev.ps1 PowerShell wrapper.
- Create scripts/doctor.py environment diagnostic tool.
- Add doctor unit tests (redaction, classification, orchestration).
- Document task runner in README.md.

## Pending

### Stage 2: Stabilization Complete

- Align host PostgreSQL port in .env with Docker port mapping.
- Resolve Docker PyPI networking (confirmed working).
- Define first scoped QuickBooks milestone.

### Stage 3A: QuickBooks OAuth Configuration and Authorization URL

- Define QuickBooks OAuth configuration using pydantic-settings.
- Add placeholder variables to `.env.example`.
- Validate required configuration at startup.
- Generate a QuickBooks authorization URL.
- Define OAuth-related exceptions.
- Add unit tests using fake credentials.
- Document the authorization flow.

## Pending

- Stage 3B: QuickBooks Token Exchange and Storage.
- Stage 3C: QuickBooks Transaction Sync.
- Stage 3D: Transaction Categorization Agent.
- Buildium Integration Agent.
- Vendor Management Agent.
- Maintenance Automation Agent.
- Financial Reporting Agent.
- Executive Dashboard Agent.
- Data Analytics Pipeline Agent.
- Document Intelligence Agent.
- Communication Agents.
