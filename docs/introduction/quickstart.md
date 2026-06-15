# Quickstart

**From zero to a signed audit row in under ten curl commands** — and from there to an offline auditor-verifiable evidence package in five more. This page targets the live prod-ha deployment at `https://ha.aegisagent.in`. For a local install, swap the host for `http://localhost:8000` (or run `infra/minimal/docker-compose.minimal.yml` — see [Deployment](../operations/deployment.md) for the 3-container self-host shape).

## Three install paths

| You want to … | Use this |
|---|---|
| Wrap an existing Anthropic / OpenAI / LangChain agent in three lines of code | `pip install aegis-anthropic` (or `aegis-openai` / `aegis-langchain`) — see [SDK Wrappers](../integrations/sdk-wrappers.md) |
| Verify an evidence bundle as an auditor, offline | `pip install aegis-aevf`, then `aegis-verify --bundle bundle.json` — see [AEVF Overview](../AEVF/README.md) |
| Drive the full HTTP API from a custom integration | Follow the curl steps below, then [API Reference](../api/reference.md) |

## Prerequisites

- `curl` and `jq` on the command line.
- A demo or admin account on the target deployment. The public deployment ships with both. Production deployments use SSO or local credentials provisioned by an admin. Treat the credentials section below as the demo-only path — never paste production credentials into a shared shell.

## Credentials (demo deployment only)

For the public live demo at `https://ha.aegisagent.in`, two accounts are pre-seeded:

| Account | Role | Use case |
|---|---|---|
| `demo@aegisagent.in` | `VIEWER` | Read-only tour. Cannot trigger any write or `/execute` action. The Login page exposes this account via the "Try Live Demo" button so reviewers can click straight in. |
| `admin@acp.local` | `ADMIN` | Full write access. Required to run the playground steps below. The gateway's email validator accepts `.local` TLDs as of 2026-06-01 — see `services/gateway/routers/auth.py`. |

Passwords for the demo deployment are documented in the live deployment's onboarding email, not in this repository. If you need them and you have access, ask the deployment owner. For self-hosted installs, see [Deployment](../operations/deployment.md) for the seed script.

The tenant ID for the demo deployment is the canonical default UUID `00000000-0000-0000-0000-000000000001`. All requests below send it as an `X-Tenant-ID` header. This is the value of the `tenants.tenant_id` column, **not** the row primary key — see `architecture/data-model.md` for the split.

## 1. Get a token

```bash
HOST=https://ha.aegisagent.in
TENANT=00000000-0000-0000-0000-000000000001

TOKEN=$(curl -sS -X POST "$HOST/auth/token" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT" \
  -d '{"email":"admin@acp.local","password":"REDACTED"}' \
  | jq -r '.data.access_token')

echo "token length: ${#TOKEN}"
```

A successful login returns `{"success": true, "data": {"access_token": "...", "tenant_id": "...", "role": "ADMIN"}}`. The token is a JWT signed by the identity service; it expires in 15 minutes. The same call sets an `acp_token` HTTPOnly cookie for browser clients, but curl ignores cookies by default — pass the token explicitly via `Authorization: Bearer`.

## 2. Confirm authentication

```bash
curl -sS "$HOST/auth/me" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq
```

Expected response includes `role`, `tenant_id`, `email`, and `user_id`. If you get `{"detail": "Invalid or expired token"}`, the token has timed out — repeat step 1.

## 3. List the agents in your tenant

```bash
curl -sS "$HOST/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '.data.items[] | {name, risk_level, status}'
```

On the public demo this returns three pre-seeded agents: `db-copilot-demo`, `devops-agent-demo`, `support-agent-demo`. Each is registered in the `registry` service with an allow-listed set of tool permissions and a behaviour profile that drives the [demo packs](demo-packs.md). All three ship at `risk_level: low` until they generate enough decision history for the trust-score worker to recompute.

Capture one agent ID for the next step:

```bash
AGENT_ID=$(curl -sS "$HOST/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data.items[] | select(.name=="db-copilot-demo") | .id')

echo "agent id: $AGENT_ID"
```

## 4. Execute an allowed tool

```bash
curl -sS -X POST "$HOST/execute" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "db.query",
    "payload": {"query": "SELECT id, email FROM customers LIMIT 5"}
  }' | jq
```

