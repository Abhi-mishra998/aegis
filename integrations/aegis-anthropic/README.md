# aegis-anthropic

> Drop-in Anthropic SDK wrapper that routes every `tool_use` call through Aegis's runtime governance pipeline before execution.

[![PyPI](https://img.shields.io/badge/pypi-aegis--anthropic-blue)](https://pypi.org/project/aegis-anthropic/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-green)]()

```bash
pip install aegis-anthropic
```

## What it does

Wraps `anthropic.Anthropic()` so that every `tool_use` block produced by Claude is **pre-checked by Aegis** (`POST /execute`) before the tool actually runs. Blocked tool calls become text blocks that explain the denial. The Claude agent loop handles them naturally — no special error path in your code.

Aegis itself decides what to block based on action semantics (the `DROP TABLE`, `rm -rf`, `kubectl delete`, external-PII-egress patterns from `services/policy/policies/action_semantics_deny.rego`). The deny is earned from content, not from a hardcoded "critical" tag — so it survives a buyer changing the agent's risk level.

Every check produces a signed audit row in the Aegis chain. Your auditor can verify it offline with `aegis-verify` (the `tools/aegis_verify/` CLI).

## Three-line install

```python
from aegis_anthropic import AegisAnthropic

client = AegisAnthropic(
    api_key="sk-ant-...",      # Anthropic key (or ANTHROPIC_API_KEY env)
    aegis_key="acp_...",       # Aegis API key (or AEGIS_API_KEY env)
    aegis_url="https://aegisagent.in",  # or AEGIS_URL env
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<your-agent-uuid>",
)

response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=1024,
    tools=[{
        "name": "shell",
        "description": "run a shell command",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
    }],
    messages=[{"role": "user", "content": "Clean up /var/log to free disk space."}],
)
# Claude proposes tool_use → Aegis pre-checks each one → destructive
# commands become text blocks explaining the deny. Allowed calls
# return through the normal anthropic SDK path.
```

## Fail-closed by default

If the Aegis gateway is unreachable, every tool call is treated as a **deny** with reason `aegis_unreachable_fail_closed`. Letting unchecked tool calls through because the security plane was down defeats the point of the integration.

## What you can verify offline

After any allowed (or denied) tool call:

1. Pull the public key once: `GET /receipts/key` → ed25519 PEM
2. Download an evidence bundle: `GET /compliance/export/eu-ai-act?period_start=…&period_end=…`
3. Run `aegis-verify --bundle bundle.json` (the standalone CLI) — V1–V6 checks pass without any network call back to Aegis.

The same chain backs every SDK in the family (`aegis-anthropic`, `aegis-openai`, `aegis-langchain`).

## Requirements

- Python 3.10+
- `anthropic>=0.25` (install with `pip install "aegis-anthropic[anthropic]"` if you want it pulled in automatically)

## See also

- [aegis-openai](https://pypi.org/project/aegis-openai/) — same pattern for OpenAI tool_calls
- [aegis-langchain](https://pypi.org/project/aegis-langchain/) — same pattern for LangChain agents
- [Aegis live demo](https://aegisagent.in/live-demo) — three real scenarios across three risk profiles

## License

Apache 2.0.
