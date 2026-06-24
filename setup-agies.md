# Aegis — A Practical Guide for the Person Evaluating Us

**For:** the security lead, CISO, compliance officer, or platform owner who is about to put us through an evaluation.
**Time to read:** 20 minutes.
**Time from this page → first decision audited:** 10 minutes.
**Site:** https://aegisagent.in

> Already signed up and rolling out to your team? For the post-signup
> adoption flow (Day 0 → Day 7), see [ui-setup.md](ui-setup.md). This
> document is the evaluator's 5-minute no-signup tour.

This document is intentionally written for the *person who has to make the buy-or-pass decision* — not the engineer who will integrate later (that audience has its own SDK reference at https://aegisagent.in/developer). Wherever you see a screen, you can open it in your own workspace and verify the claim live before you read on.

---

## What Aegis actually does (in one paragraph)

Your business is starting to use AI agents — Claude, GPT, Bedrock, in-house tools — to take real actions on real production systems. The question your board is going to ask you, sooner than you think, is "what happens when one of them does something it shouldn't?" Aegis is the layer that catches that. Every tool call your AI agent attempts — every SQL query, every email, every wire transfer, every file delete — passes through a 10-stage policy engine whose **inter-service round-trip is p95 ≈ 28 ms** (`/system/health.latency`); the user-facing `/execute` round-trip from your agent — which is what your audit dashboard quotes — depends on your network distance to ap-south-1 and on concurrent traffic in the gateway. We publish both numbers on `/status` so you can verify them yourself. We decide allow / monitor / escalate / deny / quarantine, we sign the decision with ed25519, and we publish the daily Merkle root to a bucket you can verify offline without trusting us. You get the receipts. Your auditor gets the chain. Your CFO gets the kill switch.

We are not another LLM. We are not a chat product. We are the governance plane that sits between your AI and the systems your AI can touch.

---

## Table of contents

1. [What you'll evaluate](#1-what-youll-evaluate)
2. [Live 5-minute tour — no signup needed](#2-live-5-minute-tour--no-signup-needed)
3. [Sign up + your first 10 minutes](#3-sign-up--your-first-10-minutes)
4. [The four product modules](#4-the-four-product-modules)
5. [Wire up your first agent (Path A vs Path B)](#5-wire-up-your-first-agent)
6. [Page-by-page tour of every screen](#6-page-by-page-tour-of-every-screen)
7. [What Aegis catches out of the box](#7-what-aegis-catches-out-of-the-box)
8. [Cryptographic evidence — the moat that compounds](#8-cryptographic-evidence)
9. [Integrations — SSO, Slack, SIEM, ITSM, webhooks](#9-integrations)
10. [Roles + RBAC — who can do what](#10-roles--rbac)
11. [Day 1 / Day 7 / Day 30 rollout plan](#11-day-1--day-7--day-30-rollout-plan)
12. [Compliance posture — SOC 2, EU AI Act, NIST AI RMF, DPDP](#12-compliance-posture)
13. [Pricing](#13-pricing)
14. [What Aegis is NOT yet — be honest with yourself](#14-what-aegis-is-not-yet)
15. [Reporting feedback + getting help](#15-reporting-feedback--getting-help)

---

## 1. What you'll evaluate

We expect you to walk away from this evaluation with a confident answer to four questions:

| Question | Where it gets answered |
|---|---|
| **Does it actually catch a malicious agent action?** | §2 (5-minute tour) and §7 (out-of-the-box catches). You'll fire a dangerous action and see the deny in the live feed within 200 ms. |
| **Will it survive an audit?** | §8 (cryptographic evidence) and §12 (compliance). You'll download an evidence bundle and verify it offline with our open-source CLI — without our cooperation. |
| **Does it fit my stack?** | §5 (integration paths) and §9 (integrations). You'll wire your existing Anthropic key in 90 seconds; Aegis sits between you and Claude with zero code changes. |
| **What's the cost when our credits run out and the bill is real?** | §13 (pricing). |

If at any point during the evaluation a claim in this document does not match what you see live, that is a bug — file it via the template in §15 and we treat it as a Severity 1. We mean that.

---

## 2. Live 5-minute tour — no signup needed

Use this if you want to confirm the product is alive before committing to a signup.

1. Open **https://aegisagent.in** in a fresh tab.
2. On the landing page, click **"Spawn a demo workspace"**. We mint you an anonymous 30-minute workspace with full OWNER role on a sandbox tenant. The workspace is auto-seeded on spawn with 5 named agents (db-copilot, support-bot, devops-agent, finance-bot, sales-research-agent), 60 audit events across 14 days, 2 incidents, 2 shadow policies, 10 identity-graph nodes, and 8 edges — every sidebar surface (Live Feed, Incidents, Shadow Mode, Identity Graph, Forensics, Audit Logs) is populated immediately, so you do not start on empty screens.
3. You land on your dashboard. KPI tiles show the seeded counts immediately; the live tick badge will start incrementing as soon as you fire any new action.
4. Open **Live Feed** in the sidebar. Leave it open.
5. Back on Dashboard, click **"Run sample agent action"** (or go to Advanced → Agent Playground and fire any tool call). Within 200 ms a row appears on Live Feed.
6. Fire a deliberately dangerous one: in the Playground, change the tool to `read_file` with path `/etc/passwd`. The Live Feed row shows up red with `decision=deny`, `policy_id=SEC-PATH-001`, MITRE tactic `TA0009`. Click the row → see the full signed receipt, the signal that fired, and the policy that matched.
7. Click **Kill Switch** in the top-right (red icon). Confirm engage. Try the same `read_file` again — it returns 403 with "Tenant blocked due to security violation" in under 5 seconds. Release the kill switch.
8. Open **Audit Logs**. Every action you just did is recorded with an ed25519 signature.

If all eight steps worked, you've already seen the product do exactly what it claims to do. Your sandbox session expires in 30 minutes; sign up for a real workspace if you want to keep going.

---

## 3. Sign up + your first 10 minutes

1. Click **Sign up** at the top-right or open https://aegisagent.in/signup. Email + password, or Google / Microsoft / Apple SSO (whichever your IdP uses).
2. Clerk handles the auth. You land on the **Onboarding Wizard** — 5 steps:

   | Step | What you do | What we do |
   |---|---|---|
   | Account | Confirm name + work email | Provision your tenant in our identity service |
   | Path | Pick **A** (you control the agent code) or **B** (Anthropic/OpenAI proxy) — §5 explains the difference | Generate the right credential format for your path |
   | SDK install | Copy the `pip install` line | — |
   | First call | Run the snippet from your laptop | We record the decision, mint the receipt, route it through the policy engine |
   | Done | You're on the dashboard | We surface the action in Live Feed within 200 ms |

3. You're now in **shadow mode by default**. That means for 14 days, policies *observe* but do not *deny*. We tell you what we *would have* blocked, but we don't break your traffic while you're tuning. Exit shadow mode in Settings → Workspace whenever you're ready.

You can stop here. Your account is provisioned, the dashboard is yours, and you've put one real decision through the chain.

---

## 4. The four product modules

The left-side navigation is organized into four product modules, intentionally so that a first-time CISO or CTO can find what they need without docs.

### Observe — what your AI did

This is the surface a CIO opens daily. Five pages:

- **Dashboard** — the headline numbers. Protected agents, actions evaluated, allowed, denied, escalated, active findings. A 30-day window by default. The pulse on "Escalated" means approvals are waiting on you.
- **Live Feed** — every governance decision in real time. Filterable by event type, employee, model. Within 200 ms of every decision. This is where a SOC analyst lives.
- **Team** — one row per employee key with their AI spend, monthly budget, daily budget, harmful actions caught. Click any row for the per-employee drill.
- **Per-employee profile** — budget bars, 30-day sparkline, last 25 calls with token counts, latency, the signal that fired on denies.
- **Notifications** — every routed notification (incident escalations, quota warnings, key revokes, kill-switch toggles).

### Protect — what got blocked, who approves

The operator surface. Seven pages:

- **Agents** — your AI fleet. Name, provider, risk level, status (ACTIVE / QUARANTINED / TERMINATED), owner, created date. Click any agent for full snapshot.
- **Agent snapshot** — the per-agent overview: tool allowlist, last 50 decisions, behavioral baseline + drift score, MITRE tactics that have fired against it. Tabs for Overview, Tools, Decisions, Blast Radius.
- **Incidents** — your SOC queue. Severity (sev-0 to sev-3), assignee, opened-at. Click for the triage view.
- **Approval Inbox** — pending high-risk actions waiting for a human to approve. Each row shows the matched pattern, the prompt excerpt, the employee who triggered it. Approve / Reject with a reason in two clicks. The SDK call that was blocked auto-replays on approve.
- **Policies** — the Rego policy registry. Tabs for Editor (write policies), Simulator (replay against the last 1,000 decisions), Staging (run in shadow), Analytics (which policy fires how often).
- **Kill Switch** — workspace-wide halt. Engage with a reason → every action your tenant tries returns 403 within 5 seconds. Audit row records actor + reason; non-repudiable.
- **Auto-Response** — auto-response rules. Example: "if 50 fails in 5 minutes → quarantine agent + page on-call Slack."

### Prove — cryptographic evidence

The compliance surface. Four pages:

- **Compliance** — framework picker (SOC 2 / EU AI Act / NIST AI RMF / India DPDP). One-click bundle export (PDF or JSON). Per-control evidence row counts. Every control links to the AEVF (Aegis Evidence Verification Format) reference for offline verification.
- **Audit Logs** — the filterable chain. Per row: tenant, agent, tool, decision, risk, findings, policy ID, timestamp. Drill into receipt → see the ed25519 signature, previous hash, chain sequence.
- **Trust Center** — your public-facing trust page. Hash of latest Merkle root, signing-key fingerprint, SOC 2 status, sub-processor list, security.txt link. No auth required — your prospects and auditors can read it.
- **Status** — operational status. 13/13 services operational, p95 latency, queue depth, kill-switch state, recent incidents. Public.

### Workspace — config + admin

The admin surface. Eleven pages:

- **Settings** — hub for everything below.
- **SSO** — wire your IdP (Okta / Azure AD / Google Workspace / generic OIDC).
- **User Management** — invite, role-assign, revoke.
- **RBAC** — visual matrix of who can do what (18 capabilities × 6 roles). Read-only; source of truth is the code.
- **Quota Management** — per-tenant rate caps, per-agent daily/monthly USD caps.
- **Billing** — plan tier, today's spend, monthly forecast, invoices.
- **SIEM** — pick Splunk / Datadog / Elastic / Sentinel / Chronicle, paste creds, test delivery.
- **Webhook Settings** — Slack webhook + signing secret, PagerDuty Routing Key, generic egress webhook.
- **Scheduled Reports** — cron'd evidence exports (weekly SOC 2 PDF emailed to your compliance lead).
- **Developer Panel** — API keys, SDK examples, OpenAPI link, webhook event log.
- **Admin Console** — root-only cross-tenant operations. You won't see this unless you're Aegis staff.

A collapsible **Advanced** group at the bottom of the sidebar exposes 13 analyst surfaces: Forensics, Identity Graph, Threat Graph, Flight Recorder, Decision Explorer, Session Explorer, Fleet, Evaluation, Shadow Mode, Playbooks, Threat Intel, Agent Playground, System Health. All optional, all tenant-isolated.

---

## 5. Wire up your first agent

You pick a path during signup. You can change later, and the two paths can coexist.

### Path A — Aegis-as-a-firewall (you control the agent code)

You wrap each tool call in `aegis.evaluate(tool, args)`. We decide allow / deny / escalate before the tool runs. You decide whether to honor our verdict (you almost always do).

This is the right path when:
- You wrote the agent yourself
- You can change the agent code
- You want maximum control over what gets passed to Aegis

The SDK is one `pip install` and four lines of code:

```python
pip install 'aegis-anthropic==1.1.3'   # or aegis-openai==1.1.3 (also: pip install openai)
                                       # or aegis-langchain==1.1.4
                                       # or aegis-bedrock==1.1.4 (also: pip install 'aegis-bedrock[bedrock]')
```

Then in your agent:

```python
from aegis_anthropic import AegisAnthropic
client = AegisAnthropic(api_key="acp_...")  # the Aegis key, not your Anthropic key
response = client.messages.create(...)       # behaves like Anthropic, just governed
```

The wizard mints you an `acp_...` key during onboarding. The latest PyPI release ships four SDKs — `aegis-anthropic==1.1.3`, `aegis-openai==1.1.3`, `aegis-langchain==1.1.4`, `aegis-bedrock==1.1.4` — all defaulting to the consolidated `https://aegisagent.in` gateway via the `gateway_url=` constructor kwarg (deprecated alias `aegis_url=` still accepted for backwards compatibility on `aegis-anthropic` and `aegis-bedrock`). `aegis-openai` requires `pip install openai` separately, and `aegis-bedrock`'s `AegisBedrockAgentRuntime` requires `pip install 'aegis-bedrock[bedrock]'` for the boto3 dependency. A separate `aegis-aevf==1.1.1` package ships our offline verifier as an audit-only install (`pip install aegis-aevf` → `aegis-verify --bundle <file>`).

### Path B — Aegis-as-a-proxy (zero code changes)

You point your existing Anthropic or OpenAI integration at `https://aegisagent.in/v1/messages` (or `/v1/chat/completions`) and use an Aegis-minted `acp_emp_...` key in place of your provider key. Aegis forwards the call to the real upstream, intercepts the tool calls, and applies governance. Your code doesn't know we're there.

This is the right path when:
- You can't modify the agent code (vendor agent, closed-source tooling, Claude Code, OpenHands, Cursor)
- You want zero rollout risk — a one-line URL change at the SDK config layer
- You have multiple teams using AI and you want centralized governance with employee-level attribution

For Path B you mint one `acp_emp_...` key per employee (Settings → Developer Panel → API keys). Token usage, spend, harmful-action counts roll up to that employee on the Team page.

### Which one should I pick first?

If you're a 50-person engineering org and you want governance over Cursor and Claude Code without asking 50 engineers to change anything: **start with Path B**. You'll have data in 10 minutes.

If you're building your own production AI agent and you want the policy engine deeply embedded: **start with Path A**. You'll have more control and a smaller blast radius.

Both can coexist. Many customers run Path B for general AI use and Path A for their flagship production agent.

---

## 6. Page-by-page tour of every screen

This is the section to hand to your QA team or evaluators so they can systematically walk every screen. Allow 45 minutes for the full sweep. If anything on this list does not match what you see live, it is a bug — file it via §15.

> The full step-by-step QA playbook with screenshots-to-take, viewport checks at 1366×768 and 1920×1080, demo-traffic snippets, failure-injection tests, and the feedback template lives at the end of this document in **Appendix A**. Most evaluators don't need that level — the table below is enough.

| Module | Page | What to look for |
|---|---|---|
| Observe | Dashboard | 6 KPI tiles, live event tick badge on the right, recent activity list, provider mix, risk tier breakdown |
| Observe | Live Feed | Green "Live" pill top-right, throughput sparkline, 15 distinct event types with color-coded pills |
| Observe | Notifications | SSE-driven (no refresh needed), dedupes, persists read state across reload |
| Observe | Team | Per-employee budgets, harmful actions count, live-activity green dot |
| Protect | Agents | Risk score, status, owner email; click any row to drill |
| Protect | Agent snapshot | Tabs: Overview / Tools / Decisions / Blast Radius |
| Protect | Incidents | Severity-color-sorted queue, status filters update URL query |
| Protect | Approval Inbox | "Trigger sample ESCALATE" button → row appears → click Approve → confirm dialog |
| Protect | Kill Switch | "Last engaged: never" idle copy; engage → ConfirmDialog danger variant; live banner on top |
| Protect | Policies | Builder, Simulator, Playground, Staging, Analytics tabs |
| Prove | Compliance | Framework picker (SOC 2 / EU AI Act / NIST AI RMF / DPDP); per-control evidence counts |
| Prove | Audit Logs | Tail-follows in real time as you fire decisions; click any row for the signed receipt modal |
| Prove | Trust Center | 10 capability cards, all linking to GitHub for the code citation |
| Prove | Status | Live ops status, p95 latency, queue depth, kill-switch state |
| Workspace | Settings | Tabs for SSO, Users, RBAC, Quota, Billing, SIEM, Webhooks, Scheduled, Developer, Admin |
| Workspace | Developer Panel | API keys, "Create key" inline form, OpenAPI link |
| Advanced | Threat Graph | Force-directed canvas; demo workspaces see "feature gated to paid tier" empty state |
| Advanced | Identity Graph | Agent ↔ tool ↔ system relationships, blast-radius simulator |
| Advanced | Flight Recorder | Per-call execution timelines, step-by-step playback |
| Advanced | Decision Explorer | Tree view of one decision — every signal, every rule fired |

**Responsive design:** every page is tested at 1366×768 (typical exec laptop) and 1920×1080 (external monitor). No page overflows horizontally. Sidebars collapse, tab bars become horizontally scrollable, force-directed graphs scale to container.

---

## 7. What Aegis catches out of the box

These all work the moment you sign up — no policy authoring required. The corpus of patterns we ship with covers the OWASP LLM Top-10, MITRE ATT&CK for agents, and the catalog of high-risk financial / data / infrastructure actions.

| Category | Examples | Default verdict |
|---|---|---|
| Filesystem path traversal | `read_file('/etc/passwd')`, `/root/.aws/credentials`, `/.ssh/id_rsa` | **deny** |
| Destructive shell | `rm -rf /`, `sudo dd if=/dev/zero of=/dev/sda`, `chmod 777 /` | **deny** |
| SQL injection / mass extraction | `DROP TABLE users`, `SELECT * FROM users WHERE 1=1`, queries without `LIMIT` over 10k rows | **deny** / **escalate** |
| Wire transfers above threshold | `wire_transfer(amount_usd>=$100,000, recipient=external)` | **escalate** (CFO approval required) |
| Kubernetes destruction | `kubectl delete namespace prod`, `kubectl drain prod-node-*` | **deny** |
| Terraform destruction | `terraform destroy` against `env=prod` | **deny** |
| Cross-tenant access | any query that crosses tenant_id boundaries | **deny** (hard block, defense-in-depth) |
| Mass PII extraction | bulk SELECT against PII tables without WHERE | **escalate** |
| Email exfil patterns | `sendmail` / `aws s3 cp` / `curl --data-binary` of files containing PII | **deny** |
| Agent behavior drift | an agent suddenly calling tools it has never called before | **escalate** + per-agent baseline alert |
| Cumulative risk threshold | per-session risk score crosses 95 (tier-95) | **deny** (regardless of individual action risk) |
| Inference cost runaway | per-employee daily AI spend > cap | **block** with structured 429 |

For everything else, you author policies in OPA Rego. Aegis ships a Policy Builder with templates for the four most-common patterns; the Policy Simulator replays your draft against the last 1,000 decisions before you push it live.

---

## 8. Cryptographic evidence

Every decision Aegis records is signed with ed25519. Every day at 00:00 UTC we compute a Merkle root over that day's signed records and publish the root + signature to a public S3 bucket. The roots form an append-only chain — yesterday's root hash is included in today's root.

**Why this matters:** an auditor (or a paying customer) can verify months of audit log history *offline* — without trusting our DNS, our load balancer, or our application code. The reference implementation is **open source**: `pip install aegis-aevf` installs an `aegis-verify` CLI that takes an evidence bundle and runs six independent integrity checks:

| Check | What it proves |
|---|---|
| V1 — Bundle format | The bundle is a well-formed Aegis evidence bundle (AEVF spec aevf/0.1.0) |
| V2 — Event hash recompute | Every row's canonical hash matches the recorded hash |
| V3 — Per-shard chain | Each shard's `prev_hash` chain is unbroken |
| V4 — Root signatures | Each Merkle root's ed25519 signature verifies against the published key |
| V5 — Root chain | Today's root references yesterday's root hash (no holes) |
| V6 — Retention metadata | Retention claims match the per-record metadata |

If any of V1–V6 fails, the verifier exits non-zero and tells you exactly which row, which shard, which root broke the chain. **You can run this against our evidence on your laptop with no network calls.** That's the whole point: the chain is self-verifying. We can't tamper with it after the fact without breaking V4/V5 on every prior root, which a customer who archived an earlier root will notice immediately.

The signing keys are stored in AWS KMS (customer-managed CMK), rotated quarterly, with the historical keys preserved in `transparency_historical_keys` so old receipts still verify after rotation.

---

## 9. Integrations

You don't need any of these to use Aegis. The platform works out of the box with email-only Clerk auth. Wire integrations in as you scale.

| Integration | What it does | Where to configure |
|---|---|---|
| **SSO** (Okta / Azure AD / Google / generic OIDC) | Single sign-on for your team; SCIM auto-provisioning | Settings → SSO |
| **Slack** | Approval requests delivered as messages with HMAC-signed buttons; you click ✅ / ❌ inside Slack | Settings → Webhook Settings → Slack |
| **PagerDuty** | Sev-0/1 incidents page on-call | Settings → Webhook Settings → PagerDuty |
| **SIEM** — Splunk HEC / Datadog Logs / Elastic ECS / Microsoft Sentinel / Chronicle | Every decision streamed to your SIEM with full envelope | Settings → SIEM |
| **Jira / ServiceNow** | Auto-create ITSM tickets for incidents and approvals | Settings → Integrations |
| **Generic egress webhook** | For anything else — HMAC-signed POST to a URL you provide | Settings → Webhook Settings |
| **Stripe** | Self-serve billing | Settings → Billing |

Webhook signatures use Svix (Clerk-style) or HMAC-SHA256 depending on the destination. We constant-time compare and we surface a per-webhook delivery log so you can debug retries without contacting us.

---

## 10. Roles + RBAC

Aegis ships six roles. Every authenticated route is mapped to an allowed role set; there are 18 enforceable capabilities. The matrix is rendered live in the UI (Settings → RBAC) so you can hover any cell to see the enforcing code path.

| Role | Sees | Can change | Typical owner |
|---|---|---|---|
| **OWNER** | Everything | Everything including billing + workspace deletion | Founder / CISO |
| **ADMIN** | Everything | Everything except billing | Head of security ops |
| **SECURITY_ANALYST** | Everything | Policies, approvals, incidents | SOC analyst |
| **AUDITOR** | Read-only — every screen | Nothing | External auditor, compliance lead |
| **OPERATOR** | Operational pages | Agents, kill switch, approvals | On-call engineer |
| **AGENT** | API key calling /execute only | N/A | Service account for your agent |

A `DEVELOPER` token cannot call `/compliance/export`. An `AUDITOR` token can read everything and modify nothing. Cross-tenant access is structurally impossible — every SQL query carries `WHERE tenant_id = $1`, and we ran a 7-attack isolation pentest with 0 bypasses (the script is in our public GitHub).

---

## 11. Day 1 / Day 7 / Day 30 rollout plan

The rollout pattern we've seen work across our design partners.

### Day 1 — proof of life
- Sign up, complete onboarding wizard, mint one Path B employee key
- Point your most-used Cursor / Claude Code workflow at the proxy
- Open Live Feed in a second monitor; leave it open for the day
- Look for: every action you take in your AI tool shows up in Aegis within 200 ms

### Day 7 — observation
- Mint employee keys for 5–10 colleagues
- Stay in shadow mode (default — policies observe but don't enforce)
- Open Shadow Mode Review daily and ask: "would Aegis have blocked something that would have hurt us?" Usually yes, within the first 48 hours
- Configure SSO so your team logs in with their work identity instead of Clerk default

### Day 30 — enforcement
- Exit shadow mode in Settings → Workspace
- Wire Slack approvals so the CFO gets a Slack message for wire transfers ≥ $100k
- Connect SIEM (Splunk / Datadog / Elastic)
- Run the first month-end Compliance export, hand to your auditor; have them run `aegis-verify` against the bundle on their own laptop
- Set up Scheduled Reports for weekly evidence delivery

### Quarterly
- Rotate signing keys (we run the runbook for you on request)
- Run the monthly DR drill: restore a recent backup into an isolated VPC, verify the audit chain end-to-end
- Review the per-policy Analytics page; sunset policies that haven't fired in 90 days

---

## 12. Compliance posture

We are honest about what we have and what we're working toward.

| Framework | Status |
|---|---|
| **SOC 2 Type I** | In progress (Q3 2026) |
| **SOC 2 Type II** | Scheduled Q1 2027 |
| **EU AI Act Article 12** (audit-record minimum) | Code-compliant — AEVF spec maps every record to the article's requirements |
| **India DPDP Act Sec. 8(5)** (record retention) | Code-compliant — default 365-day retention with per-tenant override |
| **NIST AI RMF** | Mapped — see AEVF spec |
| **ISO 27001** | Not started; on the roadmap behind SOC 2 Type II |
| **HIPAA** | BAA template available on request; not yet certified |
| **PCI-DSS** | Not in scope (we don't touch payment card data) |

For auditors: every claim above has a citation. The Trust Center at https://aegisagent.in/trust has the source links. You can verify our audit chain end-to-end without us in the room.

---

## 13. Pricing

Built for seed-stage budgets and scales with usage.

| Tier | Price | What you get |
|---|---|---|
| **Free** | $0 | 1 workspace, 1k actions/month, 30-day retention, Clerk auth, email support |
| **Pro** | $49/mo per workspace | 50k actions/month, 365-day retention, SSO, Slack/SIEM, weekly evidence exports, 4h SLA on Sev-1 |
| **Enterprise** | Custom | Unlimited actions, multi-region, dedicated tenant, BAA + DPA, named SRE on call, custom Rego authoring support, white-glove onboarding |

We bill by signed audit row, not by AI tokens — your AI provider already charges you for tokens. We won't double-charge.

There are no setup fees, no minimums on Pro, and you can cancel at any time from Settings → Billing.

---

## 14. What Aegis is NOT yet

Honest list. We'd rather lose a deal on Day 1 over a missing feature than land it and lose it on Day 60.

- **No on-prem deployment.** SaaS only on `ap-south-1` (default) and `eu-west-1` (paid contract). Self-hosted is roadmap, not committed.
- **No multi-region active-active.** Multi-AZ within a region, yes. Active-active across regions, no — that's a Q2 2027 item.
- **No SOC 2 Type II yet.** Type I in progress. If your procurement requires Type II today, we are not the right fit yet.
- **No on-the-fly model fine-tuning suppression.** We govern tool calls and outputs, not the model weights themselves.
- **No automatic incident remediation beyond quarantine.** We block, we alert, we open a ticket. We don't auto-revert your infra. That's deliberate — we don't trust ourselves enough to do that yet.
- **No iOS / Android app.** Web only.

If any of the above is a blocker for you, tell us. We'd rather know now.

---

## 15. Reporting feedback + getting help

We treat every Sev-1 finding from an evaluator as if it were from a paying customer. If a claim in this document does not match what you see live, we want to know within minutes.

### How to file feedback

Use this template — one finding per submission:

```
TITLE: <one-line summary>

SEVERITY: 1 / 2 / 3
  1 = white screen, console error, broken auth, data loss risk, claim in this doc is false
  2 = wrong copy, missing CTA, visible layout bug, real-time feature not ticking
  3 = polish nit, wording, color contrast

URL: https://aegisagent.in/<path>
VIEWPORT: 1366×768 / 1920×1080 / other
BROWSER: Chrome 120 / Edge 120 / Safari 17
SIGNED-IN AS: <your-email> OR anonymous demo workspace
TIMESTAMP: 2026-06-23 14:32 IST

REPRO STEPS:
1. …
2. …
3. …

EXPECTED: <one sentence>
ACTUAL: <one sentence>
CONSOLE OUTPUT (paste any red lines):
SCREENSHOT: <link or attachment>
```

### Where to send

- **Fastest** — email the founder directly (you have the address).
- **Best** — open a GitHub issue at https://github.com/Abhi-mishra998/aegis/issues/new with label `evaluator-feedback`.
- **Bulk** — paste the lot into a Google Doc and share the link.

### What we commit to

| Severity | Acknowledge | Fix or workaround |
|---|---|---|
| **1** | 4 working hours | 1 business day |
| **2** | 1 business day | Next scheduled deploy (typically same week) |
| **3** | 1 business day | Tracked in public backlog |

Every Sev-1 we receive shows up in the Trust Center incident history within 24 hours of resolution.

---

## Appendix A — Full QA playbook (for your testing team)

Use this section as a script if you have a dedicated tester on the evaluation. A single tester completes the full sweep in **45–60 minutes**.

### A.0 Prerequisites

- **Browser:** Chrome 120+ or Edge 120+ on macOS or Windows. (Safari works but we test less aggressively.)
- **Viewports to check:** 1366×768 (typical exec laptop) and 1920×1080 (external monitor). Chrome DevTools → Toggle device toolbar (⌘+Shift+M / Ctrl+Shift+M) → enter dimensions.
- **Screenshot tool:** macOS ⌘+Shift+4 (region) or Windows Snipping Tool (Win+Shift+S). Save to a folder named `aegis-feedback-<your-name>`.
- **DevTools open during walk:** Hit `F12`. Keep the **Console** tab visible — any **red** entry = bug to file. Yellow warnings are usually fine (React StrictMode, third-party SDK noise).
- **Test workspace:** sign up at https://aegisagent.in/signup OR use the anonymous "Spawn demo workspace" button on the landing page for a 30-minute sandbox.

### A.1 Walk 1 — Public surface (anonymous, ~5 min)

These pages must work without any credential. They are what a prospect, auditor, or pentester sees first.

| Step | URL | Expected |
|---|---|---|
| 1 | `/` | Hero loads, "Spawn demo workspace" button works (shows loading spinner, returns magic link or error toast — does NOT silently fail) |
| 2 | `/login` | Clerk SignIn renders; email field focusable |
| 3 | `/signup` | Clerk SignUp renders; no console error |
| 4 | `/trust` | 10 capability cards (Tenant isolation, Encryption, Crypto transparency, RBAC, Supply chain, Ops monitoring, DR, Subprocessors, Data residency, Reference architectures) — all with icons + outbound GitHub links |
| 5 | `/status` | Either "All systems operational" OR friendly "Nightly artefact not yet published — for live state visit /system/health" — **NOT** a red error banner |
| 6 | `/security` | Responsible disclosure: 48h ack / 5d triage / 90d fix SLOs, in-scope + out-of-scope lists, PGP key link |
| 7 | `/.well-known/security.txt` | Plain text with Contact, Encryption, Policy, Expires fields |

**Red flag:** if any page above shows a white screen, a stack trace, or HTTP 500, screenshot the Console tab and file it as **Severity 1**.

### A.2 Walk 2 — Onboarding wizard (5 min, first-time only)

| Check | Expected |
|---|---|
| Step indicator | 5 steps across top (Account → Path → SDK → First call → Done). Narrow screen collapses to dots; wide shows full labels |
| Path A snippet | Code in monospace, copy-button works, no bare spinner during generation (skeleton instead) |
| Path B snippet | Different snippet for the proxy URL setup |
| Skip button | Routes to /dashboard cleanly |

### A.3 Walk 3 — Observe module (10 min)

Open a second terminal so you can fire demo traffic while watching the UI tick.

#### Dashboard `/dashboard`

| Check | Expected |
|---|---|
| KPI tiles | 6 in a row: Protected Agents, Actions Evaluated, Allowed, Denied, Escalated, Active Findings |
| Loading | Skeleton shimmer (NOT "0" that looks broken) |
| Empty state | "No activity yet — start onboarding wizard" CTA card |
| Provider mix empty | "Onboard your first agent" CTA |
| Risk tier empty | "No risk tiers yet…" with onboarding CTA |
| Recent activity empty | "Onboard agent" + "Open live feed" CTAs |
| Responsive 1366×768 | KPI row collapses to `grid-cols-2 sm:3 lg:3 xl:6`, no horizontal overflow |
| Real-time tick | When you fire `/execute` in your terminal, the "Live · N events" badge ticks within 200ms |

#### Live Feed `/live-feed`

| Check | Expected |
|---|---|
| Layout | `max-w-7xl` centered, fills both 1366 and 1920 |
| Connection badge | "Live" green / "Connecting" amber / "Disconnected — reason" red |
| Throughput gauge | 12-bar sparkline |
| Empty + connected | "Live stream open — no events yet. Trigger via /agents or /playground." with both CTAs |
| Empty + disconnected | "Disconnected — Reconnect" button |
| Empty + filtered | "No matching events — Reset filters" |
| Real-time | New rows appear at the top **without refresh** when you fire `/execute` |
| Row content | Icon, event type pill, employee email, model chip (single), risk score, "X seconds ago" |
| Pause button | Stops new rows from prepending; resume catches up |
| Clear button | Wipes visible list; SSE continues |

#### Notifications `/notifications`

| Check | Expected |
|---|---|
| SSE pill | Live / Connecting / Offline indicator |
| Empty (never) | "No notifications yet — open Live Feed or Register agent" with CTAs |
| Empty (filter on) | "No unread — Show all" button |
| Mark read persistence | Click unread → goes read. Reload page → still read (localStorage cache) |
| Real-time | New alert row within ~1s when /execute denies |

#### Team `/team` and `/team/<email>`

| Check | Expected |
|---|---|
| Empty | EmptyState pointing at /team/invite and /settings/sso |
| Loaded | Per-employee row: 30-day spend, daily/monthly budget bars, harmful actions count |
| Live ping | Small green dot next to row when employee fires /execute (60s window) |
| Profile drill | 30-day sparkline, last 25 calls, signal that fired on each deny |

#### Demo-traffic snippet

Use this in your second terminal — replace JWT with your demo workspace token:

```bash
JWT="eyJ…"   # from /demo/spawn-workspace

# ALLOW
curl -sS -X POST https://aegisagent.in/execute \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -A "Mozilla/5.0 Chrome/120" \
  -d '{"tool":"http_get","args":{"url":"https://example.com"}}'

# DENY
curl -sS -X POST https://aegisagent.in/execute \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -A "Mozilla/5.0 Chrome/120" \
  -d '{"tool":"read_file","args":{"path":"/etc/passwd"}}'

# ESCALATE
curl -sS -X POST https://aegisagent.in/execute \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -A "Mozilla/5.0 Chrome/120" \
  -d '{"tool":"wire_transfer","args":{"amount_usd":250000,"recipient":"external"}}'
```

Each call should produce a visible new row on Live Feed, Dashboard, and Notifications within 200 ms.

### A.4 Walk 4 — Protect module (10 min)

| Page | Check |
|---|---|
| Agents | Empty: "No agents registered — go to /onboarding" CTA. Loaded: name, provider, risk_score, status, owner email. Click any row → /agents/<id> snapshot with 4 tabs |
| AgentTopology | Force-directed canvas sized to container, ResizeObserver, no overflow at either viewport |
| AgentHealth | Green for ACTIVE, red for QUARANTINED. SSE-driven — quarantine flips dot within 1s |
| AgentCost | Per-agent USD chart, skeleton during load, "—" with hint for zero (not bare "0") |
| Incidents | Empty: "No incidents — system healthy" + /dashboard link. Severity colors: sev-0 red, sev-1 orange, sev-2 amber, sev-3 green |
| ApprovalInbox | Empty: "No pending approvals — appears here when agent triggers ESCALATE" + **Trigger sample ESCALATE** button. After trigger: row appears within ~2s with matched pattern, prompt excerpt, employee email, Approve/Reject buttons |
| KillSwitch | Idle: "Kill switch idle — last engaged: never". Engage → ConfirmDialog danger variant with reason field. Engaged: ENGAGED red banner + history list. Release → ConfirmDialog default → idle |
| Policies | Tab nav: Editor / Simulator / Playground / Analytics / Staging |
| Policy Builder | Empty: "Start with a template" — 4 buttons (high-risk-deny, destructive-shell, anomaly-monitor, inference-throttle) + Build from scratch + Test in Playground. Split-pane stacks vertically at 1366 |
| Policy Analytics | Empty: "No policy hits yet — generate sample traffic" CTA. Live: counters tick + pulse on policy_decision SSE events |
| Shadow Mode | OFF: "Shadow mode off — toggle ON" CTA. ShadowModeReview: live counter on would_have_blocked events |

### A.5 Walk 5 — Prove module (5 min)

| Page | Check |
|---|---|
| Compliance | Framework picker (SOC 2 / EU AI Act / NIST AI RMF / DPDP). Per-control rows show evidence count (may be 0 for fresh workspace). Empty: "No evidence — click Refresh after agent activity OR export empty-period attestation". Export → PDF or JSON download, may take 5-10s |
| AuditLogs | Empty: "No audit rows — run any agent action via /playground" + sample-trigger. Loaded: 8+ column table, horizontal scroll on narrow (min-w-900px inside max-w-full overflow-x-auto). Tail-follow SSE — new rows prepend without refresh. Click row → modal with ed25519 signature, prev_hash, chain_sequence |
| FlightRecorder | Empty: "Timeline empty — start a session" + /playground CTA. Loaded: per-call step-by-step playback. Replay pane: "Select a timeline to replay" until clicked |
| Forensics | Empty: Fingerprint icon EmptyState, CTAs to /incidents (primary) and /audit-logs (secondary) |
| Evaluation | Empty: "Run nightly corpus" / "Seed OWASP corpus first" depending on datasets state. Click → job runs, status moves running → done |

### A.6 Walk 6 — Workspace module (5 min)

| Page | Check |
|---|---|
| Settings | Tab router — 7 tabs across top (or left rail on wide screens); on narrow, `overflow-x-auto` so all stay reachable |
| SSO | Not configured: "SSO not configured — use email/Clerk or upload SAML metadata" with anchored form |
| Users | Empty: "No users — first via /signup or SCIM auto-provisions on next login" |
| RBAC | 18×6 matrix (capabilities × roles); legend below |
| Quota | RPS + burst + daily + monthly numbers; free-tier CTA to /billing |
| Billing | Empty usage: "No usage yet — $0.00 — try sample traffic" + CTA to api-keys |
| SIEM | Not connected: tile picker (Splunk HEC / Datadog Logs / Elastic ECS / Sentinel / Chronicle); tiles have anchor link into input row |
| Webhooks | Empty: "No webhooks — add one to forward events" with Add button |
| Developer | No API keys: full empty-state card with dashed border, icon, "Create API key" primary CTA opening inline form |
| Admin | OWNER-only: 403/EmptyState if not ROOT; cross-tenant ops if ROOT |

### A.7 Walk 7 — Advanced analyst surfaces (5 min — paid-tier mostly)

| Page | Expected on demo workspace |
|---|---|
| ThreatGraph | "Graph empty — generated from incident clusters. Trigger sample" CTAs. ReactFlow canvas sized to container at both viewports |
| ThreatIntel | "No IOCs ingested — connect a feed via /settings/integrations" |
| DecisionExplorer | EmptyState with CTAs to Playground / Live Demo / Flight Recorder. Live tick badge on policy_decision events |
| SessionExplorer | EmptyState with Users icon + CTAs to /onboarding + /playground |
| Replay | Skeleton stage cards during load → empty "No timelines to replay — open from /flight-recorder" |
| IdentityGraph | Empty graph EmptyState + CTAs to /agents and /onboarding. Force-directed re-layouts on container resize |
| Fleet | Empty fleet EmptyState + skeleton KPI grid + chart skeleton during load |
| AgentPlayground | No agents: amber CTA card (not red error) pointing at /onboarding |

### A.8 Walk 8 — Responsive sweep (3 min, must pass)

DevTools → Toggle device toolbar. Set **1366×768**. Walk every sidebar page once. **No page should:**

- Overflow horizontally (no body-level horizontal scrollbar — table-level horizontal scroll is fine)
- Show a tab bar half-cut off (Settings, Policies, Forensics)
- Show a force-directed graph taller than the viewport
- Show a modal wider than the viewport

Then **1920×1080**. Walk again. Same checks. KPI rows expand from `grid-cols-3` (lg) to `grid-cols-6` (xl) on Dashboard.

### A.9 Walk 9 — Failure injection (5 min)

Verify the UI tells the truth when things go wrong.

| Inject | Where | Expected |
|---|---|---|
| Disconnect Wi-Fi 5s | LiveFeed | Connection badge: amber "Connecting" → red "Disconnected — network error". Reconnect on Wi-Fi return |
| Expire JWT (wait 30 min) | Any auth'd page | 401 → routes to /login + toast "Session expired — please sign in" |
| Engage Kill Switch then /agents | /agents | All actions show 403; "Workspace halted" banner at top |
| Run 100 deny `/execute` in 30s | Notifications | Debounced; aggregated count, NOT 100 stacked toasts |

---

## Closing — what we're asking from you

You're evaluating a young product. The site, the SDK, the CLI verifier — they all work today, live, and you can verify every claim above without us in the room. The team is small. The roadmap is honest. The credits we run on today come from AWS Activate; the pricing column in §13 is what becomes real when those credits expire in Q1 2027.

If you decide Aegis is wrong for you, we'd love a one-line note on why — that's how we get better. If you decide it's right, we'd love to be your governance plane, and we'll show up the same way every Sev-1 customer gets shown up for: fast, honest, in writing.

— The Aegis team
