# AEGIS — PRODUCTIZATION PLAN (v2)

**2026-06-15** · Author: ground-level codebase audit + product-feedback v2 · Status: locked plan, ready to execute

> **The product mental model is "Stripe for agent security."**
> Customer signs up → creates workspace → adds agent → copies SDK snippet → runs their agent → sees decisions → upgrades plan.
> Everything that doesn't move us toward that flow in the next 60-90 days is deprioritized.

> **The success metric.** A customer signs up. Adds their agent. Runs it. Aegis starts protecting it. Without talking to me. The day that happens, Aegis stops being an engineering project and becomes a product.

---

## 0. CURRENT STATE — HONEST SCORECARD

| Dimension | Score | Evidence |
|---|---|---|
| Security engine | 9/10 | Brutal test 2026-06-15: 23/24 = 95.8 %, 0 security misses |
| Detection coverage | 8.5/10 | 34-signal MITRE-mapped registry, cumulative risk, threat-intel, IAG, remediation all shipped |
| Architecture | 9/10 | Tenant-isolated multi-service, ed25519 transparency chain, SDK-on-endpoint topology (correct) |
| **Product experience** | **3/10** | No self-service signup. No onboarding wizard. No "create agent → copy SDK → done" flow. |
| **Customer adoption** | **1/10** | Zero external users. Internal brutal-test agent only. No pilots. |

**The next failure is not a missing rule. It is that nobody can install, operate, or buy this.**

---

## 1. GROUND-LEVEL AUDIT — WHAT ACTUALLY EXISTS

### 1.1 Auth + Identity (works — gap is signup)

| Capability | File:line | State |
|---|---|---|
| `/auth/login` JWT mint | `services/identity/router.py:337-421` | ✅ works |
| JWT validator + cache | `services/gateway/auth.py:167-306` | ✅ works |
| Login UI | `ui/src/pages/Login.jsx:1-238` | ✅ polished — email/password + SSO buttons |
| SSO buttons | `Login.jsx:161-207` | ⚠ UI present, backend stubs |
| `POST /auth/users` | `services/identity/router.py:154-207` | ⚠ first user auto-approved, subsequent need ADMIN |
| `POST /auth/tenants` | `services/identity/router.py:673-746` | ❌ **INTERNAL_SECRET-gated — no public path** |
| `POST /auth/credentials` (agent API key) | gateway proxies to API service | ❌ **INTERNAL_SECRET-gated for new agents** |

### 1.2 Agent Lifecycle (works — gap is wiring)

| Capability | File:line | State |
|---|---|---|
| `POST /agents` create | `services/registry/router.py:51-92` | ✅ works |
| Agent CRUD UI | `ui/src/pages/Agents.jsx:111-142` | ✅ Deploy button works |
| `POST /agents/{id}/permissions` | `services/registry/router.py` | ✅ verified live |
| API-key mint UI | `ui/src/pages/DeveloperPanel.jsx:134-148` | ✅ works |

### 1.3 SDKs — the right architecture (don't touch)

| SDK | Path | State |
|---|---|---|
| `aegis-anthropic` | `integrations/aegis-anthropic/aegis_anthropic/__init__.py:206-244` | ✅ on PyPI, drop-in |
| `aegis-openai` | `integrations/aegis-openai/` | ✅ on PyPI |
| `aegis-langchain` | `integrations/aegis-langchain/` | ✅ on PyPI |
| `aegis-bedrock` | `integrations/aegis-bedrock/` | ✅ on PyPI |
| `aegis-aevf` | `integrations/aegis-aevf/` | ✅ on PyPI |
| MCP server (Claude Desktop / Cursor / Claude Code) | `services/mcp_server/server.py:1-60` | ✅ shipping |

**Architectural truth (CORRECTED v2):**
> The customer's Claude / OpenAI / Bedrock key stays on the customer's machine. **Aegis never sees, stores, or asks for that key.** The customer installs the SDK, sets `AEGIS_API_KEY` (the only key Aegis issues), and the SDK posts tool-call *intent* to `/execute`. Aegis returns allow/deny/escalate. The customer's machine executes — or doesn't.
>
> This is exactly how CrowdStrike, Datadog, New Relic, and the Wiz Agent work. **It is the moat for enterprise sales.** Don't break it.

### 1.4 Real-Time Monitoring (works)

| Capability | File:line | State |
|---|---|---|
| `_publish_event` to Redis | `services/gateway/main.py:128-164` | ✅ called after every `/execute` |
| `/events/stream` SSE | gateway main.py:1348-1372 | ✅ JWT-cookie-auth, tenant-isolated |
| `useSSE` hook | `ui/src/hooks/useSSE.js:28-207` | ✅ exponential backoff, 45 s heartbeat |
| LiveFeed page | `ui/src/pages/LiveFeed.jsx` | ✅ renders 8 event types live |
| Per-tenant scoping | every endpoint via `Depends(get_tenant_id)` | ✅ no cross-tenant leaks |

### 1.5 Threat Graph / Incidents / Remediation (built — defer surfacing)

| Capability | File:line | State |
|---|---|---|
| Sprint 4 Incident Storyline | `services/security/incidents/storyline.py` | ✅ 24h grouping |
| Sprint 5 IAG (graph + blast-radius) | `services/security/iag/` | ✅ `/iag/agents/{id}` + `/iag/incidents/{INC-id}/blast-radius` live |
| Sprint 6 Auto-Remediation | `services/security/remediation/` | ✅ revoke_api_key, kill_active_tokens, page_oncall |
| Sprint 7 Threat-Intel | `services/security/threatintel/` | ✅ providers shipping |
| MITRE ATT&CK signal registry | `services/security/signal_registry.py` | ✅ 34 signals tagged |
| Decision Explorer | `ui/src/pages/DecisionExplorer.jsx` | ✅ React Flow per request_id |

**These exist but ship later (Phase 5).** Buyers don't buy Threat Graph. They buy: "can I install this · can I see value · can I trust it." Threat Graph is a closing weapon, not an opening offer.

### 1.6 UI Surface (48 pages — too many for v1)

Customer-facing v1 = 5 pages. Hide / collapse / delete the rest until pilot feedback says otherwise.

| Keep for customer v1 | Hide for v1 (re-introduce as feedback demands) |
|---|---|
| `/dashboard` (NEW — Agent Inventory + hero metrics) | RBAC, SSO, SIEM, Webhook, Billing, Quota, AdminConsole, SystemHealth, Pricing |
| `/agents` + `/agents/:id` | Playbooks, AutonomyContracts, AttackSimulation, AutoResponse, Compliance, Evaluation |
| `/live-feed` | PolicyBuilder, PolicyAnalytics, PolicySim, Forensics, ThreatIntel, RiskEngine |
| `/incidents` | DeveloperPanel, ScheduledReports, IdentityGraph, FleetStatus |
| `/decision-explorer/:request_id` (drill-down only) | Settings (collapse) |

### 1.7 Live Infra (verified 2026-06-15)

- ALB `https://ha.aegisagent.in` · ASG 2× m6g.medium · RDS Multi-AZ · ElastiCache 2-node · ap-south-1
- `scripts/ops/build_release_bundle.sh` (commit 644e9f7) pins the bundle build
- 0 security misses · 95.8 % correctness · p50 773 ms / p95 2256 ms

---

## 2. THE 4 BLOCKERS (close to ship the pilot)

### Blocker 1 — `POST /auth/tenants` is INTERNAL_SECRET-gated
`services/identity/router.py:673-746` · **Fix:** add `POST /signup` (public).

### Blocker 2 — Agent API key requires INTERNAL_SECRET
Customer can create agent via UI but can't mint key without ops. **Fix:** chain agent-create + key-mint behind customer JWT, return both in one response.

