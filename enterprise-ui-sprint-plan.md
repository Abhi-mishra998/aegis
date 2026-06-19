# Aegis — Enterprise UI Sprint Plan (Brutal & Honest)

**Goal:** Take Aegis from *"functional UI + lots of curl"* to *"a Fortune-500 CTO can run the platform for a year without ever opening a terminal."*
**Stance:** Solo-founder, 8-12 weeks calendar. Every sprint has a **BUILD** or **SKIP** verdict with the dollar reason. No vanity work.

---

## 0. Brutal honest framing before the plan

I audited the UI code page-by-page in the previous turn. **You already have ~85% of the platform implemented in the UI:**

- ✅ OnboardingWizard (4 steps, 566 lines)
- ✅ PolicyBuilder (form-based no-code → emits Rego, 758 lines)
- ✅ Team page with per-department rollup (738 lines)
- ✅ ApprovalInbox + KillSwitch + ThreatGraph + IdentityGraph + LiveFeed + Compliance
- ✅ 5 policy packs (SOC2, PCI, HIPAA, FINANCE, DEVOPS) toggleable from Settings
- ✅ Per-employee virtual key minting from Team modal

**The real 3 gaps the CTO complained about:**
1. Slack/SIEM/Okta = paste-credentials, not OAuth "Connect" buttons
2. No industry-preset onboarding ("I'm a Fintech" → auto-enable SOC2+FINANCE+wire-transfer policy)
3. No demo-workspace seeding ("show me what this looks like with data")

**This is 3-4 sprints of work, not 12.** But you ALSO need to:
- Sign 2 design partners (Sprint 0 of the 30-day plan — the *real* P0)
- Sign Drata/Vanta for SOC 2
- Close the inst-1 deploy flap from yesterday

**If you spend the next 8 weeks polishing UI without signing a customer, the polish is wasted.** The honest answer: run THIS UI plan AND the `30-day-product-plan.md` IN PARALLEL — engineering hours for UI, calendar time for vendor + sales calls.

---

## 1. Sprint priority matrix

| Sprint | Weeks | UI deliverable | Build / Skip | Why |
|---|---|---|---|---|
| **S0** | parallel | Design-partner outreach (10 emails) + Drata signup | **CRITICAL — do FIRST** | $0 in revenue = no Sprint matters |
| **S1** | 1 | Onboarding industry presets (Fintech/Healthcare/DevOps/Startup) | **BUILD** | Closes "select governance" CTO objection. 3 days. |
| **S2** | 1 | Connect-button Slack OAuth | **BUILD** | Most-cited UX gap. 5 days. |
| **S3** | 1 | Per-vendor SIEM wizard + connection-test | **BUILD** | Splunk/Datadog landings convert from this. 4 days. |
| **S4** | 1 | Demo-workspace one-click seeding (7 agents + 200 decisions + 3 incidents) | **BUILD** | Demo conversion 3-5×. 5 days. |
| **S5** | 1 | Hierarchical Teams object (replace free-text department) | **BUILD** | Per-team budget caps + manager dashboards. 5 days. |
| **S6** | 1 | One-click SOC 2 evidence bundle export (mapped to all CC controls) | **BUILD** | This is the "moat" workflow. 4 days. |
| **S7** | 1 | Marketplace landing tiles on Dashboard ("Connect Slack/Datadog/Okta/PagerDuty") | **BUILD** | Discoverability — currently buried in Settings. 3 days. |
| **S8** | 1 | Real-time per-industry dashboards (Finance / DevOps / HR pre-built layouts) | **BUILD** | CIO opens app once → instantly sees what matters. 5 days. |
| **S9** | 1 | Live reliability page (embed Grafana of nightly soak + chaos results) | **CONDITIONAL** | Only build AFTER staging chaos is running (Day 6-10 of 30-day plan) |
| **S10** | 1 | Multi-region UI (region selector + data-residency badge) | **SKIP** until first EU customer signs | Pre-mature. Wait for demand. |
| **S11** | 2 | Okta Integration Network (OIN) listing + SCIM auto-provisioning | **CONDITIONAL** | Only after 1 named Okta-customer asks. 30-day OIN review wait. |
| **S12** | 2 | Policy marketplace (community-contributed rules, install with one click) | **SKIP** until $500K ARR | Premature scaling. Curated > marketplace at <$1M ARR. |

