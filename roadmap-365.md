# roadmap-365.md — Aegis: 6-month plan to a Series-A-ready product

**Author:** the CTO / VP Eng you keep asking me to be.
**Audience:** you, the founder.
**Format:** answers the 10 strategic questions you asked, then a
month-by-month plan, hiring, metrics, and what Day 365 looks like.
**Tone:** straight. No hedging. Where I'm uncertain I say "bet" not "fact."

---

## Part 1 — Answers to your 10 questions

### Q1. Which market should we dominate first?

**Healthcare + fintech mid-market deploying VOICE AI in customer-facing
roles.** Specifically:

- **Digital-health companies** (Hims, Ro, Cerebral, Talkspace, Headspace
  Health, K Health) with 100–1,000 employees, deploying voice agents
  for appointment scheduling, intake triage, prescription refills.
  HIPAA forces evidence. AI adoption is happening NOW.
- **Fintech contact centers** (Chime, Mercury, Brex, Modern Treasury,
  Ramp) using voice AI for support. SOC 2 + PCI exposure.
- **Insurance claims call centers** (Lemonade, Hippo, Root) using AI
  assist for claims adjusters.

**Why this slice:**
1. **Voice is less crowded** than text — Lakera, Lasso, Calypso, Robust
   Intelligence are all text-LLM-first. Voice-aware governance is wide
   open.
2. **HIPAA + PCI = forced audit trail.** You sell to fear + regulation,
   not aspiration.
3. **Buyer is identifiable.** Director of Compliance + Director of IT,
   joint sign-off, $24K–$120K budget per year, decisions made in 30–60
   days.
4. **Failure stories already exist.** UnitedHealth Optum's GPT bot
   leaked PHI in 2024. Air Canada's chatbot lawsuit ($812 to a
   passenger, $millions in PR damage). You can ride those headlines.

**Don't pursue:** general "AI for everyone" governance. That's where
Lakera and Datadog are. You lose.

---

### Q2. Which features should we freeze for 12 months?

These work. Touch only for bug fixes:

- All 13 "Advanced analyst" UI surfaces (Flight Recorder, Decision
  Explorer, Threat Graph, Identity Graph, Session Explorer, Replay,
  Fleet, Evaluation, ThreatIntel, Shadow Mode/Review, Forensics, Agent
  Playground, Agent Snapshot). They're cool. They're not what the
  buyer pays for. Hide them behind an Advanced toggle.
- Demo workspace flow (Spawn-demo CTA). It works. Don't touch.
- 4 PyPI SDKs — bug-fix-only releases. Stop bumping every other day.
- Shadow mode + kill switch + cumulative risk pipeline — solid.
- Compliance bundle export for SOC 2 / EU AI Act / NIST AI RMF.
- AEVF offline verifier (`aegis-aevf` on PyPI).
- ed25519 + Merkle chain itself — V3 + V5 are fixed this sprint.
- /pricing page (you have one now).
- The 4-wave arch-26 fixes that just shipped.

**Why freeze?** Engineering effort spent on these = engineering effort
NOT spent on closing your first 25 paying customers. Resist the urge.

---

### Q3. Which features should we delete?

Hard delete, this month:

- **`services/learning/`** — refactor to a library under
  `sdk/common/learning.py` (it's already imported as a library by
  `services/behavior/`; the separate alembic + database adds 0 value
  and 1 service worth of ops).
- **`services/insight/`** — worker runs but no UI surfaces it.
  Either point Live Feed at it (1 day of work) or rip it. Recommend
  rip.
- **`services/mcp_server/`** — it's a library mislabeled as a service.
  Move to `sdk/mcp/`.
- **3 GitHub repos worth of obsolete examples** in `docs/examples/`
  (audit which ones, but you have ~10 example scripts that no
  customer will read).
- **`/agents/playground` from the default sidebar** — useful for you
  internally; confusing for buyers. Move behind Advanced toggle.
- **5 of the 13 OPA Rego rule files** that have never fired against
  real traffic (per `acp_policy_hits_total` Prometheus query — if zero
  hits in 60 days, delete).
- **The 626-line OnboardingWizard.jsx** — replace with a 100-line
  3-step flow: "Mint key → Copy snippet → Ship a call."
- **Multi-region terraform stubs** that don't deploy anything —
  delete entirely. Re-add when EU data residency is a real
  enterprise demand (not before).