### Blocker 3 — Zero onboarding wizard
No `Signup.jsx` or `Onboarding.jsx` in `ui/src/pages/`. **Fix:** build 3-screen wizard. **Customer NEVER enters their Claude/OpenAI key.** They get an Aegis key + SDK snippet.

### Blocker 4 — Default mode is ENFORCE from day 1
Customers won't install something that blocks prod on day 1. **Fix:** add `Workspace.shadow_mode_until = now() + 14 days` column. Gateway middleware: while in shadow window, downgrade deny/escalate → log-only and emit `would_have_blocked` event.

---

## 3. COMPETITIVE POSITIONING (the moat)

| Vendor | Strong | Blind to |
|---|---|---|
| **CrowdStrike** | process / bash / network / files | Claude tool call · agent memory · agent-to-agent · prompt attack |
| **Wiz** | AWS/Azure/GCP/IAM posture | LLM-agent runtime · tool permissions · agent actions |
| **Palo Alto Cortex** | SOC · XDR · TI · response | Agent-native actions · LLM reasoning |
| **Microsoft Defender** | identity / cloud / endpoint / O365 | AI-agent runtime (catching up but slow) |
| **Lakera / Protect AI / Prompt Security / CalypsoAI** | prompt injection · jailbreak · model security | Actual tool execution · agent actions · long-running autonomous workflows |

### Aegis's unique semantic
Every prompt-security product sees: `Prompt → Model → Response`
Aegis sees: `Agent → Tool Call → Action → Business Impact`

`transfer_money($25M)` is, to everyone else, "an API call." To Aegis it is "Financial transfer · Risk 95 · FIN-WIRE-001 · DENY · MITRE T1657."

**The semantic is the moat. Distribution is the strategy.**

The advantage is not the idea — Microsoft / Anthropic / OpenAI / Wiz / CrowdStrike are all exploring this. The advantage is **execution + adoption + speed + distribution**.

---

## 4. THE PHASES (REORDERED per v2 feedback)

### PHASE 0 — STOP (effective immediately)

Freeze:
- New detection rules
- New policy engines
- New attack simulations
- New architecture sprints

Exception: pilot-customer-reported gaps · production incidents.

**Eng split for the next 90 days: 80 % product, 20 % security.**

---

### PHASE 1 — REAL SaaS (Sprint 1, Week 1)

#### Goal
Replace `docker exec curl tenant_id agent_id api_key` with `Signup → Workspace → Dashboard`.

#### Build

**Backend**
- `POST /signup` (public, in `services/identity/router.py`) — atomic txn: workspace + first user (OWNER role) + JWT. **Does not yet create an agent.** Customer creates the agent in Phase 2 flow.
- `POST /signup/oauth/google` — Google OAuth.
- `POST /workspace/invite` — owner invites teammate.
- `POST /auth/refresh` — finish the half-built refresh-token endpoint in `services/identity/router.py`.
- Add `Role` enum: `OWNER · ADMIN · SECURITY_ANALYST · DEVELOPER · READ_ONLY`. Wire `verify_role(*allowed)` into `services/gateway/auth.py`.

**Frontend**
- `ui/src/pages/Signup.jsx` (NEW) — email/password + Google OAuth.
- `ui/src/pages/Login.jsx` — add "Sign up" link at bottom.
- `ui/src/pages/WorkspaceSettings.jsx` (NEW) — name, plan, members. Collapses scattered Settings pages.
- `ui/src/App.jsx` — routes `/signup` (public), gate root by `first_login`.

**Workspace data model**
```
Workspace (existing tenant rebranded "workspace" in the UI)
  ├── Users (Owner / Admin / SecurityAnalyst / Developer / ReadOnly)
  ├── Agents
  ├── Policies
  ├── Incidents
  ├── Audit Logs
  └── Billing
```

#### DoD
- Anyone hitting `https://ha.aegisagent.in/signup` creates a workspace + lands in a dashboard in **under 60 seconds.** Zero curl. Zero docker exec.

#### Estimate **5 dev days**

---

### PHASE 2 — AGENT ONBOARDING (Sprint 2, Week 2)

#### Goal
Customer presses **+ Add Agent** → picks integration → names it → presses **Generate Aegis Key** → copies install snippet → runs SDK → first decision arrives. **The customer's LLM key never leaves their machine.**

#### Build

**Backend**
- `POST /agents/wizard` (NEW in `services/registry/router.py`) — composes 3 existing endpoints into one: create agent + whitelist standard 8 tools + mint `acp_...` Aegis key. Returns `{agent_id, aegis_api_key, install_snippet}` in one response. Behind customer JWT (no INTERNAL_SECRET).
- `GET /agents/wizard/install-snippet/{agent_id}/{provider}` — returns a SDK-specific copy-paste block, pre-filled with the customer's `tenant_id`, `agent_id`, and `aegis_api_key`. **Never returns or asks for a Claude/OpenAI/Bedrock key.**

**Frontend** — `ui/src/pages/OnboardingWizard.jsx` (NEW), 3 screens:

**Step 1 — Pick integration**
8 cards: Claude · OpenAI · Cursor · Claude Code · OpenHands · CrewAI · LangChain · Custom

**Step 2 — Name + classification**
- Agent name (e.g. "Finance Agent")
- Risk level: low / medium / high
- (NO LLM API KEY FIELD — explicitly call this out in the UI: "Your Claude key stays on your machine. Aegis never sees it.")

**Step 3 — Install snippet + live "waiting…" panel**
```bash
pip install aegis-anthropic
export AEGIS_API_KEY=acp_a1b2c3...
export AEGIS_TENANT_ID=...
export AEGIS_AGENT_ID=...
export ANTHROPIC_API_KEY=sk-ant-...   # stays on YOUR machine
```
```python
from aegis_anthropic import AegisAnthropic
client = AegisAnthropic(aegis_endpoint="https://ha.aegisagent.in")
# use exactly like you used Anthropic() before
```

Under the snippet, an SSE-subscribed panel: **"⏳ Waiting for your agent's first tool call…"** → auto-advances to **"✅ First decision received! [View it ↗]"** when a `/execute` with that `agent_id` lands.

#### Why no LLM-key form
- Enterprise security teams reject vendors that store their LLM keys.
- This is the CrowdStrike / Datadog / Wiz pattern. The agent is on the customer's endpoint; we give them an Aegis-issued key, that's it.
- The brutal-test architecture today already works this way — don't break it.

#### DoD
- A customer adds their first agent in **under 5 minutes**. They see their first decision arrive in the wizard's "waiting…" panel in **under 10 minutes** total from signup. Their Anthropic key never touches our infra.

#### Estimate **5 dev days**

---

### PHASE 3 — SHADOW MODE + INVENTORY + INCIDENTS (Sprint 3, Weeks 3-4)

This is the most important phase. **Shadow mode + Agent Inventory landing page = the things that convert pilots into customers.**

#### 3.1 Shadow Mode (the trust builder)

**Why**
Every security vendor follows: `Observe → Recommend → Enforce`. Not `Install → Break production`.

**Default state for every new workspace:** 14-day shadow mode. Aegis records every decision as `would_have_blocked` instead of actually blocking. Customer's prod is never affected. After 14 days customer reviews the "would have blocked" report and clicks **Enforce**.

**Build**

Backend
- Migration: `workspaces.shadow_mode_until` timestamp, defaults to `now() + interval '14 days'` on workspace create.
- `services/gateway/middleware.py` — in the deny/escalate path:
  ```python
  if workspace.shadow_mode_until > now():
      audit_event(decision="would_have_blocked", original_decision=outcome, ...)
      return AllowResponse(annotation="shadow_mode")  # HTTP 200 with annotation
  ```
