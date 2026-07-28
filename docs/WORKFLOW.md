# Stage 10 — Production Accounting Workflow

## Overview

Stage 10 implements the production accounting workflow that allows Agent Blue to:
ingest transactions, generate recommendations, present them for human review,
support accountant correction and approval, execute controlled QuickBooks write-back,
reconcile results, and preserve a complete audit trail.

**Stage 10 does NOT make Agent Blue an autonomous accountant.**

## Work-Item State Machine

```
INGESTED → VALIDATED → RECOMMENDED → NEEDS_REVIEW
                                          ↓
                              ┌→ IN_REVIEW → CORRECTED → APPROVED
                              │                          ↓
                              ├→ REJECTED → CLOSED    READY_FOR_WRITEBACK
                              │                          ↓
                              ├→ DEFERRED ←──────── WRITEBACK_IN_PROGRESS
                              │                    ↓           ↓
                              └→ ESCALATED    WRITTEN    WRITEBACK_FAILED
                                                  ↓           ↓
                                            RECONCILING   READY_FOR_WRITEBACK (retry)
                                                  ↓           ↓
                                            RECONCILED   RECONCILIATION_FAILED
                                                  ↓           ↓
                                              CLOSED      ESCALATED → CLOSED
```

### Valid Transitions

| From | To | Required Permission |
|------|----|--------------------|
| INGESTED | VALIDATED | (system) |
| VALIDATED | RECOMMENDED | (system) |
| RECOMMENDED | NEEDS_REVIEW | (system) |
| NEEDS_REVIEW | IN_REVIEW | ACCOUNTING_REVIEW |
| NEEDS_REVIEW | APPROVED | ACCOUNTING_APPROVE |
| NEEDS_REVIEW | REJECTED | ACCOUNTING_REVIEW |
| NEEDS_REVIEW | DEFERRED | ACCOUNTING_REVIEW |
| IN_REVIEW | CORRECTED | ACCOUNTING_REVIEW |
| IN_REVIEW | APPROVED | ACCOUNTING_APPROVE |
| CORRECTED | APPROVED | ACCOUNTING_APPROVE |
| APPROVED | READY_FOR_WRITEBACK | ACCOUNTING_WRITEBACK |
| READY_FOR_WRITEBACK | WRITEBACK_IN_PROGRESS | ACCOUNTING_WRITEBACK |
| WRITEBACK_IN_PROGRESS | WRITTEN | ACCOUNTING_WRITEBACK |
| WRITEBACK_IN_PROGRESS | WRITEBACK_FAILED | ACCOUNTING_WRITEBACK |
| WRITEBACK_FAILED | READY_FOR_WRITEBACK | ACCOUNTING_WRITEBACK |
| WRITTEN | RECONCILING | (system) |
| RECONCILING | RECONCILED | (system) |
| DEFERRED | NEEDS_REVIEW | ACCOUNTING_REVIEW |
| ESCALATED | NEEDS_REVIEW | ACCOUNTING_REVIEW |

## Review Workflow

1. Work items appear in the review queue with status NEEDS_REVIEW
2. Accountants can claim items (assigns reviewer)
3. Accountants can correct recommendations (preserves original)
4. Corrections require a reason and create correction records
5. Corrections do NOT count as approval
6. Separate approval action required by APPROVER role

## Separation of Duties

- ACCOUNTANT reviews and corrects
- APPROVER approves (distinct from reviewer for high-risk items)
- SERVICE_ACCOUNT executes approved jobs but cannot approve
- ADMIN cannot bypass accounting approval
- ML_OPERATOR cannot review, approve, or write back

## Write-Back Job Lifecycle

```
PENDING → VALIDATING → READY → IN_PROGRESS → SUCCEEDED → RECONCILED
                                    ↓
                              FAILED_RETRYABLE → IN_PROGRESS (retry)
                                    ↓
                              FAILED_PERMANENT (terminal)
```

### Pre-Write Validation

Before every QuickBooks write:
- Authenticated execution context
- ACCOUNTING_WRITEBACK permission
- Authorized realm
- Valid approval
- Approved payload unchanged
- Work item version matches approval version
- Target QuickBooks company matches realm
- Expected SyncToken is current
- Target GL account is active
- Idempotency key is valid
- No conflicting write-back job exists

### Failure Classification

| Category | Retryable | Description |
|----------|-----------|-------------|
| AUTHENTICATION_EXPIRED | No | OAuth token expired |
| AUTHORIZATION_DENIED | No | Permission denied |
| RATE_LIMITED | Yes | 429 from QuickBooks |
| NETWORK_TIMEOUT | Yes | Request timed out |
| NETWORK_UNAVAILABLE | Yes | Network error |
| VALIDATION_FAILED | No | Schema validation error |
| STALE_SYNCTOKEN | No | Transaction changed since review |
| TARGET_NOT_FOUND | No | Transaction no longer exists |
| ACCOUNT_INACTIVE | No | Target GL account inactive |
| PERIOD_CLOSED | No | Accounting period closed |
| DUPLICATE_REQUEST | No | Already processed |
| QUICKBOOKS_REJECTED | No | QuickBooks rejected the update |
| UNKNOWN_EXTERNAL_FAILURE | Yes | Unknown QuickBooks error |