- **The 13 Compose-merged files** in `infra/` — collapse to 3
  (`docker-compose.yml` + `docker-compose.aws.yml` + override).

---

### Q4. Which three features create the strongest moat?

**Moat #1 — Cryptographically verifiable audit chain.**
ed25519-signed Merkle roots, mirrored to a public S3 bucket, with an
offline open-source verifier (`aegis-aevf`). **Lakera, Lasso, Calypso,
Hidden Layer, Datadog LLM Obs, Portkey, Langfuse, Helicone — NONE of
them have this.** Your auditor doesn't have to trust you. That's a
genuine technical differentiator that survives copycat attempts for at
least 18 months because rebuilding it requires a credible cryptography
investment + a public-mirror reputation that takes time to build.

**Moat #2 — Voice-aware governance.**
The proposed voice SDK + voice-policy primitives + voice-audit-trail.
**Doesn't fully exist yet** — you'll build it in Month 3. But once
shipped, it's a defensible niche because:
- Voice platforms (Vapi, Retell, LiveKit Agents, ElevenLabs) have
  their own SDKs — you wrap them like aegis-anthropic wraps Anthropic.
- Voice has 5+ unique attack surfaces (audio injection, voice cloning,
  cross-modal jailbreak, real-time intervention) that text-first
  competitors will take 12+ months to ship.

**Moat #3 — Per-employee attribution + manager hierarchy.**
The Department + Manager model from `guide-arch-26.md` Day 3. Lakera
+ Lasso are workspace-level (one bucket per company). Aegis becomes
**per-employee + per-department + per-tenant**, which is what an IT
Director or HR Director actually buys: "show me what my team's Claude
did this week" or "Alice's spike yesterday."

**Combined moat statement:** "Aegis is the only platform that gives
you (a) verifiable proof your AI didn't lie, (b) voice-aware
governance, and (c) per-employee attribution — in one product."

---

### Q5. What would the first 100 paying customers look like?

**Persona breakdown:**

| Segment | Count | Profile |
|---|---|---|
| Digital health (HIPAA) | 30 | Hims/Ro/Cerebral-shaped, 100–500 emp, voice intake bots |
| Fintech contact centers | 25 | Chime/Brex/Mercury-shaped, voice support |
| Insurance | 15 | Lemonade-shaped, AI claims assist |
| SaaS with customer support AI | 15 | Customer support voice + text AI |
| BPO / contact center operators | 15 | Concentrix-shaped, multi-tenant AI rollouts |

**Buyer:** VP/Director of Compliance + Director of IT (joint).
**Champion:** lead AI engineer who deployed Claude.
**Geography:** US 60, EU 25 (GDPR + EU AI Act force evidence), India 10,
other 5.
**ACV:** $24K average year 1, climbing to $60K by year 3 as Enterprise
mix grows.
**ARR at customer #100:** $2.4M.
**Sales cycle:** first 20 take 60 days; after 3 case studies, 14–30 days.
**Discovery channel:** CISO Slack communities (CISO Series, OWASP),
HIPAA-focused LinkedIn groups, BSides talks, podcast appearances on
"Cloud Security Podcast" and "Defense in Depth."

---

### Q6. What pricing should we use?

**Three-tier, mostly enterprise.**

| Tier | Price | Audience | What's included |
|---|---|---|---|
| **Free** | $0 | Evaluators | 1 workspace, 5 agents, 1k actions/day, 30-day retention, Aegis-branded |
| **Pro** | **$999/mo per workspace** | Mid-market self-serve | Up to 50 employees, 100k actions/day, all SOC 2/EU AI Act bundles, Slack approvals, ed25519 chain, public Merkle mirror, email support |
| **Enterprise** | **$5K–$25K/mo per workspace** | 200+ employee orgs | Unlimited employees, HIPAA BAA, **voice agent SDKs**, BYOC option, SAML+SCIM, EU data residency, named CSM, custom Rego, quarterly key rotation drill, 4h Sev-1 SLA |

**Pricing tactics:**
- **No free Pro trial — 30-day money-back guarantee instead.** Free
  trials signal commodity. Money-back signals confidence.
- **Annual contracts only on Enterprise** (20% discount for 1y prepay).
- **Per-employee pricing is the WRONG knob** — buyers hate scaling
  fees as headcount grows. Charge per workspace + per "module"
  (voice = +$2K/mo).
- **Don't undercharge.** Anchor at $999/mo. The first 3 customers will
  try to negotiate to $499 — politely decline. If they walk, they
  weren't real buyers.

