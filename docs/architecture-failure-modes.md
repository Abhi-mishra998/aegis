# Aegis — Architecture & Failure Modes

This is the one-page answer a buyer's security team needs when they
ask the questions every senior reviewer asks: *what happens if X dies?*
Every behaviour described here is implemented in the named code paths —
this isn't a brochure.

The platform is 22 containers on a 2-host ASG behind one ALB. A demo
spawns one tenant; a paid tenant gets the same control plane with
larger quotas. The Merkle audit chain anchors integrity claims; daily
roots are signed ed25519 and mirrored to a public S3 bucket
(`s3://aegis-public-roots-628478946931`) so any auditor can verify
historical decisions offline without trusting our control plane.

## TL;DR — failure-mode behaviour

| Dependency | If it dies | Customer impact | Code path |
|---|---|---|---|
| Redis | `behavior_firewall` fails closed per tenant policy; in-flight quotas tolerate a 5 s blip; durable state is replayed from Postgres on recovery | Tenants in `block_high_risk` mode see deny; `allow_with_audit` mode keeps serving | `services/gateway/middleware.py` → `degraded_mode_policy` |
| OPA | Policy engine returns **deny** for any path not in the 60-second LRU cache; circuit breaker opens after 5 consecutive timeouts | New deny patterns enforce; previously-evaluated allows keep flowing for 60 s | `services/policy/local_eval.py` + `sdk/common/resilient_client.py` |
| PostgreSQL | Audit writes spill to a Redis-backed DLQ + retried on recovery; reads served from in-memory caches where applicable; `/auth/me` continues serving JWT sessions for the cache window | Mutations (create agent, register key) return 503 with `Retry-After`; reads degrade gracefully | `sdk/common/resilient_client.py` + `acp_audit.audit_logs_dlq` |
| Any one downstream service | `ResilientClient` retries 3× with exponential backoff (0.5 s / 1 s / 2 s) then opens a per-service circuit breaker (60 s window) | Health surfaces in `/system/health` + `/status`; user sees `service_status="skipped"` on the affected decision | `sdk/common/resilient_client.py:ResilientClient.call(...)` |
| Whole gateway pod | ALB target-group health check fails → instance deregistered; ASG launches a replacement that bootstraps from `/aegis-prodha/current-sha` SSM parameter | <120 s of degraded throughput on the remaining host | `infra/terraform/modules/asg/` + `scripts/ops/safe_deploy.sh` |

## What survives each scenario in detail

### Redis dies

Redis stores three things: in-flight rate-limit counters, the
behavior-firewall sliding-window state, and the demo-token active-key
set. None of these are *primary* state — Postgres is the audit truth.

- **`/execute` evaluation** still runs because the policy engine
  doesn't touch Redis for routine decisions. Tenant-quota rate-limit
  pre-checks fail open with a `redis_unavailable` finding in the audit
  receipt, so the operator can see exactly when this happened.
- **Behavior firewall** has a per-tenant `degraded_mode_policy`
  (`block_high_risk` / `block_all` / `allow_with_audit` — settable in
  Settings → Security). The chosen policy is the documented
  fail-behaviour. Default is `block_high_risk` so anomalous patterns
  still get denied even without Redis.
- **SSE event stream** stops broadcasting but the underlying audit
  rows still land; on Redis recovery the EventSource reconnects and
  the Live Feed catches up via the backfill endpoint.
- **Demo workspaces** keep working because their JWTs are HS256-signed
  with `JWT_SECRET_KEY` — no Redis lookup required for verification.

Recovery is automatic: when Redis comes back, `ResilientClient` clears
its open circuit and the next call succeeds.

### OPA dies

OPA is the policy decision engine. Without it, we can't evaluate new
rules.

- **Cached decisions (60-second LRU)** continue serving — most demos
  hit the same handful of rule fingerprints, so the cache absorbs a
  short OPA outage transparently.
- **Cache miss with OPA down** returns `deny` with finding
  `policy_engine_unreachable`. This is intentional: a governance
  product defaulting to allow during a policy outage would be a
  scandal. The deny is logged + audited so an operator can see
  precisely which requests fail-closed.
- **Circuit breaker** opens after 5 consecutive OPA timeouts (60 s
  window). While open, all cache-miss requests deny immediately
  instead of hanging on retries — preserves p95 latency for the rest
  of the traffic.

### PostgreSQL dies

Postgres holds: tenants, agents, audit chain, identity graph, shadow
policies, incidents.

