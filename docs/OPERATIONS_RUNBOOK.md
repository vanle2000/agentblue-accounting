# Agent Blue — Operations Runbook

## Normal Startup

```bash
# Start production-shadow environment
docker compose --profile production-shadow up -d

# Verify health
curl http://localhost:8000/api/v1/health/live
curl http://localhost:8000/api/v1/health/ready
curl http://localhost:8000/api/v1/health/startup
curl http://localhost:8000/api/v1/health/mode
```

## Normal Shutdown

```bash
# Graceful shutdown (sends SIGTERM)
docker compose --profile production-shadow down

# Verify no orphaned jobs
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT count(*) FROM write_back_job WHERE status = 'IN_PROGRESS';"
```

## Deployment

```bash
# Pull latest
git pull origin main

# Build and deploy
docker compose --profile production-shadow up -d --build

# Verify
curl http://localhost:8000/api/v1/health/startup
```

## Rollback

```bash
# Revert to previous commit
git checkout <previous-commit>
docker compose --profile production-shadow up -d --build

# Verify
curl http://localhost:8000/api/v1/health/ready
```

## Migration Failure

```bash
# Check current state
docker compose exec api alembic current

# If migration failed mid-way, downgrade and retry
docker compose exec api alembic downgrade -1
docker compose exec api alembic upgrade head

# If downgrade also fails, restore from backup
# See Backup and Restore section
```

## Database Outage

```bash
# Check PostgreSQL status
docker compose exec db pg_isready -U agentblue -d agentblue_dev

# Check logs
docker compose logs db --tail=100

# Restart database
docker compose restart db

# Verify application reconnects
curl http://localhost:8000/api/v1/health/ready
```

## Worker Outage

```bash
# Check worker status
docker compose --profile production-shadow logs worker --tail=50

# Restart worker
docker compose --profile production-shadow restart worker

# Check for orphaned jobs
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT id, status, updated_at FROM write_back_job
      WHERE status = 'IN_PROGRESS'
      AND updated_at < now() - interval '5 minutes';"
```

## Stale Lease Recovery

```bash
# Find stale jobs
docker compose exec db psql -U agentblue -d agentblue \
  -c "UPDATE write_back_job
      SET status = 'FAILED_RETRYABLE',
          failure_category = 'WORKER_CRASH',
          failure_message = 'Recovered from stale lease'
      WHERE status = 'IN_PROGRESS'
      AND updated_at < now() - interval '10 minutes'
      AND attempt_count < max_attempts
      RETURNING id;"

# Or rely on automatic recovery (worker.recover_orphan_jobs)
```

## Orphan Recovery

Automatic on worker startup. Manual:

```bash
docker compose exec db psql -U agentblue -d agentblue \
  -c "UPDATE write_back_job
      SET status = 'FAILED_RETRYABLE'
      WHERE status = 'IN_PROGRESS'
      AND updated_at < now() - interval '10 minutes'
      RETURNING id;"
```

## OAuth Failure

```bash
# Check QuickBooks health
curl http://localhost:8000/api/v1/integrations/quickbooks/health

# Refresh tokens manually if needed
# Check QB_CLIENT_ID and QB_CLIENT_SECRET in environment
```

## QuickBooks Rate Limiting

```bash
# Check for rate-limit errors
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT count(*) FROM write_back_attempt
      WHERE failure_category = 'RATE_LIMITED'
      AND created_at > now() - interval '1 hour';"

# Wait for rate limit to reset, then retry
```

## Uncertain Write Outcome

```bash
# Find uncertain jobs
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT id, status, failure_category FROM write_back_job
      WHERE failure_category = 'UNKNOWN_EXTERNAL_FAILURE';"

# For each: reconcile before retry
# 1. Check QuickBooks for the transaction
# 2. Compare with approved state
# 3. If confirmed failed, retry
# 4. If confirmed succeeded, mark RECONCILED
# 5. If uncertain, escalate to human
```

## Reconciliation Mismatch

```bash
# Find mismatches
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT id, job_id, status, differences FROM reconciliation_result
      WHERE status = 'MISMATCH';"

# Review each mismatch
# If source changed: re-approve
# If target missing: investigate QuickBooks
# If field mismatch: escalate to human
```

## Dead-Letter Processing

```bash
# Find dead-letter jobs
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT id, work_item_id, failure_category, failure_message
      FROM write_back_job
      WHERE status = 'FAILED_PERMANENT';"

# For each: investigate root cause, then either:
# - Manual retry (if remediated)
# - Escalate to human
# - Close as won't fix
```

## Manual Retry

```bash
# Requires ACCOUNTING_WRITEBACK permission
# Via API:
curl -X POST http://localhost:8000/api/v1/accounting/writeback-jobs/{id}/execute \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Manual retry after OAuth fix"}'
```

## Backup

```bash
# Create backup
docker exec agentblue-db pg_dump -U agentblue -d agentblue_dev \
  --no-owner --no-privileges > backup_$(date +%Y%m%d_%H%M%S).sql

# Verify checksum
sha256sum backup_*.sql
```

## Restore

```bash
# Stop application
docker compose --profile production-shadow down

# Restore database
docker exec -i agentblue-db psql -U agentblue -d agentblue_dev < backup_FILE.sql

# Verify migrations
docker compose --profile production-shadow up -d
docker compose exec api alembic current

# Verify health
curl http://localhost:8000/api/v1/health/ready
```

## Revoked-Token Incident

```bash
# Check revoked tokens
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT jti, revoked_at, reason FROM revoked_token ORDER BY revoked_at DESC LIMIT 10;"

# Verify revoked tokens are rejected
curl -H "Authorization: Bearer <revoked-token>" \
  http://localhost:8000/api/v1/accounting/work-items
# Should return 401
```

## Cross-Realm Attempt

```bash
# Check cross-realm attempts (from metrics)
curl http://localhost:8000/metrics | grep cross_realm

# Investigate in audit log
docker compose exec db psql -U agentblue -d agentblue \
  -c "SELECT * FROM audit_event
      WHERE action LIKE '%cross_realm%'
      ORDER BY created_at DESC LIMIT 10;"
```

## Alert Triage

1. Check alert severity (critical vs warning)
2. Check Grafana dashboard for context
3. Check application logs
4. Check database state
5. Follow runbook for specific alert
6. Escalate if unresolved after 30 minutes

## Escalation Ownership

| Severity | Owner | Response Time |
|----------|-------|---------------|
| Critical | On-call engineer | 15 minutes |
| Warning | Team lead | 1 hour |
| Info | Next business day | 24 hours |
