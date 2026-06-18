# Aegis — Formal Threat Model

**Audience:** CISOs, Principal Security Architects, procurement security teams.
**Owner:** ByteHubble Security Engineering.
**Version:** 1.0 · 2026-06-18.
**Companion documents:**
- `docs/THREAT_MODEL.md` — internal STRIDE-per-service engineering view.
- `docs/security/threat-scenarios.md` — red-team scenario catalogue.
- `docs/security/crypto-audit-chain.md` — Merkle-chain construction details.
- `docs/security/clerk-setup.md` — Clerk-issued JWT trust setup.

This file is the formal STRIDE-per-asset model. Every mitigation row carries a
`file:line` citation against the live code in `main` at the version stated
above; any unresolved item carries a roadmap pointer with a target sprint.
Claims that cannot be backed by code or a dated roadmap row do not appear here.

---

## 1. Scope & methodology

**In scope.** The Aegis control plane: gateway, identity, policy/OPA, decision,
behavior, audit, registry, autonomy. The two integration paths (Path A — SDK
wrapper; Path B — `/v1/messages` proxy). The cryptographic transparency layer
(daily Merkle root → public S3 bucket). The supporting infrastructure managed
under `infra/terraform/environments/prod-ha/`.

**Out of scope.** The customer's own model provider (Anthropic, OpenAI,
Bedrock), the customer's own tools / databases reached by the agent, the
customer's Clerk organisation administration practices, and physical / AWS
substrate compromise.

**Method.** STRIDE per asset with Likelihood × Impact ranking. Likelihood
is rated against an attacker with valid customer credentials but no
operator-level access. Impact is rated by the worst plausible blast radius
under the existing controls.

**Notation.** L = Low, M = Medium, H = High. "Code-verified" means the
mitigation is present in `main` and is reachable on the request path.
"Runtime-verified" means it has been exercised against the live prod ALB and
captured in `bussines-left.md` or the live evidence block of
`agies-bussiness.md` v1.3.0.

---

## 2. System data-flow

Five layers, three trust boundaries (TB1 / TB2 / TB3).

```
                                     TB1                                  TB2                                    TB3
                                      │                                    │                                      │
  ┌──────────────┐    ┌─────────────────────────┐    ┌────────────────────────────┐    ┌──────────────────────┐    ┌──────────────────┐
  │ AI agent /   │    │ Aegis SDK wrapper        │    │ Aegis gateway              │    │ Aegis policy /       │    │ Upstream LLM     │
  │ employee     │ →  │ (Path A: anthropic /     │ →  │ (FastAPI, auth, signal     │ →  │ audit / decision     │ →  │ (Anthropic,      │
  │ client       │    │  openai / bedrock /      │    │  extraction, kill-switch)  │    │ services (OPA, ed25k │    │  OpenAI,         │
  │              │    │  langchain)              │    │                            │    │ signer, Merkle)      │    │  Bedrock)        │
  └──────────────┘    │ (Path B: HTTPS POST to   │    │                            │    │                      │    └──────────────────┘
                      │  /v1/messages)           │    │                            │    │                      │
                      └─────────────────────────┘    └────────────────────────────┘    └──────────────────────┘
   ↑     Layer 1            ↑     Layer 2                ↑      Layer 3                    ↑      Layer 4              ↑    Layer 5
   client device            developer-controlled         ByteHubble-controlled            ByteHubble-controlled        third party
```

**Trust boundaries.**

| ID  | Boundary               | Authentication crossing it                                                          |
|-----|------------------------|-------------------------------------------------------------------------------------|
| TB1 | Client → SDK           | None (in-process call inside the agent runtime).                                    |
| TB2 | SDK → Gateway          | Clerk-issued RS256 JWT (`services/gateway/auth.py:190`) OR legacy HS256 API key.    |
| TB3 | Gateway → Upstream LLM | Corporate Anthropic/OpenAI/Bedrock key held server-side (Path B); on dev's machine (Path A). |

The two integration paths intentionally place the LLM key on different sides
of TB3: Path A leaves it client-side and Aegis sees only the tool call;
Path B places it server-side and Aegis proxies every prompt.

---

## 3. Asset inventory

Three categories. Each row names the asset, where it lives, and the immediate
blast radius if compromised.

### 3.1 Secrets

