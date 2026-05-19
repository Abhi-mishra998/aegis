# ACP Client Demo — Step-by-Step Playbook

> **Runtime security for AI agents.**
> Every command below is verified against the live stack.
> Paste each block exactly — no typing during a client demo.

---

## BROWSER TABS — open before the client arrives

| Tab | URL | Login |
|---|---|---|
| ACP UI | http://localhost:5173 | admin@acp.local / password |
| Grafana | http://localhost:3000 | admin / admin |
| Jaeger | http://localhost:16686 | — |
| Prometheus | http://localhost:9090 | — |

---

## TERMINAL TABS — open 6 tabs in iTerm

| Tab | Purpose |
|---|---|
| Tab 1 | Environment variables (STEP 0) — keep open all demo |
| Tab 2 | Agent creation + permissions |
| Tab 3 | Tool execution (allow) |
| Tab 4 | Tool execution (path traversal block) |
| Tab 5 | Tool execution (permission block) |
| Tab 6 | Everything else |

---

## PHASE 0 — Stack Boot (do this 10 minutes before the client arrives)

### 0a. Build and start all 26 containers

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp/infra
docker compose down -v
docker compose up --build -d
```

Wait ~90 seconds for containers to initialise, then verify:

```bash
docker ps --format "{{.Names}}\t{{.Status}}" | grep "(healthy)" | wc -l
```

Expected: `26`

If a container is unhealthy:

```bash
# See which container is sick
docker ps --format "{{.Names}}\t{{.Status}}" | grep -v "(healthy)"

# Read its logs
docker logs <container_name> --tail 40
```

### 0b. Seed the admin user + provision demo packs (REQUIRED after every `down -v`)

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp

# 1. Seed admin (required — every demo uses admin@acp.local / password)
.venv/bin/python scripts/utils/seed_admin.py

# 2. Provision all 3 demo packs (registers agents + credentials + identity graph)
#    REQUIRED after every docker compose down -v (volumes are wiped)
.venv/bin/python demos/db_copilot/setup_demo.py
.venv/bin/python demos/devops_agent/setup_demo.py
.venv/bin/python demos/support_agent/setup_demo.py
```

Expected: each setup prints `✅ Setup complete` with an agent_id and credentials path.

### 0c. Run alembic migrations (idempotent)

```bash
docker exec acp_audit bash -lc "cd /app/services/audit && python -m alembic upgrade head"
docker exec acp_identity bash -lc "cd /app/services/identity && python -m alembic upgrade head"
docker exec acp_behavior bash -lc "cd /app/services/learning && python -m alembic upgrade head"
```

Expected: each prints `INFO  [alembic.runtime.migration] Running upgrade … done` or `no changes`.

### 0d. Seed today's transparency root (required on fresh stack)

The daily root scheduler runs hourly. On a fresh `down -v` boot, seed it manually so Phase 14 works immediately:

```bash
# Obtain an admin token first (TOKEN set in Phase 1, but we need it here too)
BOOTSTRAP_TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token // .access_token')

curl -s -X POST "http://localhost:8000/transparency/compute" \
  -H "Authorization: Bearer $BOOTSTRAP_TOKEN" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  | jq '{root_date: .data.root_date, root_hash: (.data.root_hash // .data.signed.root_hash)}'
```

Expected: `{"root_date": "<today>", "root_hash": "<64-char hex>"}` — if `data` is `null` there are no audit rows yet; run after Phase 4.

---

## PHASE 1 — Environment Variables (Tab 1 — keep this tab open all demo)

```bash
# Working directory
cd /Users/abhishekmishra/mcp-security-controller/acp

# Fixed tenant for the demo
export TENANT="00000000-0000-0000-0000-000000000001"

# Pull the internal secret from the running gateway container
export INTERNAL_SECRET=$(docker exec acp_gateway sh -c 'echo $INTERNAL_SECRET')
[ -n "$INTERNAL_SECRET" ] && echo "✅ INTERNAL_SECRET (${#INTERNAL_SECRET} chars)" || echo "❌ empty — check docker"

# Admin token (15-min TTL — re-run this block if you get 401 later)
export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')

[ "$TOKEN" = "null" ] || [ -z "$TOKEN" ] \
  && echo "❌ login failed — did you run seed_admin.py?" \
  || echo "✅ Token: ${TOKEN:0:40}…"
```