**Total real work to BUILD: 8 sprints × 1 week = 8 weeks engineering.**
**Sprints to SKIP today: S10, S12. Sprints CONDITIONAL: S9, S11.**

---

## 2. Sprint S1 — Onboarding industry presets (BUILD, 3 days)

**Goal:** A non-engineer founder picks "Fintech" or "Healthcare" or "DevOps shop" on Step 0 of OnboardingWizard, and Aegis auto-enables the right policy packs + escalation roles + budget caps + dashboard layout.

**Files to touch:**

| File | Change |
|---|---|
| `ui/src/pages/OnboardingWizard.jsx` | Add a Step 0 "Industry" selector before the existing Step 1 (name) |
| `ui/src/data/industry_presets.js` (new) | Static config mapping industry → policy packs, default approvers, default budget caps, default dashboard preset |
| `services/policy/packs.py` | Add `_AI_STARTUP_GENERIC` pack (basic prompt injection + budget caps) |
| `services/api/router/workspace.py` | New `POST /workspace/apply-preset` endpoint |

**Industry presets (proposed):**

```js
const PRESETS = {
  fintech: {
    label: "Fintech / Banking",
    packs: ["SOC2", "FINANCE", "PCI"],
    default_approvers: { wire_transfer: "CFO", pii_lookup: "CISO" },
    budget_caps: { daily: 100, monthly: 2500 },
    dashboard: "finance",
  },
  healthcare: {
    label: "Healthcare / HealthTech",
    packs: ["HIPAA", "SOC2"],
    default_approvers: { pii_lookup: "CISO", patient_record: "Compliance Officer" },
    budget_caps: { daily: 50, monthly: 1500 },
    dashboard: "healthcare",
  },
  devops: {
    label: "DevOps / Infrastructure",
    packs: ["DEVOPS", "SOC2"],
    default_approvers: { kubectl_delete: "SRE_LEAD", terraform_destroy: "SRE_LEAD" },
    budget_caps: { daily: 100, monthly: 3000 },
    dashboard: "devops",
  },
  ai_startup: {
    label: "AI Startup / Generic",
    packs: ["AI_STARTUP_GENERIC"],
    default_approvers: { wire_transfer: "OWNER" },
    budget_caps: { daily: 20, monthly: 500 },
    dashboard: "ai_startup",
  },
};
```

**Acceptance:** A new user signs up → picks "Fintech" → 1 click → has SOC2 + FINANCE + PCI policies enabled, wire-transfer → CFO approver wired, dashboard pre-laid-out with the 6 fintech KPI tiles.

**Commercial impact:** Closes the CTO's "select governance not configure governance" objection. **Eliminates the #1 friction in time-to-first-value.**

---

## 3. Sprint S2 — Connect-button Slack OAuth (BUILD, 5 days)

**Goal:** Click "Connect Slack" → Slack auth screen → done. No more "create app at api.slack.com → add scopes → distribute → install URL → paste webhook → paste HMAC secret."

**Files to touch:**

| File | Change |
|---|---|
| `services/gateway/routers/slack_oauth.py` (new) | OAuth handshake: redirect to Slack → callback → store bot token + per-tenant webhook URL |
| `ui/src/pages/WebhookSettings.jsx` | Replace paste-fields with `<Button onClick={connectSlack}>Connect Slack</Button>` |
| `services/identity/models.py` | Add `slack_bot_token`, `slack_workspace_id`, `slack_channel_id` to tenants |
| Slack app manifest | One Slack app manifest file: `infra/slack/manifest.yml` |

**OAuth flow:**