**Year 1 ARR mix target:** $1M total = 25 × $20K Pro + 5 × $100K Ent.

---

### Q7. Series-A pitch deck narrative

**10 slides. Each one sharp.**

1. **Hook (60 sec):** "Every Fortune 500 is putting Claude and GPT in
   front of customers via voice agents. They can't tell their auditor
   what those agents did. Aegis can."
2. **Problem:** "AI agent leaks PHI. AI voice bot promises a refund
   the company won't honor (Air Canada). AI tool calls deletion on
   prod. CISOs are caught between adoption pressure and audit failure
   risk."
3. **Solution screen:** the manager dashboard + the verifiable bundle
   export + the voice agent live feed. **Three screens, 90 seconds.**
4. **Market sizing:** $50B AI governance TAM by 2030 (Gartner). $5B
   SAM for verifiable + voice. $50M SOM by year 5.
5. **Traction:** logos. ARR. NRR. Net new ARR / month curve. The
   first 3 case studies (logo + quote + dollar saved).
6. **Why us / why now:** voice AI adoption hit hockey stick in 2024-25;
   EU AI Act Article 12 enforcement Feb 2025; SOC 2 CC8.1 expanded
   coverage 2026. Aegis was the only verifiable-audit platform on
   the market when the regulation landed.
7. **Product moat:** the 3 from Q4. Cite specific competitor diffs.
8. **Team:** founder + 4 engineers + 1 GTM. Advisors: 1 ex-CISO, 1
   ex-Anthropic safety eng, 1 ex-Datadog GTM.
9. **Competition 2×2:** axes = "verifiable / closed source" and
   "voice-aware / text-only." You're alone in the top-right.
10. **Ask:** $6M Series A at $30M post → 18-month runway, 10 engineers,
    5 GTM, first 100 customers, SOC 2 Type II in Q4 2026.

**Tell the story in the order:** problem → solution → traction →
moat. Not features. Investors don't buy features.

---

### Q8. Technical debt to deliberately ignore for 12 months

Carry these on the balance sheet:

- **17→6 service consolidation.** Operationally painful but works.
  Fix when team grows past 8 engineers.
- **alembic_version varchar(32) limit.** Already used short rev IDs;
  document the convention. Don't migrate the existing column.
- **Stripe outbound.** Skip if Enterprise-only motion. Manual invoicing
  is fine at <50 customers. Build at customer #75.
- **57 UI page count.** Hide 48; don't delete.
- **Mobile responsive design at 375px.** Your buyer is at a 27" iMac
  reviewing dashboards. Mobile is not their first concern.
- **aegis-langchain async support.** LangChain users are not your
  ICP. Defer.
- **Cross-region replication.** Multi-AZ is enough until you have an
  EU customer demanding it. Re-evaluate at customer #50.
- **The 6 retracted arch-26 items.** Don't re-investigate. They were
  false positives.
- **Refactoring `services/policy/canonical.py`** (903 lines). It works,
  the test coverage is decent, the cost of refactoring outweighs the
  benefit until you ship a second policy engine.
- **i18n.** English-only until you sell EU customer #5.
- **Per-tenant feature flags.** Use environment variables until tenant
  count > 50.
- **Audit log partitioning.** Already migration-locked one-way; defer
  to a real DBA-led project at customer #100.

**Rule:** if no paying customer has asked for it in writing, you
don't build it.

---

### Q9. Metrics investors care about every month

**The investor-facing dashboard, ranked by what gets a Series A funded:**

**Tier 1 (every monthly investor update leads with these):**
- **Net New ARR / month** — the slope is what matters. Growth >15% MoM
  for the first 12 months is the signal.
- **Logo count** — 3 → 10 → 25 → 50 → 100 in 12 months is the curve.
- **Net Revenue Retention (NRR)** — target 110%+ at 12 months. NRR
  >120% is "best in class" and gets premium multiples.
- **CAC payback period** — target <12 months. Anything <9 is great.

**Tier 2 (every other slide):**
- **Logo retention** — target 95%+ annual.
- **ACV trend** — climbing means you're moving upmarket.
- **Sales cycle length** — target 30 days median by month 6.
- **Pipeline coverage** — 3× the quarterly target in qualified pipe.

**Tier 3 (product health, included for completeness):**
- **Time-to-first-protected-call** — from signup to first /execute.
  Target <30 min.
