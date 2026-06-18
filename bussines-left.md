# Aegis Business-Doc Audit — what's true, what's wrong, what's unverified

**Source under audit:** `agies-bussiness.md` v1.1.0 (June 2026)
**Audited:** 2026-06-18 against this branch + live prod + public artifacts
**Auditor:** code review + 3 runtime probes (HTTPS curl, S3 list, PyPI metadata)

> **CLOSURE LEDGER (sprint v2.0 in progress — see `sprint.md`):**
> - **B1 — RESOLVED 2026-06-18 (commits `943d83c` + `cad227c`).** Wire-transfer
>   enforcement aligned at $100k across `services/policy/local_action_semantics.py:101`,
>   `services/security/objectives/impact.py:28`,
>   `services/policy/policies/action_semantics_deny.rego:501`,
>   `services/security/signal_registry.py:456`. Regression guard at
>   `tests/policy/test_wire_threshold.py` (10 cases, includes $150k external
>   gap-closure assertion). $100k–$199k routing gap closed end-to-end.
> - **B2 — SOURCE-PREP RESOLVED 2026-06-18 (commit `25b2810`).** `aegis-aevf`
>   source `__version__` bumped from `1.0.0` to `1.1.0` at
>   `tools/aegis_verify/__init__.py:16` and `tools/aegis_verify/pyproject.toml:7`;
>   `tools/aegis_verify/CHANGELOG.md` added with 1.0.0 + 1.1.0 entries
>   (version-sync release, no functional changes, AEVF spec unchanged at
>   `aevf/0.1.0`). PyPI publish itself remains gated on Track B1 release-engineer
>   ops (`python -m build` + `twine upload`).
> - **B3 — ALREADY RESOLVED IN v1.3.0.** Doc no longer claims "50k rows"; line 187
>   reads "Bulk PII export: deny tier ≥ 10k rows (risk-level-dependent: critical=0,
>   high=100, medium=1k, low=10k)" with citation to `local_action_semantics.py:81-86`.
> - **B4 — RESOLVED IN SOURCE 2026-06-18 (commit `ec84e22`).** `aegis-bedrock.__version__`
>   and `aegis-langchain.__version__` bumped to `1.1.0`. PyPI re-publish as 1.1.1
>   pending (sprint Track B2/B3 — release-engineer ops).
> - **B5 — ALREADY RESOLVED IN v1.3.0.** §3 point 8 reconciled per L1 to cite the
>   only measured number (21.49ms p95 synthetic dry-run) and defer production
>   numbers to sprint Track D. Line 418 carries a behavioral rule against citing
>   "27ms" or "150ms" as a production SLA.
> - **B6 — ALREADY RESOLVED IN v1.3.0.** Line 270 reads "Advanced surfaces (15 total,
>   per `ui/src/components/Layout/Sidebar.jsx:62-78`...)" with an explicit
>   "earlier versions said 16 — code count is 15" parenthetical.
> - **B7 — ALREADY RESOLVED IN v1.3.0.** Lines 251–255 carry a "Pricing note: Dollar
>   amounts live in Stripe Price IDs (`STRIPE_PRO_PRICE_ID`,
>   `STRIPE_ENTERPRISE_PRICE_ID` env vars) — figures reflect current Stripe
>   dashboard configuration. State as 'subject to current pricing' when sending
>   to a VC or enterprise buyer."
> - **B8 — ALREADY RESOLVED IN v1.3.0.** Line 208 reads "Tenant-isolated at the
>   policy-bundle layer (per-tenant OPA bundle paths + `X-Tenant-ID` header)"
>   instead of the previous imprecise "tenant-isolated Rego policies".
> - **B9 — ALREADY RESOLVED IN v1.3.0.** Lines 54 and 285 both state "A DBA with full
>   RDS credentials cannot mutate a row without dropping the trigger first — that
>   DDL is itself an audited event. This is storage-layer immutability, not
>   application-layer immutability." Superuser caveat is disclosed.
> - **B10 — ALREADY RESOLVED IN v1.3.0.** Line 59 states "**Public verification
>   path is `aws s3 cp` + `aegis-verify` CLI, NOT any `aegisagent.in/transparency/*`
>   endpoint** (those are JWT-gated UI routes)."
> - **L1 / L2 / L3 — RESOLVED 2026-06-18 (commit `3c26088`).** `agies-bussiness.md`
>   published as v1.3.0 with §3 latency reconciliation, S3 public-witness live
>   evidence added, `/status` JSON sample added.
> - **All B-items resolved in code, source, or doc.** Only deferred items are the
>   PyPI publishes (B1/B2/B3 of `sprint.md` §5) which are release-engineer ops.

---

## TL;DR

