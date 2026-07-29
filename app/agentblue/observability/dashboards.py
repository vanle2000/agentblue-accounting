"""Grafana-compatible dashboard and Prometheus alert definitions.

Dashboards and alerts for operational monitoring of Agent Blue
in production-shadow mode.
"""

from __future__ import annotations

from typing import Any


def get_dashboard_definitions() -> dict[str, Any]:
    """Return all dashboard definitions as a dictionary."""
    return {
        "executive_summary": _executive_summary(),
        "review_queue": _review_queue(),
        "approval_queue": _approval_queue(),
        "writeback_operations": _writeback_operations(),
        "reconciliation_health": _reconciliation_health(),
        "escalations": _escalations(),
        "security_events": _security_events(),
        "system_health": _system_health(),
        "ml_shadow": _ml_shadow(),
        "beta_readiness": _beta_readiness(),
    }


def get_alert_rules() -> dict[str, Any]:
    """Return all alert rule definitions."""
    return {
        "groups": [
            _critical_alerts(),
            _warning_alerts(),
        ]
    }


def _executive_summary() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Executive Summary",
        "panels": [
            {"title": "Work Items Today", "query": "sum(increase(agentblue_work_items_created_total[24h]))"},
            {"title": "Pending Reviews", "query": "sum(agentblue_work_items_by_state{status='NEEDS_REVIEW'})"},
            {"title": "Approval Backlog", "query": "sum(agentblue_work_items_by_state{status='APPROVED'})"},
            {"title": "Write-Back Success Rate", "query": "sum(agentblue_writeback_jobs_total{status='SUCCEEDED'}) / sum(agentblue_writeback_jobs_total)"},
            {"title": "Reconciliation Match Rate", "query": "sum(agentblue_reconciliations_total{status='MATCHED'}) / sum(agentblue_reconciliations_total)"},
            {"title": "Exception Rate", "query": "sum(increase(agentblue_workflow_transitions_total{result='failed'}[24h]))"},
            {"title": "API P95 Latency", "query": "histogram_quantile(0.95, sum(rate(agentblue_http_request_duration_seconds_bucket[5m])) by (le))"},
            {"title": "Active Escalations", "query": "sum(agentblue_work_items_by_state{status='ESCALATED'})"},
        ],
    }


def _review_queue() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Review Queue",
        "panels": [
            {"title": "Pending Reviews", "query": "sum(agentblue_work_items_by_state{status='NEEDS_REVIEW'}) by (realm_id)"},
            {"title": "In Review", "query": "sum(agentblue_work_items_by_state{status='IN_REVIEW'}) by (realm_id)"},
            {"title": "Oldest Pending Age", "query": "max(agentblue_review_queue_age_seconds_bucket)"},
            {"title": "Claim Latency P95", "query": "histogram_quantile(0.95, sum(rate(agentblue_claim_duration_seconds_bucket[5m])) by (le))"},
            {"title": "Corrections Today", "query": "sum(increase(agentblue_workflow_transitions_total{to_status='CORRECTED'}[24h]))"},
            {"title": "Rejections Today", "query": "sum(increase(agentblue_workflow_transitions_total{to_status='REJECTED'}[24h]))"},
        ],
    }


def _approval_queue() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Approval Queue",
        "panels": [
            {"title": "Pending Approvals", "query": "sum(agentblue_work_items_by_state{status='APPROVED'})"},
            {"title": "Approval Latency P50", "query": "histogram_quantile(0.50, sum(rate(agentblue_approval_latency_seconds_bucket[5m])) by (le))"},
            {"title": "Approval Latency P95", "query": "histogram_quantile(0.95, sum(rate(agentblue_approval_latency_seconds_bucket[5m])) by (le))"},
            {"title": "Approvals Today", "query": "sum(increase(agentblue_approvals_total{result='approved'}[24h]))"},
            {"title": "SoD Violations Blocked", "query": "sum(increase(agentblue_separation_violations_total[24h]))"},
        ],
    }


def _writeback_operations() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Write-Back Operations",
        "panels": [
            {"title": "Jobs by State", "query": "sum(agentblue_writeback_jobs_total) by (status)"},
            {"title": "Success Rate", "query": "sum(agentblue_writeback_attempts_total{result='success'}) / sum(agentblue_writeback_attempts_total)"},
            {"title": "Retry Backlog", "query": "sum(agentblue_writeback_jobs_total{status='FAILED_RETRYABLE'})"},
            {"title": "Dead-Letter Count", "query": "sum(agentblue_dead_letter_count)"},
            {"title": "Idempotency Conflicts", "query": "sum(increase(agentblue_idempotency_conflicts_total[24h]))"},
            {"title": "Uncertain Outcomes", "query": "sum(agentblue_writeback_attempts_total{failure_category='UNKNOWN_EXTERNAL_FAILURE'})"},
            {"title": "Execution P95", "query": "histogram_quantile(0.95, sum(rate(agentblue_writeback_duration_seconds_bucket[5m])) by (le))"},
        ],
    }


