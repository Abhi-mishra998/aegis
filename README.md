
<div align="center">

<!-- ANIMATED HEADER -->
<a href="https://github.com/Abhi-mishra998/aegis">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=12,20,24&height=240&section=header&text=AEGIS&fontSize=90&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=Runtime%20Security%20Control%20Plane%20for%20AI%20Agents&descSize=18&descAlignY=58&descAlign=50" alt="Aegis Header"/>
</a>

<!-- TYPING ANIMATION -->
<a href="https://github.com/Abhi-mishra998/aegis">
  <img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=22&duration=2800&pause=600&color=00E5A0&center=true&vCenter=true&width=900&lines=Block+prompt+injection+before+it+executes.;Prove+every+decision+with+ed25519+%2B+Merkle.;Stop+rogue+agents+in+%3C+5+seconds%2C+tenant-wide.;12+services.+330%2B+tests.+Sub-30ms+p95." alt="Aegis tagline"/>
</a>

<br/>
<br/>

<!-- HERO ONE-LINER -->
<h3>
🛡️ A runtime firewall for AI agents that <strong>blocks</strong> dangerous actions before they run,
<br/>
and <strong>cryptographically proves</strong> what happened after.
</h3>

<br/>

<!-- BADGES ROW 1 — Tech Stack -->
<p>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/PostgreSQL-14+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL"/>
  <img src="https://img.shields.io/badge/Redis-7+-DC382D?style=for-the-badge&logo=redis&logoColor=white" alt="Redis"/>
  <img src="https://img.shields.io/badge/OPA-Rego-7D4698?style=for-the-badge&logo=openpolicyagent&logoColor=white" alt="OPA"/>
  <img src="https://img.shields.io/badge/Docker-25+-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker"/>
  <img src="https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black" alt="React"/>
</p>

<!-- BADGES ROW 2 — Project Health -->
<p>
  <img src="https://img.shields.io/badge/services-12-1f2937?style=flat-square&labelColor=111827" alt="services"/>
  <img src="https://img.shields.io/badge/containers-25-1f2937?style=flat-square&labelColor=111827" alt="containers"/>
  <img src="https://img.shields.io/badge/tests-330%2B-22c55e?style=flat-square&labelColor=111827" alt="tests"/>
  <img src="https://img.shields.io/badge/p95_latency-27ms-22c55e?style=flat-square&labelColor=111827" alt="p95"/>
  <img src="https://img.shields.io/badge/attack_block_rate-100%25-22c55e?style=flat-square&labelColor=111827" alt="block rate"/>
  <img src="https://img.shields.io/badge/audit_chain-verified-22c55e?style=flat-square&labelColor=111827" alt="audit chain"/>
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square&labelColor=111827" alt="license"/>
</p>

<!-- CTA ROW -->
<p>
  <a href="#-quick-start"><img src="https://img.shields.io/badge/🚀_QUICK_START-1_min-00E5A0?style=for-the-badge&labelColor=000000" alt="Quick Start"/></a>
  <a href="https://drive.google.com/file/d/1Eojid76NcrRLC1Gp302i113pNgrH1hso/view"><img src="https://img.shields.io/badge/▶_WATCH_DEMO-5_min-ef4444?style=for-the-badge&labelColor=000000" alt="Demo"/></a>
  <a href="https://projectsphere.hashnode.dev/i-built-a-runtime-firewall-for-ai-agents"><img src="https://img.shields.io/badge/📝_READ_THE_BLOG-12_min-3b82f6?style=for-the-badge&labelColor=000000" alt="Blog"/></a>
  <a href="#-architecture-at-a-glance"><img src="https://img.shields.io/badge/🏗️_ARCHITECTURE-deep_dive-8b5cf6?style=for-the-badge&labelColor=000000" alt="Architecture"/></a>
</p>

<br/>