```
User clicks Connect Slack
  → GET /sso/slack/initiate?tenant_id=X
  → 302 to slack.com/oauth/v2/authorize?client_id=...&scope=chat:write,incoming-webhook&state=<signed>
  → User picks channel + authorizes
  → 302 to https://aegisagent.in/sso/slack/callback?code=...&state=...
  → Aegis exchanges code for bot_token + webhook_url
  → Persists per-tenant
  → 302 to /settings/webhooks?ok=slack
```

**Acceptance:** A non-engineer founder clicks ONE button, signs in to Slack, picks a channel, done. No `openssl rand -hex 32`, no `api.slack.com` tab.

**Commercial impact:** This was the loudest CTO objection in the previous turn. Eliminates ~10 min of paste-fields + a docs-reading session.

---

## 4. Sprint S3 — Per-vendor SIEM wizard + connection-test (BUILD, 4 days)

**Goal:** "Connect Splunk" → modal with vendor-specific fields + screenshots + "Test Connection" button → green checkmark → done.

**Files to touch:**

| File | Change |
|---|---|
| `ui/src/pages/SiemSettings.jsx` | Replace single form with vendor-card grid; each vendor has its own modal flow |
| `ui/src/components/SiemVendorCard.jsx` (new) | Per-vendor card (Splunk / Datadog / Elastic / Sentinel / Chronicle / Sumo Logic) |
| `services/gateway/routers/siem.py` | New `POST /siem/test` endpoint that fires a 1-row test event with detailed error reporting |
| `services/audit/siem.py` | Already supports all 5 vendors; just add per-vendor `health_check()` method |
| `ui/src/assets/siem-vendor-screenshots/` | Screenshots of where to find HEC URL / API key per vendor |

**Per-vendor wizard shape:**

```
[Splunk Card]
  Step 1: "Where to find your HEC URL"  (screenshot)
  Step 2: Paste HEC URL + token
  Step 3: [Test Connection]  → green check OR specific error
                                ("HEC URL wrong format" / "Token rejected with 401" / "TLS cert invalid" / etc.)
  Step 4: Pick severity floor (CRITICAL only / ALL)
  Step 5: [Save & Activate]
```

**Acceptance:** Even a junior platform engineer can wire Splunk in <3 minutes with no docs.

**Commercial impact:** Splunk + Datadog are the two SIEMs every F500 has. Friction-free wiring → faster pilot conversion.

---

## 5. Sprint S4 — Demo workspace one-click seeding (BUILD, 5 days)

**Goal:** A prospect clicks "Show me what this looks like" on the marketing page → lands in a fully-populated demo workspace with 7 agents + 200 decisions + 3 incidents + 1 pending approval + active threat graph.

**Files to touch:**

| File | Change |
|---|---|
| `scripts/ops/seed_demo_tenant.py` (new) | Programmatic seeder: mint tenant, 7 agents (db-copilot, support-bot, devops-agent, finance-bot, marketing-writer, ops-runbook, sales-research-agent), 200 audit_log rows across 30 days, 3 incidents with different severities, 1 pending CFO approval |
| `ui/src/pages/Landing.jsx` | Add "View live demo" button → calls `/demo/seed` → redirect to demo workspace JWT-signed read-only session |
| `services/gateway/routers/demo.py` | `POST /demo/seed` mints a read-only sandbox tenant + JWT (5-min TTL) + redirects |
| `services/identity/models.py` | New `is_demo` flag on tenants — auto-deleted after 24h |

**Sample seeded data:**

- 7 agents at varying risk levels
- 200 decisions: 140 allow, 35 deny (mostly path-traversal + SQLi), 18 escalate, 4 quarantine, 3 monitor
- 3 incidents at HIGH severity, 5 days ago + 2 days ago + 6 hours ago
- 1 pending CFO approval ($250k wire transfer)
- AEVF bundle pre-computed for the previous 14 days

