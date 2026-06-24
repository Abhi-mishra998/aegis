# Enterprise Architecture Rationale

This document responds directly to the senior security architect review. Each
section names a critique, gives the engineering answer with code citations,
states what the choice costs us, and identifies the trigger that would flip
the decision.

## Scale story — what we've proven vs what we'd benchmark before a multi-tenant cutover

The demo runs at 2-3 requests per second. That is functional proof the
request fan-out works end-to-end. It is not a load test.

What we have on disk:

- `tests/load/soak.py` — 1000 concurrent users, 60-minute window, 5 tenants
  in parallel, attack-shaped 60/15/10/10/5 traffic mix. Provisions tenants,
  drives Locust headless, then runs four post-run checks (chain verify,
  reconciliation, flight-timeline closure, transparency-root verification).
  The acceptance gate (lines 373-376) is **aggregate failure rate ≤ 0.5%**
  and **`/execute/valid` p99 ≤ 500 ms**.
- `tests/load/fairness.py` — Phase A: 50 users on one quiet tenant for 5 min.
  Phase B: 4 quiet tenants at 50 users each + 1 noisy tenant at 500 users for
  5 min. Gate (line 153): **no quiet tenant's p99 may regress by more
  than 20%** versus its baseline.
- `tests/load/v2_realistic_burst.py` — burst shape registered as `burst_10k`
  in `tests/load/soak.py:227`: ramp from 100 to 10,000 users over 60
  seconds, hold for 5 minutes, ramp down.

What we have not done: published a soak result for a new buyer's traffic
shape. The 0.5% / 500 ms gate is what the harness enforces today; it is
not yet a contractual SLO.

**Cost.** A buyer at sustained >100 RPS who turns Aegis on cold is the first
observed run at that scale. Blast radius is bounded — per-tenant rate
limits, the per-tenant kill switch in `services/decision/router.py:102`,
and the `acp_behavior_firewall_consult` breaker — but the latency curve
under their workload is unmeasured.

**Trigger.** Before any buyer goes to sustained >100 RPS, run
`python tests/load/soak.py --users <projected-peak> --duration 60m --tenants
<count>` against a clone of their tenant shape and publish the resulting
`reports/soak/{ts}/summary.json`. The script's exit code is the signal.

**What we will not commit.** A p99 number at sustained 500 RPS. We have not
run that workload, and inventing the number to win a deal is how we lose
the renewal.

## Microservice count — why 13 services today, and the consolidation roadmap

`infra/docker-compose.yml` enumerates the runtime: **gateway, identity,
policy, audit, api, usage, decision, behavior, insight, registry, forensics,
identity_graph, flight_recorder, autonomy** — 14 application services, plus
the data-plane and observability stack (postgres, pgbouncer, redis, opa,
bundle-server, ui, prometheus, alertmanager, grafana, jaeger). Each
application service sits on a real boundary:

- **gateway** — the only public-internet listener. Every downstream call
  requires `X-Internal-Secret` plus per-service identity headers set in
  `services/gateway/main.py:174` (`_internal_headers`).
- **identity / audit / api / usage / registry / identity_graph /
  flight_recorder / autonomy** — each owns its own Postgres database with
  a service-scoped role and RLS (see the `DATABASE_URL` blocks at
  `infra/docker-compose.yml` lines 150, 246, 289, 372, 424, 459, 680, 716,
  751). A bug or compromise in one cannot read another's rows.
- **policy / decision** — split because the decision engine carries wider
  blast radius (kill switch, behavior consult, cumulative risk) than the
  policy engine, which only evaluates Rego against an input.
- **behavior** — 4 workers on a hot-path consult
  (`infra/docker-compose.yml:159`). The failure mode is explicitly
  degraded (`services/decision/behavior_consult.py`), not silent bypass.
- **forensics / insight / insight_worker** — read-only paths over
  audit_logs. Split so a heavy report query cannot OOM the audit writer.

