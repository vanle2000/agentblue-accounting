# Project Context

## Overview

Agent Blue is a modular AI platform for accounting, property management,
data analytics, and workflow automation.

The first production agent is the QuickBooks Transaction Categorization
Agent. Future agents include Buildium integration, vendor management,
maintenance automation, financial reporting, executive dashboard, data
analytics pipeline, document intelligence, and communication agents.

The architecture must support future expansion.

## Repository

- Repository: `agentblue-accounting`
- Branch: `master` (stable integration branch)
- Initial commit: `a2f9b1a`

## Stack

| Component         | Technology              |
|-------------------|-------------------------|
| Language          | Python 3.12             |
| Framework         | FastAPI 0.115           |
| ORM               | SQLAlchemy 2.x async    |
| Database          | PostgreSQL 16           |
| Migrations        | Alembic 1.15            |
| Validation        | Pydantic v2             |
| Settings          | pydantic-settings       |
| Logging           | structlog               |
| Containerization  | Docker Compose          |
| Linter            | Ruff                    |
| Type Checker      | Mypy (strict mode)      |
| Test Framework    | Pytest + pytest-asyncio |
| Pre-commit Hooks  | pre-commit              |

## Implemented Capabilities

- FastAPI application factory with lifespan management.
- Async SQLAlchemy engine and session with dependency injection.
- Liveness endpoint: `GET /api/v1/health/live` (no database check).
- Readiness endpoint: `GET /api/v1/health/ready` (verifies PostgreSQL).
- Structured logging via structlog with automatic secret redaction.
- Environment-based configuration via pydantic-settings and `.env`.
- Docker Compose with PostgreSQL 16-alpine and Uvicorn hot reload.
- Alembic async migration environment (no migration versions yet).
- Unit and integration test scaffolding.
- Architecture Decision Record: `docs/adr/0001-initial-architecture.md`.

## Current Milestone

Stage 5: QuickBooks Transaction Synchronization (implemented).

## Next Milestone

Stage 6: Chart of Accounts and accounting context.

## Known Infrastructure Issues

None. Docker outbound networking and Alembic port mismatch are resolved.

## Directory Structure

```
agentblue-accounting/
  app/
    agentblue/
      __init__.py
      main.py           # FastAPI app factory
      config.py          # pydantic-settings configuration
      logging.py         # structlog configuration
      api/
        __init__.py
        health.py        # Liveness and readiness endpoints
      db/
        __init__.py
        base.py          # SQLAlchemy DeclarativeBase
        session.py       # Async engine and session factory
        models/
          __init__.py    # Future models package
  migrations/
    __init__.py
    env.py               # Alembic async environment
    script.py.mako
    versions/.gitkeep
  tests/
    __init__.py
    conftest.py
    unit/
      __init__.py
      test_health_live.py
    integration/
      __init__.py
      test_health_ready.py
      test_health_ready_failure.py
  docs/
    adr/
      0001-initial-architecture.md
  scripts/.gitkeep
  .dockerignore
  .env.example
  .gitattributes
  .gitignore
  .pre-commit-config.yaml
  alembic.ini
  docker-compose.yml
  docker-compose.override.yml
  Dockerfile
  Makefile
  pyproject.toml
  README.md
```