- **% customers with >100 actions/week** — engagement gauge.
- **p95 /execute latency** — gateway internal, <50ms.
- **Decision-engine availability** — target 99.9%.
- **Audit chain integrity violations** — target 0. Zero. Forever.
- **Customer-reported Sev-1 incidents** — target 0/quarter.

**Pick ONE star metric** to lead every investor update with — for
Aegis I'd pick **Net New ARR / month with a 12-month trailing chart**.
Investors read that one chart and decide whether to lean in.

---

### Q10. What should the product look like on Day 365?

**Concrete vision:**

**Services (6):**
1. `gateway/` — /execute + /v1/messages proxy hot path
2. `identity/` — Clerk + RBAC + Department + Manager
3. `audit/` — Merkle chain + compliance bundle
4. `policy/` — OPA + signal registry + canonical
5. `billing/` — Stripe outbound + per-employee + per-dept rollups
6. `registry/` — agents + employees + permissions

**UI pages (~20):**
- 12 customer-default (Dashboard, Live Feed, Agents, Team, Departments,
  Manager Dashboard, Per-Employee Timeline, Policies, Incidents,
  Approval Inbox, Compliance, Billing, Settings)
- 5 admin (Settings tabs: SSO, SCIM, Slack, Webhooks, Plan Management)
- 3 hidden-by-default "Advanced" (Flight Recorder, Decision Explorer,
  Threat Graph)

**SDKs (5):**
- 4 current (aegis-anthropic, openai, langchain, bedrock)
- 1 new: **aegis-voice** wrapping Vapi + Retell + LiveKit Agents +
  ElevenLabs Conversational AI

**New product capabilities:**
- Voice-aware policy engine (transcription → intent classification →
  audit trail with audio reference)
- Department + Manager model live
- Manager Dashboard + Per-Employee Timeline
- Stripe outbound (real invoicing)
- Self-serve Pro tier signup
- Cost anomaly detection (z-score per employee, Slack alert)
- Daily Slack digest bot for managers
- SOC 2 Type I certified
- HIPAA BAA template available

**Business state:**
- 25–50 paying customers
- $600K–$1M ARR
- 3 case studies on website
- 1 published security research piece
- 1 conference talk delivered
- Multi-AZ in us-east-1 + ap-south-1; EU readiness designed (not yet
  deployed)
- Team: founders + 5–8 hires (5 eng, 2 GTM, 1 CS)

---

## Part 2 — Six-month plan, month by month

### Month 1 — POSITION + DISCOVER (no new features)

**Goal:** know exactly who you're selling to. Find first 3 design
partners.

**Week 1:**
- Day 1: Rewrite Landing.jsx hero around the verifiable + voice + per-
  employee positioning. Push live.
- Day 2-3: Build a 1-page "What is Aegis?" PDF for cold outreach.
- Day 4-5: Identify 100 target accounts (50 healthtech, 30 fintech,
  20 insurance/SaaS). Use Crunchbase + Clay + LinkedIn Sales Nav.

**Week 2:**
- Send 100 cold emails. Personal, 4 sentences, "I built X, want to be
  design partner #1 for free?" Track replies in Airtable.
- Target reply rate: 15%. Target meetings booked: 10–15.

**Week 3-4:**
- Run 20 customer discovery calls (30 min each). Goal: validate the
  positioning. Listen for "I need this" vs "interesting but…"
- Pick 3 design partners. Get them on a 90-day free pilot agreement.
- **Output by EOM:** Landing live, 3 signed design partner LOIs,
  positioning validated.

**Don't build features this month.** Ship the 7-day plan from
`guide-arch-26.md` only if a design partner explicitly asks.

**Cost:** ~$500 (cold email tooling). Founder time: 100%.

### Month 2 — MVP FOR DESIGN PARTNERS

**Goal:** ship the manager-dashboard story + the department model.
Get design partners using it in production.

**Week 1: Department + Manager model**
- Alembic migration: `departments` + `team_memberships` tables.
- Add `MANAGER` role to `_rbac_map.py:ROLE_TIERS`.
- Backfill: each existing tenant gets a "Default" department.

**Week 2: Manager Dashboard UI**
- New page: `/team/department/{id}`.
- 30-day spend chart, employee table, escalations feed.
- Manager-scoped — only sees own department.

**Week 3: Per-Employee Timeline**
- New page: `/team/employee/{id}`.
- 30-day usage line chart, tool-call breakdown, deny rate.
- Export CSV button.

