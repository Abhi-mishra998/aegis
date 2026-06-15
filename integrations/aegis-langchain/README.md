# aegis-langchain

> Drop-in LangChain middleware that routes every tool invocation through Aegis's runtime governance pipeline before execution.

[![PyPI](https://img.shields.io/badge/pypi-aegis--langchain-blue)](https://pypi.org/project/aegis-langchain/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-green)]()

```bash
pip install aegis-langchain
```

## What it does

Wraps a LangChain agent (`AgentExecutor` / `Runnable`) so every tool the agent decides to call is **pre-checked by Aegis** (`POST /execute`) before the tool actually runs. Blocked calls return a descriptive message back to the agent loop; allowed calls pass through unchanged.

Aegis decides what to block based on **action semantics** — the `DROP TABLE`, `rm -rf`, `kubectl delete`, external-PII-egress patterns in `services/policy/policies/action_semantics_deny.rego`. The deny is earned from the content of the action, not from a hardcoded "critical" tag on the agent. So a buyer flipping the agent's risk level can't accidentally bypass it.

Every check produces a signed audit row in the Aegis chain. Verify any of them offline with `aegis-verify` (the `tools/aegis_verify/` CLI).

## Three-line install

```python
from aegis_langchain import AegisMiddleware

agent = AegisMiddleware(
    my_langchain_agent,
    api_key="acp_...",        # or AEGIS_API_KEY env var
    aegis_url="https://ha.aegisagent.in",   # or AEGIS_URL env
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="<your-agent-uuid>",
)

result = agent.invoke({"input": "analyze the customer table and clean up old rows"})
# Each tool invocation is pre-checked. Blocked tools return a message
# explaining the deny; the agent reasons over it like any other tool
# observation.
```

Works with any LangChain agent that uses tools (`AgentExecutor`, structured-chat agents, custom runnables that invoke tools via `tool_call`).

## Fail-closed by default

If the Aegis gateway is unreachable, tool invocations return deny with reason `aegis_unreachable_fail_closed`. Letting unchecked calls through because the security plane was down defeats the integration's purpose.

## What you can verify offline

After any allowed (or denied) tool call:

1. `GET /receipts/key` — ed25519 PEM
2. `GET /compliance/export/eu-ai-act?period_start=…&period_end=…` — signed bundle
3. `aegis-verify --bundle bundle.json` — V1–V6 checks pass without any network call back to Aegis

## Requirements

- Python 3.10+
- `langchain-core>=0.1` (auto-pulled with `pip install "aegis-langchain[langchain]"`)

## See also

- [aegis-anthropic](https://pypi.org/project/aegis-anthropic/) — same pattern for Anthropic tool_use
- [aegis-openai](https://pypi.org/project/aegis-openai/) — same pattern for OpenAI tool_calls
- [Aegis live demo](https://ha.aegisagent.in/live-demo) — three real scenarios across three risk profiles

## License

Apache 2.0.
