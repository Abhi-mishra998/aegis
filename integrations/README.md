# Aegis ACP — Framework Integration Packages

Three pip-installable packages that add Aegis governance to existing AI agents in 3 lines of code.
Every tool call is checked against your policies before execution. Blocked calls return a descriptive message — your agent handles it naturally without code changes.

## Packages

| Package | Framework | What it wraps |
|---|---|---|
| `aegis-langchain` | LangChain | `AgentExecutor` tool `_run` / `_arun` |
| `aegis-openai` | OpenAI | `chat.completions.create()` → `tool_calls` |
| `aegis-anthropic` | Anthropic | `messages.create()` → `tool_use` blocks |

---

## aegis-langchain

```bash
pip install aegis-langchain
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
pip install aegis-openai
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
pip install aegis-anthropic
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

All three packages read from the same set of env vars:

| Variable | Description |
|---|---|
| `AEGIS_API_KEY` | API key created in the Developer Panel (`acp_...`) |
| `AEGIS_TENANT_ID` | Your tenant UUID |
| `AEGIS_URL` | Gateway URL (default: `https://aegisagent.in`) |
| `AEGIS_AGENT_ID` | Agent identifier for policy evaluation |

---

## Fail-open behaviour

All three packages are designed to **never crash your agent**. If the Aegis gateway is unreachable or returns an unexpected status:

- The tool call is **allowed** (fail-open)
- A `findings` field of `["aegis_error:<ExceptionType>"]` is attached to the internal decision object
- Your agent continues uninterrupted

To switch to **fail-closed** (block on network error), set `fail_closed=True` in the constructor (all three packages support this flag).

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