> **Token expires every 15 minutes.** If you see a 401 at any point, re-paste the `export TOKEN=…` block above.

---

## PHASE 2 — Prove the Stack is Alive (Tab 1 — 30 seconds)

**Say:** *"26 containers, 14 microservices. One health endpoint tells you everything is green."*

```bash
curl -s "http://localhost:8000/system/health" \
  | jq '{status, healthy, total, p95_ms: .latency.p95_ms}'
```

Expected:
```json
{
  "status": "operational",
  "healthy": 12,
  "total": 12,
  "p95_ms": 34
}
```

> **Switch to UI** → login → click **System Health** in sidebar → 12/12 green tiles.

---

## PHASE 3 — Create an Agent FROM SCRATCH (Tab 2 — live in front of client)

**Say:** *"Every AI agent has a per-tool permission policy. Here is what onboarding looks like."*

### 3a. Create the agent

```bash
export AID=$(curl -s -X POST "http://localhost:8000/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "client-demo-agent",
        "description": "Live demo agent created during client walkthrough",
        "owner_id": "demo-presenter",
        "risk_level": "low",
        "metadata": {"demo": true, "created_during": "live_demo"}
      }' \
  | jq -r '.data.id')

echo "✅ Agent ID: $AID"
```

> **Switch to UI** → **Agents** page → refresh → `client-demo-agent` appears at the top, status `ACTIVE`.

### 3b. Grant tool permissions

```bash
for tool in read_file query execute_agent db.query; do
  curl -s -X POST "http://localhost:8000/agents/$AID/permissions" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Tenant-ID: $TENANT" \
    -H "Content-Type: application/json" \
    -d "{\"tool_name\":\"$tool\",\"action\":\"ALLOW\"}" \
    | jq -c "{tool: \"$tool\", granted: .success}"
done
```

Expected (4 lines):
```
{"tool":"read_file","granted":true}
{"tool":"query","granted":true}
{"tool":"execute_agent","granted":true}
{"tool":"db.query","granted":true}
```

> **UI** → agent detail panel → permissions table shows 3 `ALLOW` entries.

### 3c. Provision agent credentials + issue agent JWT

```bash
# Generate once — must be the SAME secret for both provision and token calls
export AGENT_SECRET="client-demo-secret-$(date +%s)"

# Provision the secret (admin-only, calls identity service directly)
curl -s -X POST "http://localhost:8002/auth/credentials" \
  -H "X-Internal-Secret: $INTERNAL_SECRET" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"secret\":\"$AGENT_SECRET\"}" \
  | jq '{provisioned: .success}'

# Get the agent runtime JWT
export AGENT_TOKEN=$(curl -s -X POST "http://localhost:8000/auth/agent/token" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"secret\":\"$AGENT_SECRET\"}" \
  | jq -r '.data.access_token')

[ "$AGENT_TOKEN" = "null" ] || [ -z "$AGENT_TOKEN" ] \
  && echo "❌ null token — secret mismatch or provision failed" \
  || echo "✅ Agent JWT: ${AGENT_TOKEN:0:40}…"
```

**Say:** *"The agent now has a scoped JWT. Every call this token makes goes through ACP first. The agent's code doesn't change — we sit in front of it."*

---

## PHASE 4 — Playground: Drive the Agent (most visual moment)

> **Switch to UI** → **Observability** page so the client watches the decision feed update live.

### 4a. Allowed call (Tab 3) — expected: ALLOW, risk ≈ 0.09

```bash
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"path\":\"customers.csv\"}}" \
  | jq '{action, risk, findings}'
```

Expected:
```json
{
  "action": "allow",
  "risk": 0.09,
  "findings": []
}
```

> **UI** → Observability → decision feed shows `read_file → ALLOW` appear in real time via SSE.

### 4b. Path-traversal attack (Tab 4) — expected: 403 BLOCKED

```bash
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"path\":\"../../etc/passwd\"}}" \
  | jq '{success, error}'
```