The biz doc is **mostly accurate but has 3 hard inaccuracies and 5 soft mismatches** you need to fix before it leaves the building. The platform's strongest claims — append-only audit, multi-tenant isolation, public transparency chain, MITRE-tagged signal coverage — **are real and currently operating in prod**. The numbers around SDK versions, performance, and a couple of detection thresholds are sloppy.

**Score recount (corrected from my earlier draft):**

| Verdict | Count | Meaning |
|---|---|---|
| ✅ Verified by code AND runtime | 9 | Greppable + I hit the actual prod endpoint or fetched the artifact |
| ✅ Verified by code only | ~21 | Greppable file:line, prod state not independently confirmed |
| ⚠️ Partial / soft mismatch | 7 | Code disagrees with doc in a fixable way |
| ❌ Refuted by evidence | 3 | Doc claim is wrong; code or PyPI or S3 disagrees |
| ❓ Unverifiable from outside | ~8 | Latency SLOs, deployed state, attribution claims |

---

## Methodology

### Code review (4 parallel Explore agents)
Cross-checked 57 specific claims in `agies-bussiness.md` against file:line in `/Users/abhishekmishra/mcp-security-controller/acp/` on branch `main`.

### Runtime probes

1. **HTTPS curl against `aegisagent.in`** — checked `/status`, `/api/health`, root, `/transparency/keys`, and `ha.aegisagent.in`
2. **`aws s3 ls --no-sign-request s3://aegis-public-roots-628478946931`** — confirmed bucket exists, object count, fetched one root JSON
3. **PyPI metadata** for `aegis-anthropic`, `aegis-openai`, `aegis-bedrock`, `aegis-langchain`, `aegis-aevf`

### What I did NOT do (still unverified after this audit)

- Did not run a live test against `/v1/messages` with a real API key — I can't tell from outside if a denied tool call returns `_aegis_blocked` in production right now.
- Did not measure actual decision latency — only have the dry-run synthetic `reports/gateway_p95_dry.json`.
- Did not confirm `aws s3 ls` returns the same content from inside a regulated network (the public S3 might be region-blocked for some auditors).
- Did not verify Clerk JWKS rotation actually rotates on schedule in prod.
- Did not run `aegis-verify` against a real bundle to confirm signature validation works.
- Did not verify Stripe is actually processing test/live charges at the claimed price points.

---

## Bugs / inaccuracies found (sorted by severity)

### 🔴 HIGH — fix before next CISO meeting

#### B1. Wire-transfer enforcement has a real $100k–$200k gap
**Doc says (§4):** Path A "Wire transfer ≥ $200k → ESCALATE → CFO". Path B "Wire transfer > $100k → 202 → CFO approval".

**Code says:**
- Pattern detector: `services/gateway/escalation_patterns.py:39-52` fires at **$100k+**
- Signal registry: `services/security/signal_registry.py:451-457` labels at **$200K+**
- Rego enforcement: `services/policy/policies/action_semantics_deny.rego:495-500` checks `>= $200_000`
- Constant: `services/policy/local_action_semantics.py:98` `_WIRE_ESCALATE_EXTERNAL_USD = 200_000`

**Reality:** A wire transfer of $150k to an external destination will **match the prompt pattern (gateway returns 202 + CFO approver_role)** but **will not be flagged by Rego enforcement** (which only fires at $200k). The two layers disagree.

**Fix options:** raise the pattern threshold to $200k (so it matches Rego), OR drop the Rego floor to $100k (so enforcement matches detection). The pattern-only path means an $X-$200k wire gets queued for CFO approval via pattern match — which is probably the *intended* behaviour. Then the doc should describe a 2-stage ladder: pattern-based escalation at $100k, hard enforcement at $200k. **Update the doc to describe this explicitly** instead of presenting it as a contradiction.

---

#### B2. `aegis-aevf` is v1.0.0 on PyPI — doc claims 1.1.0
**Doc says (§12):** "SDK version: 1.1.0 (PyPI: aegis-anthropic, aegis-openai, aegis-bedrock, aegis-langchain) … Verifier: pip install aegis-aevf"

**PyPI confirms:**
```
aegis-aevf  → version 1.0.0 (only release on PyPI)
aegis-anthropic → 1.1.0 ✓
aegis-openai    → 1.1.0 ✓
aegis-bedrock   → 1.1.0 ✓
aegis-langchain → 1.1.0 ✓
```

The 4 wrappers are correctly at 1.1.0 on PyPI. **`aegis-aevf` is 1.0.0.** The biz doc implies all SDK packages live at the same version line. Any CISO who runs `pip show aegis-aevf` after their team installs it will catch this immediately.

**Fix:** Either publish `aegis-aevf` 1.1.0, or in the doc say "Verifier: pip install aegis-aevf==1.0.0" explicitly. Don't pretend it's at the same version as the wrappers.

