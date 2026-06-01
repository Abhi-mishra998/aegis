# Examples

*Copy-paste-able samples for the operations you'll actually use. Curl, Python (`acp_client`), and Node fetch — same flow, three languages.*

## Setup

Set these environment variables once. They thread through every example.

```bash
export AEGIS_HOST=https://dev.aegisagent.in
export AEGIS_TENANT_ID=<your-tenant-uuid>
export AEGIS_EMAIL=<your-admin-email>
# Do NOT export the password to a shared shell. Pipe it in or use AWS Secrets Manager.
```

## 1. Mint a token

### curl

```bash
read -s -p "Password: " AEGIS_PASSWORD; echo
AEGIS_TOKEN=$(curl -sS -X POST "$AEGIS_HOST/auth/token" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -d "{\"email\":\"$AEGIS_EMAIL\",\"password\":\"$AEGIS_PASSWORD\"}" \
  | jq -r '.data.access_token')

echo "Token length: ${#AEGIS_TOKEN}"
```

### Python

```python
import os
import getpass
import httpx

host = os.environ["AEGIS_HOST"]
tenant_id = os.environ["AEGIS_TENANT_ID"]
email = os.environ["AEGIS_EMAIL"]
password = getpass.getpass("Password: ")

resp = httpx.post(
    f"{host}/auth/token",
    headers={"Content-Type": "application/json", "X-Tenant-ID": tenant_id},
    json={"email": email, "password": password},
)
resp.raise_for_status()
token = resp.json()["data"]["access_token"]
print(f"Token length: {len(token)}")
```

### Node

```javascript
import { stdin as input, stdout as output } from 'node:process';
import readline from 'node:readline/promises';

const host = process.env.AEGIS_HOST;
const tenantId = process.env.AEGIS_TENANT_ID;
const email = process.env.AEGIS_EMAIL;

const rl = readline.createInterface({ input, output });
const password = await rl.question('Password: ');
rl.close();

const r = await fetch(`${host}/auth/token`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': tenantId },
  body: JSON.stringify({ email, password }),
});
const { data } = await r.json();
console.log(`Token length: ${data.access_token.length}`);
```

In all three cases, never paste the password value into the request body literal — read it from stdin or from a secret store.

## 2. List agents

### curl

```bash
curl -sS "$AEGIS_HOST/agents" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  | jq '.data.items[] | {name, risk_level, status}'
```

### Python

```python
import httpx

token = ...  # from step 1
resp = httpx.get(
    f"{host}/agents",
    headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant_id},
)
for item in resp.json()["data"]["items"]:
    print(f"  {item['name']:20} risk={item['risk_level']:8} status={item['status']}")
```

### Node

```javascript
const r = await fetch(`${host}/agents`, {
  headers: { Authorization: `Bearer ${token}`, 'X-Tenant-ID': tenantId },
});
const { data } = await r.json();
for (const item of data.items) {
  console.log(`  ${item.name.padEnd(20)} risk=${item.risk_level.padEnd(8)} status=${item.status}`);
}
```

## 3. Execute a tool (the main event)

### curl

```bash
AGENT_ID=$(curl -sS "$AEGIS_HOST/agents" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  | jq -r '.data.items[] | select(.name=="db-copilot") | .id')

curl -sS -X POST "$AEGIS_HOST/execute" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "db.query",
    "payload":   {"query": "SELECT id, email FROM customers LIMIT 5"}
  }' | jq
```

### Python (acp_client SDK)

```python
import os
from acp_client import ACPClient

async def main():
    async with ACPClient(
        host=os.environ["AEGIS_HOST"],
        tenant_id=os.environ["AEGIS_TENANT_ID"],
        token=token,  # from step 1
    ) as client:
        result = await client.execute(
            agent_id="<agent-uuid>",
            tool_name="db.query",
            payload={"query": "SELECT id, email FROM customers LIMIT 5"},
        )
        print(f"action: {result['action']}")
        print(f"risk:   {result['risk_score']}")
        print(f"audit:  {result['audit_id']}")

import asyncio; asyncio.run(main())
```