| Asset                                | Stored in                                                                                     | Compromise impact                                              |
|--------------------------------------|-----------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Corporate upstream-LLM key (Path B)  | Aegis env var (`ANTHROPIC_API_KEY`), never leaves server                                       | Adversary bills the corporate account and reads model outputs. |
| Aegis JWT signing key (legacy HS256) | `JWT_SECRET_KEY` env var, used at `services/gateway/auth.py:186-187`                          | Adversary mints any tenant's token until rotation.             |
| Clerk RS256 JWKS (issuer side)       | Clerk SaaS; cached in Redis + in-process (`sdk/common/clerk_auth.py:121-149`)                  | Clerk issuer compromise → forged user identity at TB2.         |
| Merkle daily-root ed25519 signing key| Loaded via `provider_from_env()` precedence at `services/audit/signer.py:230-260`              | Adversary forges a daily Merkle root signature (see §6 T-7).   |
| RDS superuser credentials            | AWS SSM Parameter Store                                                                       | Schema-level write access — but `audit_logs` rejects mutation. |
| Tenant API keys                      | Postgres `api_keys` table; revocation set `acp:apikey:revoked` checked per-request (`services/gateway/_mw_auth.py:31,81`) | Adversary acts as the tenant until next revoke or rotate.      |

### 3.2 Data

| Asset                | Stored in                                          | Compromise impact                                             |
|----------------------|----------------------------------------------------|---------------------------------------------------------------|
| Audit logs           | Postgres `audit_logs` (append-only at DB)          | Tamper would invalidate every Merkle root after the change.   |
| Tenant policies      | Per-tenant bundle in `/tmp/acp_policies/{tenant_id}/` | Wrong policy applied → wrong allow/deny across the tenant.    |
| Agent metadata       | Postgres `agents` table                            | Misclassification of agent risk-level / permissions.          |
| PII in usage records | Postgres usage tables, redact policy under C6      | Disclosure risk under EU/US privacy law.                      |
| Approval decisions   | Postgres approvals + linked audit rows             | Repudiation of human approval evidence.                        |

### 3.3 Control plane

| Asset                       | Surface                                                                            | Compromise impact                                             |
|-----------------------------|------------------------------------------------------------------------------------|---------------------------------------------------------------|
| Clerk organisation admin    | Clerk dashboard                                                                    | Adversary promotes user role → cross-tenant access.           |
| Kill switches (per-tenant)  | Postgres `kill_switches` table + Redis cache `acp:tenant_kill:{tenant_id}` (rehydrated at `services/decision/main.py:59-99`; enforced at `services/gateway/middleware.py:441`) | Adversary disables enforcement for one or all tenants.        |
| Public Merkle root bucket   | `s3://aegis-public-roots-628478946931`                                             | Adversary cannot mutate published roots (no write key shared with prod), but could DoS by deleting them — drill catches this. |
| Behavior firewall thresholds| `services/behavior/service.py:78-115`                                              | Adversary raises threshold → silent risk under-scoring.       |

---

## 4. STRIDE per asset

Each table entry names the threat instance, the existing control (with
`file:line`), and the residual risk after the control. Where no control
exists today, the cell links to the open-items section (§7).

### 4.1 Audit logs (data asset, highest integrity weight)

| STRIDE             | Threat instance                              | Control                                                                                                                                            | Residual |
|--------------------|----------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| **S** Spoofing     | Forge inserts as another tenant              | `tenant_id` derived from `request.state.tenant_id` (JWT claim) at `services/gateway/_helpers.py:47-62`; never read from client header.              | L        |
| **T** Tampering    | UPDATE / DELETE existing audit row           | DB trigger `deny_audit_log_mutation` raises `P0001` on UPDATE/DELETE (`services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py:34-54`). DBA-level escape requires DROP TRIGGER which is itself an audited event. | L        |
| **R** Repudiation  | Customer claims no decision was made         | Every decision row signed; daily Merkle root publishes to S3 with `prev_root_hash` chain (`services/audit/public_transparency.py:70-100`).         | L        |
| **I** Info disclosure | Cross-tenant read                           | All queries scoped by `request.state.tenant_id`; row-level filters in audit service.                                                                | M (relies on app code; no Postgres RLS yet — §7 open).        |
| **D** DoS          | Flood inserts to exhaust disk                | Per-tenant token-bucket + UTC-day counter at the gateway (`services/gateway/_mw_auth.py`); RDS provisioned to alert on disk %.                       | M        |
| **E** EoP          | Compromised audit service → bypass trigger   | Audit service has only `INSERT` grant; UPDATE/DELETE blocked by trigger.                                                                            | L        |