---

#### B3. Bulk PII deny floor is 10k rows, not 50k
**Doc says (§4):** "Bulk PII export > 50k rows (email/SSN-shaped columns) → DENY"

**Code says:**
- `services/security/signal_registry.py:271-285` defines two signals:
  - `bulk_pii_egress_above_threshold` → escalate
  - `bulk_pii_egress_dump` → **deny, fires at ≥10k rows** (per-risk-level: low=10k, medium=1k, high=100, critical=0)
- `services/policy/local_action_semantics.py:81-86` is the per-risk threshold table
- `services/policy/policies/action_semantics_deny.rego:335-351` enforces

**Reality:** 10k rows is the deny floor for low-risk tenants; lower for higher-risk tenants. The "50k" number in the doc is **not in the code anywhere**.

**Fix:** Either (a) change the doc to "Bulk PII export ≥ 10k rows (or lower depending on tenant risk tier) → DENY" or (b) change the code to actually use a 50k floor and document the tier-based logic separately.

---

### 🟡 MEDIUM — fix when you update the doc

#### B4. `aegis-bedrock` and `aegis-langchain` source `__version__` lags PyPI
**Code says:**
- `integrations/aegis-bedrock/aegis_bedrock/__init__.py:34` → `__version__ = "1.0.0"`
- `integrations/aegis-langchain/aegis_langchain/__init__.py:26` → `__version__ = "1.0.1"`

**PyPI says:** both packages published at 1.1.0.

So the published wheel is 1.1.0 but inside it the `__version__` string still says 1.0.0 / 1.0.1. Not user-facing harmful (the wrappers work) but anyone reading `aegis_bedrock.__version__` after `pip install` will see a stale value.

**Fix:** bump the in-source `__version__` strings to 1.1.0 in both packages. Release-hygiene cleanup, not a doc issue.

---

#### B5. Decision-latency numbers in doc contradict each other
**§3 says:** "Decision latency: ~150 ms p95 (stated; consistent with live SSE observation)"
**§12 says:** "Decision latency: ~27 ms p95 (stated), < 200 ms to SSE UI"

These are the *same metric* expressed 5.5× apart. The only published number anywhere is `reports/gateway_p95_dry.json` p95 ≈ 21.49ms — and that's a **dry-run synthetic** benchmark on a single m6g.medium at 4 concurrency, not a production load test.

**Fix:** pick one number, label it clearly as "dry-run synthetic, single host" or "stated SLO, not yet measured under load". Don't quote both.

---

#### B6. "16 advanced surfaces" — code has 15, doc enumerates 13
**§8 says:** "Advanced surfaces (16 total)" then lists: Audit Logs, Forensics, Observability, Threat Graph + MITRE ATT&CK coverage, Identity Graph, Auto-Response, Evaluation, Playbooks, Shadow Mode, Flight Recorder, Decision Explorer, Session Explorer, Fleet — that's 13.

**Code says:** `ui/src/components/Layout/Sidebar.jsx:62-78` `advancedNav` array contains 15 items (after the U6 merge that removed Observability + SecurityDashboard, the count dropped from 17 to 15).

**Fix:** state the actual count (15), enumerate all 15, OR just say "15+ advanced operator surfaces" if exact count keeps drifting.

---

#### B7. Pricing dollar amounts ($499 / $4,999) are not in the code
**§7 says:** "Pro $499 / month, Enterprise $4,999 / month"

**Code says:** `services/gateway/routers/billing.py:41-45` references `STRIPE_PRO_PRICE_ID` and `STRIPE_ENTERPRISE_PRICE_ID` as **env vars**. The dollar amounts live in the Stripe dashboard, not in source.

**Fix:** doc should say "Pricing controlled in the Stripe dashboard at the price-IDs referenced by `STRIPE_PRO_PRICE_ID` / `STRIPE_ENTERPRISE_PRICE_ID`. Current tier list: Free / Pro / Enterprise." The dollar amounts can be quoted if you confirm what's actually live in the Stripe dashboard — but they're not "in the code".

---

### 🟢 LOW — phrasing tweaks

#### B8. "Tenant-isolated Rego policies" is technically true but misleading
**Doc says (§4):** "Custom policies: Rego language under Protect → Policies. Tenant-isolated, version-controlled."

**Code says:** Rego files at `services/policy/policies/*.rego` don't have `input.tenant_id` checks inside them. Tenant isolation comes from upstream — gateway adds `X-Tenant-ID`, OPA bundles are loaded into per-tenant paths `/tmp/acp_policies/{tenant_id}/`, and OPA runs with tenant-scoped data. Defense-in-depth.

**Fix:** say "tenant-isolated at the policy-bundle layer (per-tenant OPA bundle paths + X-Tenant-ID header), not inside the Rego rules themselves". CISO will appreciate the precision.