**Acceptance:** A founder clicks "View live demo" on `aegisagent.in/demo` → enters a workspace populated with realistic data → can click Live Feed (200 events scroll), Threat Graph (MITRE matrix lights up), Approval Inbox (1 row), Compliance (SOC2 controls), Forensics (replay a denied call). **Zero setup, zero docs.**

**Commercial impact:** Solves the cold-start problem. Empty dashboards convert at <5%; populated demos convert 25-40%. **This is the single highest-ROI sprint on the list.**

---

## 6. Sprint S5 — Hierarchical Teams (BUILD, 5 days)

**Goal:** Replace the free-text `department` field with a formal `teams` table — parent team, manager (user_id), nested rollup. Engineering Lead can see only Engineering's spend; CFO can see All.

**Files to touch:**

| File | Change |
|---|---|
| `services/identity/alembic/versions/k1l2m3n4o5p6_teams_table.py` (new) | `teams (id, tenant_id, name, parent_team_id, manager_user_id)` |
| `services/identity/models.py` | New `Team` model + `users.team_id` FK |
| `ui/src/pages/Team.jsx` | Add Teams tab — tree view with drill-down to per-team rollup |
| `services/gateway/routers/messages.py:792 team_overview` | Replace `department` group-by with `team_id` |
| `ui/src/pages/TeamSettings.jsx` (new) | Create/Rename/Move teams + assign managers |

**Acceptance:** A CFO sees a 3-level tree (Engineering → Backend / Frontend / Mobile; Finance → AP / AR; Sales → SDRs / AEs / CS) with per-team spend, harmful-blocked count, and a click-through to per-employee. The Engineering Lead sees only Engineering + children.

**Commercial impact:** Mid-market sells on "your manager sees what their team is doing." Free-text department fails this — Sales = Sales ≠ Sales-EU. **Required for 50-500 person customer tier ($4,999/mo).**

---

## 7. Sprint S6 — One-click SOC 2 evidence bundle (BUILD, 4 days)

**Goal:** The Compliance page has a "Generate SOC 2 Evidence Bundle" button. One click → 30-second progress bar → downloads a ZIP that maps every relevant audit_log row to a Trust Services Criteria control (CC6.1, CC7.2, CC8.1, etc.) with the cryptographic chain proof attached.

**Files to touch:**

| File | Change |
|---|---|
| `services/audit/compliance_export.py` | Add SOC 2 mapping — per-criterion query + chain-walk + Merkle inclusion proof |
| `ui/src/pages/Compliance.jsx` | Add "Generate Evidence Bundle" with framework dropdown (SOC2 T1 / SOC2 T2 / HIPAA / PCI / ISO27001) |
| `services/audit/grc_export.py` | Add per-control evidence row format Vanta/Drata can ingest |
| `infra/grafana-dashboards/soc2-controls.json` | Per-control dashboard so the founder can SEE coverage gaps before bundle generation |

**Bundle shape:**

```
aegis-soc2-evidence-2026-Q2.zip
├── controls/
│   ├── CC6.1_access_control_evidence.csv         (every auth event for the quarter)
│   ├── CC7.2_monitoring_evidence.csv             (every audit row + signal)
│   ├── CC8.1_change_management_evidence.csv      (every policy version + diff)
│   └── ...
├── chain_proofs/
│   ├── 2026-04-01.json  (Merkle root + chain to genesis)
│   ├── 2026-04-02.json
│   └── ...
├── verify.sh                                     (one-line aegis-verify wrapper)
├── README.md                                     (auditor walkthrough)
└── manifest.json                                 (control IDs + counts + signatures)
```

**Acceptance:** A founder hands the ZIP to their Drata/Vanta auditor; the auditor runs `bash verify.sh` and sees `[PASS] V1-V6 for every control bundle`. No Aegis access needed.

**Commercial impact:** This is the moat. **One bundle export → 60% of SOC 2 T1 evidence collected.** A non-Aegis startup spends 4-8 weeks gathering this manually.

---

## 8. Sprint S7 — Marketplace landing tiles (BUILD, 3 days)