Expected:
```json
{
  "success": false,
  "error": "Security: Prompt injection detected: '../../'"
}
```

> **UI** → **Audit Logs** → filter `decision=deny` → deny appears at top with `findings: [path_traversal_detected]` and risk score.

### 4c. Permission-denied attack (Tab 5) — agent doesn't have `shell.exec`

```bash
curl -s -X POST "http://localhost:8000/execute/shell.exec" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"cmd\":\"rm -rf /\"}}" \
  | jq '{success, error}'
```

Expected:
```json
{
  "success": false,
  "error": "Tool not permitted"
}
```

**Say:** *"Three layers blocked this — JWT permission claims, Registry permission table, OPA policy. Defence in depth. Every layer logs its decision."*

---

## PHASE 5 — Audit Logs (Tab 6)

> **Switch to UI** → **Audit Logs** → filter `agent_id = client-demo-agent` → 3 events: 1 allow + 2 denies.

```bash
curl -s "http://localhost:8000/audit/logs?limit=5&agent_id=$AID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '.data.items // [] | map({ts: .timestamp[0:19], action, decision, tool, reason})'
```

Expected: 3 rows — one allow, two deny.

> If you see `[]` but ran tool calls moments ago, your `$TOKEN` expired. Re-run PHASE 1.

---

## PHASE 6 — Flight Recorder: Execution Replay (Tab 6)

> **Switch to UI** → **Flight Recorder** (homepage) → click any row → 3-pane: steps timeline / snapshot diff / metadata.

```bash
TL_ID=$(curl -s "http://localhost:8000/flight/timelines?limit=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data[0].id')

curl -s "http://localhost:8000/flight/timeline/$TL_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '.data | {
      tool:        .timeline.tool,
      final:       .timeline.final_decision,
      duration_ms: .timeline.duration_ms,
      steps:       (.steps | length),
      snapshots:   (.snapshots | length)
    }'
```

Expected: `tool`, `final`, `duration_ms` filled in, `steps >= 5`, `snapshots >= 2`.

---

## PHASE 7 — Identity Graph + Blast-Radius Simulation (Tab 6)

> **Switch to UI** → **Identity Graph** → visual web of agents → users → API keys → tools → tenants.

```bash
# Find the agent node (falls back to first agent if demo agent not yet in graph)
AGENT_NODE=$(curl -s "http://localhost:8000/graph/agents?limit=50" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq -r --arg aid "$AID" '
      ([.data.nodes[] | select(.node_type=="agent" and (.name==$aid or .id==$aid))][0].id)
      // ([.data.nodes[] | select(.node_type=="agent")][0].id)
      // empty')

if [ -z "$AGENT_NODE" ] || [ "$AGENT_NODE" = "null" ]; then
  echo "⚠️  No agent nodes yet — run the demo packs first (PHASE 12)"
else
  echo "✅ Simulating compromise of $AGENT_NODE"
  curl -s -X POST "http://localhost:8000/graph/compromise/simulate" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Tenant-ID: $TENANT" \
    -H "Content-Type: application/json" \
    -d "{\"actor_node_id\":\"$AGENT_NODE\",\"scenario\":\"stolen_token\",\"depth\":3}" \
    | jq '.data | {
        scenario,
        blast_radius,
        risk_score,
        classification: .summary.risk_classification,
        reachable_count: (.reachable_nodes | length)
      }'
fi
```

Expected:
```json
{
  "scenario": "stolen_token",
  "blast_radius": 4,
  "risk_score": 0.21,
  "classification": "LOW",
  "reachable_count": 4
}
```

**Say:** *"If this agent's token is stolen, here is exactly what an attacker can reach. We can simulate this for any agent before you onboard it."*

---

## PHASE 8 — Autonomy Contracts (Tab 6)

> **Switch to UI** → **Autonomy** → contracts table.