- `POST /workspace/exit-shadow-mode` (OWNER role).
- New event type emitted to SSE: `would_have_blocked` (UI distinguishes from `blocked`).

Frontend
- New widget on dashboard: **"Shadow mode · 11 days left · 14 would-have-blocked decisions [Review ↗]"**
- `ui/src/pages/ShadowModeReview.jsx` (NEW) — list of all `would_have_blocked` events: timestamp, agent, tool, args excerpt, policy_id, MITRE technique. Operator hits **Confirm Block** on each to lock policy in, or **Allow** to add to allow-list.

#### 3.2 Agent Inventory (the new landing page)

**Replace** `/flight-recorder` as the post-login homepage with a new `/dashboard`.

**Hero card**
```
37 Agents
  12 Claude
   9 OpenAI
   8 Cursor
   5 Claude Code
   3 Custom

High Risk:    4
Medium Risk: 18
Low Risk:    15
```

**Below:** quick-glance grid — last 24 h decisions per agent, status (healthy / shadow / quarantined), last activity.

**Backend** — `GET /workspace/inventory` aggregates from `registry.agents` + last-24h audit counts + risk-level + provider tag. Provider tag added in Phase 2 (`agents.metadata.provider`).

**Frontend** — `ui/src/pages/Dashboard.jsx` (NEW). Replaces FlightRecorder as the `/` route after login. FlightRecorder stays as `/audit-feed` for analysts.

#### 3.3 Live Feed (already works — promote it)

Move `LiveFeed.jsx` to top-nav. Add per-agent filter chip.

#### 3.4 Incidents (already 70 % built — surface it)

- Promote `services/security/incidents/storyline.py` output to a top-nav `/incidents`.
- Every deny/escalate AND every `would_have_blocked` creates an incident row.
- Incident card shape (per spec):
  ```
  INC-2026-06-15-001 · Severity: CRITICAL · Technique: T1136.001
  Agent: Finance Agent · Action: create_admin_user(role="root")
  Result: BLOCKED · Blast Radius: customer DB, payments, prod cluster
  [Acknowledge] [Escalate] [Allow-List] [Open Storyline]
  ```
- Reuse `services/security/remediation/executor.py` for the action buttons.

#### DoD
- A workspace owner sees: their agent inventory, their last 14 days of would-have-blocked, every incident, the live feed — all behind login. The dashboard answers "how many agents do we have, what are they doing, what risks are showing up" in 10 seconds.

#### Estimate **10 dev days**

---

### PHASE 4 — 3 EXTERNAL PILOTS (Month 2-3, calendar work)

#### Audience — NOT friends

Friends say "looks good bro." That kills product-market fit.

**Target personas (who will complain and force product-market fit):**
- **Startup CTO** — early-adopter, decision authority, ships fast
- **DevOps / Platform Lead** — operational pain, infra buyer, will catch shadow-mode UX issues
- **Security Engineer** — will rip apart the policy engine, demands receipts
- **AI Team Lead** — knows good agent UX, has Cursor/Claude Code in their daily flow

#### Target markets (in priority order)

| Market | Why first | Pitch hook | Outreach math |
|---|---|---|---|
| **Fintech** | money movement + PII + compliance pressure | live `$25M FIN-WIRE-001 DENY` demo | 30 → 5 → 1 |
| **Healthcare** | PHI / HIPAA enforcement risk | live `HC-PII-001 ESCALATE` on bulk SSN | 20 → 3 → 1 |
| **Internal AI / DevTool teams** | already use Cursor / Claude Code | "audit trail for every autonomous coding-agent action" | 30 → 5 → 1 |

#### Outreach channels
- Cold LinkedIn DMs (CTOs / DevOps leads / heads of security)
- Hacker News Show HN once dashboard is screenshot-able
- AI security communities (Discord, Slack workspaces, a16z AI Founders, YC AI Week)

#### Pilot offer template
> 60 days free. Aegis runs in **shadow mode for the first 14 days** — allow + log everything; we send you a weekly "would-have-blocked" report. Switch to enforce when you're confident. In return: 1 weekly 30-min call, bug reports, real agent traffic. No infra on your side.

#### DoD
**3 logos.** Each one running real agent traffic through Aegis. Each one with ≥ 1 prevented-incident screenshot for the case study.

---

### PHASE 5 — THREAT GRAPH + MITRE + BLAST RADIUS + REMEDIATION (Month 4)

#### When
Only after Phase 4 has 3 paying-or-letter-of-intent pilots. **These features are closing weapons for enterprise expansion deals, not opening pitches.**

#### Build (mostly surfacing, not building — code exists)

**Threat Graph** — `ui/src/pages/ThreatGraph.jsx` (NEW), full-screen React Flow reading `services/security/iag/graph.py`. The visualization:
```
Finance Agent
    ↓
Touched: customers table
Contains: SSN · Credit Card
Can Reach: Payment Service → Wire API
Blast Radius: $50 M
```

**MITRE coverage grid** — `<MitreCoverageGrid />` over `services/security/signal_registry.py` (34 techniques already mapped).

**Blast Radius dollar calculation** — sum over (reachable system × tagged value). System tags configured in `WorkspaceSettings`.

**Remediation panel** — every incident shows "what we did / would do": revoke key · kill tokens · page on-call · quarantine agent.

#### DoD
A CISO buyer demo answers all 5 enterprise-ready questions in one screen-share:
- Show MITRE ATT&CK ✓
- Show Blast Radius ✓
- Show Attack Path ✓
- Show Remediation ✓
- Show Audit Trail ✓ (already done — Decision Explorer + signed receipts)

#### Estimate **15 dev days**

---

### PHASE 6 — SOC 2 + ENTERPRISE + REVENUE (Month 5-6)

