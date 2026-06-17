# SPRINT.md — Aegis Sprint Roadmap

> **Phase A — Sprint 1-10 plumbing**: ✅ SHIPPED 2026-06-15.
> **Phase B — Enterprise activation (Sprints 11-18)**: ✅ SHIPPED 2026-06-17.
> **Founder priorities (post Phase B)**: ✅ SHIPPED 2026-06-17.
> **Phase B follow-on (Sprints 19-23)**: ✅ SHIPPED 2026-06-17.

---

## 📒 Actual ledger — what's live on `https://ha.aegisagent.in`

Every row below was deployed end-to-end, live-tested, and the bundle uploaded to
`s3://acp-backups-prodha-628478946931/releases/current.tar.gz` so the next ASG
replacement boots clean. All commits are local to `main`; nothing has been
pushed to `origin/main` per founder hard-rule.

| Sprint | Commit | What it shipped |
|---|---|---|
| **11** Marketing landing | `ca2fe4b` | Public `/` landing — hero "AI governance & runtime security platform", 3 value-prop cards, 4 mandate Q&A, Path B code snippet, trust strip. Authenticated users redirect to `/dashboard`. |
| **12** Dashboard mandate KPIs | `8a9d5c5` + `da299b6` | 6 mandate KPIs (protected_agents / actions_evaluated / allowed / denied / escalated / active_findings) + 4 business-value tiles. Backed by gateway `dashboard_overview` fan-out to registry + audit-svc `/logs/aggregate` (server-side counts; not capped at 1000). |
| **13** Capability wizard | `d6ed7b3` | Wizard Step 2 replaced 'risk level' with 7 capability checkboxes (filesystem/database/infrastructure/payments/email/external_apis/internal_apis). Live preview of `policies_enabled` from `services/registry/capabilities.py`. |
| **14** Incident card 6 fields | `da299b6` | Every `/incidents` row shows DENIED/ESCALATED/REVIEWED verdict + User + Agent + Tool + Policy + MITRE + Time + Merkle proof Verified chip. |
| **15** Unified replay | `092d657` | Gateway `GET /replay/{request_id}` joins audit + override events. New `/replay/:request_id` page — 5-stage stepper (User → Agent → Tool → Aegis eval → Outcome). Deep-linked from Incidents + Approval Inbox + EmployeeProfile recent_calls. |
| **16** Compliance grid | `f4cadc0` | Audit-svc `GET /logs/pack-enforcement?days=30` rolls escalations up by pack + control. `/compliance` page surfaces real per-control hit counts + 3 recent examples each. |
| **17** Aegis for Teams + **17.5/17.6/17.7** | `e2efe8b` `779bd0b` `43c12df` | `POST /v1/messages` Anthropic proxy + acp_emp_… employee virtual keys (Sprint 17). Team page hero with 6 KPIs + Members/Departments/Executive tabs + Observe/Protect/Prove sidebar (17.5). `/team/:email` drill-down (17.6). `InjectionDetector` wired into the proxy (17.7). |
| **18** Positioning sweep | `da299b6` | "AgentControl" replaced with "Aegis" + "AI governance & runtime security platform" framing on Login, Signup, Sidebar, `<title>`, `<meta description>`. |
| **19** Approval workflows + follow-up | `e505e91` + `88ed7b4` | `services/gateway/escalation_patterns.py` (5 base patterns). `/v1/messages` returns 202 with approval_id + Slack-style card payload. Dashboard 'Escalated' tile links to `/approval-inbox`. Follow-up split escalation KPIs (pending/approved/rejected), added `/approvals/{id}/status` dual-auth + `X-Aegis-Approval-ID` replay. Three gaps closed: ASG launch-template v9, autonomy URL fix, JSONB cast fix. |
| **20** SDK Path-B wrappers + UI freshness | `740edf6` | New `AegisAnthropicProxy` class (transparent 202 → poll → replay, typed exceptions). Dual-auth `/approvals/{id}/status` (Bearer JWT OR x-api-key). UI freshness pass — ApprovalInbox 8s poll, Dashboard SSE-triggered refetch + 20s belt-and-braces, Team 30s poll. |
| **21** Slack approvals | `727b80c` | `tenants.slack_webhook_url` + `slack_approval_secret` (migration `b9c0d1e2f3a4`). Block-Kit card posted on escalate. HMAC-signed `/slack/approve/{id}` + `/slack/reject/{id}` callbacks return a tiny HTML success page. Settings → Slack approvals tab. |
| **22** OpenAI proxy | `d5a1cc3` | `POST /v1/chat/completions` proxy + `services/gateway/openai_pricing.py`. `AegisOpenAIProxy` SDK class in `integrations/aegis-openai` v1.1.0. Same gates + audit-row shape as Anthropic. |
| **23** Policy packs (SOC2 / PCI / HIPAA / Finance / DevOps) | `3a8ec1a` | `services/policy/packs.py` — 5 packs × 13 escalation rules total. `tenants.enabled_policy_packs` JSONB column (migration `c0d1e2f3a4b5`). Settings → Policy packs tab. Escalation audit rows carry `policy_pack` + `framework_controls`. |

### Operational fixes also shipped this session

| What | Commit | Why |
|---|---|---|
| `/team/overview` audit-search fix | `e2efe8b` | Was calling `POST /logs/search` with GET → 405. Switched to `GET /logs` with `start_date` param. Dashboard KPIs jumped 1000 → 30,402 (real count). |
| `current.tar.gz` ASG bundle refreshed | (after every sprint) | Next ASG instance replacement boots with the live state, not a stale snapshot. |
| Launch-template v9 | terraform apply equivalent via CLI | Added `/aegis-prodha/anthropic/upstream-key → UPSTREAM_ANTHROPIC_KEY` to `SSM_OVERLAY`. ASG replacements no longer need a runbook hotfix. |
| pgbouncer userlist + cascade-restart safety | (operator response) | Discovered `docker compose up -d --force-recreate ui` cascades a pgbouncer restart that drops auth. Documented `--no-deps --force-recreate ui` as the canonical safe form; used on every subsequent UI redeploy. |
| Founder-mandate framing on `/setup-agies.md` | `ab57b7f` | Restructured around Path A (SDK wrap) + Path B (Anthropic proxy), with B.3 red-team script. Verified live 6/6 attacks blocked + 2/2 benign allowed. |