#### B9. Append-only audit trigger overstated as "physically forbids mutation"
**Doc says (§2):** "PostgreSQL (RDS Multi-AZ) with INSTEAD OF UPDATE/DELETE trigger on `audit_logs` (migration `3a519b48a6f2`) — physically forbids mutation"

**Code says:** `services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py:35-54` — the trigger raises `P0001`. ✅ True for the *application* DB user (`audit_user`). A PostgreSQL superuser can `DROP TRIGGER deny_audit_log_mutation;` and then mutate. RDS superuser is in your control plane.

**Fix:** "Append-only trigger blocks UPDATE/DELETE for the application database user. Database superusers can drop the trigger; superuser access is logged via RDS database activity streams." Honest, defensible, and the CISO will not catch you on it.

#### B10. `/transparency/keys` is JWT-gated, not public
The public verifiability story (good!) is via S3 + `aegis-verify` CLI. The `/transparency/keys` endpoint on `aegisagent.in` returns **401** when curled (confirmed live). That's correct — it's an in-product UI endpoint — but a casual reader could think they could verify roots via `https://aegisagent.in/transparency/keys`. They can't.

**Fix:** in §2, when describing the audit trail, say explicitly that the **public verification path is S3 + `aegis-verify` CLI**, not any aegisagent.in endpoint.

---

## What the doc gets RIGHT (sometimes understates)

### S1. Public S3 transparency chain is real and recent — *understated*
**Doc says:** "ed25519-signed Merkle roots, daily job, mirrored to public S3 (`s3://aegis-public-roots-628478946931`)"

**Runtime confirms:**
- `aws s3 ls --no-sign-request s3://aegis-public-roots-628478946931/` returns **48 objects**
- Structure: `keys/`, `latest/`, `roots/`
- Daily root files for **2026-06-14, -15, -16, -17, -18** (today)
- **Multi-tenant: 7 tenant subdirectories** (including 2 synthetic test tenants `00…001` and `22…222`, and 5 real-UUID tenants)
- One root file content:
  ```
  algorithm: ed25519
  prev_root_hash: 4ede406671c903b8730f700a3d5ef17dfa382880a1beef796c94a1c60cc28800
  root_hash:      f5451a1b4ee7207b41b38fa3a999b1f9242e3d1a2d10a6970ec8fc021b3cddec
  root_date:      2026-06-18
  notes:          "External witness: download this file directly (no Aegis credentials required). Verify the signature against the public key at /keys/<signing_kid>.pem. Walk the prev_root_hash chain back to genesis to detect rewrite."
  ```

This is a real, currently-operating, cryptographically chained public transparency log. Lead with this in any CISO conversation. Could also surface in §3 as live evidence — currently §3 only quotes a `_aegis_blocked` block.

### S2. `/status` returns operational across 12 services — *not in doc*
**Live probe:** `curl https://aegisagent.in/status` → HTTP 200, body:
```json
{
  "status": "operational",
  "components": {
    "registry": "operational", "identity": "operational", "policy": "operational",
    "audit": "operational", "usage": "operational", "behavior": "operational",
    "decision": "operational", "insight": "operational", "forensics": "operational",
    "identity_graph": "operational", "flight_recorder": "operational", "autonomy": "operational"
  },
  "uptime_seconds": 58356,
  "latency": { "scope": "gateway_internal", ... }
}
```

12 components all "operational", ~16 hours uptime as of 2026-06-18. The biz doc references the status page but doesn't quote it. A public, no-auth status endpoint with per-component health is genuinely good DX — surface it.

### S3. 36 signals / 9 MITRE tactics / 17 prompt patterns — all exact-counted matches
- 36 `SignalDefinition` entries in `services/security/signal_registry.py:138-457` — exact count
- 9 MITRE tactics in `SecurityObjective` enum (TA0001/3/4/5/6/7/9/10/40) at `services/security/signal_registry.py:45-67`
- 17 prompt-injection regex patterns in `sdk/common/injection_patterns.py:19-171` — exact count

State these with confidence and cite the file. "We have N detections" lands harder when you can quote the line.

### S4. Algorithm-downgrade hardening is exactly the subtle attack CISOs reward
**Code:** `services/gateway/auth.py:239-253` — dispatcher rejects HS256 tokens carrying Clerk-shaped `iss` before they reach the Clerk validator. The U4 fix from 2026-06-17.

**Doc says (§2):** "legacy HS256 path rejects Clerk-shaped `iss` (closes algorithm-downgrade attack)"

This is right but buried. In any CISO/PSA conversation, lead with this. It's the kind of finding that signals "we read OWASP cheatsheets, not just security marketing."

