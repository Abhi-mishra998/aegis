# Sprint 25 — "Make It Sellable"

**Single sprint, 14 calendar days, one drop.**
**Goal:** Close every production blocker in `report-bussines-25.md` + every honest gap admitted in `matrix-25.md` in one coordinated push. No iterative "ship one fix at a time." When this sprint ends, the system is pilot-sellable to a non-regulated enterprise design partner.

**Pre-conditions to start the sprint** (must be true on Day 0 morning):
- All API keys leaked in chat or anywhere else are **rotated**.
- A second-environment AWS account is available for live tests (the `aegisagent.in` prod cannot be the test target).
- 14 uninterrupted working days. **No feature requests accepted mid-sprint.**

**Definition of done** (single, unambiguous):
- All 28 tickets below are merged to `main` and tagged `v0.25`.
- A new `reports/sprint-25/` evidence directory contains the artefact for every ticket.
- `aegis-verify --bundle <fresh>` returns V1-V6 PASS on prod data **after** all changes.
- The 14-day soak (real, not the prior 15-second sham) is running with metrics shipped.

---

## How this differs from previous "sprints"

Per `report-bussines-25.md`, the previous 10 `batch/*` branches each shipped a single fix in isolation. That pattern adds entropy: each merge moves on a different axis, regressions slip through, and the codebase grew 100k LoC in 21 days with nothing pruned. Sprint 25 is the opposite:

| Old pattern | Sprint 25 |
|---|---|
| `batch/03-silent-catches-to-toasts` ships in isolation | **All silent-catch fixes ship in one PR** (`fix/silent-exception-sweep`) |
| Voice-agent cleanup over multiple commits | **One coordinated removal across code + infra + docs + EC2** |
| "We'll measure perf after the cleanup" | **Perf evidence is a gate inside the sprint, not after** |
| 25 stale `docs/p-hard-1-unit-*` branches | **Close all stale branches by Day 14 or delete them** |

---

## The 28 tickets

Ordered by dependency. Tickets marked **[BG]** run in the background (long-running tests, drills) — start them early so they're done by Day 14.

### Phase A — Stop the bleeding (Days 1-3)

**A1 — Rotate the leaked API key + ban-in-chat policy** *(30 min)*
- Revoke the API key leaked in chat at https://console.anthropic.com/settings/keys
- Write `docs/security/credential-handling.md` (1 page): never paste creds in chat / issue tracker / Slack. Use 1Password / AWS Secrets Manager / `.env` only.
- Acceptance: leaked key returns 401 on a probe; the doc is linked from `README.md`.
- **Evidence:** `reports/sprint-25/a1-key-rotation.json`

**A2 — Fix the 3 F821 runtime crashes** *(1 hour)*
- `scripts/ops/seed_demo_workspace.py:1081` — add `import hashlib`
- `services/audit/tests/test_transparency_endpoints.py:425,427` — add `from typing import Any`
- Acceptance: `ruff check . --select F821` → 0 errors.
- **Evidence:** `reports/sprint-25/a2-ruff-f821.txt`

**A3 — Purge hardcoded test credentials from shippable paths** *(2 hours)*
- `services/gateway/tests/load/locustfile.py:46, 508-509` — replace `admin@acp.local` / `admin1234` / `postgres:postgres@localhost:5433` with env-var reads that bomb on missing values.
- Search for any other `password = 'admin|test|changeme|devsecret'` patterns and fix.
- Acceptance: grep for `password\s*=\s*['\"]admin|secret\s*=\s*['\"]changeme` across services/sdk returns empty.
- **Evidence:** `reports/sprint-25/a3-creds-purged.txt`

**A4 — Replace `localhost:8000-8015` SDK defaults with fail-fast** *(2 hours)*
- `sdk/common/config.py:172-179` — each of the 9 service URL settings: **remove the localhost default**. Make them `Field(..., description="...")` (required). On startup, validate all 9 are set; bomb with a clear `ConfigError` listing the missing names if not.
- Acceptance: starting the gateway with `REGISTRY_SERVICE_URL` unset fails with a clear error in <2 s, not a silent localhost connection.
- **Evidence:** `reports/sprint-25/a4-config-validate.log`

