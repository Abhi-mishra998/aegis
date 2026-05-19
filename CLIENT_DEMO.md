# ACP Client Demo — From-Scratch Playground Walkthrough

> **What this is:** A live, end-to-end demo of ACP — runtime security for AI agents.
> You will: create an agent from zero, watch ACP block bad behaviour in real time, verify cryptographic proof, replay any decision, and run three pre-built enterprise scenarios.
>
> **What ACP does:** sits between AI agent code and the world. Blocks dangerous actions *before* they execute. Produces a tamper-evident, cryptographically verifiable audit trail of every decision.

---

## PRE-FLIGHT (do this 5 minutes before the client arrives)

```bash
# 1. Confirm the stack is healthy — must show 25+ "healthy"
docker ps --format "{{.Names}}\t{{.Status}}" | grep -c "(healthy)"

# 2. Provision admin (first boot only; idempotent)
.venv/bin/python scripts/utils/seed_admin.py

# 3. Open these browser tabs, login to UI (admin@acp.local / password):
#    http://localhost:5173         ← UI (Flight Recorder homepage)
#    http://localhost:3000         ← Grafana (admin/admin)
#    http://localhost:16686        ← Jaeger traces
#    http://localhost:9090         ← Prometheus

# 4. Open 6 terminal tabs in iTerm. Paste STEP 0 into Tab 1.
```

---

## STEP 0 — Shell variables (paste in Tab 1, reused everywhere)

```bash
export TENANT="00000000-0000-0000-0000-000000000001"

export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')

# Internal secret — used for credential provisioning (admin-only)
export INTERNAL_SECRET=$(grep "^INTERNAL_SECRET" infra/.env | cut -d= -f2)

echo "✅ Token : ${TOKEN:0:40}…"
echo "✅ Tenant: $TENANT"
```

> **Golden rule:** every command below is already typed in a separate tab. You only press Enter. Never type during a client demo.

---

## STEP 1 — Prove the system is alive (30 seconds)

> **Say:** *"This is ACP — 26 containers, 14 microservices plus infrastructure. One health endpoint tells you everything is green."*

```bash
curl -s "http://localhost:8000/system/health" \
  | jq '{status, healthy, total, p95_ms: .latency.p95_ms}'
```

Expected: `"status": "operational", "healthy": 12, "total": 12, "p95_ms": <50`