### Python (raw httpx)

```python
resp = httpx.post(
    f"{host}/execute",
    headers={
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID": tenant_id,
        "X-Agent-ID": agent_id,
        "Content-Type": "application/json",
    },
    json={
        "tool_name": "db.query",
        "payload": {"query": "SELECT id, email FROM customers LIMIT 5"},
    },
)
print(resp.json())
```

### Node

```javascript
const r = await fetch(`${host}/execute`, {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${token}`,
    'X-Tenant-ID': tenantId,
    'X-Agent-ID': agentId,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    tool_name: 'db.query',
    payload: { query: 'SELECT id, email FROM customers LIMIT 5' },
  }),
});
const result = await r.json();
console.log(result);
```

## 4. Execute a tool that should be denied (attack scenario)

```bash
curl -sS -X POST "$AEGIS_HOST/execute" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "db.query",
    "payload":   {"query": "SELECT * FROM customers; DROP TABLE customers;"}
  }' | jq
```

Expected: HTTP 403 with body shape:

```json
{
  "success": false,
  "error": "policy_denied",
  "data": {
    "action": "deny",
    "rule_id": "agent.deny.destructive_sql",
    "findings": ["destructive_sql"],
    "score": 0.97,
    "audit_id": "<uuid>"
  }
}
```

In Python (with SDK), the same call raises `PolicyDeniedError`:

```python
from acp_client.errors import PolicyDeniedError

try:
    await client.execute(agent_id=agent_id, tool_name="db.query",
                         payload={"query": "DROP TABLE customers"})
except PolicyDeniedError as e:
    print(f"Denied by rule {e.rule_id}: {e.findings}")
```

## 5. Verify the audit chain

```bash
curl -sS "$AEGIS_HOST/audit/logs/verify" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  | jq '{ valid, violations, rows_checked }'
```

Healthy: `{ "valid": true, "violations": [], "rows_checked": <N> }`.

## 6. Fetch a signed receipt

```bash
AUDIT_ID=<from the previous execute response>

curl -sS "$AEGIS_HOST/audit/logs/$AUDIT_ID/receipt" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  | jq
```

The response includes the canonical receipt, the ed25519 signature, the key fingerprint, the prev_hash, and the Merkle inclusion proof for the day's transparency root.

## 7. Subscribe to the Server-Sent Events stream

```bash
# Get an SSE-specific query token (stored in localStorage in browser flows)
SSE_TOKEN=$(curl -sS "$AEGIS_HOST/auth/me" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  | jq -r '.data.sse_token // .data.access_token')

# Subscribe — -N disables buffering so events stream as they arrive
curl -N "$AEGIS_HOST/events/stream?token=$SSE_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID"
```

Each line is a JSON object: `data: {"type":"decision","data":{...},"ts":"..."}`. Heartbeats every 15 seconds.

In Node:

```javascript
const es = new EventSource(`${host}/events/stream?token=${sseToken}`, {
  headers: { 'X-Tenant-ID': tenantId },
});
es.onmessage = (e) => {
  const event = JSON.parse(e.data);
  console.log(`${event.ts} ${event.type} ${JSON.stringify(event.data)}`);
};
```

## 8. Engage and disengage the kill switch

```bash
# Engage — body is the action literal only; the engagement reason is recorded
# server-side as `manual_admin_lockdown`.
curl -sS -X POST "$AEGIS_HOST/decision/kill-switch/$AEGIS_TENANT_ID" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"engage"}'

# Verify engaged — response shape:
# {"success":true,"data":{"status":"engaged"|"disengaged","tenant_id":"...","reason":"manual_admin_lockdown"|null}}
curl -sS "$AEGIS_HOST/decision/kill-switch/$AEGIS_TENANT_ID" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" | jq

