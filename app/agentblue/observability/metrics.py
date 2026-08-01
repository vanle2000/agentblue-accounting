"""Prometheus-compatible metrics for Agent Blue.

Provides counters, histograms, and gauges for HTTP, workflow,
write-back, reconciliation, security, and system metrics.

All metric labels use bounded cardinality — no transaction IDs,
user emails, or free-form error messages.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ---------------------------------------------------------------------------
# Application info
# ---------------------------------------------------------------------------
APP_INFO = Info("agentblue", "Agent Blue Accounting application info")

# ---------------------------------------------------------------------------
# HTTP metrics
# ---------------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "agentblue_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "agentblue_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "agentblue_http_requests_in_progress",
    "HTTP requests currently in progress",
    ["method", "endpoint"],
)

# ---------------------------------------------------------------------------
# Workflow metrics
# ---------------------------------------------------------------------------
WORK_ITEMS_CREATED = Counter(
    "agentblue_work_items_created_total",
    "Total work items created",
    ["realm_id"],
)

WORK_ITEMS_BY_STATE = Gauge(
    "agentblue_work_items_by_state",
    "Work items by current state",
    ["realm_id", "status"],
)

TRANSITIONS = Counter(
    "agentblue_workflow_transitions_total",
    "Total workflow state transitions",
    ["from_status", "to_status", "result"],
)

TRANSITION_DURATION = Histogram(
    "agentblue_workflow_transition_duration_seconds",
    "Workflow transition duration",
    ["from_status", "to_status"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

QUEUE_AGE = Histogram(
    "agentblue_review_queue_age_seconds",
    "Time work items spend in review queue",
    ["realm_id"],
    buckets=(60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400),
)

CLAIM_DURATION = Histogram(
    "agentblue_claim_duration_seconds",
    "Time from NEEDS_REVIEW to claim",
    buckets=(10, 30, 60, 300, 900, 1800, 3600),
)

# ---------------------------------------------------------------------------
# Approval metrics
# ---------------------------------------------------------------------------
APPROVALS = Counter(
    "agentblue_approvals_total",
    "Total approvals",
    ["realm_id", "result"],
)

APPROVAL_LATENCY = Histogram(
    "agentblue_approval_latency_seconds",
    "Time from review to approval",
    buckets=(60, 300, 900, 1800, 3600, 7200, 14400),
)

SEPARATION_VIOLATIONS = Counter(
    "agentblue_separation_violations_total",
    "Separation-of-duties violations blocked",
    ["realm_id", "violation_type"],
)

# ---------------------------------------------------------------------------
# Write-back metrics
# ---------------------------------------------------------------------------
WRITEBACK_JOBS = Counter(
    "agentblue_writeback_jobs_total",
    "Total write-back jobs",
    ["realm_id", "status"],
)

WRITEBACK_ATTEMPTS = Counter(
    "agentblue_writeback_attempts_total",
    "Total write-back attempts",
    ["realm_id", "result", "failure_category"],
)

WRITEBACK_DURATION = Histogram(
    "agentblue_writeback_duration_seconds",
    "Write-back execution duration",
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

IDEMPOTENCY_CONFLICTS = Counter(
    "agentblue_idempotency_conflicts_total",
    "Idempotency conflicts prevented",
    ["realm_id"],
)

DEAD_LETTER_COUNT = Gauge(
    "agentblue_dead_letter_count",
    "Jobs in dead-letter state",
    ["realm_id"],
)

# ---------------------------------------------------------------------------
# Reconciliation metrics
# ---------------------------------------------------------------------------
RECONCILIATIONS = Counter(
    "agentblue_reconciliations_total",
    "Total reconciliation results",
    ["realm_id", "status"],
)

RECONCILIATION_LATENCY = Histogram(
    "agentblue_reconciliation_latency_seconds",
    "Reconciliation duration",
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0),
)

UNRESOLVED_MISMATCH_AGE = Gauge(
    "agentblue_unresolved_mismatch_age_seconds",
    "Age of oldest unresolved reconciliation mismatch",
    ["realm_id"],
)

# ---------------------------------------------------------------------------
# Security metrics
# ---------------------------------------------------------------------------
AUTH_FAILURES = Counter(
    "agentblue_auth_failures_total",
    "Authentication failures",
    ["failure_type"],
)

AUTHZ_FAILURES = Counter(
    "agentblue_authz_failures_total",
    "Authorization failures",
    ["failure_type"],
)

CROSS_REALM_ATTEMPTS = Counter(
    "agentblue_cross_realm_attempts_total",
    "Cross-realm access attempts blocked",
    ["endpoint"],
)

REVOKED_TOKEN_ATTEMPTS = Counter(
    "agentblue_revoked_token_attempts_total",
    "Revoked token usage attempts",
)

RATE_LIMIT_EVENTS = Counter(
    "agentblue_rate_limit_events_total",
    "Rate limit events",
    ["endpoint"],
)

# ---------------------------------------------------------------------------
# ML shadow metrics
# ---------------------------------------------------------------------------
ML_RECOMMENDATIONS = Counter(
    "agentblue_ml_recommendations_total",
    "ML shadow recommendations generated",
    ["realm_id"],
)

ML_SHADOW_LATENCY = Histogram(
    "agentblue_ml_shadow_latency_seconds",
    "ML shadow prediction latency",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

ML_DISAGREEMENTS = Counter(
    "agentblue_ml_disagreements_total",
    "ML shadow disagreements with human correction",
    ["realm_id"],
)

# ---------------------------------------------------------------------------
# System metrics
# ---------------------------------------------------------------------------
DB_POOL_SIZE = Gauge(
    "agentblue_db_pool_size",
    "Database connection pool size",
)

DB_POOL_CHECKED_OUT = Gauge(
    "agentblue_db_pool_checked_out",
    "Database connections currently checked out",
)

WORKER_HEARTBEAT = Gauge(
    "agentblue_worker_last_heartbeat_timestamp",
    "Timestamp of last worker heartbeat",
    ["worker_id"],
)

PROCESS_UPTIME = Gauge(
    "agentblue_process_uptime_seconds",
    "Process uptime in seconds",
)

STARTUP_DURATION = Histogram(
    "agentblue_startup_duration_seconds",
    "Application startup duration",
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
