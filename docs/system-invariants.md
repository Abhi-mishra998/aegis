# System invariants

This is the contract that every code change in this repo must preserve.
Reviewers should read this before approving a PR that touches audit,
tenant lifecycle, decision pipeline, or transparency code. CI gates
listed in each section enforce the invariants automatically where
possible; the rest are review-time obligations.

A "violation" of any invariant in this doc is a P0. Open an incident,
freeze deploys, and root-cause before continuing.

## Audit invariants

### A1 — Every gateway decision produces exactly one audit record

Every `/execute` request that reaches the gateway's policy fan-out
must land in `audit_logs` exactly once. Producer-side dedupe lives in
the request_id check (`services/gateway/_mw_audit.py`); consumer-side
idempotency comes from `AuditWriter.log()` (`services/audit/writer.py`)
using a per-(tenant, chain_shard) advisory lock.

Failure mode this prevents: silent under-counting of denied actions,
which would mis-state the customer's compliance posture.

CI gate: `tests/integration/test_audit_idempotency.py` re-fires the
same request_id 100x concurrently and asserts exactly one row.

### A2 — Every audit record carries `tenant_id`, `request_id`, `action`, `decision`, `ts`

These are MANDATORY fields. They are validated at the producer
(`sdk/common/audit_emit.py:emit_audit_event`) BEFORE the Redis xadd.
Missing-field events land in `acp:audit_stream:producer_dlq` with a
stacktrace identifying the offending caller — the consumer never sees
them. Why producer-side: if validation runs at the consumer, the
operator gets a 30-minute stale DLQ entry instead of an immediate
visible bug at the call site.

Failure mode this prevents: malformed events landing in
`acp:audit_stream:dlq` and silently rotting (which is how we ended up
with 65 DLQ entries — see Phase 1/2 fix in
`fix(audit): producer-side validation` commit).

CI gate: `services/audit/tests/test_emit_audit_event.py` asserts that
missing any required field raises + writes to producer_dlq.

### A3 — Audit chain is append-only and Merkle-rooted

Every row carries `event_hash = sha256(prev_hash || canonical(event))`
where `prev_hash` is the previous row's `event_hash` for the same
(tenant_id, chain_shard) pair (`services/audit/writer.py:80`). Chains
are sharded 16-way (`compute_chain_shard` at line 33) to avoid a single
write hotspot per tenant. The daily Merkle root over all rows in the
window is signed with ed25519 and published to
`s3://aegis-public-roots-628478946931`.

Failure mode this prevents: tamper-without-detection. Even a full
root-key compromise is publicly verifiable because any customer that
archived an earlier root can replay the chain.

CI gate: `tests/integration/test_chain_verifier.py` rebuilds the chain
from raw rows + verifies every event_hash. Runs on every PR.

### A4 — The audit consumer's DLQ landings have a path back

Terminal failures dropped into `acp:audit_stream:dlq` are NOT a quiet
black hole. The `dlq_replay` worker (`services/audit/dlq_replay.py`)
inspects every DLQ entry every 60 s, classifies the error
(transient → replay, FK-violation → permanently_failed,
retry_count ≥ 5 → permanently_failed), and re-xadd's eligible
entries to the live stream. The `/system/health` endpoint surfaces:

- `audit_dlq_length` — DLQ awaiting replay
- `audit_dlq_replay_success_rate_pct` — successful replays as % of
  attempted in the last 24 h
- `audit_permanently_failed_length` — gave-up bucket

Failure mode this prevents: a 1-minute Postgres blip silently
dropping the customer's compliance evidence.

## Tenant invariants

### T1 — Tenant deletion is ordered: STOP → CONFIRM → DRAIN → DELETE

A demo tenant cannot be deleted from `tenants` until:

1. **STOP**: the background traffic worker is SIGTERM'd and proc.poll()
   confirms exit (SIGKILL fallback at 5 s).
2. **CONFIRM**: the consumer-group XPENDING count for events tagged with
   this tenant_id is 0 (poll up to 30 s).
3. **DRAIN**: the audit stream has consumed everything emitted by the
   worker before it exited.
4. **DELETE**: only THEN does the `DELETE FROM tenants WHERE
   tenant_id = $1` statement run.

If STOP or CONFIRM can't be completed in their budgets, the tenant
is left in place and a follow-up reaper sweeps it on the next cron.
A leaked tenant row is recoverable; a leaked tenant + a still-running
worker emitting orphan audit events is the bug that produced the
DLQ-65 incident on 2026-06-24.

Implementation: `services/gateway/routers/demo.py:_run_demo_traffic`
+ `cleanup_expired_demos`.

CI gate: `services/gateway/tests/test_demo_lifecycle.py` mocks the
subprocess + redis stream and asserts the DELETE statement only runs
after the drain confirmation.

