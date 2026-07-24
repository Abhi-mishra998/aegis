# Agent Trust Fabric (ATF) v3.0
## The Verifiable Kernel — A Product Architecture, Not a Platform Fantasy

**Version:** 3.2
**Classification:** Product Architecture — Buildable Specification
**Owner:** Aegis Platform, ByteHubble
**Date:** 2026-07-21
**Supersedes:** ATF v3.1 / v3.0 / v2.0

**v3.2 revision note (final paper revision):** Incorporates second external review. Changes: differentiation restated as the bound artifact, not the Witness (§1); explicit product boundary sentence (§1); guarantee wording tightened to "evidence consistent with the action" (§1, §3); formal Security Invariants (§3.0) paired with Security SLOs (§12.3); attacker economics (§10); protocol-agnostic Gate statement (§5.1); versioned external formats (§7.4); guardrail vendors reframed as complements (§14.3); ADR appendix (Appendix C); deployment lifecycle (§14.5); operational detail moved to Appendix D. **The next revision of this system is a running prototype, not a v3.3.**

**Revision thesis:** v1.0 optimized for completeness. v2.0 optimized for prioritization but remained a platform written for a company with 10B requests/day. v3.0 optimizes for **the first paying deployment**. Every section in this document either ships in the kernel, is consumed from a standard, or is explicitly declared out of scope. Nothing here is decoration.

---

## Table of Contents