## Idempotency

- Stable idempotency key on every write-back job
- Database uniqueness constraint prevents duplicates
- Payload fingerprint prevents stale replay
- Duplicate submission returns original outcome
- PostgreSQL-level constraint prevents concurrent duplicates

## Reconciliation

After every successful write-back:
1. Retrieve QuickBooks transaction independently
2. Compare approved state to observed state
3. Record field-level differences
4. Mark RECONCILED only on exact match

### Reconciliation States

| State | Meaning |
|-------|---------|
| MATCHED | Approved state matches observed state |
| MISMATCH | Field-level differences detected |
| SOURCE_CHANGED | Source transaction changed since approval |
| TARGET_MISSING | Target transaction not found |
| MANUAL_REVIEW_REQUIRED | Human must verify |

## Duplicate Detection

| Classification | Action |
|----------------|--------|
| EXACT_DUPLICATE | Blocks write-back |
| LIKELY_DUPLICATE | Requires human resolution |
| POSSIBLE_DUPLICATE | Warning in review queue |
| NOT_DUPLICATE | Proceeds normally |

Signals: external transaction ID, idempotency key, date+amount+vendor.

## Exception Escalation

Categories: LOW_CONFIDENCE, MISSING_ACCOUNT, INACTIVE_ACCOUNT,
AMBIGUOUS_VENDOR, DUPLICATE_SUSPECTED, CLOSED_PERIOD,
SYNCTOKEN_CONFLICT, OAUTH_FAILURE, RECONCILIATION_MISMATCH,
UNUSUAL_AMOUNT, POLICY_VIOLATION

Each escalation includes: work item, severity, explanation,
supporting evidence, attempted actions, failure history,
recommended next step, assigned owner, due date.

## Route Matrix

| Route | Method | Permission | Realm |
|-------|--------|------------|-------|
| /api/v1/accounting/work-items | GET | ACCOUNTING_READ | Yes |
| /api/v1/accounting/work-items/{id} | GET | ACCOUNTING_READ | No |
| /api/v1/accounting/work-items/{id}/claim | POST | ACCOUNTING_REVIEW | No |
| /api/v1/accounting/work-items/{id}/release | POST | ACCOUNTING_REVIEW | No |
| /api/v1/accounting/work-items/{id}/correct | POST | ACCOUNTING_REVIEW | Yes |
| /api/v1/accounting/work-items/{id}/approve | POST | ACCOUNTING_APPROVE | Yes |
| /api/v1/accounting/work-items/{id}/reject | POST | ACCOUNTING_REVIEW | No |
| /api/v1/accounting/work-items/{id}/defer | POST | ACCOUNTING_REVIEW | No |
| /api/v1/accounting/work-items/{id}/escalate | POST | ACCOUNTING_REVIEW | No |
| /api/v1/accounting/batch/approve | POST | ACCOUNTING_APPROVE | Yes |
| /api/v1/accounting/escalations | GET | ACCOUNTING_READ | Yes |
| /api/v1/accounting/escalations/{id}/resolve | POST | ACCOUNTING_APPROVE | No |
| /api/v1/accounting/writeback-jobs | GET | ACCOUNTING_READ | Yes |
| /api/v1/accounting/writeback-jobs/{id}/execute | POST | ACCOUNTING_WRITEBACK | Yes |
| /api/v1/accounting/reconciliation/{id} | GET | ACCOUNTING_READ | No |

## Database Tables

| Table | Purpose |
|-------|---------|
| accounting_work_item | Durable work item with full lifecycle |
| work_item_transition | Immutable state transition audit |
| work_item_correction | Accountant corrections |
| write_back_job | Durable write-back with retry tracking |
| write_back_attempt | Immutable attempt history |
| reconciliation_result | Post-write verification |
| escalation | Exception queue |
| batch_operation | Batch operation tracking |
| batch_operation_item | Individual batch items |

## Known Limitations

- Worker/cron for automatic retry not implemented (API-triggered)
- Multi-realm reconciliation dashboard deferred
- ML retraining pipeline deferred
- Production metrics endpoints deferred
- Rate limiting remains in-memory (not distributed)

## Production Blockers

- No automated retry worker (manual trigger only)
- No production QuickBooks OAuth flow tested
- No real training data for ML recommendations

## Safety Confirmations

- ML remains SHADOW-only: CONFIRMED
- Human approval remains mandatory: CONFIRMED
- No autonomous QuickBooks write: CONFIRMED
- Stage 7 operational categorization preserved: CONFIRMED
- Stage 8 ML shadow mode preserved: CONFIRMED
- Stage 9 security controls preserved: CONFIRMED
