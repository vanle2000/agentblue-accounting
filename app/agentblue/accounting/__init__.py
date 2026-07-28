"""Accounting workflow state machine and enums.

Defines the complete workflow lifecycle for accounting work items,
write-back jobs, reconciliation, and escalation.
"""

from __future__ import annotations

from enum import Enum


class WorkItemStatus(str, Enum):
    """Workflow states for an accounting work item."""

    INGESTED = "INGESTED"
    VALIDATED = "VALIDATED"
    RECOMMENDED = "RECOMMENDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    CORRECTED = "CORRECTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    READY_FOR_WRITEBACK = "READY_FOR_WRITEBACK"
    WRITEBACK_IN_PROGRESS = "WRITEBACK_IN_PROGRESS"
    WRITTEN = "WRITTEN"
    WRITEBACK_FAILED = "WRITEBACK_FAILED"
    RECONCILING = "RECONCILING"
    RECONCILED = "RECONCILED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class WriteBackJobStatus(str, Enum):
    """States for a write-back job."""

    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    CANCELLED = "CANCELLED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    RECONCILED = "RECONCILED"


class ReconciliationStatus(str, Enum):
    """States for post-write reconciliation."""

    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    TARGET_MISSING = "TARGET_MISSING"
    AUTH_FAILED = "AUTH_FAILED"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class DuplicateClassification(str, Enum):
    """Duplicate detection results."""

    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    LIKELY_DUPLICATE = "LIKELY_DUPLICATE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    NOT_DUPLICATE = "NOT_DUPLICATE"


class FailureCategory(str, Enum):
    """Classification of write-back and reconciliation failures."""

    AUTHENTICATION_EXPIRED = "AUTHENTICATION_EXPIRED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    STALE_SYNCTOKEN = "STALE_SYNCTOKEN"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    PERIOD_CLOSED = "PERIOD_CLOSED"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    QUICKBOOKS_REJECTED = "QUICKBOOKS_REJECTED"
    UNKNOWN_EXTERNAL_FAILURE = "UNKNOWN_EXTERNAL_FAILURE"


class EscalationCategory(str, Enum):
    """Categories for exception escalation."""

    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MISSING_ACCOUNT = "MISSING_ACCOUNT"
    INACTIVE_ACCOUNT = "INACTIVE_ACCOUNT"
    AMBIGUOUS_VENDOR = "AMBIGUOUS_VENDOR"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    CLOSED_PERIOD = "CLOSED_PERIOD"
    REALM_MISMATCH = "REALM_MISMATCH"
    SYNCTOKEN_CONFLICT = "SYNCTOKEN_CONFLICT"
    OAUTH_FAILURE = "OAUTH_FAILURE"
    REPEATED_EXTERNAL_FAILURE = "REPEATED_EXTERNAL_FAILURE"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    UNUSUAL_AMOUNT = "UNUSUAL_AMOUNT"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    APPROVAL_CONFLICT = "APPROVAL_CONFLICT"