#### Build
- SOC 2 Type II readiness prep (start the 9-month process now — it's gated on time, not engineering)
- HIPAA BAA template + lawyer review
- EU + US data residency (replicate Terraform stack in `eu-west-1` + `us-east-1`)
- Stripe billing integration (model exists in `services/billing/` — wire it up)
- Self-hosted on-prem install path (use existing `infra/docker-compose.yml` + a "BYO infra" guide)
- White-glove migration tooling for pilot → paid conversion

#### DoD
First $ in. First annual contract. First enterprise procurement passed.

---

## 5. 6-MONTH ROADMAP

| Month | Phase | Deliverable | Outcome |
|---|---|---|---|
| **1** | 1 | Signup + Workspace + JWT + RBAC | `/signup` live · 60 s from new visitor to logged-in dashboard |
| **2** | 2 | Agent Wizard + SDK snippet + first decision | 5 min from "Add Agent" click to "first decision received" SSE event |
| **2-3** | 3 | Shadow mode + Agent Inventory + Live Feed + Incidents | Workspace owner sees inventory, would-have-blocked review, incidents on one screen |
| **3** | 4a | First-pilot outreach (Fintech) | Letters of intent in hand |
| **4** | 4b | 3 pilots running | Real traffic · weekly feedback · 3 prevented-incident screenshots |
| **5** | 5 | Threat Graph + MITRE + Blast Radius + Remediation | CISO demo answers every enterprise question |
| **6** | 6 | SOC 2 prep + Stripe + EU/US residency | First $ · first annual contract |

---

## 6. WHAT WE EXPLICITLY DO NOT BUILD

| Skip | Why |
|---|---|
| **Aegis stores customer LLM keys (BYOK proxy mode)** | Enterprise trust killer. SDK-on-endpoint is the *correct* architecture. Maybe revisit at $1M ARR if a buyer specifically asks. |
| **Multi-LLM orchestration ("switch GPT-4 → Claude if risk > 70")** | Solves zero pilot pain. Product complexity. |
| **30 new detection rules** | 95.8 % correctness · 0 misses. Coverage is fine. |
| **Per-agent docker sandbox we host** | Their runtime is their problem. We decide, they execute. CrowdStrike pattern. |
| **Friend pilots** | Friends lie. Use CTOs / DevOps leads / Security Eng / AI Team Leads who will complain. |
| **Mobile app** | Web is enough until $1M ARR |
| **White-label / partner program** | Direct sales until 10 paying customers |
| **Threat Graph / MITRE / Blast Radius BEFORE Phase 4 pilots ship** | These are demo features that don't close until people are already using the product |

---

## 7. THE STARTING GUN — 3 DECISIONS LEFT

Three picks needed to start Phase 1 work today:

### 7.1 Signup mechanism
- (a) Email + password — 1 d
- (b) Magic link — 2 d (needs SES sender)
- (c) Google OAuth only — 1.5 d
- **Recommendation:** ship (a) + (c) in Phase 1, defer (b)

### 7.2 Wizard shape
- (a) Opinionated 3-step (faster, less friction)
- (b) Optional / skippable
- **Recommendation:** (a) — the wizard IS the product on day 1

### 7.3 Shadow-mode duration
- (a) 14 days (industry standard, recommended)
- (b) 7 days (faster to revenue, more risk for customer)
- (c) Customer-configurable from start
- **Recommendation:** (a) — locked, with option for owner to extend or `exit-shadow-mode` early

---

## 8. FILES TO CREATE / MODIFY — BY PHASE

### Phase 1 (Real SaaS)

Backend
- `services/identity/router.py` — add `POST /signup`, `POST /signup/oauth/google`, `POST /workspace/invite`, finish `POST /auth/refresh`
- `services/identity/models.py` — `Role` enum, `Workspace.first_login`, `Workspace.shadow_mode_until` columns
- `services/identity/alembic/versions/<new>.py` — migration for those columns
- `services/gateway/auth.py` — `verify_role(*allowed)` middleware

Frontend
- `ui/src/pages/Signup.jsx` — NEW
- `ui/src/pages/WorkspaceSettings.jsx` — NEW (collapses RBAC + SSO + Billing)
- `ui/src/pages/Login.jsx` — add "Sign up" link
- `ui/src/App.jsx` — public `/signup` route, `first_login` gate
- `ui/src/services/authService.js` — `signup()`, `signupGoogle()`, `inviteTeammate()`

### Phase 2 (Agent Onboarding — no BYOK)

Backend
- `services/registry/router.py` — `POST /agents/wizard` (compose create+whitelist+mint-key behind customer JWT)
- `services/registry/router.py` — `GET /agents/wizard/install-snippet/{agent_id}/{provider}`
- `services/registry/service.py` — `create_agent_with_defaults(workspace, name, provider, risk_level)`
- `agents.metadata.provider` column (small migration)

Frontend
- `ui/src/pages/OnboardingWizard.jsx` — NEW, 3 screens (NO LLM-key field)
- `ui/src/pages/Agents.jsx` — replace Deploy button with `/onboarding`

### Phase 3 (Shadow + Inventory + Live + Incidents)

Backend
- `services/gateway/middleware.py` — shadow-mode downgrade path: deny/escalate → `would_have_blocked` annotated 200
- `POST /workspace/exit-shadow-mode` (OWNER role)
- `GET /workspace/inventory` — aggregate inventory endpoint
- New SSE event type `would_have_blocked` (in `_publish_event` switch)

Frontend
- `ui/src/pages/Dashboard.jsx` — NEW landing page with Agent Inventory hero card
- `ui/src/pages/ShadowModeReview.jsx` — NEW, list + bulk-confirm / allow-list
- `ui/src/pages/Incidents.jsx` — exists, promote to top-nav + enrich with `would_have_blocked` filter
- `ui/src/pages/LiveFeed.jsx` — exists, promote to top-nav + per-agent filter chip
- `ui/src/App.jsx` — `/` redirects to `/dashboard` (not `/flight-recorder`)

### Phase 4 (Pilots — calendar work, not code)
Outreach docs, pricing page, case-study template.

### Phase 5 (Threat Graph + MITRE + Blast Radius + Remediation)

Frontend
- `ui/src/pages/ThreatGraph.jsx` — NEW, React Flow over `/iag/agents/{id}` payload
- `ui/src/components/security/MitreCoverageGrid.jsx` — NEW, from `signal_registry.py`
- `ui/src/components/incidents/BlastRadiusCard.jsx` — NEW, on every incident
- `ui/src/components/incidents/RemediationPanel.jsx` — NEW, executor.py action buttons

Backend
- `services/security/threatgraph/router.py` — NEW, combines IAG + storyline + recent decisions
- Blast-radius dollar formula on the workspace (system-value tags configurable in `WorkspaceSettings.jsx`)

### Phase 6 (SOC 2 + Enterprise + Revenue)
Stripe integration in `services/billing/`, residency replication in `infra/terraform/environments/`, lawyer-reviewed BAA template under `docs/compliance/`.

---

## 9. THE "STRIPE FOR AGENT SECURITY" MENTAL MODEL

The product flow we're building toward:

```
Signup
   ↓
Create Workspace
   ↓
Create Agent
   ↓
Copy SDK snippet
   ↓
Run agent
   ↓
See decisions
   ↓
Upgrade plan
```

Every step must be self-serve. Every step must work in under 60 seconds. Every step must answer one specific buyer question.

**If a user needs ANY of these to make Aegis work:**
- `docker exec`
- `curl`
- `tenant creation script`
- `manual JWT`
- `admin access`

…**then we don't have a product yet.**

---

## 10. SUCCESS METRIC

> A customer signs up. Adds their agent. Runs it. Aegis starts protecting it. Without talking to me.

That's it. That's the only metric that matters for the next 60-90 days.

---

## 11. THE STARTING GUN

Pick:
1. **Signup:** (a) email+password / (b) magic link / (c) Google OAuth only
2. **Wizard shape:** (a) opinionated 3-step / (b) optional
3. **Shadow duration:** (a) 14 d / (b) 7 d / (c) configurable

Tell me a/b/c × 3 and I start Phase 1 Sprint 1 the same day. End of Week 1 = `/signup` live on `https://ha.aegisagent.in`. End of Week 2 = first real CTO can self-onboard their Claude in 5 minutes. End of Week 4 = shadow-mode + inventory + incidents shipped. Month 3 = pilot outreach. Month 4 = 3 logos.

---

## 12. UI INVENTORY + CLEANUP PLAN (the 49 → 15 refactor)

> **Hard rule from the founder:** *"Don't think to remove the service things — how to use it in better way and show it in the UI."* No backend service or endpoint gets deleted in this refactor. Only the UI gets consolidated.

### 12.1 Headline numbers (audited 2026-06-15)

| Metric | Count |
|---|---|
| Pages (`ui/src/pages/*.jsx`) | **49** |
| Routes in `App.jsx` | 54 |
| Dead pages (no route + no imports) | **0** |
| Common components | 13 |
| Custom hooks | 5 |
| React contexts | 2 |
| Backend service objects in `api.js` | 34 |
| Backend services under `services/` | 27 |
| Dead backend code | **0** |
| Total UI LOC | 24,887 |
| **v1 customer-facing target** | **15 pages** |
| Hidden behind `/advanced` (KEEP-V2) | 25 pages |
| Hidden behind `/admin` (RBAC-gated) | 6 pages |
| **DELETE (DEMO-ONLY)** | **3 pages** |

### 12.2 Page-by-page verdict (every one of the 49)

| File | LOC | Routed | Backend services it consumes | Verdict |
|---|---|---|---|---|
| `Login.jsx` | 238 | / | `authService` | **KEEP-V1** |
| `Signup.jsx` | – | – | – | **CREATE (Phase 1)** |
| `OnboardingWizard.jsx` | – | – | – | **CREATE (Phase 2)** |
| `Dashboard.jsx` (new) | – | – | `registryService`, `auditService`, `incidentService` | **CREATE (Phase 3)** — replaces FlightRecorder as `/` landing |
| `ShadowModeReview.jsx` (new) | – | – | `shadowService`, `auditService` | **CREATE (Phase 3)** |
| `AgentSnapshot.jsx` (new) | – | – | `registryService`, `fleetService`, `billingService`, `graphService` | **CREATE (Phase 3)** — replaces 4 separate agent pages |
| `FlightRecorder.jsx` | 339 | `/flight-recorder` | `flightService`, `receiptService`, `transparencyService` | **KEEP-V1** (move from `/` to `/audit-feed`) |
| `Agents.jsx` | 497 | `/agents` | `registryService` | **KEEP-V1** — list + create + delete |
| `LiveFeed.jsx` | 368 | `/live-feed` | `auditService` + SSE | **KEEP-V1** — promote to primary nav |
| `Incidents.jsx` | 742 | `/incidents` | `incidentService`, `socService` | **KEEP-V1** — enrich w/ remediation panel + blast-radius |
| `DecisionExplorer.jsx` | 360 | `/decision-explorer/:id` | `flightService` | **KEEP-V1** — drill-down only |
| `Settings.jsx` | 97 | `/settings` | (nav hub) | **KEEP-V1** — collapses 7 settings pages into tabs |
| `AuditLogs.jsx` | 877 | `/audit-logs` | `auditService`, `auditExportService` | **KEEP-V2** — under `/advanced` |
| `Forensics.jsx` | 637 | `/forensics` | `forensicsService` | **KEEP-V2** — under `/advanced`; surface link from incident detail |
| `IdentityGraph.jsx` | 399 | `/identity-graph` | `graphService` | **KEEP-V2** — feeds Phase 5 ThreatGraph |
| `AgentTopology.jsx` | 213 | `/agent-topology` | `graphService` | **MERGE → /agents/:id** (tab "Topology") |
| `AgentHealth.jsx` | 242 | `/agent-health` | `fleetService` | **MERGE → /agents/:id** (tab "Health") |
| `AgentCost.jsx` | 190 | `/agent-cost` | `fleetService`, `billingService` | **MERGE → /agents/:id** (tab "Cost") |
| `AgentProfile.jsx` | 655 | `/agent-profile/:id` | `auditService`, `registryService` | **MERGE → /agents/:id** (tab "Profile") |
| `Fleet.jsx` | 201 | `/fleet` | `fleetService`, `auditService` | **MERGE → /dashboard** (hero card uses same data) |
| `AgentPlayground.jsx` | 616 | `/playground` | `playgroundService`, `registryService` | **KEEP-V2** — under `/advanced` (dev tool) |
| `PolicyBuilder.jsx` | 758 | `/policy-builder` | `policyService`, `registryService` | **MERGE → /policies** (tab "Editor") |
| `PolicySim.jsx` | 317 | `/policy-sim` | `policyService` | **MERGE → /policies** (tab "Simulator") |
| `PolicyPlayground.jsx` | 380 | `/policy-playground` | `policyPlaygroundService` | **MERGE → /policies** (tab "Staging") |
| `PolicyAnalytics.jsx` | 634 | `/policy-analytics` | `auditService` | **MERGE → /policies** (tab "Analytics") |
| `ShadowMode.jsx` | 479 | `/shadow-mode` | `shadowService` | **KEEP-V2** — surface via `/dashboard` shadow widget |
| `ApprovalInbox.jsx` | 333 | `/approval-inbox` | `auditService`, `autonomyService`, `autoResponseService` | **MERGE → /incidents** (filter chip "Needs approval") |
| `AutonomyContracts.jsx` | 402 | `/autonomy` | `autonomyService` | **MERGE → /policies** (tab "Autonomy contracts") |
| `AutoResponse.jsx` | 1243 | `/auto-response` | `autoResponseService`, `playbookService` | **KEEP-V2** — under `/advanced` |
| `Playbooks.jsx` | 507 | `/playbooks` | `playbookService` | **KEEP-V2** — under `/advanced` |
| `KillSwitch.jsx` | 327 | `/kill-switch` | `killSwitchService`, `auditService` | **KEEP-V2** — `/admin` only; promote as global modal trigger |
| `RiskEngine.jsx` | 697 | `/risk-engine` | `riskService`, `auditService` | **MERGE → /dashboard** (hero "Risk trend" card) |
| `Observability.jsx` | 755 | `/observability` | `auditService`, `decisionService`, `riskService` | **KEEP-V2** — under `/advanced` (SRE view) |
| `SecurityDashboard.jsx` | 881 | `/security` | 4 services | **MERGE → /dashboard** (its hero metrics become the new landing hero) |
| `SessionExplorer.jsx` | 244 | `/sessions` | `flightService` | **MERGE → /decision-explorer** (tab "Session view") |
| `Evaluation.jsx` | 341 | `/evaluation` | `evaluationService` | **KEEP-V2** — under `/advanced` |
| `AttackSimulation.jsx` | 462 | `/attack-sim` | `playgroundService` | **STUB → DEFER v2** — backend run-endpoint not wired |
| `ThreatIntel.jsx` | 304 | `/threat-intel` | `threatIntelService` | **KEEP-V2** — under `/advanced`; feeds Phase 5 |
| `Compliance.jsx` | 261 | `/compliance` | `complianceService` | **KEEP-V2** — under `/admin`; surface `/compliance/tool-ledger` orphan endpoint here |
| `Billing.jsx` | 974 | `/billing` | `billingService` | **KEEP-V2** — under `/admin` |
| `QuotaManagement.jsx` | 228 | `/quota` | `tenantService` | **MERGE → /settings** (tab "Quota") |
| `ScheduledReports.jsx` | 394 | `/scheduled-reports` | `scheduledReportsService` | **MERGE → /settings** (tab "Reports") |
| `SystemHealth.jsx` | 290 | `/system-health` | `dashboardService` | **KEEP-V2** — under `/admin` |
| `AdminConsole.jsx` | 375 | `/admin` | `adminService`, `auditService`, `dashboardService` | **KEEP-V2** — under `/admin` |
| `UserManagement.jsx` | 337 | `/users` | `userService` | **MERGE → /settings** (tab "Team") |
| `RBAC.jsx` | 365 | `/rbac` | `registryService` | **MERGE → /settings** (tab "Roles & Permissions") |
| `SsoSettings.jsx` | 267 | `/sso` | `ssoService` | **MERGE → /settings** (tab "SSO") |
| `SiemSettings.jsx` | 261 | `/siem` | `siemService` | **MERGE → /settings** (tab "SIEM") |
| `WebhookSettings.jsx` | 221 | `/webhook-settings` | `webhookService` | **MERGE → /settings** (tab "Webhooks") |
| `DeveloperPanel.jsx` | 653 | `/developer` | `api` (keys, billing, risk) | **MERGE → /settings** (tab "API keys & SDK") |
| `Notifications.jsx` | 213 | `/notifications` | `notificationService` | **KEEP-V2** — surfaces via `NotificationCenter` component already |
| `LiveDemo.jsx` | 589 | `/live-demo` | `demoService`, `auditService` | **DELETE — DEMO-ONLY** (kill from sidebar + route) |
| `Pricing.jsx` | 413 | `/pricing` | – | **DELETE FROM APP — move to marketing landing** (out of authenticated UI) |
| `ExecutiveDashboard.jsx` | 472 | `/executive` | `riskService`, `complianceService`, `dashboardService`, `threatIntelService` | **DELETE — DEMO-ONLY** (redirect `/executive` → `/dashboard`) |

### 12.3 What to DELETE (the only 3 removals)

| Page | LOC | Why delete | Where its data goes |
|---|---|---|---|
| `LiveDemo.jsx` | 589 | Public Groq sandbox is a sales toy. Customers don't need it after signup. | Replace with `/onboarding` SDK snippet — same "see your first decision" moment |
| `Pricing.jsx` | 413 | Marketing pages don't belong inside the authenticated app | Move to standalone marketing site or `/pricing` outside `/app/*` |
| `ExecutiveDashboard.jsx` | 472 | C-suite view is duplicate of new Dashboard hero metrics | Redirect route → `/dashboard` (which carries the same data) |

**Sidebar removal:** drop `/live-demo` from primary nav (`Sidebar.jsx:22-30`).

**Backend impact: zero.** `demoService.runGroqAgent` becomes unused but stays on the server (no harm).

### 12.4 What to MERGE (4 consolidation groups)

**Group A — Policies (4 pages → 1)**
```
PolicyBuilder + PolicySim + PolicyPlayground + PolicyAnalytics
                    ↓
       /policies   (tabs: Editor | Simulator | Staging | Analytics | Autonomy)
```
- Backend services preserved: `policyService`, `policyPlaygroundService`, `autonomyService`, `auditService`
- Code: build `/policies/index.jsx` as a tab router; reuse each existing page as a tab body. Net saving: 4 routes → 1, but no logic deleted.

**Group B — Agent detail (5 pages → 1)**
```
AgentProfile + AgentHealth + AgentCost + AgentTopology + RBAC permissions card
                              ↓
           /agents/:id    (tabs: Overview | Health | Cost | Topology | Permissions)
```
- Backend services preserved: `registryService`, `fleetService`, `billingService`, `graphService`, `auditService`
- Customer hits **one URL per agent** instead of jumping between 5 pages.

**Group C — Settings (7 pages → 1)**
```
Settings (current hub) + UserManagement + RBAC + SsoSettings + SiemSettings + WebhookSettings + QuotaManagement + ScheduledReports + DeveloperPanel
                                            ↓
                     /settings   (tabs: Workspace | Team | Roles | SSO | API keys | Webhooks | SIEM | Reports | Quota)
```
- 9 separate routes → 1 tabbed page
- Backend services preserved: `userService`, `ssoService`, `siemService`, `webhookService`, `scheduledReportsService`, `tenantService`, plus the `/api-keys` endpoints on the api object

**Group D — Decision drill-down (2 pages → 1)**
```
DecisionExplorer + SessionExplorer
              ↓
   /decision-explorer/:request_id   (tabs: Graph | Timeline | Session view | JSON)
```
- Backend service preserved: `flightService`
- Already share the same data source — consolidating is pure UX win.

**Net page count after merges: 49 → 24. After /advanced + /admin grouping: 15 customer-facing.**

### 12.5 The v1 customer-facing 15-page surface

| # | Route | Page | Replaces (old) | Backend services it feeds from |
|---|---|---|---|---|
| 1 | `/signup` | `Signup.jsx` (new) | – | `identityService` (new `POST /signup`) |
| 2 | `/login` | `Login.jsx` | – | `authService` |
| 3 | `/onboarding` | `OnboardingWizard.jsx` (new) | LiveDemo | `registryService.wizard`, `agentService` |
| 4 | `/dashboard` | `Dashboard.jsx` (new) | FlightRecorder root + ExecutiveDashboard + SecurityDashboard + Fleet + RiskEngine | `registryService`, `auditService`, `incidentService`, `riskService`, `shadowService` |
| 5 | `/agents` | `Agents.jsx` | (unchanged) | `registryService` |
| 6 | `/agents/:id` | `AgentSnapshot.jsx` (new, tabbed) | AgentProfile + AgentHealth + AgentCost + AgentTopology + per-agent RBAC | `registryService`, `fleetService`, `billingService`, `graphService`, `auditService`, `iagService` |
| 7 | `/incidents` | `Incidents.jsx` (enriched) | Incidents + ApprovalInbox | `incidentService`, `autoResponseService`, `remediationService`, `forensicsService`, `graphService` |
| 8 | `/live-feed` | `LiveFeed.jsx` | (unchanged, promoted to top nav) | SSE + `auditService` |
| 9 | `/decision-explorer/:request_id` | `DecisionExplorer.jsx` (tabbed w/ SessionExplorer) | DecisionExplorer + SessionExplorer | `flightService`, `receiptService`, `transparencyService` |
| 10 | `/policies` | `Policies.jsx` (new tab router) | PolicyBuilder + PolicySim + PolicyPlayground + PolicyAnalytics + AutonomyContracts | `policyService`, `policyPlaygroundService`, `autonomyService`, `auditService` |
| 11 | `/shadow-review` | `ShadowModeReview.jsx` (new) | (data from ShadowMode page surfaced) | `shadowService`, `auditService` |
| 12 | `/settings` | `Settings.jsx` (tabbed, 9 sub-pages) | Settings + UserManagement + RBAC + SsoSettings + SiemSettings + WebhookSettings + QuotaManagement + ScheduledReports + DeveloperPanel | `userService`, `ssoService`, `siemService`, `webhookService`, `scheduledReportsService`, `tenantService`, api keys |
| 13 | `/advanced` | `AdvancedHub.jsx` (new index) | – | (gateway page with cards to /audit-logs · /forensics · /observability · /playground · /threat-intel · /evaluation · /playbooks · /auto-response · /identity-graph · /shadow-mode) |
| 14 | `/admin` | `AdminConsole.jsx` (promoted) | AdminConsole + SystemHealth + KillSwitch + Compliance + Billing | `adminService`, `dashboardService`, `killSwitchService`, `complianceService`, `billingService` |
| 15 | `/notifications` | `Notifications.jsx` (or just modal — see 12.9) | (unchanged) | `notificationService` |

### 12.6 Every backend service has a UI home — full mapping

The user's hard rule. None of these 27 services lose their UI surface. Some get a NEW or BETTER home in the cleaned-up surface.

| Backend service | Endpoints | v1 UI home |
|---|---|---|
| `services/identity/` | `/auth/*` + `/signup` (new) | `/signup`, `/login`, `/settings → Team / Roles / SSO` |
| `services/gateway/` | `/execute`, `/events/stream`, `/security/posture`, `/system/health` | `/onboarding` (first decision waits), `/live-feed`, `/admin` (system health) |
| `services/policy/` | `/policy/simulate`, `/policy/test`, `/policy/upload` | `/policies → Editor / Simulator / Staging` |
| `services/decision/` | `/decision/history`, `/decision/summary`, `/decision/kill-switch` | `/decision-explorer`, `/admin → Kill Switch` |
| `services/audit/` | `/audit/logs`, `/audit/logs/summary`, `/audit/risk-trend`, `/audit/agent-findings`, `/audit/shadow/*` | `/dashboard` hero, `/live-feed`, `/shadow-review`, `/advanced → Audit Logs` |
| `services/registry/` | `/agents`, `/agents/{id}/permissions`, `/agents/wizard` (new) | `/agents`, `/agents/:id`, `/onboarding` |
| `services/flight_recorder/` | `/flight/timelines`, `/flight/timeline/by-request`, `/flight/sessions` | `/decision-explorer`, `/advanced → Flight Recorder` |
| `services/security/incidents/` | `/incidents`, `/incidents/{id}/actions`, `/incidents/{id}/comments` | `/incidents`, `/dashboard` open-incidents card |
| `services/security/iag/` | `/iag/agents/{id}`, `/iag/incidents/{id}/blast-radius` | `/agents/:id → IAG panel` (was orphan), `/incidents → blast-radius card` (was orphan), Phase 5 `/threat-graph` |
| `services/security/remediation/` | `/remediation/policy`, `/remediation/dry-run`, `/remediation/incidents/{id}/replay` | `/incidents → Remediation panel on each incident` (was orphan) |
| `services/security/threatintel/` | `/threat-intel/summary`, `/threat-intel/ip`, `/threat-intel/domain` | `/advanced → Threat Intel`, Phase 5 `/threat-graph` |
| `services/security/signal_registry.py` | (Python module — 34 MITRE-mapped signals) | Phase 5 `<MitreCoverageGrid />` on `/dashboard` + `/threat-graph` |
| `services/forensics/` | `/forensics/investigation`, `/forensics/blast-radius`, `/forensics/replay` | `/incidents → Forensics drawer per incident`, `/advanced → Forensics` |
| `services/identity_graph/` | `/graph/agents`, `/graph/blast-radius`, `/graph/risky-paths`, `/graph/runtime-relationships`, `/graph/trust-boundaries`, `/graph/compromise/simulate` | `/agents/:id → Topology tab`, Phase 5 `/threat-graph` |
| `services/behavior/` | `/analyze`, `/check` (internal) | `/dashboard` — surface as "Risk score trend" card (was orphan) |
| `services/insight/` | `/insights/recent` | `/dashboard` "Recent insights" widget (was orphan) |
| `services/learning/` | – (training pipeline) | not surfaced in v1 (internal) |
| `services/usage/` | `/usage/dashboard` | `/admin → Billing` |
| `services/api/` (api keys) | `/api-keys`, `/api-keys/validate` | `/settings → API keys`, `/onboarding` (auto-mint in wizard) |
| `services/mcp_server/` | (stdio MCP server) | docs page in `/settings → API keys & SDK` (install instructions for Claude Desktop / Cursor / Claude Code) |
| `services/audit/compliance` | `/compliance/eu-ai-act`, `/compliance/soc2`, `/compliance/nist-ai-rmf`, `/compliance/tool-ledger` | `/admin → Compliance` (tool-ledger was orphan, surface here) |
| `services/audit/evaluation` | `/audit/evaluation/datasets`, `/audit/evaluation/jobs` | `/advanced → Evaluation` |
| `services/autonomy/` | `/autonomy/contracts`, `/autonomy/violations` | `/policies → Autonomy Contracts tab`, `/incidents → violations as incidents` |
| `services/autoresponse/` | `/auto-response/rules`, `/auto-response/history`, `/auto-response/feedback` | `/advanced → Auto-Response` |
| `services/playbooks/` | `/playbooks`, `/playbooks/run` | `/advanced → Playbooks` |
| `services/notifications/` | `/notifications`, `/notifications/{id}/read` | global `<NotificationCenter />` component in Topbar (already wired) |
| `services/scheduled_reports/` | `/reports/scheduled` | `/settings → Reports` |

**Zero backend services lose their UI home. Every orphan endpoint gets a v1 surface.**

### 12.7 Orphan-endpoint rescue — concrete

Endpoints today with NO UI consumer get a new home:

| Orphan endpoint | New v1 home | What we render there |
|---|---|---|
| `POST /analyze` (`services/behavior/`) | `/dashboard` | "Behavior risk score" sparkline card |
| `POST /check` (`services/behavior/`) | `/agents/:id → Overview` | "Last behavior check" badge |
| `GET /insights/recent` (`services/insight/`) | `/dashboard` | "Recent insights" list (5 items) |
| `GET /iag/agents/{id}` (`services/security/iag/`) | `/agents/:id → Identity & Access` | full IAG panel (replaces old IdentityGraph page for per-agent view) |
| `GET /iag/incidents/{id}/blast-radius` | `/incidents → detail drawer` | blast-radius card |
| `GET /remediation/policy` | `/incidents → detail drawer` | "Remediation policy that fired" |
| `POST /remediation/dry-run` | `/incidents → Allow-list flow` | "Dry-run this remediation" button |
| `POST /remediation/incidents/{id}/replay` | `/incidents → detail drawer` | "Replay this remediation" button |
| `GET /compliance/tool-ledger` | `/admin → Compliance` | "Tool-to-rule ledger" tab (new) |
| `POST /compliance/board-report` | `/admin → Compliance` | "Generate board report" button |
| `GET /voice/status`, `/voice/token` | (defer — voice agent feature) | Phase 6 |

### 12.8 Sidebar restructure — 3-tier nav (concrete spec)

Current sidebar has **1 primary tier with 7 mixed items** (`Sidebar.jsx:21-58`) + 18 ops + admin. Rewrite to:

**Primary (always visible, 6 items):**
```
🏠  Dashboard          (g d)
🤖  Agents             (g a)
🚨  Incidents          (g i)
📡  Live Feed          (g l)
📜  Policies           (g p)
⚙️   Settings          (g s)
```

**Advanced (collapsed by default, 10 items — analyst tools):**
```
📊  Audit Logs
🔍  Forensics
📈  Observability
🧪  Agent Playground
🌐  Threat Intel
🎯  Evaluation
📕  Playbooks
🤖  Auto-Response
🕸️  Identity Graph
🎭  Shadow Mode (legacy)
```

**Admin (RBAC-gated, OWNER/ADMIN only, 4 items):**
```
🏥  System Health
💳  Billing
✅  Compliance
🔴  Kill Switch
```

**Hidden / removed:**
```
LiveDemo · Pricing · ExecutiveDashboard · Fleet · AgentHealth · AgentCost · AgentTopology · AgentProfile · PolicyBuilder · PolicySim · PolicyPlayground · PolicyAnalytics · AutonomyContracts · UserManagement · RBAC · SsoSettings · SiemSettings · WebhookSettings · QuotaManagement · ScheduledReports · DeveloperPanel · SessionExplorer · SecurityDashboard · RiskEngine · ApprovalInbox · AdminConsole
```
…all still routed (under `/advanced/*` or `/settings/*` or `/admin/*`), just hidden from primary sidebar.

### 12.9 Component library (no surgery needed — already clean)

| Component | Status |
|---|---|
| `Button.jsx`, `Card.jsx`, `Modal.jsx`, `ConfirmDialog.jsx`, `SkeletonLoader.jsx`, `Toast.jsx` | ✅ used 10+ pages, keep |
| `DataTable.jsx` | ⚠ used by 1 page (AuditLogs) — leave for now, may extract later |
| `ErrorBoundary.jsx`, `IncidentOverlay.jsx`, `KeyboardCheatsheet.jsx`, `CommandPalette.jsx` | ✅ App-root utilities, keep |
| `ConnectorPrimitives.jsx` | ✅ shared by SSO/SIEM/Webhook settings — keep |
| `NotificationCenter.jsx` | ✅ persistent inbox in Topbar — keep |
| Hooks: `useAuth`, `useAgents`, `useHotkeys`, `useRole`, `useSSE` | ✅ all 5 working, all in use |
| Contexts: `AuthContext`, `AgentContext` | ✅ both working, both consumed by 16+ pages |

**No merges, no deletions. The component layer is the cleanest part of the codebase.**

### 12.10 Stubs / broken / fix-list

| File | What's broken | Fix |
|---|---|---|
| `Sidebar.jsx:22` — primary nav `/live-demo` | Page being deleted | Remove the nav item in same PR as page delete |
| `AttackSimulation.jsx` | Backend `.execute()` not wired for attack-scenario context (only single-tool playground) | Defer page until backend gets `POST /attack-sim/run` endpoint — for v1 redirect `/attack-sim` → `/onboarding` with a "coming soon" toast |
| `QuotaManagement.jsx` | Display-only, no edit UX | Acceptable for v1; add edit UI in Phase 4 |

### 12.11 New pages to CREATE (only 5 new files, mapped to phases)

| File | Phase | Replaces |
|---|---|---|
| `ui/src/pages/Signup.jsx` | Phase 1 | (new product surface) |
| `ui/src/pages/OnboardingWizard.jsx` | Phase 2 | LiveDemo |
| `ui/src/pages/Dashboard.jsx` (+ `components/dashboard/AgentInventoryHero.jsx`, `HeroMetricsCard.jsx`) | Phase 3 | FlightRecorder as `/`, ExecutiveDashboard, SecurityDashboard, Fleet, RiskEngine |
| `ui/src/pages/ShadowModeReview.jsx` | Phase 3 | (new surface, data from ShadowMode) |
| `ui/src/pages/AgentSnapshot.jsx` (tabbed) | Phase 3 | AgentProfile + AgentHealth + AgentCost + AgentTopology |

### 12.12 Files to MODIFY (UI consolidation — no backend deletes)

| File | Change |
|---|---|
| `ui/src/components/Layout/Sidebar.jsx:21-58` | Rewrite nav array into the 3-tier structure (primary 6 / advanced 10 / admin 4) |
| `ui/src/App.jsx:107-149` (routes) | Trim 54 → ~30. Add `/signup`, `/onboarding`, `/dashboard`, `/shadow-review`. Redirect `/` → `/dashboard`. Redirect `/executive` → `/dashboard`. Redirect `/live-demo` → `/onboarding`. Tab-route under `/policies`, `/agents/:id`, `/settings`, `/decision-explorer`. |
| `ui/src/pages/Incidents.jsx` | Add 3 panels per incident: Blast Radius (from `/iag`), Remediation (from `/remediation`), Forensics quick-link (from `/forensics`) |
| `ui/src/pages/Policies.jsx` (new tab router) | 5 tabs, each lazy-loads one of the 5 existing policy pages as a body |
| `ui/src/pages/Settings.jsx` | 9 tabs, each lazy-loads one of the 9 existing settings pages |

### 12.13 Backend preservation guarantee (the user's hard rule)

**Every backend service stays. Verified 1-to-1:**

```
services/identity/         ✓ surfaced in /signup, /login, /settings
services/gateway/          ✓ surfaced in /onboarding, /live-feed, /admin
services/policy/           ✓ surfaced in /policies (Editor + Simulator tabs)
services/decision/         ✓ surfaced in /decision-explorer, /admin (kill switch)
services/audit/            ✓ surfaced in /dashboard, /live-feed, /shadow-review, /advanced
services/registry/         ✓ surfaced in /agents, /agents/:id, /onboarding
services/flight_recorder/  ✓ surfaced in /decision-explorer, /advanced
services/security/incidents/    ✓ surfaced in /incidents, /dashboard
services/security/iag/     ✓ surfaced in /agents/:id, /incidents (was orphan — NOW LIVE)
services/security/remediation/  ✓ surfaced in /incidents (was orphan — NOW LIVE)
services/security/threatintel/  ✓ surfaced in /advanced, Phase 5 /threat-graph
services/security/signal_registry/  ✓ surfaced in Phase 5 MITRE grid
services/forensics/        ✓ surfaced in /incidents drawer + /advanced
services/identity_graph/   ✓ surfaced in /agents/:id, Phase 5 /threat-graph
services/behavior/         ✓ surfaced in /dashboard risk card (was orphan — NOW LIVE)
services/insight/          ✓ surfaced in /dashboard insights card (was orphan — NOW LIVE)
services/learning/         ✓ internal pipeline, no UI surface (correct)
services/usage/            ✓ surfaced in /admin → Billing
services/api/              ✓ surfaced in /settings → API keys, /onboarding
services/mcp_server/       ✓ surfaced in /settings → API keys & SDK (install docs)
services/audit/compliance  ✓ surfaced in /admin → Compliance
services/audit/evaluation  ✓ surfaced in /advanced → Evaluation
services/autonomy/         ✓ surfaced in /policies → Autonomy, /incidents → violations
services/autoresponse/     ✓ surfaced in /advanced → Auto-Response
services/playbooks/        ✓ surfaced in /advanced → Playbooks
services/notifications/    ✓ surfaced in Topbar bell <NotificationCenter />
services/scheduled_reports/ ✓ surfaced in /settings → Reports
```

**Net: 27/27 services keep a home. 5 services that were orphaned today get NEW homes in v1.**

### 12.14 The UI work, broken into the existing phases

| Phase | UI work |
|---|---|
| **Phase 1** (Week 1) | Create `Signup.jsx`. Add "Sign up" link to `Login.jsx`. Update `App.jsx` to route `/signup` (public). |
| **Phase 2** (Week 2) | Create `OnboardingWizard.jsx`. Delete `LiveDemo.jsx` + its route + its sidebar nav item. Wire `/agents/wizard` endpoint to wizard. |
| **Phase 3** (Weeks 3-4) | Create `Dashboard.jsx`, `AgentSnapshot.jsx`, `ShadowModeReview.jsx`. **Rewrite `Sidebar.jsx`** into 3-tier structure. **Trim `App.jsx` routes 54→30**. Merge agent-detail pages. Surface IAG + remediation + behavior on dashboard/incidents. |
| **Phase 4** (Months 2-3) | No UI work — pilot calendar + outreach |
| **Phase 5** (Month 4) | Create `ThreatGraph.jsx`, `<MitreCoverageGrid />`, `<BlastRadiusCard />` on `/incidents` |
| **Phase 6** (Months 5-6) | Stripe in `Settings.jsx → Billing`, residency in `AdminConsole.jsx` |

### 12.15 Summary

| Today | After cleanup |
|---|---|
| 49 pages, 54 routes, 7 mixed primary nav items | 15 customer-facing pages, ~30 routes, 6 + 10 + 4 tiered nav |
| 5 backend services with no UI home (IAG, behavior, insight, remediation, compliance tool-ledger) | 0 orphan services — all 27 surface in v1 |
| 3 demo-only pages cluttering the customer UI | 0 demo pages in the app |
| 4 policy pages competing for the same flow | 1 `/policies` with tabs |
| 5 agent-detail pages competing | 1 `/agents/:id` with tabs |
| 9 settings pages buried in different places | 1 `/settings` with tabs |
| 0 backend services deleted | 0 backend services deleted (hard rule honored) |

---

## 13. THE STARTING GUN — 3 DECISIONS LEFT

Pick:
1. **Signup:** (a) email+password / (b) magic link / (c) Google OAuth only
2. **Wizard shape:** (a) opinionated 3-step / (b) optional
3. **Shadow duration:** (a) 14 d / (b) 7 d / (c) configurable

Tell me a/b/c × 3 and I start Phase 1 Sprint 1 the same day. End of Week 1 = `/signup` live on `https://ha.aegisagent.in`. End of Week 2 = first real CTO can self-onboard their Claude in 5 minutes. End of Week 4 = shadow-mode + inventory + incidents shipped + sidebar rewrite + 49→15 page cleanup committed. Month 3 = pilot outreach. Month 4 = 3 logos.

**No more plan files after this. This is v2 locked.**