def _reconciliation_health() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Reconciliation Health",
        "panels": [
            {"title": "Matched", "query": "sum(agentblue_reconciliations_total{status='MATCHED'})"},
            {"title": "Mismatched", "query": "sum(agentblue_reconciliations_total{status='MISMATCH'})"},
            {"title": "Source Changed", "query": "sum(agentblue_reconciliations_total{status='SOURCE_CHANGED'})"},
            {"title": "Target Missing", "query": "sum(agentblue_reconciliations_total{status='TARGET_MISSING'})"},
            {"title": "Match Rate", "query": "sum(agentblue_reconciliations_total{status='MATCHED'}) / sum(agentblue_reconciliations_total)"},
            {"title": "Unresolved Mismatch Age", "query": "max(agentblue_unresolved_mismatch_age_seconds)"},
            {"title": "Reconciliation P95", "query": "histogram_quantile(0.95, sum(rate(agentblue_reconciliation_latency_seconds_bucket[5m])) by (le))"},
        ],
    }


def _escalations() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Escalations and Exceptions",
        "panels": [
            {"title": "Active Escalations", "query": "sum(agentblue_work_items_by_state{status='ESCALATED'})"},
            {"title": "Escalations Today", "query": "sum(increase(agentblue_workflow_transitions_total{to_status='ESCALATED'}[24h]))"},
            {"title": "Mean Time to Resolution", "query": "avg(agentblue_approval_latency_seconds_sum) / avg(agentblue_approval_latency_seconds_count)"},
        ],
    }


def _security_events() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Security Events",
        "panels": [
            {"title": "Auth Failures", "query": "sum(increase(agentblue_auth_failures_total[24h])) by (failure_type)"},
            {"title": "Authz Failures", "query": "sum(increase(agentblue_authz_failures_total[24h])) by (failure_type)"},
            {"title": "Cross-Realm Attempts", "query": "sum(increase(agentblue_cross_realm_attempts_total[24h]))"},
            {"title": "Revoked Token Attempts", "query": "sum(increase(agentblue_revoked_token_attempts_total[24h]))"},
            {"title": "Rate Limit Events", "query": "sum(increase(agentblue_rate_limit_events_total[24h]))"},
        ],
    }


def _system_health() -> dict[str, Any]:
    return {
        "title": "Agent Blue — System Health",
        "panels": [
            {"title": "API P50 Latency", "query": "histogram_quantile(0.50, sum(rate(agentblue_http_request_duration_seconds_bucket[5m])) by (le))"},
            {"title": "API P95 Latency", "query": "histogram_quantile(0.95, sum(rate(agentblue_http_request_duration_seconds_bucket[5m])) by (le))"},
            {"title": "API P99 Latency", "query": "histogram_quantile(0.99, sum(rate(agentblue_http_request_duration_seconds_bucket[5m])) by (le))"},
            {"title": "DB Pool Utilization", "query": "agentblue_db_pool_checked_out / agentblue_db_pool_size"},
            {"title": "Worker Heartbeat Age", "query": "time() - agentblue_worker_last_heartbeat_timestamp"},
            {"title": "Process Uptime", "query": "agentblue_process_uptime_seconds"},
            {"title": "Error Rate", "query": "sum(rate(agentblue_http_requests_total{status=~'5..'}[5m])) / sum(rate(agentblue_http_requests_total[5m]))"},
        ],
    }


def _ml_shadow() -> dict[str, Any]:
    return {
        "title": "Agent Blue — ML Shadow Performance",
        "panels": [
            {"title": "Recommendations Today", "query": "sum(increase(agentblue_ml_recommendations_total[24h]))"},
            {"title": "Shadow Latency P95", "query": "histogram_quantile(0.95, sum(rate(agentblue_ml_shadow_latency_seconds_bucket[5m])) by (le))"},
            {"title": "Human Disagreements", "query": "sum(increase(agentblue_ml_disagreements_total[24h]))"},
            {"title": "Correction Rate", "query": "sum(increase(agentblue_ml_disagreements_total[24h])) / sum(increase(agentblue_ml_recommendations_total[24h]))"},
        ],
    }


def _beta_readiness() -> dict[str, Any]:
    return {
        "title": "Agent Blue — Beta Readiness",
        "panels": [
            {"title": "System Uptime", "query": "agentblue_process_uptime_seconds"},
            {"title": "Error Rate < 1%", "query": "sum(rate(agentblue_http_requests_total{status=~'5..'}[5m])) / sum(rate(agentblue_http_requests_total[5m])) < 0.01"},
            {"title": "Zero Cross-Realm", "query": "sum(increase(agentblue_cross_realm_attempts_total[24h])) == 0"},
            {"title": "Zero Duplicate Writes", "query": "sum(increase(agentblue_idempotency_conflicts_total[24h])) >= 0"},
            {"title": "Backup Age < 24h", "query": "time() - agentblue_backup_last_success_timestamp < 86400"},
        ],
    }


