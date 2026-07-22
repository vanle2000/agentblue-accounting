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

### Stage 3B: QuickBooks OAuth Callback, Token Exchange, and Token Lifecycle

- Implement OAuth callback validation with constant-time state comparison.
- Implement authorization-code exchange via async httpx client.
- Implement token response models with expiration calculations.
- Implement token refresh with retry for transient failures.
- Define TokenRepository protocol with in-memory implementation.
- Add FastAPI endpoints for authorize and callback.
- Expand exception hierarchy (callback, state mismatch, token errors).
- Add comprehensive unit tests with mocked HTTP responses.
- Document token security design and deferred items.

### Stage 4: Production QuickBooks API Client

- Build authenticated async API client with httpx.
- Implement automatic token refresh on expiry and 401.
- Implement retry with exponential backoff for transient failures.
- Implement rate limiting with Retry-After support.
- Implement pagination via STARTPOSITION/MAXRESULTS.
- Map HTTP errors to domain exceptions with status codes and Intuit TID.
- Create service wrappers (CompanyInfo implemented; others as interfaces).
- Add health check endpoint for token and company reachability.
- Add 28 new unit tests with mocked HTTP.
- Document API client architecture and retry strategy.

### Stage 5: QuickBooks Transaction Synchronization

- Implement entity registry for all 12 supported transaction types.
- Implement normalization adapters for Purchase, Deposit, Transfer,
  JournalEntry, Bill, BillPayment, Payment, SalesReceipt, RefundReceipt,
  CreditMemo, VendorCredit, Invoice.
- Implement source snapshot, canonical transaction, and transaction line
  persistence with idempotent upserts.
- Implement sync checkpoint with optimistic concurrency.
- Implement backfill service using paginated Query API.
- Implement incremental CDC sync with overlap windows and window splitting.
- Implement sync run audit trail with per-entity result tracking.
- Add Alembic migration for 6 new tables.
- Add FastAPI sync endpoints (backfill, incremental, status).
- Add 37 unit tests covering registry, normalization, query builder,
  sync service, deletion handling, and security.

### Stage 6: QuickBooks Chart of Accounts and Accounting Context

- Implement Account source snapshot persistence.
- Implement canonical Account persistence with full field mapping.
- Implement Account normalization with safe Decimal handling.
- Implement parent/subaccount hierarchy resolution.
- Implement account validation service.
- Implement account candidate filtering service.
- Implement account usage evaluation service.
- Implement transaction account-reference resolution.
- Implement Account backfill via paginated Query API.
- Implement Account incremental CDC sync.
- Add Alembic migration for 3 new tables.
- Add FastAPI accounting context endpoints.
- Add 19 unit tests covering normalization, validation, usage, and security.

### Stage 7: Intelligent Transaction Categorization

- Implement vendor and text normalization (Unicode, legal suffixes, processor prefixes).
- Implement deterministic rule engine (vendor, keyword, amount, composite rules).
- Implement scoring model with configurable confidence thresholds.
- Implement categorization engine with rule evaluation and candidate ranking.
- Implement human review workflow (approve, change, reject, defer).
- Implement training label capture for future ML.
- Add categorization ORM models (7 tables).
- Add Alembic migration for categorization schema.
- Add FastAPI categorization endpoints.
- Add 31 unit tests covering normalization, rules, scoring, and security.

## Pending

- Stage 8: ML-assisted categorization.
- Stage 3D: Transaction Categorization Agent.
- Buildium Integration Agent.
- Vendor Management Agent.
- Maintenance Automation Agent.
- Financial Reporting Agent.
- Executive Dashboard Agent.
- Data Analytics Pipeline Agent.
- Document Intelligence Agent.
- Communication Agents.