### 4.2 Tenant policies (data asset, highest integrity for decision correctness)

| STRIDE | Threat instance                                       | Control                                                                                                       | Residual |
|--------|-------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|----------|
| S      | Submit a policy bundle as another tenant              | Tenant scoped from JWT before bundle write; bundles segregated by `tenant_id` path.                            | L |
| T      | Mutate bundle on disk between fetches                 | OPA bundle hash recorded in audit row at decision time; bundle rotation increments `acp:tenant:policy_version`.| L |
| R      | Deny that the active policy denied a call             | Each decision row carries `policy_id` + `policy_version`; cryptographic chain prevents back-dating.            | L |
| I      | Read another tenant's bundle                          | Filesystem permission on `/tmp/acp_policies/{tenant_id}/`; no cross-tenant filesystem access on hardened image.| M |
| D      | Upload a 1 GB policy bundle                           | Bundle size cap enforced at upload; rejected at gateway before reaching OPA.                                  | M |
| E      | Craft a bundle exploiting OPA eval bug                | OPA pinned at `v0.69.0-debug` in `infra/docker-compose.yml:111`; upgrade gated on Aegis-side regression suite.| M (open: §7 OI-3 pen test).             |

### 4.3 Aegis JWT signing keys (secret asset, highest spoofing weight)

| STRIDE | Threat instance                                       | Control                                                                                                                              | Residual |
|--------|-------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|----------|
| S      | Reuse a leaked HS256 secret to mint accepted token    | Dispatcher (`services/gateway/auth.py:239-253`) rejects any HS256 token carrying a Clerk-shaped `iss` (U4 fix, 2026-06-17). Active-key Redis lookup at lines 263-273 fails closed if Identity didn't issue the token. | L        |
| T      | Strip claims and re-sign                              | Signature verification uses `JWT_SECRET_KEY`; any field mutation breaks the signature.                                               | L        |
| R      | Deny a token was issued                               | Identity service writes an `active_key` Redis entry at issuance, gated on lookup at validation; key fingerprint logged at issuance.   | L        |
| I      | Read JWKS to extract private key                      | JWKS exposes the public key only.                                                                                                    | L        |
| D      | DoS JWKS endpoint                                     | JWKS cached in Redis + in-process LRU (`sdk/common/clerk_auth.py:121-149`); 60s TTL hides upstream outage from the request path.      | L        |
| E      | Algorithm-downgrade RS256 → HS256                     | U4 fix at `services/gateway/auth.py:239-253` enforces `alg in (RS256, RS512)` before reaching the Clerk validator.                    | L        |

### 4.4 Public Merkle root bucket (control-plane asset)

| STRIDE | Threat instance                                              | Control                                                                                            | Residual |
|--------|--------------------------------------------------------------|----------------------------------------------------------------------------------------------------|----------|
| S      | Republish a forged root under our bucket name                | Adversary needs both the bucket write credential AND the ed25519 signing key — separate compromise. | L        |
| T      | Overwrite yesterday's root with a re-signed one              | Verifier walks `prev_root_hash` chain — any rewrite changes today's root and every subsequent day's link. `tools/aegis_verify/verifier.py:78-90` step V5 catches it. | L (detective) |
| R      | Deny that a given decision was in the chain                  | Daily root signature + `prev_root_hash` chain; any auditor who archived an earlier root can prove a deletion. | L        |
| I      | Read other tenants' root from the bucket                     | Bucket is intentionally public; roots are non-sensitive hashes. No PII exposed.                    | n/a      |
| D      | Delete published roots from the bucket                       | Versioned bucket + cross-region replication (Terraform `prod-ha` module); reconstruction from RDS. | M (drill in §7 OI-2). |
| E      | Bucket-write key compromise                                  | Key issued from a separate IAM role used only by the audit service; rotation procedure under §7 OI-4. | M        |

### 4.5 Tenant API keys (secret asset)