**A5 — Fix the CRITICAL unguarded `int()` in tenant PATCH** *(2 hours)*
- `services/identity/router.py:2252-2265` — wrap `int(body["rpm_limit"])`, `int(body["requests_per_second"])`, `int(body["burst"])`, `int(body["daily_request_cap"])` in try/except ValueError; return 400 with explicit field name + actual value.
- Add a test that PATCHes each field with `"xyz"` and asserts 400 not 500.
- **Evidence:** `tests/test_tenant_quota_patch.py` + green pytest output

**A6 — Fix the CRITICAL `IndexError` in SCIM bearer extraction** *(1 hour)*
- `services/gateway/_scim_auth.py:36` — guard `auth.split(None, 1)` length; return 401 "malformed Authorization header" if `< 2` parts.
- Add unit test that sends `Authorization: Bearer` (no token) and asserts 401 not 500.
- **Evidence:** `tests/test_scim_auth_edge.py`

**A7 — Fix the CRITICAL fail-open Clerk webhook idempotency** *(4 hours)*
- `services/identity/webhooks_clerk.py:643` — when Redis is unavailable for the replay-id check, **fail-CLOSED with retry-after**, not fail-open. Pattern: return 503 with `Retry-After: 30` so Svix retries; on next attempt, Redis is hopefully back.
- Add a test that monkey-patches Redis to raise ConnectionError and asserts 503, not 200-with-duplicate-user.
- **Evidence:** `tests/test_clerk_webhook_idempotency.py`