1. [The One-Paragraph Thesis](#1-the-one-paragraph-thesis)
2. [What Changed and Why: v2.0 → v3.0 Decision Log](#2-what-changed-and-why)
3. [The Kernel: Three Components, One Promise](#3-the-kernel)
4. [Identity: Consume, Don't Compete](#4-identity-consume-dont-compete)
5. [Component 1 — Capability Gate](#5-component-1--capability-gate)
6. [Component 2 — Execution Witness](#6-component-2--execution-witness)
7. [Component 3 — Execution Ledger](#7-component-3--execution-ledger)
8. [Regulatory Mapping: EU AI Act Article 12 & India DPDP](#8-regulatory-mapping)
9. [Trust Scoring v3: Cold-Start Honest](#9-trust-scoring-v3)
10. [Threat Model (Kernel Scope Only)](#10-threat-model)
11. [Failure Modes & Degradation](#11-failure-modes--degradation)
12. [Cost Model: A Benchmark Protocol, Not Fabricated Numbers](#12-cost-model)
13. [What We Deliberately Do Not Build](#13-what-we-deliberately-do-not-build)
14. [Buyer, Deployment, and Go-To-Market Shape](#14-buyer-deployment-gtm)
15. [Roadmap: Kernel → Product → Platform](#15-roadmap)
16. [Research Appendix (Formerly Tier 2)](#appendix-a-research)
17. [Version Comparison: v1 → v2 → v3](#appendix-b-version-comparison)
18. [Architecture Decision Records](#appendix-c-architecture-decision-records)
19. [Implementation Notes](#appendix-d-implementation-notes-operational-detail)

---

## 1. The One-Paragraph Thesis

An enterprise deploying autonomous agents in a regulated workflow cannot answer one question today: **"Prove what your agent actually did."** Application logs are mutable, agent self-reports are unverified claims, and observability traces are written by the same process they describe. ATF v3.0 is a deployer-owned kernel of three components — a **Capability Gate** that makes every consequential tool call pass an enforceable policy check, an **Execution Witness** that independently observes what the runtime actually did, and an **Execution Ledger** that binds intent, authorization, observation, and outcome into a tamper-evident, exportable, independently verifiable record. Identity is consumed from the emerging open standards stack (SPIFFE/SPIRE, IETF AIMS, OAuth 2.x/XAA, Entra Agent ID, Okta for AI Agents), not reinvented. The kernel maps line-by-line to EU AI Act Article 12 and India DPDP audit obligations. Everything else — behavioral ML, trust gradients, ZK proofs, TEEs, federated reputation — is either roadmap or research, and this document says which.

**Design law of v3.0+:** *Enforcement and audit are the same act.* A governance layer that observes logs after the fact is not governance. Every consequential action passes through a gate; the gate's decision and the witness's observation are the audit record. There is no separate "compliance mode."

**Product boundary (one sentence that ends category confusion):** ATF is **not** an AI firewall, SIEM, IAM, observability platform, or agent framework. It is a **runtime evidence layer** that integrates with all of those.

**The differentiator, stated precisely.** Any competent platform team can deploy eBPF + Falco + OpenTelemetry and *observe* execution — that is not the product, and pretending otherwise loses the first technical evaluation. What cannot be assembled from commodity tooling is the **cryptographic binding of authorization (Gate decision), independent observation (Witness verdict), and outcome into one canonically hashed, externally anchored, offline-verifiable artifact**, with lifecycle, escalation, and export semantics an auditor accepts. The Witness is a component. The bound artifact is the product.

**Guarantee wording (normative for all customer-facing material):** ATF does not claim to prove, in an absolute sense, "what the agent did." It proves that **the system observed evidence consistent — or inconsistent — with the claimed action**, under the stated trust boundary (§6.1). This wording is not hedging; it is the difference between a guarantee that survives contract review and one that doesn't.

---

## 2. What Changed and Why: v2.0 → v3.0 Decision Log

Every change below is a decision with a stated reason. This table is the contract between versions.

| # | v2.0 Position | v3.0 Decision | Reason |
|---|--------------|---------------|--------|
| 1 | Custom identity universe: `did:atf:v1`, proprietary Agent Passport format, custom capability token format | Identity **consumed** from SPIFFE/SVID + IETF AIMS composition + OAuth token exchange. Passport becomes a signed **profile document** referencing standard identities | Six major IAM vendors (Okta, Microsoft Entra, Ping, SailPoint, BeyondTrust, Snowflake) shipped agent identity in H1 2026. IETF AIMS (draft-klrc-aiagent-auth-00) composes WIMSE + SPIFFE/SPIRE + OAuth 2.0 as the reference stack. CSA guidance: interoperate with emerging standards, do not commit to proprietary frameworks. Competing here means competing with Microsoft on a services-funded budget. |
| 2 | Three-tier platform (T0/T1/T2) as the product | **Kernel** (3 components) as the product; T1 analytics as roadmap; T2 moved to Research Appendix | v2.0 was a platform for a hyperscaler. v3.0 is a product for a first customer. |
| 3 | Fabricated per-request cost table ($0.0003/token validation, etc.) | Cost table **deleted**. Replaced with a benchmark protocol producing measured numbers from a reference deployment | Unmeasured precision is worse than no numbers. One real benchmark beats twelve fabricated columns. |
| 4 | "Sybil resistance: 1000 fake agents costs 1000 TPMs" | Claim corrected. Cloud vTPMs and SEV-SNP attestations are near-free to mint per container. Sybil resistance now rests on **tenant-anchored issuance quotas + human-responsible binding**, not hardware scarcity | The v2.0 claim fails any competent red-team review. |
| 5 | "Ensemble verification converts probabilistic reasoning into verifiable consensus" | Renamed **Consistency Sampling** and honestly scoped: it detects *instability*, not *incorrectness*. Three consistent samples of flawed reasoning pass unanimously at 3× inference cost | A consistency check marketed as correctness verification is a liability in a security product. |
| 6 | Trust Engine with AutoML weights "retrained monthly on confirmed compromises" | Cold-start honest: **static weights + hard guards** at launch. Learned fusion is a Phase 3 feature gated on ≥6 months of production incident data | You cannot train on incidents you don't have. |
| 7 | Behavioral fingerprinting mandatory for T1 | Optional add-on, off by default | Crowded space (Lakera→Check Point, Robust Intelligence→Cisco, Protect AI→Palo Alto). Not the wedge. |
| 8 | 100M agents / 10B requests/day scale targets | Reference deployment target: **50 agents, 1M gated actions/day, single tenant** | Architecture for the customer you have, headroom for the customer you want. |
| 9 | MCP handled implicitly via TBOM | **MCP tool-call layer is the primary Gate interception point** | MCP is where agent actions actually happen in 2026 deployments, and MCP security remains the underfunded gap in the market. |
| 10 | 20 sections, everything load-bearing | 15 sections + 2 appendices; sections 13 and Appendix A explicitly list what is *not* built | Good architecture is subtraction. v3.0 finally practices it. |

---

## 3. The Kernel: Three Components, One Promise

**The promise (customer-facing, one sentence):**
> "For every consequential action mediated through Aegis, we produce one cryptographically bound record — authorization, independent observation, and outcome — that your auditor can verify without trusting you, your vendor, or the agent."

### 3.0 Security Invariants (The Backbone)

Every component in this document exists to uphold one of six invariants. Anything that upholds none of them gets cut. Their measurable form is the Security SLO set in §12.3.

```
I1  No Gate decision record → no execution.
    (Gate-mediated consequential actions; enforced by fail-closed
     C2/C3 behavior + default-deny egress, §3.2)

I2  No Witness evidence → no CORROBORATED verdict.
    Absence of observation is always UNOBSERVED — never silence,
    never assumed success.

I3  Ledger entries are immutable after external anchoring.
    Pre-anchor tampering is bounded by the anchor interval and
    detectable by chain verification.

I4  Every human approval binds exactly one gate_decision_id
    and expires. No blanket approvals exist.

I5  Every agent identity resolves to a live human owner,
    or the agent is QUARANTINED.

I6  Every hash and signature is computed over the canonical
    (RFC 8785) form. Two correct implementations cannot disagree.
```

### 3.1 Kernel Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CUSTOMER'S AGENT RUNTIME (untrusted from kernel's perspective)          │
│                                                                          │
│   LLM / Framework (LangGraph, CrewAI, custom)                            │
│        │                                                                 │
│        ▼  tool call (MCP / HTTP / SDK)                                   │
│  ┌──────────────────────┐                                                │
│  │  ① CAPABILITY GATE   │  policy check BEFORE execution                 │
│  │  (in-line proxy /    │  ALLOW │ DENY │ ESCALATE_HUMAN                 │
│  │   MCP middleware)    │  decision record → Ledger                      │
│  └──────────┬───────────┘                                                │
│             ▼  (only if ALLOW)                                           │
│      Tool / API / Database / External system                             │
│             │                                                            │
│  ┌──────────┴───────────┐                                                │
│  │  ② EXECUTION WITNESS │  independent observation of what               │
│  │  (sidecar; syscall + │  ACTUALLY happened: network, fs,               │
│  │   network + API tap) │  API response codes, payload hashes            │
│  └──────────┬───────────┘                                                │
│             ▼                                                            │
│  ┌──────────────────────┐                                                │
│  │  ③ EXECUTION LEDGER  │  binds: intent + authorization +               │
│  │  (Merkle-anchored,   │  observation + outcome                         │
│  │   append-only,       │  → tamper-evident, exportable,                 │
│  │   deployer-owned)    │    independently verifiable                    │
│  └──────────────────────┘                                                │
│                                                                          │
│  IDENTITY: consumed from SPIFFE/SPIRE, Entra Agent ID, Okta,             │
│  or AIMS-composed stack. ATF verifies; ATF does not issue.               │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3.2 The Kernel Invariant

For every consequential action **mediated through the Capability Gate**:

```
Ledger entry exists  ⟺  action was permitted to execute
```

No gate decision record → the gate did not allow it. No witness observation matching the claim → the claim is flagged `UNVERIFIED` and surfaced. This biconditional is the product. Everything else in this document serves it.

**Scope honesty:** the invariant covers Gate-mediated actions, not everything a workload could conceivably do. It therefore carries a **bypass-resistance deployment requirement**: agent runtime pods/hosts MUST have default-deny egress with the Gate proxy as the only permitted path to tool endpoints (Kubernetes NetworkPolicy, security groups, or host firewall). An agent that can reach the internet around the Gate makes the coverage claim false, and ATF's install-time checker verifies egress lockdown before the deployment is marked compliant. Unmanaged workloads outside the Gate's mediation are explicitly out of scope and are stated as such in customer-facing coverage claims.

### 3.3 Consequential Action Classification

Actions are classified at Gate configuration time, not per-request:

| Class | Examples | Gate | Witness | Ledger |
|-------|----------|------|---------|--------|
| **C0 — Read, reversible** | Search, read record, list files | Log-only (async) | Sampled (1–10%) | Batched entry |
| **C1 — Write, reversible** | Update record, send internal message | Inline check | Full | Full entry |
| **C2 — Write, hard to reverse** | Delete, external email, payment < threshold, PII export | Inline check + constraint eval | Full + payload hash | Full entry, sync anchor |
| **C3 — Irreversible / high-value** | Payment ≥ threshold, contract execution, production deploy | Inline + human escalation option | Full + payload hash + response capture | Full entry, sync anchor, receipt to human owner |

Classification is **deterministic, not judgment-based**. The compiler evaluates a predicate over typed action attributes; example default (tenant-tunable):

```
class(action) =
  C3 if irreversible == true
        OR financial_value >= high_value_threshold
        OR legal_commitment == true
  C2 if resource_classification >= CONFIDENTIAL
        OR financial_value > 0
        OR external_communication == true
        OR pii_touched == true
        OR mutation_reversibility == HARD
  C1 if mutation == true                     (reversible, internal)
  C0 otherwise                               (read, reversible)

Tie-break: highest matching class wins. Unknown/missing attribute → treat
as the more restrictive value (fail-toward-scrutiny).
```

Attributes come from the tool manifest (each tool declares `mutation`, `external`, `reversibility`) plus request inspection (amount fields, PII detectors, target resource labels). Classification lives in a versioned policy file (see §5.3), so "what counted as consequential in March" is itself auditable — and because the predicate is deterministic, two implementations classify identically.

---

## 4. Identity: Consume, Don't Compete

### 4.1 The Standards Reality (July 2026)

The identity layer is being settled by bodies and incumbents with more leverage than any startup:

- **IETF AIMS** (draft-klrc-aiagent-auth-00, March 2026): composes WIMSE + SPIFFE/SPIRE + OAuth 2.0 into the reference agent identity stack.
- **NIST NCCoE** concept paper (Feb 2026): names MCP, OAuth 2.0/2.1, OIDC, SPIFFE/SPIRE, and SCIM as candidate standards; demonstration project to follow.
- **Vendor products shipped H1 2026:** Microsoft Entra Agent ID (OAuth/OIDC tokens, Conditional Access, Copilot Studio native), Okta for AI Agents + Cross-App Access (XAA, 25+ integrations), Auth0 for AI Agents, Ping, SailPoint, BeyondTrust, Snowflake Horizon.
- **SCIM agent extension** (Okta-authored IETF draft): agents provisioned in the directory like employees, with owners and lifecycle.
- **AuthZEN** (OpenID Authorization API 1.0, approved Jan 2026): the standard PDP interface.

### 4.2 ATF v3.0 Identity Posture

**ATF verifies identity; ATF does not issue identity.**

```
ACCEPTED IDENTITY INPUTS (any one, in order of preference):
  1. SPIFFE SVID          (workload-native; K8s/SPIRE deployments)
  2. Entra Agent ID token (Microsoft-shop enterprises)
  3. Okta/Auth0 agent token via XAA / token exchange
  4. Generic OAuth 2.1 client credential w/ DPoP     (fallback)

ATF REQUIREMENTS ON TOP (the "Aegis Profile"):
  - human_responsible: a directory-resolvable human owner (SCIM ref)
  - tenant issuance quota: max concurrent agent identities per tenant
  - provenance block: model ref, prompt hash, tool manifest hash
    (the useful core of v2.0's Passport/DNA, minus the parallel universe)
```

### 4.3 The Aegis Profile (Passport, Reborn as an Overlay)

The v2.0 Agent Passport had the right *content* and the wrong *sovereignty*. v3.0 keeps the content as a signed overlay referencing standard identities:

```json
{
  "aegis_profile_version": "3.0",
  "subject": {
    "spiffe_id": "spiffe://tenant-a.example/agent/finance-001",
    "idp_ref": "entra:appid:9f2c...|okta:agent:0oa8...",
    "scim_ref": "scim://tenant-a/Agents/finance-001"
  },
  "human_responsible": "scim://tenant-a/Users/security-lead",
  "provenance": {
    "model_ref": "registry://tenant-a/models/gpt-4o/2026-07-15",
    "prompt_template_hash": "sha256:8b2c...",
    "tool_manifest_hash": "sha256:9d1e...",
    "container_image_digest": "sha256:4f5a...",
    "sbom_ref": "sbom://tenant-a/finance-agent/2.3.1"
  },
  "gate_policy_ref": "policy://tenant-a/finance/v17",
  "action_class_max": "C3",
  "signatures": {
    "tenant": "Ed25519(tenant-root, sha256(profile))"
  }
}
```

**What was cut from the v2.0 Passport and why:** live trust score (belongs in the scoring service, not a static document), cost budget (belongs in the Gate policy), behavioral baseline (optional add-on), the four separate BOM refs beyond SBOM (MBOM/PBOM/TBOM collapse into `provenance` — same content, less ceremony), dual ATF-anchor countersignature (a trust root we don't have the standing to operate yet; single tenant signature + Ledger anchoring provides tamper evidence).

### 4.4 Sybil Resistance, Corrected

v2.0 claimed hardware scarcity (one TPM per agent). In cloud reality, vTPMs are free. v3.0's honest defenses:

1. **Issuance quota:** tenant root signs at most N concurrent Aegis Profiles; N is a contractual/config limit.
2. **Human-responsible binding:** every profile resolves to a directory human; orphaned agents are auto-quarantined by the Gate.
3. **Ledger-visible birth:** profile creation is itself a C2 ledger event. A burst of new identities is an anomaly visible in the export any auditor sees.

This does not make Sybils impossible. It makes them *quota-bounded, attributable, and evident* — which is what a governance product can honestly promise.

---

## 5. Component 1 — Capability Gate

### 5.1 Placement

**Gate interception occurs at the tool boundary, irrespective of protocol.** MCP is the primary implementation because it is the dominant tool interface in 2026; the decision flow, records, and invariants are protocol-agnostic and survive whatever succeeds MCP. Current interception points:

- **MCP middleware** (primary): the Gate runs as an MCP proxy between the agent runtime and MCP servers. Every `tools/call` passes through it. This also gives ATF a native answer to MCP-specific threats (tool poisoning, rogue MCP servers, schema drift) — the market's most underfunded surface.
- **HTTP egress proxy** (secondary): for non-MCP tool calls.
- **SDK shim** (tertiary): drop-in wrapper for direct API clients.

**Per-environment deployment** (Kubernetes, ECS/Fargate, VMs, bare metal, serverless — each with its egress-lockdown mechanism) is an operational concern, maintained as a matrix in **Appendix D**. The architectural requirement is identical everywhere: **the Gate is the only egress path** (I1), and the serverless case is stated honestly — no co-located Witness means all verdicts are UNOBSERVED, and the export says so.

### 5.2 Decision Flow

```
tool_call(agent_identity, tool, args)
  │
  ├─ 1. Verify identity (SVID / IdP token / OAuth)          [~1–3 ms typical]
  ├─ 2. Resolve Aegis Profile; check human_responsible live
  ├─ 3. Classify action (C0–C3) from versioned policy
  ├─ 4. Evaluate constraints (OPA/Rego, AuthZEN-compatible PDP)
  │       amount, vendor allowlist, hours, geo, daily count,
  │       delegation depth, PII flags
  ├─ 5. Decision: ALLOW │ DENY │ ESCALATE_HUMAN
  ├─ 6. Write Gate Decision Record → Ledger   (sync for C2/C3)
  └─ 7. If ALLOW: forward call; tag with gate_decision_id
```

### 5.3 Policy: Compiled, Versioned, Auditable

The v2.0 Policy Compiler survives — it was a good idea — with narrowed targets (no ZK circuit backend in the kernel):

```
Human policy (English, reviewed)
   │  compile
   ▼
Formal constraint set (typed, testable)
   │  emit
   ├─▶ OPA Rego bundle          (Gate enforcement)
   ├─▶ Action classification map (C0–C3 assignments)
   └─▶ Policy manifest hash      (recorded in every Ledger entry)
```

Example Rego (deployable as-is):

```rego
package aegis.gate.finance

default decision := "DENY"

decision := "ALLOW" {
  input.action == "purchase_order.create"
  input.args.amount_usd <= data.limits.auto_approve_usd
  input.args.vendor in data.vendors.allowlist
  time.clock([input.ts, "Asia/Kolkata"])[0] >= 9
  time.clock([input.ts, "Asia/Kolkata"])[0] < 18
  count(data.today[input.agent_id]) < data.limits.daily_tx
}

decision := "ESCALATE_HUMAN" {
  input.action == "purchase_order.create"
  input.args.amount_usd > data.limits.auto_approve_usd
  input.args.amount_usd <= data.limits.hard_max_usd
}
```

### 5.4 Policy Testing (New in v3.1)

Enterprises do not deploy authorization policies they cannot test. The compiler toolchain ships with:

- **Unit tests:** each compiled rule carries assertion cases (`this request → ALLOW`, `this one → DENY`) run in CI; a policy bundle without passing tests cannot reach PUBLISHED.
- **Simulation (replay):** evaluate a candidate policy against the tenant's own historical ledger entries — "under v18, 3.2% of last month's ALLOWs become DENYs; here are the 40 affected workflows." This is a compounding moat: the ledger makes policy simulation trivially realistic.
- **Shadow mode:** candidate policy evaluates in parallel with the active one on live traffic; divergences are ledgered but the active policy decides. Promotion requires N days of shadow with divergence below threshold.
- **Dry-run mode:** whole-Gate mode for initial onboarding — everything ALLOWs, everything is classified and ledgered, producing a baseline before enforcement is switched on.
- **Coverage report:** which rules fired in the last 30 days, which tools/action classes have no rule other than default — dead rules and coverage gaps surfaced explicitly.

### 5.5 Policy Lifecycle (Kept from v2.0, Trimmed)

The DRAFT → PUBLISHED → ACTIVE → DEPRECATED → RETIRED state machine survives intact — it solved a real problem. Kernel simplifications: propagation window 5 minutes; mid-flight actions complete under the policy version stamped in their Gate Decision Record (the record carries `policy_manifest_hash`, so "which rules applied" is never ambiguous); rollback is a config flip with a mandatory C3 ledger event.

### 5.6 Delegation (Hard-Coded, Unchanged from v2.0)

The five delegation rules were among the best content in v2.0 and carry forward verbatim: scope subset by intersection, hard depth limit of 2, delegated expiry ≤ min(delegator expiry, 1h), cascade revocation from root, full chain present in every request. Delegated tokens ride standard OAuth token exchange (RFC 8693) rather than a custom format.

### 5.7 Human Escalation — Fully Specified (New in v3.1)

`ESCALATE_HUMAN` is an operational protocol, not a keyword:

```
Escalation object:
  approvers:        ordered list of SCIM user/group refs (from policy)
  quorum:           1 (default) | N-of-M
  channels:         canonical webhook; adapters for chat, email,
                    paging, and ITSM systems (adapter list: Appendix D)
  timeout:          policy-set, default 30 min
  on_timeout:       DENY (default) | route to fallback approver group
                    — never ALLOW on timeout
  approval_expiry:  an approval authorizes exactly one gate_decision_id
                    and expires in 15 min if the action hasn't executed;
                    no blanket or reusable approvals
  conflict rule:    any explicit DENY from an approver overrides
                    pending/later APPROVEs (deny-wins)
  identity:         approver authenticates via tenant IdP (OIDC);
                    approval is signed and ledgered as part of the entry
```

The approval itself becomes `outcome.human_verification` in the ledger entry — approver identity, timestamp, channel, and signature. Auditors see not just that a human approved, but *which* human, *when*, and that the approval bound to *this specific action*.

---

## 6. Component 2 — Execution Witness

### 6.1 Principle and Trust Boundary

**The agent's claim is not evidence. The Gate's ALLOW is not evidence that the action occurred. Only independent observation is evidence.**

Sharpened: *traditional observability records what software says happened; the Execution Witness records independently observable side effects. The distinction matters because an untrusted process cannot reliably attest to its own behavior.*

The Witness runs in a separate container (sidecar) with no shared trust with the agent runtime. It cannot be disabled by the agent process, and its absence is itself a recorded, alertable state.

**Threat boundary (normative, stated so nobody over-reads the guarantee):**

> The Witness assumes the **host kernel and container runtime are trusted**. It defends against a compromised or dishonest *agent process*, not a compromised *host*. An attacker with root on the host can kill the Witness, tamper with eBPF probes, or falsify kernel-level events — this is **out of kernel scope**. Two properties bound (but do not eliminate) that risk: (1) Witness heartbeat loss flips all subsequent verdicts to `UNOBSERVED` and raises an alert — silence is visible, not exploitable; (2) already-anchored ledger batches cannot be retroactively rewritten, so host compromise taints evidence only forward from the compromise, bounded by the anchor interval. Customers whose threat model includes malicious hosts should combine ATF with confidential computing (SEV-SNP/TDX) or external attestation — see Appendix A; ATF does not claim that protection today.

### 6.2 Observation Sources (Kernel Scope)

| Source | Mechanism | What it proves |
|--------|-----------|----------------|
| Network egress | eBPF socket tracing + TLS SNI / connection metadata | Agent actually contacted claimed endpoint |
| API tap | Gate-forwarded response codes + response payload hash | Claimed operation returned claimed status |
| Filesystem | eBPF file open/write/unlink events | Local side effects match claims |
| Process | exec/fork events | No unauthorized child processes |
| Resource | CPU/mem/net counters | Gross anomaly detection (crypto-mining, exfil bursts) |

**Cut from v2.0 kernel scope:** memory heap inspection and full packet payload capture (high overhead, privacy hazard, marginal evidentiary value for the compliance buyer). Payload *hashes* at the API tap provide integrity binding without retention risk.

### 6.3 Verification Semantics — Three Honest Verdicts

For each C1–C3 action, the Witness emits exactly one verdict:

- **`CORROBORATED`** — observations are consistent with the claim (DELETE request seen, 200 observed, target matches).
- **`CONTRADICTED`** — observations conflict with the claim (agent reported success; no matching network event, or non-2xx observed). → automatic flag, optional automatic Gate lockout for the agent.
- **`UNOBSERVED`** — Witness was degraded or the action class wasn't instrumented. Never silently upgraded to corroborated.

This trichotomy is deliberately honest: the Witness proves *consistency of observable side effects with claims*. It does not prove the agent's reasoning was sound. That honesty is a selling point in front of auditors, not a weakness.

### 6.4 Witness Attestation Record

```json
{
  "attestation_version": "3.0",
  "gate_decision_id": "gd_01J3ZK...",
  "claim": "DELETE record 12345 in crm.accounts",
  "verdict": "CORROBORATED",
  "evidence": [
    {"type": "net", "detail": "TLS to crm.internal:443, 14:02:11.032Z"},
    {"type": "api", "detail": "DELETE /records/12345 → 200", "resp_hash": "sha256:..."},
    {"type": "fs",  "detail": "audit journal append observed"}
  ],
  "witness_id": "spiffe://tenant-a.example/witness/node-7",
  "signature": "Ed25519(witness-key, sha256(record))"
}
```

### 6.5 Deployment Modes (Simplified from v2.0)

| Mode | Overhead (to be benchmarked, §12) | Default for |
|------|-----------------------------------|-------------|
| Sidecar, same pod | target < 10% CPU | All kernel deployments |
| None (C0 sampling only) | ~0 | Read-only agent fleets |

The v2.0 "separate VM" and "remote witness cluster" modes move to roadmap; they add operational surface the first ten customers don't need.

---

## 7. Component 3 — Execution Ledger

### 7.1 The Ledger Entry — The Atomic Unit of the Product

One entry binds four things that today live in four unreliable places:

```json
{
  "entry_version": "3.0",
  "entry_id": "el_01J3ZK7Q...",
  "ts": "2026-07-21T14:02:11.101Z",

  "intent": {
    "agent": "spiffe://tenant-a.example/agent/finance-001",
    "aegis_profile_hash": "sha256:...",
    "claim": "delete duplicate CRM record 12345",
    "action_class": "C2"
  },

  "authorization": {
    "gate_decision_id": "gd_01J3ZK...",
    "decision": "ALLOW",
    "policy_manifest_hash": "sha256:policy-v17...",
    "constraints_evaluated": ["record_scope", "daily_delete_quota"],
    "delegation_chain": []
  },

  "observation": {
    "witness_attestation_id": "wa_01J3ZK...",
    "verdict": "CORROBORATED",
    "witness_sig": "Ed25519(...)"
  },

  "outcome": {
    "status": "COMPLETED",
    "response_hash": "sha256:...",
    "human_verification": null
  },

  "chain": {
    "prev_entry_hash": "sha256:...",
    "merkle_leaf": "sha256:...",
    "anchor_batch": "mb_2026-07-21T14:05Z"
  }
}
```

### 7.2 Tamper Evidence & Independent Verifiability

The audit-failure pattern this design answers directly: application logs are mutable, database records can be edited by anyone with access, and assurances are not evidence. Therefore:

0. **Canonicalization (normative):** all hashing and signing operates on **RFC 8785 JSON Canonicalization Scheme (JCS)** serializations of entries and attestations — never on raw JSON strings. Key ordering, whitespace, number formatting, and Unicode normalization are therefore deterministic across implementations; two independent implementations of `aegis-verify` MUST produce identical hashes for the same logical entry. (CBOR canonical encoding is the reserved compact alternative for high-volume streams; a stream declares its encoding in its header and never mixes.)
1. **Append-only store**, hash-chained per tenant stream.
2. **Merkle batching** every N seconds/entries; batch roots signed by the deployer's key.
3. **External anchoring** (configurable): batch roots published to a customer-chosen external witness — an S3 object-lock bucket, an RFC 3161 timestamping authority, or a public transparency log. The point: verification does not require trusting Aegis or the deployer's ops team.
4. **Open verifier:** a standalone, open-source CLI (`aegis-verify`) that takes an export bundle + anchor references and validates the chain offline. The auditor runs it; we never ask to be trusted.

### 7.3 Regulator Export

One command produces an export bundle: JSON-lines entries for a time range, the Merkle proofs, anchor references, the policy manifests in force during the range, and a human-readable summary (period of use, agent identities, action classes, escalations, contradictions). Retention default: **6 months minimum, configurable upward** — matching Article 12/19 log-retention floors.

---

### 7.4 Versioned External Formats (New in v3.2)

Everything an external party consumes carries a semver and a compatibility rule — not just policies, profiles, and ledger entries:

| Format | Consumed by | Compatibility rule |
|--------|-------------|--------------------|
| Ledger Entry schema | Verifier, SIEM integrations, auditors | Minor = additive only |
| Witness Attestation schema | Verifier, SOC tooling | Minor = additive only |
| Aegis Profile | Gate, IdP adapters | Minor = additive only |
| Export Bundle format | Regulators, auditors | Majors supported ≥ 24 months |
| `aegis-verify` protocol | Anyone, offline | Verifier refuses unknown *majors* rather than guessing |
| Policy manifest format | Compiler, Gate | Pinned by hash in every entry (§7.1) |

Hash and signature verification is schema-agnostic by design — canonical-form hashing (I6) covers all fields whether or not a verifier semantically understands them — so an older verifier can still prove *integrity* of newer entries even when it cannot *interpret* new fields. Semantic interpretation is what versions gate. Every artifact self-declares its schema version in a signed field.

## 8. Regulatory Mapping: EU AI Act Article 12 & India DPDP

### 8.1 The Forcing Function — Stated Honestly

Annex III high-risk obligations take effect **2 August 2026**. The Digital Omnibus package has proposed delaying Annex III enforcement, possibly to December 2027; Council and Parliament adopted negotiating positions in March 2026 and trilogues are underway — but nothing has passed, so August 2026 remains the enforceable date. Penalties for logging-related violations reach €15M or 3% of worldwide turnover (higher tiers reach €35M/7%), with Article 99 proportionality for SMEs.

**Strategic posture:** sell the pain ("you cannot prove what your agents did"), not the date. The pain survives an Omnibus delay; a pitch built on a deadline does not.

### 8.2 Article 12 Requirement → Kernel Mapping

| Article 12 / auditor requirement | Kernel answer | Artifact |
|----------------------------------|---------------|----------|
| Automatic recording of events over system lifetime; not toggleable by developers | Gate is in-line: no gate, no execution. Logging is enforcement, not an option | Gate Decision Record |
| Structured, complete records: timestamp, identity, action, input, output, context | Ledger entry schema (§7.1) | Ledger entry |
| Tamper-evident via cryptographic measures, not access controls | Hash chain + Merkle + external anchoring | Anchor batch + proofs |
| Independently verifiable without relying on the provider's assertion | Open-source offline verifier | `aegis-verify` bundle |
| Retained ≥ 6 months | Retention policy enforced at store level | Retention config, itself ledgered |
| Exportable, retrievable format for national authorities | One-command export | Export bundle |
| Traceability enabling output verification | Witness verdict binds claim↔observation | Witness attestation |
| Human oversight interpretability (Art. 14 adjacency) | ESCALATE_HUMAN path + human receipts on C3 | Outcome.human_verification |

### 8.3 India DPDP Alignment

The same ledger serves DPDP audit posture: PII-touching actions are C2+ by default classification, payload hashes avoid storing personal data in the ledger itself, and the export bundle demonstrates purpose-bound processing by agents. (Detailed DPDP mapping is a sales-collateral document, not an architecture concern.)

---

## 9. Agent States, Not Trust Scores

### 9.1 The v3.1 Decision: No Numeric Score

v2.0 had a learned trust score; v3.0 had a static one. v3.1 removes the number entirely. A numeric trust value invites calibration debates ("why 0.73?"), implies a precision the inputs don't have, and adds nothing a deterministic state machine doesn't. Successful security systems run on **deterministic policy decisions plus evidence**.

Every agent is in exactly one state, derived from rules any operator can read:

```
VERIFIED     identity valid ∧ human_responsible resolvable
             ∧ no CONTRADICTED verdict in 7d
             ∧ unobserved_ratio_7d ≤ threshold
             → full policy-permitted action classes

RESTRICTED   identity valid, but unobserved_ratio_7d > threshold
             OR escalation_ratio_30d > threshold
             → C0/C1 only; C2/C3 auto-escalate to human

QUARANTINED  any CONTRADICTED verdict in 24h
             OR orphaned human_responsible
             → all actions DENY pending human review;
               release requires a ledgered human decision

UNKNOWN      new agent, < N ledgered actions, or identity
             not yet resolvable
             → C0 only until profile completes
```

State transitions are themselves ledger events. Deny reasons cite the rule, not a number: "QUARANTINED: contradiction wa_01J3ZK at 14:02Z" survives an audit; "score fell below 0.4" does not.

### 9.2 Learned Analytics — Gated, Not Promised

Learned models return only as **Phase 3 advisory analytics** (anomaly ranking for SOC triage), never as the deciding authority, and only after ≥ 6 months of production ledger data including ≥ 1 confirmed incident class and red-team results. Decisions remain deterministic states + policy forever; ML may *suggest* a state review, not *set* a state. Until the data exists, shipping a "learned" model would mean shipping a model trained on nothing — v3.1 declines to pretend otherwise.

### 9.3 Consistency Sampling (Renamed, Rescoped)

For C3 actions only, and only when the customer enables it: sample the agent's plan 3× and require 2/3 constraint-consistency before the Gate forwards. Documented property: **detects reasoning instability; does not detect consistent error.** Cost: 3× inference on C3 planning steps only (typically < 1% of actions). This is an integrity speed bump, honestly labeled.

---

## 10. Threat Model (Kernel Scope Only)

**Attacker economics — the buyer's real question.** With mutable application logs, falsifying the record costs one `UPDATE` statement by anyone with database access. Under the kernel, an attacker who wants an unauthorized action to appear authorized *and* corroborated must simultaneously defeat **tenant identity issuance, Gate mediation (through default-deny egress), host integrity (to subvert the Witness), and the external anchor** — four independent systems with different owners — within one anchor interval. The kernel does not make attacks impossible; it changes the attacker's cost from trivial-and-silent to multi-system-and-visible: every partial failure surfaces as CONTRADICTED or UNOBSERVED rather than as clean fake evidence.

| Tactic | Technique | Kernel mitigation | Residual risk (stated) |
|--------|-----------|-------------------|------------------------|
| Agent impersonation | Stolen/forged identity | Standard-stack verification (SVID/IdP) + profile hash pinning | Compromised IdP is out of scope; inherit IdP's guarantees |
| Privilege escalation | Over-broad tool calls | Gate constraint evaluation, delegation subset rule | Policy authoring errors — mitigated by compiler tests, not eliminated |
| Delegation abuse | Depth/scope expansion, token theft | Hard depth 2, scope intersection, cascade revocation, short expiry | Sub-1h theft window remains |
| Execution fraud | Agent lies about actions | Witness trichotomy; CONTRADICTED → lockout | Actions with no observable side effects (pure reasoning) are unverifiable — stated openly |
| MCP surface | Tool poisoning, rogue server, schema drift | Gate pins tool manifest hash; unlisted tool/schema mismatch → DENY | Malicious-but-manifest-matching tool behavior needs Witness + policy, not manifest pinning |
| Audit tampering | Log editing, deletion, backdating | Hash chain + external anchor + offline verifier | Wholesale suppression before first anchor window (bounded to anchor interval) |
| Sybil / identity flooding | Mass agent minting | Issuance quota + human binding + ledgered births | Quota-compliant slow farming — visible, not blocked |
| Collusion / multi-agent composition | Split a violating action across agents | **Roadmap (Phase 3)** — interaction graph; kernel offers per-agent quotas only | Explicitly deferred; do not claim coverage |
| Host / kernel compromise | Root attacker kills or tampers with Witness, eBPF, or kernel | **Out of kernel scope** (§6.1 trust boundary). Bounded by: heartbeat loss → UNOBSERVED + alert; anchored batches immune to retroactive rewrite | Forward-tainted evidence from compromise time until detection; confidential computing (Appendix A) for buyers who need more |
| Gate bypass | Agent reaches tool endpoints around the proxy | Default-deny egress requirement + install-time lockdown checker (§3.2) | Misconfigured egress = false coverage; checker mitigates, doesn't eliminate ops error |

The last column exists because a security product that states its residual risks is credible in procurement; one that claims completeness is not.

---

## 11. Failure Modes & Degradation

The v2.0 degradation ladder was strong; v3.0 scopes it to the kernel:

| Component fails | Detection | Behavior | Blast radius |
|-----------------|-----------|----------|--------------|
| Gate (PDP/OPA) | Health check | **Fail-closed for C2/C3** (deny), fail-open for C0 with flag; cached decisions ≤ 5 min for C1 | Consequential actions pause — by design |
| Witness | Missing heartbeat | Actions proceed; verdicts = UNOBSERVED; unobserved_ratio guard tightens agent to C0/C1 | Evidence quality degrades visibly, never silently |
| Ledger write path | Queue depth | Local durable buffer (append-only, signed) → replay on recovery; C3 actions block if buffer unsafe | Bounded evidence latency |
| External anchor | Anchor timeout | Batches queue; verifier reports "anchored late" honestly | Tamper-evidence window widens, disclosed |
| Identity provider | Token validation errors | Cached SVID validity ≤ SVID lifetime; new agents denied | New sessions pause |

**Design rule:** every degradation is *visible in the ledger itself*. An auditor reading the export sees when the system was degraded. Governance that hides its own outages is not governance.

---

## 12. Cost Model: A Benchmark Protocol, Not Fabricated Numbers

v2.0's per-request cost tables are deleted. In their place, the measurement that produces real numbers:

### 12.1 Reference Deployment — Fully Defined Workload

A benchmark without a workload definition is a marketing number. The reference workload is specified so results are reproducible and comparable:

- **Scale:** 1 tenant, 50 agents; 20 concurrent at steady state, burst to 50
- **Volume:** 1M gated actions/day (~11.6/s avg, 60/s p99 burst)
- **Action-class mix:** 85% C0, 10% C1, 4.5% C2, 0.5% C3
- **Payload sizes:** request p50 2 KB / p99 32 KB; response p50 8 KB / p99 128 KB
- **Downstream tool latency** (simulated): p50 300 ms / p99 2 s — so Gate overhead is reported as *added* latency, isolated from tool latency
- **Policy complexity:** 60 compiled rules across 12 tools; 3 constraint evaluations avg per C1+, 8 per C3; one delegation chain (depth 1) on 5% of calls
- **Escalations:** 0.2% of actions hit ESCALATE_HUMAN (approval simulated at 5 min)
- **Ledger state at measurement:** pre-loaded with 30 days of history (~30M entries) so writes, chain verification, and export are measured against a realistic store, not an empty database
- **Anchor cadence:** 60 s batches to S3 object-lock
- **Infra:** 1× Gate proxy (2 vCPU/4 GB), Witness sidecars per agent pod, Postgres 16 (4 vCPU) + object storage for Ledger

Any published number cites this workload ID; deviations get a new workload ID. No cherry-picking.

### 12.2 What Gets Measured (and Published)

| Metric | Target (hypothesis) | Measured |
|--------|--------------------:|----------|
| Gate added latency, C1 (p50/p99) | < 5 ms / < 20 ms | *TBD — benchmark* |
| Gate added latency, C2/C3 sync-ledger (p99) | < 50 ms | *TBD* |
| Witness CPU overhead per agent pod | < 10% | *TBD* |
| Ledger write throughput per node | > 5k entries/s | *TBD* |
| Infra cost per 1M gated actions | < $5 | *TBD* |
| Export generation, 6-month range | < 10 min | *TBD* |

The "Target" column is a stated hypothesis, labeled as such. The first sales deck cites the *Measured* column or cites nothing. This is the difference between a cost model and a cost costume.

### 12.3 Security SLOs (New in v3.2 — Acceptance Criteria)

Performance targets say *fast*; these say *safe*. Each SLO is the measurable form of an invariant (§3.0), computed continuously and reported inside the export itself:

| SLO | Invariant | Measurement |
|-----|-----------|-------------|
| 100% of C2/C3 executed actions carry a Gate decision record | I1 | Witness-observed egress reconciled against gate records |
| 0 silent Witness failures — every gap surfaces as UNOBSERVED + alert | I2 | Heartbeat continuity audit |
| 100% of anchor batches verify against the external anchor | I3 | `aegis-verify` full-chain runs, scheduled |
| 100% of human approvals ledgered and single-action-bound | I4 | Approval↔decision join, no orphans |
| 100% of active agents resolve to a human owner or sit in QUARANTINED | I5 | Daily SCIM reconciliation |
| Verifier determinism: same bundle → byte-identical verdict across implementations | I6 | Cross-implementation CI check |

An SLO breach is itself a **ledgered C3 event** with a human receipt — the system's failures are recorded with the same rigor as the agents' actions.

---

## 13. What We Deliberately Do Not Build

Subtraction, made binding. Each line names the reason and the substitute.

| Not built | Reason | What fills the need |
|-----------|--------|---------------------|
| Identity issuance / DID method / custom token format | Commoditized by Okta, Entra, Ping, SailPoint et al.; standards converging on AIMS/WIMSE/SPIFFE/OAuth | Consume theirs; Aegis Profile overlay (§4.3) |
| Behavioral ML fingerprinting (as core) | Crowded, acquired space (Lakera/Check Point, Robust Intelligence/Cisco, Protect AI/Palo Alto); high FP ops burden | Optional add-on post-kernel; contradiction/escalation stats cover 80% of buyer need |
| ZK proofs | ~$0.50+/proof economics; no customer requirement yet; formal circuit-policy equivalence unsolved | Appendix A; TEE/redaction if a regulated buyer forces it |
| TEE-based execution | 15%+ overhead; ops complexity; not required by Article 12 | Container isolation + Witness |
| Federated reputation / EigenTrust | Requires a multi-tenant network we don't have | Local stats only |
| Differential privacy sharing | No cross-org sharing exists to protect | N/A until Phase 4 |
| Full packet capture | Privacy hazard, storage cost | Payload hashes at API tap |
| Custom A2A protocol (ATFP) | Agents already talk MCP/HTTP; a new envelope is adoption friction | Gate rides existing transports |
| Global trust root / ATF Anchor consortium | We lack the standing; premature | Customer-chosen external anchors (§7.2) |

---

## 14. Buyer, Deployment, and Go-To-Market Shape

### 14.1 The Buyer (Absent from v1.0 and v2.0 Entirely)

- **Primary:** Head of Security / CISO or Head of Compliance at a mid-size enterprise or GCC deploying agents in an Annex III-adjacent workflow (finance ops, HR screening, lending support, healthcare admin) — extraterritorial EU AI Act exposure included.
- **Trigger:** internal audit or customer due-diligence asks "show us your AI agent audit trail" and the honest answer is a LangSmith screenshot of mutable traces.
- **Champion:** the platform engineer who has to produce that answer.

### 14.2 Deployment Promise

- **Day 1:** MCP proxy in front of one agent fleet; default C-classification; ledger live.
- **Week 1:** policy compiled from the customer's written SOPs; escalation wired to their approval channel (email/Slack/ITSM).
- **Deliverable:** first `aegis-verify`-checkable export in the customer's hands within 7 days.

### 14.3 Competitive Position — A Fair Table

A comparison table that straw-mans competitors gets discounted the moment a buyer knows better. This one is honest about what each layer does, which makes the gap it shows credible:

| Capability | IAM (Entra Agent ID, Okta/Auth0 for AI Agents) | Observability (LangSmith, Langfuse, Arize) | AI runtime security (guardrail/firewall vendors) | **Aegis kernel** |
|------------|------------------------------------------------|--------------------------------------------|--------------------------------------------------|------------------|
| Agent identity & lifecycle | **✓ strong** (their core) | ✗ | Partial | ✓ **consumed** from them, not rebuilt |
| Inline enforcement at tool call | Partial (conditional access at auth time, not per-action constraints) | ✗ (observes, doesn't gate) | ✓ inline — but for a **different problem**: prompt/content safety (injection, toxicity, leakage patterns), not business-constraint authorization (amounts, vendors, quotas, approvals) | ✓ per-action business constraints, fail-closed |
| Record of what happened | Auth logs | ✓ rich traces — but **self-reported** by the instrumented process | Alert logs | ✓ Gate decision + **independent Witness observation** |
| Tamper evidence | Access-controlled logs | Mutable stores | Mutable stores | ✓ hash chain + Merkle + **external anchoring** |
| Independent verifiability (auditor needn't trust vendor/deployer) | ✗ | ✗ | ✗ | ✓ open-source offline verifier |
| Claim-vs-reality verdict (CORROBORATED/CONTRADICTED) | ✗ | ✗ | ✗ | ✓ — the differentiator |

The honest summary for a buyer: *keep your IAM, keep your observability, keep your AI firewall — Aegis is the evidence layer none of them provides.* Guardrail vendors in particular are **complements, not competitors**: they decide whether *content* is safe; the Gate decides whether the *action* is authorized; the Ledger proves both decisions happened. Misrepresenting them invites correction in front of the buyer — accuracy is the stronger sales position. And when the CISO asks "why can't my team build this with eBPF + Falco + OTel?", the answer is §1: observation is commodity; the **bound, anchored, offline-verifiable artifact** — with escalation semantics, lifecycle, and export formats an auditor accepts — is roughly two engineer-years of work that isn't their roadmap.

### 14.4 Pricing Anchor

Price against **audit-failure risk and compliance spend**, not per-request infrastructure: per-agent-fleet annual license + per-regulated-workflow module. Per-request pricing invites the customer to compare you to a log pipeline; compliance pricing invites them to compare you to a fine and a failed enterprise deal.

### 14.5 Deployment Lifecycle (New in v3.2)

Regulated customers ask about the exit before the entrance. The full lifecycle, each transition itself a ledgered event:

```
INSTALL       Gate + Witness deployed; egress lockdown checker passes
BOOTSTRAP     tenant signing keys generated (customer-held);
              first anchor established; dry-run mode ledger begins
ENFORCE       dry-run → enforcement cutover (a C3 event, human receipt)
ROTATE        signing-key rotation on schedule or on suspicion;
              old key's final batch cross-signed by new key so chain
              verification survives rotation
UPGRADE       schema/format versions per §7.4; verifier compatibility
              confirmed BEFORE Gate upgrade, never after
ROLLBACK      config-level, ledgered, mandatory post-incident review
EXPORT        continuous capability, not an exit-only feature
DECOMMISSION  final export generated and verified; final anchor
              published; Gate removed; egress rules handed back
DESTROY       ledger destruction only after retention floor passes
              (≥ 6 months, §7.3); destruction produces a signed
              certificate referencing the final anchor — the customer
              can forever prove what existed and when it was destroyed
```

The last line matters: even destruction leaves evidence. A compliance product whose exit is "we deleted everything, trust us" has misunderstood its own category.

---

## 15. Roadmap: Kernel → Product → Platform

**Phase 1 — Kernel (Months 0–4).** Gate (MCP proxy + OPA), Witness (sidecar, net/api/fs), Ledger (JCS + chain + Merkle + S3 anchor), `aegis-verify` CLI, export bundle, egress lockdown checker, Article 12 mapping collateral. **Exit criteria (all measurable):** 1 production deployment; ≥ 1M ledger entries written and 100% verified by `aegis-verify` including anchor checks; zero ledger data-loss incidents; first regulator-format export accepted by the customer's compliance team; dry-run → enforcement cutover completed at that customer.

**Phase 2 — Product (Months 4–10).** Policy compiler UI + testing suite (§5.4), SCIM-integrated human-responsible flows, escalation adapters (Slack/Teams/ITSM), Entra/Okta turnkey identity adapters, RFC 3161 anchoring, consistency sampling for C3, benchmark publication (§12). **Exit criteria:** 3 paying tenants; 10M+ cumulative verified ledger entries; measured p99 Gate added latency < 20 ms on the reference workload; zero data-loss incidents across all tenants; **SOC 2 Type II audit completed** — a compliance-evidence vendor that hasn't passed its own audit doesn't get past procurement; measured cost table replaces every hypothesis in §12.2.

**Phase 3 — Analytics (Months 10–18).** Contradiction analytics, per-tenant interaction graph (collusion detection from v2.0 §10.2 — the Louvain + taint approach survives here), learned trust fusion *if* the §9.2 data criterion is met, behavioral add-on for customers who ask.

**Phase 4 — Ecosystem (18+).** Open the ledger entry + attestation formats as a spec; pursue alignment with whatever AIMS/NCCoE demonstration projects standardize; consortium anchoring only if ≥ 10 tenants demand it.

---

## Appendix A: Research (Formerly Tier 2)

Preserved as intellectual capital, removed from product scope: ZK proof-of-policy-compliance (incl. batching/recursive aggregation via Halo2/Nova), TEE attestation flows (SEV-SNP), federated reputation (EigenTrust), differential-privacy reputation sharing, quantum-resistant signatures (Dilithium size problem), formal verification of policy↔circuit equivalence, adversarial behavioral mimicry, self-modifying agent detection. The v2.0 open questions list (§19) carries forward unchanged. None of these appear in sales material until they have a Phase number.

## Appendix B: Version Comparison — v1 → v2 → v3

| Dimension | v1.0 | v2.0 | v3.0 |
|-----------|------|------|------|
| Organizing idea | Complete trust fabric | Tiered platform | Sellable kernel |
| Identity | Everything (TPM→VC→DID→HSM) | Simplified custom stack | Consumed from standards; overlay profile |
| Differentiator | None isolated | Execution Witness (buried, 1 of ~20 sections) | Witness + Ledger + Gate **is** the product |
| Cost claims | None | Fabricated precision | Benchmark protocol, hypotheses labeled |
| Scale frame | Implicit hyperscaler | 100M agents / 10B req/day | 50 agents / 1M actions/day reference |
| Probabilistic reasoning | Ignored | "Ensemble verification" (oversold) | Consistency Sampling (honestly scoped) |
| Trust engine | Academic formula | AutoML (cold-start impossible) | Static + guards; learned fusion gated on data |
| Sybil claim | — | "1000 TPMs" (false in cloud) | Quota + human binding + ledgered births |
| Buyer | Absent | Absent | §14 |
| Regulatory mapping | Mentioned | Mentioned | Line-by-line Article 12 table |
| What's NOT built | Nothing excluded | Fallbacks listed | Binding exclusion table (§13) |
| Residual risk disclosure | No | Partial | Explicit column in threat model |

## Appendix C: Architecture Decision Records

Four decisions carry the philosophy; everything else is detail.

**ADR-001 — Consume identity; never issue it.**
*Context:* Six major IAM vendors shipped agent identity in H1 2026; IETF AIMS composes WIMSE + SPIFFE + OAuth as the reference stack. *Decision:* ATF verifies standard identities and adds an overlay profile; no DID method, no token format, no issuance. *Consequence:* zero rip-and-replace objection; ATF rides every dollar Okta and Microsoft spend educating the market; the cost is dependence on IdP guarantees, accepted and stated in the threat model.

**ADR-002 — Deterministic states, no numeric trust score.**
*Context:* Scores demand calibration nobody can defend, and learned fusion requires incident data a new product lacks. *Decision:* four states (VERIFIED/RESTRICTED/QUARANTINED/UNKNOWN) from readable rules; ML is advisory-only, forever. *Consequence:* every deny cites a rule, which survives audits and support tickets; the cost is coarser risk granularity, accepted.

**ADR-003 — No custom protocol.**
*Context:* v2.0's ATFP envelope required ecosystem adoption to be useful. *Decision:* the Gate rides existing transports (MCP, HTTP, SDK); interception at the tool boundary is protocol-agnostic. *Consequence:* zero adoption friction; ATF inherits rather than fights whatever succeeds MCP.

**ADR-004 — No ZK or TEE in the kernel.**
*Context:* ZK economics (~$0.50+/proof) and TEE overhead solve problems no current buyer has, while Article 12 requires neither. *Decision:* tamper evidence via canonical hashing + Merkle + external anchoring; confidential computing stays in Appendix A until a paying buyer's threat model demands it. *Consequence:* the kernel ships in months, not years; the cost is a trust boundary at the host kernel, stated normatively in §6.1.

## Appendix D: Implementation Notes (Operational Detail)

Moved here from the architecture body so the "what" stays readable; this appendix is the seed of the future deployment guide.

**D.1 Deployment matrix:**

| Environment | Gate deployment | Witness deployment | Egress lockdown mechanism |
|-------------|-----------------|--------------------|---------------------------|
| Kubernetes | Sidecar or namespace-level MCP proxy | Sidecar container, shared pod | NetworkPolicy: default-deny, allow gate-svc only |
| AWS ECS / Fargate | Reverse-proxy service in task network | Sidecar task container | Security group egress rules |
| VM (cloud or on-prem) | Local daemon on loopback proxy port | Companion daemon w/ eBPF capabilities | Host firewall default-deny egress |
| Bare metal | Service proxy on dedicated node | Host daemon | Host firewall + VLAN policy |
| Serverless agents | Regional Gate endpoint (remote proxy) | **Unavailable → all verdicts UNOBSERVED** | Function egress via Gate URL only |

**D.2 Escalation channel adapters:** canonical webhook, plus first-party adapters for Slack, Microsoft Teams, email, PagerDuty, and ServiceNow; additional ITSM systems via the webhook contract. All adapters deliver the same signed escalation object; the channel never changes the semantics (timeout→DENY, deny-wins, single-action binding).

**D.3 Anchor backends:** S3 object-lock (default), RFC 3161 timestamping authority, public transparency log — customer's choice, declared in the export bundle.

---

*Document Version: 3.2 — Buildable Specification (final paper revision; next milestone is the prototype)*
*© 2026 Aegis Platform, ByteHubble India Pvt. Ltd.*