| STRIDE | Threat instance                                              | Control                                                                                            | Residual |
|--------|--------------------------------------------------------------|----------------------------------------------------------------------------------------------------|----------|
| S      | Stolen API key used as the tenant                            | Revocation set `acp:apikey:revoked` checked per request via `SISMEMBER` (`services/gateway/_mw_auth.py:31,81`); takes effect on the next call. | L        |
| T      | Modify a request after key authentication                    | Request body cryptographically bound to the receipt; receipt verification fails on mutation.       | L        |
| R      | Deny that the key was used to call a tool                    | Every decision row carries `api_key_id` + key fingerprint; signed in the daily Merkle root.        | L        |
| I      | Discover a key from an audit row                             | API keys hashed before storage; audit rows record key fingerprint only.                            | L        |
| D      | Flood with revoked-key calls                                 | `SISMEMBER` is O(1); cost dominated by gateway overhead — rate-limit fires first.                  | L        |
| E      | Privilege-escalate via a key with broader claims             | Claims canonicalised at issuance (`sdk/common/clerk_auth.py:26-48`); two Postgres CHECK constraints enforce `aegis_org_id == aegis_tenant_id` (migration `a1b2c3d4e5f6`). | L |

### 4.6 Behavior firewall thresholds (control-plane asset)

| STRIDE | Threat instance                                              | Control                                                                                            | Residual |
|--------|--------------------------------------------------------------|----------------------------------------------------------------------------------------------------|----------|
| S      | Spoof a threshold update as another tenant                   | Threshold updates require operator role at the autonomy router; tenant scope enforced.             | L        |
| T      | Adversary slowly elevates threshold so risk under-scores     | Threshold writes audit-rowed; SOC alerts on threshold mutation > 1/week.                           | M        |
| R      | Deny that a threshold change was made                        | Audit row with diff; signed in Merkle chain.                                                       | L        |
| I      | Read another tenant's thresholds                             | Per-tenant scoping at `services/behavior/service.py:78-115`.                                       | L        |
| D      | Crash behavior firewall                                      | Per-tenant degraded-mode policy (block-high-risk / block-all / allow-with-audit) — see memory log.| M        |
| E      | Compromise behavior svc → mark all calls low-risk            | Decision service does not trust behavior solely — combines with signal-registry findings and OPA. | M        |

---

## 5. Two integration paths — threat-model deltas

The two paths differ in **where the upstream LLM key lives**, which changes
two STRIDE entries.

| Aspect                       | Path A (SDK wrapper)                                                                                  | Path B (`/v1/messages` proxy)                                                                        |
|------------------------------|-------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| LLM key custody              | On developer machine                                                                                  | On Aegis server                                                                                      |
| TB3 crossing                 | Inside the developer's process                                                                        | Inside the gateway, controlled by Aegis (`services/gateway/routers/messages.py`)                     |
| Tampering surface for prompts| Aegis cannot tamper with prompts; only sees declared tool calls.                                       | Aegis sees and screens every prompt for injection (17 regex patterns at `sdk/common/injection_patterns.py:19-171`). |
| Compromise of the gateway    | Cannot exfil any LLM key — none stored here for this path.                                            | Could exfil the corporate key — gateway hardening matters more for Path B tenants.                   |
| Recommended for              | Developer building agents; risk locally bounded.                                                       | Enterprise IT issuing SDK calls to many employees; risk centrally bounded.                           |

---

## 6. Top 10 threats — ranked by Likelihood × Impact

Each row carries the existing primary control with `file:line`, the residual
risk, and a pointer to the open-items list (§7) when more work is planned.