**Goal:** First thing a founder sees on the Dashboard after signup: 6 tiles asking "Connect Slack? Connect Datadog? Connect Okta? Connect PagerDuty? Connect Splunk? Connect Sentinel?" with progress indicators. Currently these are buried 3 clicks deep in Settings.

**Files to touch:**

| File | Change |
|---|---|
| `ui/src/pages/Dashboard.jsx` | Add `<IntegrationsRow />` component above the existing KPI grid for first-7-days workspaces |
| `ui/src/components/IntegrationCard.jsx` (new) | Tile with logo, "Connect" or "Connected ✓", click → modal or OAuth |
| Backend already done — just routing |

**Acceptance:** A 1-day-old workspace shows the integration row prominently; a 30-day-old workspace shows it collapsed under "Integrations" link.

**Commercial impact:** Discoverability. Most prospects don't even know SIEM forwarding exists until they ask a sales call.

---

## 9. Sprint S8 — Per-industry pre-built dashboards (BUILD, 5 days)

**Goal:** When the OnboardingWizard preset is "Fintech," the Dashboard layout that loads is FINANCE-flavored (wire-transfer escalations tile, monthly spend tile, CFO approval queue tile, PII lookup tile). DevOps preset → DEVOPS layout (kubectl/terraform escalations, prod-namespace blocks, runaway-loop counters). Healthcare → HIPAA-flavored.

**Files to touch:**

| File | Change |
|---|---|
| `ui/src/data/dashboard_layouts.js` (new) | Per-industry layout definitions |
| `ui/src/pages/Dashboard.jsx` | Read `tenant.dashboard_preset` and render the appropriate layout |
| `services/identity/models.py` | Add `dashboard_preset` to tenants (already set by S1 preset apply) |

**Acceptance:** A Fintech founder logs in on Day 1 and the dashboard shows wire-transfer-flavored tiles, not generic AI tiles. They never have to configure or customize.

**Commercial impact:** Mid-market customer says "this dashboard already looks like our financial controls binder." Reduces dashboard customization-debt that kills SaaS adoption.

---

## 10. Sprint S9 — Live reliability proof page (CONDITIONAL, 5 days)

**Goal:** A public `aegisagent.in/reliability` URL that embeds a Grafana dashboard showing: nightly soak test pass rate (30 days), nightly chaos test pass rate, last 90 days of `aegis-verify` runs on the public reference bundle, SLA up-time.

**Why CONDITIONAL:** This only makes sense AFTER the 30-day plan lands staging.aegisagent.in with the nightly Suite A/B/D/E + chaos harness. **Until then, the dashboard would be empty or fake — anti-evidence.**

**Files to touch:**

| File | Change |
|---|---|
| `infra/grafana-dashboards/public-reliability.json` (new) | Public Grafana board |
| `ui/src/pages/Reliability.jsx` (new) | iframe + commentary |
| `.github/workflows/nightly-suite.yml` (new — depends on S0 staging) | The nightly job that produces the data |

**Acceptance:** A prospect can click `aegisagent.in/reliability` and see real-time reliability evidence without signing up.

**Commercial impact:** Replaces "we say we're reliable" with "look at our public Grafana." Procurement reviewers love this. But premature without real data.

---

## 11. Sprint S10 — Multi-region UI (SKIP until first EU customer)

**Goal:** Region selector at signup; data-residency badge on every page; per-region pricing tier.

**Why SKIP:** Multi-region engineering cost (8 weeks Terraform + Postgres replication + bundle distribution) is far higher than the UI cost (1 week of UI work). **Build the infra first when a real EU customer asks; the UI is a 1-week wrap on top of working infra.**

**Cost of premature build:** 1 week of UI work that ships dark + becomes architecturally outdated by the time you actually have EU infra.

---

## 12. Sprint S11 — Okta OIN listing + SCIM (CONDITIONAL, 2 weeks)

**Goal:** "Connect Okta" button → Okta's "Add Application" wizard → SCIM auto-provisioning for new hires.