The response carries the full decision envelope: `action: "allow"`, `risk_score`, `findings`, the upstream tool result, and a `receipt_url` pointing at the signed audit row created for this call.

The decision is also pushed to the SSE event stream, so anyone with the Live Feed page open in a browser sees this exact event arrive in real time.

## 5. Execute a denied tool

The four shipped attack scenarios are wired into the Playground UI. The simplest one to fire from curl is the `DROP TABLE` SQL injection case:

```bash
curl -sS -X POST "$HOST/execute" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "db.query",
    "payload": {"query": "SELECT * FROM customers; DROP TABLE customers;"}
  }' | jq
```

Expected response: HTTP `403`, body shape `{"success": false, "error": "policy_denied", "data": {"action": "deny", "rule_id": "...", "findings": [...]}}`. The policy stage flagged the destructive SQL pattern, the decision engine combined the five risk signals, the audit row was written, and the receipt URL is included in the response. Execution did not happen.

To see all four shipped attack scenarios in one place, open `https://ha.aegisagent.in/playground` and click any of the **Attack Scenario** cards.

## 6. Fetch and verify the audit row

Each `/execute` response includes the audit row id in `data.audit_id`. Pull the signed row:

```bash
AUDIT_ID=<copy from the previous response>

curl -sS "$HOST/audit/logs/$AUDIT_ID/receipt" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq
```

The receipt contains the canonical row content, the `event_hash`, the `prev_hash` linking to the previous row in the same shard, the ed25519 signature, the signing-key fingerprint, and the inclusion proof for the day's Merkle transparency root.

Verify a window of the chain:

```bash
curl -sS "$HOST/audit/logs/verify" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '{ valid, violations, rows_checked }'
```

A healthy response is `{ "valid": true, "violations": [], "rows_checked": <N> }`. The verifier recomputes each row's `event_hash` from canonical content, checks each row's `prev_hash` against the previous row in the same shard, and validates each signature against the day's signing key. Current and historical signing keys are both honored — key rotation does not invalidate older receipts. See [Key Rotation](../operations/key-rotation.md).

## 7. Watch decisions live (optional)

Live Feed in the UI uses Server-Sent Events. From curl:

```bash
curl -N "$HOST/events/stream?token=$TOKEN" \
  -H "X-Tenant-ID: $TENANT"
```

`-N` disables buffering so events stream as they arrive. Each line is a JSON object: `data: {"type": "decision", "data": {...}, "ts": "..."}`. The connection stays open with 15-second heartbeats.

## Common errors

| HTTP | Symptom | Cause | Fix |
|---|---|---|---|
| 400 | `"X-Tenant-ID required"` | Missing tenant header | Add `-H "X-Tenant-ID: ..."`. |
| 401 | `"Invalid or expired token"` | JWT timed out (15 minutes) or signature invalid | Re-run step 1 to mint a new token. |
| 403 | `"Write operations require ADMIN or SECURITY role"` | Logged in as `VIEWER` | Re-login as an account with ADMIN or SECURITY role. The `demo` account is intentionally VIEWER. |
| 403 | `"policy_denied"` with `rule_id` | The action matched a deny rule | Inspect the rule in the Policy Builder. Often the expected outcome on attack scenarios. |
| 422 | `"Validation failed"` with `meta.details[].loc` | Malformed body | Match the schema in the gateway OpenAPI: `curl $HOST/openapi.json \| jq '.paths."/execute".post.requestBody'`. |
| 429 | Body includes `Retry-After` and `limit_type` | Per-tenant or per-agent quota exceeded | Wait the indicated seconds or raise the cap in Settings → Quota Management. |
| 504 | `"decision_timeout"` | Decision pipeline exceeded the gateway deadline | Usually transient. Retry once. Persistent 504 indicates a downstream service issue — see Settings → System Health. |

## Next

- [60-second tour](60-second-tour.md) — the UI walkthrough, in the order a buyer evaluates first.
- [What is Aegis](what-is-aegis.md) — the product overview if you skipped it.
- [System Overview](../architecture/system-overview.md) — the full architecture and request path with code references.
- [API Reference](../api/reference.md) — every endpoint generated from the live OpenAPI spec.
- **SDK Quickstart** — Python, LangChain, OpenAI, and Anthropic integrations are in the repo's top-level `docs/quickstart.md` and will be folded into this section in Phase 2.
