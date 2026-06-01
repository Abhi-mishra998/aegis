# Aegis ACP — 5-Minute Quickstart

From `pip install` to your first governance decision in under 5 minutes.

---

## Step 1: Create an API key (1 minute)

1. Open the Aegis dashboard → **Developer Panel** → **API Keys**
2. Click **New Key**, enter a name (e.g. `my-service`), click **Create**
3. Copy the key — it starts with `acp_` and is shown only once

```
acp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## Step 2: Choose your integration (1 minute)

Pick the package that matches your stack:

```bash
# LangChain agents
pip install aegis-langchain

# OpenAI tool_calls
pip install aegis-openai

# Anthropic / Claude tool_use
pip install aegis-anthropic

# Low-level Python (any framework)
pip install acp-client
```

---

## Step 3: Add governance (1 minute)

### LangChain

```python
from aegis_langchain import AegisMiddleware

# Wrap your existing agent — no other changes needed
agent = AegisMiddleware(
    my_agent,
    api_key="acp_YOUR_KEY",
    tenant_id="YOUR_TENANT_UUID",
)

result = agent.invoke({"input": "read the database schema"})
# Allowed tools run normally.
# Blocked tools: "[BLOCKED by Aegis] Tool 'delete_records' denied (risk=0.94)..."
```

### OpenAI

```python
from aegis_openai import AegisOpenAI

client = AegisOpenAI(
    aegis_key="acp_YOUR_KEY",
    tenant_id="YOUR_TENANT_UUID",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Export all user emails"}],
    tools=[...],
)
# Blocked tool_calls removed from response; see response._aegis_blocked
```

### Anthropic

```python
from aegis_anthropic import AegisAnthropic

client = AegisAnthropic(
    aegis_key="acp_YOUR_KEY",
    tenant_id="YOUR_TENANT_UUID",
)

response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    tools=[...],
    messages=[{"role": "user", "content": "Access /etc/passwd"}],
)
# Blocked tool_use replaced with TextBlock; see response._aegis_blocked
```

### Low-level (any framework)

```python
from acp_client import ACPClient
import asyncio

async def main():
    async with ACPClient(
        agent_id="my-agent-uuid",
        secret="YOUR_AGENT_SECRET",
        gateway_url="https://dev.aegisagent.in",
        identity_url="https://dev.aegisagent.in",
    ) as client:
        await client.authenticate(tenant_id="YOUR_TENANT_UUID")
        result = await client.execute_tool(
            tool_name="data.query",
            payload={"query": "SELECT * FROM users LIMIT 10"},
        )
        print(result["action"])   # "allow" or "deny"
        print(result["risk"])     # 0.0 – 1.0
        print(result["findings"]) # list of risk signals

asyncio.run(main())
```

---

## Step 4: See it in action (1 minute)

The audit log captures every governance decision. Open **Audit Logs** in the dashboard and you'll see your tool calls appear within seconds, with risk scores, decisions, and cryptographic receipts.

```bash
# Or via API:
curl https://dev.aegisagent.in/audit/logs \
  -H "Authorization: Bearer acp_YOUR_KEY" \
  -H "X-Tenant-ID: YOUR_TENANT_UUID" \
  | jq '.data.items[:3]'
```

---

## Step 5: Set your first policy (1 minute)

Open **Policy Builder** → create a rule:

- **Agent**: your agent
- **Tool**: `delete_*` (wildcard)
- **Action**: Deny
- **Reason**: No bulk deletes in production

From that point forward, any `delete_files`, `delete_records`, or similar tool call is blocked — automatically logged, receipted, and included in your compliance export.

---

## What's next

| Task | Where |
|---|---|
| View risk scores and trends | Observability → Risk Engine |
| Export EU AI Act evidence | Compliance → Export PDF |
| Add team members | User Management |
| Set spend caps per agent | Billing → Agent Caps |
| Set up Slack/PagerDuty alerts | Settings → Notifications |
| Schedule weekly compliance reports | Scheduled Reports |
| Engage kill switch for an agent | Kill Switch |

## Environment variables (alternative to constructor args)

```bash
export AEGIS_API_KEY="acp_YOUR_KEY"
export AEGIS_TENANT_ID="YOUR_TENANT_UUID"
export AEGIS_URL="https://dev.aegisagent.in"   # default
export AEGIS_AGENT_ID="my-agent"
```

All three integration packages read these automatically — no constructor args needed.
