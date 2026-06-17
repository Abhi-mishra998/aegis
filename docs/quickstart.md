# Aegis ACP — 5-Minute Quickstart

From `pip install` to your first governance decision in under 5 minutes.

For the full client onboarding narrative — wizard walkthrough, red-team
script, dashboard tour, Path B employee-key flow, shadow-mode rollout
— follow [`setup-agies.md`](../setup-agies.md) at the repo root. This
page is the "first call" path only.

---

## Step 1: Create an API key (1 minute)

1. Sign up at `https://aegisagent.in` and land in your workspace dashboard.
2. Click **Onboard a new agent** (or **Developer Panel → API Keys**).
3. Enter a name (e.g. `my-service`) and click **Create**.
4. Copy the key — it starts with `acp_` and is shown only once.

```
acp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

The wizard also returns your **agent ID** (UUID) and **tenant ID**.
Keep all three handy.

---

## Step 2: Choose your integration (1 minute)

Pick the package that matches your stack. All four SDKs are pinned at
**v1.1.0** on PyPI:

```bash
# LangChain agents
pip install aegis-langchain==1.1.0

# OpenAI tool_calls
pip install aegis-openai==1.1.0

# Anthropic / Claude tool_use
pip install aegis-anthropic==1.1.0

# AWS Bedrock Agents
pip install aegis-bedrock==1.1.0

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
    gateway_url="https://aegisagent.in",
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
    gateway_url="https://aegisagent.in",
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
    gateway_url="https://aegisagent.in",
)

response = client.messages.create(
    model="claude-haiku-4-5",
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
        gateway_url="https://aegisagent.in",
        identity_url="https://aegisagent.in",
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

The audit log captures every governance decision. Open **Audit Logs**
in the dashboard and you'll see your tool calls appear within seconds,
with risk scores, decisions, and cryptographic receipts.

```bash
# Or via API:
curl https://aegisagent.in/audit/logs \
  -H "Authorization: Bearer acp_YOUR_KEY" \
  -H "X-Tenant-ID: YOUR_TENANT_UUID" \
  | jq '.data.items[:3]'
```

---

## Step 5: Set your first policy (1 minute)

Open **Protect → Policies** → create a rule:

- **Agent**: your agent
- **Tool**: `delete_*` (wildcard)
- **Action**: Deny
- **Reason**: No bulk deletes in production

From that point forward, any `delete_files`, `delete_records`, or
similar tool call is blocked — automatically logged, receipted, and
included in your compliance export.

---

## Where next

The 5-minute path stops here. For the full hands-on narrative — the
Path A vs Path B decision tree, the eight-attack red-team script, the
approval-inbox replay flow, exit-shadow-mode rollout, the dashboard
tour, and the auditor-verifiable evidence bundle — follow
[`setup-agies.md`](../setup-agies.md). For the verified end-to-end
test matrix that ships with each release, see
[`final-testing.md`](../final-testing.md).

## Environment variables (alternative to constructor args)

```bash
export AEGIS_API_KEY="acp_YOUR_KEY"
export AEGIS_TENANT_ID="YOUR_TENANT_UUID"
export AEGIS_URL="https://aegisagent.in"   # default
export AEGIS_AGENT_ID="my-agent"
```

All four integration packages read these automatically — no
constructor args needed.

> The clean URL `https://aegisagent.in` is the canonical endpoint. The
> `https://ha.aegisagent.in` alias points at the same backend and
> remains valid for historical scripts.