> **Now switch to UI** `http://localhost:5173`
> - Login as `admin@acp.local` / `password`
> - Land on **Flight Recorder** (homepage)
> - Click **System Health** in the sidebar → all 12/12 green, p95 < 50 ms
> - Click **Observability** → live decision feed (we'll fill this with traffic in a moment)

---

## STEP 2 — Create an agent FROM SCRATCH (live, in front of client)

> **Say:** *"Every AI agent has a per-tool permission policy. Here's what onboarding looks like — from API or UI."*

### 2a. Create the agent via API (Tab 2)

```bash
export AID=$(curl -s -X POST "http://localhost:8000/agents" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "client-demo-agent",
        "description": "Live demo agent — created during client walkthrough",
        "owner_id": "demo-presenter",
        "risk_level": "low",
        "metadata": {
          "demo": "client_walkthrough",
          "created_during": "live_demo"
        }
      }' \
  | jq -r '.data.id')

echo "✅ Agent created: $AID"
```

> **Switch to UI** → **Agents** page → **refresh** → `client-demo-agent` is at the top of the list, status `ACTIVE`.
> Click the row → details panel shows owner, risk level, metadata, empty permissions.

### 2b. Grant tool permissions (Tab 2 continued)

```bash
for tool in read_file query execute_agent; do
  curl -s -X POST "http://localhost:8000/agents/$AID/permissions" \
    -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
    -H "Content-Type: application/json" \
    -d "{\"tool_name\":\"$tool\",\"action\":\"ALLOW\"}" \
    | jq -c '{tool: "'$tool'", granted: .success}'
done
```

> **UI:** refresh the agent detail panel → permissions table now shows 3 entries: `read_file`, `query`, `execute_agent` — all `ALLOW`.

### 2c. Provision agent credentials + issue agent JWT (Tab 2 continued)

```bash
# Generate the agent secret ONCE and reuse it for both provisioning and
# the agent-token request. Using two different secrets (e.g. $(date +%s)
# vs. a fallback) makes the credential check fail and the token comes
# back null.
export AGENT_SECRET="client-demo-secret-$(date +%s)"

# Provision a secret for the agent (admin-only, signed by internal secret)
curl -s -X POST "http://localhost:8002/auth/credentials" \
  -H "X-Internal-Secret: $INTERNAL_SECRET" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"secret\":\"$AGENT_SECRET\"}" \
  | jq '{provisioned: .success}'

# Get the agent's runtime JWT (this is what the agent code uses).
# Must use the SAME secret that was just provisioned.
export AGENT_TOKEN=$(curl -s -X POST "http://localhost:8000/auth/agent/token" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"secret\":\"$AGENT_SECRET\"}" \
  | jq -r '.data.access_token')

# Sanity check — if AGENT_TOKEN is null, the secret didn't match
if [ "$AGENT_TOKEN" = "null" ] || [ -z "$AGENT_TOKEN" ]; then
  echo "❌ Agent token is null — secret mismatch between provision + token request"
else
  echo "✅ Agent JWT: ${AGENT_TOKEN:0:40}…"
fi
```

> **Narrative:** *"The agent now has a scoped JWT. Every call this token makes goes through ACP first. The agent's code doesn't change — we sit in front of it."*

---

## STEP 3 — Playground: drive the agent (most visual moment of the demo)

> **Switch to UI** → **Playground** (left sidebar)
> Select agent: `client-demo-agent`
> Tool: `read_file`

### 3a. Allowed call (Tab 3) — *expected: ALLOW, risk ≈ 0.09*

```bash
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $AGENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"path\":\"customers.csv\"}}" \
  | jq '{action, risk, findings, signals: (.signals | {inference, behavior, anomaly})}'
```

> **Watch the UI:** Observability page → **decision feed updates in real time via SSE**. You see `read_file → ALLOW` appear instantly.

### 3b. Path-traversal attack (Tab 4) — *expected: 403, blocked before execution*

```bash
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $AGENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"path\":\"../../etc/passwd\"}}" \
  | jq '{success, error, status_hint: "HTTP 403"}'
```

Expected: `"error": "Security: Prompt injection detected: '../../'"`.

> **Switch to UI** → **Audit Logs** → filter `decision=deny` → the deny appears at the top with the path traversal payload, findings `[path_traversal_detected]`, and risk score.

### 3c. Permission-denied attack (Tab 5) — *agent doesn't have `shell.exec`*

```bash
curl -s -X POST "http://localhost:8000/execute/shell.exec" \
  -H "Authorization: Bearer $AGENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"cmd\":\"rm -rf /\"}}" \
  | jq '{success, error}'
```

> **Say:** *"Three layers blocked this — JWT permission claims, Registry permission table, OPA policy. Defence in depth. Every layer logs its decision."*

---

## STEP 4 — Audit Logs (the regulator's friend)

> **Switch to UI** → **Audit Logs**
> - Filter `agent_id = client-demo-agent`
> - You see 3 events: 1 allow + 2 denies, all timestamped, all with full payload context
> - Click a row → side panel shows: request ID, signals, decision rationale, HMAC chain position

```bash
# CLI equivalent for the technical buyer. The `.data?.items // []` guard
# means a 401 (expired token) produces an empty list instead of a confusing
# jq "Cannot iterate over null" error. If the list is empty, refresh your
# token with STEP 0 — the JWT TTL is 15 minutes.
curl -s "http://localhost:8000/audit/logs?limit=5&agent_id=$AID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data?.items // [] | map({ts: .timestamp[0:19], action, decision, tool, reason})'
```

> **If you see `[]` and you ran a tool call moments ago,** your $TOKEN expired
> (15-min TTL). Re-run STEP 0 to refresh, then try again.

---

## STEP 5 — Flight Recorder: step-by-step execution replay

> **Switch to UI** → **Flight Recorder** (the homepage)
> - List of every `/execute` call across all tenants, last 90 days
> - Click any row → 3-pane: steps timeline (left), snapshot diff (middle), metadata (right)
> - The denied path-traversal call shows every phase: auth → rate limit → security signals → decision (KILL) → no execution → audit write

```bash
# CLI: pull a recent timeline ID and replay
TL_ID=$(curl -s "http://localhost:8000/flight/timelines?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data[0].id')

curl -s "http://localhost:8000/flight/timeline/$TL_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | {tool: .timeline.tool, final: .timeline.final_decision,
                 duration_ms: .timeline.duration_ms,
                 steps: (.steps | length), snapshots: (.snapshots | length)}'
```

---

## STEP 6 — Identity Graph + Blast-Radius Simulation

> **Switch to UI** → **Identity Graph**
> - Visual web: agents → users → API keys → tools → tenants
> - Click `client-demo-agent` → see what tools it's wired to, what data it touches

```bash
# Identity-graph agent nodes are named by UUID (not the human-readable name).
# Use the $AID we created in STEP 2a to match directly. If $AID isn't a graph
# node yet (no edges seeded), fall back to the first agent node so the demo
# still produces a real blast radius.
AGENT_NODE=$(curl -s "http://localhost:8000/graph/agents?limit=50" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r --arg aid "$AID" '
      ([.data.nodes[] | select(.node_type=="agent" and (.name == $aid or .id == $aid))][0].id)
      // ([.data.nodes[] | select(.node_type=="agent")][0].id)
      // empty')

if [ -z "$AGENT_NODE" ] || [ "$AGENT_NODE" = "null" ]; then
  echo "❌ No agent nodes in identity graph yet. Run the demos first:"
  echo "   ACP_DRY_RUN=1 .venv/bin/python demos/run_all_demos.py"
else
  echo "✅ Simulating compromise of $AGENT_NODE"
  curl -s -X POST "http://localhost:8000/graph/compromise/simulate" \
    -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
    -H "Content-Type: application/json" \
    -d "{\"actor_node_id\":\"$AGENT_NODE\",\"scenario\":\"stolen_token\",\"depth\":3}" \
    | jq '.data | {scenario, blast_radius, risk_score, classification: .summary.risk_classification, reachable_count: (.reachable_nodes | length)}'
fi
```

Expected output (numbers will vary):
```json
{
  "scenario": "stolen_token",
  "blast_radius": 4,
  "risk_score": 0.2125,
  "classification": "LOW",
  "reachable_count": 4
}
```

> **Narrative:** *"If this agent's token is stolen, here's exactly what an attacker can reach. We can simulate this for any agent before you onboard it."*

---

## STEP 7 — Autonomy Contracts (bounded autonomy)

> **Switch to UI** → **Autonomy** (left sidebar)

If you haven't run the demo packs yet, the contracts table is empty. Seed one for the live agent so the demo has something to show:

```bash
# Seed a contract for our client-demo-agent (idempotent: skip if 409)
curl -s -X POST "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{
        \"agent_id\":\"$AID\",
        \"name\":\"client-demo-contract\",
        \"allowed_actions\":[\"read_file\",\"query\"],
        \"denied_actions\":[\"shell.exec\",\"k8s.delete.namespace\"],
        \"approval_required\":[\"transfer_funds\",\"send_email\"],
        \"max_tool_calls\":3,
        \"max_cost_usd\":1.0,
        \"max_runtime_seconds\":300
      }" | jq -c '{seeded: .success, id: .data.id, error}'
```

Now list contracts — the new one + any contracts seeded by the 3 demo packs:

```bash
curl -s "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {name, allowed: (.allowed_actions | length),
                   denied: (.denied_actions | length),
                   max_destructive_per_hour: .max_tool_calls}'