```bash
# Seed a contract for the client-demo-agent
curl -s -X POST "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{
        \"agent_id\":\"$AID\",
        \"name\":\"client-demo-contract\",
        \"allowed_actions\":[\"read_file\",\"query\",\"db.query\"],
        \"denied_actions\":[\"shell.exec\",\"k8s.delete.namespace\"],
        \"approval_required\":[\"transfer_funds\",\"send_email\"],
        \"max_tool_calls\":3,
        \"max_cost_usd\":1.0,
        \"max_runtime_seconds\":300
      }" | jq -c '{seeded: .success, id: .data.id}'
```

```bash
# List all contracts
curl -s "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {name, allowed: (.allowed_actions | length), denied: (.denied_actions | length)}'
```

**Say:** *"Autonomy contracts are the seatbelt — the AI acts freely within bounds, anything else escalates to a human. Editable in the UI, version-tracked, every change auditable."*

---

## PHASE 9 — Autonomous Response Engine (Tab 6)

> **Switch to UI** → **Auto-Response** → rules table.

```bash
# Rule 1: CRITICAL → kill agent + alert (auto mode)
curl -s -X POST "http://localhost:8000/auto-response/rules" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "critical-risk-auto-response",
        "is_active": true,
        "priority": 100,
        "conditions": {"severity_in": ["CRITICAL"], "risk_score_gte": 0.85},
        "actions": [{"type": "KILL_AGENT", "params": {}}, {"type": "ALERT", "params": {"channel":"slack"}}],
        "mode": "auto",
        "cooldown_seconds": 300,
        "max_triggers_per_hour": 50
      }' | jq -c '{seeded: .success, name: .data.name}'

# Rule 2: HIGH → Slack alert only
curl -s -X POST "http://localhost:8000/auto-response/rules" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "high-risk-slack-alert",
        "is_active": true,
        "priority": 50,
        "conditions": {"severity_in": ["HIGH","CRITICAL"], "risk_score_gte": 0.7},
        "actions": [{"type": "ALERT", "params": {"channel":"slack"}}],
        "mode": "auto",
        "cooldown_seconds": 60
      }' | jq -c '{seeded: .success, name: .data.name}'

# Rule 3: Any severity → suggest/notify
curl -s -X POST "http://localhost:8000/auto-response/rules" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "security-event-notify",
        "is_active": true,
        "priority": 10,
        "conditions": {"severity_in": ["LOW","MEDIUM","HIGH","CRITICAL"]},
        "actions": [{"type": "ALERT", "params": {"channel":"slack"}}],
        "mode": "suggest"
      }' | jq -c '{seeded: .success, name: .data.name}'
```

```bash
# Verify 3 rules are live
curl -s "http://localhost:8000/auto-response/rules" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {name, actions: [.actions[].type], mode, active: .is_active}'
```

**Say:** *"At 3am when nobody is watching, ARE fires automatically. Kill the agent, alert Slack — your runbook executes itself."*

---

## PHASE 10 — Kill Switch (Tab 6)

> **Switch to UI** → **Observability** → watch the decision feed.

```bash
# Engage kill switch
curl -s -X POST "http://localhost:8000/decision/kill-switch/$TENANT" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"action":"engage","reason":"client_demo"}' \
  | jq '.data.status'
```

```bash
# Prove it blocks everything (safe call that would normally ALLOW)
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $AGENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"path\":\"safe.txt\"}}" \
  | jq '{success, error}'
```

Expected: `"success": false, "error": "Tenant blocked due to security violation"`

```bash
# Disengage — restore normal operation
curl -s -X DELETE "http://localhost:8000/decision/kill-switch/$TENANT" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data.status'
```

**Killer talking point:** *"This survives a Redis flush. Double-written to Postgres, re-hydrated every 5 seconds. Even if the cache layer goes down, the kill switch holds."*

---

## PHASE 11 — Slack Alert (real notification fires live)

> **Open Slack** in a browser window — point at the security channel before pressing Enter.

```bash
# Fire a CRITICAL incident — Slack alert goes out automatically
curl -s -X POST "http://localhost:8005/incidents" \
  -H "X-Internal-Secret: $INTERNAL_SECRET" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{
        \"tenant_id\":\"$TENANT\",
        \"agent_id\":\"$AID\",
        \"severity\":\"CRITICAL\",
        \"trigger\":\"policy_deny\",
        \"title\":\"Agent attempted destructive rm -rf / command\",
        \"tool\":\"shell.exec\",
        \"risk_score\":0.97,
        \"explanation\":\"ACP gateway blocked execution and auto-escalated to security team\"
      }" \
  | jq '{success, severity: .data.severity, incident_number: .data.incident_number, status: .data.status}'
```