**Week 4: Design partner onboarding**
- 3 partners deployed in production. Daily check-ins.
- Instrument every click. Find friction. Patch.
- **Output by EOM:** 3 live deployments, 3-7 days of real production
  traffic, first NPS scores.

**Hire:** 1 founding engineer (full-stack Python+React). Source from
your network or YC W23/24 talent pool.

### Month 3 — VOICE WEDGE

**Goal:** ship the voice SDK + voice-aware policy. Make this your
moat-defining moment.

**Week 1: Voice SDK MVP**
- New package: `aegis-voice` on PyPI.
- Wraps Vapi, Retell, LiveKit Agents, ElevenLabs Conversational AI.
- Same `_AegisGuard` pattern as the text SDKs.

**Week 2: Voice-aware policy engine**
- Transcript → intent classifier (use Claude Haiku as the classifier).
- Voice-specific signal registry entries: voice_jailbreak,
  voice_clone_attempted, off_script_promise_made, pii_disclosed_audio.
- Audit row carries audio s3 reference (encrypted).

**Week 3: Voice Live Feed UI**
- New event type in Live Feed: voice_decision with audio playback.
- Customer can listen back to what the agent said + agent input.

**Week 4: First voice case study**
- Pick the design partner with most voice usage.
- Deploy. Capture 1 week of blocked attempts.
- Co-author a case study with them: "How [Healthcare X] caught 12
  PHI disclosures from their voice intake bot in 7 days."
- **Output by EOM:** voice SDK on PyPI, 1 published case study draft.

**Hire:** 1 GTM/sales hire. Technical SDR/AE profile. Healthcare or
fintech sales background preferred.

### Month 4 — COMMERCIAL (Pricing, Stripe, Self-Serve)

**Goal:** start charging real money. Land the first 10 paying customers.

**Week 1: Stripe outbound**
- Build `stripe.Customer.create()`, `stripe.Subscription.create()`,
  `stripe.Invoice.create()`. Wire `invoice.payment_succeeded` webhook.
- Remove the "Beta — usage view only" banner from Billing.jsx.
- Real card-on-file → real recurring billing.

**Week 2: Self-serve Pro signup flow**
- New page: `/signup/pro` → Clerk auth → Stripe checkout → workspace
  provisioned → first agent registered in 10 minutes total.
- Email drip: Day 0 "Welcome," Day 1 "Quick wins," Day 3 "Set up
  Slack approvals," Day 7 "Manager dashboard tour."

**Week 3: Convert design partners to paid**
- 3 design partners → 2 sign Pro contracts (3rd churns gracefully).
- Add 5 more from the 50 you've been nurturing since Month 1.

**Week 4: Hit first MRR milestone**
- Target: **10 paying customers, $15K MRR.**
- Public quote-bait: "We just crossed $X MRR in our 4th month of
  selling." Founder LinkedIn post.

### Month 5 — TRUST + COMPLIANCE EVIDENCE

**Goal:** build the reputation artifacts that convert mid-funnel into
won deals.

**Week 1-2: SOC 2 Type I**
- Start the audit with Vanta or Drata (~$10K). Type I is a snapshot;
  Type II takes 6+ months of evidence.
- Output: Type I certificate by EOM, public Trust Center page.

**Week 3: HIPAA BAA**
- Template BAA agreement (use Cooley or Wilson Sonsini, ~$5K legal).
- Public landing page section: "HIPAA-ready, BAA available."

**Week 4: Published security research piece**
- Pick a high-profile attack class: voice cloning + cross-modal
  jailbreak.
- Red-team Anthropic/OpenAI voice agents. Publish responsible
  disclosure + your detection methodology.
- Post on r/AISafety, Hacker News, your CTO LinkedIn.
- This is the "earn reputation" play. One viral post = 50 inbound
  leads.

### Month 6 — SCALE PREP + FUNDRAISE

**Goal:** ready the Series A. Have the proof points investors want.

**Week 1: Conference talk**
- BSides Las Vegas, AI Village, or AICAS — submit talk: "Building
  a verifiable AI audit chain that auditors can verify without us."
- If timing missed, do a public webinar with 200+ registrants.

**Week 2: Quarterly business review**
- Internal review: ARR, NRR, CAC, retention, NPS.
- Update the 10-slide deck. Add real numbers.

