# Stage 9 Security Architecture

## Overview

Stage 9 adds authentication, authorization, realm isolation, and audit
identity to Agent Blue Accounting. Every protected API request now
requires an authenticated principal with explicit permissions and
realm assignments.

## Authentication

### Mechanism

- **JWT (JSON Web Tokens)** via `python-jose[cryptography]`
- Algorithm: HS256 (configurable)
- Signed with a secret key loaded from `JWT_SECRET_KEY` environment variable
- Validates: signature, expiration, issuer, audience, issued-at
- Clock skew tolerance: 30 seconds (configurable)

### Token Claims

| Claim | Description |
|-------|-------------|
| `sub` | Principal ID (required) |
| `principal_type` | "human" or "service" |
| `email` | Principal email |
| `name` | Display name |
| `active` | Account active status |
| `roles` | List of role strings |
| `realm_ids` | List of permitted realm IDs |
| `auth_method` | Authentication method used |
| `iss` | Issuer (must match configured issuer) |
| `aud` | Audience (must match configured audience) |
| `exp` | Expiration timestamp |
| `iat` | Issued-at timestamp |
| `jti` | Unique token ID |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET_KEY` | (empty) | Signing key — **required in production** (min 32 chars) |
| `JWT_ALGORITHM` | `HS256` | Signing algorithm |
| `JWT_ISSUER` | `agentblue-accounting` | Token issuer claim |
| `JWT_AUDIENCE` | `agentblue-api` | Token audience claim |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Token lifetime |
| `JWT_ALLOWED_CLOCK_SKEW_SECONDS` | `30` | Clock skew tolerance |
| `AUTH_BYPASS_ENABLED` | `false` | Development-only bypass |

### Development Mode

In development mode (`APP_ENV=development`), if `JWT_SECRET_KEY` is
empty, a random per-process key is generated. This means tokens are
valid only for the current process lifetime.

`AUTH_BYPASS_ENABLED=true` creates a default admin principal without
a token. **Never enable in production.**

### Production Requirements

- `JWT_SECRET_KEY` must be at least 32 characters
- `AUTH_BYPASS_ENABLED` must be `false` (or unset)
- Tokens must be obtained from a trusted identity provider
- Refresh token infrastructure is deferred to a future stage

## Principal Types

| Type | Description |
|------|-------------|
| `human` | Interactive user with email and roles |
| `service` | Machine principal with narrowly scoped permissions |

## Roles

| Role | Description |
|------|-------------|
| `VIEWER` | Read-only access to accounting data and ML shadow results |
| `ACCOUNTANT` | Review categorization recommendations, modify proposals |
| `APPROVER` | Approve accounting actions, authorize controlled write-back |
| `ML_OPERATOR` | Create datasets, train models, activate SHADOW mode |
| `ADMIN` | Manage users, roles, and realm assignments |
| `SERVICE_ACCOUNT` | Narrowly scoped machine permissions (deny by default) |

### Key Constraints

- **ADMIN does not bypass accounting controls.** ADMIN has
  `identity:manage` and `realm:manage` but NOT `accounting:writeback`.
- **ML_OPERATOR cannot write to QuickBooks.** ML_OPERATOR has
  `ml:train` and `ml:shadow:activate` but NOT `accounting:writeback`.
- **SERVICE_ACCOUNT has minimal permissions.** Only
  `quickbooks:read` and `accounting:read` by default.

## Permissions

| Permission | VIEWER | ACCOUNTANT | APPROVER | ML_OPERATOR | ADMIN | SERVICE |
|------------|--------|------------|----------|-------------|-------|---------|
| `accounting:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `accounting:review` | | ✓ | ✓ | | | |
| `accounting:approve` | | | ✓ | | | |
| `accounting:writeback` | | | ✓ | | | |
| `accounting:reconcile` | | ✓ | ✓ | | | |
| `quickbooks:read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `quickbooks:write` | | | | | | |
| `ml:read` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `ml:dataset:create` | | | | ✓ | ✓ | |
| `ml:train` | | | | ✓ | ✓ | |
| `ml:evaluate` | | | | ✓ | ✓ | |
| `ml:shadow:activate` | | | | ✓ | ✓ | |
| `audit:read` | | | | | ✓ | |
| `identity:manage` | | | | | ✓ | |
| `realm:manage` | | | | | ✓ | |

Deny by default: any permission not explicitly granted is denied.

## Realm Isolation

Every realm-scoped resource requires the authenticated principal to
have an explicit assignment to that realm.

- Realm IDs are validated against the principal's `realm_ids` claim
- Cross-realm reads, updates, approvals, and write-backs are rejected
  with HTTP 403
- Realm validation is enforced at the service layer, not just the
  router layer
- Realm IDs in URLs or request bodies are never trusted without
  verification

### Implementation

- `require_realm_access(principal, realm_id)` — raises 403 if denied
- `require_any_realm_access(principal)` — raises 403 if no realms
- Applied in every endpoint that takes a `realm_id` parameter

## Audit Events

Every sensitive action records an audit event with:

- Actor identity (principal ID, type, email, roles)
- Request correlation ID
- Action performed
- Resource type and ID
- Realm context
- Success/failure status
- Sanitized metadata (secrets redacted)

Audit events are append-only. No API exists to modify or delete
audit history.

### Sensitive Key Redaction

The following keys are automatically redacted in audit metadata:
`password`, `secret`, `token`, `authorization`, `access_token`,
`refresh_token`, `api_key`, `client_secret`, `db_password`.

## Request Correlation

Every request gets a correlation ID via `CorrelationIDMiddleware`:

- Accepts incoming `X-Correlation-ID` header (max 128 chars)
- Generates UUID if missing or invalid
- Attaches to structlog context, response headers, and audit events
- Rejects header injection (alphanumeric + hyphens + underscores only)

## Protected Route Matrix

| Route | Method | Permission | Realm Scoped |
|-------|--------|------------|--------------|
| `/api/v1/health/live` | GET | **PUBLIC** | No |
| `/api/v1/health/ready` | GET | **PUBLIC** | No |
| `/api/v1/categorization/runs` | POST | `accounting:review` | Yes |
| `/api/v1/categorization/runs/{id}` | GET | `accounting:read` | No |
| `/api/v1/categorization/categorizations` | GET | `accounting:read` | Yes |
| `/api/v1/categorization/categorizations/{id}` | GET | `accounting:read` | No |
| `/api/v1/categorization/categorizations/{id}/approve` | POST | `accounting:approve` | Yes |
| `/api/v1/categorization/categorizations/{id}/reject` | POST | `accounting:review` | No |
| `/api/v1/categorization/categorizations/{id}/defer` | POST | `accounting:review` | No |
| `/api/v1/categorization/rules` | POST | `accounting:review` | Yes |
| `/api/v1/categorization/rules` | GET | `accounting:read` | Yes |
| `/api/v1/categorization/review-queue` | GET | `accounting:read` | Yes |
| `/api/v1/categorization/supported-writeback-types` | GET | `accounting:read` | No |
| `/api/v1/ml/datasets` | GET | `ml:read` | Yes |
| `/api/v1/ml/datasets/{id}` | GET | `ml:read` | No |
| `/api/v1/ml/datasets/{id}/quality-report` | GET | `ml:read` | No |
| `/api/v1/ml/training-runs` | GET | `ml:read` | Yes |
| `/api/v1/ml/training-runs/{id}` | GET | `ml:read` | No |
| `/api/v1/ml/models` | GET | `ml:read` | Yes |
| `/api/v1/ml/models/{id}` | GET | `ml:read` | No |
| `/api/v1/ml/models/{id}/validate` | POST | `ml:train` | No |
| `/api/v1/ml/models/{id}/activate-shadow` | POST | `ml:shadow:activate` | No |
| `/api/v1/ml/models/{id}/retire` | POST | `ml:train` | No |
| `/api/v1/ml/models/{id}/metrics` | GET | `ml:read` | No |
| `/api/v1/ml/predictions` | GET | `ml:read` | Yes |
| `/api/v1/ml/shadow-evaluations` | GET | `ml:read` | Yes |
| `/api/v1/ml/monitoring/summary` | GET | `ml:read` | Yes |
| `/api/v1/ml/drift-reports` | POST | `ml:train` | No |
| `/api/v1/ml/config` | GET | `ml:read` | No |
| `/api/v1/integrations/quickbooks/authorize` | GET | `quickbooks:read` | No |
| `/api/v1/integrations/quickbooks/callback` | GET | `quickbooks:read` | No |
| `/api/v1/integrations/quickbooks/health` | GET | `quickbooks:read` | No |
| `/api/v1/integrations/quickbooks/sync/backfill` | POST | `quickbooks:read` | Yes |
| `/api/v1/integrations/quickbooks/sync/incremental` | POST | `quickbooks:read` | Yes |
| `/api/v1/integrations/quickbooks/sync/status` | GET | `quickbooks:read` | Yes |
| `/api/v1/integrations/quickbooks/accounting/*` | * | `quickbooks:read` | Yes |

## Error Responses

| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid authentication token |
| 403 | Authenticated but unauthorized (permission or realm) |
| 404 | Resource not found (or realm mismatch — no disclosure) |
| 409 | Safe conflict handling |
| 422 | Request validation error |
| 429 | Rate limit (deferred) |

Error responses never expose:
- Stack traces
- Database internals
- Token parsing internals
- Secret names or values
- QuickBooks tokens
- Filesystem paths

## ML Security Boundary (Stage 8 Preserved)

- ML remains **shadow-only**: predictions are recorded but never
  change Stage 7 output
- ML **cannot write to QuickBooks**: zero writeback imports in ML module
- ML **cannot approve accounting actions**: no approval endpoints in ML router
- ML **cannot promote to CHAMPION**: removed from valid transitions
- ML **cannot promote to PRIMARY**: rejected as inference mode
- Only `ml:shadow:activate` holders can activate SHADOW models
- Model and dataset access must be realm-scoped
- One SHADOW model per realm enforced by PostgreSQL partial unique index
- Artifact path traversal prevented by `ArtifactManager._validate_path()`

## Write-Back Security Boundary (Stage 7 Preserved)

- Write-back requires `accounting:writeback` permission
- Write-back requires authenticated principal
- Write-back requires authorized realm
- Write-back requires valid approval state
- Write-back requires active target GL account
- Write-back requires correct QuickBooks realm/company
- ML_OPERATOR alone cannot grant write-back
- ADMIN cannot bypass accounting approval requirements

## Production Deployment Requirements

1. Set `JWT_SECRET_KEY` to a cryptographically random string (≥32 chars)
2. Set `APP_ENV=production`
3. Ensure `AUTH_BYPASS_ENABLED=false`
4. Configure a trusted identity provider for token issuance
5. Set up encrypted storage for QuickBooks tokens
6. Configure database connection with production credentials
7. Enable structured JSON logging (`LOG_LEVEL=INFO`)
8. Set up monitoring for audit events

## Known Limitations

- **Authentication**: JWT-only; no OAuth2/OIDC integration yet
- **Rate limiting**: Deferred to a future stage
- **Refresh tokens**: Not implemented; tokens expire after 30 minutes
- **Password hashing**: Infrastructure exists (passlib/bcrypt) but
  no local user registration flow
- **Token revocation**: Not implemented; relies on short token lifetime
- **Encryption at rest**: External service tokens stored in environment
  variables, not encrypted in database
- **Multi-factor authentication**: Not implemented
- **Production ML performance**: Not established; shadow mode only