Expected:
```json
{
  "success": true,
  "severity": "CRITICAL",
  "incident_number": "INC-000NN",
  "status": "OPEN"
}
```

```bash
# Confirm Slack actually fired (not just the API response)
docker logs acp_api 2>&1 | grep "hooks.slack.com" | tail -1
```

Expected: `HTTP Request: POST https://hooks.slack.com/… "HTTP/1.1 200 OK"`

> **Switch to Slack** — within 2 seconds a Block Kit message appears: severity badge, risk score, agent ID, "View in ACP" deep-link button.

**Say:** *"One incident, three layers react simultaneously: incident logged, Slack alerted, agent killed. All in under 2 seconds. No human in the loop."*

---

## PHASE 12 — Cryptographic Proof (the auditor's friend)

### 12a. Public key — auditors archive this

```bash
curl -s "http://localhost:8000/receipts/key" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{algorithm, fingerprint: .public_key_fingerprint}'
```

Expected: `"algorithm": "ed25519"`

### 12b. Pull a signed receipt for a real decision

```bash
EXEC_ID=$(curl -s "http://localhost:8000/audit/logs?limit=20" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '[.data.items[] | select(.action=="execute_tool" and .decision=="allow")][0].id')

curl -s "http://localhost:8000/receipts/$EXEC_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{algorithm, sig_length: (.signature | length), fingerprint}'
```

Expected: `"algorithm": "ed25519", "sig_length": 86`

### 12c. Verify the ENTIRE audit chain — court-admissible

```bash
.venv/bin/acp verify-chain \
  --base-url http://localhost:8000 \
  --token "$TOKEN" \
  --tenant "$TENANT" \
  --json \
  | jq '{valid: .valid, processed, errors: .total_violations}'
```

Expected: `"valid": true, "errors": 0`

**Say (slowly):** *"Every decision just got proven mathematically intact. Your auditor doesn't need to trust ACP — they archive the public key and daily Merkle root, then verify offline whenever they want."*

### 12d. Daily Merkle root chain (transparency log)

```bash
curl -s "http://localhost:8000/transparency/roots?limit=3" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {
      root_date,
      leaf_count,
      root_hash: (.root_hash[0:24] + "…"),
      algorithm: .signed.algorithm,
      key_fingerprint: .signed.public_key_fingerprint
    }'
```

Expected (one entry per sealed day):
```json
{
  "root_date": "2026-05-19",
  "leaf_count": 119,
  "root_hash": "fec8c14a6193bfdfa3b62958…",
  "algorithm": "ed25519",
  "key_fingerprint": "5615db00ca28c2e792dc7e3d5d70f3c0"
}
```

---

## PHASE 13 — Three Enterprise Demo Packs (one command)

> **Switch to UI** → **Flight Recorder** so the client watches timelines fill in real time.

