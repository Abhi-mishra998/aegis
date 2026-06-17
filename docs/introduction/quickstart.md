# Quickstart

**Sign up, wrap one tool call, and watch the signed audit row land —
in under five minutes.** This is the "first call" path; the full
client onboarding narrative (wizard, red-team script, dashboard tour,
shadow-mode rollout) lives in
[`setup-agies.md`](../../setup-agies.md) at the repo root.

This page targets the public deployment at `https://aegisagent.in`.
For a local install, swap the host for `http://localhost:8000` (or run
`infra/minimal/docker-compose.minimal.yml` — see
[Deployment](../operations/deployment.md) for the 3-container
self-host shape).

## Three install paths

| You want to … | Use this |
|---|---|
| Wrap an existing Anthropic / OpenAI / Bedrock / LangChain agent in three lines of code | `pip install aegis-anthropic==1.1.0` (or `aegis-openai==1.1.0` / `aegis-bedrock==1.1.0` / `aegis-langchain==1.1.0`) — see [SDK Wrappers](../integrations/sdk-wrappers.md) |
| Verify an evidence bundle as an auditor, offline | `pip install aegis-aevf`, then `aegis-verify --bundle bundle.json` — see [AEVF Overview](../AEVF/README.md) |
| Drive the full HTTP API from a custom integration | Follow the curl steps below, then [API Reference](../api/reference.md) |

## Prerequisites

- `curl` and `jq` on the command line.
- A workspace on `https://aegisagent.in`. Sign up with email + password
  or Google; you'll land in a personal workspace with an OWNER role
  and a 14-day shadow window.
- An Aegis API key (`acp_…`) from **Onboard a new agent** or
  **Developer Panel → API Keys**, plus your tenant ID and agent ID
  from the same wizard.

```bash
HOST=https://aegisagent.in
ACP_KEY="acp_..."                # from the wizard, shown once
TENANT="..."                     # your tenant UUID
AGENT_ID="..."                   # your agent UUID
```

The tenant header is the value of the `tenants.tenant_id` column,
**not** the row primary key — see `architecture/data-model.md` for the
split. Treat `ACP_KEY` like a password; never paste it into a shared
shell.

## 1. Confirm authentication

```bash
curl -sS "$HOST/auth/me" \
  -H "Authorization: Bearer $ACP_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  | jq
```

A healthy response returns `role`, `tenant_id`, `email`, and
`user_id`. If you get `{"detail": "Invalid or expired token"}`, mint a
fresh key in the dashboard.

## 2. Execute an allowed tool

```bash
curl -sS -X POST "$HOST/execute" \
  -H "Authorization: Bearer $ACP_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "db.query",
    "payload": {"query": "SELECT id, email FROM customers LIMIT 5"}
  }' | jq
```

The response carries the full decision envelope: `action: "allow"`,
`risk_score`, `findings`, the upstream tool result, and a
`receipt_url` pointing at the signed audit row created for this call.
The decision is also pushed to the SSE event stream — the Live Feed
page picks it up within ~150 ms.

## 3. Execute a denied tool

The simplest deny to fire from curl is a destructive-SQL pattern:

```bash
curl -sS -X POST "$HOST/execute" \
  -H "Authorization: Bearer $ACP_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "db.query",
    "payload": {"query": "SELECT * FROM customers; DROP TABLE customers;"}
  }' | jq
```

Expected response: HTTP `403`, body shape `{"success": false, "error":
"policy_denied", "data": {"action": "deny", "rule_id": "...",
"findings": [...]}}`. The policy stage flagged the destructive SQL
pattern, the decision engine combined the risk signals, the audit row
was written, and the receipt URL is included in the response.
Execution did not happen.

For the full eight-attack red-team script (injection, jailbreak, wire
transfer, PII lookup, kubectl-delete-prod, terraform destroy), see
[`setup-agies.md` §B.3](../../setup-agies.md). To see the four shipped
scenarios in the Playground UI, open
`https://aegisagent.in/playground` and click any of the **Attack
Scenario** cards.

## 4. Fetch and verify the audit row

Each `/execute` response includes the audit row id in
`data.audit_id`. Pull the signed row:

```bash
AUDIT_ID=<copy from the previous response>

curl -sS "$HOST/audit/logs/$AUDIT_ID/receipt" \
  -H "Authorization: Bearer $ACP_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  | jq
```

The receipt contains the canonical row content, the `event_hash`, the
`prev_hash` linking to the previous row in the same shard, the
ed25519 signature, the signing-key fingerprint, and the inclusion
proof for the day's Merkle transparency root.

Verify a window of the chain:

```bash
curl -sS "$HOST/audit/logs/verify" \
  -H "Authorization: Bearer $ACP_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '{ valid, violations, rows_checked }'
```

A healthy response is `{ "valid": true, "violations": [],
"rows_checked": <N> }`. The verifier recomputes each row's
`event_hash` from canonical content, checks each row's `prev_hash`
against the previous row in the same shard, and validates each
signature against the day's signing key. Current and historical
signing keys are both honoured — key rotation does not invalidate
older receipts. See [Key Rotation](../operations/key-rotation.md).

## 5. Watch decisions live (optional)

Live Feed in the UI uses Server-Sent Events. From curl:

```bash
curl -N "$HOST/events/stream?token=$ACP_KEY" \
  -H "X-Tenant-ID: $TENANT"
```

`-N` disables buffering so events stream as they arrive. Each line is
a JSON object: `data: {"type": "decision", "data": {...}, "ts":
"..."}`. The connection stays open with 15-second heartbeats.

## Common errors

| HTTP | Symptom | Cause | Fix |
|---|---|---|---|
| 400 | `"X-Tenant-ID required"` | Missing tenant header | Add `-H "X-Tenant-ID: ..."`. |
| 401 | `"Invalid or expired token"` | API key revoked or never minted | Mint a new `acp_…` key from the dashboard. |
| 403 | `"policy_denied"` with `rule_id` | The action matched a deny rule | Inspect the rule in the Policy Builder. Often the expected outcome on attack scenarios. |
| 422 | `"Validation failed"` with `meta.details[].loc` | Malformed body | Match the schema in the gateway OpenAPI: `curl $HOST/openapi.json \| jq '.paths."/execute".post.requestBody'`. |
| 429 | Body includes `Retry-After` and `limit_type` | Per-tenant or per-agent quota exceeded | Wait the indicated seconds or raise the cap in Settings → Quota Management. |
| 504 | `"decision_timeout"` | Decision pipeline exceeded the gateway deadline | Usually transient. Retry once. Persistent 504 indicates a downstream service issue — see Settings → System Health. |

## Next

- [`setup-agies.md`](../../setup-agies.md) — the full client
  onboarding narrative (Path A SDK vs Path B proxy, red-team script,
  approval inbox replay, exit shadow mode).
- [`final-testing.md`](../../final-testing.md) — the
  release-verified end-to-end test matrix (31/31 PASS as the
  "verified working" reference).
- [60-second tour](60-second-tour.md) — the UI walkthrough, in the
  order a buyer evaluates first.
- [What is Aegis](what-is-aegis.md) — the product overview if you
  skipped it.
- [System Overview](../architecture/system-overview.md) — the full
  architecture and request path with code references.
- [API Reference](../api/reference.md) — every endpoint generated
  from the live OpenAPI spec.

> The clean URL `https://aegisagent.in` is the canonical endpoint.
> The `https://ha.aegisagent.in` alias points at the same backend and
> remains valid for historical scripts.