**Week 3: Investor outreach**
- Target 15 Series A funds (Lightspeed AI, Greylock, Bessemer, a16z
  AI fund, Felicis, NEA, Lux). Warm intros only.
- Run 8-10 first meetings.

**Week 4: Term sheet**
- Realistic outcome: 1-2 term sheets at $25-40M post for $5-8M raise.
- If outcome is no term sheet: 6-month bridge round from existing
  angels, keep grinding, raise A in month 9-10.

**Output by EOM 6:**
- **25 paying customers**
- **$50K MRR ($600K ARR run rate)**
- **SOC 2 Type I certificate**
- **3 case studies live**
- **1 published security research piece**
- **1 conference talk delivered**
- **1-2 term sheets in hand OR strong investor pipeline for month 7**

---

## Part 3 — Hiring plan

| Month | Hire | Profile | Cost |
|---|---|---|---|
| 1 | Founder doing everything | — | — |
| 2 | **Founding Engineer #1** | Full-stack Python + React, 5+ yrs, ex-startup | $200K salary + 1.5% equity |
| 3 | **GTM #1 (SDR/AE)** | Technical sales, healthcare or fintech background | $150K OTE (50/50 split) + 0.5% equity |
| 5 | **Customer Success #1** | Technical CS, ex-developer, can demo + onboard | $130K + 0.2% equity |
| 6 | **Founding Engineer #2 + Designer** | Both fractional initially | $300K combined + 1% combined |

**Team at end of Month 6:** founders + 4-5 hires = 6-7 people.

**Burn at Month 6:** ~$100K/mo. Runway from $500K seed: 5 months.
Plan to raise Series A in Month 6-7 to extend runway 18+ months.

---

## Part 4 — Investor metrics dashboard (monthly update format)

```
AEGIS MONTHLY UPDATE — Month X

★ STAR METRIC
   Net New ARR: $X (Y% MoM growth)

BUSINESS
   Total ARR:           $X
   Customer count:      X (was Y last month)
   ACV (annualized):    $X
   Net Revenue Retention: X%
   Gross Margin:        X%
   CAC:                 $X    Payback: X months
   Cash:                $X    Runway: X months

PRODUCT
   p95 /execute latency:         Xms (SLO: <50ms)
   Decision-engine availability: X% (SLO: 99.9%)
   Audit chain violations:       X (SLO: 0)
   Time-to-first-protected-call: X min median (target: <30min)

PIPELINE
   Qualified opportunities: X    ($X total value)
   In-cycle deals:          X    ($X total value)
   Lost deals + reasons:    [list]

LOWLIGHTS / RISKS
   [3 honest items — investors lose trust when only highlights appear]

ASKS
   [1-2 specific introductions or advice asks]
```

---

## Part 5 — Brutal closing

**What this plan assumes that might not be true:**

1. You can find 3 design partners in Month 1 by cold outreach. If you
   can't, the whole timeline shifts right by 1-2 months. Mitigate
   by starting outreach this week.

2. Voice agent adoption stays on its current curve. If Anthropic /
   OpenAI ship native voice safety, your voice wedge narrows. Mitigate
   by going deep on the verifiable-audit moat for voice specifically.

3. The team can ship the Manager Dashboard + Voice SDK in 2 months
   with 1 new hire + the founder. Tight. Possible. If a key bug eats
   a week, ship the Manager Dashboard first and defer voice to Month 4.

4. SOC 2 Type I lands in 4-6 weeks (typical with Vanta/Drata fast-track).
   If it slips to 8 weeks, push the security research piece to Month 6
   and run the fundraise in Month 7.

5. **Cash.** You haven't told me how much runway you have. If <6 months,
   compress the timeline and raise sooner. If 12+, you can be patient.

**What this plan needs from you that I can't do:**

- Pick the positioning sentence and post it on the team wall.
- Cold-email the 100 prospects in Month 1, Week 1-2.
- Make the design partner pitches yourself. Founder-led for first 10
  customers, then hand to the GTM hire.
- Make the call on Stripe outbound vs Enterprise-only motion in
  Month 4.
- Decide whether to bridge round or full Series A in Month 6.

**The codebase is more than ready.** The positioning, the GTM motion,
the first 10 customer conversations — those are the gates now, not
the engineering.

If you ship this plan with the same discipline you shipped arch-26,
you have a **fundable Series A demo in 6 months and $1M ARR in 12.**

— end of roadmap-365.md —