### Offline / dry-run (~10 seconds, no Groq calls)

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp
ACP_DRY_RUN=1 .venv/bin/python demos/run_all_demos.py
```

Expected:
```
Pack 1: AI Database Copilot     PASS    ~4s
Pack 2: AI DevOps Agent         PASS    ~4s
Pack 3: AI Support Agent        PASS    ~3s
All scenarios passed. Demo platform ready.
```

### Live against the stack (~46 seconds, real Groq calls, real audit chain)

```bash
.venv/bin/python demos/run_all_demos.py
```

### What each pack proves

| Pack | Key scenarios | What ACP proves |
|---|---|---|
| DB Copilot | safe SELECT · DROP TABLE · PII columns · kill switch | DDL hard-deny + token revocation + PII column filter |
| DevOps Agent | safe reads · scale · delete namespace · clusterrolebinding · rate-limit storm | K8s hard-deny + 3-op/hr destructive budget + 429 rate-limit |
| Support Agent | ticket lookup · cross-tenant attack · bulk PII export · email exfiltration | Tenant isolation + email hard-deny + 30 req/min rate-limit |

---

## PHASE 14 — Observability Tour

### Grafana — http://localhost:3000 (admin / admin)

Four pre-built dashboards:
- **Platform SLO** — `/execute` p50/p95/p99, error rate, rate-limit breakdown
- **Trust Layers** — chain integrity, reconcile gap, behavior consult mix
- **Tenant Activity** — per-tenant request rate, cost, quota usage
- **Queue Health** — every stream depth, DLQ length, outbox age

### Jaeger — http://localhost:16686

```
Service: gateway
```

Click any recent trace → full span tree: auth → rate-limit → OPA → decision → audit → response. Exact ms breakdown per phase.

### Prometheus — http://localhost:9090

```promql
rate(acp_gateway_requests_total[1m])
```

Live request rate broken down by decision outcome.

---

## PHASE 15 — Python SDK (Tab 6 — for the engineering buyer)

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp
export ACP_AGENT_ID="$AID"
export ACP_TOKEN="$AGENT_TOKEN"
.venv/bin/python examples/agent.py
```

Expected:
```
allow -> [{'row': '1', 'value': 'SELECT * FROM customers LIMIT 1'}]
deny  -> Security: Tool 'shell.exec' not in agent's allow-list
receipt verifies -> skipped (set ACP_LAST_EXECUTION_ID to a real id)
```

The integration is **5 lines of code** — one decorator:

```python
import acp

client = acp.Client()                          # reads ACP_TOKEN from env

@client.protect(agent_id=ACP_AGENT_ID)
def read_data(path: str) -> list[dict]:
    return open(path).read()                   # only runs if ACP allows it
```

**Say:** *"Your engineers add the decorator. ACP does the rest. Policy enforcement, audit, receipts — all automatic."*

---

## PHASE 16 — Billing Reconciliation (Tab 6 — the CFO's friend)

> **Switch to UI** → **Billing** → reconciliation gauge at bottom: `0 gaps`.

```bash
.venv/bin/python scripts/ops/reconcile.py \
  --tenant "$TENANT" \
  --json \
  | jq '{status, audit_without_usage_count, usage_without_audit_count}'
```

Expected: `"status": "VERIFIED"`, both counts `0`.

**Killer talking point:** *"Transactional outbox pattern — audit and billing written in one DB transaction. If anything crashes between them, the outbox worker drains the gap on restart. Zero revenue leakage, provable."*

---

## PHASE 17 — Encrypted Backup (Tab 6 — the compliance officer's friend)

