# ACP Threat Model

## Assets

| Asset | Confidentiality | Integrity | Availability |
|-------|----------------|-----------|--------------|
| Audit log chain | High — contains all agent decisions | **Critical** — tamper = undetectable policy bypass | High |
| Tenant isolation | Critical — cross-tenant read = customer data breach | Critical | Medium |
| Agent credentials (JWT) | Critical — stolen token = impersonation | High | Medium |
| Transparency roots | Medium | Critical — forged root = undetectable tampering | Medium |
| OPA policy bundles | Medium | Critical — altered policy = all decisions wrong | Medium |
| Billing / usage records | High | High — incorrect billing = financial harm | High |
| INTERNAL_SECRET | Critical | High | Low |

---

## Threat actors

| Actor | Capability | Motivation |
|-------|-----------|------------|
| Compromised agent token | Valid JWT, no shell/DB access | Exfiltrate data, invoke denied tools |
| Malicious tenant admin | API access to their own tenant | Cross-tenant data access, bypass rate limits |
| Network attacker (MITM) | Intercept unencrypted traffic | Steal tokens, forge responses |
| Insider (developer) | Full system access including DB | Tamper audit log, hide actions, steal customer data |
| Compromised container | RCE in one service | Lateral movement, read INTERNAL_SECRET, inject audit rows |

---

## STRIDE per service

### Gateway (primary enforcement boundary)

| Threat | Example | Control |
|--------|---------|---------|
| **S**poofing | Forged agent JWT | Ed25519 signature verification; 5-min expiry; Redis revocation list |
| **T**ampering | Modified request body after signing | Request body hash stored in audit row; receipt verification |
| **R**epudiation | Deny that a tool was called | Cryptographically signed receipt per execution; Merkle-chained audit |
| **I**nformation disclosure | Cross-tenant tool call via crafted body | Tenant ID extracted from JWT + body; mismatch = 403 |
| **D**enial of service | Request flood | Token bucket per tenant/agent/IP; global limiter; PgBouncer pool cap |
| **E**levation of privilege | Exploit OPA policy gap | OPA policy unit tests; `opa_fail_mode=closed` (deny on OPA failure) |

### Audit Service

| Threat | Control |
|--------|---------|
| Direct DB write (bypasses chain) | Audit writer is the only service with INSERT permission; no shared DB password for other services |
| Hash collision / preimage attack | SHA-256 with structured prefix; Merkle tree with daily root |
| Row deletion | `audit_logs` table has no DELETE grant; append-only enforced at Postgres role level |
| Transparency root forgery | Root signed with Ed25519; public key embedded in receipts; old keys retained in `transparency_historical_keys` so past roots verify post-rotation |

### Identity / Registry

| Threat | Control |
|--------|---------|
| Agent registration impersonation | Admin JWT required for `POST /agents`; agent secrets hashed with bcrypt |
| Token replay | JTI stored in Redis; `exp` enforced; revocation on logout |
| Brute-force agent secret | bcrypt cost factor 12; rate limited per-IP |

### Inference Proxy / Decision Service

| Threat | Control |
|--------|---------|
| Prompt injection via tool payload | Input sanitised before Groq call; output classification is statistical, not trust-based |
| Behavior signal manipulation | Signals are read-only aggregations over audit history; no caller can write scores directly |
| Decision service unavailability | Gateway `degraded_mode_policy` per tenant: `block_high_risk`, `block_all`, or `allow_with_audit` |

---

## Out of scope

The following are explicitly **not defended against** by ACP at this stage:

- **Compromised Postgres primary** — an attacker with psql superuser can truncate tables. Mitigation is Postgres access controls + encryption at rest (AWS RDS), not ACP.
- **Kubernetes control-plane compromise** — if the cluster is owned, container isolation fails. Use a hardened node pool and network policies.
- **Side-channel attacks** (timing, speculative execution) — outside scope for this application layer.
- **ACP admin credential compromise** — ACP enforces policy for agents, not for its own administrators. Separate your ACP admin credentials from agent credentials.
- **Supply-chain attacks on Python packages** — mitigated by pinned lockfile + Dependabot; not solved by ACP itself.

---

## Data flow and trust boundaries

```
External caller
     │  HTTPS (TLS 1.3)
     ▼
[nginx reverse proxy]
     │
     ▼
[Gateway]  ← enforces authn, authz, rate-limit, tool-guard, OPA
     │  INTERNAL_SECRET on X-Internal-Auth header
     ├──▶ [Registry]    agent/tool lookups
     ├──▶ [Identity]    tenant + quota lookups
     ├──▶ [Policy]      OPA decisions
     ├──▶ [Decision]    behavioral risk scores
     ├──▶ [Audit]       append-only event log
     ├──▶ [Billing]     usage metering
     └──▶ [Autonomy]    contract enforcement
```

Trust boundary: the gateway is the only service reachable from the public internet. All other services communicate only with each other over the internal Docker/K8s network.

---

## Key controls summary

| Control | Prevents |
|---------|---------|
| JWT + Ed25519 receipt | Spoofing, repudiation |
| Merkle chain + daily roots | Audit tampering |
| OPA policy + fail-closed | Privilege escalation, policy bypass |
| Tenant ID isolation (JWT + body) | Cross-tenant data access |
| Per-tenant rate limiting | DoS, credential stuffing |
| `degraded_mode_policy` per tenant | Behavioral firewall fail-open |
| append-only audit (no DELETE) | Insider log deletion |
| Transparency root chaining | Undetectable root forgery |
| INTERNAL_SECRET rotation | Stolen inter-service token reuse |