**Where the split is premature.**

- **`insight` and `identity_graph`** are both read-only views over
  audit_logs and agent metadata. They ship as separate FastAPI apps for
  blast-radius reasons that don't matter yet at our scale.
- **`autonomy` and `flight_recorder`** could be one service until either
  reaches >50 RPS sustained or develops a divergent deployment cadence.
- **`api` and `registry`** overlap. `api` mints api-keys; `registry` owns
  agent metadata. Their shared CRUD shape suggests merging.

**Consolidation roadmap.**

1. **Sprint M1** — merge `insight` + `identity_graph` into a single
   `views` service. Trigger: doing this gets harder once either reaches
   >20 RPS sustained. Rollback: each route keeps its path so the gateway
   can split traffic back on a single env-var flip.
2. **Sprint M2** — merge `autonomy` + `flight_recorder`. Trigger: on-call
   burden of two services for what is one workflow state machine.
3. **Sprint M3** — merge `api` + `registry`. Higher risk because of the
   distinct database. Only after M1 + M2 are clean wins and we are still
   below 5 engineers.

Non-negotiables: `gateway`, `policy`, `decision`, `audit`, `identity`,
`behavior` stay separate. Each has a security-relevant isolation reason
and an independent deployment cadence.

## Self-governance — who governs Aegis when Aegis is the governance tool

Three layers, all real today.

**Layer 1 — RBAC on policy edits.** `services/policy/router.py:554` defines
`_ALLOWED_ROLES = frozenset({"OWNER", "ADMIN", "SECURITY_ANALYST",
"SECURITY"})`. The `_require_admin_or_security` dependency at line 558
gates `/upload` (line 688), `/test`, and the rest of the policy-mutation
surface. The role is **not** read from the request body — the gateway
injects `X-ACP-Role` via the `Header` dependency at line 559, and
`verify_internal_secret` (line 560) requires the gateway-only secret. A
holder of a non-admin token cannot escalate by sending the header
themselves.

**Layer 2 — append-only audit with public Merkle chain.** The audit log is
hash-chained; daily Merkle roots are signed (ed25519) and linked via
`prev_root_hash` (`services/audit/transparency.py:150`). Each signed
payload commits to the previous day's root, so rewriting yesterday
invalidates every subsequent day's signature — every one of which an
adversary without the root key cannot forge. Roots are mirrored to the
public bucket `s3://aegis-public-roots-628478946931`
(`services/audit/public_transparency.py:39`), anonymously fetchable, so
total compromise of the production database is still publicly detectable
to any customer who archived a prior root. Rotation procedure:
`docs/runbooks/key_rotation.md`. Historical keys stay in
`transparency_historical_keys` so old receipts still verify.

**Layer 3 — kill switch.** Operator of last resort, behind the same RBAC
gate. `services/decision/router.py:102` exposes
`POST /kill-switch/{tenant_id}` requiring `ADMIN` or `SECURITY` role plus
`X-Internal-Secret`. `_assert_authenticated_tenant_matches` (line 76)
rejects cross-tenant kill operations — a malicious tenant admin cannot
black-hole a peer. State is persisted in the `kill_switches` table and
re-hydrated to Redis on container restart (`services/decision/main.py:59`).

**What is honestly not built yet.**

- **Dual-approval for policy edits.** `grep -rIn 'dual.approval\|
  two.approver' services/` returns zero hits. A single OWNER role can
  promote a Rego policy today. Mitigation: every upload writes a
  tamper-evident audit row identifying the actor. Preventive control is
  on the roadmap. Trigger: any customer security review that lists it as
  a non-negotiable.
- **Hardware-backed root key.** The root signing key is a file on the
  `audit_keys` Docker volume (`infra/docker-compose.yml:377`). KMS-rooted
  signing is on the roadmap but not shipped.

## SLOs / MTTR / DR — what we measure and what we'd publish to a buyer