class RiskLevel(str, Enum):
    """Risk classification for work items."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# --- Valid state transitions ---

WORK_ITEM_TRANSITIONS: dict[WorkItemStatus, set[WorkItemStatus]] = {
    WorkItemStatus.INGESTED: {WorkItemStatus.VALIDATED, WorkItemStatus.ESCALATED},
    WorkItemStatus.VALIDATED: {WorkItemStatus.RECOMMENDED, WorkItemStatus.ESCALATED},
    WorkItemStatus.RECOMMENDED: {WorkItemStatus.NEEDS_REVIEW, WorkItemStatus.APPROVED},
    WorkItemStatus.NEEDS_REVIEW: {
        WorkItemStatus.IN_REVIEW,
        WorkItemStatus.APPROVED,
        WorkItemStatus.REJECTED,
        WorkItemStatus.DEFERRED,
        WorkItemStatus.ESCALATED,
    },
    WorkItemStatus.IN_REVIEW: {
        WorkItemStatus.CORRECTED,
        WorkItemStatus.APPROVED,
        WorkItemStatus.REJECTED,
        WorkItemStatus.DEFERRED,
        WorkItemStatus.ESCALATED,
    },
    WorkItemStatus.CORRECTED: {
        WorkItemStatus.APPROVED,
        WorkItemStatus.REJECTED,
        WorkItemStatus.DEFERRED,
        WorkItemStatus.ESCALATED,
    },
    WorkItemStatus.APPROVED: {
        WorkItemStatus.READY_FOR_WRITEBACK,
        WorkItemStatus.ESCALATED,
        WorkItemStatus.CLOSED,
    },
    WorkItemStatus.REJECTED: {WorkItemStatus.CLOSED},
    WorkItemStatus.DEFERRED: {
        WorkItemStatus.NEEDS_REVIEW,
        WorkItemStatus.CLOSED,
    },
    WorkItemStatus.READY_FOR_WRITEBACK: {
        WorkItemStatus.WRITEBACK_IN_PROGRESS,
        WorkItemStatus.CLOSED,
    },
    WorkItemStatus.WRITEBACK_IN_PROGRESS: {
        WorkItemStatus.WRITTEN,
        WorkItemStatus.WRITEBACK_FAILED,
    },
    WorkItemStatus.WRITTEN: {
        WorkItemStatus.RECONCILING,
        WorkItemStatus.RECONCILIATION_FAILED,
    },
    WorkItemStatus.WRITEBACK_FAILED: {
        WorkItemStatus.READY_FOR_WRITEBACK,
        WorkItemStatus.ESCALATED,
        WorkItemStatus.CLOSED,
    },
    WorkItemStatus.RECONCILING: {
        WorkItemStatus.RECONCILED,
        WorkItemStatus.RECONCILIATION_FAILED,
    },
    WorkItemStatus.RECONCILED: {WorkItemStatus.CLOSED},
    WorkItemStatus.RECONCILIATION_FAILED: {
        WorkItemStatus.ESCALATED,
        WorkItemStatus.CLOSED,
    },
    WorkItemStatus.ESCALATED: {
        WorkItemStatus.NEEDS_REVIEW,
        WorkItemStatus.CLOSED,
    },
    WorkItemStatus.CLOSED: set(),  # Terminal state
}

WRITEBACK_JOB_TRANSITIONS: dict[WriteBackJobStatus, set[WriteBackJobStatus]] = {
    WriteBackJobStatus.PENDING: {WriteBackJobStatus.VALIDATING, WriteBackJobStatus.CANCELLED},
    WriteBackJobStatus.VALIDATING: {
        WriteBackJobStatus.READY,
        WriteBackJobStatus.FAILED_PERMANENT,
        WriteBackJobStatus.REQUIRES_REVIEW,
    },
    WriteBackJobStatus.READY: {
        WriteBackJobStatus.IN_PROGRESS,
        WriteBackJobStatus.CANCELLED,
    },
    WriteBackJobStatus.IN_PROGRESS: {
        WriteBackJobStatus.SUCCEEDED,
        WriteBackJobStatus.FAILED_RETRYABLE,
        WriteBackJobStatus.FAILED_PERMANENT,
    },
    WriteBackJobStatus.SUCCEEDED: {WriteBackJobStatus.RECONCILED},
    WriteBackJobStatus.FAILED_RETRYABLE: {
        WriteBackJobStatus.IN_PROGRESS,
        WriteBackJobStatus.FAILED_PERMANENT,
        WriteBackJobStatus.REQUIRES_REVIEW,
    },
    WriteBackJobStatus.FAILED_PERMANENT: set(),  # Terminal
    WriteBackJobStatus.CANCELLED: set(),  # Terminal
    WriteBackJobStatus.REQUIRES_REVIEW: {
        WriteBackJobStatus.IN_PROGRESS,
        WriteBackJobStatus.CANCELLED,
    },
    WriteBackJobStatus.RECONCILED: set(),  # Terminal
}


def validate_work_item_transition(
    current: WorkItemStatus,
    target: WorkItemStatus,
) -> bool:
    """Check if a work-item state transition is valid.

    Args:
        current: Current status.
        target: Desired status.

    Returns:
        True if the transition is allowed.

    Raises:
        ValueError: If the transition is not allowed.
    """
    allowed = WORK_ITEM_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Invalid transition: {current.value} -> {target.value}. "
            f"Allowed: {sorted(s.value for s in allowed)}"
        )
    return True


def validate_writeback_job_transition(
    current: WriteBackJobStatus,
    target: WriteBackJobStatus,
) -> bool:
    """Check if a write-back job state transition is valid.

    Args:
        current: Current status.
        target: Desired status.

    Returns:
        True if the transition is allowed.

    Raises:
        ValueError: If the transition is not allowed.
    """
    allowed = WRITEBACK_JOB_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Invalid write-back job transition: {current.value} -> {target.value}. "
            f"Allowed: {sorted(s.value for s in allowed)}"
        )
    return True
