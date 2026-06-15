# SDK Wrappers — three-line install for Anthropic / OpenAI / LangChain

> Drop-in wrappers that route every tool call from an existing agent through Aegis's runtime governance pipeline before execution. All three published on PyPI on 2026-06-14, Apache 2.0.

## The three packages

| Package | Framework | What it wraps | PyPI |
|---|---|---|---|
| **`aegis-anthropic`** | Anthropic Python SDK | `messages.create()` → `tool_use` blocks | [pypi.org/project/aegis-anthropic/](https://pypi.org/project/aegis-anthropic/) |
| **`aegis-openai`** | OpenAI Python SDK | `chat.completions.create()` → `tool_calls` | [pypi.org/project/aegis-openai/](https://pypi.org/project/aegis-openai/) |
| **`aegis-langchain`** | LangChain | `AgentExecutor` / `Runnable` tool invocations | [pypi.org/project/aegis-langchain/](https://pypi.org/project/aegis-langchain/) |

## Install

```bash
pip install aegis-anthropic     # or aegis-openai / aegis-langchain
```

All three depend only on `httpx>=0.25`. None pull the underlying provider SDK by default (so the package install stays small); install with the extra to get it auto-pulled:

```bash
pip install "aegis-anthropic[anthropic]"
pip install "aegis-openai[openai]"
pip install "aegis-langchain[langchain]"
```

## Three-line install pattern

### Anthropic

```python
from aegis_anthropic import AegisAnthropic

client = AegisAnthropic(
    api_key="sk-ant-...",
    aegis_key="acp_...",
    aegis_url="https://ha.aegisagent.in",
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<agent-uuid>",
)

response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    tools=[{"name": "shell", "input_schema": {...}}],
    messages=[{"role": "user", "content": "Clean up /var/log"}],
)
# Each tool_use block is pre-checked. Blocked calls become text blocks
# explaining the deny; allowed calls return through the normal SDK path.
```

### OpenAI

```python
from aegis_openai import AegisOpenAI

client = AegisOpenAI(
    openai_api_key="sk-...",
    aegis_key="acp_...",
    aegis_url="https://ha.aegisagent.in",
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<agent-uuid>",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Delete all temp files"}],
    tools=[{"type": "function", "function": {"name": "run_shell", "parameters": {...}}}],
)
# Blocked tool_calls are replaced with a synthetic assistant message
# explaining the deny.
```

### LangChain

```python
from aegis_langchain import AegisMiddleware

agent = AegisMiddleware(
    my_langchain_agent,         # any AgentExecutor / Runnable
    api_key="acp_...",
    aegis_url="https://ha.aegisagent.in",
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<agent-uuid>",
)

result = agent.invoke({"input": "read /etc/passwd"})
# → "[BLOCKED by Aegis] Tool 'file_read' denied (risk=0.97): ['path_traversal']"
```

## Environment variables (all three packages)

| Variable | Description |
|---|---|
| `AEGIS_API_KEY` | API key created in the Developer Panel (`acp_…`) |
| `AEGIS_TENANT_ID` | Your tenant UUID |
| `AEGIS_URL` | Gateway URL (default: `https://ha.aegisagent.in`) |
| `AEGIS_AGENT_ID` | Agent identifier for policy evaluation |

You can pass any of these as constructor arguments instead — explicit constructor args override env vars.

## Fail-closed by default

All three packages **fail closed** if the Aegis gateway is unreachable. Tool calls return `deny` with reason `aegis_unreachable_fail_closed` or `aegis_http_<code>`. Letting unchecked tool calls through because the security plane was down defeats the whole point of the integration — it is the exact failure mode the SDKs exist to prevent.

This is enforced in `aegis_anthropic/__init__.py::AegisClient.check()` and the equivalent in the other two packages. Your agent loop still continues; it just sees a denied tool result and reasons over it like any other observation.

## What ends up in your audit chain

For each tool call made through any of these SDKs:

1. The wrapper calls `POST /execute` on the gateway with `{agent_id, tool, arguments}`.
2. The gateway runs the 11-stage pipeline (auth → policy → behavior → decision → autonomy → execute → output-filter → audit).
3. A signed audit row is written for every call — allowed and denied.
4. The row appears in the next AEVF bundle for the period.
5. Any auditor can verify it offline via `aegis-verify`. See [AEVF Overview](../AEVF/README.md).

## Source

- `integrations/aegis-anthropic/`
- `integrations/aegis-openai/`
- `integrations/aegis-langchain/`

Each package's source is ~200 LOC of Python with one runtime dependency (`httpx`). Read it in fifteen minutes; fork it if you want to.

## Compatibility

| Package | Python | Wrapped provider |
|---|---|---|
| `aegis-anthropic` 1.0.0 | ≥ 3.10 | `anthropic>=0.25` |
| `aegis-openai` 1.0.0 | ≥ 3.10 | `openai>=1.0` |
| `aegis-langchain` 1.0.0 | ≥ 3.10 | `langchain-core>=0.1` |

## See also

- [Evidence Export Adapters](evidence-export.md) — how the audit rows the SDK produces are surfaced through SIEM (Splunk/Datadog/Sentinel/Chronicle/Elastic) and GRC (Vanta/Drata) with the AEVF back-reference attached
- [AEVF Overview](../AEVF/README.md) — the open standard the audit rows conform to
- [API Reference](../api/reference.md) — the underlying `/execute` contract the SDKs call
