# aegis-openai

> Drop-in OpenAI SDK wrapper that routes every `tool_calls` invocation through Aegis's runtime governance pipeline before execution.

[![PyPI](https://img.shields.io/badge/pypi-aegis--openai-blue)](https://pypi.org/project/aegis-openai/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-green)]()

```bash
pip install aegis-openai
```

## What it does

Wraps `openai.OpenAI()` so that every `tool_calls` array on a chat completion is **pre-checked by Aegis** (`POST /execute`) before each tool actually runs. Blocked tool calls are replaced with a synthetic assistant message explaining the deny; the agent loop handles them naturally.

Aegis decides what to block based on **action semantics** (the `DROP TABLE`, `rm -rf`, `kubectl delete`, external-PII-egress patterns in `services/policy/policies/action_semantics_deny.rego`). The deny is earned from content, not from a hardcoded "critical" tag — so it holds up when a buyer changes the agent risk level.

Every check produces a signed audit row in the Aegis chain. Verify any of them offline with `aegis-verify` (the `tools/aegis_verify/` CLI).

## Three-line install

```python
from aegis_openai import AegisOpenAI

client = AegisOpenAI(
    openai_api_key="sk-...",
    aegis_key="acp_...",      # or AEGIS_API_KEY env var
    aegis_url="https://aegisagent.in",  # or AEGIS_URL env
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<your-agent-uuid>",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Free up disk space — delete old logs."}],
    tools=[{
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Execute a shell command",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        },
    }],
)
# Each tool_call is pre-checked. Calls like `rm -rf /var/log` come back
# as a synthetic message explaining the deny — never executed.
```

## Fail-closed by default

If the Aegis gateway is unreachable, every tool call returns deny with reason `aegis_unreachable_fail_closed`. Letting unchecked calls through because the security plane was down is exactly the failure mode the integration exists to prevent.

## What you can verify offline

After any allowed (or denied) tool call:

1. `GET /receipts/key` — ed25519 PEM
2. `GET /compliance/export/eu-ai-act?period_start=…&period_end=…` — signed bundle
3. `aegis-verify --bundle bundle.json` — V1–V6 checks pass without any network call back to Aegis

## Requirements

- Python 3.10+
- `openai>=1.0` (auto-pulled if you install with `pip install "aegis-openai[openai]"`)

## See also

- [aegis-anthropic](https://pypi.org/project/aegis-anthropic/) — same pattern for Anthropic tool_use
- [aegis-langchain](https://pypi.org/project/aegis-langchain/) — same pattern for LangChain agents
- [Aegis live demo](https://aegisagent.in/live-demo) — three real scenarios across three risk profiles

## License

Apache 2.0.