def _critical_alerts() -> dict[str, Any]:
    return {
        "name": "agentblue-critical",
        "rules": [
            {
                "alert": "AgentBlueAPIUnavailable",
                "expr": "up{job='agentblue-api'} == 0",
                "for": "1m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "Agent Blue API is unavailable", "runbook": "docs/INCIDENT_RESPONSE.md"},
            },
            {
                "alert": "AgentBlueDatabaseUnavailable",
                "expr": "agentblue_db_pool_checked_out == 0",
                "for": "2m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "Database connection pool empty", "runbook": "docs/INCIDENT_RESPONSE.md"},
            },
            {
                "alert": "AgentBlueWorkerHeartbeatMissing",
                "expr": "time() - agentblue_worker_last_heartbeat_timestamp > 120",
                "for": "2m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "Worker heartbeat missing for >2 minutes", "runbook": "docs/WORKER_OPERATIONS.md"},
            },
            {
                "alert": "AgentBlueWritebackStuck",
                "expr": "sum(agentblue_writeback_jobs_total{status='IN_PROGRESS'}) > 0",
                "for": "10m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "Write-back job stuck in IN_PROGRESS", "runbook": "docs/RETRY_DEAD_LETTER.md"},
            },
            {
                "alert": "AgentBlueDeadLetterNonEmpty",
                "expr": "sum(agentblue_dead_letter_count) > 0",
                "for": "5m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "Dead-letter queue has jobs", "runbook": "docs/RETRY_DEAD_LETTER.md"},
            },
            {
                "alert": "AgentBlueHighErrorRate",
                "expr": "sum(rate(agentblue_http_requests_total{status=~'5..'}[5m])) / sum(rate(agentblue_http_requests_total[5m])) > 0.05",
                "for": "5m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "API error rate above 5%", "runbook": "docs/INCIDENT_RESPONSE.md"},
            },
            {
                "alert": "AgentBlueCrossRealmSpike",
                "expr": "sum(increase(agentblue_cross_realm_attempts_total[5m])) > 10",
                "for": "1m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "Cross-realm access attempts spike", "runbook": "docs/SECURITY_OPERATIONS.md"},
            },
        ],
    }


def _warning_alerts() -> dict[str, Any]:
    return {
        "name": "agentblue-warning",
        "rules": [
            {
                "alert": "AgentBlueReviewQueueAging",
                "expr": "max(agentblue_review_queue_age_seconds_bucket) > 14400",
                "for": "10m",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "Review queue items aging >4 hours", "runbook": "docs/OPERATIONS_RUNBOOK.md"},
            },
            {
                "alert": "AgentBlueRetryBacklog",
                "expr": "sum(agentblue_writeback_jobs_total{status='FAILED_RETRYABLE'}) > 5",
                "for": "10m",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "Retry backlog growing", "runbook": "docs/RETRY_DEAD_LETTER.md"},
            },
            {
                "alert": "AgentBlueHighCorrectionRate",
                "expr": "sum(increase(agentblue_workflow_transitions_total{to_status='CORRECTED'}[24h])) / sum(increase(agentblue_workflow_transitions_total{to_status='APPROVED'}[24h])) > 0.5",
                "for": "1h",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "High human correction rate", "runbook": "docs/OPERATIONS_RUNBOOK.md"},
            },
            {
                "alert": "AgentBlueHighDBPoolUtilization",
                "expr": "agentblue_db_pool_checked_out / agentblue_db_pool_size > 0.8",
                "for": "5m",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "DB pool utilization above 80%", "runbook": "docs/OPERATIONS_RUNBOOK.md"},
            },
            {
                "alert": "AgentBlueStaleEscalations",
                "expr": "sum(agentblue_work_items_by_state{status='ESCALATED'}) > 0",
                "for": "4h",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "Escalations unresolved for >4 hours", "runbook": "docs/OPERATIONS_RUNBOOK.md"},
            },
        ],
    }


def validate_dashboard_json(dashboard: dict[str, Any]) -> bool:
    """Validate that a dashboard definition is well-formed."""
    required_keys = {"title", "panels"}
    if not all(k in dashboard for k in required_keys):
        return False
    for panel in dashboard.get("panels", []):
        if "title" not in panel or "query" not in panel:
            return False
    return True


def validate_alert_rules(rules: dict[str, Any]) -> bool:
    """Validate that alert rules are well-formed."""
    for group in rules.get("groups", []):
        if "name" not in group or "rules" not in group:
            return False
        for rule in group.get("rules", []):
            required = {"alert", "expr", "labels", "annotations"}
            if not all(k in rule for k in required):
                return False
    return True
