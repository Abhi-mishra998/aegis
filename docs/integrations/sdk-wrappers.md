# SDK Wrappers — three-line install for Anthropic / OpenAI / Bedrock / LangChain

> Drop-in wrappers that route every tool call from an existing agent through Aegis's runtime governance pipeline before execution. All four packages published on PyPI under Apache 2.0; the latest line is **v1.1.0** (2026-06-17).

## The four packages

| Package | Framework | What it wraps | PyPI |
|---|---|---|---|
| **`aegis-anthropic`** | Anthropic Python SDK | `messages.create()` → `tool_use` blocks | [pypi.org/project/aegis-anthropic/1.1.0/](https://pypi.org/project/aegis-anthropic/1.1.0/) |
| **`aegis-openai`** | OpenAI Python SDK | `chat.completions.create()` → `tool_calls` | [pypi.org/project/aegis-openai/1.1.0/](https://pypi.org/project/aegis-openai/1.1.0/) |
| **`aegis-bedrock`** | AWS Bedrock Agents (`boto3` `bedrock-agent-runtime`) | `invoke_agent()` tool actions | [pypi.org/project/aegis-bedrock/1.1.0/](https://pypi.org/project/aegis-bedrock/1.1.0/) |
| **`aegis-langchain`** | LangChain | `AgentExecutor` / `Runnable` tool invocations | [pypi.org/project/aegis-langchain/1.1.0/](https://pypi.org/project/aegis-langchain/1.1.0/) |

## Supported provider versions

| Package | Python | Wrapped provider |
|---|---|---|
| `aegis-anthropic` 1.1.0 | ≥ 3.10 | `anthropic ≥ 0.25` |
| `aegis-openai` 1.1.0 | ≥ 3.10 | `openai ≥ 1.0` |
| `aegis-bedrock` 1.1.0 | ≥ 3.10 | `boto3 ≥ 1.34` (with `bedrock-agent-runtime`) |
| `aegis-langchain` 1.1.0 | ≥ 3.10 | `langchain-core ≥ 0.1` |

## Install — pinned

```bash
pip install aegis-anthropic==1.1.0
pip install aegis-openai==1.1.0
pip install aegis-bedrock==1.1.0
pip install aegis-langchain==1.1.0
```

All four depend only on `httpx>=0.25`. None pull the underlying provider SDK by default (so the package install stays small). Install with the matching extra to auto-pull the provider:

```bash
pip install "aegis-anthropic[anthropic]==1.1.0"
pip install "aegis-openai[openai]==1.1.0"
pip install "aegis-bedrock[bedrock]==1.1.0"
pip install "aegis-langchain[langchain]==1.1.0"
```

## Two integration paths

The four packages support both deployment styles, and most teams will use both — Path A for the runtime-governance hot path, Path B for legacy callers that already speak OpenAI/Anthropic HTTP without changing imports.

- **Path A — SDK wrapper.** Replace the provider client class with the Aegis class; every tool call routes through `/execute` before the upstream provider sees it. Fail-closed by default.
- **Path B — drop-in proxy.** Point the provider client at `https://aegisagent.in/v1` (or `https://aegisagent.in/v1/messages` for Anthropic). No code change, no extra import. The gateway proxies the body to the upstream provider and enforces the same `/execute` pipeline on each tool call.

Long-form recipes for both paths live in `setup-agies.md` at the repo root — that doc is the authoritative install runbook (terraform module, env vars, ASG settings). This page is the API surface summary.

## Path A — SDK wrapper (recommended for new agents)

### Anthropic

```python
from aegis_anthropic import AegisAnthropic

client = AegisAnthropic(
    api_key="sk-ant-...",
    aegis_key="acp_emp_...",
    aegis_url="https://aegisagent.in",
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
    aegis_key="acp_emp_...",
    aegis_url="https://aegisagent.in",
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<agent-uuid>",
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Delete all temp files"}],
    tools=[{"type": "function", "function": {"name": "run_shell", "parameters": {...}}}],
)
# Blocked tool_calls are stripped from the response and surfaced
# under response._aegis_blocked with the deny reason.
```

### Bedrock

```python
from aegis_bedrock import AegisBedrockClient

client = AegisBedrockClient(
    aegis_key="acp_emp_...",
    aegis_url="https://aegisagent.in",
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<agent-uuid>",
    region_name="us-east-1",
)

response = client.invoke_agent(
    agentId="<bedrock-agent-id>",
    agentAliasId="<alias>",
    sessionId="<session-uuid>",
    inputText="Clean up old EC2 snapshots",
)
# Each Bedrock tool action is pre-checked; blocked actions are returned
# to Bedrock as a tool error so the agent can reason over the deny.
```

### LangChain

```python
from aegis_langchain import AegisMiddleware

agent = AegisMiddleware(
    my_langchain_agent,          # any AgentExecutor / Runnable
    api_key="acp_emp_...",
    aegis_url="https://aegisagent.in",
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<agent-uuid>",
)

result = agent.invoke({"input": "read /etc/passwd"})
# → "[BLOCKED by Aegis] Tool 'file_read' denied (risk=0.97): ['path_traversal']"
```

## Path B — drop-in proxy (zero code change)

The gateway hosts a transparent compatibility layer that speaks the OpenAI and Anthropic HTTP contracts. Existing agents that already call `openai.OpenAI()` or `anthropic.Anthropic()` only need their `base_url` (or equivalent) pointed at Aegis. The gateway terminates the request, runs the 11-stage pipeline on each tool call, and forwards the redacted/approved payload to the upstream provider.

### OpenAI proxy

```python
import openai

client = openai.OpenAI(
    api_key="acp_emp_...",
    base_url="https://aegisagent.in/v1",
)
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "..."}],
)
```

The `api_key` field carries the Aegis API key (`acp_emp_…`), not the OpenAI key — the gateway holds the OpenAI key server-side so the upstream credential never leaves your infrastructure. The `base_url` is the only line you change.

### Anthropic proxy

```python
import anthropic

client = anthropic.Anthropic(
    api_key="acp_emp_...",
    base_url="https://aegisagent.in/v1",
)
resp = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    messages=[{"role": "user", "content": "..."}],
)
```

Same pattern: `base_url` swap + Aegis key. The gateway maps `/v1/messages` to the upstream Anthropic endpoint and runs every `tool_use` block through `/execute`.

> The `/v1` namespace is a stable alias for every Aegis endpoint (`services/gateway/main.py::_v1_prefix_alias`). `https://aegisagent.in/v1/execute` and `https://aegisagent.in/execute` resolve to the same handler. Customers should pin `/v1`; the unversioned forms exist for the dashboard.

## The `X-Aegis-Approval-ID` header contract

When a decision returns `403 approval_required`, the operator approves the action via the Approval Inbox UI (or `POST /autonomy/approvals/{key}/approve`). The approval payload includes a one-time **approval ID**. The original SDK caller replays the same tool call with the header:

```
X-Aegis-Approval-ID: <approval_uuid>
```

The gateway looks up the approval against a **5-minute TTL** key in Redis (`acp:approval:{tenant_id}:{approval_id}`). If the approval is still valid and the **policy version** at approval time matches the **policy version** at replay time, the gateway short-circuits the deny — the call passes through with the same risk score it had at evaluation, recorded as `decision=allow_approved` on the audit row.

If the policy version changed between approval and replay (e.g. an operator promoted a stricter Rego file in the 5-minute window), the approval is invalidated and the deny stands. The risk a stale approval slips through a tightened policy is the failure mode we refuse to ship; the same operator who approved the action must re-approve under the new policy version.

All four SDKs accept the approval ID on the next call. The pattern is the same across packages — set the header on the constructor or the per-call kwarg:

```python
# Anthropic — per-call
client.messages.create(
    model="claude-opus-4-7",
    messages=[...],
    tools=[...],
    extra_headers={"X-Aegis-Approval-ID": approval_id},
)

# OpenAI — per-call
client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[...],
    extra_headers={"X-Aegis-Approval-ID": approval_id},
)

# Bedrock — per-call kwarg on AegisBedrockClient
client.invoke_agent(
    agentId="<id>", sessionId="<sid>", inputText="...",
    aegis_approval_id=approval_id,
)

# LangChain — passed when invoking the AegisMiddleware-wrapped agent
agent.invoke({"input": "...", "aegis_approval_id": approval_id})
```

The header travels through Path A and Path B identically — `aegis_approval_id` constructor kwargs simply set the header on every outbound `/execute` call.

## Environment variables (all four packages)

| Variable | Description |
|---|---|
| `AEGIS_API_KEY` | API key created in the Developer Panel (`acp_emp_…`) |
| `AEGIS_TENANT_ID` | Your tenant UUID |
| `AEGIS_URL` | Gateway URL (default: `https://aegisagent.in`) |
| `AEGIS_AGENT_ID` | Agent identifier for policy evaluation |

Explicit constructor arguments override env vars.

## Fail-closed by default

All four packages **fail closed** if the Aegis gateway is unreachable. Tool calls return `deny` with reason `aegis_unreachable_fail_closed` or `aegis_http_<code>`. Letting unchecked tool calls through because the security plane was down defeats the whole point of the integration — it is the exact failure mode the SDKs exist to prevent.

This is enforced in `aegis_anthropic/__init__.py::AegisClient.check()` and the equivalent in the other three packages. Your agent loop still continues; it sees a denied tool result and reasons over it like any other observation.

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
- `integrations/aegis-bedrock/`
- `integrations/aegis-langchain/`

Each package's source is ~200 LOC of Python with one runtime dependency (`httpx`). Read it in fifteen minutes; fork it if you want to.

## See also

- `setup-agies.md` at the repo root — long-form install runbook covering both Path A (SDK wrapper) and Path B (proxy) including terraform and env-var bootstrap.
- [Evidence Export Adapters](evidence-export.md) — how the audit rows the SDK produces are surfaced through SIEM (Splunk/Datadog/Sentinel/Chronicle/Elastic) and GRC (Vanta/Drata) with the AEVF back-reference attached.
- [AEVF Overview](../AEVF/README.md) — the open standard the audit rows conform to.
- [API Reference](../api/reference.md) — the underlying `/execute` contract the SDKs call.
- [Error Codes](../api/error-codes.md) — what every status code from the gateway means, with sample bodies.