### S5. 3-layer multi-tenancy enforcement is real and orthogonal
**Code says:**
- Layer 1 webhook write: `services/identity/webhooks_clerk.py:286-290` — sets `aegis_org_id` and `aegis_tenant_id` to the same UUID
- Layer 2 JWT: `sdk/common/clerk_auth.py` — payload carries both claims, canonicalizes
- Layer 3 DB CHECK constraint: migration `a1b2c3d4e5f6_add_check_constraint_org_tenant_match.py:23-32` adds TWO constraints (`ck_users_org_tenant_match` and `ck_agent_creds_org_tenant_match`)

A DB CHECK constraint that *can't* be satisfied by a row where org_id ≠ tenant_id is a strong defensive position. Highlight it.

---

## Verification matrix (every claim, every verdict)

### §2 Architecture (16 claims)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | FastAPI gateway | ✅ Code | `services/gateway/main.py` FastAPI app |
| A2 | PostgreSQL Multi-AZ | ✅ Code, ❓ Runtime | `infra/terraform/environments/prod-ha/main.tf:184` `multi_az = true` (Terraform desired state; not independently verified against live RDS) |
| A3 | Append-only trigger `3a519b48a6f2` | ✅ Code | `services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py:35-54` |
| A4 | OPA v0.69.0-debug | ✅ Code | `infra/docker-compose.yml:111` `openpolicyagent/opa:0.69.0-debug` |
| A5 | ElastiCache Redis | ✅ Code | compose + `acp:apikey:revoked` set at `services/gateway/_mw_auth.py:31,81` |
| A6 | Budget fast-path in Redis | ✅ Code | `services/usage/cost_engine.py:52-93` |
| A7 | Clerk RS256 + JWKS rotation | ✅ Code | `services/gateway/auth.py:238-255`; `sdk/common/clerk_auth.py:100-184` |
| A8 | HS256+Clerk-iss reject | ✅ Code | `services/gateway/auth.py:239-253` |
| A9 | 2-host ASG behind ALB | ✅ Code, ❓ Runtime | `infra/terraform/environments/prod-ha/main.tf:269-284` |
| A10 | `one_nat_per_az=true` | ✅ Code | `infra/terraform/environments/prod-ha/main.tf:77` |
| A11 | Pinned Docker image tags | ✅ Code | compose has `postgres:15-alpine`, `pgbouncer:1.23.1`, `redis:7-alpine`, `prometheus:v2.55.1`, `grafana:11.3.0`, `jaeger:1.57` |
| A12 | ed25519 daily Merkle root | ✅ Code, ✅ Runtime | `services/audit/public_transparency.py:70-100`; S3 bucket shows 5 days of dated roots |
| A13 | Public S3 `aegis-public-roots-628478946931` | ✅ Code, ✅ Runtime | `services/audit/public_transparency.py:37-40`; **48 objects live, ed25519-signed** |
| A14 | SSE per-tenant channel | ✅ Code | `services/gateway/main.py:1455-1457` `f"acp:events:{tenant_id_str}"` |
| A15 | SSE `< 200ms` decision-to-UI | ❓ | No measurement, design target only |
| A16 | 3-layer multi-tenancy (webhook/JWT/DB CHECK) | ✅ Code | webhook `webhooks_clerk.py:286-290`; JWT `sdk/common/clerk_auth.py`; DB `a1b2c3d4e5f6_add_check_constraint_org_tenant_match.py:23-32` |

### §2 SDK Packages (5 claims)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| S1 | `aegis-anthropic` v1.1.0 | ✅ Code, ✅ PyPI | Both 1.1.0 |
| S2 | `aegis-openai` v1.1.0 | ✅ Code, ✅ PyPI | Both 1.1.0 |
| S3 | `aegis-bedrock` v1.1.0 | ⚠️ Code 1.0.0 / ✅ PyPI 1.1.0 | `__version__` lag; published wheel is 1.1.0 |
| S4 | `aegis-langchain` v1.1.0 | ⚠️ Code 1.0.1 / ✅ PyPI 1.1.0 | `__version__` lag; published wheel is 1.1.0 |
| S5 | `aegis-aevf` (verifier) | ❌ Doc says 1.1.0; PyPI is **1.0.0** | This is the only published release |

### §3 Live evidence (1 claim)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| L1 | `_aegis_blocked` shape `{tool_use_id, tool_name, decision: {action, risk, findings}}` | ✅ Code shape; ❓ Specific capture | `integrations/aegis-anthropic/aegis_anthropic/__init__.py:198-204` matches the shape. The specific test "captured by Abhishek Mishra against aegisagent.in" is a human-claim I cannot verify. |

