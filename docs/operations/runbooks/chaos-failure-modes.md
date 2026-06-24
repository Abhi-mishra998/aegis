# Chaos failure modes — expected vs observed

Companion document to `docs/runbooks/chaos_drill_log.md` (the append-only
results log) and `tests/chaos/test_resilience_live.py` (the automated
suite). This page describes what each failure scenario is supposed to
do — the contract — so an on-call reviewing a drill output knows
whether "5 s p95 during kill" is a pass or a regression.

Each scenario has four fields:

- **Trigger** — exactly what the drill does
- **Expected behavior** — what the platform contract says happens
- **Observed behavior** — what we saw in the most recent drill
- **Recovery time** — wall-clock seconds from kill to back-to-healthy

If observed diverges from expected during a drill, page on-call and
open a SEV-2. Do NOT silently update this doc to match the new
behavior unless you've also updated the contract elsewhere
(`docs/system-invariants.md`, runbooks, customer SLO doc).

## Scenario 1 — Redis dies

**Trigger**
```bash
docker kill acp_redis
# 30s sustained 10 req/s /execute load runs throughout
```

**Expected behavior**
- Gateway returns ≤ 25 % 5xx during the kill window (acceptable
  back-pressure; not a 500 storm)
- Sessions degrade to in-memory fallback for the kill window
  (`services/gateway/session_intelligence.py`)
- SSE handshake fails-soft — connections close, clients retry on
  reconnect, no incident overlay
- Behavior firewall consult falls through to the tenant's
  `degraded_mode_policy` (default `block_high_risk` — i.e., deny
  anything above tier medium)
- Audit emits queue locally (in-process bounded buffer) for up to
  10 s; if Redis stays down longer, events fail-closed and the
  gateway returns 503 with `service_status=audit_unavailable`
- Container restarts within 60 s (Docker `restart: always`)

**Observed behavior** (last drill: nightly, see `chaos_drill_log.md`)
- 18 % 5xx during the 12 s kill window (under the 25 % budget)
- p95 latency 3.8 s during the window (under the 5 s budget)
- Container back-healthy in 42 s

**Recovery time** (target): ≤ 60 s. (Last observed: 42 s.)

## Scenario 2 — OPA dies

**Trigger**
```bash
docker kill acp_opa
```

**Expected behavior**
- Policy evaluation reports `service_status=skipped` on its decision
  slice
- Gateway's decision fan-out (`services/gateway/decision_orchestrator.py`,
  total budget 1.5 s) treats missing-OPA as a fail-closed signal
- For tenants on the default `degraded_mode_policy=block_high_risk`,
  any decision the surviving signal-registry classifies above tier
  medium gets denied
- For tenants on `degraded_mode_policy=block_all`, every request is
  denied while OPA is down (audit row tagged `degraded_block`)
- Container restarts within 60 s

**Observed behavior**
- 4 % 5xx during the kill window (most requests cleanly denied, not
  errored)
- p95 latency 2.1 s (decision fan-out timeout is the bound)
- Every denied request emitted an audit row — no silent allows

**Recovery time** (target): ≤ 60 s. (Last observed: 38 s.)

This is the most security-critical of the kill scenarios. Silent
allows during OPA downtime would be a P0 customer incident — the
test asserts ≥ 95 % of decisions during the kill window resolved to
a valid action (allow / monitor / escalate / deny), NOT a 500.

## Scenario 3 — Postgres dies

**Trigger** (currently NOT in the automated suite — manual-only)
```bash
docker kill acp_postgres
```

**Expected behavior**
- All `/execute` requests start returning 503 within ~2 s (pgbouncer
  connection-pool exhaustion + downstream timeout cascade)
- Gateway emits `service_status=db_unavailable` audit rows TO the
  local in-process buffer (cannot land them in `audit_logs` because
  the DB is gone — they're flushed on recovery)
- Identity service falls back to JWT-only validation; existing
  sessions continue working until JWT exp
- New auth (login / signup / clerk-provision) fails 503
- Container restarts when the underlying DB process resumes (not
  Docker-managed in prod — RDS is external)

**Observed behavior** — NOT YET DRILLED. Open issue: add an
automated case that uses `pg_terminate_backend` on a per-connection
basis since `docker kill acp_postgres` doesn't model the real prod
failure (RDS multi-AZ failover, not container death).

**Recovery time** (target, prod): ≤ 120 s for RDS automated
failover. Application-side: ≤ 30 s after DB is back (connection
pool refill + first successful query).

**Action item**: Q3 sprint — add the `test_db_terminate_backend`
case to `tests/chaos/test_resilience_live.py`.

## Scenario 4 — Decision service dies

**Trigger**
```bash
docker kill acp_decision
```

**Expected behavior**
- Gateway back-pressures cleanly — decision-call timeouts trigger
  the same fail-closed path as OPA-down (Scenario 2)
- No 500 storm; back-pressure shows up as 4xx (degraded-mode block)
  for the kill window
- Recovery: container restarts within 60 s; gateway resumes normal
  fan-out automatically

**Observed behavior** (last drill)
- 6 % 5xx during the kill window
- p95 latency 4.4 s (close to the 5 s budget — investigate if it
  drifts further)
- 100 % of decisions during the window resolved to a valid action

**Recovery time** (target): ≤ 60 s. (Last observed: 45 s.)

## Scenario 5 — DB connection-pool burst

**Trigger**
```bash
# Not a kill — a saturation. Fires 200 concurrent /execute in 5s.
pytest tests/chaos/test_resilience_live.py::test_db_pool_exhaustion_under_burst
```

**Expected behavior**
- Gateway returns 429 / 503 with `Retry-After` header (clean
  back-pressure) instead of 500s
- Audit emits for the 429 responses still land (rate-limit denials
  are first-class audit events — `services/gateway/_mw_rate_limit.py:181`)
- p95 latency under 8 s during the burst (higher bound than kill
  scenarios because this is intentional saturation)
- Crash rate < 5 % (uncaught exceptions in the gateway worker
  process)

**Observed behavior** (last drill)
- 12 % 429 + 4 % 503; zero 500s; zero crashes
- p95 4.9 s during burst
- All rate-limit denials carried valid audit rows

## What ISN'T tested today (gaps to close)

- **Postgres failover** (RDS multi-AZ) — see Scenario 3 action item
- **ALB partial failure** (one of two target hosts hard-fails mid-deploy)
  — covered by `safe_deploy.sh`'s rolling deploy gate, but no
  automated drill
- **Clerk outage** — Clerk's JWKS endpoint going down should fall
  back to cached keys; not in the automated suite (would require
  mocking Clerk + a separate test harness)
- **S3 outage** (transparency root publish) — non-critical to the
  request path; sealing job retries on next cron

## How to run a drill manually

```bash
# Must run on one of the prod or staging EC2 hosts (needs docker).
# NEVER run on a host serving live customer traffic.

ssh <staging-host>
cd /opt/aegis
pytest -m chaos tests/chaos/test_resilience_live.py -v

# Record the result in docs/runbooks/chaos_drill_log.md.
# If any case FAILS, page on-call and open SEV-2.
```

## How to add a new scenario

1. Add the test case to `tests/chaos/test_resilience_live.py`.
2. Add a section here following the four-field template (Trigger /
   Expected / Observed / Recovery time).
3. Update the "What ISN'T tested today" list.
4. The first nightly run after merge auto-appends a row to
   `chaos_drill_log.md` — fill in Observed + Recovery time here from
   that first result.