# Disengage — DELETE takes no body; the POST variant accepts {"action":"disengage"} too.
curl -sS -X DELETE "$AEGIS_HOST/decision/kill-switch/$AEGIS_TENANT_ID" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID"
```

Requires `ADMIN` or `SECURITY` role. The path `tenant_id` must match the JWT's tenant — mismatch returns HTTP 403 from `_assert_authenticated_tenant_matches`. Pre-2026-06-01 images returned HTTP 422 "Validation failed" on every call due to a missing `Path(...)` annotation on the dependency arg — if you see that, you're running a stale image. See [Kill Switch runbook](../operations/runbooks/kill-switch-engaged.md) before engaging.

## 9. Add an analyst note

```bash
curl -sS -X POST "$AEGIS_HOST/audit/logs/$AUDIT_ID/notes" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "note_type":  "false_positive",
    "body":       "This is a legitimate ETL query; the rule is over-broad.",
    "created_by": "analyst@acme.com"
  }'
```

`note_type` ∈ `analysis`, `false_positive`, `confirmed_threat`, `escalated`.

## 10. Grant a tool permission

```bash
curl -sS -X POST "$AEGIS_HOST/agents/$AGENT_ID/permissions" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name":  "crm.lookup_ticket",
    "action":     "ALLOW",
    "granted_by": "operator@acme.com"
  }'
```

The new grant is effective within 60 seconds (gateway permission cache TTL).

## 11. Run a policy simulation

```bash
curl -sS -X POST "$AEGIS_HOST/policy/simulate" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "policy_text": "package aegis.decision\ndeny[r] { input.tool_name == \"db.query\"; regex.match(`(?i)\\bdelete\\b`, input.payload.query); r := {\"id\":\"draft.deny.delete\",\"severity\":\"high\"} }",
    "window_hours": 24
  }' | jq '{ total, would_allow, would_deny, would_have_blocked }'
```

Requires `ADMIN` or `SECURITY` role.

## 12. Export a compliance report PDF

```bash
curl -sS "$AEGIS_HOST/compliance/export?framework=soc2&period_start=2026-04-01&period_end=2026-04-30" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -o soc2-2026-04.pdf
```

Time the call — very large periods can take 30+ seconds.

## Idempotency on `/execute`

For retry safety, include `X-Idempotency-Key`:

```bash
curl -sS -X POST "$AEGIS_HOST/execute" \
  -H "Authorization: Bearer $AEGIS_TOKEN" \
  -H "X-Tenant-ID: $AEGIS_TENANT_ID" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "X-Idempotency-Key: client-trace-12345" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"db.query","payload":{"query":"SELECT 1"}}'
```

A duplicate call with the same key within 60 seconds returns the original response, not a re-execution.

## Error handling pattern

```python
from acp_client.errors import (
    PolicyDeniedError, KillSwitchEngagedError, RateLimitError,
    AuthError, DecisionTimeoutError, EscalationRequiredError,
)

try:
    result = await client.execute(agent_id=..., tool_name=..., payload=...)
except AuthError:
    # Re-login
    ...
except PolicyDeniedError as e:
    # Expected for known-bad inputs; log and continue
    print(f"Denied: {e.rule_id}")
except KillSwitchEngagedError:
    # Hard stop — escalate to operator
    raise
except RateLimitError as e:
    # Wait and retry
    await asyncio.sleep(e.retry_after)
    ...
except EscalationRequiredError as e:
    # Human approval workflow needed
    ...
except DecisionTimeoutError:
    # Transient; retry once
    ...
```

## Next

- [Reference](reference.md) — full endpoint inventory
- [Authentication](authentication.md) — token issuance and validation
- [Error Codes](error-codes.md) — full status code matrix
- [Quickstart](../introduction/quickstart.md) — the canonical first-call walkthrough
