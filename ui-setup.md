# Aegis — How a Company Actually Rolls It Out

> Looking for the 5-minute, no-signup evaluator tour first? See
> [setup-agies.md](setup-agies.md). This file picks up at Day 0 after
> sign-up and walks the post-signup adoption flow through Day 7.

This is the operational counterpart to `setup-agies.md` (which is the
evaluator's tour). This file is for the person on the customer side who
just signed up and needs to take the platform from "logged in" to "every
agent in our company is governed."

Everything in this doc is **doable today** with the version live on
`aegisagent.in` — you do not need to wait for a new SDK release. The
2026-06-24 PyPI packages (`aegis-anthropic==1.1.2`, `aegis-openai==1.1.2`,
`aegis-langchain==1.1.3`, `aegis-bedrock==1.1.3`) default to the
consolidated `https://aegisagent.in` gateway and keep working across our
backend deploys.

---

## Table of contents

1. [Day 0 — Sign up + workspace (5 minutes)](#1-day-0--sign-up--workspace-5-minutes)
2. [Day 1 — Workspace settings (15 minutes)](#2-day-1--workspace-settings-15-minutes)
3. [Day 2 — First real agent (30 minutes)](#3-day-2--first-real-agent-30-minutes)
4. [Day 3 — Slack approvals + on-call routing](#4-day-3--slack-approvals--on-call-routing)
5. [Day 4 — Add employees + per-seat budgets](#5-day-4--add-employees--per-seat-budgets)
6. [Day 5 — Compliance pack + Shadow mode](#6-day-5--compliance-pack--shadow-mode)
7. [Day 6 — Live incident response](#7-day-6--live-incident-response)
8. [Day 7 — Auditor handoff](#8-day-7--auditor-handoff)
9. [Page-by-page reference](#9-page-by-page-reference)
10. [QA test plan a buyer's security team runs](#10-qa-test-plan-a-buyers-security-team-runs)
11. [SDK + version management](#11-sdk--version-management)
12. [Common questions](#12-common-questions)

---

## 1. Day 0 — Sign up + workspace (5 minutes)

**Where to start:** `https://aegisagent.in/signup`

1. Sign up with your company email. Aegis uses Clerk for auth — Google /
   Microsoft SSO are one click, and you can wire your enterprise IdP
   later (SAML, Okta, etc.) under Settings → SSO.
2. Clerk creates your account, the gateway provisions a new tenant
   automatically (idempotent — webhook + first-call sync covers either
   ordering), and you land on `/dashboard`.
3. Your tenant starts in **14-day Shadow Mode** by default. Every
   decision is recorded with what the production action WOULD have been,
   but nothing is blocked. You exit shadow mode when you're ready
   (Settings → Workspace → "Exit shadow mode" — see §6).
4. You're the OWNER. You can do everything. Add more roles in
   Settings → RBAC (§5).

> If you only want to test the product first, go through the
> evaluator-flow in [setup-agies.md](setup-agies.md) instead —
> `https://aegisagent.in/` → "Spawn demo workspace" gives you a 30-min
> sandbox with 5 named agents (db-copilot, support-bot, devops-agent,
> finance-bot, sales-research-agent), 60 audit events across 14 days,
> 2 incidents, 2 shadow policies, 10 identity-graph nodes, 8 edges —
> all populated automatically on spawn. No signup needed.

---

## 2. Day 1 — Workspace settings (15 minutes)

Open **Settings** (sidebar → Workspace → Settings, or keyboard `G S`).

### Identity (who can sign in)

- Settings → SSO: paste your IdP metadata if you want SAML / Okta /
  Azure AD instead of email + password.
- Settings → SCIM: enable SCIM 2.0 provisioning if you want HR to push
  users into Aegis automatically when they join the company.

### Notifications

- Settings → Slack → "Connect Slack" — runs the OAuth handshake (see
  §4 for the full flow).
- Settings → Webhooks: paste any incident-management webhook URL
  (PagerDuty, Opsgenie, your own SIEM). Aegis HMAC-signs every payload.
- Settings → SIEM Forwarder: stream audit events to Splunk / Datadog /
  Elastic with a single endpoint URL + bearer token.

### API keys

- Settings → API Keys → "Create new key" — this is the `acp_...` key
  your SDK uses. Scope it (read-only / employee / full) and copy it
  ONCE (we never store the plaintext).

### Workspace identity

- Settings → Workspace → set the company name + logo + default
  compliance pack (SOC 2 / PCI / HIPAA / Finance / DevOps).

---

## 3. Day 2 — First real agent (30 minutes)

You wire one of your existing apps to call Aegis before any tool runs.
The SDK does this with a **single line change** — `base_url=` (or the
constructor name).

### Step 1: register the agent in Aegis

Sidebar → **Agents** → "New Agent" → fill in:
- Name (e.g. "support-bot")
- Provider (anthropic / openai / bedrock / langchain / custom)
- Risk tier (low / medium / high / critical — drives default budgets)
- Tool allow-list (the names of tools this agent is permitted to use)

Click create. The agent UUID + permissions are stored. You'll reference
the UUID in the SDK constructor.

### Step 2: install the SDK

| Stack | Install |
|---|---|
| Anthropic Python SDK | `pip install 'aegis-anthropic==1.1.2'` |
| OpenAI Python SDK | `pip install 'aegis-openai==1.1.2'` |
| LangChain agent | `pip install 'aegis-langchain==1.1.3'` |
| AWS Bedrock Agents | `pip install 'aegis-bedrock[bedrock]==1.1.3'` |

### Step 3: swap the constructor

**Anthropic:**

```python
# Before
import anthropic
client = anthropic.Anthropic(api_key="sk-ant-...")

# After
from aegis_anthropic import AegisAnthropic
client = AegisAnthropic(
    api_key="sk-ant-...",                       # your Anthropic key
    aegis_key="acp_...",                        # from Settings → API Keys
    aegis_url="https://aegisagent.in",
    tenant_id="<your-tenant-uuid>",             # in Settings → Workspace
    agent_id="<agent-uuid-from-step-1>",
)
```

**OpenAI:**

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

**LangChain:**

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

**Bedrock:**

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

### Step 4: run + watch Live Feed

Open the **Live Feed** page (`G L`) in another tab, then run any
agent invocation that exercises tools. Each tool call appears within
~200ms with decision (allow / deny / escalate), the rule that fired,
and the MITRE tactic label.

Click any row → see the signed receipt + the signal that triggered.

---

## 4. Day 3 — Slack approvals + on-call routing

Aegis can route any **ESCALATE** decision (money movement above cap,
prod destruction, mass-PII access, etc.) to a Slack channel as an
interactive approval card with Approve / Reject buttons.

### Connect Slack

1. Settings → Integrations → Slack → "Connect Slack".
2. Sign in as a Slack admin, pick the workspace + channel.
3. The OAuth handshake (`/integrations/slack/initiate` →
   `/integrations/slack/callback`) installs the Aegis bot.
4. Status flips to "connected" with the bot user ID + channel listed.

### Test it

1. Sidebar → **Agent Playground** (`/playground`) → pick the agent →
   fire a tool call that should escalate (e.g. `send_wire` with
   `amount=500000`, `recipient_kind=external`).
2. Aegis returns ESCALATE. The Approval Card lands in Slack within 2
   seconds with: requester, agent, tool, args summary, MITRE label,
   risk score, two buttons.
3. Click **Approve** → the original `/execute` call returns 200, the
   tool runs, the audit row records who approved.
4. Click **Reject** → the tool is permanently blocked for this call,
   audit row records the rejecter.

Approvals also surface in the **Approval Inbox** (`G Q`) UI for the
human who wants to review without leaving the dashboard. HMAC on every
Slack callback so a leaked URL can't be replayed.

---

## 5. Day 4 — Add employees + per-seat budgets

Aegis tracks AI usage **per employee**, not per agent or per app. That's
how you answer "who is using AI?" and "what's it costing us?" without
asking everyone.

### Provision employees

- Settings → Team → "Invite member" — paste a comma-separated email
  list. Aegis sends each one a Clerk-backed signup link that lands
  them in your tenant.
- If you turned on SCIM in Day 1, your IdP can push the employee
  list automatically. New joiners appear within minutes of their HR
  record landing.

### Roles (RBAC)

- Settings → RBAC → assign each employee a role:
  - **OWNER**: full access (you, your CTO).
  - **ADMIN**: settings + integrations + RBAC (your SecOps lead).
  - **SECURITY_ANALYST**: audit + incidents + forensics. No write to
    policies.
  - **DEVELOPER**: register agents, fire test calls, view their own
    activity.
  - **READ_ONLY**: dashboards only. For auditors + observers.

### Per-seat budgets

- Settings → Team → click a member → set daily + monthly USD budget.
- The gateway enforces the cap before the upstream LLM call is made.
  When a seat hits the cap, the next call returns `quota_exceeded`
  with a Retry-After + a notification to the seat AND to OWNER/ADMIN.
- The Team page rolls up daily/monthly spend per seat AND per team
  (group seats under "Engineering", "Support", etc).

---

## 6. Day 5 — Compliance pack + Shadow mode

### Pick your compliance pack

Settings → Compliance → choose one or more:
- **SOC 2** — CC8.1 change-control, audit-trail integrity, access reviews.
- **PCI-DSS** — PAN/card-data egress patterns, scope segmentation.
- **HIPAA** — PHI patterns, minimum-necessary access rules.
- **Finance / SOX** — money movement, segregation of duties, four-eyes.
- **DevOps** — production destruction, IaC apply, mass deletions.

Each pack maps every block / escalate decision to the specific control
it covers (visible on every audit row + in the Compliance Posture report
under Compliance → Generate evidence).

### Shadow Mode → Enforce

You start in Shadow Mode (14 days by default — you have time to look at
every would-be decision before anything gets blocked).

- Sidebar → **Shadow Mode** (`/shadow-mode`) shows policies in
  shadow with "would have denied" counts.
- Sidebar → **Shadow Review** (`/shadow-review`) is where you decide:
  Promote → policy starts enforcing. Rollback → discard.

When you're confident, Settings → Workspace → "Exit shadow mode". From
that point Aegis blocks at runtime; everything you saw in shadow now
fires for real.

---

## 7. Day 6 — Live incident response

### What an incident looks like in Aegis

When an agent does something the policy engine considers a denied or
escalated action, an **incident** is opened:
- Sidebar → **Incidents** (`G I`) — open / acknowledged / mitigated
  / resolved, severity (LOW / HIGH / CRITICAL), assigned-to.
- Click an incident → full timeline: original request, decision
  receipt, related audit rows, MITRE tactic, signal that triggered,
  blast radius from Identity Graph.

### Auto-response playbooks

Sidebar → **Auto-Response** (`/auto-response`). Wire any incident
class to an automatic action:
- "If risk_score > 90 AND tool in money_movement → quarantine agent
  for 1 hour."
- "If incident.severity = CRITICAL AND tactic = TA0040 → page on-call
  in Slack + open a ServiceNow ticket."

### Kill Switch

The red icon in the top-right is your panic button. One click +
confirm and **every agent in the tenant returns 403 in <5 seconds**.
Use during a confirmed compromise. Release the switch from the same
dialog when the threat is contained.

---

## 8. Day 7 — Auditor handoff

This is the part your auditor cares about.

### What Aegis gives an auditor

- **Audit Logs** (`/audit-logs`): every decision in the last N days,
  filterable by tenant / agent / decision / tool / tactic. CSV export
  with one click.
- **Compliance** (`/compliance`): per-control rollup. "How many
  escalations did SOC 2 CC8.1 generate this quarter?"
- **Forensics** (`/forensics`): pick an agent → see a 24-hour
  timeline of every action it took, sorted by risk score.
- **Cryptographic verification**: every audit row is in a Merkle
  chain. Daily roots are signed ed25519 and mirrored to a public S3
  bucket (`s3://aegis-public-roots-628478946931`). Your auditor runs
  `pip install aegis-aevf && aegis-verify --bundle <download>` and
  validates the chain offline, without trusting our control plane.

### Evidence bundle export

Compliance → "Generate evidence" → date range → produces a TAR with:
- Every audit row (JSONL)
- The Merkle proof for each row
- The signed root for each day
- The policies that were active during that range
- A `manifest.json` with SHA-256 of each component

Send the TAR to the auditor. They verify, you've answered the audit
without an interview.

---

## 9. Page-by-page reference

| Sidebar item | What's on it | Who uses it |
|---|---|---|
| Dashboard | KPI hero (protected agents, evaluated actions, allow/deny/escalate counts, $ risk mitigated, records protected, controls enforced) | CTO / CISO daily |
| Team | Per-employee spend, budgets, roles, last activity | Finance + IT |
| Live Feed | Real-time decisions, MITRE label, click-through to receipt | SecOps during demo / triage |
| Agents | Agent inventory, risk tier, tools, provider | Eng leads |
| Incidents | Open / acknowledged / mitigated / resolved | SecOps + IR |
| Policies | Policy editor (rules + compliance mapping) | SecEng |
| Approval Inbox | Pending ESCALATE decisions awaiting a human | Approvers (CFO for wires, CISO for prod) |
| Compliance | Control rollup + evidence export | Auditors |
| Settings | SSO, SCIM, Slack, webhooks, RBAC, API keys, workspace | Admin |
| Audit Logs | Filterable audit table + CSV export | Anyone with READ_ONLY+ |
| Forensics | Per-agent 24h timeline | IR |
| Agent Playground | Manual `/execute` for testing | Devs |
| Threat Intel | IOC matches across your audit history | SecOps |
| Evaluation | Replay historic prompts against a new policy version | SecEng before promote |
| Playbooks | Pre-built auto-response templates | SecEng |
| Auto-Response | Wire incidents → automatic actions | SecEng |
| Identity Graph | Agents → resources access graph | IR / threat hunting |
| Threat Graph | Same data + MITRE tactic coverage view | SOC |
| Shadow Mode | Candidate policies running in shadow | SecEng |
| Shadow Review | Promote / rollback shadow policies | SecEng + Approvers |
| Flight Recorder | End-to-end timeline of any request_id | Debugging |
| Decision Explorer | Walk the policy evaluation step by step | SecEng |
| Session Explorer | Conversation-level view, grouped by session | SOC |
| Fleet | Cross-service health (gateway / identity / policy / decision) | Ops |
| System Health | Per-container health, latency, error rate | Ops |
| Billing | Invoices + usage CSV per period | Finance |

---

## 10. QA test plan a buyer's security team runs

Hand this to your security team. Every step is observable in Aegis.

### A. Sanity (5 minutes)

1. Sign in. Open Dashboard. Confirm Protected agents = at least your
   first registered agent.
2. Register a `test-agent` with tools `read_file`, `query_database`,
   `send_email`.

### B. The four ALLOW paths (verify nothing benign gets blocked)

3. From the Agent Playground, fire `read_file` with `path=/tmp/foo.txt`
   → expect **allow**.
4. `query_database` with `SELECT id, email FROM users LIMIT 10` →
   **allow**.
5. `send_email` to an internal address → **allow**.
6. Click each row in Live Feed → confirm the signed receipt loads.

### C. The five DENY paths (the rules you cared about most)

7. `read_file` with `path=/etc/passwd` → **deny**, reason
   `process_env_read` or `system_file_read`. Live Feed row turns red.
8. `query_database` with `SELECT * FROM users; DROP TABLE customers;` →
   **deny**, reason `sql_injection_pattern`.
9. `kubectl_delete` with `target=production` → **deny**, reason
   `prod_destruction`.
10. `send_email` whose body contains `Bearer sk-ant-…` (an LLM key) →
    **deny**, reason `secret_exfil_pattern`.
11. `send_wire` `amount_usd=10000000, recipient_kind=external` →
    **deny**, reason `money_movement_hard_cap`.

### D. The escalate path (human-in-loop)

12. `send_wire` `amount_usd=250000, recipient_kind=external` →
    **escalate**. Slack channel pings, approval card visible in
    Approval Inbox.
13. Click Approve in Slack → tool runs, audit row records approver.
14. Repeat with Reject → tool is blocked, audit row records rejecter.

### E. Kill switch

15. Top-right Kill Switch → engage. Confirm.
16. From the Agent Playground, fire `read_file` again → expect **403
    Tenant blocked**. Should land in <5 seconds.
17. Release Kill Switch. Same call → **allow** again.

### F. Cryptographic verification

18. Audit Logs → CSV export the last 7 days.
19. Compliance → Generate evidence (7d) → download TAR.
20. `pip install aegis-aevf && aegis-verify --bundle ./evidence.tar.gz`
    → expect "VERIFIED ✓" with zero chain breaks.

Hand the report to your auditor or your CISO. If any step
misbehaves, file via the in-product feedback widget — every report
includes the request_id so engineering can pull the exact
Flight Recorder trace.

---

## 11. SDK + version management

### Do I need to publish a new SDK version after every backend deploy?

**No.** The SDKs talk to the gateway over HTTP. The contract is
versioned at `/v1/`. Backend deploys (the kind you saw today —
auto-seed pipeline, demo middleware fix, Clerk live wiring) only
change the *server's* implementation. The wire format is stable.

You publish a new SDK version only when:
- A new feature requires a new request shape (rare — we add fields
  backwards-compatibly).
- We add a new SDK convenience (`aegis_python` async client, etc.).
- A security fix in a dependency.

Customers can `pip install --upgrade aegis-anthropic` whenever they
want; we don't force-update.

### How customers find out about new versions

- The Settings → API Keys page shows the *minimum* SDK version that
  the current gateway will accept.
- The Dashboard's top banner surfaces "your SDK is N versions behind"
  if the customer's last `/execute` call advertised an older
  `aegis-sdk-version` header.

---

## 12. Common questions

**Q: Do I have to give Aegis my Anthropic / OpenAI key?**
No. Your model key never reaches us. The Aegis SDK keeps it on your
machine; we only see the `tool_use` block extracted from the LLM
response (action + arguments, not the full prompt unless you opt in
to the optional `log_prompt=true` flag).

**Q: What happens if Aegis goes down mid-call?**
Default is **fail-closed**: tool calls are denied with a clear error
"aegis-unreachable". Switch to **fail-open** under Settings →
Workspace if you'd rather degrade to "log only" during an Aegis
outage — typical for read-only analytics agents.

**Q: We're already on PCI / HIPAA. Does Aegis change our scope?**
No. Aegis is a control plane — it sits in front of your agents and
adds policy + audit. It doesn't store PHI / PCI data (we never see
the tool's return values). You're enforcing scope at the agent;
Aegis enforces *who can ask the agent for what* + *what gets
recorded*.

**Q: Can the auditor verify our evidence without trusting Aegis?**
Yes. Daily Merkle roots are mirrored to a public S3 bucket and
signed ed25519. Your auditor runs `aegis-verify` offline and gets a
PASS / FAIL on the chain without ever talking to our control plane.
If we deleted our database tonight, your historical evidence is
still cryptographically verifiable from the public mirror.

**Q: How do I onboard 200 employees in a day?**
SCIM. Settings → Integrations → SCIM → copy the bearer token to
your IdP (Okta / Azure AD / OneLogin). Provisioning runs from the
IdP side; new joiners appear in the Team page within ~minutes of
the HR record landing.

**Q: We use AWS Bedrock agents — does the integration work?**
Yes. `pip install "aegis-bedrock[bedrock]"`. Drop-in for
`boto3.client("bedrock-agent-runtime")`. Every action-group
invocation is checked. Same audit chain.

**Q: Pricing?**
See `/pricing` (top-right link). Tiers are Basic / Pro / Enterprise,
priced per protected agent + per evaluated action. No charge for
the demo tenants.

---

## Where to file feedback or get help

- In-product: any page → top-right "?" → "Report a bug" — includes
  the current request_id so engineering can pull the exact Flight
  Recorder trace.
- Email: `support@aegisagent.in`.
- Status: `https://aegisagent.in/status` (public, mirrored to S3 so
  it survives an outage of the ALB).
- Security disclosure: `https://aegisagent.in/security` (PGP key +
  responsible-disclosure form).
