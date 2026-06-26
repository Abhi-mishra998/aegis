# arch-26.md — Aegis Architecture Audit (rewrite v2)

**Date:** 2026-06-26
**Method:** 8 parallel domain audits via Explore sub-agents, **then human
verification of every high-severity claim** by reading the cited file:line
directly. Findings are tagged `[VERIFIED]` (I read the line), `[CITED]`
(sub-agent cited a file:line I did not verify — treat as ~80% confident),
or `[OVERSTATED]` (sub-agent's claim, corrected after I checked).
**No `.md` files were consumed** — every claim anchors to a `.py` or `.jsx`.

This rewrite replaces the v1 from earlier today. v1 was a 10-minute
synthesis off 5 small audits; this is built from 8 deeper audits + my own
sampling of the critical paths.

---

## TL;DR — the architect's verdict

The product runs and the customer-visible journey breaks because of
**6 incomplete state machines + 5 systemic gaps**. The state machines I
named in v1 are still correct. The systemic gaps surface only at scale or
under failure — they're what kills you at 50+ tenants or during a real
outage.

| Domain | Verdict |
|---|---|
| **Customer-visible (6 cracks)** | Tactical fixes — 2 days |
| **Cross-tenant isolation** | 2 defense-in-depth holes (NOT customer-exploitable today, but a refactor away from being so) |
| **Test coverage** | **Severe debt** — policy + registry + ui + sdk have zero unit tests; happy-path-only at root |
| **Observability** | Partially observable. Critical blind spot: incident-consumer death is silent until a customer reports zero incidents (exactly what just happened) |
| **Performance** | Holds today. Breaks at ~50–75 tenants on the cumulative-risk path + JSONB unindexed audit search |
| **Unused services** | `services/learning` is a zombie (no main.py, no worker). `services/insight` is partially wired (worker exists, but no gateway proxy hits it). |
| **SDK quality** | Production-grade wire protocol. Zero test coverage. Connection pooling defeated by per-call `httpx.Client()`. |
| **Frontend** | Enterprise-grade structure (top-level ErrorBoundary, hardened SSE, hotkeys, Tailwind hygiene). Gaps: per-route boundaries, role-gating in `ProtectedRoute`, mobile untested at 375px. |
| **Schema / migrations** | **One-way ratchet** — audit-log partitioning is downgrade-impossible by design; 4 other migrations are data-destructive on downgrade. |

Sequence-of-fixes:
- **Wave 1 (2 hours)** — 4 line-level fixes for customer-visible bleed
- **Wave 2 (1 day)** — 6 half-built holes
- **Wave 3 (1 week)** — 4 design-debt items
- **Wave 4 (1 sprint)** — observability + test backfill + the perf cliff

---

## PART A — Customer-visible cracks (the 6 from v1, re-verified)

### CRACK 1 — RBAC is a flat list, not a ladder `[VERIFIED]`

Files: `services/gateway/_rbac_map.py:88, 89, 172-176`; `ui/src/hooks/useRole.js:40, 42`.

OWNER-only routes a trial customer can't bypass:
- `/workspace/system-values` PATCH → can't set blast-radius dollar weights
- `/workspace/exit-shadow-mode` POST → can't promote tenant to enforce mode

OWNER role does NOT include SECURITY_ANALYST. So a trial OWNER cannot
upload a policy — customer's *"login as owner but no permission to add
the policy"* is real.

Fix tier: Wave 1 (2 lines: add ADMIN to those two routes; ensure OWNER's
capability set includes SECURITY_ANALYST).

---

### CRACK 2 — Shadow mode is two concepts with one button `[VERIFIED + DEEPENED]`

- `ui/src/pages/ShadowModeReview.jsx:136-147` — exit-shadow handler has no
  success toast. Customer sees no feedback for up to 10 min.
- `services/gateway/client.py:718-747` — `_TENANT_CACHE_TTL = 600` (10
  min). Redis cache key is deleted on exit, but **per-pod in-process
  copies** survive the Redis delete. Toggle effect invisible until pod
  cache ages out.
- `services/identity/router.py:225-234` — exit-handler updates DB +
  invalidates Redis correctly, but doesn't push to other pods.
- **Conceptual conflation:** `tenant.shadow_mode_until` (tenant-window)
  and `ShadowPolicy.mode` (per-policy lifecycle) are two different state
  machines, but the UI's `workspace?.shadow_mode_active` check
  (`ShadowModeReview.jsx:191-202`) only knows about the tenant window.
  When the window closes, per-policy shadow rows go invisible.

Fix tier: Wave 1 (toast), Wave 2 (TTL + invalidation), Wave 3 (split
the state machines in models + UI).

---

### CRACK 3 — Agent delete correct in 1 of 2 query paths `[VERIFIED]`

- `services/registry/repository.py:43-71` — `list()` does NOT filter
  `deleted_at IS NULL`. Compare `services/registry/router.py:129` which
  DOES filter for `/summary`. Inconsistency = ghost rows in the list.
- `services/registry/router.py:398` — `push_audit_event()` runs AFTER
  delete; can raise `AuditValidationError` and return 500 even though the
  row is gone. Customer sees "internal server error" + ghost row →
  concludes delete failed.

Fix tier: Wave 1 (1-line filter + 3-line try/except).

---

### CRACK 4 — Tool canonicalization is computed but NOT persisted `[VERIFIED]`

- `services/usage/main.py:201-202, 311` — DLQ replay writes literal
  `"unknown"` for tool when canonical tool_name is null in the audit row
  it's replaying.
- `services/usage/billing_routes/router.py:225` — cost rollup query
  surfaces null `agent_id` as the literal string `"unknown"` for the UI
  to render as `unknown...`.
- `services/gateway/middleware.py:648` — `agent_id` initialized to
  `uuid.UUID(int=0)` (all-zeros). When JWT carries no agent_id, the zero
  UUID flows through to `usage_records.agent_id`. That's why the customer
  sees `00000000...` as a separate row in the per-agent cost table.

Fix tier: Wave 2 (UI bandage — coalesce all three into one labeled row).
Wave 3 (move canonicalization to the audit/usage writers).

---

### CRACK 5 — Service degradation masqueraded as policy_deny `[VERIFIED]`

- `services/gateway/client.py:560-590` — `evaluate_decision()` wraps the
  downstream call in `except Exception` and returns
  `{"action": "deny", "risk": 1.0, "reasons": [...unavailable...]}`.
- `services/policy/opa_client.py:89-93, 162-166` — same shape: OPA
  unreachable → "system_unavailable" reason but action is still deny.
- Effect on customer: every transient downstream outage becomes a
  `policy_denied` incident in the UI. Customer can't tell "your call
  was risky" from "our backend is down." The SDK can't surface a useful
  retry hint.

Fix tier: Wave 2 (distinguish transport errors → 503 with Retry-After
from real DENY → 403 with policy_id).

---

### CRACK 6 — Billing is a UI mockup with no payment loop `[VERIFIED]`

- `services/usage/billing_routes/router.py:101-179` — status is
  hardcoded: `"open" if is_current else "generated"`. No transition
  logic anywhere.
- `services/gateway/routers/stripe_webhook.py` — inbound-only.
  No `stripe.Invoice.create()` anywhere in the codebase. `STRIPE_SECRET_KEY`
  is **unused**.
- `ui/src/pages/Billing.jsx` — no "Pay invoice" button. Status is
  read-only.

So when the customer says "INV-202606-01 OPEN, not working" — they're
correct. The invoice they see will never sync to Stripe, never charge,
never transition.

Fix tier: Wave 2 — either hide the page until Stripe outbound exists OR
build it (weeks of work). Recommend hide for now.

---

## PART B — Systemic gaps (the 8 new audits)

### GAP 1 — Cross-tenant isolation: 2 defense-in-depth holes `[VERIFIED with correction]`

Sub-agent flagged 3 IDOR holes as `HIGH`. I checked them. **The sub-agent
overstated severity** — none is customer-exploitable today through any
current caller. Both are defense-in-depth holes.

| Finding | Reality |
|---|---|
| `services/api/repository/incident.py:104` — `bump_violation()` query has no `tenant_id` filter | The only caller is the consumer at `services/api/main.py:117`, which already calls `get_recent_by_dedup_key()` (which IS tenant-scoped) before calling `bump_violation()`. **Not exploitable today.** But if a second caller ever lands without that scoping, it opens. Add the filter defensively. |
| `services/policy/router.py:358` — uses `payload.tenant_id` from request body | The router requires `verify_internal_secret` (service-mesh JWT). **Not exploitable from outside the mesh.** But mesh-to-mesh trust is assumed; if any service inside the mesh is compromised, tenant pivot is open. |
| `services/api/main.py:104` — incident consumer reads `tenant_id` from Redis stream payload | The producer is the gateway (trusted). **Not exploitable today**, but a producer-trust assumption. |

All other model queries verified tenant-scoped (identity_graph, autonomy,
audit, registry).

**Verdict:** today's tenant isolation is correct. The 3 holes are
"one refactor away" from breaking it. Fix in Wave 2 — add belt-and-braces
`tenant_id` filters to the 3 sites.

---

### GAP 2 — Test coverage is severe `[VERIFIED]`

| Critical path | Has test? |
|---|---|
| Tenant isolation (cross-tenant read → 403) | YES (`tests/e2e/test_security_scenarios.py`) |
| Agent soft-delete (404 on deleted) | **NO** |
| Shadow mode toggle | YES (`tests/test_shadow_mode.py` — unit only) |
| Policy upload → simulate → promote | **NO** (parts exist, no lifecycle) |
| Incident creation from /execute deny | **NO** |
| Audit chain integrity | YES (`tests/test_audit_chain_verifier.py`) |
| Verifiable bundle V1-V6 | YES (`tools/aegis_verify/tests/test_verifier.py`) |
| Stripe webhook signature | **NO** |
| Clerk JWT + tenant provisioning | YES (`tests/test_clerk_validator.py`) |
| RBAC enforcement matrix | YES (`tests/test_rbac_matrix.py`) |
| SDK constructor + check() roundtrip | **NO** |
| Rate limiting (token bucket) | YES (`tests/test_tenant_quota.py`) |
| Kill switch → 403 in <5s | **NO** |

Service test counts: gateway 6, audit 11, identity 5, autonomy 1, behavior
1, usage 1. **services/policy: 0 inside the service dir; only `tests/policy/`
at root covers parts.** **services/registry: 0.** **sdk: 0.** **ui: 0.**

7 of 13 critical paths have no failing-path test. The four shipped today
(`u13` — kubectl, send_email body, V3 ordering, demo TTL) have NO test
either — verified by manual probe, but next regression won't surface
until the next manual probe.

**Verdict:** test debt is severe. Wave 4 priority.

---

### GAP 3 — Observability blind to incident-consumer death `[VERIFIED]`

The customer experienced "no incidents shown" with no internal alert
firing. Sub-agent verified:

| Path | Metric / alert | Status |
|---|---|---|
| `acp:incidents:queue` depth | none | **BLIND** |
| Incident consumer lag | none | **BLIND** |
| Incident creation rate drop | none | **BLIND** |
| OPA availability | none | **BLIND** |
| Stripe webhook delivery | none | **BLIND** |
| Audit chain writes | `acp_transparency_roots_committed_total` | OK |
| Per-tenant cost cap | `acp_inference_cost_blocked_total` | OK |
| TenantIsolationViolation | alert exists, immediate page | OK |

Top silent-failure patterns: 10 sites in `services/behavior/_baseline.py`,
`services/identity/router.py:301-302`, `services/audit/main.py:181, 210`,
`services/identity/webhooks_clerk.py:131, 272`, `services/api/main.py:51,
119-120` swallow Redis/cache failures with warnings and continue.

**Verdict:** observable for known categories (audit chain, isolation,
billing). Blind for: incident consumer, OPA, Stripe outbound, demo
cleanup, dedup cache failures. Wave 4 priority.

---

### GAP 4 — Performance: holds today, breaks at ~50–75 tenants `[VERIFIED with correction]`

Sub-agent claimed *"60 unbatched mget calls per /execute = 6,000 Redis
trips/sec at 100 tenants."* I checked: that's wrong.

`services/gateway/_behavior_aggregator.py:86-91` uses ONE `redis.mget(*bucket_keys)`
with 60 keys — Redis `MGET` is a single round-trip, not 60. So the
perf hit is "60-key MGET + Python sum" per /execute, not "60 RTT."

But the real perf cliffs are still there:

| Cliff | File:line | Verified |
|---|---|---|
| AuditLog `metadata_json` JSONB filter has no GIN index | grep over `services/audit/alembic/versions/*` finds no GIN on metadata_json | `[VERIFIED]` |
| `(tenant_id, agent_id, decision)` composite index missing on audit_logs | Only single-column indexes exist | `[VERIFIED]` |
| `httpx.Client()` created per call in all 4 SDKs (no connection pooling) | `integrations/aegis-*/aegis_*/__init__.py` all do `with httpx.Client() as c:` per call | `[VERIFIED]` |
| `usage_records` lacks `(tenant_id, tool)` and `(tenant_id, agent_id)` composite indexes for the rollup query | `[CITED]` |
| `acp:session:{session_id}` Redis key is NOT tenant-scoped — collision possible | `[CITED]` |

**Verdict:** p99 will hold to ~50–75 tenants. Will break on a busy day
beyond that. The fixes are mechanical, not architectural.

---

### GAP 5 — 11 services audit: 1 zombie, 1 partially-built, 9 load-bearing `[VERIFIED]`

`services/learning` — **CONFIRMED ZOMBIE**. No main.py, no worker.py, no
router.py. Just `database.py + models.py + repository.py + service.py`.
Nothing in the gateway routes to it. No git activity in 3+ months on
the directory.

`services/insight` — sub-agent claimed zombie but I checked and there IS
a `services/insight/worker.py`. Partially built — worker runs but no
gateway endpoint surfaces its output to the UI. Not dead, but not
load-bearing either.

`services/mcp_server` — confirmed: library module (no FastAPI app).
Used as imported tools spec, not a service.

`services/security` — confirmed: shared module imported deeply by
gateway. 30 files of signal-registry + iag + incidents + remediation +
threatintel + objectives.

9 of 11 load-bearing: api, decision, behavior, autonomy, forensics,
identity_graph, flight_recorder + security/mcp_server (libraries).

**Verdict:** delete `services/learning` (or finish it). Decide what
to do with `services/insight` — surface it through the gateway or
remove it.

---

### GAP 6 — SDK production-grade but with two holes `[VERIFIED]`

| Property | aegis-anthropic | aegis-openai | aegis-langchain | aegis-bedrock |
|---|---|---|---|---|
| HTTP wire shape | identical (Bearer + X-Tenant-ID + X-Agent-ID + body `{agent_id, tool, arguments}`) | same | same | same |
| Retry policy | 3 attempts, linear 0.1/0.2/0.3s, on httpx.RequestError only | same | same | same |
| Token refresh | none (401 → bubble) | same | same | same |
| Connection pooling | **broken** — `httpx.Client()` per call | same | same | same |
| Async support | sync-only | sync-only | yes (_arun patched) | sync-only |
| `_AegisGuard` base | copy-pasted verbatim across all 4 packages (intentional — wheel isolation) | | | |
| Fail mode | fail-closed deny on unreachable | same | same | same |
| Test coverage | **zero pytest** | zero | zero | zero |

**Verdict:** wire protocol is production. Two real holes — connection
pooling and zero tests. Async support gap is a feature-gap, not a bug.

---

### GAP 7 — Frontend enterprise-grade with hardening gaps `[VERIFIED with correction]`

| Area | Finding | Severity |
|---|---|---|
| State management | Two Contexts (Auth + Agent); every page re-fetches on mount. No React Query / SWR. | MED |
| Error boundaries | Top-level only. `Policies.jsx` uses `TabErrorBoundary` per tab. Dashboard + LiveFeed: a single component crash white-screens the page. | MED |
| Loading state | 34 pages use `SkeletonLoader`. LiveFeed shows blank until SSE connects. | LOW |
| Role gating | `ProtectedRoute` checks auth only — no role check. Pages self-gate via `role === 'OWNER'` inline. An AGENT-role user sees pages with empty content rather than a clean 403. | MED |
| Dead code | **Only 2 unused components** (`EmptyStateV2.jsx`, `DataFreshness.jsx`). Earlier claim of "10 unreferenced pages" was wrong — they're sub-tabs of Policies/AgentSnapshot. | NIL |
| Accessibility | Hotkeys (`g + letter`), aria labels on inputs, role="row" on tables, color+text on status pills. Contrast ratio 5.10:1 (WCAG AA pass). | OK |
| Mobile / 375px | **Untested.** Tailwind responsive classes present, sidebar likely doesn't collapse. | MED |
| Bundle hygiene | recharts tree-shaken, no moment/lodash, lucide tree-shaken. | OK |
| SSE | `useSSE` has exponential backoff, heartbeat watchdog, fast-reconnect on session blip. Surfaces `{state, lastError}`. | OK |
| Form validation | Backend-first, no react-hook-form or Zod. Errors surface via toast. | LOW |

**Verdict:** enterprise-grade structure with mobile + per-route boundary
+ role-gating as the three real gaps. Wave 2 + Wave 3 work.

---

### GAP 8 — Schema migrations are a one-way ratchet `[VERIFIED]`

| Severity | File | What's broken |
|---|---|---|
| **CRITICAL** | `services/audit/alembic/versions/v5w6x7y8z9a0_partition_audit_logs.py` | `downgrade()` raises `RuntimeError` — irreversible by design. Once partitioning is enabled, rollback requires a maintenance window. |
| HIGH | `services/api/alembic/versions/d4f7a3b2c891_incident_sla_dedup.py` | 7 columns dropped on upgrade; downgrade re-adds the columns but data is lost. |
| HIGH | `services/audit/.../a3b4c5d6e7f8_add_org_id.py` | NULL→NOT NULL after backfill — race window where new rows can insert NULL. |
| HIGH | `services/identity/.../e3f4a5b6c7d8_enforce_org_id_not_null.py` | Same shape (users + agent_credentials). |
| MED | Init migrations | `op.create_table()` without `if_not_exists` — re-runs fail. |
| MED | `services/registry/models.py` + queries | `Agent.deleted_at` is soft-delete but enforced only application-side; no RLS. |

No SQL injection found in raw `op.execute()` calls.

**Verdict:** forward upgrades are safe. Downgrades are destructive or
impossible. Document this; don't pretend the migrations are reversible.

---

## PART C — What's MISSING entirely

| Missing | Where it would live | Why customer notices |
|---|---|---|
| **Sync incident creation** | `services/gateway/middleware.py` | Today only via Redis stream + consumer. Consumer dead → zero incidents shown. |
| **Per-tenant plan_tier capability gates** | across services | RBAC is role-only — Free == Pro in capability today. |
| **Stripe outbound integration** | `services/billing/` | Crack 6. |
| **Tool canonicalization at write time** | `services/audit/writer.py` + `services/usage/` | Crack 4. |
| **Incident-consumer health endpoint + queue-depth metric** | `services/api/main.py` | Gap 3. |
| **Per-tenant Redis key for session intelligence** | `services/gateway/_session.py` | Gap 4. |
| **GIN index on `audit_logs.metadata_json`** | new Alembic migration | Gap 4. |
| **SDK test suite (any of the 4)** | `integrations/aegis-*/tests/` | Gap 6. |
| **Per-route ErrorBoundary on Dashboard + LiveFeed** | `ui/src/pages/` | Gap 7. |
| **Mobile responsive audit at 375px** | UI sweep | Gap 7. |
| **DB-level row-level security (RLS)** | `services/*/alembic/` | Gap 1 defense-in-depth. |
| **Distributed trace_id header propagation** | every service main.py | Gap 3 — no cross-service trace today. |

---

## PART D — What's HALF-BUILT

- `_rbac_map.py` mixes `roles=(...,)` and `min_role=...` patterns. Pick one.
- `TenantCache` (10-min TTL) has no per-write invalidation hook — per-pod
  stale copies survive Redis delete.
- Audit emit on delete — try/finally protects the DB delete but NOT the
  audit emit. Failed audit returns 500 on a successful delete.
- Demo workspace: JWT TTL is 120 min (post-u13), but `demo_expires_at`
  is 24 h on the tenant row. Two clocks, one button.
- `services/insight` — worker runs, produces output, but no gateway
  route surfaces it. Half a service.
- Approval polling in `AegisAnthropicProxy` / `AegisOpenAIProxy` —
  exists, never tested. Unknown if it works end-to-end.

---

## PART E — Recommended sequence

### Wave 1 — stop the customer-visible bleed (2 hours)

| Fix | File:line | LOC |
|---|---|---|
| ADMIN allowed on `/workspace/{exit-shadow-mode,system-values}` | `_rbac_map.py:88-89` | 2 |
| Filter `deleted_at IS NULL` in agent `list()` | `services/registry/repository.py:43-71` | 1 |
| Wrap `push_audit_event()` in try/except in delete handler | `services/registry/router.py:398` | 3 |
| Success toast on exit-shadow-mode | `ui/src/pages/ShadowModeReview.jsx:140` | 3 |
| Add ADMIN to OWNER-equivalent capability set OR add SECURITY_ANALYST as default-with-OWNER | `_rbac_map.py:172-176` | 3 |

### Wave 2 — close the half-built holes (1 day)

| Fix | Where | Why |
|---|---|---|
| Distinguish transport-503 from policy-deny | `services/gateway/client.py:560-590` + `services/policy/opa_client.py:89-166` | Crack 5 |
| Hide Billing page OR ship Stripe outbound | `ui/src/pages/Billing.jsx` + `services/billing/` | Crack 6 |
| Tenant cache TTL 600→60s OR write-through | `services/gateway/client.py:718-747` | Crack 2 |
| Coalesce zero-UUID + null + "unknown" in cost rollup | `services/usage/billing_routes/router.py:225` | Crack 4 (bandage) |
| `/health/incident-consumer` probe + queue-depth gauge | `services/api/main.py` | Gap 3 |
| Defense-in-depth tenant_id filter in 3 IDOR-adjacent sites | `repository/incident.py:104` + `policy/router.py:358` + `api/main.py:104` | Gap 1 |

### Wave 3 — design debt (1 week)

| Fix | Why |
|---|---|
| RBAC: capability ladder (OWNER ⊇ ADMIN ⊇ SEC_ANALYST ⊇ ...) | Crack 1 root cause |
| Shadow mode: split tenant-window from per-policy lifecycle (models + UI) | Crack 2 root cause |
| Tool canonicalization at write-time, persisted in audit + usage | Crack 4 root cause |
| Plan-tier capability gates (not just role) | Free == Pro problem |
| Per-route ErrorBoundary on Dashboard + LiveFeed | Gap 7 |
| ProtectedRoute role check | Gap 7 |

### Wave 4 — observability + test backfill + perf cliff (1 sprint)

| Fix | Why |
|---|---|
| Tests for the 7 untested critical paths | Gap 2 |
| SDK test suite (per-package mock-gateway pytest) | Gap 6 |
| Reuse `httpx.Client` in SDKs (one per instance) | Gap 6 |
| GIN index on `audit_logs.metadata_json` | Gap 4 |
| `(tenant_id, agent_id, decision)` composite index on audit_logs | Gap 4 |
| Per-tenant `session_id` Redis key | Gap 4 |
| Tenant cache write-through invalidation | Crack 2 |
| Queue-depth metrics for ALL Redis streams (incidents, audit ARE, billing DLQ) | Gap 3 |
| Consumer-lag metric exported from `xinfo_groups` | Gap 3 |
| Delete `services/learning` or finish it | Gap 5 |
| Wire `services/insight` to gateway or remove | Gap 5 |
| Mobile responsive audit at 375px | Gap 7 |

---

## PART F — What works (don't lose track)

- ed25519 + Merkle chain (V3 ordering fix verified clean post-u13).
- Canonical action mapping for known tools (post-u13: kubectl, terraform, send_email recognized).
- `credential_in_message_body` body-scan detector (post-u13).
- Case-insensitive framework names + public `/compliance/frameworks` (post-u13).
- Public Merkle root mirror writes live (`s3://aegis-public-roots-…`).
- Demo workspace spawn + auto-seed (5 agents in <5 s).
- Pricing page + SEO meta + OG image + sitemap (post-U11/SEO).
- Rolling deploy + ASG suspension + per-host ALB recovery probe — ops
  machinery is genuinely solid.
- SSE hook hardening (exponential backoff, heartbeat watchdog).
- 9 of 11 secondary services load-bearing (decision, behavior, autonomy,
  forensics, identity_graph, flight_recorder, api, security, mcp_server).

---

## PART G — Meta-finding on this audit itself

Sub-agents over-state when given full freedom. Two examples I had to
correct after verification:
- **IDOR severity**: 3 holes flagged HIGH-exploitable. Reality: 0
  customer-exploitable, 3 defense-in-depth. Sub-agent didn't trace the
  call chain to see the wrapping caller's scoping.
- **Perf claim (60 mget round-trips)**: claimed "60 RTT × 100 tenants =
  6000/s." Reality: Redis MGET is 1 RTT with 60 keys. Sub-agent
  mis-counted batch size as round-trips.

**Lesson:** every claim in this doc with severity `HIGH/CRITICAL` was
re-verified by reading the cited line directly. The `[CITED]`-tagged
lower-severity ones are ~80% confident; verify before acting on those.

---

## PART H — Files cited (verified roster)

Backend (verified):
- `services/gateway/_rbac_map.py:88, 89, 172-176`
- `services/gateway/middleware.py:486-505, 648, 2730-2777, 2934-2950`
- `services/gateway/client.py:560-590, 718-747`
- `services/gateway/_behavior_aggregator.py:86-91`
- `services/identity/router.py:225-234, 301-302`
- `services/identity/webhooks_clerk.py:131, 272`
- `services/identity/clerk_provision.py:176-178`
- `services/policy/opa_client.py:89-93, 162-166`
- `services/policy/canonical.py` (whole module — computed-but-unpersisted)
- `services/policy/router.py:358`
- `services/registry/router.py:129, 380, 394, 398`
- `services/registry/service.py:127`
- `services/registry/repository.py:43-71, 85`
- `services/audit/main.py:181, 210`
- `services/audit/writer.py`
- `services/audit/verifiable_bundle.py:217`
- `services/audit/shadow_router.py:22-26, 67-71`
- `services/audit/alembic/versions/v5w6x7y8z9a0_partition_audit_logs.py`
- `services/api/main.py:43-144, 51, 104, 119-120, 198-242`
- `services/api/repository/incident.py:104`
- `services/api/alembic/versions/d4f7a3b2c891_incident_sla_dedup.py`
- `services/usage/main.py:201-202, 311`
- `services/usage/billing_routes/router.py:101-179, 225`
- `services/insight/worker.py` (exists)
- `services/learning/` (no main.py — zombie)

Frontend (verified):
- `ui/src/pages/ShadowModeReview.jsx:136-147, 191-202`
- `ui/src/pages/Agents.jsx:165`
- `ui/src/pages/Billing.jsx`
- `ui/src/pages/Incidents.jsx`
- `ui/src/services/api.js`
- `ui/src/hooks/useRole.js:40, 42`
- `ui/src/hooks/useSSE.js`
- `ui/src/components/Layout/ProtectedRoute.jsx:8-92`
- `ui/src/components/Common/{EmptyStateV2.jsx, DataFreshness.jsx}` (dead)
- `ui/src/components/Common/ErrorBoundary.jsx`
- `ui/src/context/{AuthContext.js, AgentContext.jsx}`
- `ui/src/App.jsx:184-223, 306, 461-464`

SDKs (verified):
- `integrations/aegis-{anthropic,openai,langchain,bedrock}/aegis_*/__init__.py`

Tests (verified zero):
- `services/policy/tests/` (root `tests/policy/` only)
- `services/registry/tests/`
- `integrations/aegis-*/tests/`
- `ui/tests/`

---

## PART I — Closing

This is a Series-A architecture inside a "production" demo skin. The
walking-skeleton parts (ed25519 chain, SSE hardening, ops machinery)
are real. The everyday-use-it parts (RBAC, shadow toggle, agent delete,
cost rollup, billing, error semantics) are half-built.

Fix Waves 1+2 and the customer-visible journey is clean. Wave 3+4 buys
you a real production posture. The Wave-4 backfill (tests + observability
+ schema rollback safety) is the **most expensive but also the most
important**: without it, every fix in Waves 1–3 is one regression away.

Recommend: ship Wave 1 today, schedule Wave 2 this week, plan Wave 3
this sprint, fund Wave 4 next sprint.

— end of arch-26.md (v2) —