```

> **UI:** the **Autonomy** page now shows the contract. Each row caps allowed actions, denied actions, approval-required actions, and a destructive-ops budget per hour.
>
> **Say:** *"Autonomy contracts are the seatbelt — the AI can act on its own within these bounds, anything else escalates to a human. Editable in the UI, version-tracked, every change auditable."*

---

## STEP 8 — Autonomous Response Engine (ARE)

> **Switch to UI** → **Auto-Response** (or `/auto-response`)

A fresh stack has no rules. Seed three so the demo has something concrete to show:

```bash
# Rule 1: CRITICAL severity → KILL_AGENT immediately (auto-mode)
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

# Rule 2: HIGH severity → ALERT only (no destructive action)
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

# Rule 3: Any severity catch-all → notify (SSE only, no action)
curl -s -X POST "http://localhost:8000/auto-response/rules" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "security-event-slack-notify",
        "is_active": true,
        "priority": 10,
        "conditions": {"severity_in": ["LOW","MEDIUM","HIGH","CRITICAL"]},
        "actions": [{"type": "ALERT", "params": {"channel":"slack"}}],
        "mode": "suggest"
      }' | jq -c '{seeded: .success, name: .data.name}'
```

Now list the rules:

```bash
curl -s "http://localhost:8000/auto-response/rules" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {name, severity: .conditions.severity_in, action_types: [.actions[].type], mode, enabled: .is_active}'
```

> **UI:** **Auto-Response** page now shows 3 rules. Toggle one off/on — change propagates within seconds. Click a rule → latency p50/p95, fire count, last 24h trigger history.
>
> **Say:** *"At 3 am when nobody's watching, ARE fires automatically. Kill the agent, isolate it, throttle it, alert Slack — your runbook executes itself."*

---

## STEP 9 — Kill Switch (emergency stop)

> **Switch to UI** → **Kill Switch** (or just stay in Observability)
> - Per-tenant kill switch toggle
> - Press toggle → live event feed shows every subsequent `/execute` returning 403

```bash
# Engage tenant kill switch
curl -s -X POST "http://localhost:8000/decision/kill-switch/$TENANT" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"action":"engage","reason":"client_demo"}' \
  | jq '.data.status'

