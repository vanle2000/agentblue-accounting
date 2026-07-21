# Architecture

## Pattern: Modular Monolith

Agent Blue uses a modular monolith: a single deployable unit with clear
internal package boundaries. Each module owns its interfaces and can be
tested independently.

Why not microservices: Stage 1 has no business logic, no background
workers, and no scaling concerns. Microservices would introduce network
boundaries, service discovery, distributed tracing, and deployment
complexity with no value at this stage. The modular monolith preserves
the option to extract services later when concrete scaling or
team-boundary requirements emerge.

See `docs/adr/0001-initial-architecture.md` for the full decision
record.

## Package Layout

```
app/agentblue/
  __init__.py       # Package version
  main.py           # FastAPI application factory and lifespan
  config.py         # pydantic-settings configuration
  logging.py        # structlog configuration with secret redaction
  api/              # HTTP layer (routers, request/response models)
    health.py       # Liveness and readiness probes
  db/               # Database layer
    base.py         # SQLAlchemy DeclarativeBase
    session.py      # Async engine, session factory, get_db dependency
    models/         # ORM models (empty until first schema)
```

## Layers

### API Layer (`agentblue.api`)

Owns HTTP routing, request validation, and response serialization.
Depends on the database layer via FastAPI's dependency injection.

### Database Layer (`agentblue.db`)

Owns the async SQLAlchemy engine, session factory, declarative base,
and ORM models. Exposes `get_db` as a FastAPI dependency for request-
scoped sessions.

### Configuration (`agentblue.config`)

Owns application settings loaded from environment variables and `.env`
via pydantic-settings. Validates configuration at startup and fails
early with clear errors.

### Logging (`agentblue.logging`)

Owns structlog configuration. Supports colored console output in
development and structured JSON in production. Automatically redacts
sensitive keys.

## Request Flow

```
Client -> FastAPI -> Router -> Depends(get_db) -> AsyncSession -> PostgreSQL
                                      |
                               Commit/Rollback
```

1. Client sends HTTP request.
2. FastAPI routes to the appropriate handler.
3. Handler declares `Depends(get_db)` for database access.
4. `get_db` yields an `AsyncSession` from the session factory.
5. Handler executes business logic and queries.
6. On success: session commits. On exception: session rolls back.
7. Response is serialized by Pydantic and returned.

## Database

- Engine: async via `create_async_engine` with `asyncpg`.
- Sessions: `async_sessionmaker` with `expire_on_commit=False`.
- Migrations: Alembic with async environment.
- Connection pooling: default pool with `pool_pre_ping=True`.

## Docker Architecture

### Development (`docker compose up -d`)

- `docker-compose.yml`: base configuration.
- `docker-compose.override.yml`: auto-merged. Adds bind mounts for
  `app/` and `migrations/`, enables Uvicorn `--reload`.
- PostgreSQL data persisted in named volume `agentblue-pgdata`.

### Production-like (`docker compose -f docker-compose.yml up -d`)

- Uses only the base configuration.
- Code is COPYed into the image during build.
- No bind mounts, no `--reload`.

### Services

| Service | Image              | Port | Healthcheck         |
|---------|--------------------|------|---------------------|
| api     | agentblue-api      | 8000 | /api/v1/health/live |
| db      | postgres:16-alpine | 5432 | pg_isready          |

## Test Architecture

- Unit tests: no external dependencies. Test application logic in
  isolation using FastAPI's `TestClient` via `httpx.AsyncClient`.
- Integration tests: require PostgreSQL via Docker Compose. Test
  database connectivity and failure modes.
- Fixtures: shared in `tests/conftest.py`. Provides `app` and `client`
  fixtures.
- Dependency override: integration failure tests override `get_db` with
  a broken session to test error handling deterministically.

## QuickBooks Integration Module

```
app/agentblue/integrations/
  __init__.py
  quickbooks/
    __init__.py
    config.py         # OAuth settings (pydantic-settings, SecretStr)
    exceptions.py     # QuickBooksConfigurationError, QuickBooksOAuthError
    oauth.py          # Authorization URL generation, state handling
```

### Module Responsibilities

- `config.py`: Loads QuickBooks OAuth settings from environment
  variables. Validates required fields, normalizes scopes, maps
  sandbox/production to Intuit endpoints. Uses SecretStr for
  sensitive fields.
- `exceptions.py`: Domain-specific exceptions with actionable messages
  that never expose secret values.
- `oauth.py`: Generates cryptographically secure state values and
  builds Intuit OAuth2 authorization URLs. Pure functions, no global
  mutable state.

### Security Design

- Sensitive fields use `SecretStr` to prevent accidental exposure.
- Validation error messages identify missing settings by environment
  variable name, never by value.
- Authorization URLs never include the client secret.
- State values use `secrets.token_urlsafe(32)` for 256 bits of entropy.
- No secrets appear in logs, repr output, or test assertions.
