# Soak Tests

*Long-running load tests that confirm the platform stays healthy under realistic traffic for an extended window. The fairness harness ensures one noisy tenant doesn't degrade another.*

## What soak proves

A soak test answers two questions a one-shot load test cannot:

1. **Does anything leak under sustained load?** Memory growth, connection pool exhaustion, stuck queues, transparent retries that accumulate over hours.
2. **Do per-tenant guarantees hold under multi-tenant pressure?** A noisy tenant should not push a quiet tenant's p99 latency over budget.

## Harness layout

Source: `tests/load/`.

| File | Purpose |
|---|---|
| `soak.py` | The 1,000-user / 60-minute / 5-tenant soak driver |
| `fairness.py` | Baseline + burst-load harness that measures per-tenant p99 |
| `locustfile.py` | The locust-driven user behavior model |
| `soak_user.py` | The per-user state machine |
| `concurrent_agents.py` | The multi-agent fan-out test |
| `post_run_checks.py` | The post-soak validation suite |

## Soak run configuration

The default soak:

- **1,000 concurrent users**
- **60 minutes total wall time**
- **5 tenants** with traffic mix:
  - Tenant A: 60% of total traffic (heavy)
  - Tenant B: 15%
  - Tenant C: 10%
  - Tenant D: 10%
  - Tenant E: 5% (quiet — the fairness target)
- Per-user request mix: 60% allowed, 15% denied (attack scenarios), 10% reads, 10% chain-verify, 5% misc

The mix is realistic — most decisions are allowed; some fail policy; a non-trivial fraction reads aggregates.

## Acceptance criteria

A soak run is PASSED when all of these hold at the end:

1. **`acp verify-chain` returns `violations=0`** for every tenant.
2. **`scripts/ops/reconcile.py` returns no audit↔usage gap** for every tenant.
3. **No flight-recorder timeline is stuck `in_progress`** — the worker backfill should resolve any pre-existing in-flight rows by end of soak.
4. **All transparency roots for the soak window are sealed** (one per tenant per day).
5. **p95 gateway latency stays under SLO** for every tenant (default budget: 250 ms).
6. **Quiet tenant (E) p99 does not degrade more than 20%** during the heavy tenant's burst — the fairness invariant.

A failed soak blocks the next production deploy until investigated.

## Running a soak locally

```bash
# Start a clean stack
cd infra && docker compose up -d

# Run the soak
cd ../tests/load
LOCUST_USERS=1000 LOCUST_RUN_TIME=60m python soak.py

# Output lands in reports/soak/{ts}/
```

The driver:

1. Provisions 5 tenants with the configured mix.
2. Spawns 1,000 locust users.
3. Runs for 60 minutes.
4. At the end, runs `post_run_checks.py`.
5. Writes a verdict to `reports/soak/{ts}/verdict.json`.

## Tear-down

After the run, the harness sets `rpm_limit=0` on every soak tenant in `acp_identity.tenants` so the synthetic agents cannot trigger further traffic. The append-only `audit_logs` rows from the soak stay; they're useful for analyzing chain-verify behavior at scale.

To fully clean up:

```bash
# Truncate the soak tenants' rows (does NOT preserve chain integrity; only do in non-prod)
psql $DATABASE_URL -c "
  DELETE FROM audit_logs WHERE tenant_id IN ('soak-a-uuid', 'soak-b-uuid', ...);
  DELETE FROM usage_records WHERE tenant_id IN ('soak-a-uuid', ...);
"
```

Or simply drop and recreate the local stack.

## Fairness harness

Source: `tests/load/fairness.py`.

The fairness harness runs in two phases:

**Phase 1: Baseline.** Every tenant runs at low load (10 users each) for 10 minutes. Records per-tenant p50, p95, p99 baseline.

**Phase 2: Burst.** Tenant A spikes to 500 users for 10 minutes. The other tenants stay at 10 users. Records p99 during the burst.

Acceptance:

- Tenant A's own p99 can rise (expected).
- Other tenants' p99 must not exceed `baseline * 1.20`. A 20% degradation is the budget.

The harness writes a per-tenant before/after comparison to `reports/fairness/{ts}/comparison.json`.

## Multi-agent fan-out

`tests/load/concurrent_agents.py` tests the per-tenant scenario where 50 agents in the same tenant all run concurrently. Confirms:

- The audit chain lock (`acp:audit_chain_lock:{tenant_id}`) serializes writes correctly without deadlock.
- Per-agent permission cache invalidation works under contention.
- Identity Graph edge writes do not collide.

## Continuous soak in CI

The full soak does not run on every CI commit (it's an hour). Instead:

- **Pull requests**: run a 5-minute mini-soak with 100 users.
- **Nightly**: run the full 60-minute soak against a dedicated stack.
- **Pre-release**: run a 4-hour soak with 2,000 users.

Failing soaks at any level block the corresponding promotion.

## Post-run checks

`tests/load/post_run_checks.py` runs after every soak. Checks:

```python
# 1. Chain integrity
GET /audit/logs/verify  → valid=true, violations=[]

# 2. Reconciliation
scripts/ops/reconcile.py → no gaps

# 3. No in-progress timelines
SELECT count(*) FROM execution_timelines WHERE status='in_progress' AND started_at < now() - interval '5 minutes' → 0

# 4. Transparency roots sealed
SELECT count(*) FROM transparency_roots WHERE date = today AND tenant_id IN (soak tenants) → 5

# 5. Latency SLO
acp_gateway_request_latency_seconds p95 < 0.25

# 6. Fairness invariant
quiet_tenant_p99 / quiet_tenant_baseline_p99 < 1.20
```

Any FAIL blocks the next promotion.

## Reporting

Soak reports land in `reports/soak/{ts}/`:

- `verdict.json` — overall pass/fail.
- `latency.json` — per-tenant p50/p95/p99.
- `chain_verify.json` — per-tenant verify output.
- `reconcile.json` — audit↔usage gap counts.
- `errors.log` — locust user-visible errors.

These are not committed; the operator reviews them before deciding to deploy.

## Common soak failures

| Symptom | Cause | Fix |
|---|---|---|
| Chain violations after soak | Concurrent writes raced past the chain lock | Verify lock TTL (5s) is honored; check Redis SETNX behavior |
| Quiet tenant p99 doubles | Gateway worker concurrency exhausted | Tune `UVICORN_WORKERS`; check rate limit per noisy tenant |
| `pending_usage_events` non-zero at end | Usage worker slow | Inspect drain latency histogram |
| Many `recovered_backfill` timelines | Gateway crashing mid-pipeline | Stack trace in gateway logs |
| `reconcile.py` reports gaps | Outbox not draining fully | Wait 30 seconds and re-run; expected on very-end-of-soak |

## Next

- [Gateway service](../services/gateway.md) — the load-bearing component
- [Audit service](../services/audit.md) — the chain lock and outbox
- [Multi-Tenancy](../architecture/multi-tenancy.md) — what fairness guarantees the platform offers
- [Observability](observability.md) — the dashboards that show soak progress live
