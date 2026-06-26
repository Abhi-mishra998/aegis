# Aegis — End-to-End Guide

**For:** the person evaluating Aegis, signing it up, configuring it, and rolling it out across a company.
**Time to read:** 25 minutes.
**Time from this page → first decision audited:** 10 minutes.
**Site:** https://aegisagent.in

This is the single canonical guide. It walks the buyer journey end-to-end —
evaluate without signing up, sign up, wire one agent, configure the
workspace, roll out to the team, exit shadow mode, hand evidence to an
auditor. Every claim in this document is live on `aegisagent.in`; you can
verify it yourself before reading on.

> Replaces the previous two-file split (`setup-agies.md` evaluator tour +
> `ui-setup.md` adoption flow). One file, one path.

---

## Table of contents

**Part I — Understand**
1. [What Aegis actually does](#1-what-aegis-actually-does)
2. [How the decision pipeline works](#2-how-the-decision-pipeline-works)
3. [What Aegis catches out of the box](#3-what-aegis-catches-out-of-the-box)

**Part II — Evaluate (no signup)**
4. [5-minute tour — no signup needed](#4-5-minute-tour--no-signup-needed)

**Part III — Sign up + first 10 minutes**
5. [Sign up + workspace creation](#5-sign-up--workspace-creation)
6. [Path A vs Path B — which integration to pick](#6-path-a-vs-path-b)

**Part IV — Wire your first agent (Day 2 — 30 min)**
7. [Register the agent](#7-register-the-agent)
8. [Install the SDK](#8-install-the-sdk)
9. [Swap the constructor (4 SDKs)](#9-swap-the-constructor)
10. [Run + watch Live Feed](#10-run--watch-live-feed)

**Part V — Configure your workspace (Day 1 — 15 min)**
11. [Workspace identity + compliance pack](#11-workspace-identity--compliance-pack)
12. [SSO + SCIM](#12-sso--scim)
13. [Slack approvals + on-call](#13-slack-approvals--on-call)
14. [Webhooks, SIEM, ITSM](#14-webhooks-siem-itsm)
15. [API keys](#15-api-keys)
16. [Roles + RBAC](#16-roles--rbac)

**Part VI — Roll out (Day 4–5 — 1 hour)**
17. [Add employees + per-seat budgets](#17-add-employees--per-seat-budgets)
18. [Pick compliance pack](#18-pick-compliance-pack)
19. [Shadow Mode → Enforce](#19-shadow-mode--enforce)

**Part VII — Operate (Day 6+)**
20. [Live incident response](#20-live-incident-response)
21. [Auto-response playbooks](#21-auto-response-playbooks)
22. [Kill switch](#22-kill-switch)

**Part VIII — Prove (Day 7 — auditor handoff)**
23. [Cryptographic chain (ed25519 + Merkle)](#23-cryptographic-chain)
24. [Evidence bundle export](#24-evidence-bundle-export)
25. [Offline verification with `aegis-verify`](#25-offline-verification)

**Part IX — Reference**
26. [Every page in the product](#26-every-page-in-the-product)
27. [The four product modules](#27-the-four-product-modules)
28. [Integrations matrix](#28-integrations-matrix)
29. [Out-of-the-box catches](#29-out-of-the-box-catches)

**Part X — Plan**
30. [Day 1 / Day 7 / Day 30 rollout plan](#30-day-1--day-7--day-30-rollout-plan)
31. [SDK + version management](#31-sdk--version-management)
32. [QA test plan a buyer's security team runs](#32-qa-test-plan)
33. [Pricing](#33-pricing)
34. [Compliance posture](#34-compliance-posture)
35. [What Aegis is NOT yet — honest list](#35-what-aegis-is-not-yet)
36. [Common questions](#36-common-questions)

**Part XI — Help**
37. [Feedback + support](#37-feedback--support)

---

# Part I — Understand

## 1. What Aegis actually does

Your business is starting to use AI agents — Claude, GPT, Bedrock,
in-house tools — to take real actions on real production systems. The
question your board is going to ask, sooner than you think, is *"what
happens when one of them does something it shouldn't?"*

Aegis is the layer that catches that.

Every tool call your AI agent attempts — every SQL query, every email,
every wire transfer, every file delete — passes through a 10-stage policy
engine whose inter-service round-trip is **p95 ≈ 28 ms**
(`/system/health.latency`); the user-facing `/execute` round-trip from
your agent depends on your network distance to ap-south-1. Both numbers
are published on `/status` so you can verify them yourself.

We decide **allow / monitor / escalate / deny / quarantine**, sign the
decision with **ed25519**, and publish the daily Merkle root to a public
S3 bucket you can verify offline without trusting us.

- **You** get the receipts.
- **Your auditor** gets the chain.
- **Your CFO** gets the kill switch.

We are not another LLM. We are not a chat product. We are the governance
plane that sits between your AI and the systems your AI can touch.

---

## 2. How the decision pipeline works

```
   ┌──────────────┐    1. tool call             ┌─────────────────────┐
   │  Your Agent  │ ──────────────────────────▶ │  Aegis Gateway      │
   │  (Path A or  │                              │  (aegisagent.in)    │
   │   Path B)    │ ◀──── 5. allow/deny/         │                     │
   └──────────────┘       escalate decision      │  10-stage pipeline: │
                                                  │   1. AuthN/AuthZ    │
                                                  │   2. Rate limit     │
                                                  │   3. Quota          │
                                                  │   4. Tenant isolate │
                                                  │   5. Action norm    │
                                                  │   6. Signal eval    │
                                                  │   7. OPA Rego       │
                                                  │   8. Cumulative risk│
                                                  │   9. Behavior drift │
                                                  │  10. Audit chain    │
                                                  └─────────────────────┘
                                                            │
                                                            ▼
                                                   ed25519-signed audit
                                                   + daily Merkle root
                                                   (mirrored to public S3)
```

Decisions return in **one of five verdicts**:

| Verdict | Meaning | Typical use |
|---|---|---|
| **allow** | No signal, no policy, no budget threshold tripped | Day-to-day analyst queries |
| **monitor** | Allowed but a signal fired — recorded for tuning | Suspicious-but-not-blocking patterns |
| **escalate** | Sent to a human reviewer (Slack/Approval Inbox) | Money movement, prod actions, mass-PII |
| **deny** | Hard block — Aegis refused before action ran | Path traversal, SQLi, prod destruction |
| **quarantine** | Agent locked out for N minutes | Cumulative risk crossed threshold |

---

## 3. What Aegis catches out of the box

These all work the moment you sign up — no policy authoring required. The
ship corpus covers OWASP LLM Top-10, MITRE ATT&CK for agents, and the
catalog of high-risk financial / data / infrastructure actions.

| Category | Examples | Default verdict |
|---|---|---|
| Filesystem path traversal | `read_file('/etc/passwd')`, `/root/.aws/credentials`, `/.ssh/id_rsa` | **deny** |
| Destructive shell | `rm -rf /`, `sudo dd if=/dev/zero of=/dev/sda`, `chmod 777 /` | **deny** |
| SQL injection / mass extraction | `DROP TABLE users`, `SELECT * FROM users WHERE 1=1`, no `LIMIT` over 10k rows | **deny** / **escalate** |
| Wire transfers above threshold | `wire_transfer(amount_usd>=$100,000, recipient=external)` | **escalate** (CFO approval) |
| Kubernetes destruction | `kubectl delete namespace prod`, `kubectl drain prod-node-*` | **deny** |
| Terraform destruction | `terraform destroy` against `env=prod` | **deny** |
| Cross-tenant access | any query crossing tenant_id boundaries | **deny** (hard block) |
| Mass PII extraction | bulk SELECT against PII tables without WHERE | **escalate** |
| Email exfil patterns | `sendmail` / `aws s3 cp` / `curl --data-binary` of files containing PII | **deny** |
| Agent behavior drift | agent suddenly calling tools it has never called before | **escalate** + baseline alert |
| Cumulative risk threshold | per-session risk score crosses 95 (tier-95) | **deny** + auto-quarantine |
| Inference cost runaway | per-employee daily AI spend > cap | **block** with structured 429 |

For anything else, you author policies in OPA Rego. Aegis ships a Policy
Builder with templates for the four most-common patterns; the Policy
Simulator replays your draft against the last 1,000 decisions before you
push it live.

---

# Part II — Evaluate (no signup)

## 4. 5-minute tour — no signup needed

Use this if you want to confirm the product is alive before committing
to a signup.

1. Open **https://aegisagent.in** in a fresh tab.
2. Click **"Spawn demo workspace"** on the landing page. We mint you an
   anonymous 30-minute workspace with full OWNER role on a sandbox
   tenant. The workspace is auto-seeded with **5 named agents**
   (db-copilot, support-bot, devops-agent, finance-bot,
   sales-research-agent), **60 audit events** across 14 days,
   **2 incidents**, **2 shadow policies**, **10 identity-graph nodes**,
   and **8 edges** — every sidebar surface is populated immediately so
   you do not start on empty screens.
3. You land on your dashboard. KPI tiles show the seeded counts; the
   live tick badge starts incrementing as soon as you fire any action.
4. Open **Live Feed** in the sidebar. Leave it open in one tab.
5. Back on Dashboard, click **"Run sample agent action"** (or Advanced
   → Agent Playground and fire any tool call). Within 200 ms a row
   appears on Live Feed.
6. Fire a deliberately dangerous one: in the Playground, change the
   tool to `read_file` with path `/etc/passwd`. The Live Feed row
   shows up red with `decision=deny`, `policy_id=SEC-PATH-001`, MITRE
   tactic `TA0009`. Click the row → see the full signed receipt, the
   signal that fired, the policy that matched.
7. Click **Kill Switch** in the top-right (red icon). Confirm. Try the
   same `read_file` again — it returns **403** with "Tenant blocked due
   to security violation" in **under 5 seconds**. Release the switch.
8. Open **Audit Logs**. Every action you just did is recorded with an
   ed25519 signature.

If all eight steps worked, you've already seen the product do exactly
what it claims. Your sandbox session expires in 30 minutes; sign up
for a real workspace if you want to keep going.

---

# Part III — Sign up + first 10 minutes

## 5. Sign up + workspace creation

1. Open **https://aegisagent.in/signup**. Email + password, or Google /
   Microsoft / Apple SSO (whichever your IdP uses).
2. Clerk handles the auth. The gateway provisions a new tenant
   automatically (idempotent — webhook + first-call sync covers either
   ordering), and you land on `/dashboard`.
3. Your tenant starts in **14-day Shadow Mode** by default. Every
   decision is recorded with what the production action WOULD have
   been, but nothing is blocked. You exit shadow mode when you're ready
   (Settings → Workspace → "Exit shadow mode" — see §19).
4. You're the **OWNER**. You can do everything. Add more roles in
   Settings → RBAC (see §16).

You can stop here. Your account is provisioned, the dashboard is yours,
and you've put one real decision through the chain.

---

## 6. Path A vs Path B

You pick a path during signup. You can change later, and the two paths
can coexist.

### Path A — Aegis as a firewall (you control the agent code)

You wrap each tool call in the Aegis SDK constructor. We decide
allow / deny / escalate **before** the tool runs. You decide whether
to honor our verdict (you almost always do).

**The right path when:**
- You wrote the agent yourself
- You can change the agent code
- You want maximum control over what gets passed to Aegis

### Path B — Aegis as a proxy (zero code changes)

You point your existing Anthropic or OpenAI integration at
`https://aegisagent.in/v1/messages` (or `/v1/chat/completions`) and use
an Aegis-minted `acp_emp_...` key in place of your provider key. Aegis
forwards the call upstream, intercepts the tool calls, and applies
governance. Your code doesn't know we're there.

**The right path when:**
- You can't modify the agent code (vendor agent, Claude Code, Cursor, OpenHands)
- You want zero rollout risk — a one-line URL change at the SDK config layer
- You have multiple teams using AI and want centralized governance with
  employee-level attribution

### Which one first?

| Situation | Pick |
|---|---|
| 50-person eng org using Cursor / Claude Code, no code changes possible | **Path B** — data in 10 minutes |
| Building your own production AI agent, want policy engine embedded | **Path A** — more control, smaller blast radius |
| Both | **Both** — Path B for general AI use, Path A for flagship agent |

---

# Part IV — Wire your first agent (Day 2)

This is the 30-minute walk that turns "logged in" into "first agent
governed."

## 7. Register the agent

Sidebar → **Agents** → **"New Agent"** → fill in:

- **Name** (e.g. `support-bot`)
- **Provider** (`anthropic` / `openai` / `bedrock` / `langchain` / `custom`)
- **Risk tier** (`low` / `medium` / `high` / `critical` — drives default budgets)
- **Tool allow-list** (the names of tools this agent is permitted to use)

Click create. The agent UUID + permissions are stored. You'll reference
the UUID in the SDK constructor.

---

## 8. Install the SDK

| Stack | Install (current PyPI as of 2026-06-26) |
|---|---|
| Anthropic Python SDK | `pip install 'aegis-anthropic==1.1.3'` |
| OpenAI Python SDK | `pip install 'aegis-openai==1.1.4'` (also `pip install openai`) |
| LangChain agent | `pip install 'aegis-langchain==1.1.5'` |
| AWS Bedrock Agents | `pip install 'aegis-bedrock[bedrock]==1.1.5'` |
| Offline verifier (audit-only) | `pip install 'aegis-aevf==1.1.1'` → `aegis-verify --bundle <file>` |

> Unpin any of the runtime SDKs (`pip install aegis-anthropic`) and you'll get
> the latest — the wire contract is stable, so any patch / minor that ships
> after this date keeps working with the live gateway.

All four runtime SDKs default to the consolidated `https://aegisagent.in`
gateway via the `gateway_url=` constructor kwarg (deprecated alias
`aegis_url=` still accepted on `aegis-anthropic` and `aegis-bedrock`).

---

## 9. Swap the constructor

The SDK does this with a **single line change** — `base_url=` (or the
constructor name).

### Anthropic

```python
# Before
import anthropic
client = anthropic.Anthropic(api_key="sk-ant-...")

# After
from aegis_anthropic import AegisAnthropic
client = AegisAnthropic(
    api_key="sk-ant-...",                       # your Anthropic key (stays on your machine)
    aegis_key="acp_...",                        # from Settings → API Keys
    aegis_url="https://aegisagent.in",
    tenant_id="<your-tenant-uuid>",             # in Settings → Workspace
    agent_id="<agent-uuid-from-step-7>",
)
```

### OpenAI

```python
from aegis_openai import AegisOpenAI
client = AegisOpenAI(
    openai_api_key="sk-...",
    aegis_key="acp_...",
    aegis_url="https://aegisagent.in",
    tenant_id="<your-tenant-uuid>",
    agent_id="<agent-uuid>",
)
```

### LangChain

```python
from aegis_langchain import AegisMiddleware
agent = AegisMiddleware(
    my_langchain_agent,
    api_key="acp_...",
    aegis_url="https://aegisagent.in",
    tenant_id="<your-tenant-uuid>",
    agent_id="<agent-uuid>",
)
result = agent.invoke({"input": "..."})
```

### Bedrock

```python
from aegis_bedrock import AegisBedrockAgentRuntime
client = AegisBedrockAgentRuntime(
    aegis_key="acp_...",
    aegis_url="https://aegisagent.in",
    tenant_id="<your-tenant-uuid>",
    agent_id="<agent-uuid>",
    region_name="us-east-1",
)
```

---

## 10. Run + watch Live Feed

Open the **Live Feed** page (keyboard: `G L`) in another tab, then run
any agent invocation that exercises tools. Each tool call appears
within ~200 ms with decision (allow / deny / escalate), the rule that
fired, and the MITRE tactic label.

Click any row → see the signed receipt + the signal that triggered.

---

# Part V — Configure your workspace (Day 1)

Open **Settings** (sidebar → Workspace → Settings, or keyboard `G S`).

## 11. Workspace identity + compliance pack

- **Settings → Workspace** → set company name + logo + default
  compliance pack (SOC 2 / PCI / HIPAA / Finance / DevOps).
- Workspace identity is what shows up on the public Trust Center page
  and on every Slack / SIEM / webhook payload.

---

## 12. SSO + SCIM

- **Settings → SSO** — paste your IdP metadata for SAML / Okta / Azure
  AD / generic OIDC. Email + password (Clerk default) stays available
  as a fallback.
- **Settings → SCIM** — enable SCIM 2.0 provisioning if you want HR to
  push users into Aegis automatically when they join the company. New
  joiners appear in the Team page within minutes of the HR record
  landing.

---

## 13. Slack approvals + on-call

Aegis routes any **ESCALATE** decision (money movement above cap, prod
destruction, mass-PII access) to a Slack channel as an interactive
approval card with Approve / Reject buttons.

### Connect

1. **Settings → Integrations → Slack → "Connect Slack"**.
2. Sign in as a Slack admin, pick the workspace + channel.
3. OAuth handshake (`/integrations/slack/initiate` →
   `/integrations/slack/callback`) installs the Aegis bot.
4. Status flips to "connected" with the bot user ID + channel listed.

### Test it

1. Sidebar → **Agent Playground** (`/playground`) → pick the agent →
   fire a tool call that should escalate (e.g. `send_wire` with
   `amount=500000`, `recipient_kind=external`).
2. Aegis returns ESCALATE. The Approval Card lands in Slack within
   2 seconds with: requester, agent, tool, args summary, MITRE label,
   risk score, two buttons.
3. Click **Approve** → the original `/execute` call returns 200, the
   tool runs, the audit row records who approved.
4. Click **Reject** → the tool is permanently blocked for this call,
   audit row records the rejecter.

Approvals also surface in the **Approval Inbox** (`G Q`) UI. Every
Slack callback is HMAC-signed so a leaked URL can't be replayed.

---

## 14. Webhooks, SIEM, ITSM

| Integration | What it does | Where to configure |
|---|---|---|
| **PagerDuty** | Sev-0/1 incidents page on-call | Settings → Webhook Settings → PagerDuty |
| **SIEM** — Splunk HEC / Datadog Logs / Elastic ECS / Sentinel / Chronicle | Every decision streamed to your SIEM with full envelope | Settings → SIEM |
| **Jira / ServiceNow** | Auto-create ITSM tickets for incidents and approvals | Settings → Integrations |
| **Generic egress webhook** | HMAC-signed POST to a URL you provide | Settings → Webhook Settings |
| **Stripe** | Self-serve billing | Settings → Billing |

Webhook signatures use Svix (Clerk-style) or HMAC-SHA256 depending on
the destination. Constant-time comparison; per-webhook delivery log so
you can debug retries without contacting us.

---

## 15. API keys

- **Settings → API Keys → "Create new key"** — this is the `acp_...` key
  your SDK uses. Scope it (`read-only` / `employee` / `full`) and copy
  it **once** (we never store the plaintext).
- For **Path B** (proxy), mint one `acp_emp_...` key per employee. Token
  usage, spend, harmful-action counts roll up to that employee on the
  Team page.

---

## 16. Roles + RBAC

Aegis ships six roles. Every authenticated route maps to an allowed
role set; 18 enforceable capabilities. The matrix is rendered live in
the UI (Settings → RBAC) so you can hover any cell to see the
enforcing code path.

| Role | Sees | Can change | Typical owner |
|---|---|---|---|
| **OWNER** | Everything | Everything including billing + workspace deletion | Founder / CISO |
| **ADMIN** | Everything | Everything except billing | Head of security ops |
| **SECURITY_ANALYST** | Everything | Policies, approvals, incidents | SOC analyst |
| **AUDITOR** | Read-only — every screen | Nothing | External auditor, compliance lead |
| **OPERATOR** | Operational pages | Agents, kill switch, approvals | On-call engineer |
| **AGENT** | API key calling `/execute` only | N/A | Service account for your agent |

Cross-tenant access is **structurally impossible** — every SQL query
carries `WHERE tenant_id = $1`. We ran a 7-attack isolation pentest with
zero bypasses (the script is public on GitHub).

---

# Part VI — Roll out (Day 4–5)

## 17. Add employees + per-seat budgets

Aegis tracks AI usage **per employee**, not per agent or per app. That's
how you answer "who is using AI?" and "what's it costing us?" without
asking everyone.

### Provision

- **Settings → Team → "Invite member"** — paste a comma-separated email
  list. Aegis sends each a Clerk-backed signup link that lands them in
  your tenant.
- Or rely on **SCIM** (set up in §12) to auto-push from your IdP.

### Per-seat budgets

- **Settings → Team** → click a member → set daily + monthly USD budget.
- The gateway enforces the cap **before** the upstream LLM call is made.
- When a seat hits the cap, the next call returns `quota_exceeded` with
  `Retry-After` + a notification to the seat AND to OWNER/ADMIN.
- The Team page rolls up daily/monthly spend per seat AND per team
  (group seats under "Engineering", "Support", etc.).

---

## 18. Pick compliance pack

**Settings → Compliance** → choose one or more.

**Shipped today** (the API parameter name is shown in `code`):

- `soc2` — CC6.1 logical access, CC7.2 system monitoring, CC8.1 change-control, audit-trail integrity, access reviews.
- `eu-ai-act` — Article 12 record-keeping, Article 13 transparency, Article 61 post-market monitoring, Annex IV §3.
- `nist-ai-rmf` — MEASURE 2.1 (system performance & operations), GOVERN 5.1 (incident response).

**On the roadmap** (mapping exists in code, no UI picker yet):

- `dpdp` (India DPDP Act) — Section 8(5)–8(8).
- PCI-DSS, HIPAA, Finance/SOX, DevOps — separate signal packs ship
  out-of-the-box and are *enforced* in the gateway today; the
  evidence-bundle pivot per pack is the unshipped piece.

Each pack maps every block / escalate decision to the specific control
it covers (visible on every audit row + in the Compliance Posture report
under Compliance → Generate evidence).

> **API note** — `/compliance/verifiable-bundle/{framework}` framework
> names are **case-sensitive, hyphenated**: `soc2`, `eu-ai-act`,
> `nist-ai-rmf`. Passing `SOC2` returns 400.

---

## 19. Shadow Mode → Enforce

You start in Shadow Mode (14 days by default — you have time to look at
every would-be decision before anything is blocked).

- Sidebar → **Shadow Mode** (`/shadow-mode`) shows policies in shadow
  with "would have denied" counts.
- Sidebar → **Shadow Review** (`/shadow-review`) is where you decide:
  **Promote** → policy starts enforcing. **Rollback** → discard.

When you're confident, **Settings → Workspace → "Exit shadow mode"**.
From that point Aegis blocks at runtime; everything you saw in shadow
now fires for real.

---

# Part VII — Operate (Day 6+)

## 20. Live incident response

When an agent does something the policy engine considers a denied or
escalated action, an **incident** is opened.

- Sidebar → **Incidents** (`G I`) — open / acknowledged / mitigated /
  resolved, severity (LOW / HIGH / CRITICAL), assigned-to.
- Click an incident → full timeline: original request, decision receipt,
  related audit rows, MITRE tactic, signal that triggered, blast radius
  from Identity Graph.

---

## 21. Auto-response playbooks

Sidebar → **Auto-Response** (`/auto-response`). Wire any incident class
to an automatic action:

- *"If risk_score > 90 AND tool in money_movement → quarantine agent
  for 1 hour."*
- *"If incident.severity = CRITICAL AND tactic = TA0040 → page on-call
  in Slack + open a ServiceNow ticket."*

---

## 22. Kill switch

The red icon in the top-right is your panic button. **One click +
confirm and every agent in the tenant returns 403 in <5 seconds.**

Use during a confirmed compromise. Release the switch from the same
dialog when the threat is contained. Audit row records actor + reason
— non-repudiable.

---

# Part VIII — Prove (Day 7 — auditor handoff)

This is the part your auditor cares about.

## 23. Cryptographic chain

Every decision Aegis records is signed with **ed25519**. Every day at
**00:00 UTC** we compute a Merkle root over that day's signed records
and publish the root + signature to a public S3 bucket
(`s3://aegis-public-roots-628478946931`). The roots form an
**append-only chain** — yesterday's root hash is included in today's
root.

**Why this matters:** an auditor (or a paying customer) can verify
months of audit history *offline* — without trusting our DNS, our load
balancer, or our application code.

---

## 24. Evidence bundle export

**Compliance → "Generate evidence"** → date range → produces a TAR with:

- Every audit row (JSONL)
- The Merkle proof for each row
- The signed root for each day
- The policies that were active during that range
- A `manifest.json` with SHA-256 of each component

Send the TAR to the auditor. They verify, you've answered the audit
without an interview.

---

## 25. Offline verification

`pip install aegis-aevf` installs an `aegis-verify` CLI that takes an
evidence bundle and runs **six independent integrity checks**:

| Check | What it proves |
|---|---|
| **V1** — Bundle format | Bundle is a well-formed AEVF (spec `aevf/0.1.0`) |
| **V2** — Event hash recompute | Every row's canonical hash matches the recorded hash |
| **V3** — Per-shard chain | Each shard's `prev_hash` chain is unbroken |
| **V4** — Root signatures | Each Merkle root's ed25519 signature verifies against the published key |
| **V5** — Root chain | Today's root references yesterday's root hash (no holes) |
| **V6** — Retention metadata | Retention claims match the per-record metadata |

If any of V1–V6 fails, the verifier exits non-zero and tells you which
row, shard, or root broke the chain. **You can run this against our
evidence on your laptop with no network calls.** That's the whole
point: the chain is self-verifying. We can't tamper with it after the
fact without breaking V4/V5 on every prior root.

Signing keys are stored in AWS KMS (customer-managed CMK), rotated
quarterly, with historical keys preserved in
`transparency_historical_keys` so old receipts still verify after
rotation.

---

# Part IX — Reference

## 26. Every page in the product

| Sidebar item | What's on it | Who uses it |
|---|---|---|
| **Dashboard** | KPI hero (protected agents, evaluated actions, allow/deny/escalate counts, $ risk mitigated, records protected, controls enforced) | CTO / CISO daily |
| **Team** | Per-employee spend, budgets, roles, last activity | Finance + IT |
| **Live Feed** | Real-time decisions, MITRE label, click-through to receipt | SecOps during demo / triage |
| **Agents** | Agent inventory, risk tier, tools, provider | Eng leads |
| **Incidents** | Open / acknowledged / mitigated / resolved | SecOps + IR |
| **Policies** | Policy editor (rules + compliance mapping) | SecEng |
| **Approval Inbox** | Pending ESCALATE decisions awaiting a human | Approvers (CFO for wires, CISO for prod) |
| **Compliance** | Control rollup + evidence export | Auditors |
| **Settings** | SSO, SCIM, Slack, webhooks, RBAC, API keys, workspace | Admin |
| **Audit Logs** | Filterable audit table + CSV export | Anyone with READ_ONLY+ |
| **Forensics** | Per-agent 24h timeline | IR |
| **Agent Playground** | Manual `/execute` for testing | Devs |
| **Threat Intel** | IOC matches across your audit history | SecOps |
| **Evaluation** | Replay historic prompts against a new policy version | SecEng before promote |
| **Playbooks** | Pre-built auto-response templates | SecEng |
| **Auto-Response** | Wire incidents → automatic actions | SecEng |
| **Identity Graph** | Agents → resources access graph | IR / threat hunting |
| **Threat Graph** | Same data + MITRE tactic coverage view | SOC |
| **Shadow Mode** | Candidate policies running in shadow | SecEng |
| **Shadow Review** | Promote / rollback shadow policies | SecEng + Approvers |
| **Flight Recorder** | End-to-end timeline of any `request_id` | Debugging |
| **Decision Explorer** | Walk the policy evaluation step by step | SecEng |
| **Session Explorer** | Conversation-level view, grouped by session | SOC |
| **Fleet** | Cross-service health (gateway / identity / policy / decision) | Ops |
| **System Health** | Per-container health, latency, error rate | Ops |
| **Billing** | Invoices + usage CSV per period | Finance |
| **Kill Switch** | Workspace-wide halt | Owner / Admin during incident |
| **Trust Center** | Public-facing trust page (Merkle root, signing key, SOC 2 status) | Prospects, auditors |
| **Status** | Operational status (services up, p95, queue depth) | Anyone |

---

## 27. The four product modules

The left-side navigation is organized into four product modules,
intentionally so that a first-time CISO or CTO can find what they
need without docs.

### Observe — what your AI did

Surface a CIO opens daily.

- **Dashboard** — headline numbers, 30-day window by default.
- **Live Feed** — every decision in real time, within 200 ms.
- **Team** — per-employee row with spend, budgets, harmful actions caught.
- **Per-employee profile** — budget bars, 30-day sparkline, last 25
  calls with token counts, latency, the signal that fired on denies.
- **Notifications** — every routed notification (escalations, quota
  warnings, key revokes, kill-switch toggles).

### Protect — what got blocked, who approves

Operator surface.

- **Agents** — your AI fleet (name, provider, risk, status, owner).
- **Agent snapshot** — per-agent overview (tool allowlist, last 50
  decisions, behavioral baseline + drift score, MITRE tactics fired).
  Tabs: Overview / Tools / Decisions / Blast Radius.
- **Incidents** — SOC queue (sev-0 to sev-3, assignee, opened-at).
- **Approval Inbox** — pending high-risk actions awaiting approval.
- **Policies** — Rego policy registry (Editor / Simulator / Playground /
  Staging / Analytics tabs).
- **Kill Switch** — workspace-wide halt; non-repudiable audit.
- **Auto-Response** — auto-response rules.

### Prove — cryptographic evidence

Compliance surface.

- **Compliance** — framework picker (SOC 2 / EU AI Act / NIST AI RMF /
  India DPDP). One-click bundle export.
- **Audit Logs** — filterable chain; signed receipts.
- **Trust Center** — public-facing trust page.
- **Status** — operational status (13/13 services operational, p95
  latency, queue depth, kill-switch state).

### Workspace — config + admin

Admin surface.

- **Settings** — hub for SSO, SCIM, Users, RBAC, Quota, Billing, SIEM,
  Webhook Settings, Scheduled Reports, Developer Panel, Admin Console.

A collapsible **Advanced** group at the bottom of the sidebar exposes
13 analyst surfaces: Forensics, Identity Graph, Threat Graph, Flight
Recorder, Decision Explorer, Session Explorer, Fleet, Evaluation,
Shadow Mode, Playbooks, Threat Intel, Agent Playground, System Health.

---

## 28. Integrations matrix

You don't need any of these to use Aegis. The platform works out of
the box with email-only Clerk auth. Wire integrations in as you scale.

| Integration | What it does | Where to configure |
|---|---|---|
| **SSO** (Okta / Azure AD / Google / generic OIDC) | Single sign-on; SCIM auto-provisioning | Settings → SSO |
| **Slack** | Approval requests as messages with HMAC-signed buttons | Settings → Webhook Settings → Slack |
| **PagerDuty** | Sev-0/1 incidents page on-call | Settings → Webhook Settings → PagerDuty |
| **SIEM** — Splunk HEC / Datadog Logs / Elastic ECS / Sentinel / Chronicle | Every decision streamed | Settings → SIEM |
| **Jira / ServiceNow** | Auto-create ITSM tickets | Settings → Integrations |
| **Generic egress webhook** | HMAC-signed POST to a URL you provide | Settings → Webhook Settings |
| **Stripe** | Self-serve billing | Settings → Billing |

---

## 29. Out-of-the-box catches

(Same table as §3 — repeated here so the reference section is complete
without scrolling up.)

| Category | Default verdict |
|---|---|
| Filesystem path traversal | deny |
| Destructive shell | deny |
| SQL injection / mass extraction | deny / escalate |
| Wire transfers above threshold | escalate |
| Kubernetes destruction | deny |
| Terraform destruction | deny |
| Cross-tenant access | deny |
| Mass PII extraction | escalate |
| Email exfil patterns | deny |
| Agent behavior drift | escalate + baseline alert |
| Cumulative risk threshold | deny + auto-quarantine |
| Inference cost runaway | block (429 + Retry-After) |

---

# Part X — Plan

## 30. Day 1 / Day 7 / Day 30 rollout plan

The pattern we've seen work across design partners.

### Day 1 — proof of life

- Sign up, complete onboarding wizard, mint one Path B employee key
- Point your most-used Cursor / Claude Code workflow at the proxy
- Open Live Feed in a second monitor; leave it open
- Look for: every AI action shows up in Aegis within 200 ms

### Day 7 — observation

- Mint employee keys for 5–10 colleagues
- Stay in shadow mode (default — policies observe but don't enforce)
- Open Shadow Mode Review daily and ask: "would Aegis have blocked
  something that would have hurt us?" Usually yes, within 48 hours
- Configure SSO so your team logs in with their work identity

### Day 30 — enforcement

- Exit shadow mode in Settings → Workspace
- Wire Slack approvals (CFO gets a message for wires ≥ $100k)
- Connect SIEM (Splunk / Datadog / Elastic)
- Run the first month-end Compliance export, hand to your auditor;
  have them run `aegis-verify` against the bundle on their own laptop
- Set up Scheduled Reports for weekly evidence delivery

### Quarterly

- Rotate signing keys (we run the runbook for you on request)
- Run the monthly DR drill: restore a recent backup into an isolated
  VPC, verify the audit chain end-to-end
- Review the per-policy Analytics page; sunset policies that haven't
  fired in 90 days

---

## 31. SDK + version management

### Do I need to publish a new SDK version after every backend deploy?

**No.** The SDKs talk to the gateway over HTTP. The contract is
versioned at `/v1/`. Backend deploys change the *server's*
implementation. The wire format is stable.

You publish a new SDK version only when:

- A new feature requires a new request shape (rare — we add fields
  backwards-compatibly).
- We add a new SDK convenience.
- A security fix in a dependency.

Customers can `pip install --upgrade aegis-anthropic` whenever they
want; we don't force-update.

### How customers find out about new versions

- **Settings → API Keys** page shows the minimum SDK version the
  current gateway will accept.
- **Dashboard top banner** surfaces "your SDK is N versions behind" if
  your last `/execute` call advertised an older `aegis-sdk-version`
  header.

---

## 32. QA test plan

Hand this to your security team. Every step is observable in Aegis.

### A. Sanity (5 min)

1. Sign in. Open Dashboard. Confirm Protected agents ≥ 1.
2. Register a `test-agent` with tools `read_file`, `query_database`,
   `send_email`.

### B. Four ALLOW paths (nothing benign blocked)

3. Playground → `read_file` with `path=/tmp/foo.txt` → **allow**.
4. `query_database` with `SELECT id, email FROM users LIMIT 10` →
   **allow**.
5. `send_email` to an internal address → **allow**.
6. Click each row in Live Feed → confirm the signed receipt loads.

### C. Five DENY paths (the rules you cared about most)

7. `read_file` with `path=/etc/passwd` → **deny**, reason
   `process_env_read` or `system_file_read`.
8. `query_database` with `SELECT * FROM users; DROP TABLE customers;` →
   **deny**, reason `sql_injection_pattern`.
9. **Via `shell` wrapper** — `tool_name="shell"`,
   `arguments={"command":"kubectl delete namespace prod"}` → **deny**
   (`OPS-K8S-001`, risk 90). The bare `kubectl` / `terraform` tool
   names are not yet in the canonical action mapping, so the firewall
   today recognizes destructive Kubernetes / Terraform actions only
   through the `shell` wrapper. Same for `tool_name="shell"` with
   `command="terraform destroy ..."` → **escalate** (`OPS-IAC-002`).
10. **Via `shell` wrapper** — secret/PII exfil patterns
   (`curl --data-binary @/path/secret`, `aws s3 cp …`, `sendmail …`)
   inside `tool_name="shell"` → **deny**. The bare `send_email`
   tool name is not yet canonical, so body-content scanning fires
   today only through `shell`.
11. `send_wire` `amount_usd=10000000, recipient_kind=external` →
    **deny**, reason `money_movement_hard_cap`.

### D. Escalate path (human-in-loop)

12. `send_wire` `amount_usd=250000, recipient_kind=external` →
    **escalate**. Slack channel pings, approval card visible in
    Approval Inbox.
13. Click Approve in Slack → tool runs, audit row records approver.
14. Repeat with Reject → tool blocked, audit row records rejecter.

### E. Kill switch

15. Top-right Kill Switch → engage. Confirm.
16. Playground → `read_file` again → expect **403 Tenant blocked**.
    Should land in <5 s.
17. Release Kill Switch. Same call → **allow** again.

### F. Cryptographic verification

18. Audit Logs → CSV export last 7 days.
19. Compliance → Generate evidence (7 d) → download TAR. (API: hit
    `/compliance/verifiable-bundle/soc2?days=7` — lowercase, hyphenated.)
20. `pip install aegis-aevf && aegis-verify --bundle ./evidence.tar.gz`
    → expect **"VERIFIED ✓"** with zero chain breaks. **Known limit:**
    a tenant in the middle of a rapid-burst incident window (e.g. an
    agent that just triggered `runaway_loop` auto-quarantine with
    50+ writes in <2 s) may show 1–2 V3 `prev_hash` mismatches on a
    single shard. We're hardening the per-shard write serialization
    in the next release; flag it via §37 if you see it outside that
    scenario.

Hand the report to your auditor or CISO. If any step misbehaves, file
via the in-product feedback widget — every report includes the
`request_id` so engineering can pull the exact Flight Recorder trace.

---

## 33. Pricing

| Tier | Price | What you get |
|---|---|---|
| **Free** | $0 | 1 workspace, 1k actions/month, 30-day retention, Clerk auth, email support |
| **Pro** | $499/mo per workspace | 1M requests/day, 365-day retention, SSO, Slack/SIEM, weekly evidence exports, 4h SLA on Sev-1 |
| **Enterprise** | Custom | Unlimited actions, multi-region, dedicated tenant, BAA + DPA, named SRE on call, custom Rego authoring support, white-glove onboarding |

We bill by signed audit row, not by AI tokens — your AI provider
already charges you for tokens. We won't double-charge.

No setup fees, no minimums on Pro, cancel anytime from Settings →
Billing. Full pricing page: https://aegisagent.in/pricing

---

## 34. Compliance posture

Honest about what we have and what we're working toward.

| Framework | Status |
|---|---|
| **SOC 2 Type I** | In progress (Q3 2026) |
| **SOC 2 Type II** | Scheduled Q1 2027 |
| **EU AI Act Article 12** (audit-record minimum) | Code-compliant — AEVF spec maps every record |
| **India DPDP Act Sec. 8(5)** (record retention) | Code-compliant — default 365-day retention with per-tenant override |
| **NIST AI RMF** | Mapped — see AEVF spec |
| **ISO 27001** | Not started; on roadmap behind SOC 2 Type II |
| **HIPAA** | BAA template available on request; not yet certified |
| **PCI-DSS** | Not in scope (we don't touch payment card data) |

Every claim has a citation. The Trust Center at
https://aegisagent.in/trust has the source links.

---

## 35. What Aegis is NOT yet

Honest list. We'd rather lose a deal on Day 1 over a missing feature
than land it and lose it on Day 60.

- **No on-prem deployment.** SaaS only on `ap-south-1` (default) and
  `eu-west-1` (paid contract). Self-hosted is roadmap, not committed.
- **No multi-region active-active.** Multi-AZ within a region, yes.
  Cross-region active-active is a Q2 2027 item.
- **No SOC 2 Type II yet.** Type I in progress. If procurement
  requires Type II today, we are not the right fit yet.
- **No model fine-tuning suppression.** We govern tool calls and
  outputs, not the model weights themselves.
- **No automatic incident remediation beyond quarantine.** We block,
  alert, open a ticket. We don't auto-revert your infra — deliberate;
  we don't trust ourselves enough to do that yet.
- **No iOS / Android app.** Web only.

If any of the above is a blocker for you, tell us. We'd rather know now.

---

## 36. Common questions

**Q: Do I have to give Aegis my Anthropic / OpenAI key?**
No. Your model key never reaches us. The Aegis SDK keeps it on your
machine; we only see the `tool_use` block extracted from the LLM
response (action + arguments, not the full prompt unless you opt in
to the optional `log_prompt=true` flag).

**Q: What happens if Aegis goes down mid-call?**
Default is **fail-closed**: tool calls are denied with
"aegis-unreachable". Switch to **fail-open** under Settings → Workspace
if you'd rather degrade to "log only" during an Aegis outage —
typical for read-only analytics agents.

**Q: We're already on PCI / HIPAA. Does Aegis change our scope?**
No. Aegis is a control plane — it sits in front of your agents and
adds policy + audit. It doesn't store PHI / PCI data (we never see the
tool's return values). You're enforcing scope at the agent; Aegis
enforces *who can ask the agent for what* + *what gets recorded*.

**Q: Can the auditor verify our evidence without trusting Aegis?**
Yes. Daily Merkle roots are mirrored to a public S3 bucket and signed
ed25519. Your auditor runs `aegis-verify` offline and gets a PASS /
FAIL on the chain without ever talking to our control plane. If we
deleted our database tonight, your historical evidence is still
cryptographically verifiable from the public mirror.

**Q: How do I onboard 200 employees in a day?**
SCIM. Settings → Integrations → SCIM → copy the bearer token to your
IdP (Okta / Azure AD / OneLogin). Provisioning runs from the IdP side;
new joiners appear in the Team page within minutes of the HR record
landing.

**Q: We use AWS Bedrock agents — does the integration work?**
Yes. `pip install "aegis-bedrock[bedrock]"`. Drop-in for
`boto3.client("bedrock-agent-runtime")`. Every action-group invocation
is checked. Same audit chain.

**Q: Does Aegis see our prompts or LLM responses?**
The gateway scans prompts for injection patterns and routes the
request. We never store prompt bodies — only decision metadata +
finding IDs land in the audit log.

**Q: Where is data hosted?**
Pro is multi-tenant on AWS `ap-south-1`. Enterprise gets a dedicated
region or full bring-your-own-cloud (BYOC) deployment in your VPC.

---

# Part XI — Help

## 37. Feedback + support

We treat every Sev-1 finding from an evaluator as if it were from a
paying customer.

### How to file feedback

```
TITLE: <one-line summary>

SEVERITY: 1 / 2 / 3
  1 = white screen, console error, broken auth, data loss risk,
      claim in this doc is false
  2 = wrong copy, missing CTA, visible layout bug, real-time feature
      not ticking
  3 = polish nit, wording, color contrast

URL: https://aegisagent.in/<path>
VIEWPORT: 1366×768 / 1920×1080 / other
BROWSER: Chrome 120 / Edge 120 / Safari 17
SIGNED-IN AS: <your-email> OR anonymous demo workspace
TIMESTAMP: 2026-06-26 14:32 IST

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

- **Fastest** — email the founder directly.
- **Best** — open a GitHub issue at
  https://github.com/Abhi-mishra998/aegis/issues/new with label
  `evaluator-feedback`.
- **Bulk** — paste the lot into a Google Doc and share the link.

### What we commit to

| Severity | Acknowledge | Fix or workaround |
|---|---|---|
| **1** | 4 working hours | 1 business day |
| **2** | 1 business day | Next scheduled deploy (typically same week) |
| **3** | 1 business day | Tracked in public backlog |

Every Sev-1 we receive shows up in the Trust Center incident history
within 24 hours of resolution.

### Other channels

- **In-product**: any page → top-right "?" → "Report a bug" — includes
  the current `request_id` so engineering can pull the exact Flight
  Recorder trace.
- **Email**: `support@aegisagent.in`.
- **Status**: `https://aegisagent.in/status` (public, mirrored to S3 so
  it survives an outage of the ALB).
- **Security disclosure**: `https://aegisagent.in/security` (PGP key +
  responsible-disclosure form).

---

## Closing — what we're asking from you

You're evaluating a young product. The site, the SDK, the CLI verifier
— they all work today, live, and you can verify every claim above
without us in the room. The team is small. The roadmap is honest.
The credits we run on today come from AWS Activate; the pricing in §33
is what becomes real when those credits expire in Q1 2027.

If you decide Aegis is wrong for you, we'd love a one-line note on why
— that's how we get better. If you decide it's right, we'd love to be
your governance plane, and we'll show up the same way every Sev-1
customer gets shown up for: fast, honest, in writing.

— The Aegis team