### T2 — Tenant_id in a JWT must match `X-Tenant-ID` in the header

The gateway's auth middleware (`services/gateway/_mw_auth.py`) does a
constant-time compare between the JWT's `tenant_id` claim and the
inbound `X-Tenant-ID` header. Mismatch → 403, audit row tagged
`security_violation`. Never weaken this to a "warn-only" check.

Failure mode this prevents: a token stolen from tenant A being used
to impersonate tenant B by spoofing the header.

### T3 — Tenant_id is the boundary for every row in every table

Postgres-level RLS (Row-Level Security) is enabled on every multi-
tenant table. Service-side ORM access uses
`SET LOCAL app.current_tenant_id` per-request so even a SQL injection
that bypasses the application can't cross tenant lines.

CI gate: `tests/integration/test_rls_enforcement.py` runs a query as
tenant A and asserts zero rows from tenant B's data are returned even
when the query has no WHERE clause.

## Receipt invariants

### R1 — Every audit row has a verifiable receipt

`GET /receipts/{request_id}` returns the audit row + the daily Merkle
root + the inclusion proof. Verifier code lives in `sdk/utils.py`
(`verify_root_chain`, `verify_root_signature`) and the
`acp verify-chain` / `acp verify-root` CLI commands. Customers can
verify offline against the publicly-archived root.

Failure mode this prevents: customer-side trust regression. A
receipt that can't be verified is functionally worthless.

### R2 — Receipts are signed with a key whose successor is recorded

When the ed25519 signing key is rotated
(`scripts/maintenance/rotate_transparency_key.py`), the previous key
is moved to `transparency_historical_keys` BEFORE the new key is
activated. The signature on every previously-issued receipt remains
verifiable forever.

Runbook: `docs/runbooks/key_rotation.md`. Drill cadence: quarterly,
logged at `docs/runbooks/key_rotation_drill_log.md`.

## Decision invariants

### D1 — Policy fan-out is fail-closed for policy decisions

If OPA / decision-service / signal-registry can't be reached within
`DECISION_GATHER_TOTAL_TIMEOUT` (currently 1.5 s), the gateway
returns `service_status=skipped` for the missing slice AND drops the
overall decision to the tenant's `degraded_mode_policy`
(`block_high_risk` by default — i.e., deny anything above tier
medium). It does NOT silently allow.

Failure mode this prevents: a network blip turning into an "allow
all" window that gets exploited.

CI gate: `tests/chaos/test_resilience_live.py[acp_opa]` kills OPA
mid-load and asserts the decision verdict drops to deny.

### D2 — A 429 (rate limited) request still writes an audit row

Rate limits are first-class denials, not silent drops. Every 429
emits an audit row with `action="rate_limited"` carrying
`limit_type`, `reset_at`, and the request's tenant/agent context.
The customer's compliance evidence is complete even under attack.

Implementation: `services/gateway/_mw_rate_limit.py:181-198`.

## Operational invariants

### O1 — No production secret in code, .env files committed, or logs

`.gitleaks.toml` runs on every commit. CI fails the PR if any line
matches a credential signature. Secrets live in AWS Secrets Manager,
mounted via SSM Parameter Store. Never `echo $SECRET` in a deploy
script.

### O2 — Append-only deploy history

Every prod deploy writes its SHA to
`/aegis-prodha/current-sha` in SSM Parameter Store. The deploy
wrapper (`scripts/ops/rolling_deploy.sh` calling `safe_deploy.sh`)
reads + verifies + records. Lets a fresh ASG host self-bootstrap to
the canonical version via user_data. Never bypass this — direct
`docker compose up` on a host without writing the SSM value
back is how prod silently drifts.

### O3 — Backups are restorable, not just recordable

Backups land at `s3://aegis-prod-backups-628478946931` nightly via
`scripts/ops/backup.sh`. They are exercised quarterly with a
real-restore drill (`scripts/ops/restore_drill.sh`) that:
- Provisions a fresh isolated VPC + RDS instance
- Restores the latest age-encrypted pg_dump
- Runs the schema-integrity test suite against the restored DB
- Reports MTTR

Drill log: `docs/runbooks/dr_drill_log.md`.

A backup that has never been restored doesn't exist.

## Adding a new invariant

When a new invariant ships:

1. Add it here under the right section with: rule, failure-mode-prevented,
   implementation file:line, CI gate (or `review-time obligation` if no
   automated gate).
2. Add or extend the CI gate test.
3. Reference this doc from the PR description.

A new invariant without a CI gate is a polite suggestion, not a
contract. Either write the test or write "review-time obligation"
explicitly so the next maintainer knows where the guard rail is.
