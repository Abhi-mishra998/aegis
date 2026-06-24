# Audit DLQ + Replay Worker Runbook

## Background

The audit consumer at `services/audit/main.py` lands terminal failures in the Redis stream `acp:audit_stream:dlq`. As of 2026-06-24 (Phase 3), `services/audit/dlq_replay.py` runs alongside the consumer and drains the DLQ every 60s.

Three outcomes per DLQ entry:

| Outcome              | Trigger                                                | Effect                                                        |
| -------------------- | ------------------------------------------------------ | ------------------------------------------------------------- |
| `replayed`           | Recoverable error (connection, timeout, IntegrityError) + retry_count < 5 | `XADD` back onto `acp:audit_stream`; consumer retries it      |
| `skipped`            | Payload unparseable (rare)                             | Promoted to `acp:audit_stream:permanently_failed`             |
| `permanently_failed` | FK violation / tenant deleted / retry_count >= 5       | Promoted to `acp:audit_stream:permanently_failed`             |

All three increment `acp_audit_dlq_replay_total{outcome=...}` (Prometheus, lifetime cumulative). The gateway's `/system/health` reads this counter alongside `acp_slo_audit_durability_total{stage=...}` to compute the dashboard tile percentages.

## Dashboard signals

The `/system/health` payload exposes (under `queues`):

```jsonc
{
  "audit_stream_length":                  0,    // live stream depth
  "audit_dlq_length":                     0,    // entries awaiting replay
  "audit_permanently_failed_length":      0,    // operator review queue
  "audit_success_rate_pct":             99.98,  // persisted / (persisted + dlq landings)
  "audit_dlq_replay_success_rate_pct":  100.0   // replayed / (replayed + permanently_failed)
}
```

The UI surfaces these on the **System Health → Audit Pipeline Health** card.

## When "DLQ Replay Success Rate" drops below 90%

Symptom: `audit_dlq_replay_success_rate_pct < 90` for >5min, or `acp_audit_dlq_replay_total{outcome="permanently_failed"}` climbs faster than `outcome="replayed"`.

### Triage

1. **Identify the dominant error class** in the DLQ:

   ```bash
   docker exec acp_redis redis-cli XRANGE acp:audit_stream:dlq - + COUNT 50 \
     | awk '/error/{getline; print}' \
     | sort | uniq -c | sort -rn | head -10
   ```

2. **Inspect the `permanently_failed` stream** to see what classes are dominating:

   ```bash
   docker exec acp_redis redis-cli XRANGE acp:audit_stream:permanently_failed - + COUNT 20
   ```

3. **Check downstream health**:

   ```bash
   # Postgres reachable + accepting writes
   docker exec acp_audit pg_isready -d "$DATABASE_URL"

   # Redis stream consumer group still healthy
   docker exec acp_redis redis-cli XINFO GROUPS acp:audit_stream
   ```

### Common root causes

| Symptom in DLQ errors                        | Root cause                                                  | Action                                                                  |
| -------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------- |
| `connection refused` / `timeout`             | Postgres pgbouncer or RDS unreachable                       | Restore connectivity. Replay will drain naturally on next 60s tick.     |
| `ForeignKeyViolation: tenant_id`             | Tenant deleted before audit drain (Phase 2 scenario)        | Already classified `permanently_failed`. Audit the deletion order.       |
| `IntegrityError on column X`                 | Schema drift between writer and migrations                  | Apply the missing migration; restart the audit service.                  |
| `ed25519 signature verify failed`            | Receipt-signing key rotation gone wrong                     | See `docs/runbooks/key_rotation.md`. Promote historical key first.       |

## When "Permanently Failed" depth grows

Symptom: `audit_permanently_failed_length > 50`, or Grafana alert on the same metric.

These events are **not lost** — they're parked for forensic review. Common causes:

1. **Tenant deleted before drain** — race between `DELETE FROM tenants WHERE id = …` and the audit consumer catching up on a backlog. The Phase 2 fix narrows this window; old events still land here.
2. **Schema drift** — a column was added/dropped without a migration rollout to all audit-service replicas. Check `alembic current` on each replica.
3. **Key rotation gone wrong** — receipt-signing key was rotated but the historical key wasn't promoted to `transparency_historical_keys`, so receipts written under the old key fail verification on insert. Use `scripts/maintenance/rotate_transparency_key.py` to recover.

### Recovery

1. Export the entries for forensic review:

   ```bash
   docker exec acp_redis redis-cli XRANGE acp:audit_stream:permanently_failed - + > /tmp/permfailed.txt
   ```

2. Once the root cause is fixed (migration applied, key promoted, tenant restored), re-emit valid entries:

   ```bash
   # Manual replay — pop one entry, re-XADD onto acp:audit_stream, then XDEL.
   # The audit consumer's idempotent ON CONFLICT path will dedupe if a duplicate
   # has somehow already landed.
   docker exec acp_redis redis-cli XRANGE acp:audit_stream:permanently_failed - + COUNT 1
   # …inspect, then…
   docker exec acp_redis redis-cli XADD acp:audit_stream '*' tenant_id "$TID" agent_id "$AID" action execute_tool tool db.query decision allow
   docker exec acp_redis redis-cli XDEL acp:audit_stream:permanently_failed "$ID"
   ```

3. Document the incident — every entry promoted to `permanently_failed` deserves a postmortem line so the same class doesn't recur.

## Configuration

Tunables (env vars, defaults shown):

| Variable                          | Default | Effect                                                |
| --------------------------------- | ------- | ----------------------------------------------------- |
| `AUDIT_DLQ_REPLAY_INTERVAL`       | `60`    | Seconds between drain passes                          |
| `AUDIT_DLQ_REPLAY_BATCH_SIZE`     | `100`   | Max entries read per pass                             |
| `AUDIT_DLQ_REPLAY_MAX_RETRIES`    | `5`     | Replays per entry before promotion                    |

## See also

- `services/audit/dlq_replay.py` — replay worker source
- `services/audit/main.py` — consumer + lifespan wiring
- `services/gateway/main.py` — `/system/health` exposure of the success-rate fields
- `ui/src/pages/SystemHealth.jsx` — "Audit Pipeline Health" dashboard card
- `docs/runbooks/audit_chain_violation.md` — adjacent runbook for chain (not buffer) integrity
