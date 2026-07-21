# ADR 0001: Initial Architecture

## Status

Accepted

## Context

Agent Blue Accounting is a property management accounting system that will
eventually integrate with QuickBooks, Buildium, and transaction
classification logic. Stage 1 must establish a clean, testable foundation
without premature complexity.

The development environment runs on Windows 10 with Python 3.12.10
locally and Python 3.12 in Docker. Hermes Agent (Python 3.11.15) assists
with development but must remain isolated from the application runtime.

## Decision

### Modular monolith as the initial architecture

We adopt a modular monolith: a single deployable unit with clear internal
package boundaries (`agentblue.api`, `agentblue.db`, `agentblue.config`,
`agentblue.logging`). Each module owns its interfaces and can be tested
independently.

**Why not microservices:** Stage 1 has no business logic, no background
workers, and no scaling concerns. Microservices would introduce network
boundaries, service discovery, distributed tracing, and deployment
complexity that provide no value at this stage. The modular monolith
preserves the option to extract services later when concrete scaling or
team-boundary requirements emerge.

### FastAPI for the API layer

FastAPI provides native async support, automatic OpenAPI documentation,
Pydantic v2 integration, and a clean dependency injection system. Its
type-driven design aligns with our goal of a fully typed codebase.

**Alternatives considered:**
- **Django:** Heavier framework with ORM, admin, and template layers we
  do not need. Django's async support is less mature than FastAPI's.
- **Flask:** Lacks native async and type-driven request validation.
  Would require significant extension to match FastAPI's features.
- **Litestar:** Strong alternative but smaller ecosystem and community
  compared to FastAPI.

### PostgreSQL as the database

PostgreSQL is the industry standard for financial applications due to
ACID compliance, mature JSON support, and robust concurrent-access
handling. It supports async drivers (asyncpg) and has excellent Docker
support.

**Alternatives considered:**
- **SQLite:** Insufficient for concurrent writes and lacks async driver
  support for production workloads.
- **MySQL:** Viable but PostgreSQL's JSONB, array types, and
  financial-precision numeric handling are superior for accounting.

### SQLAlchemy 2.x async with asyncpg

SQLAlchemy 2.x provides a modern async API with full type annotation
support. asyncpg is the fastest async PostgreSQL driver for Python.
Together they enable non-blocking database access that integrates
cleanly with FastAPI's async request handling.

**Alternatives considered:**
- **ORM-less (raw SQL):** Loses type safety, migration support, and
  developer productivity for complex queries.
- **Tortoise ORM:** Less mature, smaller community, weaker migration
  story than SQLAlchemy + Alembic.
- **psycopg3 (async):** Viable but asyncpg is faster and more widely
  used in the FastAPI ecosystem.

### Pydantic Settings for environment configuration

pydantic-settings provides typed, validated configuration loaded from
environment variables and `.env` files. It integrates natively with
Pydantic v2 and supports caching via `lru_cache`.

**Alternatives considered:**
- **python-dotenv + os.environ:** No type validation, no defaults, error-
  prone manual parsing.
- **Dynaconf:** More complex than needed for a single-environment Stage 1.

### structlog for structured logging

structlog produces machine-readable structured logs with a clean
processor pipeline. It integrates with the standard logging module and
supports both JSON (production) and colored console (development) output.

**Alternatives considered:**
- **Standard logging + JSON formatter:** More boilerplate, less
  composable processor chain.
- **Loguru:** Attractive API but less structured-output focused and
  harder to integrate with standard logging.

### Docker Compose for the local authoritative runtime

Docker Compose provides a consistent Linux environment that matches
future production deployment. It manages PostgreSQL, health checks,
service dependencies, and port mappings in a single declarative file.

**Why Docker is authoritative:** Local Windows development introduces
path-separation, line-ending, and system-library differences that Docker
eliminates. Running the application in Docker during development catches
deployment issues early.

**Alternatives considered:**
- **Local-only PostgreSQL:** Faster startup but introduces Windows-
  specific configuration drift.
- **Kubernetes (kind/minikube):** Premature complexity for local
  development.

### Python 3.12 for the application

Python 3.12 offers performance improvements, better error messages, and
full support for all required dependencies. It is the latest Python
version with broad library compatibility.

### Hermes remaining on its isolated Python 3.11.15 runtime

Hermes Agent runs on Python 3.11.15 and must not be modified or upgraded
to accommodate Agent Blue. The two projects share no runtime dependencies
and must remain fully isolated. Hermes can edit files and run commands
but must not install Agent Blue packages into its own environment.

### Local .venv for VS Code and fast developer feedback

The `.venv` provides fast linting, type checking, and unit testing
without Docker overhead. It is created from the local Python 3.12.10
installation and serves as the VS Code interpreter target.

### No QuickBooks, Buildium, ML, LLM, or posting logic in Stage 1

Stage 1 establishes the foundation only. All integration, classification,
and financial-posting logic is deferred to later stages. No placeholder
implementations are created for out-of-scope features.

## Consequences

### Positive

- Clean separation between application layers (api, db, config, logging).
- Full async request path from FastAPI through SQLAlchemy to PostgreSQL.
- Typed codebase validated by mypy in strict mode.
- Structured logging with automatic secret redaction.
- Docker-based development that mirrors production.
- Fast local feedback loop via .venv for linting and unit tests.
- Comprehensive test coverage with deterministic failure testing.
- Clear migration path via Alembic for future schema changes.
- Modular monolith boundaries enable future service extraction.

### Negative

- Docker Compose startup adds latency compared to purely local development.
- Two Python versions (3.11.15 for Hermes, 3.12 for Agent Blue) require
  careful command documentation.
- async SQLAlchemy + Alembic configuration is more complex than sync
  equivalents.
- No business logic in Stage 1 means limited end-to-end validation.

### Risks

- Developers may accidentally use Python 3.14 (Hermes) instead of 3.12.
  Mitigated by explicit documentation and `.venv` activation guidance.
- Package version drift between local and Docker environments.
  Mitigated by pinning Python 3.12 in both environments and testing in
  Docker as the authoritative runtime.