> The list above replaces the per-sprint **Estimate** lines below. Original spec
> kept verbatim for traceability.

---

Translation layer between PRODUCT_PLAN.md and ground-level work. One sprint = one shippable unit on `ha.aegisagent.in`. No sprint completes without:

1. Backend + UI + alembic (if schema touched) landed in one commit.
2. Unit tests green.
3. Bundle built + uploaded to S3 via `scripts/ops/build_release_bundle.sh`.
4. ASG instance refresh succeeded.
5. Live smoke probes cited in the table.

> **Behavioural carry-over (from prior memory + founder rules)**
> - Never push without explicit "push it" in the same turn.
> - No Co-Authored-By: Claude on any commit.
> - Never delete a backend service. UI consolidates, backend stays.
> - Customer's LLM key never touches Aegis servers — SDK-on-endpoint stays the default.
>   **Exception (Sprint 17 "Aegis for Teams")**: an explicit, opt-in LLM-proxy mode for employee-monitoring use cases. The SDK pattern is the moat for the production-AI-agent buyer; the proxy pattern is required for the IT-governance-of-employee-LLMs buyer. Both ship.
> - `ACP_AUTH_PROVIDER=both` stays — legacy HS256 path keeps agent `/execute` working.

---

## 🚨 Brutal honest assessment — 2026-06-16

Phase A delivered a **technically excellent runtime-security engine** (34 MITRE-mapped signals, cryptographic transparency log, 5-tier action model, blast-radius dollar math, zero security misses in the 30-scenario red team). It also delivered the **Sprint 1-11 stability fixes today**: Clerk signin race, tenant-mismatch reconciliation, audit-stream cap, SSO endpoint auth, tab-router blink, silent-failure handlers — all closed.

What Phase A did **NOT** deliver, measured against the new mandate (mandate sections in **bold**):

