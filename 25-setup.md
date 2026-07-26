# Aegis — Client Setup Guide

**Live site:** [https://aegisagent.in](https://aegisagent.in)
**API base:** `https://aegisagent.in`
**Region:** AWS `ap-south-1` (Mumbai) — Multi-AZ Postgres + Redis replication + WAFv2 + ed25519-signed audit chain
**Status page:** `https://aegisagent.in/status`

Aegis is a runtime security control plane for AI agents. It sits in front of every tool call and every LLM prompt your agents make, applies a 10-layer policy pipeline, and produces a cryptographically verifiable audit trail. Every request is authenticated, risk-scored, policy-checked, PII-scanned, and logged before it executes.

---

## 1. What Aegis protects

- **Tool calls** (via `POST /execute` or the `@acp.protect` SDK decorator)
- **LLM proxy** (via `POST /v1/messages` with the Anthropic SDK interface — same headers, same schema)
- **Multi-provider** — Anthropic, OpenAI, LangChain, Bedrock all supported via dedicated wrappers
- **Cross-tenant isolation** — employee virtual keys are scoped to their owning tenant; the gateway blocks cross-tenant header manipulation
- **Cost caps** — per-tenant `max_tokens` ceiling (default 2048) + per-employee daily/monthly budgets

---

## 2. Sign up and get your keys

1. **Log in at [https://aegisagent.in](https://aegisagent.in)** — sign up with your work email (email/password via Clerk).
2. **Create your workspace / tenant** — the wizard walks you through role assignment and initial policy pack selection.
3. **Mint an employee virtual key** at Settings → Team → "Mint API Key". Format: `acp_emp_…`. This is what your agents send on every request.
4. **Register your first agent** at Agents → New Agent. Give it a name, a description, and the list of tools it's allowed to invoke (whitelist).

> The employee virtual key is what your agent processes send on every call. Keep it in an environment variable, never in code.

---

## 3. Install the SDK

Pick your provider — all 5 are on PyPI and pinned to the versions our production tests certified today:

```bash
pip install 'aegis-anthropic==1.1.5'    # Anthropic Claude
pip install 'aegis-openai==1.1.6'       # OpenAI GPT
pip install 'aegis-langchain==1.1.7'    # LangChain
pip install 'aegis-bedrock==1.1.7'      # AWS Bedrock

# The audit verification tool for regulators / compliance:
pip install 'aegis-aevf==1.1.1'
```

> These pins include the 2026-07-26 WAF-compatibility fix (Mozilla-shaped User-Agent). If you're on an older version, `pip install --upgrade aegis-anthropic aegis-openai aegis-langchain aegis-bedrock`.

---

## 4. Quick start — 5 lines to protect a tool

### Pattern A — the `@acp.protect` decorator (custom tools)

```python
from sdk.acp_client import Client, DeniedError

acp = Client()  # reads ACP_API_KEY + ACP_BASE_URL from env

@acp.protect(agent_id="agent_42", tool="db.query")
def query(sql: str) -> list[dict]:
    return db.execute(sql)  # only runs if allowed

@acp.protect(agent_id="agent_42", tool="shell.exec")
def run_shell(cmd: str) -> dict:
    return {"output": subprocess.check_output(cmd)}

query("SELECT * FROM customers LIMIT 1")   # allowed
try:
    run_shell("rm -rf /")
except DeniedError as exc:
    log.warning("denied: %s", exc)          # findings: ["dangerous_code_pattern"]
```

### Pattern B — `guard()` mode (for framework-dispatched tools like LangChain, AutoGen, CrewAI)

```python
decision = acp.guard(
    tool="read_file",
    parameters={"path": request.path},
    tokens=200,
    task="analyst workflow step 3",
)
# Raises PermissionError on deny; returns decision dict on allow
result = open(request.path).read()
```

### Pattern C — LLM proxy (Anthropic example, full prompt scanning)

Use `AegisAnthropicProxy` to route every prompt through Aegis's `/v1/messages` gate. This is the drop-in wrapper that gets prompt-injection, PII, and cost-cap scanning on every call.

```python
from aegis_anthropic import AegisAnthropicProxy

client = AegisAnthropicProxy(
    employee_key=os.environ["AEGIS_EMPLOYEE_KEY"],   # acp_emp_…
    gateway_url="https://aegisagent.in",              # or set AEGIS_URL env
)

# Same interface as the official Anthropic SDK.
# Every message is scanned for prompt injection, PII, cost cap, etc.
# BEFORE it reaches Anthropic. Blocked calls raise httpx.HTTPStatusError.
resp = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Summarize this doc: ..."}],
)
print(resp["content"][0]["text"])
```

You do NOT pass an Anthropic API key here — the gateway holds the upstream credential per tenant (set via `aws ssm put-parameter --name /aegis-prodha/anthropic/upstream-key`). The employee key is what identifies the caller.

If you want an in-process wrapper that only gates **tool calls** (not prompts), use `AegisAnthropic` instead — but then you also need `pip install anthropic` and your own `ANTHROPIC_API_KEY`, and prompt scanning is NOT applied.

The OpenAI, LangChain, and Bedrock wrappers follow the same Proxy pattern — check `pip show aegis-<provider>` for the class name.

---

## 5. What Aegis will block for you (verified 2026-07-26 against live prod)

| Attack class | Result | Latency |
|---|---|---|
| Prompt injection ("Ignore all previous instructions", DAN, OMEGA, "reveal system prompt") | ✅ 403 blocked at gate | ~140 ms |
| Zero-width unicode obfuscation | ✅ 403 (normalized then matched) | ~150 ms |
| SSN, credit card (Luhn-verified), API keys, private-key material in prompt | ✅ 400 `pii_in_prompt` | ~150 ms |
| `max_tokens: 4000` cost bomb | ✅ 400 `exceeds ceiling` | ~140 ms |
| Cross-tenant header manipulation | ✅ 403 `header does not match` | ~130 ms |
| Bulk PII SQL query patterns | ✅ 202 pending CISO approval (Category B escalation) | ~240 ms |
| Path traversal to `/root/.aws/credentials` | ✅ 403 blocked at input validation | ~90 ms |
| SQL injection in tool parameters | ✅ 403 detected + blocked | in-line |
| Runaway agent (50+ failed calls in 5 min) | ✅ Auto-quarantined for 24 h | in-line |
| Runaway loop, kill switch, rate limit, model whitelist | ✅ All enforced | 50-530 ms |

**Aegis-gate catch rate on our brutal test suite: 100%** (13/13 LLM attacks blocked before touching your Anthropic bill; 11/11 tool-call attacks blocked at the gate).

---

## 6. Monitor what Aegis is doing

- **Dashboard:** `https://aegisagent.in/dashboard` — live decision feed, threat rollup, posture score
- **Live SOC feed:** `/live-feed` — real-time SSE stream of every decision
- **Incidents:** `/incidents` — triaged security events with drill-down + collusion cluster detector
- **Agent profile:** `/agents/<id>` — full history, risk timeline, blast-radius, provenance
- **Audit logs:** `/audit-logs` — chain-verifiable log with in-browser "Verify Integrity" button
- **Compliance:** `/compliance` — AEVF v3 evidence bundle export + destruction-certificate download + signing-key history
- **Identity graph:** `/identity-graph` — compromise simulation, blast-radius analysis

For programmatic access, every UI page consumes the same REST API you can hit directly with a bearer token.

---

## 7. Configuration knobs

| What | Where | Default |
|---|---|---|
| `max_tokens` ceiling per LLM call | `MAX_TOKENS_CEILING` env or `/settings?tab=feature-flags` | 2048 |
| Input prompt char cap | `MAX_INPUT_CHARS` env | 24000 |
| Rapid-fire drip threshold | `LLM_DRIP_THRESHOLD` env | 20 per 60s |
| Rate limit per tenant | Settings → Quota | 10 rps default |
| Daily / monthly employee budget | Settings → Team → per-key budget | disabled by default |
| Consistency sampling (ATF §9.3 — 3× planner cost, C3 actions only) | `/settings?tab=feature-flags` toggle | off (opt-in) |
| Behavior fingerprinting (§9.2, advisory-only) | `/settings?tab=feature-flags` toggle | off (opt-in) |
| Kill switch (emergency isolation) | `/kill-switch` — one click, tenant-wide | disengaged |

---

## 8. Cost expectations

- **Aegis is open source and free** — clone, run, self-host. No paid tier, no license fees.
- **Aegis blocks cost you $0 upstream** — attacks never reach Anthropic/OpenAI, no tokens billed
- **Aegis allows add ~200-400 ms** on top of upstream LLM latency for the pre-checks
- **Self-hosted infra baseline:** ~$260/mo on AWS (2× m6g.large + RDS Multi-AZ + ElastiCache + ALB + WAF). Free on local Docker Compose.

Example cost saving: a `max_tokens: 4000` prompt-injection attack at Anthropic Opus pricing would burn **~$3 per attack** without Aegis. With Aegis: $0, blocked in 140 ms.

---

## 9. Verifiable compliance

- **Every decision** is signed with ed25519 and chained per shard (16 shards per tenant)
- **Daily Merkle root** anchors the chain publicly at `s3://aegis-public-roots-628478946931/`
- **Regulator can verify offline** with the open-source AEVF tool — no Aegis API call needed:

```bash
pip install 'aegis-aevf==1.1.1'
aegis-verify --bundle exported_bundle.json
# → V1-V6 pass/fail summary
```

- **Destruction certificate** on tenant termination — signed proof of what existed and when it was destroyed (kept forever by the customer)

---

## 10. What's NOT included and needs your own setup

- **Your LLM API keys** — Anthropic/OpenAI/Groq/Bedrock keys are yours; Aegis proxies to them but doesn't provide them
- **Your identity provider** — Aegis accepts SPIFFE / Entra Agent ID / Okta XAA workload tokens for agent auth (config via env vars, ops-controlled)
- **Slack/PagerDuty/Teams webhooks** — configure per tenant at `/settings?tab=webhooks` for escalation alerts
- **Custom OPA policies** — write Rego at `/policies` (visual builder) or upload directly

---

## 11. Support + limits

- **Rate limit default:** 10 rps per tenant, 6000/min. Raise by patching `acp_identity.tenants.requests_per_second` in your DB.
- **Audit retention:** 10 years on the cryptographic chain, 90 days on operational logs. Configurable per tenant.
- **Documentation:** [https://github.com/Abhi-mishra998/aegis](https://github.com/Abhi-mishra998/aegis) (full technical reference)
- **Blog:** [projectsphere.hashnode.dev — I built a runtime firewall for AI agents](https://projectsphere.hashnode.dev/i-built-a-runtime-firewall-for-ai-agents)
- **Issues / security disclosures:** email `founder@aegisagent.in`

---

## 12. Two-minute smoke test (post-signup)

Verify your keys work end-to-end:

```bash
export AEGIS_EMPLOYEE_KEY="acp_emp_…"
export AEGIS_TENANT_ID="your-tenant-uuid"

# 1. Register a test agent (needs OWNER/ADMIN token — use the UI, or your admin JWT)
curl -sk -X POST https://aegisagent.in/agents \
  -H "Authorization: Bearer <owner-jwt>" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-first-agent","description":"first test","owner_id":"me","risk_level":"low"}'

# 2. Add a tool to its allow-list
# NOTE: Steps 1 + 2 require an OWNER/ADMIN token, not an employee key.
# Use the UI (Agents → New Agent + Permissions tab) for these two steps.
# Only Step 3 (/execute) and Step 4 (attack block) use the employee key.
curl -sk -X POST https://aegisagent.in/agents/<agent-id>/permissions \
  -H "Authorization: Bearer <owner-jwt>" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"search_web","action":"ALLOW","granted_by":"me"}'

# 3. Fire an /execute
curl -sk -X POST https://aegisagent.in/execute \
  -H "x-api-key: $AEGIS_EMPLOYEE_KEY" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"<agent-id>","tool":"search_web","parameters":{"query":"hello aegis"}}'
# → 200 with action=allow, real request_id, risk score

# 4. Try an attack — should get blocked
# `db.query` isn't in the agent's allow-list from step 2, so the deny
# fires at the allow-list check ("Tool not in agent's allow-list").
# To specifically trigger SQL-injection detection, add db.query to the
# allow-list first, then send the DROP TABLE payload.
curl -sk -X POST https://aegisagent.in/execute \
  -H "x-api-key: $AEGIS_EMPLOYEE_KEY" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"<agent-id>","tool":"db.query","parameters":{"sql":"DROP TABLE users"}}'
# → 403 Security: Tool 'db.query' not in agent's allow-list
```

If both work, you're live.

---

## 13. Rollback

- **Emergency stop:** engage the kill switch at `/kill-switch` — every request from your tenant returns 403 immediately. Reversible with one click.
- **Feature flag reset:** `/settings?tab=feature-flags` → toggle any experimental flag off.
- **Full tenant termination:** `/lifecycle` → DECOMMISSION → DESTROY. Downloads a signed destruction certificate you keep forever.

---

**Aegis, self-hosted in ap-south-1, 2× m6g.large behind ALB, RDS Multi-AZ, ElastiCache Redis, ed25519 audit chain. Every attack path in Section 5 blocked live today. Ship.**