- **Audit chain writes** that can't reach Postgres land in a
  Redis-backed DLQ (`acp_audit.audit_logs_dlq`). A background worker
  drains the DLQ on Postgres recovery. Merkle-root sealing is paused
  during the outage — the chain catches up once the DLQ drains.
- **Tenant mgmt mutations** (create agent, rotate API key, invite
  user) return **503 Service Unavailable** with a Retry-After header.
  No silent failure.
- **`/auth/me` + JWT verification** keep working because Clerk JWTs
  are JWKS-verified at the gateway — no Postgres round-trip on the
  hot path. Existing sessions survive a Postgres outage.
- **Read endpoints** (Audit Logs, Forensics, Dashboard) degrade to
  the most recent in-memory cache + an explicit "data may be stale"
  banner.

### A single downstream service becomes unhealthy

Every cross-service call in the gateway goes through
`sdk/common/resilient_client.py:ResilientClient`. The contract:

- **3 retries** with exponential backoff (0.5 s, 1 s, 2 s) on
  network errors + 5xx.
- **Circuit breaker** per (target host, target path) tuple. Opens
  after 5 consecutive failures in a 60-second window. While open,
  calls fail fast with `service_status="skipped"` in the audit row.
- **Timeout budget** per upstream is short (8 s gateway, 1.5 s for
  the decision-engine consultation) so a slow service can't pin the
  whole request.

The `/system/health` endpoint walks every dependency in parallel +
returns per-service `{status, latency_ms, last_error}`. `/status`
collapses it to operational/degraded/outage for a public status
page.

## CSP migration plan (referenced from `ui/nginx.conf`)

Today's enforcing CSP still permits `unsafe-inline` on `script-src`
and `style-src`. The reasons (React JSX style-prop compilation,
Clerk overlay components, Stripe Elements iframe) are documented in
the nginx config. To get rid of `unsafe-inline` cleanly:

1. **Phase 1 (today)**: ship a parallel `Content-Security-Policy-
   Report-Only` header with the strict policy. Browsers don't enforce
   it but emit violation reports — that's how we learn which inline
   patterns are actually used in prod.
2. **Phase 2**: per-render nonce injection in the SPA shell
   (`ui/index.html`). React 18 + Vite can be configured to emit
   nonce-prefixed inline scripts.
3. **Phase 3**: drop `unsafe-inline` from the enforcing header.

`unsafe-eval` was already removed (2026-06-24) — production Vite
builds don't require it; it was a leftover from dev-mode HMR
configuration.

## What we don't have yet

- A live status page at `status.aegisagent.in` separate from
  `/status` (statuspage.io ingest is the planned source).
- Per-tenant uptime dashboards. Today every customer sees the
  global `/status` view.
- A formal SLA contract — Pro tier currently offers best-effort
  99.5%; Enterprise tier negotiates per-contract.

## Operational metrics (snapshot — refreshed nightly)

These are the numbers a buyer's procurement team should care about:

| Metric | Value | Source |
|---|---|---|
| Uptime (current 30-day window) | TBD — published nightly | CloudWatch synthetics on `/status` |
| Decisions evaluated (last 30 d) | TBD | `acp_audit.audit_logs` count |
| Violations blocked (last 30 d) | TBD | `audit_logs WHERE decision IN (deny, block, quarantine)` |
| Cross-tenant data incidents | 0 since launch | Postgres `users_org_tenant_match` constraint + per-row `tenant_id` filter on every read; integration tests in `tests/test_tenant_isolation.py` |
| Mean policy evaluation latency (p95) | ~21 ms | `services.gateway.latency_window.gateway_internal_window.summary()` |
| Audit-chain verification time (1 year of rows) | ~12 s | `aegis-verify` CLI; reference bundle at `/aevf/reference-bundle.json` |

Real operational telemetry is published at `/status` (real-time) and
the public Merkle roots at `s3://aegis-public-roots-628478946931`
(historical, signed).

## References

- `/status` — JSON snapshot of overall + per-service health, version,
  git sha, build time.
- `/system/health` — operator-grade detailed per-service health with
  latency breakdown.
- `/aevf/spec.md` — Aegis Evidence Verification Format (open spec the
  audit chain conforms to).
- `s3://aegis-public-roots-628478946931` — daily Merkle-root mirror,
  ed25519-signed, anonymously readable.
- `docs/security.md` — security architecture + threat model.
- `docs/dr_runbook.md` — disaster recovery procedures.