**A8 — Fix the CRITICAL split-without-length in risk pipeline** *(1 hour)*
- `services/policy/risk_pipeline.py:192, 200` — replace `int(m.split(":", 2)[2])` with explicit length check. If malformed, log at WARNING and skip the row (don't silently 0-score it).
- **Evidence:** `tests/test_risk_pipeline_malformed.py`

**A9 — Sweep the 7 silent `except Exception: pass`** *(3 hours)*
- For each of the 7 sites (`services/behavior/_baseline.py:114,283,342`, `services/usage/dlq_replay.py:105`, `services/usage/router/fleet.py:190`, `services/security/incidents/recorder.py:273`, `services/audit/playground_router.py:298`) and the 3 rate-limiter ones (`services/gateway/_mw_rate_limit.py:227, 237, 272`), add structured logging (`logger.warning("X_failed", error=str(exc))`) before `pass`.
- **NEVER remove the `pass`** — fail-open is intentional in each site. But silent fail-open is the bug. Logged fail-open is acceptable.
- For the 3 rate-limiter sites, additionally add a Prometheus counter `acp_rate_limiter_redis_failure_total` incremented on each silent path. Alert when > 10/min.
- **Evidence:** `reports/sprint-25/a9-silent-except-sweep.diff` + new Prom rule

**A10 — Spot-verify the 9 MEDIUM-confidence agent-cited findings** *(half day)*
- Read each file:line cited in `report-bussines-25.md` §13 Evidence Matrix as MEDIUM confidence. Confirm or refute.
- For each confirmed: file a ticket inside this sprint and fix during Phase B.
- For each refuted: update `report-bussines-25.md` with the correction.
- **Evidence:** `reports/sprint-25/a10-spot-verify.md`

---

### Phase B — Close the security gaps matrix-25 missed (Days 4-6)

**B1 — Outbound SSRF protection** *(1 day)*
- Add a `sdk/common/outbound_url_allowlist.py` helper that rejects: private CIDRs (10/8, 172.16/12, 192.168/16, 169.254/16, 127/8, ::1, fe80::/10), non-HTTPS unless explicitly allowed, ports outside {80, 443, 8080, 8443}, and DNS rebinding (resolve twice, check both).
- Call it from:
  - `services/audit/siem.py` (SIEM HEC/Datadog/Splunk URLs) — before every outbound request
  - `services/gateway/routers/integrations.py` — before Jira/ServiceNow webhook delivery
  - Any other outbound `httpx.post` against tenant-supplied URLs
- Add metric `acp_outbound_ssrf_blocked_total{service,reason}`.
- Acceptance: a test tenant configuring `SPLUNK_HEC_URL=http://169.254.169.254/...` gets the config-PUT rejected with 400.
- **Evidence:** `tests/test_outbound_ssrf.py`

**B2 — Approval double-execution race** *(half day)*
- Add `UNIQUE(request_id, event_type, tenant_id)` constraint to `HumanOverrideEvent` table.
- Add new alembic migration in `services/autonomy/alembic/versions/`.
- Convert the insert in `services/autonomy/router.py:392` to an upsert (INSERT ON CONFLICT DO NOTHING) and check `rowcount` to decide whether to fire the SSE.
- Test: 50 concurrent POSTs with same request_id → exactly 1 row, exactly 1 SSE fired.
- **Evidence:** `tests/test_approval_no_double_exec.py`

**B3 — Kill-switch TOCTOU fix** *(half day)*
- Re-check `acp:tenant_kill:{tenant_id}` immediately before the final downstream call in `services/gateway/middleware.py` (currently checked early only).
- Test: engage kill-switch mid-flight on a long `/execute` (artificially slowed via debug latency injection) and assert the request returns 403 instead of completing.
- **Evidence:** `tests/test_kill_switch_toctou.py`

**B4 — Stored XSS protection in audit viewer** *(half day)*
- Audit `ui/src/pages/AuditLogs.jsx` + every component that renders `audit_row.reason` / `audit_row.metadata_json`. Confirm they use safe React text rendering (auto-escaped JSX text nodes), NOT any raw-HTML injection prop. Replace any raw-HTML prop usage with text rendering or a sanitizer (DOMPurify).
- Add a CSP header check via a new Playwright test that opens the audit page with a poisoned reason containing an `onerror` attack vector and asserts no script execution.
- **Evidence:** `ui/tests/test_audit_xss.spec.ts`

**B5 — HMAC timing-attack fix** *(15 min)*
- `services/gateway/routers/itsm_webhooks.py` — replace `==` HMAC compare with `hmac.compare_digest()`.
- **Evidence:** the diff is the evidence.

**B6 — JWT `aud` validation** *(2 hours)*
- `services/identity/oidc.py:150+` `verify_id_token` — explicitly validate `aud` against the configured Aegis OIDC client_id. Reject if mismatch.
- Test: present a valid Google OIDC token for a different app + matching email → 401.
- **Evidence:** `tests/test_jwt_aud_validation.py`

**B7 — OIDC state robust unpack + open-redirect close** *(2 hours)*
- `services/identity/oidc.py:350-354` — replace `provider, tenant_id, ts, sig = parts` with explicit length check; 401 on malformed.
- After verifying state signature, restrict post-callback redirect to a hard-coded allowlist (`/dashboard`, `/sso/complete`); reject any other target.
- **Evidence:** `tests/test_oidc_state_robust.py`

**B8 — API key prefix removal** *(half day)*
- `services/api/repository/api_key.py:28-30` — drop the `key_prefix` column from `api_keys`; derive display suffix from `key_hash[:8]` at read time.
- Alembic migration to drop column.
- Backfill: emit a deprecation warning for 30 days, then drop.
- **Evidence:** migration file + `tests/test_api_key_no_prefix.py`

**B9 — Mass-assignment audit on `POST /agents`, `POST /policies`** *(2 hours)*
- Confirm every Pydantic schema used for these endpoints has `model_config = ConfigDict(extra="forbid")`.
- Fix any that don't.
- Test: POST with extra `is_admin: true` → 422.
- **Evidence:** `tests/test_mass_assignment.py`

---

### Phase C — SRE gates (Days 4-7, parallel with Phase B)

**C1 — Per-tenant LLM cost cap** *(1 day)*
- Add `tenant_daily_inference_cost_cap_usd` column on `acp_identity.tenants` (default $50/day).
- In `services/gateway/middleware.py` cost-cap check: aggregate per-tenant first (across all agents), then per-agent. Block if either trips.
- New Redis key: `acp:tenant_cost_today:{tenant_id}:{YMD}`.
- Add `/tenant/quota` endpoint to expose the aggregate.
- Test: 5 agents × 5 inferences each → cap fires when tenant aggregate hits $50.
- **Evidence:** `tests/test_per_tenant_cost_cap.py`

**C2 — Graceful shutdown for in-flight `/execute`** *(half day)*
- Add `stop_grace_period: 30s` to every service in `infra/docker-compose.prod.yml`.
- Add a FastAPI lifespan shutdown hook in `services/gateway/main.py` that:
  1. Stops accepting new requests (return 503 from a health flag)
  2. Waits for the `MAX_CONCURRENT_EXECUTION` semaphore to drain (up to 25s)
  3. Then signals uvicorn to exit
- Test: spawn 5 long `/execute` requests, send SIGTERM, verify all 5 complete and write audit rows.
- **Evidence:** `tests/test_graceful_shutdown.py` + manual evidence

**C3 — `/health` deep-probe** *(2 hours)*
- `/health` currently returns 200 if FastAPI is up. Change it to internally call `/system/health` with a 1s timeout and return 503 if ANY downstream is unreachable.
- Update `infra/docker-compose.prod.yml` ELB/ASG health checks to keep using `/health` (they don't need to change — `/health` now means what they assumed it meant).
- **Evidence:** `tests/test_health_deep_probe.py`

**C4 — Restore drill: actually run it** *(1 day)*
- The 3 drills in `reports/restore_drill/` are all DRY_RUN (`status: "DRY_RUN"`, `verdict: "skipped (dry_run)"`). Run a **real** restore against a fresh test database from yesterday's prod backup.
- Document RPO + RTO with measured numbers in `reports/sprint-25/c4-restore-drill-real.json`.
- Schedule `scripts/ops/restore_drill.sh` as a weekly cron in CI; alert on any failure.
- **Evidence:** `reports/sprint-25/c4-restore-drill-real.json` (status: PASS, duration_seconds: N, restored_databases: 8, audit_chain_verifies: true)

**C5 — Coordinated migration ordering** *(half day)*
- `infra/docker-compose.prod.yml`: each `migrate_X` job declares explicit `depends_on: [migrate_<predecessor>]` chains. The chain order is `identity → registry → api → autonomy → audit → usage → identity_graph → flight_recorder → policy`.
- Add a CI test that runs all migrations from scratch on a fresh Postgres and asserts no errors.
- **Evidence:** `.github/workflows/migration-order-check.yml` (or your CI equivalent)

**C6 — DLQ permanently-failed viewer endpoint** *(half day)*
- Add `GET /system/dlq/audit/permanently-failed` and `GET /system/dlq/billing/permanently-failed` — admin-only, paginated.
- Add Prometheus rule `BillingPermanentlyFailedGrowing` (mirrors existing `OutboxPoisonGrowing`).
- Add a tile to the Dashboard UI showing both counts.
- **Evidence:** new Prom rule + UI screenshot

**C7 — Multi-region DR statement** *(half day)*
- Write `docs/operations/dr.md`: either (a) the documented multi-region failover runbook with measured RTO/RPO, OR (b) an explicit statement "Aegis is single-region (ap-south-1) by design; pilots are scoped to non-regulated workloads."
- If (b), add a `region_strategy: single` field to `docs/README.md` so a buyer can't be surprised.
- **Evidence:** `docs/operations/dr.md`

**C8 [BG] — Real 24h soak** *(start Day 4, finish Day 7)*
- The "soak" in `reports/soak/20260515T155819Z/` was 15 seconds with all 4 post-checks failing on auth (401 because the harness never authenticated). Replace it.
- Use the existing `tests/load/soak.py` harness; fix the auth bug so the chain-verify / reconciliation / flight / transparency checks actually run.
- Run for 24h against a staging environment (not prod). Measure: error rate over time, memory growth per service, Redis memory growth, queue oldest-age growth, chain-verify pass at end.
- **Evidence:** `reports/sprint-25/c8-soak-24h.json` with all 4 post-checks PASSED.

**C9 [BG] — Multi-IP load test** *(start Day 5, finish Day 7)*
- The 2026-Q3 load-test templates in `reports/load-test-2026-Q3/` were never executed (only README + execution-guide markdown files exist, no results). Execute them.
- Run k6 (or locust on a fleet) from at least 10 distinct source IPs at 100 RPS per IP for 30 minutes against staging. Measure: `/execute` p99, gateway internal latency, ALB 5xx rate, Postgres connection saturation, Redis ops/sec.
- Acceptance: `/execute` p99 < 1500 ms across 1k RPS sustained. Current evidence from `reports/e2e_test_2026_06_20/load.json` shows p99=3174ms at just 25 users × 12 reqs — **this must be improved**.
- **Evidence:** `reports/sprint-25/c9-multi-ip-load.json` + `reports/load-test-2026-Q3/1k-rps-results.md` (replacing the template).

---

### Phase D — Product cuts (Days 8-10)

**D1 — UI: archive 48 of 57 pages** *(1.5 days)*
- Keep only the 9 pages a buyer would tour: `Login.jsx`, `Dashboard.jsx`, `AuditLogs.jsx`, `Policies.jsx`, `ApprovalInbox.jsx`, `Compliance.jsx`, `Settings.jsx`, `Agents.jsx`, `TrustCenter.jsx`.
- `git mv` the other 48 to `ui/src/pages/_archived/`. Update `App.jsx` router. Remove `Sidebar.jsx` nav items.
- For each archived page, verify the backend route it consumed is either also unused or proxied for SDK callers only.
- Run `npm run build` — must succeed.
- **Evidence:** `ls ui/src/pages/_archived/ | wc -l` ≥ 48; `ls ui/src/pages/*.jsx | wc -l` ≤ 10; build log green.

**D2 — SDK: code-freeze 3 of 4 SDKs** *(half day)*
- Decide your hero SDK (recommend `aegis-anthropic` — your existing matrix-25 corpus is anthropic-side).
- For `aegis-openai`, `aegis-langchain`, `aegis-bedrock`: pin to current PyPI version, add a deprecation notice in each repo's README, freeze further releases until post-revenue.
- **Evidence:** 3 PyPI release notes + 3 repo READMEs updated.

**D3 — Pick a vertical, rewrite the landing page** *(1 day)*
- Decide healthcare OR banking OR developer-tools. Don't pick all. (Recommendation per `report-bussines-25.md`: developer-tools first, healthcare in year 2.)
- Rewrite `ui/src/pages/Landing.jsx` (hero copy) + `docs/README.md` (positioning paragraph) for that one ICP.
- Drop the "16 services, 49 UI pages" framing wherever it appears.
- **Evidence:** before/after screenshots committed to `reports/sprint-25/d3-positioning/`.

**D4 — God file: extract `middleware.py` body parser** *(1 day)*
- `services/gateway/middleware.py` lines ~902-1715 (the 800-line canonical body parser) → new file `services/gateway/_mw_body_parser.py` with one entry `build_tool_metadata(request, raw_body, agent_id, t_id_str, tool_name, request_id) -> dict`.
- `middleware.py` calls it as one line.
- Test that all existing pytest passes unchanged.
- **Evidence:** `wc -l services/gateway/middleware.py` < 2,400 (was 3,084); all tests green.

---

### Phase E — Evidence, docs, deploy (Days 11-13)

**E1 — Install + wire static analysis in CI** *(half day)*
- Install: `bandit`, `mypy`, `pip-audit`, `detect-secrets` (locally + in CI).
- Add a pre-merge job: `ruff --select F,E,W,B,SIM,UP --statistics` + `bandit -r services sdk` + `pip-audit` + `detect-secrets scan --baseline .secrets.baseline`.
- Baseline existing findings into `.secrets.baseline` so CI fails only on **new** findings.
- **Evidence:** `.github/workflows/static-analysis.yml` (or your CI equivalent)

**E2 — Adversarial corpus from a non-Claude model** *(1 day)*
- Re-run the matrix-25 1,000-scenario corpus using OpenAI GPT-4o (or Gemini if you prefer) as the attacker.
- Same 10 categories, same 5 tool schemas. Track block / refuse / allow per category.
- Publish results next to `F-llm-redteam-1000-summary.json` as `F-llm-redteam-1000-gpt4o-summary.json`.
- Acceptance: ≥ 95 % attack interception across both attackers (Claude + GPT-4o).
- **Evidence:** `reports/sprint-25/e2-redteam-gpt4o.json`

**E3 — Re-run `aegis-verify` post-changes** *(1 hour)*
- After Phase B database migrations, generate a fresh compliance bundle.
- Run `aegis-verify --bundle <new>` and assert V1-V6 PASS.
- **Evidence:** `reports/sprint-25/e3-aegis-verify.json`

**E4 — Reproduce matrix-25 27-probe matrix on the post-change system** *(half day)*
- Run `python3 /tmp/aegis-qa-evidence/load/security_probes.py` against the post-deploy URL.
- Acceptance: all 27 still pass (no regression from the security fixes).
- **Evidence:** `reports/sprint-25/e4-probe-rerun.json`

**E5 — Update `report-bussines-25.md` evidence matrix** *(half day)*
- For each row in §13 that was MEDIUM confidence and now spot-verified (per A10): update to HIGH or refute.
- For each row that was UNVERIFIED and is now measured (multi-IP load, soak, restore drill): update.
- **Evidence:** `report-bussines-25.md` updated; diff committed.

**E6 — Branch hygiene** *(2 hours)*
- Close or delete the ~20 in-flight branches (`docs/p-hard-1-unit-{1..15}`, the `batch/*` branches that already merged, `audit/public-surface-2026-06-21`).
- Acceptance: `git branch -a | wc -l` ≤ 5.
- **Evidence:** `git branch -a` snapshot before & after.

**E7 — Tag v0.25 + deploy** *(half day)*
- `git tag -a v0.25 -m "Sprint 25 — production-pilot gates closed"`.
- Run `safe_deploy.sh` to prod-ha. Verify `/transparency/keys` returns the same key (no key rotation in this sprint to keep the change set tight).
- Run a fresh `aegis-verify --bundle` against prod.
- **Evidence:** `reports/sprint-25/e7-deploy.log` + post-deploy `aegis-verify` PASS.

---

## What Sprint 25 **does NOT** do (and why)

These are out of scope. They are real work but they belong to a later sprint or to a different team.

- **SOC 2 Type I audit kickoff** — needs auditor selection ($40-80K spend); not a code change.
- **Multi-region active-active failover** — multi-month infra rebuild; if Phase C7 picks (b), it's deferred.
- **Replacement of the 17-service architecture** — needs a product cut backed by design-partner feedback. Phase D narrows the front of the funnel; back-end consolidation is sprint 26+.
- **Real penetration test by a third-party firm** — booked separately; the codebase has to first close Phase A + B before paying for human attackers.
- **Rewrite of the 800-line body parser** — D4 extracts it; refactoring it further requires test coverage we don't have yet.

---

## Acceptance gates at end of sprint

The sprint is done only when **all** of these are true:

| Gate | Evidence file |
|---|---|
| All 28 tickets merged to `main` and tagged `v0.25` | `git log v0.25` |
| `ruff check --select F821` returns 0 | `reports/sprint-25/a2-ruff-f821.txt` |
| `bandit -r services sdk` HIGH count ≤ 3 (matrix-25 baseline) | `reports/sprint-25/e1-bandit.json` |
| `mypy --strict services/gateway services/audit` runs (does not require 0 errors yet, just runs) | `reports/sprint-25/e1-mypy.txt` |
| `detect-secrets scan --baseline .secrets.baseline` returns no NEW findings | CI green |
| Real 24h soak passed with 4/4 post-checks | `reports/sprint-25/c8-soak-24h.json` |
| Real multi-IP load test passed, `/execute` p99 < 1500 ms | `reports/sprint-25/c9-multi-ip-load.json` |
| Real restore drill passed with measured RPO/RTO | `reports/sprint-25/c4-restore-drill-real.json` |
| Re-run matrix-25 27-probe matrix: 27/27 still PASS | `reports/sprint-25/e4-probe-rerun.json` |
| GPT-4o adversarial corpus: ≥ 95 % interception | `reports/sprint-25/e2-redteam-gpt4o.json` |
| `aegis-verify` V1-V6 PASS on a fresh post-deploy bundle | `reports/sprint-25/e7-aegis-verify-final.json` |
| `ui/src/pages/*.jsx` count ≤ 10 | `ls ui/src/pages/*.jsx \| wc -l` |
| `git branch -a` count ≤ 5 | `git branch -a` |
| `docs/operations/dr.md` exists with explicit strategy statement | `docs/operations/dr.md` |
| All test files for Phase A & B exist and are green | pytest output |

If any gate fails, **the sprint is not done**. No partial credit.

---

## Day-by-day calendar (for a solo developer)

| Day | Foreground work | Background work |
|---|---|---|
| **Day 0** | Pre-sprint: rotate keys, set up second-env AWS, install local static-analysis tools, start `report-bussines-25.md` spot-verify | — |
| **Day 1** | A1, A2, A3, A4 | — |
| **Day 2** | A5, A6, A7, A8 | — |
| **Day 3** | A9, A10 spot-verify; commit Phase A as `sprint-25/phase-a` branch | — |
| **Day 4** | B1 (SSRF), C1 (per-tenant cap) | **C8 soak start** |
| **Day 5** | B2 (approval race), C2 (graceful shutdown) | **C9 multi-IP load start**, C8 running |
| **Day 6** | B3, B4, B5 | C9 running, C8 running |
| **Day 7** | B6, B7, B8, B9 | **C8 finishes**, **C9 finishes** |
| **Day 8** | C3, C4 (real restore drill), C5 | — |
| **Day 9** | C6, C7, D1 (UI archive part 1) | — |
| **Day 10** | D1 (UI archive part 2), D2, D3 | — |
| **Day 11** | D4 (god-file extract), E1 (CI wire-up) | **E2 GPT-4o corpus start** |
| **Day 12** | E2 finalize, E3 aegis-verify, E4 probe rerun | — |
| **Day 13** | E5 evidence matrix, E6 branch hygiene | — |
| **Day 14** | E7 deploy + tag v0.25; smoke; declare done | — |

If a Phase A item slips past Day 3, **stop and reschedule the sprint** — the dependencies are tight and the background tests need 3 full days.

---

## Risk register for the sprint itself

| Risk | Likelihood | Mitigation |
|---|---|---|
| 24h soak finds memory leak → blocks v0.25 | MEDIUM | Soak runs Days 4-7; if it fails, Days 8-10 are spent fixing (Phase D becomes sprint 26) |
| Multi-IP load finds the WAF / burst limiter is mis-tuned and blocks legitimate traffic | HIGH | Tune WAF rules in the same sprint; budget Day 7 buffer. Real evidence from `latency-2026-06-14.json` already shows 95 % error rate at 100 concurrency from one IP — this is a real risk, not theoretical. |
| Real restore drill fails on encryption key mismatch (age key rotated since last backup) | MEDIUM | Test the drill on Day 8 morning; if key is stale, rotate before continuing |
| The 9 spot-verifies in A10 surface NEW criticals not in `report-bussines-25.md` | MEDIUM | Each new critical adds a Phase A ticket; sprint extends by up to 2 days |
| Solo dev gets pulled off mid-sprint | HIGH | **The sprint requires 14 uninterrupted days; if that's not possible, do not start it.** Half a sprint is worse than no sprint. |
| Background test runs against staging conflict with someone else's staging use | LOW | Spin up isolated soak/load env in a fresh VPC; document in `docs/operations/staging-conventions.md` |

---

## What "sellable" means after Sprint 25

By the end of Day 14, the following sales claims become **defensible** (i.e., a buyer can verify each one with the cited evidence):

1. "We've run a real 24-hour soak with measured memory + latency curves." → `reports/sprint-25/c8-soak-24h.json`
2. "We've tested at 1k RPS multi-IP." → `reports/sprint-25/c9-multi-ip-load.json`
3. "We've executed a real restore drill with measured RPO/RTO." → `reports/sprint-25/c4-restore-drill-real.json`
4. "Our adversarial coverage is validated against two different attacker models (Claude + GPT-4o)." → `F-llm-redteam-1000-summary.json` + `e2-redteam-gpt4o.json`
5. "Our cryptographic chain still verifies after a deploy." → `e7-aegis-verify-final.json`
6. "Our boundary security probes still pass after the security fixes." → `e4-probe-rerun.json`
7. "Our static analysis is wired into CI; the build fails on new bandit HIGH or new secret detection." → `.github/workflows/static-analysis.yml`
8. "We're focused on one vertical, with positioning to match." → `docs/README.md`
9. "We've cut the UI surface from 57 pages to 9 product pages." → `ui/src/pages/`
10. "We have explicit DR posture documented." → `docs/operations/dr.md`

Each of these moves the buyer's "is this real?" question from a sales claim to a code commit.

---

## Per-persona sign-off after the sprint

By Day 14, the verdict shifts:

| Persona | Pre-Sprint 25 verdict | Post-Sprint 25 expected verdict |
|---|---|---|
| CTO | CONDITIONAL GO with 18 gates | GO for non-regulated pilot |
| CISO | NO GO for regulated prod | CONDITIONAL GO with SOC 2 in progress |
| SRE | NO GO for 24×7 unattended | CONDITIONAL GO with pager rotation |
| Security | CONDITIONAL GO with 18 gates | GO for non-regulated pilot |
| Investor DD | CONDITIONAL YES at seed, need LOIs | YES at seed, ready for first LOI conversations |

The CISO **GO for regulated prod** still requires SOC 2 Type I (~$40-80K, 60-90 days) and a HIPAA BAA template — out of scope for this sprint, blocked behind it.

---

## Out of scope items that the next sprint (Sprint 26) MUST cover

These are deliberately deferred so Sprint 25 stays tight:

1. **SOC 2 Type I auditor selection + readiness gap** — 30 days
2. **HIPAA BAA template + first design-partner LOI** — parallel with auditor work
3. **Bug bounty program** — $500-$2000 per CRITICAL, launched on HackerOne or Intigriti
4. **First design partner onboarded** — using the post-Sprint-25 system
5. **Public benchmark vs Lakera / Patronus / NeMo / Llama Guard** — published on OWASP LLM Top 10 corpus
6. **`/v1/messages` cross-tenant fuzz with rotated API keys** — needs the API-key-rotation hygiene from this sprint
7. **The 9 MEDIUM-confidence findings in `report-bussines-25.md` §13 that get downgraded by A10** — already corrected; mention here so we don't accidentally re-ticket them
8. **`mypy --strict` clean pass on `services/gateway`** — Sprint 25 only requires it to RUN; Sprint 26 fixes findings

---

## Final note on `/security-review`

The `/security-review` skill was not invoked on the session's existing diff because the diff is mostly **deletes** (voice cleanup, orphan service exports, JSON-alias redundancy). The real security findings are codebase-wide and already captured in `report-bussines-25.md` §4. After Sprint 25's Phase A merges, invoking `/security-review` on the Phase A branch is the correct point — it will review the actual security-touching changes (fail-open Clerk fix, unguarded int(), SCIM bearer crash) for regressions before merging to `main`.

---

*Sprint 25 owner: <single named person — solo dev = you>.
Sprint 25 reviewer (every PR): <a second pair of eyes — recommended even if a contractor for 14 days>.
Sprint 25 sign-off: yourself + 1 customer-facing person (designer / sales / advisor).*

**Don't start this sprint without all three names filled in.** A sprint with no reviewer is a sprint that ships its own bugs.
