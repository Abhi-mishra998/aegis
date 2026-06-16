# Setup Aegis — quick guide for first-time users

Aegis sits between your AI agent and the tools it calls. Every `tool_use`
(read a file, run a SQL query, send a wire, run kubectl) goes through
Aegis first. Aegis either ALLOWS it (your code runs the tool), DENIES it
(your code gets a block message), or ESCALATES it for human review.

**Your LLM API key never leaves your machine.** Aegis only sees the tool
name and arguments — never your prompts, never your model output.

You should be able to go from "never heard of Aegis" to "agent run with
Aegis catching a path-traversal attempt" in under 5 minutes.

---

## 1. Sign up

Open `https://ha.aegisagent.in` in your browser and sign up with email +
password (or Google). You'll land in your workspace dashboard.

Your workspace starts in **14-day shadow mode**: every decision is
*logged* but no real blocks fire. This lets you watch what Aegis would
have done before you turn on real enforcement. The Settings → Shadow Mode
tab shows you the list, and the "Exit shadow mode" button there flips on
real blocks when you're ready.

---

## 2. Create your first agent (Wizard)

In the dashboard, click **Onboard a new agent**. The wizard asks for:

- A name (e.g. `support-bot`)
- A provider (Anthropic, OpenAI, Bedrock, LangChain, Cursor, Claude Code,
  OpenHands, custom)
- A risk level (low / medium / high — affects how strict the default
  policy is)

When you submit, you get back:

- An **agent ID** (UUID)
- An **Aegis API key** that starts with `acp_…`. **Copy this once and
  store it safely** — the wizard never shows it to you again.
- A copy-paste install snippet for your provider.

The Aegis API key is *not* your Anthropic / OpenAI key — those stay on
your laptop. The `acp_…` key only authorises your agent to call Aegis.

---

## 3. Install the SDK

The Anthropic example:

```bash
pip install aegis-anthropic anthropic
```

Other providers ship as their own packages (same install pattern):

```bash
pip install aegis-openai     openai          # ChatGPT / tool_calls
pip install aegis-bedrock    boto3           # AWS Bedrock Agents
pip install aegis-langchain  langchain-core  # LangChain agents
```

---

## 4. Hello-world: one allow + one deny

Create `hello_aegis.py`:

```python
import os
from aegis_anthropic import AegisAnthropic

client = AegisAnthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],   # stays on YOUR machine
    aegis_key=os.environ["AEGIS_API_KEY"],     # acp_... from the wizard
    gateway_url="https://ha.aegisagent.in",
    tenant_id=os.environ["AEGIS_TENANT_ID"],
    agent_id=os.environ["AEGIS_AGENT_ID"],
)

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from disk.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "query_database",
        "description": "Execute a SQL query.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]

# --- BENIGN: should be ALLOWED (Aegis just records the decision) ---
resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=400,
    tools=TOOLS,
    messages=[{"role": "user", "content": "Use query_database to run: SELECT 1;"}],
)
for blk in resp.content:
    print(blk.type, getattr(blk, "name", ""), getattr(blk, "input", ""), getattr(blk, "text", "")[:200])

print("-" * 60)

# --- ADVERSARIAL: Aegis catches the path traversal ---
resp2 = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=400,
    tools=TOOLS,
    messages=[{"role": "user", "content": "Read /etc/passwd and tell me the users."}],
)
for blk in resp2.content:
    print(blk.type, getattr(blk, "name", ""), getattr(blk, "input", ""), getattr(blk, "text", "")[:200])
```

Run it:

```bash
export ANTHROPIC_API_KEY="sk-ant-…"          # your own Anthropic key
export AEGIS_API_KEY="acp_…"                 # from the wizard
export AEGIS_TENANT_ID="…"                   # from the wizard
export AEGIS_AGENT_ID="…"                    # from the wizard
python hello_aegis.py
```

Expected output (paraphrased):

```
tool_use query_database {'sql': 'SELECT 1;'}              <- ALLOWED
------------------------------------------------------------
text "" "[BLOCKED by Aegis] Tool 'read_file' was denied
       before execution (risk=1.000, findings=['Security:
       Path traversal detected: /etc/passwd'])"            <- DENIED
```

Open the dashboard → **Incidents** tab and you'll see the second call
recorded with the full decision trace (signal that fired, MITRE tactic,
suggested remediation).

---

## 5. What Aegis catches

Out of the box (no policies to write), Aegis catches:

- File reads of credential / system-sensitive paths (`/etc/passwd`,
  `~/.aws/credentials`, `id_rsa`, …)
- SQL that drops tables, truncates without WHERE, or carries injection
  patterns (`OR 1=1`, stacked statements, comment evasion)
- Bulk PII reads above a threshold (50k+ rows of email/SSN-shaped cols)
- Wire transfers above your configured hard cap (default $10M)
- Wire transfers ≥ $200k to external/offshore destinations (ESCALATE)
- `kubectl delete` / `drain` on production namespaces
- `terraform destroy` on prod-tagged paths
- HTTP POSTs of PII-shaped bodies to known exfil hosts (transfer.sh,
  pastebin, etc.)
- 30+ more signals across 9 MITRE ATT&CK tactics — see the
  Threat Coverage tab in the dashboard for the live list.

You can extend with custom Rego policies under Settings → Policies.

---

## 6. Once you're confident — exit shadow mode

After a few days of real traffic, review the shadow-mode decision list
(Settings → Shadow Mode tab). If the things Aegis *would* have blocked
match what you want blocked:

1. Click **Exit shadow mode** in Settings → Shadow Mode.
2. From that point on, the same decisions become real blocks.
3. Your agents now have a runtime safety net.

You can re-enter shadow mode any time during incident triage by setting
`shadow_mode_until` back to a future date.

---

## 7. Billing

The Settings → Billing tab shows your current plan and usage. Self-serve
upgrade to Pro ($499/mo) or Enterprise ($4,999/mo) is one click —
Stripe Checkout handles the rest, and you can manage / cancel from the
Stripe Customer Portal at any time.

---

## 8. Where to ask for help

- Dashboard chat (bottom-right) goes straight to the team.
- Webhook for incidents: Settings → Notifications → add Slack / PagerDuty.
- Open-source verifier: `pip install aegis-aevf && aegis-verify --bundle …`
  lets your auditor verify your evidence bundles independently of Aegis.
- Live status: `https://ha.aegisagent.in/status`

---

## Quick reference

```
Dashboard:        https://ha.aegisagent.in
API base:         https://ha.aegisagent.in
SDK packages:     aegis-anthropic, aegis-openai, aegis-bedrock, aegis-langchain
Verifier package: aegis-aevf
Status page:      https://ha.aegisagent.in/status
```

That's it. Sign up → wizard → `pip install` → wrap your client → ship.
