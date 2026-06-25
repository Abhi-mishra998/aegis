# aegis-bedrock

> ⚠️ **Sprint 25 freeze (2026-06-26):** `aegis-bedrock` is now **maintenance-only**. The Aegis team has narrowed focus to a single hero SDK, `aegis-anthropic`, while we drive design-partner revenue. `aegis-bedrock` continues to receive **security patches** but **no new features** until further notice. The drop-in Bedrock contract below remains fully supported against the canonical `/execute` API; you can keep using it in production.

AWS Bedrock Agents governance middleware for Aegis ACP. Intercepts
`invoke_agent` action-group calls and consults Aegis `/execute` before any
tool runs — same SDK contract as `aegis-anthropic` and `aegis-openai`.

## Install

```bash
pip install "aegis-bedrock[bedrock]"
```

## Use

Drop-in replacement for `boto3.client("bedrock-agent-runtime")`:

```python
from aegis_bedrock import AegisBedrockAgentRuntime

client = AegisBedrockAgentRuntime(
    aegis_key="acp_…",
    aegis_url="https://aegisagent.in",
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<your-aegis-agent-id>",
    region_name="us-east-1",          # standard boto3 kwarg
)

response = client.invoke_agent(
    agentId="…",
    agentAliasId="…",
    sessionId="…",
    inputText="Find the customer that owes the most.",
)
```

Every Bedrock action-group invocation is checked by Aegis before the
underlying lambda fires. Blocked actions are replaced with a text-only
response explaining the denial; the buyer's Bedrock agent sees a clean
governance message instead of a side-effecting tool call.

## What Aegis governs

| Bedrock primitive | Aegis maps to | Notes |
|---|---|---|
| Action group invocation | `tool.<action_name>` | `arguments` = the JSON parameter block |
| Knowledge-base query | `tool.kb_search` | `arguments` = `{query, retrievalConfiguration}` |
| Code-interpreter call | `tool.python_exec` | `arguments.code` is the Python body |

The same per-tool ALLOW grants you've already configured for your Aegis
agent are honoured by Bedrock calls — no separate Bedrock permission
model.

## Standard wrapper guarantees

* Verdicts are pre-checked. A blocked tool never invokes the lambda /
  knowledge base / interpreter.
* HTML 403 (WAFv2) surfaces as `findings=["waf_blocked"]`, same as the
  other SDKs.
* JSON parse / network errors fail closed (`action="deny"`).