# Prove it blocks everything (using the same agent JWT)
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $AGENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"parameters\":{\"path\":\"safe.txt\"}}" \
  | jq '{success, error}'
# → 403, "Tenant blocked due to security violation"

# Disengage — restore normal operation
curl -s -X DELETE "http://localhost:8000/decision/kill-switch/$TENANT" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data.status'
```

> **Killer-talking-point:** *"This survives a Redis flush. Double-written to Postgres, re-hydrated every 5 seconds. Even if your cache layer goes down, the kill switch holds."*

---

## STEP 9.5 — Slack alert (real notification fires live)

> **Open Slack** in another window — point at the security channel before pressing Enter.
>
> **Say:** *"When a CRITICAL incident hits, ACP wakes up your security team in under 2 seconds. Watch."*

The api service fires Slack on every CRITICAL/HIGH incident. The trigger is the internal `POST /incidents` endpoint — same path that the gateway uses when a hard-deny happens. Calling it manually proves the wiring end-to-end without waiting for a real attack.

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
  | jq '{
      success,
      severity: .data.severity,
      incident_number: .data.incident_number,
      status: .data.status
    }'
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

> **Switch to Slack** — within 2 seconds you see a richly-formatted Block Kit message:
> - Incident number + severity badge
> - Risk score, agent ID, tool name
> - "View in ACP" deep-link button

### Confirm Slack actually fired (not just the API response)

```bash
docker logs acp_api 2>&1 | grep "hooks.slack.com" | tail -1
```

Expected: `HTTP Request: POST https://hooks.slack.com/services/… "HTTP/1.1 200 OK"`

The `200 OK` is from Slack's edge, confirming the message reached the workspace.

### Bonus — same trigger fires the ARE rules we seeded in STEP 8

Watch the api logs while you fire another CRITICAL incident:

```bash
docker logs acp_api -f 2>&1 | grep -E "are_triggered|slack" &
sleep 1
curl -s -X POST "http://localhost:8005/incidents" \
  -H "X-Internal-Secret: $INTERNAL_SECRET" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\":\"$TENANT\",\"agent_id\":\"$AID\",\"severity\":\"CRITICAL\",\"trigger\":\"policy_deny\",\"title\":\"Second test alert\",\"tool\":\"k8s.delete.namespace\",\"risk_score\":0.99,\"explanation\":\"second slack test\"}" \
  > /dev/null
sleep 3
kill %1 2>/dev/null
```

Expected log lines:
- `are_triggered: rule="critical-risk-auto-response" actions=["ALERT:slack","KILL_AGENT"]`
- `HTTP Request: POST https://hooks.slack.com/… HTTP/1.1 200 OK` (1-2 times — one for the incident, one for the ARE rule)

> **Say:** *"This is the autonomous response part. One incident, three layers reacting simultaneously: incident logged, Slack alerted, agent killed. All in under 2 seconds. No human in the loop."*

---

## STEP 10 — Cryptographic Proof (the auditor's friend)

> **Switch to UI** → **Developer** panel (or just stay terminal)

### 10a. Get the ed25519 public key (auditors archive this)

```bash
curl -s "http://localhost:8000/receipts/key" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{algorithm, fingerprint: .public_key_fingerprint}'
```

### 10b. Pull a real signed receipt

```bash
EXEC_ID=$(curl -s "http://localhost:8000/audit/logs?limit=20" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '[.data.items[] | select(.action=="execute_tool" and .decision=="allow")][0].id')

curl -s "http://localhost:8000/receipts/$EXEC_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{algorithm, sig_length: (.signature | length), fingerprint}'
```

Expected: `"algorithm": "ed25519", "sig_length": 86`

### 10c. Verify the *entire* audit chain — court-admissible