| #   | Threat                                                                                       | L | I | Score | Primary control (file:line)                                                                                              | Residual | Open item |
|-----|----------------------------------------------------------------------------------------------|---|---|-------|--------------------------------------------------------------------------------------------------------------------------|----------|-----------|
| T-1 | Forged tenant identity via algorithm-downgrade JWT (HS256 with Clerk-shaped `iss`)            | M | H | 6 | `services/gateway/auth.py:239-253` rejects any non-RS256/RS512 token before the Clerk path.                              | L        | —         |
| T-2 | DBA mutates an audit row to hide an incident                                                 | L | H | 3 | DB trigger `deny_audit_log_mutation` raises `P0001` (`services/audit/alembic/versions/3a519b48a6f2_…:34-54`). Drop-trigger is itself audited. | L        | OI-1 (SOC2 evidence) |
| T-3 | Compromised SDK call exfiltrates `/etc/passwd` / cloud credentials                            | M | H | 6 | Signal registry path-traversal + cloud-credential signals (`services/security/signal_registry.py:219-240`); enforced in policy fast-path (`services/policy/local_action_semantics.py`). | L        | —         |
| T-4 | Prompt-injection on Path B that bypasses screening                                            | M | M | 4 | 17 patterns at `sdk/common/injection_patterns.py:19-171`; OPA defence-in-depth.                                          | M        | OI-3 (pen test)        |
| T-5 | Cross-tenant read via crafted body                                                            | L | H | 3 | `tenant_id` only from `request.state.tenant_id` (`services/gateway/_helpers.py:47-62`); CHECK constraints on `org_id == tenant_id`.| L        | OI-5 (Postgres RLS)    |
| T-6 | Wire transfer above $100k routed through tooling without CFO approval                         | M | M | 4 | B1 closed 2026-06-18 — pattern detector, local fast-path, and Rego all aligned at $100k (`services/policy/local_action_semantics.py:101`, `services/policy/policies/action_semantics_deny.rego:501`, `services/gateway/escalation_patterns.py:39-52`). Regression test at `tests/policy/test_wire_threshold.py`. | L        | —         |
| T-7 | Compromise of the Merkle ed25519 signing key publishes a forged root                          | L | H | 3 | Detective: `prev_root_hash` chain at `services/audit/public_transparency.py:70-100`; verifier `tools/aegis_verify/verifier.py:78-90` step V5 detects post-hoc rewrites against any archived root. | M        | OI-4 (BYOK / KMS envelope hardening) |
| T-8 | Stolen API key acts as tenant until revocation                                                | M | M | 4 | Revocation set `acp:apikey:revoked` checked per request via `SISMEMBER` (`services/gateway/_mw_auth.py:31,81`); effective on next call. | L        | —         |
| T-9 | Kill-switch flipped by adversary disables enforcement for a tenant                            | L | H | 3 | Switch writes are role-gated; rehydration only reads engaged rows from `kill_switches` (`services/decision/main.py:59-99`); each toggle audited and Merkle-signed. | L        | —         |
| T-10| Behavior firewall outage degrades risk scoring                                                | M | M | 4 | Per-tenant degraded-mode policy (`block_high_risk` / `block_all` / `allow_with_audit`) covers the outage window.          | M        | OI-6 (SLO dashboard)   |

Scoring: L=1, M=2, H=3. Score = L × I. Tied scores are ordered by the gravity
of the impact rather than the score alone.

---

## 7. Open items

These are mitigations that are not yet implemented in code and are tracked
against named owners and dated targets. Each is referenced from the residual
column above.

| ID   | Open item                                                                                                 | Owner            | Target sprint    |
|------|------------------------------------------------------------------------------------------------------------|------------------|------------------|
| OI-1 | SOC2 Type II evidence package (controls + auditor-attested log retention).                                 | Compliance       | Track F1, sprint v2.0 + 90 days |
| OI-2 | DR drill for public Merkle bucket — verify reconstruction-from-RDS path and document RTO/RPO.              | SRE              | Track E1, in sprint |
| OI-3 | External penetration test on gateway, identity, and `/v1/messages` proxy.                                  | Security + vendor | Track F2, in sprint scope; report ≤ 8 weeks post-SoW |
| OI-4 | BYOK for the Merkle ed25519 signing key via customer-managed KMS envelope.                                 | Crypto + SRE     | Roadmap 2027-Q1  |
| OI-5 | Postgres row-level security on `audit_logs` and `usage` as defence-in-depth behind app-layer tenant scope. | Database + Sec   | Roadmap 2026-Q4  |
| OI-6 | Customer-facing SLO dashboard for behavior firewall availability.                                          | SRE              | Track E2, in sprint |

Items in *Roadmap* status are tracked against their named target in
`agies-bussiness.md` §11 (Roadmap Priorities). Items in *Track* status will
close inside this sprint and are visible in `SPRINT.md`.

---

## 8. Review cadence & change log

This file is reviewed at every major-version sprint (v2.0, v3.0…) and after
any incident classified Sev-0 or Sev-1 per `docs/operations/incident-response.md`.
Edits land via PR with at least two reviewers — one engineering, one security
— and the diff is captured in the next daily Merkle root by virtue of every
commit being audited at merge time.

| Version | Date       | Author          | Notes                                                                                       |
|---------|------------|-----------------|---------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | Security Eng    | First publication, paired with `agies-bussiness.md` v1.3.0. Closes audit finding C2.        |