<!-- STATS / VISITOR COUNTER -->
<p>
  <img src="https://komarev.com/ghpvc/?username=Abhi-mishra998&repo=aegis&label=Repo+Views&color=00E5A0&style=for-the-badge" alt="views"/>
  <img src="https://img.shields.io/github/stars/Abhi-mishra998/aegis?style=for-the-badge&logo=github&color=fbbf24&labelColor=000000" alt="stars"/>
  <img src="https://img.shields.io/github/forks/Abhi-mishra998/aegis?style=for-the-badge&logo=github&color=00E5A0&labelColor=000000" alt="forks"/>
  <img src="https://img.shields.io/github/issues/Abhi-mishra998/aegis?style=for-the-badge&logo=github&color=ef4444&labelColor=000000" alt="issues"/>
  <img src="https://img.shields.io/github/last-commit/Abhi-mishra998/aegis?style=for-the-badge&logo=git&color=8b5cf6&labelColor=000000" alt="last commit"/>
</p>

<!-- WAVE SEPARATOR -->
<img src="https://capsule-render.vercel.app/api?type=rect&color=gradient&height=2&customColorList=12,20,24" width="100%" alt="separator"/>

</div>

<br/>

## 📖 Table of Contents

<table>
<tr>
<td valign="top" width="50%">

