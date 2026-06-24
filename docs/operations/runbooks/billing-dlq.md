# Billing DLQ + Replay Worker Runbook

## Background

The gateway's billing-retry worker at `services/gateway/main.py::_process_billing_queue` lands terminal failures in the Redis list `acp:billing_dlq` (after `retry_count > 5` on the live `acp:billing_retry_queue`). As of 2026-06-24 (Phase 3), `services/usage/dlq_replay.py` runs alongside the usage service workers and drains the DLQ every 60s.

This mirrors the audit DLQ replay worker (`services/audit/dlq_replay.py`) — same control-flow, adapted for the billing pipeline's list-based queue (instead of an audit stream).

Three outcomes per DLQ entry:

| Outcome              | Trigger                                                                  | Effect                                                                |
| -------------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| `replayed`           | Recoverable error (connection, timeout, IntegrityError) + retry_count < 5 | `RPUSH` back onto `acp:billing_retry_queue`; gateway worker retries it |
| `skipped`            | Payload unparseable (rare)                                               | Promoted to `acp:billing_dlq:permanently_failed` with raw bytes preserved |
| `permanently_failed` | FK violation / tenant deleted / `max_retries_exhausted` marker / retry_count >= 5 | Promoted to `acp:billing_dlq:permanently_failed`                      |

All three increment `acp_billing_dlq_replay_total{outcome=...}` (Prometheus, lifetime cumulative). The gateway's `/system/health` reads this counter alongside `acp_billing_events_total` / `acp_billing_events_failed_total` to compute the dashboard tile percentages.

## Dashboard signals

The `/system/health` payload exposes (under `queues`):

```jsonc
{
  "billing_retry_queue":                  0,    // live retry queue depth
  "billing_dlq_length":                   0,    // entries awaiting replay
  "billing_permanently_failed_length":    0,    // operator review queue
  "billing_success_rate_pct":          99.98,   // (attempted - failed) / attempted
  "billing_dlq_replay_success_rate_pct": 100.0  // replayed / (replayed + permanently_failed)
}
```

The UI surfaces these on the **System Health → Billing Pipeline Health** card.

## When "Replay Success Rate" drops below 90%

Symptom: `billing_dlq_replay_success_rate_pct < 90` for >5min, or `acp_billing_dlq_replay_total{outcome="permanently_failed"}` climbs faster than `outcome="replayed"`.

### Triage

1. **Identify the dominant error class** in the DLQ:

   ```bash
   docker exec acp_redis redis-cli LRANGE acp:billing_dlq 0 50 \
     | jq -r '.reason // .error // "no_error_field"' \
     | sort | uniq -c | sort -rn | head -10
   ```

2. **Inspect the `permanently_failed` list** to see what classes are dominating:

   ```bash
   docker exec acp_redis redis-cli LRANGE acp:billing_dlq:permanently_failed 0 20
   ```

3. **Check downstream health**:

   ```bash
   # Usage service reachable + accepting writes
   curl -s http://localhost:8005/health
   docker exec acp_usage pg_isready -d "$DATABASE_URL"

   # Live retry queue depth — if this is also growing, the gateway worker
   # is itself stuck (not just the DLQ replay).
   docker exec acp_redis redis-cli LLEN acp:billing_retry_queue
   ```

### Common root causes

| Symptom in DLQ errors                        | Root cause                                                       | Action                                                                      |
| -------------------------------------------- | ---------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `connection refused` / `timeout`             | Usage service or its Postgres unreachable                        | Restore connectivity. Replay will drain naturally on next 60s tick.         |
| `ForeignKeyViolation: tenant_id`             | Tenant deleted before usage drain                                | Already classified `permanently_failed`. Audit the deletion ordering.       |
| `max_retries_exhausted`                      | Gateway live worker already gave up after 5 retries              | Already classified `permanently_failed`. Check the original error class via `last_error`. |
| `IntegrityError on audit_id`                 | usage_records.audit_id unique constraint hit on replay           | Healthy — idempotent dedupe. Will eventually classify as `replay → success`. |
| `usage service 500 internal_server_error`    | Usage-side bug or migration drift                                | Roll back the offending deploy or apply the missing migration.              |

## When "Permanently Failed" depth grows

Symptom: `billing_permanently_failed_length > 50`, or Grafana alert on the same metric.

These events are **not lost** — they're parked for forensic review. Common causes:

1. **Tenant deleted before billing drain** — race between `DELETE FROM tenants WHERE id = …` and the usage consumer catching up on a backlog.
2. **Schema drift** — a column was added/dropped on `usage_records` without a migration rollout to all usage-service replicas. Check `alembic current` on each replica.
3. **Idempotency-key collision** — two billing events with the same `idempotency_key` but different `tokens` / `cost` — the value engine's HINCRBYFLOAT dedupe rejects the second. Usually a gateway bug; trace via `audit_id`.

### Recovery

1. Export the entries for forensic review:

   ```bash
   docker exec acp_redis redis-cli LRANGE acp:billing_dlq:permanently_failed 0 -1 > /tmp/billing_permfailed.txt
   ```

2. Once the root cause is fixed (migration applied, tenant restored, idempotency-key collision diagnosed), re-emit valid entries onto the live retry queue:

   ```bash
   # Manual replay — pop one entry, validate, RPUSH onto acp:billing_retry_queue.
   # The gateway worker's idempotency_key path will dedupe if a duplicate has
   # somehow already landed.
   docker exec acp_redis redis-cli LPOP acp:billing_dlq:permanently_failed
   # …inspect, then…
   docker exec acp_redis redis-cli RPUSH acp:billing_retry_queue "$ENTRY_JSON"
   ```

3. Document the incident — every entry promoted to `permanently_failed` deserves a postmortem line so the same class doesn't recur.

## Configuration

Tunables (env vars, defaults shown):

| Variable                          | Default | Effect                                                |
| --------------------------------- | ------- | ----------------------------------------------------- |
| `BILLING_DLQ_REPLAY_INTERVAL`     | `60`    | Seconds between drain passes                          |
| `BILLING_DLQ_REPLAY_BATCH_SIZE`   | `100`   | Max entries read per pass                             |
| `BILLING_DLQ_REPLAY_MAX_RETRIES`  | `5`     | Replays per entry before promotion                    |

## See also

- `services/usage/dlq_replay.py` — replay worker source
- `services/usage/main.py` — usage service + lifespan wiring
- `services/gateway/main.py` — gateway billing-retry worker + `/system/health` exposure
- `services/gateway/_mw_audit.py` — primary `_persist_billing_dlq` write path
- `ui/src/pages/SystemHealth.jsx` — "Billing Pipeline Health" dashboard card
- `services/audit/dlq_replay.py` — sibling audit-side replay worker (same control flow)