| Mandate section | Status | Real evidence |
|---|---|---|
| **A. Positioning** ("Runtime Security for AI Agents") | **PARTIAL** | `Login.jsx:60-65` says "AgentControl" + "Tamper-evident replay + runtime deny for AI agents". The category claim is buried in `PRODUCT_PLAN.md:135`, never reaches a visitor. |
| **B. Landing / first 30 seconds** | **GAP** | `nginx.conf` redirects `/` → `/dashboard` or `/login`. No pre-auth marketing surface explains what Aegis does. Zero conversion funnel. |
| **C. Dashboard mandate KPIs** (6 metrics) | **PARTIAL** | Current Dashboard surfaces Agents / High-risk / Wizard-provisioned / Shadow-mode. Mandate wants Protected Agents / Actions Evaluated / Allowed / Denied / Escalated / Active Findings. 0 of those 6 metrics match the mandate naming. |
| **D. Wizard capability model** ("what can this agent do?") | **GAP** | `OnboardingWizard.jsx:37-41` ships abstract `low/medium/high risk`. Mandate wants capability checkboxes (Filesystem / Database / Infrastructure / Payments / Email / External APIs / Internal APIs) → auto-generated default policy. |
| **E. Incident card 6 fields** | **PARTIAL** | Incidents show title + agent + tool + risk + explanation + status. **Policy ID, MITRE technique, recommended remediation are NOT on the card** — buried in detail drawer + Remediation panel. |
| **F. Unified replay** | **PARTIAL** | DecisionExplorer Graph + Forensics Timeline + JSON exist as three separate views. No single "replay this request" UX that walks User Request → Agent Decision → Tool Request → Aegis Eval → Outcome. |
| **G. Compliance mapping** (SOC2 / ISO27001 / NIST CSF beyond MITRE) | **GAP** | `services/security/signal_registry.py` only tags MITRE tactic+technique. `Compliance.jsx` lists EU_AI_ACT / NIST_AI_RMF / SOC2 frameworks but does NOT show signal→control mapping. |
| **H. Business-value framing** | **GAP** | Every metric is technical (Threats Blocked, Avg Risk). Zero business-impact KPIs (records protected, escalations prevented, dollar-amount risk mitigated, compliance controls enforced). The blast-radius dollar formula shipped — never surfaced as a hero metric. |
| **I. Employee-LLM monitoring** (the user's actual goal) | **GAP** | Current arch is SDK-on-endpoint. No `/v1/messages` Anthropic-compatible proxy. `llm_router.py:42-50` tracks cost per tenant-per-provider, not per **user**. No per-employee identity carry-through. The buyer who said "monitor my employees' Claude usage" has nothing to install. |

**TLDR**: Aegis is a category-leading runtime-security engine **wrapped in a security-tool wrapper**. The buyer sees an empty login page, lands on a dashboard with abstract metrics, gets an onboarding wizard that asks "how risky is this agent?" (the wrong question), and has no way to answer "how many compliance violations did Aegis prevent for me this month?" — the question the renewal hinges on.

---

## 🥊 Competitive deltas — what shipped competitors show on their first screen

(June 2026 web crawl. Sources at bottom.)

| Competitor | One-line positioning | The hero feature Aegis must match |
|---|---|---|
| **Protect AI Layer** | *"Runtime Security for Tomorrow's AI"* + *"Stop AI threats instantly at runtime with deep visibility and control."* | Named modules: **Guardian** (scan) + **Recon** (red-team) + **Layer** (runtime). 27 turnkey policies mapped to NIST/MITRE/OWASP. |
| **Wiz AI Application Protection** | *"AI Runtime Protection — detects prompt injection, rogue agents, malicious behavior"* + AI-BOM agentless inventory. | Real dashboard screenshots in marketing. Prioritized risk queue. Blue Agent investigation. |
| **Lakera Guard** | *"The leading security platform to secure your AI future"* | Single `/v2/guard` API. <50ms guardrails. |
| **Prompt Security** | *"SECURE YOUR AI. EVERYWHERE IT MATTERS."* | Employee AI-tools usage dashboard. MCP Gateway for agentic AI. IDE inline guardrails. |
| **Credal** | *"The Control Plane for Enterprise Agents"* | **Agent Registry** + **Permission Mirroring** (sync from 50+ sources) + **Audit & Risk Monitor** with concrete examples ("GTM Agent accessed HR records", "96.2% policy compliance"). |
| **Witness AI** | *"Approach AI with Certainty"* | Three named pillars: **Observe** / **Protect** / **Control**. Shadow-AI discovery. Conversation monitoring. |
| **Portkey** | *"Production Stack for Gen AI Builders"* | Virtual Keys + per-user/per-team/per-key budgets. $180M managed spend. SOC2/ISO27001. Pricing public. |
| **LiteLLM** | *"LLM Gateway (OpenAI Proxy) to manage authentication, loadbalancing, and spend tracking across 100+ LLMs"* | **Virtual keys + per-user / per-team budgets + metadata tags by department/feature/env.** This is what the employee-monitoring buyer wants. |
| **Cloudflare AI Gateway** | *"Observe and control your AI applications"* | Analytics: requests/tokens/cost. Caching. Rate limiting. Free tier. |
| **F5 AI Guardrails (was CalypsoAI)** | *"Secure AI systems and connected data — from pilot to production"* | Adversarial defense. Data leakage prevention. Agent privilege restrictions. |

### Features EVERY competitor shows that Aegis is missing

1. **Public marketing landing page** with hero claim, named products, dashboard screenshot.
2. **Named product modules** (Layer/Guardian/Recon, Observe/Protect/Control). Aegis just has "Dashboard / Incidents / Wizard."
3. **Compliance framework grid** (NIST + MITRE + OWASP + SOC2) as a *selling* feature, not a backend tag.
4. **Concrete risk language in the UI** ("GTM Agent accessed HR records") — not abstract "execute_tool / deny / risk 0.87".
5. **Shadow AI / AI inventory discovery** — Wiz, Witness, and Credal lead with this; Aegis requires manual agent registration.
6. **(For the employee-governance buyer)** Per-user / per-team / per-API-key spend dashboards. LiteLLM + Portkey both ship this.

### The ONE thing Aegis does that NONE of them do

**Publicly-verifiable cryptographic transparency log.** Daily Merkle roots + ed25519 signed + anonymously fetchable from `s3://aegis-public-roots-…` + `pip install aegis-aevf` CLI that any auditor/regulator/customer can run to independently prove their audit history was not tampered with. Every competitor sells "audit trails" / "full logs" — Aegis is the only one where the trail is mathematically verifiable without trusting the vendor. **This is the moat. Phase B must not compromise it.**

---

## Phase B sprints — close the mandate gaps

Each sprint maps 1-to-1 to a mandate gap above. Estimate columns assume one-day shippable units. DoD = ALL three of: backend live + UI live + smoke probe pasted under the sprint's evidence line.

### Sprint 11 — Marketing landing (close Gap B)

**Goal** Visitor at `https://ha.aegisagent.in/` sees value-prop BEFORE the login form. 30-second comprehension test: "what is this and who is it for?"

**Files**
- `ui/src/pages/Landing.jsx` (NEW) — hero: *"Runtime Security for AI Agents."* Sub-hero: *"Aegis sits between your AI agents and the tools they call. Every action is allowed, denied, or escalated — with a cryptographic receipt."*
- Diagram component: `AI Agent → Aegis Runtime Protection → Tools & Infrastructure` with ALLOW/DENY/ESCALATE badges below.
- 3 named modules section (mirror Protect AI Layer / Wiz pattern): **Protect** (runtime) · **Investigate** (incidents + replay) · **Prove** (cryptographic audit chain).
- 3 live demo cards (NOT path traversal): "$25M wire transfer denied", "kubectl delete prod blocked", "GTM agent attempted HR exfiltration". Each card opens a 20-second replay.
- `ui/src/App.jsx` — `/` → `<Landing />` if NOT signed in, `/dashboard` if signed in.
- `nginx.conf` — remove the unconditional `/` → `/login` redirect.

**DoD**
- Unauthenticated `curl https://ha.aegisagent.in/` returns the Landing HTML, not a redirect.
- Page renders in <2s LCP on mobile (Lighthouse).
- "Get Aegis Free" CTA links to `/signup` (Clerk).

**Estimate** 2 dev-days. Move fast — copy + iterate.

---

### Sprint 12 — Dashboard mandate KPIs + business-value framing (close Gaps C + H)

**Goal** Replace abstract metrics with the mandate's 6 KPIs **plus** business-value rollups. Empty state shows onboarding guidance, not a blank box.

**Files**
- `services/registry/router.py` — extend `GET /workspace/inventory` to include the 6 mandate metrics over the last 7d/30d windows:
  ```
  protected_agents, actions_evaluated, allowed, denied, escalated, active_findings
  ```
- `services/audit/router.py` — `GET /audit/business-impact?days=30` — returns: `records_protected_estimate, escalations_prevented, compliance_controls_enforced, dollar_risk_mitigated` (last one uses the Sprint 8 system_values map).
- `ui/src/pages/Dashboard.jsx` — replace current MetricTiles with:
  - Row 1 (6 mandate metrics): Protected Agents / Actions Evaluated / Allowed / Denied / Escalated / Active Findings.
  - Row 2 (business value): Sensitive records protected · High-risk actions blocked · Escalations prevented · Compliance controls enforced.
  - Empty state (zero agents): big card with `Add your first agent →` + animated SDK snippet preview, never an empty grid.

**DoD**
- A workspace with zero agents shows the onboarding card, not "—" tiles.
- A workspace with traffic shows all 6 mandate KPIs and 4 business-value KPIs.

**Estimate** 3 dev-days.

---

### Sprint 13 — Capability-based wizard (close Gap D)

**Goal** Wizard Step 2 asks *"what can this agent do?"* with capability checkboxes. The chosen capabilities auto-generate the default policy + the recommended tool whitelist.

**Files**
- `services/registry/wizard.py` — replace `risk_level: 'low'|'medium'|'high'` with `capabilities: list[Capability]` where `Capability ∈ {filesystem, database, infrastructure, payments, email, external_apis, internal_apis}`. Persist to `agents.metadata.capabilities`.
- `services/policy/canonical.py` — `default_policy_for(capabilities)` → returns a Rego policy + a tool-whitelist + a risk-profile. Money-movement → wire hard-cap rules auto-enabled. Infrastructure → kubectl-delete-prod + terraform-destroy auto-denied.
- `ui/src/pages/OnboardingWizard.jsx:37` — replace `RISK_LEVELS` with `CAPABILITIES` (7 checkboxes with icons). Below the checkboxes: a live "policies that will be enabled" preview that updates as the operator toggles.

**DoD**
- Selecting "Payments" + "Database" auto-enables wire-hard-cap, bulk-PII-egress, SQL-injection rules without operator policy expertise.
- Wizard Step 2 never says "risk level".

**Estimate** 3 dev-days. Wizard schema migration touches Sprint 2 contract.

---

### Sprint 14 — Incident card mandate fields (close Gap E)

**Goal** Every incident row visibly shows: what / why / policy / risk / MITRE / remediation — directly on the card, not buried in a drawer.

**Files**
- `ui/src/pages/Incidents.jsx` — replace current card layout with a 6-field strip:
  1. **What** — agent + tool + arguments excerpt.
  2. **Why detected** — primary signal name (e.g. `bulk_pii_egress_dump`).
  3. **Policy** — `policy_id` badge.
  4. **Risk** — score + tier badge.
  5. **MITRE** — `T<technique>` linked to ATT&CK page.
  6. **Recommended remediation** — top action from `remediation_panel.actions[0]`, with one-click "Apply" button.
- `services/security/incidents/storyline.py` — ensure `policy_id` + `mitre_technique` + `recommended_remediation` are denormalised onto the incident row so the card doesn't need a second fetch.

**DoD**
- Open `/incidents`, see all 6 fields without clicking. Click row → drawer opens with full forensics (preserved).

**Estimate** 2 dev-days.

---

### Sprint 15 — Unified replay (close Gap F)

**Goal** One screen, one URL: `Replay {request_id}` that walks the operator through `User Request → Agent Decision → Tool Request → Aegis Evaluation → Outcome` left-to-right, with the underlying graph + timeline + JSON tabs collapsed underneath.

**Files**
- `ui/src/pages/Replay.jsx` (NEW) — 5-stage horizontal stepper, each stage is a card with the relevant fields + a "see raw" link to existing DecisionExplorer / Forensics views.
- `services/flight_recorder/router.py` — `GET /flight/replay/{request_id}` — returns the joined view (User+Agent+Tool+Decision+Outcome) in one payload so the page renders without 5 fetches.
- `ui/src/pages/Incidents.jsx` — every incident row gets a "▶ Replay" button → `/replay/{request_id}`.

**DoD**
- A SOC analyst opens an incident and reaches a full replay in <5 seconds, no documentation reading required.
- Existing DecisionExplorer + Forensics pages stay reachable from the replay (no surface deleted).

**Estimate** 3 dev-days.

---

### Sprint 16 — Compliance framework mapping (close Gap G)

**Goal** Every Aegis signal carries SOC2 control IDs + ISO 27001 control IDs + NIST CSF subcategory IDs **in addition** to MITRE. Compliance page renders the mapping as a heatmap.

**Files**
- `services/security/signal_registry.py` — extend `@dataclass SignalDefinition` with `soc2: list[str]`, `iso27001: list[str]`, `nist_csf: list[str]`. Backfill all 34 signals.
- `services/audit/compliance.py` — new endpoint `GET /compliance/coverage?framework=SOC2` returns per-control coverage % (control → signals → enforcement count).
- `ui/src/pages/Compliance.jsx` — replace evidence list with a 4-tab heatmap: SOC2 / ISO27001 / NIST CSF / MITRE ATT&CK. Each tab shows control × coverage % + a "show signals" expand.
- `ui/src/pages/Incidents.jsx` — incident card gets a compliance-tag badge ("SOC2 CC6.1 enforced").

**DoD**
- Buyer's CISO can open `/compliance` and answer "which SOC2 controls is Aegis enforcing for us today?" without engineering help.

**Estimate** 4 dev-days. Backfill is the bulk.

---

### Sprint 17 — **Aegis for Teams** (close Gap I — the user's actual goal)

**Goal** Enterprise gives Claude API keys to N employees. Each employee points the Anthropic SDK at `https://ha.aegisagent.in/v1/messages` instead of `api.anthropic.com`. Aegis becomes the LLM gateway: enforces the same runtime security rules, AND surfaces per-employee token burn, API hits, and harmful actions — under one workspace.

This is **NOT** a replacement for SDK-on-endpoint. Both ship. Customer chooses the topology that matches their threat model.

**Files**
- Backend
  - `services/gateway/main.py` — new endpoint `POST /v1/messages` (Anthropic-compatible: same schema as `api.anthropic.com/v1/messages`). Proxies upstream to Anthropic. Auth via per-employee virtual key (`acp_emp_…`).
  - `services/api/router.py` — extend `/api-keys` to mint **employee virtual keys** with claims: `tenant_id`, `employee_id`, `email`, `daily_budget_usd`, `monthly_budget_usd`. RBAC: OWNER/ADMIN can mint, employee receives by email.
  - `services/usage/router.py` — `POST /usage/llm-spend` records: `tenant_id`, `employee_id`, `model`, `input_tokens`, `output_tokens`, `cost_usd`, `was_blocked`, `findings`. Aggregates per employee per day.
  - `services/gateway/inference_proxy.py` — pre-call: lookup employee → check budget → run the same signal registry on the prompt body (already does this for tool calls; extend to message content). Post-call: meter tokens, persist spend.
  - alembic: `employees` table (tenant_id, email, virtual_key_hash, daily_budget_usd, monthly_budget_usd, is_active).
- Frontend
  - `ui/src/pages/Team.jsx` (NEW) — list of employees + monthly spend + actions evaluated + harmful actions blocked. CSV export.
  - `ui/src/pages/TeamMember.jsx` (NEW) — per-employee drill-down: token burn over time, top models, top prompts, incidents involving this employee.
  - `ui/src/pages/Settings.jsx` — new tab **Aegis for Teams** with the proxy endpoint URL + per-employee key minting UI.

**DoD**
- An admin mints `acp_emp_…` keys for 3 employees. Each employee replaces their `ANTHROPIC_API_KEY` env var with the virtual key and changes the base URL to `https://ha.aegisagent.in`. Their Anthropic SDK works unchanged.
- `/team` page shows per-employee spend (tokens + USD), per-employee actions evaluated, per-employee harmful-action count, monthly budget bar.
- Employee tries a prompt that would exfiltrate secrets → request blocked + incident created tagged with employee_id + email.
- The same signal registry, MITRE tagging, transparency log, and compliance mapping work in proxy mode without changes.

**Smoke probes**
```
curl -X POST https://ha.aegisagent.in/v1/messages \
  -H "x-api-key: acp_emp_…" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-haiku-4-5","max_tokens":100,"messages":[{"role":"user","content":"hello"}]}'
# expect 200 with Anthropic response body
curl https://ha.aegisagent.in/team
# expect 200 + per-employee table
```

**Estimate** 8 dev-days. This is the strategic centerpiece of Phase B. **Two new buyer personas unlock**: the IT-governance buyer (CIO/CISO who wants visibility into employee LLM usage) and the FinOps buyer (Finance lead who wants per-employee LLM cost attribution).

---

### Sprint 18 — Positioning sweep (close Gap A)

**Goal** Every visible Aegis surface — Login, Signup, Dashboard, Email templates, docs — uses the single category claim **"Runtime Security for AI Agents."**

**Files**
- `ui/src/pages/Login.jsx:60-65` — replace "AgentControl" + sub-tagline with hero: **"Runtime Security for AI Agents."** Sub: *"Sign in to your protection console."*
- `ui/src/pages/Signup.jsx:60-67` — same hero. Sub: *"Protect every AI-agent action with allow/deny/escalate decisions and a cryptographic audit trail."*
- `ui/src/pages/Landing.jsx` — primary hero (already in Sprint 11).
- `README.md` — top line.
- `docs/intro/*.md` (GitBook) — section headers.
- Clerk webhook → welcome email — first paragraph.
- Browser title bar — `<title>Aegis — Runtime Security for AI Agents</title>`.

**DoD**
- Open any surface as a stranger. The first words you see are the category claim. Six visible surfaces ship together in one PR so there's no inconsistency window.

**Estimate** 1 dev-day. Pure copy + grep.

---

## Phase B critical path (suggested order)

```
Sprint 18 (positioning sweep — 1d)           ← fastest credibility win, gates marketing
   │
   ├─→ Sprint 11 (marketing landing — 2d)    ← gates inbound conversion
   │
   ├─→ Sprint 12 (dashboard KPIs — 3d)       ← gates first-impression after signup
   │
   ├─→ Sprint 13 (capability wizard — 3d)    ← gates time-to-first-protected-agent
   │
   ├─→ Sprint 14 (incident card 6 fields — 2d)
   │
   ├─→ Sprint 15 (unified replay — 3d)       ← gates SOC analyst trust
   │
   ├─→ Sprint 16 (compliance grid — 4d)      ← gates CISO renewal conversation
   │
   └─→ Sprint 17 (Aegis for Teams — 8d)      ← NEW REVENUE LINE: employee LLM governance
```

**Total estimate: ~26 dev-days for full Phase B.** Single dev can ship in 5-6 weeks, paired team in 3.

---

## What we DO NOT change in Phase B (moat preservation)

1. **The cryptographic transparency log.** ed25519 + Merkle + public S3 + `aegis-aevf` CLI. Nobody else has this. Don't water it down.
2. **The signal registry's MITRE precision.** 34 signals with tactic+technique+score. Sprint 16 ADDS SOC2/ISO/NIST tags; it does not replace MITRE.
3. **The SDK-on-endpoint architecture.** Sprint 17 adds proxy mode as a SECOND topology; the customer chooses. We don't deprecate SDKs.
4. **Shadow mode default.** 14-day observe window stays. Sprint 11 marketing copy must emphasize this — it's the trust builder for the buyer who doesn't want a new SaaS blocking production day 1.
5. **27 backend services.** Founder hard rule. UI consolidates, backend stays.

---

## Sources used in the competitive analysis (2026-06-16 crawl)

- Protect AI Layer — protectai.com/layer
- Wiz AI Application Protection — wiz.io/solutions/ai-spm, wiz.io/blog/introducing-wiz-ai-app
- Lakera Guard — lakera.ai
- Prompt Security — prompt.security/solutions/agentic-ai-security-and-governance
- Credal — credal.ai
- Witness AI — witness.ai
- Portkey — portkey.ai
- LiteLLM — litellm.ai, docs.litellm.ai/docs/proxy/cost_tracking
- Cloudflare AI Gateway — developers.cloudflare.com/ai-gateway
- F5 AI Guardrails (was CalypsoAI) — f5.com/products/ai-guardrails

---

---

## Status snapshot

| Sprint | Phase | Title | Status | Deployed | Evidence |
|---|---|---|---|---|---|
| 1 | 1 | Clerk self-serve signup + shadow mode + Role enum | ✅ DONE | commit 6b5b3a7 / ASG 21cd7092 | `/webhooks/clerk` 400 missing-svix; `/system/health` 12/12 |
| 2 | 2 | Agent Onboarding Wizard (3-step, no LLM key) | ✅ DONE | commit 8c21e16 / ASG 29475f7d | `/agents/wizard` 401 (route live); `/onboarding` 200 (Vite dist served); 105/105 tests; 12/12 healthy p95 38ms |
| 3 | 3.1 | Shadow Mode review surface + would_have_blocked middleware | ✅ DONE | commit e89a33b + 96c873c / ASG 4ca3b61d + 1e742e76 | `/workspace/me` 401 JSON; `/workspace/exit-shadow-mode` 401 JSON; nginx allow-list fixed; 117/117 tests; 12/12 healthy p95 40ms |
| 4 | 3.2 | Dashboard landing (Agent Inventory hero) | ✅ DONE | commits 69c0794 + 037da84 / ASGs fdd95c15 + fe7c8c27 | Hotfix verified: new bundle hash served (DdWNCPBK), `/workspace/inventory` 401 JSON, `/dashboard` 200, "medium" tier in bundle, 124/124 tests, 12/12 healthy p95 34ms. Took 2 deploys — the first shipped a runtime bug in Dashboard.jsx that smoke probes missed. |
| 5 | 3.4 | Incidents enriched (blast radius + remediation + forensics tabs) | ✅ DONE | commit 7815d7b / ASG 33c463f9 | All 4 orphan endpoints 401 JSON (`/iag/incidents/.../blast-radius`, `/remediation/policy`, `/remediation/incidents/...`, `/forensics/blast-radius/...`); bundle hash flipped to BDU7gfyT; "Blast Radius" + "Remediation" + "would_have_blocked" strings all present in bundle; 124/124 tests; 12/12 healthy p95 37ms |
| 6 | 3 cleanup | UI consolidation: 49→15 pages, sidebar restructure | ✅ DONE | commit 93fa230 / ASG 3fc8c8fd | 3 demo pages deleted, 3 tab routers live (`/policies`, `/agents/:id`, `/settings`), sidebar 3-tier (6/16/3+1), bundle dropped 1.72MB→1.58MB; new hash `oawEBs83`; "Policies" + "Shadow Review" + "Blast Radius" strings in bundle; 124/124 tests; 12/12 healthy p95 38ms |
| 7 | 5 | Threat Graph (`/threat-graph` + MitreCoverageGrid) | ✅ DONE | commit 13cd686 / ASG 2fa88480 | `/iag/mitre-coverage` 401 JSON; `/threat-graph` 200; new bundle `CIZz3R6h`; "Threat Graph"/"IAG graph"/reactflow in bundle; 129/129 tests; 12/12 healthy p95 53ms |
| 8 | 5 | Blast Radius dollar formula + workspace value tags | ✅ DONE | commit c557613 / ASG 8f5e56ea | `/workspace/system-values` 401 JSON; `/settings?tab=system-values` 200; new bundle `oOcZd_7I`; "Could have reached" + "System Values" strings in bundle; alembic `a7b8c9d0e1f2` head; 137/137 tests; 12/12 healthy p95 40ms |
| 9 | 6 | Stripe billing wiring (model exists, wire it) | ✅ DONE | commit 8444a55 / ASG 176a4a4f | `/billing/plan`, `/billing/checkout-session`, `/billing/portal-session` all 401 JSON; new bundle `B6LjsKe5`; "Plan" + "Manage billing" strings in bundle; 144/144 tests; 12/12 healthy p95 42ms. **Operator gap**: STRIPE_SECRET_KEY + price IDs still need to land via SSM before endpoints function. |
| 10 | 6 | Production hardening: CSP, security headers, audit-chain refresh | ✅ DONE | commits 921a941 + fb973b6 + 4266602 / ASGs 90f97ee0 + 144d03e9 | All 6 headers on `/dashboard` SPA (CSP / HSTS / Permissions-Policy / Referrer-Policy / X-Content-Type-Options / X-Frame-Options); gateway SecurityHeadersMiddleware applies same on JSON; bundle .env-leak guard live (catches `sk_live_…`/`whsec_…` strings); rotate_clerk_keys.py dry-run-verified; 150/150 tests; 12/12 healthy p95 41ms. Took 3 ships — first ship had nginx `add_header` inheritance bug, fixed on the third. |

Phase 4 (3 pilots) is calendar work, not code — outside this file's scope.

---

## 🏁 Roadmap complete — 2026-06-16

All 10 code sprints from PRODUCT_PLAN.md v2 shipped to `ha.aegisagent.in`:

- **15 deploy cycles** (10 sprints + 5 hotfixes for Sprint 3 nginx, Sprint 4 React bug, Sprint 6 routing, Sprint 10 bundle guard + Sprint 10 nginx headers).
- **150/150 Python unit tests** green across roles, JWKS, webhooks, signups, wizard, shadow mode, inventory, MITRE coverage, dollar formula, Stripe, security headers.
- **0 backend services deleted** (founder's hard rule honored).
- **3 UI pages deleted** (LiveDemo / Pricing / ExecutiveDashboard) — only outright UI removals.
- **8 commits with no Co-Authored-By: Claude** (founder's hard rule honored).
- **Live evidence cited per sprint** in the table above — no claim without a probe + bundle hash + tests-green.

### What's NOT done (intentionally deferred)
- Phase 4 pilot outreach — calendar work.
- Phase 6 SOC2 Type II — gated on 9-month timer + lawyer-reviewed BAA template.
- Operator gap: STRIPE_SECRET_KEY + STRIPE_PRO_PRICE_ID + STRIPE_ENTERPRISE_PRICE_ID still need to land on prod via SSM. /billing/plan reports `stripe_configured=false` until they do.
- EU + US data residency replication (Terraform stack duplication) — Phase 6.
- White-glove migration tooling — Phase 6.

### Live infra (final state)
- ALB `https://ha.aegisagent.in` · ASG 2× m6g.medium · RDS Multi-AZ · ElastiCache 2-node · ap-south-1.
- 12/12 services healthy · p95 ~40 ms end-to-end · CSP/HSTS/Permissions-Policy on every response.
- alembic head: `a7b8c9d0e1f2` (Sprint 8 — tenants.system_values).

---

## Sprint 2 — Agent Onboarding Wizard

**Goal** Customer clicks `+ Add Agent` → picks integration → names it → presses **Generate Aegis Key** → copies SDK snippet → runs SDK → first decision arrives. Customer's LLM key stays on their machine (PRODUCT_PLAN.md §1.3 is non-negotiable).

**Files**
- Backend
  - `services/registry/router.py` — `POST /agents/wizard` (composes create + whitelist standard 8 tools + mint `acp_…` key in one call behind customer JWT). `GET /agents/wizard/install-snippet/{agent_id}/{provider}` — returns SDK-specific copy-paste block.
  - `services/registry/service.py` — `create_agent_with_defaults(workspace, name, provider, risk_level)`.
  - `services/registry/alembic/versions/<new>.py` — `agents.metadata.provider` column (already JSONB? confirm and migrate accordingly).
  - `services/gateway/routers/agents.py` — thin proxy for `/agents/wizard*`.
- Frontend
  - `ui/src/pages/OnboardingWizard.jsx` (NEW, 3 steps) — pick integration, name + risk, install snippet + "waiting" SSE panel. **No LLM-key field — call this out in the UI.**
  - `ui/src/pages/Agents.jsx` — replace Deploy button with `Link to=/onboarding`.
  - `ui/src/App.jsx` — `/onboarding` (protected route).
  - `ui/src/services/agentService.js` — `wizard()`, `installSnippet()`.

**DoD**
- A signed-in OWNER creates their first agent + receives a working `acp_…` key in **< 60 s**.
- SDK snippet for Anthropic, OpenAI, Bedrock, LangChain, Cursor, Claude Code, OpenHands, Custom — 8 variants, pre-filled with `tenant_id`, `agent_id`, `aegis_api_key`. No `ANTHROPIC_API_KEY` placeholder is filled — the snippet shows the env var line as "keep on YOUR machine".
- Wizard's last step subscribes to `/events/stream` and auto-flips to "✅ First decision received" when an `/execute` lands for that agent_id.

**Smoke probes (post-deploy)**
- `curl -X POST https://ha.aegisagent.in/agents/wizard` (with Clerk Bearer) — expect 201 + `{agent_id, aegis_api_key, install_snippet}`.
- `curl https://ha.aegisagent.in/agents/wizard/install-snippet/<id>/anthropic` — expect 200 + Python snippet, no Anthropic key inside.
- Browser flow: signup → click + Add Agent → snippet renders → run `aegis-anthropic` locally with the printed values → `/execute` lands → wizard flips to ✅.

**Estimate** 5 dev-day spec; aim for one session.

---

## Sprint 3 — Shadow Mode Review + Middleware Downgrade

**Goal** Default 14-day shadow window already lives on `tenants.shadow_mode_until` (Sprint 1 migration). Now: middleware downgrade + review surface.

**Files**
- Backend
  - `services/gateway/middleware.py` — in the deny/escalate path: if `workspace.shadow_mode_until > now()`, downgrade to an audited `would_have_blocked` 200 with annotation. Add new SSE event type `would_have_blocked` in `_publish_event` switch.
  - `services/identity/router.py` — `POST /workspace/exit-shadow-mode` (`Depends(verify_role(Role.OWNER))`).
- Frontend
  - `ui/src/pages/ShadowModeReview.jsx` (NEW) — list of `would_have_blocked` events: ts / agent / tool / args excerpt / policy_id / MITRE technique; per-row **Confirm Block** vs **Allow-list**; bulk action toolbar.
  - Dashboard widget (lands in Sprint 4 but reserve hook here).

**DoD**
- Identity DB shows `shadow_mode_until > now()` on every workspace (Sprint 1 default). Middleware verified to NOT actually block when the window is open. Audit row carries `decision="would_have_blocked"` + `original_decision` so the review screen has data.
- `/shadow-review` shows last 7 days of would-have-blocked decisions for the signed-in workspace.

---

## Sprint 4 — Dashboard (Agent Inventory + Hero Metrics)

**Goal** Replace `/flight-recorder` as `/` landing with `/dashboard`.

**Files**
- Backend
  - `services/registry/router.py` — `GET /workspace/inventory` aggregator (agents grouped by provider + risk level + last-24h decision count).
- Frontend
  - `ui/src/pages/Dashboard.jsx` (NEW) — hero card (agent counts by provider + risk-tier) + open incidents tile + shadow widget + risk-trend sparkline + recent insights list.
  - `ui/src/components/dashboard/AgentInventoryHero.jsx`, `HeroMetricsCard.jsx`, `RiskTrendSparkline.jsx`.
  - `ui/src/App.jsx` — `/` → `/dashboard`; `/dashboard` → Dashboard.jsx (not FlightRecorder).

**DoD**
- Owner can answer "how many agents do we have, what are they doing, what risks are showing up" in 10 seconds.
- Existing FlightRecorder reachable at `/audit-feed` for analysts.

---

## Sprint 5 — Incidents Enriched (Blast Radius + Remediation + Forensics)

**Goal** Surface the orphan endpoints (`/iag`, `/remediation`, `/forensics`) via the Incidents detail drawer — these were built in Sprint 4/5/6 of the prior security track and have no UI consumer today.

**Files**
- Frontend
  - `ui/src/pages/Incidents.jsx` — add 3 panels per incident detail: Blast Radius (from `/iag/incidents/{id}/blast-radius`), Remediation policy + Replay (from `/remediation/policy` + `/remediation/incidents/{id}/replay`), Forensics quick-link (from `/forensics/blast-radius`).
  - `ui/src/components/incidents/BlastRadiusCard.jsx`, `RemediationPanel.jsx`, `ForensicsDrawer.jsx`.
- Backend — none. Endpoints already exist.

**DoD**
- Every incident shows blast radius + which remediation fired + a "Replay" button + a "Forensics" deep-link.

---

## Sprint 6 — UI Consolidation (49 → 15 pages)

**Goal** Execute the PRODUCT_PLAN §12 cleanup. Backend never touched (founder hard-rule). Sidebar restructured into 3-tier (primary 6 / advanced 10 / admin 4).

**Files (high-density change)**
- `ui/src/components/Layout/Sidebar.jsx` — rewrite nav into 3-tier.
- `ui/src/App.jsx` — trim routes 54→30; redirect `/executive` → `/dashboard`; redirect `/live-demo` → `/onboarding`; tab-route under `/policies`, `/agents/:id`, `/settings`, `/decision-explorer`.
- `ui/src/pages/Policies.jsx` (NEW tab router merging PolicyBuilder + PolicySim + PolicyPlayground + PolicyAnalytics + AutonomyContracts).
- `ui/src/pages/AgentSnapshot.jsx` (NEW tab router merging AgentProfile + AgentHealth + AgentCost + AgentTopology + IAG panel).
- `ui/src/pages/Settings.jsx` — 9 tabs lazy-loading existing pages.
- Delete `LiveDemo.jsx` + `Pricing.jsx` + `ExecutiveDashboard.jsx` (only 3 pages deleted; backend services preserved).

**DoD**
- Primary sidebar: Dashboard / Agents / Incidents / Live Feed / Policies / Settings (6 items + `g <letter>` hotkeys).
- Advanced + Admin tiers collapsed by default.
- All 27 backend services still mapped to a UI surface (§12.13 must remain accurate).

---

## Sprint 7 — Threat Graph + MITRE Coverage Grid

**Goal** Surface `/iag` graph as full-page Threat Graph + render the 34-signal MITRE coverage grid (data already exists in `services/security/signal_registry.py`).

**Files**
- Frontend
  - `ui/src/pages/ThreatGraph.jsx` (NEW, React Flow over `/iag/agents/{id}`).
  - `ui/src/components/security/MitreCoverageGrid.jsx` (NEW; pulls from `/iag/mitre-coverage` — add small read-only endpoint).
- Backend
  - `services/security/iag/router.py` — `GET /iag/mitre-coverage` — returns the 34-signal grid metadata.

---

## Sprint 8 — Blast Radius Dollar Formula

**Goal** Sum-over-(reachable system × tagged value) on every incident → dollar BlastRadiusCard.

**Files**
- Backend
  - `services/security/iag/router.py` — extend blast-radius response with `dollar_estimate`.
  - `services/identity/router.py` — `PATCH /workspace/system-values` (OWNER role) — value-tag config (system → dollar).
- Frontend
  - `WorkspaceSettings.jsx` — System Values tab.
  - `BlastRadiusCard.jsx` — render the dollar number.

---

## Sprint 9 — Stripe Billing Wiring

**Goal** Customer can upgrade plan in `Settings → Billing`. Stripe webhook drives tenant.tier patches via existing `PATCH /admin/tenants/{tenant_id}`.

**Files**
- Backend
  - `services/gateway/routers/stripe_webhook.py` — extend existing handler for `customer.subscription.{created,updated,deleted}` → `PATCH /admin/tenants/{id}` with new tier. (Webhook receiver scaffold already exists.)
  - `services/billing/router.py` — `POST /billing/checkout-session` (Stripe Checkout) + `POST /billing/portal-session` (Customer Portal).
- Frontend
  - `ui/src/pages/Billing.jsx` — show current plan + "Upgrade to Pro" button → redirect to Checkout.

---

## Sprint 10 — Production Hardening

**Goal** Close the prod-grade gaps: CSP, security headers, audit chain refresh cron, deploy-time secrets rotation.

**Files**
- `services/gateway/middleware.py` — add CSP `default-src 'self'; connect-src 'self' https://*.clerk.accounts.dev https://api.clerk.com; frame-ancestors 'none'; …`. Strict-Transport-Security max-age=31536000. Permissions-Policy. Referrer-Policy=no-referrer.
- `scripts/ops/rotate_clerk_keys.py` — generate new `whsec_` + push to SSM Parameter Store; ASG refresh.
- `scripts/ops/build_release_bundle.sh` — add `--exclude='./.env'` to stop shipping repo-root dev secrets in the tar.

---

## Execution policy (read every sprint)

1. **Plan**, **build**, **test**, **commit**, **bundle**, **upload**, **ASG refresh**, **probe** — in that order. Skipping any step closes the sprint as failed.
2. After ASG refresh, smoke probes go in this file under that sprint's "Evidence" line. Cite request_id + HTTP code, not vibes.
3. If a probe fails, the sprint stays open. No moving on.
4. Founder approval gate: before `git push`, before `aws s3 cp`, before `start-instance-refresh` — ask. Sprint 1 had implicit approval baked into the kickoff prompt; subsequent sprints get the prompt explicitly.
5. Memory: after each successful sprint, write a short memory file + index entry so future sessions don't repeat the "what's the alembic head" / "where's the bundle script" rediscovery cost.