```bash
.venv/bin/acp verify-chain \
  --base-url http://localhost:8000 \
  --token "$TOKEN" \
  --tenant "$TENANT" \
  --json \
  | jq '{valid: .ok, processed, errors: .total_violations}'
```

Expected: `"valid": true, "errors": 0`

> **Say (slowly):** *"Hundreds of decisions just got proven mathematically intact. Your auditor doesn't need to trust ACP — they archive the public key and the daily Merkle root, then verify offline whenever they want."*

### 10d. Daily Merkle root chain (transparency log)

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

Expected output (one entry per sealed day):

```json
{
  "root_date": "2026-05-18",
  "leaf_count": 119,
  "root_hash": "fec8c14a6193bfdfa3b62958…",
  "algorithm": "ed25519",
  "key_fingerprint": "5615db00ca28c2e792dc7e3d5d70f3c0"
}
```

> *"Every day's audit chain is sealed with a Merkle root. Each root is ed25519-signed and links to the previous via `prev_root_hash`. Total signing-key compromise is publicly detectable to anyone who archived an earlier root."*
>
> **Note:** roots are sealed once per day by the transparency-log scheduler. On a freshly-started stack the list may be empty until the first seal runs (or you can trigger one manually via the audit service's `/logs/transparency/seal` endpoint).

---

## STEP 11 — The Three Enterprise Demos (one command, three full scenarios)

> **Switch to UI** → **Flight Recorder** so the client can see executions flowing in real time while the demos run.

```bash
# Offline (DRY_RUN) — instant, no Groq calls, ~10s total
ACP_DRY_RUN=1 .venv/bin/python demos/run_all_demos.py
```

Expected summary:
```
Pack 1: AI Database Copilot     PASS    ~4s
Pack 2: AI DevOps Agent         PASS    ~4s
Pack 3: AI Support Agent        PASS    ~3s
All scenarios passed. Demo platform ready.
```

Each pack prints a **truth-only summary** — every line is filled in by the scenario as it ran, never hard-coded. If anything fails, the line shows `✗ FAIL` with the actual reason.

### Or run LIVE against the stack (~46s, real Groq calls, real audit chain):

```bash
.venv/bin/python demos/run_all_demos.py
```

Watch in the UI: Flight Recorder lights up with timelines, Audit Logs shows hundreds of events, Identity Graph shows new nodes for the seeded agents/customers/tools.

### What each pack proves

| Pack | Scenarios | Key proof points |
|---|---|---|
| **1. DB Copilot** | safe SELECT · bulk SELECT * · PII columns · DROP TABLE · kill switch | DDL hard-deny + token revocation + PII column filter |
| **2. DevOps Agent** | reads · scaling · `delete namespace` · `clusterrolebinding` · blast radius · autonomy budget · rate-limit storm · kill switch · chain verify | K8s hard-deny + 3-op/hr destructive budget + true rate-limit 429 |
| **3. Support Agent** | ticket lookup · single-customer PII · cross-tenant attack · bulk PII export · email exfiltration · runaway burst · chain verify | Tenant isolation + email hard-deny against `allowed_email_domain` + 30 req/min rate-limit |

---

## STEP 12 — Observability (engineers love this part)

> **Switch to Grafana** at `http://localhost:3000` (admin / admin)
> Four pre-built dashboards:
> - **Platform SLO** — `/execute` p50/p95/p99, error rate, rate-limit breakdown
> - **Trust Layers** — chain integrity, reconcile gap, behavior consult mix
> - **Tenant Activity** — per-tenant request rate, cost, quota usage
> - **Queue Health** — every stream depth, DLQ length, outbox age

> **Switch to Jaeger** at `http://localhost:16686`
> - Select service: `gateway`
> - Recent traces — every `/execute` call as a full span tree:
>   auth → rate-limit → OPA → decision → audit → response
> - Click a span → exact ms breakdown per phase

> **Switch to Prometheus** at `http://localhost:9090`
> Try a query: `rate(acp_gateway_requests_total[1m])` — live request rate.

---

## STEP 13 — Python SDK (for the engineering buyer)

> **Switch to terminal — Tab 6**

```bash
export ACP_AGENT_ID="$AID"
export ACP_TOKEN="$AGENT_TOKEN"
.venv/bin/python examples/agent.py
```

Expected:
```
allow  → [{'row': '1', 'value': 'contents of customers.csv'}]
deny   → DeniedError: shell.exec blocked by policy
```

The example is **5 lines of code** — one decorator:

```python
import acp

client = acp.Client()                          # reads ACP_TOKEN from env
@client.protect(agent_id=ACP_AGENT_ID)
def read_data(path: str) -> list[dict]:
    return open(path).read()                   # only runs if ACP allows it
```

> **Say:** *"Your engineers add the decorator. ACP does the rest. Policy enforcement, audit, receipts — all automatic."*

---

## STEP 14 — Billing Durability (the CFO's friend)

> **Switch to UI** → **Billing**
> - Per-tool cost breakdown
> - Per-tenant invoice trail
> - **Reconciliation gauge: 0 gaps**

```bash
python scripts/ops/reconcile.py --tenant "$TENANT" --json \
  | jq '{status, audit_without_usage_count, usage_without_audit_count}'
```

Expected: `"status": "VERIFIED"`, both counts = 0.

> **Killer-talking-point:** *"Transactional outbox pattern — audit and billing written in one DB transaction. If anything crashes between them, the outbox worker drains the gap on restart. Zero revenue leakage, ever — provable."*

---

## STEP 15 — Encrypted Backup (the compliance officer's friend)

```bash
PGPASSWORD=postgres POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
  bash scripts/ops/backup.sh --no-verify
```

Expected: `✓ PASSED — 8 databases backed up to s3://acp-backups-…`

> *"age-encrypted, private key never leaves your machine. Restore drill is automated — `scripts/ops/restore_drill.sh` on an isolated Docker network."*

---

## CLOSING — Two questions for the room

> **Question 1:** *"Right now, today — if one of your AI agents tried to delete your production database, how long would it take you to find out? And could you prove exactly what happened?"*
>
> **Question 2:** *"When your auditors ask you next quarter to produce a complete, tamper-proof log of every action every AI agent took — what will you hand them?"*

**ACP's answers:**

| Question | ACP's answer |
|---|---|
| 1 | *"You'd know in 47 ms. The action would already be blocked. You'd have a signed, tamper-proof record of the attempt."* |
| 2 | *`acp verify-chain` → `valid: true, errors: 0`, processed in seconds. Done."* |

> **Next step:** *"Could we deploy this around one of your agents next week — your tools, your workflows, our pilot?"*

---

## Token expiry safety net (every 15 minutes)

If you get a 401 on any command, your admin token expired. Just re-run STEP 0:

```bash
export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')
```

---

## What's mocked, what's real

| Surface | Real | Mocked |
|---|---|---|
| ACP gateway, policy engine, audit chain, receipts | ✅ Real | — |
| OPA policy evaluation, Groq risk scoring, kill switch | ✅ Real | — |
| Identity Graph, Flight Recorder, Autonomy contracts, ARE | ✅ Real | — |
| Postgres + Redis + Prometheus + Grafana + Jaeger | ✅ Real | — |
| DevOps demo Kubernetes cluster | — | `MockK8sCluster` (in-process dict) |
| DB Copilot demo SQL execution | — | ACP evaluates SQL, doesn't run it |
| Support demo CRM/ticketing | — | `MockSupportPlatform` (in-memory) |
| Slack alerts | ✅ Real (if `SLACK_WEBHOOK_URL` set in `infra/.env`) | — |
| S3 backup encryption | ✅ Real age encryption | — |

The **governance layer is fully real**. Only the *downstream systems the agent would actually touch* are mocked, because we shouldn't `kubectl delete namespace production` on a real cluster during a demo.

---

## The exact 30-minute clock

```
0:00  Pre-flight (already done; just confirm 25 healthy containers + UI up)
0:01  STEP 1   — /system/health → 12/12 green
0:03  STEP 2   — create agent live, grant permissions, get JWT       [3 min]
0:06  STEP 3   — Playground: allow + 2 blocks                        [4 min]
0:10  STEP 4-5 — Audit Logs + Flight Recorder replay                 [3 min]
0:13  STEP 6-7 — Identity Graph + Autonomy contracts                 [3 min]
0:16  STEP 8-9 — ARE rules + Kill switch                             [3 min]
0:19  STEP 10  — Cryptographic verify-chain + Merkle roots           [4 min]
0:23  STEP 11  — 3 enterprise demos in DRY_RUN (~10s)                [2 min]
0:25  STEP 12  — Grafana + Jaeger + Prometheus tour                  [2 min]
0:27  STEP 13  — Python SDK 5-line integration                       [2 min]
0:29  Closing  — 2 questions, next-step ask                          [1 min]
```

---

> **ACP — Every action governed. Every decision proved.**
.venv/bin/locust -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --web-port 8090