> **Prerequisite:** AWS credentials must be configured (`aws configure` or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` in env). The bucket `acp-backups-abhishek-prod` lives in `ap-south-1`.

```bash
export AWS_DEFAULT_REGION=ap-south-1
export ACP_BACKUP_S3_BUCKET=s3://acp-backups-abhishek-prod/prod
export ACP_BACKUP_AGE_RECIPIENT="<age1...your-public-key>"   # from: age-keygen
export ACP_BACKUP_AGE_IDENTITY="$HOME/.age/acp-backup.key"  # from: age-keygen -o

PGPASSWORD=postgres POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
  bash scripts/ops/backup.sh --no-verify
```

Expected: `✓ PASSED — 8 databases backed up to s3://acp-backups-abhishek-prod/prod`

**Say:** *"age-encrypted. Private key never leaves your machine. Restore drill is automated — `scripts/ops/restore_drill.sh` on an isolated Docker network."*

---

## PHASE 18 — Load Test (optional — for technical buyers)

Runs a Locust load test against the live stack.

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp
.venv/bin/locust \
  -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --web-port 8090 \
  --headless \
  --users 50 \
  --spawn-rate 5 \
  --run-time 60s
```

Open http://localhost:8090 in a browser to watch the live Locust dashboard while it runs.

After the run, verify integrity:

```bash
.venv/bin/python scripts/ops/reconcile.py --tenant "$TENANT" --json \
  | jq '{status, audit_without_usage_count, usage_without_audit_count}'
```

Expected: `"status": "VERIFIED"` even under 50 concurrent users.

---

## CLOSING — Two questions for the room

> **Question 1:** *"Right now, today — if one of your AI agents tried to delete your production database, how long would it take you to find out? And could you prove exactly what happened?"*
>
> **Question 2:** *"When your auditors ask next quarter for a complete, tamper-proof log of every action every AI agent took — what will you hand them?"*

**ACP's answers:**

| Question | ACP answer |
|---|---|
| 1 | "You would know in 47 ms. The action would already be blocked. You would have a signed, tamper-proof record of the attempt." |
| 2 | `.venv/bin/acp verify-chain → "valid": true, "errors": 0`, processed in seconds. Done. |

> **Next step:** *"Could we deploy this around one of your agents next week — your tools, your workflows, our pilot?"*

---

## TOKEN EXPIRY SAFETY NET

If you get `401 Unauthorized` on any command, your admin token expired (15-min TTL). Re-run this and continue:

```bash
export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')
echo "✅ Token refreshed: ${TOKEN:0:40}…"
```

---

## SERVICE MAP

| Service | Port | Container |
|---|---|---|
| Gateway | 8000 | acp_gateway |
| UI | 5173 | acp_ui |
| Identity | 8002 | acp_identity |
| Registry | 8001 | acp_registry |
| Policy | 8003 | acp_policy |
| Decision | 8010 | acp_decision |
| Audit | 8004 | acp_audit |
| Usage/Billing | 8006 | acp_usage |
| Behavior | 8007 | acp_behavior |
| API/Incidents/ARE | 8005 | acp_api |
| Insight | 8011 | acp_insight |
| Forensics | 8012 | acp_forensics |
| Identity Graph | 8013 | acp_identity_graph |
| Flight Recorder | 8014 | acp_flight_recorder |
| Autonomy | 8015 | acp_autonomy |
| Postgres | 5433 | acp_postgres |
| Redis | 6379 | acp_redis |
| OPA | 8181 | acp_opa |
| Prometheus | 9090 | acp_prometheus |
| Grafana | 3000 | acp_grafana |
| Jaeger | 16686 | acp_jaeger |

---

## WHAT IS MOCKED vs. REAL

| Surface | Real | Mocked |
|---|---|---|
| ACP gateway, policy engine, audit chain, receipts | ✅ | — |
| OPA policy evaluation, Groq risk scoring, kill switch | ✅ | — |
| Identity Graph, Flight Recorder, Autonomy, ARE | ✅ | — |
| Postgres + Redis + Prometheus + Grafana + Jaeger | ✅ | — |
| Slack alerts | ✅ (if SLACK_WEBHOOK_URL set in infra/.env) | — |
| S3 backup encryption | ✅ age-encrypted | — |
| DevOps demo Kubernetes cluster | — | MockK8sCluster (in-process dict) |
| DB Copilot SQL execution | — | ACP evaluates SQL, does not run it |
| Support demo CRM/ticketing | — | MockSupportPlatform (in-memory) |

The **governance layer is fully real.** Only the downstream systems the agent would actually touch are mocked — because running `kubectl delete namespace production` on a real cluster during a demo is not a demo.

---

## 30-MINUTE CLOCK

```
0:00  Pre-flight   — 25 healthy containers + browser tabs open
0:01  PHASE 2      — /system/health → 12/12 green                    1 min
0:02  PHASE 3      — create agent, grant permissions, get JWT         4 min
0:06  PHASE 4      — allow + path-traversal block + permission block  4 min
0:10  PHASE 5-6    — Audit Logs + Flight Recorder replay              3 min
0:13  PHASE 7-8    — Identity Graph + Autonomy contracts              3 min
0:16  PHASE 9-10   — ARE rules + Kill switch                         3 min
0:19  PHASE 11     — Slack CRITICAL alert fires live                  2 min
0:21  PHASE 12     — verify-chain + Merkle roots                      4 min
0:25  PHASE 13     — 3 enterprise demos DRY_RUN (~10 s)              2 min
0:27  PHASE 14-15  — Grafana / Jaeger + SDK 5-line integration        2 min
0:29  Closing      — 2 questions, next-step ask                       1 min
```

---

> **ACP — Every action governed. Every decision proved.**