### §4 Detection catalogue (Path A — 36 signals + Path B — 17 patterns)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| D1 | 36 canonical signals | ✅ Code | `services/security/signal_registry.py:138-457` exactly 36 |
| D2 | 9 MITRE tactics | ✅ Code | `signal_registry.py:45-67` (TA0001/3/4/5/6/7/9/10/40) |
| D3 | `/etc/passwd` etc. → DENY | ✅ Code | `signal_registry.py:235-240` `system_sensitive_path` |
| D4 | `~/.aws/credentials` → DENY | ✅ Code | `signal_registry.py:219-225` `cloud_credential_path` |
| D5 | `id_rsa` → DENY | ✅ Code | `signal_registry.py:227-232` `ssh_credential_path` |
| D6 | SQL DROP/TRUNCATE/SQLi → DENY | ✅ Code | 3 signals (`sql_injection_detected`, `destructive_sql_ddl`, `destructive_sql_dml_no_predicate`) + Rego `action_semantics_deny.rego:107-156` |
| D7 | Bulk PII > 50k → DENY | ❌ Code says **10k** | `local_action_semantics.py:81-86` thresholds per risk: low=10k, medium=1k, high=100, critical=0 |
| D8 | `kubectl delete` prod → ESCALATE → SRE_LEAD | ✅ Code | `signal_registry.py:395-401`; routing `escalation_patterns.py:56-64` |
| D9 | `terraform destroy` → ESCALATE → SRE_LEAD | ✅ Code | `signal_registry.py:419-425`; routing `escalation_patterns.py:66-74` |
| D10 | Wire ≥ $200k → ESCALATE → CFO | ⚠️ Mixed | Signal registry $200k; pattern detector $100k — **see bug B1** |
| D11 | transfer.sh / pastebin exfil → DENY | ✅ Code | `signal_registry.py:305-327`; Rego `action_semantics_deny.rego:243-246` |
| D12 | 17 prompt-injection patterns | ✅ Code | `sdk/common/injection_patterns.py:19-171` exactly 17 |
| D13 | `ignore previous`/`forget context` → 403 | ✅ Code | `injection_patterns.py:21-37` |
| D14 | Persona reassignment → 403 | ✅ Code | `injection_patterns.py:38-54` |
| D15 | jailbreak / DAN → 403 | ✅ Code | `injection_patterns.py:68-88` |
| D16 | Mass-destruction phrasing → 403 | ✅ Code | `injection_patterns.py:90-98` |
| D17 | Token-smuggling → 403 | ✅ Code | `injection_patterns.py:111-118` |
| D18 | Wire > $100k → 202 → CFO | ✅ Code | `escalation_patterns.py:39-52` |
| D19 | Single-record PII lookup → 202 → CISO | ✅ Code | `escalation_patterns.py:114-142` |
| D20 | Bulk PII export → 202 → CISO | ✅ Code | `escalation_patterns.py:94-105` |
| D21 | DROP specific table → 202 → CISO | ✅ Code | `escalation_patterns.py:77-91` |
| D22 | Rego policies, tenant-isolated, version-controlled | ⚠️ Phrasing | Files exist + per-tenant bundle paths + version field; isolation is at bundle layer, not inside Rego — **see bug B8** |

### §5 Honest gaps section — all confirmed (the doc is correctly admitting these)

| # | Doc claim | Verdict | Evidence |
|---|---|---|---|
| G1 | No SOC2 Type II | ✅ Confirmed absent | `docs/security/soc2_tracker.md:9` "ENGAGED — vendor selection in progress (Q3 2026 target)" |
| G2 | No ISO 27001 | ✅ Confirmed absent | No cert file in repo |
| G3 | No independent pen test | ✅ Confirmed absent | `soc2_tracker.md:78` open item |
| G4 | No production load-test numbers | ✅ Confirmed absent | Only `gateway_p95_dry.json` (synthetic single-host) |
| G5 | No DR evidence / RTO/RPO SLA | ✅ Confirmed absent | No DR runbook with measured numbers |
| G6 | No SLO/SLA dashboard | ✅ Confirmed absent | No customer-facing SLO dashboard |
| G7 | No BYOK | ✅ Confirmed absent | KMS integration not surfaced |
| G8 | No data residency options | ✅ Confirmed absent | Single AWS region |
| G9 | No published incident response | ✅ Confirmed absent | No IR runbook published |
| G10 | No published retention policy | ✅ Confirmed absent | Discussed in soc2_tracker, not published |
| G11 | No public customer references | ✅ Confirmed absent | No customer logos / case studies in repo |

### §7 Business model (3 claims)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| BM1 | Free $0 / Pro $499 / Enterprise $4,999 | ⚠️ Code has env vars not amounts | `services/gateway/routers/billing.py:41-45` references `STRIPE_PRO_PRICE_ID`, `STRIPE_ENTERPRISE_PRICE_ID` |
| BM2 | Stripe Checkout | ✅ Code | `services/gateway/routers/billing.py:136-198` `POST /billing/checkout-session` |
| BM3 | Stripe Customer Portal | ✅ Code | `services/gateway/routers/billing.py:201-237` `POST /billing/portal-session` |