**Why CONDITIONAL:** Okta Integration Network (OIN) review takes 30 calendar days. SCIM endpoint is 2 weeks of engineering. **Build only when a paid customer asks for it by name.** Until then, generic OIDC works.

**Cost of premature build:** OIN listings need to be maintained, updated on every release. Without a customer paying, this is technical debt.

---

## 13. Sprint S12 — Policy marketplace (SKIP until $500K ARR)

**Goal:** Community-contributed Rego policies, install with one click, rate / comment / fork.

**Why SKIP:** Marketplaces need network effects — 100+ contributors before they're valuable. At <$500K ARR, you have 0-5 customers. **Curated starter packs (already shipped: SOC2, PCI, HIPAA, FINANCE, DEVOPS) beat empty marketplace for the first 50 customers.**

**Cost of premature build:** Empty marketplace = broken-window signal to prospects.

---

## 14. Honest closing — what to ACTUALLY do this week

If I were the founder, here's the ordering I'd execute:

### Week 1 (engineering + GTM in parallel)
- **Mon**: Sign Drata or Vanta SOC 2 vendor (4 hr).
- **Mon-Tue**: Land inst-1 deploy flap fix (`pool_mode=session` for audit OR `NullPool`) — closes yesterday's HIGH from the audit.
- **Tue-Thu**: **S1 — Industry presets** (3 days). Highest ROI: closes the "configure vs select governance" CTO objection.
- **Fri**: Email 10 design-partner prospects from `docs/sales/design-partner-outreach.md`. **Real customer outreach trumps any sprint.**

### Week 2
- **Mon-Fri**: **S4 — Demo workspace seeding** (5 days). Cold-start fix. Sales conversion jump expected: 3-5×.
- Hold 2 founder discovery calls per day with prospects.

### Week 3
- **Mon-Fri**: **S2 — Slack OAuth** (5 days). Closes the loudest CTO objection.
- 1 design partner signed mid-week (if outreach was real).

### Week 4
- **Mon-Thu**: **S3 — SIEM wizard** (4 days).
- **Fri**: Onboard first design partner. **Their feedback dictates next 4 sprints, not this plan.**

### Weeks 5-8 (gated on customer feedback)
- **S5 Hierarchical Teams** OR **S6 SOC 2 bundle export** OR **S7 marketplace tiles** OR **S8 industry dashboards** — let the design partner tell you which one matters first.

### What I'd actually NOT do
- ❌ Sprints S9-S12 in the first 8 weeks. Premature.
- ❌ Any feature outside this 12-sprint list. Stay focused.
- ❌ Rewriting OnboardingWizard from scratch. It's 90% there.
- ❌ Custom OAuth for SIEM vendors (Splunk doesn't have a public OAuth; paste-token is fine — just make the wizard great).
- ❌ Mobile app, white-label, on-premise — until $1M ARR.

---

## 15. The brutal one-liner

**You don't have a UI problem. You have a "no customers" problem.** Every UI sprint above is real engineering work, but **none of it sells the first deal**. The first deal is sold by: a working demo (S4), a one-click Connect-Slack (S2), and a founder making 50 sales calls.

**Order: S0 (sign Drata + 10 outreach emails) → S1 → S4 → S2 → S3 → S5/S6/S7/S8 (customer-prioritized) → S9/S10/S11/S12 (skip until needed).**

**8 sprints to BUILD over 8 weeks. 4 sprints to SKIP until commercial demand exists.** And the UI you already have ships 85% of the platform.

**The honest answer to "can you implement these without random or code = product":** Yes, every sprint above is implementable by you in the calendar days listed, no new architecture required. **But the harder question is "should you?" — and the answer for half the list is "not yet."**

---

*Generated 2026-06-19 IST. Each sprint scoped against verified file paths in this repo. Build/Skip verdicts reflect commercial impact at $0 ARR / 10 users / 0 paying customers — re-evaluate at $500K ARR.*