**Story**
- [The 3am Question](#-the-3am-question)
- [What Aegis Is](#-what-aegis-is)
- [The Problem](#-the-problem-stated-plainly)
- [How Aegis Closes the Gap](#%EF%B8%8F-how-aegis-closes-the-gap)

**Engineering**
- [Architecture at a Glance](#%EF%B8%8F-architecture-at-a-glance)
- [10-Stage Request Pipeline](#-the-10-stage-request-pipeline)
- [Service Inventory](#-service-inventory)
- [Data Model](#-data-model)

</td>
<td valign="top" width="50%">

**Operations**
- [10-Layer Security Architecture](#%EF%B8%8F-10-layer-security-architecture)
- [Performance Numbers](#-performance--sla)
- [The UI](#-the-ui)
- [Quick Start](#-quick-start)

**Reference**
- [What's Working / What's Next](#-whats-working--whats-next)
- [Demo Scenarios](#-three-demo-scenarios)
- [Watch the Demo](#-watch-the-demo)
- [Read the Blog](#-read-the-deep-dive)
- [Get in Touch](#-get-in-touch)

</td>
</tr>
</table>

<br/>

---

## 🌙 The 3am Question

> *"My AI agent has production database access. What's stopping it from dropping a table?"*

A few months ago, that question didn't have a good answer at the place I worked. The agent had a service-account token, a long list of tool permissions, and no real boundary between **things it should do** and **things it could do**.

If it went wrong — prompt injection, a hallucinated tool call, a stolen token — the failure modes were real:

- It could read databases it shouldn't.
- It could send emails to anyone.
- It could delete infrastructure.
- The audit trail was a stack of plain-text JSON logs across six services that nobody had verified.

The harder question was the second one: **if something did go wrong, how would we know?** And how would we prove it to a regulator, an auditor, or a customer asking why their data was touched?

Existing security tools — IAM, WAFs, SIEMs, API gateways — were built for humans clicking buttons. None of them understood what it meant for an autonomous agent to call a tool.

**So I started building one.**

<br/>

---

## 🛡️ What Aegis Is

**Aegis is a runtime control plane for AI agents.** It sits between an agent and the systems it acts on — databases, APIs, Kubernetes clusters, internal tools — and enforces one rule:

> ⚡ Every tool call is **authenticated, authorized, risk-scored, policy-checked, and cryptographically logged** before it executes.

<table>
<tr>
<td width="50%" valign="top">

**📦 What's in the box**

- 12 microservices across 25 containers
- ~330 pytest tests
- 3 end-to-end demo scenarios
- A working transparency log
- Python, FastAPI, Postgres, Redis, OPA
- Decision latency: **< 30ms p95** on the deny path
- Full stack runs locally with **one `docker compose up`**

</td>
<td width="50%" valign="top">

**🎯 What it isn't**

- Not an agent framework
- Not an LLM inference provider
- Not a general-purpose APM
- Not a wrapper around someone else's policy engine

It sits between your agent code and the world. **One product, not a platform.**

</td>
</tr>
</table>

<br/>

---

## 🔥 The Problem, Stated Plainly

Most AI agent deployments today share one weakness: **the security model assumes the agent will behave.**

When it doesn't — through prompt injection, a model hallucination, a compromised credential, or just a bad output — the failure modes are real:

| Failure mode | Why it happens today |
|---|---|
| 🔓 **Production data leak** | An agent reads tables it shouldn't have access to |
| 💥 **Destructive operation** | An agent runs `DROP TABLE`, deletes a namespace, sends mass email |
| 🕵️ **Slow PII exfiltration** | An agent leaks data over weeks, in volumes small enough to look normal |
| 🏢 **Cross-tenant access** | An agent acts across tenant boundaries because the tool wrapper didn't check |
| 📜 **No forensic trail** | Nobody can reconstruct what happened — logs aren't tamper-evident |

These aren't theoretical. Every one of them has shown up in postmortems published in the last twelve months at companies of every size.

**Existing tools don't solve this because of *structure*, not effort.** A firewall sees network traffic. An IAM system sees user identities. An API gateway sees endpoints. None of them see what an agent is *trying to do* — only what it's *technically allowed to do*. That gap is where the failures live.

<br/>

---

## ⚙️ How Aegis Closes the Gap

Every tool call from an agent flows through **ten checks in order**:

<div align="center">

```
       AGENT
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  ① Identity Verification    (JWT + revocation)          │
│  ② Rate Limiting            (per-tenant, per-agent)     │
│  ③ Kill-switch Check        (tenant- + agent-scoped)    │
│  ④ Tool Allow-list          (explicit permission)       │
│  ⑤ OPA Policy Evaluation    (declarative Rego rules)    │
│  ⑥ Behavioral Risk Scoring  (anomaly + PII signals)     │
│  ⑦ Autonomy Contract Check  (action budgets)            │
│  ⑧ DECISION                 (allow / deny / throttle)   │
│  ⑨ ed25519-Signed Receipt   (cryptographic proof)       │
│  ⑩ Transactional Outbox     (billing + telemetry)       │
└─────────────────────────────────────────────────────────┘
         │
         ▼
  REAL-WORLD ACTION
```

</div>

The interesting design choices live in three places:

**1️⃣ Policy is declarative, not code.** OPA evaluates the rules. A compliance engineer can read them, change them, and audit them without touching application code. Catastrophic operations — namespace deletion, cluster-admin grants, sending email outside the allowed domain — are hard-denied at this layer.

**2️⃣ Audit is tamper-evident, not just persisted.** Every decision is signed with **ed25519**. The audit log is **HMAC-chained**, so altering any entry breaks every entry after it. A daily **Merkle root** is computed and published, with each root linking to the previous one. An external auditor can verify the entire chain offline with one command — **zero trust in the running system required**.

**3️⃣ The kill switch is durable.** Engaging the kill switch writes to both Redis (hot-path speed) and Postgres (persistence). I tested this by flushing Redis after engaging the switch — the agent stayed blocked. That detail matters more than it sounds. It's the difference between a control that works in a demo and one that works during an actual incident.

<br/>

---

## 🏗️ Architecture at a Glance

> 📌 Diagrams below are wide. Best viewed on desktop. Click any image for full resolution.

### Full system topology

![Full System Architecture](docs/images/01-full-architecture.png)

> **Figure 1** — Every agent request enters through the API gateway on port 8000, passes through five sequential gates, fans out to six core services, picks up runtime trust signals from the intelligence tier, and gets cryptographically anchored before any side-effect is allowed to execute. The bottom row shows the live SLA numbers from the most recent load test.

<details>
<summary><strong>👉 Click here for the load-bearing design decisions</strong></summary>

<br/>

- **The gateway is the only entry point.** No service is reachable from outside. This sounds obvious until you see how often it isn't true in real deployments.
- **The five gates run in order, not in parallel.** Auth → rate limit → payload validation → permission check → risk scoring. Each gate is **fail-closed** — if it can't reach its dependency, the request is denied.
- **The cryptographic trust layer sits sideways across everything.** Receipts, kill switches, SSE streams, and reconciliation observe the core services rather than participating in the hot path. That separation is what keeps the deny-path under 30ms.
- **The async processing pipeline is the durability story.** Every audit and billing write goes through a transactional outbox in Postgres before reaching its consumer. Zero data loss without slowing down the synchronous decision.

</details>

<br/>

---

## 🚦 The 10-Stage Request Pipeline

![10-Stage Pipeline](docs/images/02-pipeline.png)

> **Figure 2** — Each stage has its own failure code and latency budget. The first eight run synchronously *before* the tool actually executes. The last two run asynchronously *after* — which is why the user gets their response in under 30ms while the audit chain and billing pipeline are still finishing their work in the background.

**Cost-and-confidence ordering.** Cheap checks run first:

- JWT verification → single signature check, **sub-millisecond**
- Rate limiting → Redis `INCR`, **sub-millisecond**
- Payload validation → Pydantic, **microseconds**

These three reject 90%+ of malformed or abusive traffic before anything expensive runs. By the time a request reaches risk assessment — the most expensive stage — it's already been validated as well-formed, authenticated, within quota, and permitted in principle.

**That's why the deny path is faster than the allow path.** A blocked request usually fails at stage 2, 3, or 4, before any intelligence services are consulted.

<br/>

---

## 🧩 Service Inventory

![Service Inventory](docs/images/03-services.png)

> **Figure 3** — All twelve services plus supporting infrastructure (PostgreSQL, Redis, OPA bundle server, Prometheus, Grafana, Jaeger). Each service owns its data and exposes its capability over a single port.

**Split along failure-domain boundaries, not feature boundaries.** If the behavior engine goes down, the gateway falls back to its degraded-mode policy. If the audit service is briefly unavailable, the transactional outbox buffers writes. If the inference proxy times out, requests **fail closed**.

A few services worth singling out:

| Service | Port | Why it matters |
|---|---|---|
| **Decision Engine** | `8010` | The brain. Aggregates inference + behavior + policy + cost into one risk score in < 50ms |
| **Identity Graph** | `8013` | Powers blast-radius analysis. Agents, users, tools, API keys are nodes; permissions are edges |
| **Flight Recorder** | `8014` | 90-day forensic replay. Every decision captured with full context, scrubbable like a debugger |
| **Autonomy** | `8015` | Enforces explicit action budgets — *"≤3 destructive ops/hour, no namespace deletes ever"* |

<br/>

---

## 💾 Data Model

![PostgreSQL Data Model](docs/images/04-data-model.png)

> **Figure 4** — Four logical schemas in PostgreSQL 14+: identity, registry, audit, usage. Each runs with its own database role and is backed by primary + 2 read replicas with PITR over 30 days.

**Two tables worth understanding deeply:**

`audit_logs` is **append-only at the database level** — there's no `UPDATE` permission granted to any service role. Every row carries:
- `event_hash` — SHA-256 of its canonical representation
- `prev_hash` — links to the previous row in the same shard
- `chain_shard` — which of 16 parallel chains it belongs to

Sharding lets the chain accept writes in parallel without contention. The daily Merkle root reduces all shards into a single signed digest.

`pending_usage_events` is the **transactional outbox**. When a request executes, the audit log row and the pending billing event are written in the **same database transaction**. The guarantee is structural: if the audit row exists, the billing event was queued. **No retries, no hope, no silent revenue leakage.**

<br/>

---

## 🛡️ 10-Layer Security Architecture

![10-Layer Security](docs/images/05-security-layers.png)

> **Figure 5** — Ten independent security layers, each addressing a different threat class.

A defense-in-depth model only works if the layers are **genuinely independent**. The mistake most security stacks make is collapsing too many checks into one layer, so one bypass disables several controls at once.

| # | Layer | Defends against |
|---|---|---|
| 1 | Auth & Authorization | Credential abuse |
| 2 | Rate Limiting | Runaway loops |
| 3 | Input Validation | Malformed payloads, injection patterns |
| 4 | Permissions | Off-allow-list tool calls |
| 5 | OPA Policy | Declarative violations |
| 6 | Content Inspection | Prompt injection, malware signatures |
| 7 | Behavioral Analysis | Anomalies over time |
| 8 | Risk Scoring | Combined signal threshold breach |
| 9 | Action Enforcement | Decision execution (kill / isolate / throttle) |
| 10 | Audit & Compliance | Cryptographic proof of what happened |

> 💡 **Why Audit is last, not first.** The audit log captures what *actually happened* — including which layer blocked the request and why. So even when a layer fails to block something it should have, the audit log records the failure, and the next chain verification surfaces it.

<br/>

---

## 📊 Performance & SLA

![SLA & Performance](docs/images/06-sla.png)

> **Figure 6** — Measurements from a load test of 100 concurrent users sustained over 120 seconds, hitting the full pipeline. Not a microbenchmark. Not a synthetic test. Not the health-check endpoint.

| Metric | Target | Achieved | Status |
|---|---|---|---|
| Availability | 99.95% | **99.99%** | ✅ Exceeds |
| P95 Response Time | < 100ms | **27ms** | ✅ Exceeds |
| P99 Response Time | < 200ms | **60ms** | ✅ Exceeds |
| Sustained Throughput | 20 req/s | **30 req/s** per instance | ✅ 150% of target |
| Attack Block Rate | Zero leaks | **100%** (346/346) | ✅ Perfect |
| Audit Log Delivery | 100% within 5s | **100% within 2s** | ✅ 2.5× faster |
| Billing Accuracy | 100% reconciled | **100%** | ✅ Perfect |
| Data Durability | 99.99% | **100%** | ✅ Exceeds |

> ⚠️ **Honest caveat:** these are single-laptop numbers with the full 25-container stack running locally. The architecture is horizontal — three gateway replicas behind a load balancer reach 100+ req/s, with the bottleneck moving to the decision engine, which scales the same way. The number to take away isn't *"30"* — it's *"the architecture scales linearly with replicas."*

<br/>

---

## 🖥️ The UI

<div align="center">

### System Health
![System Health Dashboard](docs/images/ui-01-health.png)

*Live status of all 12 services. Each card shows current latency (17–20ms range, well under the 100ms SLA target). Operational queues panel at the bottom shows audit stream depth and DLQ counts — critical signals that the async pipeline isn't backing up.*

<br/>

### Audit Logs (Immutable Chain)
![Audit Logs](docs/images/ui-02-audit.png)

*Every row is an immutable record carrying its own event hash and a link to the previous event's hash. Expanding a row reveals actor ID, HTTP status, trace ID, risk score, and the canonical event hash signed with ed25519. **"Verify Integrity"** runs the offline chain verifier against every record in view.*

<br/>

### Identity Graph + Blast-Radius Simulation
![Identity Graph](docs/images/ui-03-graph.png)

*Node-and-edge view of agents, tools, customers, and resources. Selecting the DevOps Agent and running a stolen-token compromise simulation at depth 3 produces a quantified blast radius: **8 reachable nodes, 1 affected resource, risk 0.075**. Red edges are denied paths. Answers "if this token were stolen, what could the attacker reach?" — before the question becomes urgent.*

<br/>

### Autonomy Contracts
![Autonomy Contracts](docs/images/ui-04-autonomy.png)

*Each contract binds to an agent and declares its budget (max_runtime, max_cost) plus deny-list and approval-required action lists. The K8s DevOps contract hard-denies namespace and node deletion, denies cluster-role binding creation, and requires human approval for secret and cluster-role patches. The **Recent Violations** panel shows real-time enforcement.*

<br/>

### Emergency Kill Switch
![Kill Switch](docs/images/ui-05-killswitch.png)

*One click engages a tenant-wide isolation persisted to both Redis (speed) and PostgreSQL (durability). The protocol integrity panel shows that the toggle event itself is signed and added to the audit chain — **the act of pulling the kill switch is provably part of the incident record**.*

<br/>

### Slack Critical Incident Alert
![Slack Alert](docs/images/ui-06-slack.png)

*A real Slack alert from the auto-response engine when an agent attempted a hard-denied operation (`k8s.delete.namespace`) with a risk score of 97%. The incident includes agent ID, tool, trigger reason, and the specific violation. The full forensic trail lives in the audit log.*

</div>

<br/>

---

## 🚀 Quick Start

Get the full stack running in one terminal:

```bash
# Clone
git clone https://github.com/Abhi-mishra998/aegis.git
cd aegis

# Boot the 25-container stack
cd infra && docker compose up --build -d
sleep 90

# Seed the admin user
cd .. && python3 -m venv .venv && source .venv/bin/activate
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/utils/seed_admin.py

# Run all 3 demo packs in dry-run mode (~10 seconds)
ACP_DRY_RUN=1 .venv/bin/python demos/run_all_demos.py

# Open the UI
open http://localhost:5173      # macOS
# xdg-open http://localhost:5173  # Linux
# Login: admin@acp.local / password
```

📘 **Full setup guide** with environment variables, backups, Slack integration, and troubleshooting: [`setup.md`](setup.md)

<br/>

---

## ✅ What's Working / What's Next

I'm being explicit here because *"production-grade"* claims on side projects are easy to make and hard to verify.

<table>
<tr>
<td valign="top" width="50%">

### ✅ Shipped & Tested

- JWT issuance, revocation, replay protection
- Per-tool allow-list enforcement
- OPA-evaluated policy bundles
- Behavioral risk scoring with PII density signals
- Autonomy contracts with action budgets
- Auto-response engine (KILL, ISOLATE, THROTTLE, ALERT)
- Tenant- and agent-scoped kill switches with Postgres persistence
- ed25519 receipts + HMAC chain + daily Merkle root
- Offline chain verifier (`acp verify-chain`)
- Blast-radius graph analysis
- Slack escalation for critical incidents
- Encrypted offsite backups (age + S3)
- Audit-to-billing reconciliation
- ~330 pytest tests
- Three end-to-end demo packs

</td>
<td valign="top" width="50%">

### 🚧 Actively in Progress

- Full Jaeger distributed tracing across all 12 services
- Published threat model (STRIDE per service)
- Sustained 1,000+ RPS load test
- Helm charts + production deployment guide
- Multi-region replication for the audit chain
- HashiCorp Vault integration for secret management
- TypeScript SDK with first-class type safety
- Expanded web UI dashboards (currently CLI + Grafana-only)
- External security review

</td>
</tr>
</table>

> 🔓 **I work on this in the open.** The roadmap is in the repo. Open an issue if you'd like to argue about priorities.

<br/>

---

## 🎬 Three Demo Scenarios

Each one is a single command, produces signed audit receipts, and is reproducible from a clean clone.

<table>
<tr>
<td valign="top" width="33%">

### 🤖 DevOps Agent

A Kubernetes operator demo.

Demonstrates:
- ✅ Safe reads allowed
- ✅ Non-prod scaling allowed
- ❌ Namespace deletion hard-denied
- ❌ Privilege escalation blocked
- ❌ Delete storms throttled
- ⛔ Kill switch persists through Redis flush
- 🔐 240+ events chain-verified

```bash
.venv/bin/python demos/devops_agent/scripted_demo.py
```

</td>
<td valign="top" width="33%">

### 🗄️ Database Copilot

An analyst-facing SQL assistant.

Demonstrates:
- ✅ Allowed SELECTs
- ⚠️ Behavior-scored bulk queries
- ❌ PII column exfiltration blocked
- ❌ DDL destruction (DROP) blocked + token revoked
- ⛔ Tenant-wide kill switch

```bash
.venv/bin/python demos/db_copilot/scripted_demo.py
```

</td>
<td valign="top" width="33%">

### 🎧 Support Agent

A customer-service automation.

Demonstrates:
- ✅ Ticket lookups allowed
- 👁️ Single-customer PII monitored
- ❌ Cross-tenant access denied
- ❌ Bulk PII export blocked
- ❌ Email exfiltration denied (OPA hard-rule)
- 🐢 Runaway bursts rate-limited

```bash
.venv/bin/python demos/support_agent/scripted_demo.py
```

</td>
</tr>
</table>

The output isn't slides. It's the actual system making actual decisions, in milliseconds, with signed receipts you can verify after.

<br/>

---

## 🎥 Watch the Demo

<div align="center">

[![Watch the Aegis demo](https://img.shields.io/badge/▶_FULL_WALKTHROUGH-5_minutes-ef4444?style=for-the-badge&labelColor=000000)](https://drive.google.com/file/d/1Eojid76NcrRLC1Gp302i113pNgrH1hso/view)

</div>

The video covers:
- 🛡️ Runtime policy enforcement (block before execute)
- 🔐 Cryptographic audit chain verification
- 💥 Blast-radius simulation in the identity graph
- 📜 Autonomy contracts (budget enforcement)
- ⛔ Live kill-switch activation + Redis-flush persistence test
- ✍️ ed25519-signed audit receipt verification

<br/>

---

## 📝 Read the Deep Dive

If you want the engineering story behind every design decision — why ed25519 over RSA, why a Merkle log instead of just a hash chain, why OPA, what tried to break it during development — the full blog post is here:

<div align="center">

[![Read the blog](https://img.shields.io/badge/📝_READ_THE_DEEP_DIVE-12_minutes-3b82f6?style=for-the-badge&labelColor=000000)](https://projectsphere.hashnode.dev/i-built-a-runtime-firewall-for-ai-agents)

</div>

<br/>

---

## 📂 Repository Layout

```text
aegis/
├── 🚪 services/         12 FastAPI microservices (gateway, audit, identity, ...)
├── 🐳 infra/            Docker Compose + Kubernetes orchestration
├── 🎨 ui/               React 18 SPA — SOC visibility dashboards
├── 📦 sdk/              Python SDK for 5-line agent integration
├── 📚 docs/             Architecture diagrams, runbooks, audit reports
├── 🔧 scripts/          Ops scripts (backup, reconcile, export, redact)
├── 🎭 demos/            Three reproducible demo packs
├── 🧪 tests/            ~330 pytest tests (unit → integration → E2E)
├── 📜 LICENSE           MIT
├── ⚙️ pyproject.toml    Package + dependency management
└── 📖 README.md         You are here
```

<br/>

---

## 🛠️ Tech Stack

<div align="center">

**Runtime**

<img src="https://skillicons.dev/icons?i=python,fastapi,postgres,redis,docker,kubernetes&theme=dark" alt="Runtime stack"/>

**Frontend**

<img src="https://skillicons.dev/icons?i=react,vite,tailwind,typescript&theme=dark" alt="Frontend stack"/>

**Observability**

<img src="https://skillicons.dev/icons?i=prometheus,grafana&theme=dark" alt="Observability stack"/>
<img src="https://img.shields.io/badge/Jaeger-66CFE3?style=for-the-badge&logo=jaeger&logoColor=white" alt="Jaeger"/>
<img src="https://img.shields.io/badge/OPA-7D4698?style=for-the-badge&logo=openpolicyagent&logoColor=white" alt="OPA"/>

</div>

<br/>

---

## 👥 Who This Is For

<table>
<tr>
<td valign="top" width="33%">

### 👷 Engineers building agents

You've felt the discomfort of giving an LLM real-world write access. This is the substrate I wish I'd had.

</td>
<td valign="top" width="33%">

### 🏢 Security architects

You're being asked *"how do we govern our AI?"* by your CISO, board, or auditor. This is one concrete answer.

</td>
<td valign="top" width="33%">

### 🎯 Hiring managers

Working on AI safety, platform security, or infrastructure? Below is the deepest look you'll get into how I think about systems.

</td>
</tr>
</table>

<br/>

---

## 🤝 Contributing

Aegis is MIT-licensed and built in the open. PRs welcome:

- 🐛 **Report bugs** via [issues](https://github.com/Abhi-mishra998/aegis/issues)
- 💡 **Propose features** through discussions
- 🔍 **Review the threat model** — see something I missed? Open an issue.
- 📝 **Improve docs** — typos and clarifications are always welcome
- 🧪 **Add tests** — see [`tests/`](tests/) for the existing patterns

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full guide.

<br/>

---

## 📬 Get in Touch

If you're working on agent infrastructure, AI safety, or platform security — or if you're hiring in this space — I'd love to talk.

<div align="center">

[![Email](https://img.shields.io/badge/Email-abhishekmishra09896@gmail.com-D14836?style=for-the-badge&logo=gmail&logoColor=white)](mailto:abhishekmishra09896@gmail.com)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-abhishek--mishra--eng-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/abhishek-mishra-eng)
[![Blog](https://img.shields.io/badge/Blog-blog.abhimishra.dev-FF6154?style=for-the-badge&logo=hashnode&logoColor=white)](https://blog.abhimishra.dev)
[![Portfolio](https://img.shields.io/badge/Portfolio-abhimishra.dev-000000?style=for-the-badge&logo=vercel&logoColor=white)](https://abhimishra.dev)

</div>

<br/>

---

## 📜 License

[MIT](LICENSE) — use it, fork it, build on it. If it ends up saving you from an incident, drop me a note. I'd love to hear the story.

<br/>

---

<div align="center">

### ⭐ If Aegis is useful to you, star the repo — it helps others find it.

<br/>

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=12,20,24&height=140&section=footer&text=Every%20action%20governed.%20Every%20decision%20proved.&fontSize=20&fontColor=ffffff&animation=fadeIn&fontAlignY=70" alt="footer"/>

</div>
```

---
## 📬 Connect

If you're working on agent infrastructure, AI safety, or platform security — or if you're hiring in this space — I'd love to talk.

<table align="center">
<tr>
<td align="center" width="20%">
  <a href="https://portfolio-self-seven-1zphd40voq.vercel.app">
    <img src="https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/vercel.svg" width="32" height="32" alt="Portfolio"/>
    <br/>
    <strong>Portfolio</strong>
  </a>
  <br/>
  <sub>Live site</sub>
</td>
<td align="center" width="20%">
  <a href="https://github.com/Abhi-mishra998">
    <img src="https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/github.svg" width="32" height="32" alt="GitHub"/>
    <br/>
    <strong>GitHub</strong>
  </a>
  <br/>
  <sub>@Abhi-mishra998</sub>
</td>
<td align="center" width="20%">
  <a href="https://www.linkedin.com/in/abhishek-mishra-eng/">
    <img src="https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/linkedin.svg" width="32" height="32" alt="LinkedIn"/>
    <br/>
    <strong>LinkedIn</strong>
  </a>
  <br/>
  <sub>Connect</sub>
</td>
<td align="center" width="20%">
  <a href="https://dev.to/abhishek_mishra_01">
    <img src="https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/devdotto.svg" width="32" height="32" alt="Dev.to"/>
    <br/>
    <strong>Dev.to</strong>
  </a>
  <br/>
  <sub>Technical writing</sub>
</td>
<td align="center" width="20%">
  <a href="mailto:abhishekmishra09896@gmail.com">
    <img src="https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/gmail.svg" width="32" height="32" alt="Email"/>
    <br/>
    <strong>Email</strong>
  </a>
  <br/>
  <sub>Direct contact</sub>
</td>
</tr>
</table>


