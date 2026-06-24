# Aegis ACP — Framework Integration Packages

Four pip-installable packages that add Aegis governance to existing AI agents in 3 lines of code.
Every tool call is checked against your policies before execution. Blocked calls return a descriptive message — your agent handles it naturally without code changes.

All four packages default to the consolidated production gateway at `https://aegisagent.in`; override with `gateway_url=` / `AEGIS_URL` only for self-host.

## Packages (2026-06-24 release)

| Package | Version | Framework | What it wraps |
|---|---|---|---|
| `aegis-langchain` | `1.1.3` | LangChain | `AgentExecutor` tool `_run` / `_arun` |
| `aegis-openai` | `1.1.2` | OpenAI | `chat.completions.create()` → `tool_calls` |
| `aegis-anthropic` | `1.1.2` | Anthropic | `messages.create()` → `tool_use` blocks |
| `aegis-bedrock` | `1.1.3` | AWS Bedrock | `bedrock-agent-runtime` invoke + retrieve |

---

## aegis-langchain

```bash
pip install 'aegis-langchain==1.1.3'
# or from source:
pip install -e integrations/aegis-langchain
```

```python
from aegis_langchain import AegisMiddleware

agent = AegisMiddleware(
    my_langchain_agent,         # any AgentExecutor / Runnable
    api_key="acp_...",          # or set AEGIS_API_KEY env var
    tenant_id="...",            # or set AEGIS_TENANT_ID
    agent_id="my-agent",        # optional, defaults to "langchain-agent"
    gateway_url="https://aegisagent.in",
)

# Use exactly like the original agent
result = agent.invoke({"input": "read /etc/passwd"})
# → "[BLOCKED by Aegis] Tool 'file_read' denied (risk=0.97): ['path_traversal']"
```

### Monitor-only mode (no blocking)

```python
from aegis_langchain import AegisCallbackHandler

handler = AegisCallbackHandler(api_key="acp_...", tenant_id="...")
agent.invoke(input, config={"callbacks": [handler]})
# Every tool call logged to Aegis for observability — no enforcement
```

---

## aegis-openai

```bash
pip install 'aegis-openai==1.1.2'
```

```python
from aegis_openai import AegisOpenAI

client = AegisOpenAI(
    aegis_key="acp_...",        # or set AEGIS_API_KEY
    tenant_id="...",            # or set AEGIS_TENANT_ID
    openai_api_key="sk-...",    # or set OPENAI_API_KEY
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Delete all temp files"}],
    tools=[{"type": "function", "function": {"name": "delete_files", ...}}],
)

# Blocked tools appear in response._aegis_blocked
# Allowed tools appear in response.choices[0].message.tool_calls as normal
for block in getattr(response, "_aegis_blocked", []):
    print(f"Blocked: {block['function_name']} — {block['blocked_message']}")
```

---

## aegis-anthropic

```bash
pip install 'aegis-anthropic==1.1.2'
```

```python
from aegis_anthropic import AegisAnthropic

client = AegisAnthropic(
    api_key="sk-ant-...",       # Anthropic key (or set ANTHROPIC_API_KEY)
    aegis_key="acp_...",        # Aegis API key (or set AEGIS_API_KEY)
    tenant_id="...",            # or set AEGIS_TENANT_ID
)

response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    tools=[{"name": "bash", "description": "...", "input_schema": {...}}],
    messages=[{"role": "user", "content": "rm -rf /var/data"}],
)

# Blocked tool_use blocks are replaced with TextBlock explanations.
# Allowed tool_use blocks pass through unchanged.
# Check response._aegis_blocked for block metadata.
```

---

## Environment variables

All four packages read from the same set of env vars:

| Variable | Description |
|---|---|
| `AEGIS_API_KEY` | API key created in the Developer Panel (`acp_...`) |
| `AEGIS_TENANT_ID` | Your tenant UUID |
| `AEGIS_URL` | Gateway URL (default: `https://aegisagent.in`) |
| `AEGIS_AGENT_ID` | Agent identifier for policy evaluation |

---

## Fail-closed by default

All four packages **fail closed** if Aegis is unreachable: tool calls return `deny` with reason `aegis_unreachable_fail_closed` or `aegis_http_<code>`. Allowing unchecked tool calls through because the security plane was down defeats the whole point of the integration — it's the exact failure mode the SDK exists to prevent.

This is enforced in `aegis_anthropic/__init__.py:AegisClient.check()` and the equivalent in `aegis_openai` / `aegis_langchain` / `aegis_bedrock`. Your agent still continues — the loop just sees a denied tool result and reasons over it like any other observation.

---

## Governance flow

```
Agent calls tool
     ↓
Package intercepts
     ↓
POST /execute → Aegis gateway
     ↓ 200 (allow) or 403 (deny)
     ↓
allow → original tool runs → result returned
deny  → descriptive block message returned (tool never runs)
```

The audit log in Aegis records every check — allowed and denied — with the agent ID, tool name, risk score, and findings. Use the Compliance page to export for EU AI Act / NIST AI RMF evidence.