### §8 Dashboard (5 claims)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| UI1 | OBSERVE/PROTECT/PROVE/WORKSPACE 4-module nav | ✅ Code | `ui/src/components/Layout/Sidebar.jsx:40,45,55,58` |
| UI2 | OBSERVE = Dashboard / Team / Live Feed | ✅ Code | Sidebar.jsx:40-43 |
| UI3 | PROTECT = Agents / Incidents / Approval Inbox / Policies | ✅ Code | Sidebar.jsx:45-53 |
| UI4 | PROVE = Compliance | ✅ Code | Sidebar.jsx:55-56 |
| UI5 | 16 advanced surfaces | ❌ Code has 15, doc enumerates 13 — **see bug B6** | Sidebar.jsx:62-78 |

### §12 Key facts (latency + version claims)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| K1 | "~27 ms p95" decision latency (§12) | ❓ | Only `reports/gateway_p95_dry.json` (dry-run synthetic) |
| K2 | "~150 ms p95" decision latency (§3) | ❓ | **Internal contradiction with K1** — see bug B5 |
| K3 | "< 200 ms" SSE delivery | ❓ | No measurement |
| K4 | "Live URL: https://aegisagent.in" | ✅ Runtime | curl returns 200, 12 components operational, uptime ~16h |
| K5 | "Status page: https://aegisagent.in/status" | ✅ Runtime | curl returns 200 + JSON status body |

---

## Recommended doc edits (the specific text changes)

### Edit 1 — §12 "Key Facts" SDK line

**Replace:**
> SDK version: 1.1.0 (PyPI: aegis-anthropic, aegis-openai, aegis-bedrock, aegis-langchain)
> Verifier: pip install aegis-aevf

**With:**
> SDK wrappers v1.1.0 on PyPI: aegis-anthropic, aegis-openai, aegis-bedrock, aegis-langchain. Standalone verifier: pip install aegis-aevf==1.0.0.

### Edit 2 — §3 and §12 latency reconciliation

**Pick ONE of these and use it consistently:**

(option A — conservative, defensible)
> Decision latency: design target < 200ms p95 end-to-end (gateway → policy decision → SSE). Synthetic dry-run on a single host measured ~21ms p95 ([reports/gateway_p95_dry.json](reports/gateway_p95_dry.json)). Production load-test report pending.

(option B — punchier, defer the measurement)
> Decision latency target: < 200ms p95 end-to-end. Production benchmark publishing Q3 2026.

Drop the "~27 ms p95" and "~150 ms p95" numbers until a real prod measurement exists.

### Edit 3 — §4 wire transfer description

**Replace the two contradictory rows with:**
> | Wire transfer | Detection threshold | Enforcement threshold | Approver |
> |---|---|---|---|
> | Wire pattern in prompt (Path B) | $100k | — | 202 → CFO |
> | Wire signal on tool call (Path A) | $200k | $200k (Rego deny floor) | escalate → CFO |
>
> Both paths route to the same CFO approval queue. Wires of $100k–$199k get queued for CFO sign-off via prompt detection without hitting the Rego enforcement layer.

### Edit 4 — §4 bulk PII row

**Replace:**
> Bulk PII export > 50k rows (email/SSN-shaped columns) → DENY

**With:**
> Bulk PII export ≥ 10k rows (low-risk tenants) → DENY. Threshold drops with tenant risk tier (1k for medium, 100 for high, 0 for critical). Sub-threshold bulk reads (< 10k) → ESCALATE → CISO.

### Edit 5 — §8 advanced surfaces

**Replace:**
> Advanced surfaces (16 total, JWT-gated, tenant-isolated): Audit Logs, Forensics, Observability, Threat Graph + MITRE ATT&CK coverage, Identity Graph, Auto-Response, Evaluation, Playbooks, Shadow Mode, Flight Recorder, Decision Explorer, Session Explorer, Fleet.

**With:**
> Advanced surfaces (15, JWT-gated, tenant-isolated): Audit Logs, Forensics, Agent Playground, Threat Intel, Evaluation, Playbooks, Auto-Response, Identity Graph, Threat Graph, Shadow Mode, Shadow Review, Flight Recorder, Decision Explorer, Session Explorer, Fleet.

### Edit 6 — §2 append-only trigger phrasing

**Replace:**
> with INSTEAD OF UPDATE/DELETE trigger on `audit_logs` (migration `3a519b48a6f2`) — physically forbids mutation

**With:**
> with `INSTEAD OF UPDATE/DELETE` trigger on `audit_logs` (migration `3a519b48a6f2`) — application database role cannot mutate audit rows; trigger raises `P0001`. Trigger drop and RDS-level mutation are logged via RDS database activity streams.

