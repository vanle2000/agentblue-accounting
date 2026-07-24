## Summary

Implements Stage 7 of Agent Blue Accounting: Level 2 assisted transaction categorization for synchronized QuickBooks transactions.

Agent Blue can now:

- analyze eligible QuickBooks transactions;
- generate ranked account-category recommendations;
- pre-select recommendations meeting the configured 0.97 assisted-automation threshold;
- route uncertain cases to accountant review;
- support explicit one-click approval;
- update supported QuickBooks transactions only after human approval;
- verify the resulting QuickBooks state;
- retain an immutable categorization and application audit trail;
- capture approved labels for future machine-learning work.

The confidence score is a deterministic ranking score, not a calibrated probability.

## Automation Boundary

Stage 7 implements Level 2 Assisted Automation.

The workflow is:

1. AI recommends and may pre-select an account.
2. An accountant explicitly approves or changes the account.
3. Only after approval does Agent Blue update QuickBooks.
4. The update is verified before the categorization is marked applied.

There is no approval-free or confidence-only QuickBooks write path.

## Major Components

- transaction eligibility policies;
- vendor and text normalization;
- versioned feature extraction;
- deterministic rule engine;
- approved-history matching;
- Stage 6 account candidate integration;
- deterministic scoring and confidence bands;
- 0.97 assisted pre-selection gate;
- review queue and review decisions;
- one-click approve-and-apply workflow;
- QuickBooks write-back service;
- SyncToken and stale-state protection;
- line-specific Purchase update handling;
- post-write verification;
- idempotency and retry handling;
- immutable decision and application audit records;
- training-label capture;
- FastAPI endpoints;
- database migration and persistence models.

## QuickBooks Write-Back Scope

Supported:

- Purchase transactions with safe, line-specific account updates

Not supported for write-back in Stage 7:

- Bill
- JournalEntry
- Transfer
- Payment
- Invoice
- SalesReceipt
- Deposit
- BillPayment
- CreditMemo
- RefundReceipt
- VendorCredit
- unsupported or ambiguous line structures

Unsupported entities may still receive recommendations, but they cannot be applied to QuickBooks.

## Safety Controls

- explicit accountant approval required;
- same-realm validation;
- active and non-deleted account validation;
- expected categorization version;
- current QuickBooks SyncToken verification;
- transaction and line fingerprint checks;
- exact target-line validation;
- stale transaction rejection;
- idempotency key enforcement;
- duplicate-click protection;
- post-write account verification;
- sanitized request and response audit snapshots;
- no raw OAuth credentials or authorization headers logged.

## Database Changes

Adds Stage 7 tables for:

- categorization rules;
- categorization runs;
- current transaction categorization;
- ranked recommendations;
- immutable review decisions;
- vendor mappings;
- training labels;
- QuickBooks categorization applications and application audit data.

Migration: `0003_categorization`

Uses PostgreSQL JSONB, Numeric confidence fields, realm-scoped constraints, application idempotency, and review-queue indexes.

## API Endpoints

Includes endpoints for:

- categorization runs;
- categorization details;
- review queue;
- approve-and-apply;
- reject;
- defer;
- categorization rules;
- supported write-back types.

## Verification

- Ruff lint: All checks passed
- Ruff formatting: 86 files formatted
- MyPy: No issues in 60 source files
- Unit tests: 261 passed in 20.80s
- PostgreSQL integration tests: 8 passed in 0.39s
- Stage 4-6 regression tests: All pass (included in unit suite)
- Full pytest: 269 passed (261 unit + 8 integration)
- Docker Compose: Exit 0

## Known Limitations

- Purchase is the only supported write-back entity.
- Bill write-back requires a Bill-specific endpoint and payload implementation.
- Confidence scores are not calibrated probabilities.
- No machine-learning model is trained in Stage 7.
- Reviewer metadata is not a substitute for authentication.
- Production use requires authenticated identity and authorization on approve-and-apply operations.
- No bulk autonomous approval or posting.
- Stage 8 will address model training, calibration, evaluation, drift monitoring, and controlled expansion.

## Stage 8 Boundary

Stage 8 is not included in this PR.

Potential future scope:

- supervised classification models;
- labeled-data evaluation;
- precision and recall;
- confidence calibration;
- shadow deployment;
- model registry;
- drift detection;
- champion/challenger evaluation;
- expanded entity-specific write-back support.