**Latency, measured.** `services/gateway/latency_window.py` is the single
source of truth. `gateway_internal_window` records
request-received → response-sent, surfaced by `/status`
(`services/gateway/main.py:857`). `end_to_end_window` records the
client-visible path including downstream probes, surfaced by
`/system/health` (line 971). Canonical shape: `{scope, window_seconds,
p50_ms, p95_ms, p99_ms, request_count, computed_at}`. The Grafana panel
in `infra/grafana-dashboards/acp-platform-slo.json:12` plots `/execute`
p50/p95/p99 over the SLO histogram. **Target**: `P95LatencyBudgetBreach`
fires at p95 > 400ms for 5m
(`docs/operations/observability.md:128`).

**MTTR.** `acp_incident_mttr_seconds` is the exported metric; `HighMTTR`
warns at >3600s for 5m (`docs/operations/observability.md:133`).
Kill-switch flip is operator-input + Redis SETEX, sub-second on the
engagement side; the 30-second poll loop in
`services/decision/main.py:113` is the upper bound on resynchronisation
across containers if Redis is flushed.

**Backup + DR.**
- `scripts/ops/backup.sh` — `pg_dump → age-encrypt → S3`.
- `scripts/ops/restore_drill.sh` — invoked quarterly per
  `docs/runbooks/restore_drill.md`. Spins up an isolated Postgres on a
  private Docker network, decrypts the backup with `age`, restores, and
  row-count-verifies `audit_logs`, `tenants`, `usage_events`.
- `docs/operations/disaster-recovery.md:18-24` publishes the
  **customer-facing target: RTO 4 h, RPO 15 min**. Engineering capability
  is tighter (≤ 30 min via Multi-AZ failover, ≤ 5 min RPO via WAL
  replication); the customer commitment leaves operational headroom.

**Worst-case response.** `docs/runbooks/audit_chain_violation.md` is the
P0 procedure for `ChainViolationImmediate`
(`acp_audit_chain_violations_total > 0`): pause all writes, identify the
broken link via `acp verify-chain`, scope blast radius, then escalate.

**Not published yet.** A 30-day rolling SLO-attainment number. The
dashboards compute it; the buyer-facing report does not exist. Trigger:
first customer who asks for monthly SLO reporting in writing.

## WAF bypass for authenticated traffic — rationale + monitoring

`infra/terraform/modules/waf/main.tf` is the source of truth for the
AWS-side stack: priority 10 `AWSManagedRulesCommonRuleSet`, priority 20
`AWSManagedRulesKnownBadInputsRuleSet`, priority 30
`AWSManagedRulesSQLiRuleSet`, and a per-IP rate-limit (`block {}`) at
priority 100. All run in **enforce** mode against every request,
including authenticated ones. There is no WAF-layer carve-out for
`/auth/` or `/execute`.

What is in Count mode is the Cloudflare-side Bot Score (configured at the
Cloudflare dashboard, not in this repo) for `/auth/` and `/execute`.
Reason: enterprise SDKs are headless HTTP callers (Python
`aegis-anthropic`, Node, Go) and the bot challenge would 403 them on the
call we want them to make. The trade-off:

| Path                            | Cloudflare Bot Score | AWS WAF | Replacement controls |
|---------------------------------|----------------------|---------|----------------------|
| `/landing`, `/demo`, public UI  | Enforce              | Enforce | None needed beyond bot challenge. |
| `/auth/*`, `/execute`           | Count (log only)     | Enforce | JWT validation + per-tenant quota + per-IP rate-limit + behavior firewall. |

What replaces bot inspection on authenticated paths:

- **JWT validation** — every authenticated route requires a valid token;
  the LRU + Redis JWT cache stops an invalid token before it reaches a
  worker.
- **Per-tenant quota** — three layers (token-bucket rps + UTC-day +
  UTC-month) in `services/gateway/_mw_rate_limit.py:29`
  (`_check_rate_limits`). Returns 429 with `Retry-After`.