### Edit 7 — §2 public verification path

**Add to the audit-trail bullet:**
> Public verification is performed against `s3://aegis-public-roots-628478946931` (no AWS credentials required) using the `aegis-verify` CLI. The `aegisagent.in/transparency/*` endpoints are in-product UI routes, not the public witness path.

### Edit 8 — §7 pricing

**Replace:**
> | Pro | $499 / month | Production teams up to ~20 employees |
> | Enterprise | $4,999 / month | Large orgs, SSO, SIEM, SLA, dedicated support |

**With:**
> | Pro | <current Pro Stripe Price ID amount> / month | Production teams up to ~20 employees |
> | Enterprise | <current Enterprise Stripe Price ID amount> / month | Large orgs, SSO, SIEM, SLA, dedicated support |
>
> *Prices are sourced from Stripe price IDs (`STRIPE_PRO_PRICE_ID`, `STRIPE_ENTERPRISE_PRICE_ID`). Confirm against Stripe dashboard before quoting in prospect proposals.*

---

## Adds for the doc (claims you can be more confident about)

### Add 1 — Public transparency log is live, multi-tenant, daily

Insert into §2 or §3:
> The public Merkle-root S3 bucket contains 5 days of daily ed25519-signed roots (2026-06-14 → 2026-06-18) across 7 tenant partitions. Each root carries `prev_root_hash` chaining back to genesis. External auditors can fetch any historical root with `aws s3 cp s3://aegis-public-roots-628478946931/roots/<tenant-uuid>/<date>.json --no-sign-request`, verify the ed25519 signature against `keys/<signing_kid>.pem` in the same bucket, and walk the prev_root_hash chain to detect any history rewrite.

### Add 2 — /status endpoint as live evidence

Insert into §3 (live evidence):
```
$ curl https://aegisagent.in/status
{
  "status": "operational",
  "components": { "registry":"operational", "identity":"operational",
                  "policy":"operational",   "audit":"operational",
                  "usage":"operational",    "behavior":"operational",
                  "decision":"operational", "insight":"operational",
                  "forensics":"operational","identity_graph":"operational",
                  "flight_recorder":"operational","autonomy":"operational" },
  "uptime_seconds": 58356,
  "latency": { "scope": "gateway_internal", ... }
}
```
12 components reporting operational; gateway uptime ~16h at audit time.

---

## Items that remain ❓ unverified after this audit

These are claims I cannot validate without privileged access to ByteHubble's prod, Clerk dashboard, Stripe dashboard, AWS account, or the test suite execution environment:

1. **Actual decision latency under load** (>0 QPS, sustained, multi-host) — no published number
2. **SSE decision-to-UI latency in real conditions** — no published number
3. **Whether Clerk JWKS rotation runs on schedule** — code has the cache; rotation happens externally at Clerk
4. **Whether RDS Multi-AZ is actually applied in prod-ha** — Terraform says so; couldn't confirm against live AWS
5. **Whether Stripe Pro is actually $499 and Enterprise actually $4,999** — env vars only; need Stripe dashboard access
6. **Whether `aegis-verify` CLI actually validates a real bundle end-to-end** — I have the package + the S3 roots; didn't run the validation
7. **Whether the §3 specific live capture was a real production event** — code shape matches, capture event is a human claim
8. **Whether ByteHubble actually owns Aegis and the founder attribution in §13 is correct** — not a code question; trust the doc

---

## Bottom line

**The product is real.** `aegisagent.in/status` returns 200 with 12 healthy components. The public S3 transparency chain has 5 days of cryptographically chained, ed25519-signed Merkle roots across 7 tenants — that's a meaningfully strong claim and it's true *right now*. The detection coverage (36 signals, 9 MITRE tactics, 17 prompt patterns) is exactly as stated. Multi-tenancy at the DB CHECK constraint is correctly designed. Clerk RS256 + algorithm-downgrade reject is well-implemented.

**The doc oversells in a small number of fixable ways.** Fix the 3 hard inaccuracies (B1/B2/B3) and the 5 soft mismatches (B4–B7, B8) and the doc becomes defensible to a Principal Security Architect.

**The doc undersells in important ways.** The public S3 transparency log is more impressive than §2 makes it sound, and the `/status` JSON is genuinely good DX that's not mentioned at all.

Once B1 (wire-transfer enforcement gap) is fixed in code, the platform has no production-blocking issues I found. The gaps section (§5 — no SOC2, no pen test, no load test) is honest and accurate; address those on the published 30/90/180-day plan and you are pilot-customer ready.

---

*End of audit — generated 2026-06-18 by Claude code-review + 3 runtime probes (curl, aws s3, PyPI).*