- **Per-IP rate-limit** at the WAF — `infra/terraform/modules/waf/main.tf`
  priority 100 (`block`, not `count`).
- **Behavior firewall** — `acp_behavior_firewall_consult_total` plus the
  tenant's degraded-mode policy
  (`block_high_risk` / `block_all` / `allow_with_audit`) in
  `services/decision/behavior_consult.py`.

Audit signal for a hostile authenticated caller:
`services/security/signal_registry.py:260` registers
`behavior_baseline_drift` — MITRE T1078 Valid Accounts, default score 30,
default response `escalate`. Definition: *per-agent rolling baseline
deviation: unusual tool / hour / table / 3σ burst*. Burst on a
stale-but-not-revoked JTI is exactly the shape this fires on.

**Cost.** A bot-net holding real JWTs (e.g. via admin phishing) is not
stopped at the edge — it is stopped at the behavior firewall and the
per-tenant quota. Detection window: one session of anomalous calls before
quarantine engages.

**Trigger to revisit.** Any customer who wants Bot Score in Enforce mode
can opt in via an X-header → WAF custom rule. Default stays Count to keep
their headless SDKs working.

## Team-size honesty — what one engineer can sustain and what we're not building

**Today's state.** The product is the work of one engineer. On-call
burden is small because traffic is small. Hardened paths: gateway,
identity, policy, audit, decision. Under-tested paths: insight,
identity_graph, autonomy, flight_recorder, forensics. By file count,
`services/audit/tests/` has 6 unit-test modules
(`test_crypto_trust`, `test_key_separation`, `test_merkle`, `test_signer`,
`test_transparency_endpoints`, `test_transparency_scheduler`);
`services/policy/tests/`, `services/registry/tests/` have one each;
`services/identity/tests/` has 5; `services/gateway/tests/` has 2. The
repo-level `tests/` directory has 168 modules covering integration and
red-team scenarios. Honest line coverage on the secondary services
(insight, identity_graph, autonomy, flight_recorder) is below 30%.

**On-call burden.** Single-pager rotation, Sev-0/Sev-1 acknowledge 30 min
24/7 per `docs/operations/incident-response.md:22`. What keeps this
sustainable for one engineer is alert discipline: every rule in
`infra/prometheus-rules.yml` points at a runbook in `docs/runbooks/`.
The runbooks that exist today: `key_rotation`, `audit_chain_violation`,
`tenant_data_request`, `restore_drill`.

**Next two hires, in order.**

1. **SRE.** Owns the soak harness, SLO publishing, restore-drill cadence,
   on-call rotation. Trigger: first paid customer in production.
2. **Security researcher.** Owns the signal registry, the red-team
   harness in `services/security/signal_registry.py` + the corpus tests,
   and the responsible-disclosure inbox. Trigger: second paid customer
   or any CVE issued against an integrated SDK.

**Features explicitly NOT being built until customer traction justifies
them.**

- **Dashboard widget library / drag-drop builder.** Dashboards stay
  JSON-provisioned Grafana panels. No no-code widget composer.
- **Marketplace for community-contributed signals or Rego rules.**
  Signals stay in `services/security/signal_registry.py` and ship with
  the platform.
- **Per-region replication beyond Multi-AZ + cross-region S3 for the
  public Merkle bucket.** No active-active multi-region until a customer
  pays for it.
- **In-product SCIM beyond the Clerk-native integration.** SCIM was
  scoped in the post-pentest plan (P-Hard-1 SCIM=B); no other
  identity-provider connectors are on the roadmap.
- **Self-serve compliance attestation generator.** PDF compliance
  exports exist (`services/audit/compliance.py`); SOC2 / ISO27001
  attestations stay a manual controls exercise until we hire a
  compliance lead.

The point of this list is honesty. If a buyer asks for one of these in
year one, the answer is "no" — and the answer is on paper.
