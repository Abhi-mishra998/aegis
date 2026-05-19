# 🚀 ACP Enterprise Demo Runbook — Client-Ready (2026-05-15)

> **Tamper-evident replay + runtime deny for AI agents.**
> Every command in this runbook is **tested end-to-end against the running stack**.
> Just paste and go.

**Verified 2026-05-15 (post Crypto-Trust sprint)** — receipts return flattened
`{algorithm, signature, fingerprint, public_key_fingerprint}` at the top level,
`/status` is public + reports `uptime_seconds` + `p95_latency_ms` + service
counts, SSE `/events/stream` accepts `?token=` query fallback (browsers'
`EventSource` cannot set custom headers), transparency roots form a signed
Merkle-of-Merkles chain via `prev_root_hash`, four new server endpoints
(`/receipts/verify`, `/transparency/verify-root`, `/transparency/consistency`,
`/transparency/keys`) and two new CLI commands (`acp verify-chain`,
`acp verify-root`) make the system independently verifiable with zero
residual trust in ACP infrastructure.

## What this product is (and isn't)

ACP is a runtime gateway in front of your AI agents. Two jobs:

1. **Deny dangerous actions before they execute** — policy enforcement + autonomy guardrails at runtime.
2. **Prove what happened after the fact** — tamper-evident audit chain + cryptographic receipts, replayable from the Flight Recorder for 90 days.

It is **not** an agent framework, an LLM inference provider, or a general-purpose APM. It sits between your agent code and the world. One product, not a platform.

The fastest way to evaluate: open [`docs/quickstart.md`](docs/quickstart.md). Five lines of code, one policy file.

The primary daily surface in the UI is **Flight Recorder** (homepage after login). Policies, Audit Trail, Incidents, and Settings round out the five primary nav items. Everything else lives under **Operations** (collapsible) or **Settings** (admin hub).

---

What's new in this update (vs. prior runbooks):

- ✅ **Transactional outbox** (audit→billing) — durability backstop, no integrity gap on container kills
- ✅ **Prometheus + Grafana** at ports 9090 / 3000 — operator dashboards live
- ✅ **4-state health classification** — `operational / degraded_performance / partial_outage / major_outage`
- ✅ **Gateway proxy propagates upstream status** — no more silent 200/null masking
- ✅ **Multi-worker** stateless services (gateway/decision/policy/behavior/forensics)
- ✅ **Groq AI Threat Insights** wired end-to-end (3 producer sites + worker fix)
- ✅ **DR runbook** at `docs/dr_runbook.md` with RPO 15m / RTO 30m targets
- ✅ **Cryptographic receipts + transparency log** — `/receipts/*` and `/transparency/*` now reachable through the UI nginx proxy (Flight Recorder receipt panel + offline-verifiable signatures)
- ✅ **Python SDK runnable example** at `examples/agent.py` — five-line `@acp.protect` integration
- ✅ **Hot-path tunables externalized** — payload cap, concurrency, decision timeouts now in `.env` (no recompile to tune)
- ✅ **Gateway hardening** — bare `except:` blocks replaced with typed handlers + structured logging on error paths

---

## 0. One-time setup (skip if already done)

### 0a. Required environment variables

Set these in your shell profile (or a `.env` you `source` before running
demos / ops scripts). The runtime stack reads them from
`infra/docker-compose.yml`; the ops scripts (`scripts/ops/*`) read them
from the host shell.

```bash
# ── Internal secret used by every service-to-service call.
#    Same value must appear in infra/.env so docker-compose passes it
#    to every container. Generate once, rotate annually.
export INTERNAL_SECRET="$(openssl rand -hex 32)"

# ── Backup encryption (Sprint 3.4). NEVER commit either key to the repo.
#    Provision out-of-band (KMS / 1Password / hardware key).
#    age is preferred; gpg fallback documented.
brew install age
age --version
age-keygen --version
mkdir -p ~/.acp
chmod 700 ~/.acp
age-keygen -o ~/.acp/backup-private.age
chmod 600 ~/.acp/backup-private.age
nano ~/.zshrc
source ~/.zshrc
echo $ACP_BACKUP_AGE_RECIPIENT
echo $ACP_BACKUP_AGE_IDENTITY

export ACP_BACKUP_AGE_RECIPIENT="age1abc...your_public_key"   # encryption recipient
export ACP_BACKUP_AGE_IDENTITY="$HOME/.acp/backup-private.age" # decrypt key file

6. Test encryption
echo "hello backup" > test.txt
age -r "$ACP_BACKUP_AGE_RECIPIENT" \
    -o test.txt.age \
    test.txt
    rm test.txt


7. Test decryption
Decrypt:
    age -d \
    -i "$ACP_BACKUP_AGE_IDENTITY" \
    -o restored.txt \
    test.txt.age

cat restored.txt

# ── S3 (or MinIO / Wasabi / any S3-compatible) for encrypted backups
export ACP_BACKUP_S3_BUCKET="s3://acp-backups/prod"
export ACP_BACKUP_S3_ENDPOINT="https://s3.example.com"        # OPTIONAL: MinIO etc.

# ── Slack webhook for billing alerts (monthly-quota 80% + inference-cost 80%).
#    Sprint 3.2 + 3.5 push payloads to the Redis stream `acp:billing_alerts`;
#    the notification worker (or a simple ops sidecar) forwards them.
export ACP_SLACK_WEBHOOK="https://hooks.slack.com/services/T.../B.../..."

# ── Per-service DSNs used by scripts/ops/reconcile.py and export_tenant.py
# Host port 5433 is the docker-compose mapping for acp_postgres (see
# infra/docker-compose.yml). Inside containers the port is 5432.
export ACP_AUDIT_DB="postgresql://postgres:postgres@localhost:5433/acp_audit"
export ACP_USAGE_DB="postgresql://postgres:postgres@localhost:5433/acp_usage"
export ACP_IDENTITY_DB="postgresql://postgres:postgres@localhost:5433/acp_identity"
export ACP_FLIGHT_DB="postgresql://postgres:postgres@localhost:5433/acp_flight_recorder"
export ACP_GRAPH_DB="postgresql://postgres:postgres@localhost:5433/acp_identity_graph"
export ACP_AUTONOMY_DB="postgresql://postgres:postgres@localhost:5433/acp_autonomy"

# ── Gateway URL — used by reconcile.py --watch and the soak harness
export GATEWAY_URL="http://localhost:8000"
```

### 0b. MinIO sidecar (local backup target — optional)

If you don't have an S3 endpoint handy, run MinIO in a sibling container
and point `ACP_BACKUP_S3_*` at it.

```bash
docker run -d --name acp_minio --network acp_default \
  -p 9001:9001 -p 9000:9000 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"
# Bucket
docker exec acp_minio mc alias set local http://localhost:9000 minioadmin minioadmin
docker exec acp_minio mc mb local/acp-backups
export ACP_BACKUP_S3_BUCKET="s3://acp-backups/prod"
export ACP_BACKUP_S3_ENDPOINT="http://localhost:9000"
```

### 0c. Generate backup keys (one-time)

```bash
# age keypair — recipient (public) goes into ACP_BACKUP_AGE_RECIPIENT,
# identity (private) stays on disk + injected into restore_drill via
# ACP_BACKUP_AGE_IDENTITY.
mkdir -p "$HOME/.acp" && chmod 700 "$HOME/.acp"
age-keygen -o "$HOME/.acp/backup-private.age"
# The first line of the file is the recipient — copy it.
grep '^# public key:' "$HOME/.acp/backup-private.age" | awk '{print $4}'
# → export ACP_BACKUP_AGE_RECIPIENT="age1..."
```

### 0d. Slack relay for `acp:billing_alerts` stream

Sprint 3.2 (per-tenant quota 80%) and Sprint 3.5 (inference cost 80%)
both push warning payloads onto the Redis stream `acp:billing_alerts`.
A 10-line sidecar relays them to Slack:

```bash
# scripts/ops/billing_alerts_relay.py — run this once, OR drop it into
# docker-compose as a sidecar. Reads from acp:billing_alerts and posts
# to ACP_SLACK_WEBHOOK. Idempotent at the consumer group level.
# (Reference implementation lives at docs/runbooks/billing_alerts.md.)
```

If you don't want Slack, the events still land in Redis — `redis-cli
XLEN acp:billing_alerts` lets the SOC pull them on demand.

Verified relay script: `scripts/ops/billing_alerts_relay.py`. Run it
in any host shell (durable consumer group survives restarts):

```bash
REDIS_URL=redis://localhost:6379/0 \
ACP_SLACK_WEBHOOK="$ACP_SLACK_WEBHOOK" \
    .venv/bin/python scripts/ops/billing_alerts_relay.py
```

### 0e. Bring up the stack

```bash
# Bring everything up (14 HTTP services + workers + infra + observability = 25 containers)
cd infra
docker compose down -v       # only if you've had partial state
docker compose up --build -d
sleep 90                     # 25 containers + healthchecks

# Seed the admin user — REQUIRED. Every login path below assumes this
# ran at least once. If `/auth/token` returns "Invalid credentials",
# the seed didn't run. Script is idempotent.
cd ..
.venv/bin/python scripts/utils/seed_admin.py

# Apply the latest alembic migrations (idempotent — picks up the
# Sprint 1.1 / 1.3 / 3.2 / 3.5 columns: degraded_mode_policy,
# prev_root_hash, transparency_historical_keys, leaf_range_*_id,
# signing_key_fingerprint, requests_per_second + burst +
# daily_request_cap + monthly_request_cap + daily_inference_cost_cap_usd,
# plus kill_switches table for Redis-resilient kill switch persistence).
docker exec acp_audit bash -lc "cd /app/services/audit && python -m alembic upgrade head"
docker exec acp_identity bash -lc "cd /app/services/identity && python -m alembic upgrade head"
# behavior_profiles table for the learning/behavior engine (runs inside acp_behavior)
docker exec acp_behavior bash -lc "cd /app/services/learning && python -m alembic upgrade head"
```

### 0f. Local Python deps (host shell — for ops scripts + soak harness)

The ops scripts (`scripts/ops/*.py`) and the soak harness run on the
host, not in a container. Install their dependencies with the venv's
own interpreter explicitly — on systems where `pip` and `python` got
out of sync (3.14 pip vs 3.11 python is common), the bare `pip install`
ends up writing to a sibling site-packages the runtime never sees:

```bash
.venv/bin/python -m pip install psycopg2-binary redis locust httpx
.venv/bin/python -c "import psycopg2, redis, httpx; print('host deps ok')"
```

### 0g. Resolve the gateway's INTERNAL_SECRET

Several admin endpoints (`/auth/tenants`, `/internal/*`) need the
shared secret. Pull whatever the running container already has so the
host shell and the container agree:

```bash
export INTERNAL_SECRET=$(docker exec acp_gateway sh -c 'echo $INTERNAL_SECRET')
[ -n "$INTERNAL_SECRET" ] && echo "✓ INTERNAL_SECRET (${#INTERNAL_SECRET} chars)"
```

### Optional: hot-path tunables (defaults are sane for the demo)

These live in `sdk/common/config.py` and can be overridden via `.env` without a rebuild. Useful when sizing for a customer-specific workload.

| Env var | Default | What it controls |
|---|---|---|
| `MAX_CONCURRENT_EXECUTION` | `500` | Gateway backpressure semaphore on `/execute` |
| `MAX_PAYLOAD_BYTES` | `10000` | Absolute payload size cap at gateway ingress |
| `DECISION_REGISTRY_TIMEOUT_READ` | `0.6` | Per-call read budget for the registry leg |
| `DECISION_GATHER_TIMEOUT_READ` | `0.8` | Per-call read budget for the policy+behavior fan-out |
| `DECISION_GATHER_TOTAL_TIMEOUT` | `1.0` | `asyncio.wait_for` cap on the parallel fan-out |

Expect 25 containers running. Service map:

| Tier | Service | Port | Status |
|---|---|---|---|
| Edge | gateway (4 workers) | 8000 | healthy |
| Edge | ui | 5173 | healthy |
| Identity | identity / registry | 8002 / 8001 | healthy |
| Policy/Decision | policy (4 workers) / decision (4 workers) | 8003 / 8010 | healthy |
| Audit | audit (+stream consumer + outbox worker) | 8004 | healthy |
| Billing | usage | 8006 | healthy |
| AI | behavior (4 workers) | 8007 | healthy |
| Insights | insight / insight_worker / groq_worker | 8011 | healthy |
| Forensics | forensics (2 workers) | 8012 | healthy |
| Runtime Trust | identity_graph / flight_recorder / autonomy | 8013 / 8014 / 8015 | healthy |
| API | api (incidents / ARE) | 8005 | healthy |
| Infra | postgres / redis / pgbouncer / opa / bundle_server | 5433 / 6379 / 6432 / 8181 / 8182 | healthy |
| **Observability** | **prometheus / alertmanager / grafana** | **9090 / 9093 / 3000** | **healthy** |

> **Note**: `services/intelligence/` and `services/learning/` are Python modules embedded in `behavior`, not deployed containers. See their `README.md` files for details.

---

## 1. Single copy-paste setup block ⭐ (token + agent + permission)

Paste this whole block once. All later phases reuse `$TENANT`, `$TOKEN`, `$AGENT_ID`.

```bash
export TENANT="00000000-0000-0000-0000-000000000001"

# ─── Login (cookie + body token; we use the body token for curl) ───
export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')

[ "$TOKEN" = "null" ] || [ -z "$TOKEN" ] && { echo "❌ login failed"; exit 1; }
echo "✅ Token: ${TOKEN:0:30}…"

# ─── Create a demo agent (description MUST be ≥ 10 chars) ───
AGENT_RESPONSE=$(curl -s -X POST "http://localhost:8000/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"demo_reader",
    "description":"Reads sample files for the demo",
    "owner_team":"dev",
    "framework":"custom",
    "risk_level":"low",
    "created_by":"00000000-0000-0000-0000-000000000001"
  }')
export AGENT_ID=$(echo "$AGENT_RESPONSE" | jq -r '.data.id')

if [ "$AGENT_ID" = "null" ] || [ -z "$AGENT_ID" ]; then
  echo "⚠️  Agent might already exist — fetching existing one:"
  export AGENT_ID=$(curl -s "http://localhost:8000/agents?limit=10" \
    -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
    | jq -r '.data.data[] | select(.name=="demo_reader") | .id' | head -1)
fi
echo "✅ Agent: $AGENT_ID"

# ─── Grant read_file permission (idempotent — already-exists returns success:false) ───
curl -s -X POST "http://localhost:8000/agents/$AGENT_ID/permissions" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"read_file","action":"ALLOW",
       "granted_by":"00000000-0000-0000-0000-000000000001"}' \
  | jq '{success, permission_id: .data.id, error}'
```

---

## 2. Health check — 4-state classification + latency scopes ⭐

Sprint 2.3 gave both endpoints a canonical `latency` block with a
`scope` label so customers know which number is which:

* `/status` → `scope=gateway_internal` (request received → response sent)
* `/system/health` → `scope=end_to_end` (gateway → downstream probe RTT)

```bash
# Gateway-internal latency (the dashboard / customer-monitoring view)
curl -s "http://localhost:8000/status" \
  | jq '{status, uptime_seconds, latency, kill_switch}'
# Expected: latency.scope == "gateway_internal", kill_switch.engaged == false

# End-to-end latency (operator view, includes downstream RTT)
curl -s "http://localhost:8000/system/health" \
  | jq '{status, healthy, total, summary, queues, latency}'
# Expected: latency.scope == "end_to_end"
```

Expected (note: `/system/health` is **unauthenticated** — operational endpoint for k8s/Datadog/ALB):

```json
{
  "status": "operational",                ← one of: operational | degraded_performance | partial_outage | major_outage
  "healthy": 12,
  "total": 12,
  "summary": {
    "down_services":   0,
    "queue_pressure":  false,             ← stream/DLQ/outbox saturation
    "latency_pressure": false,            ← p95 of probe latencies
    "p95_latency_ms":  18
  },
  "queues": {
    "audit_stream_length": 5031,
    "audit_dlq_length":    0,
    "billing_retry_queue": 0,
    "billing_dlq_length":  0,
    "outbox_pending":      0,             ← Transactional Outbox backlog
    "outbox_failed":       0              ← Poison events (alertable)
  }
}
```

**Key invariant per the production_hardening_spec**: queue depth alone NEVER promotes status to `outage` — only unreachable services do. Queue/latency pressure caps at `degraded_performance`.

---

## 3. Bounded Autonomy Contract (Feature 3)

```bash
curl -s -X POST "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{
    \"agent_id\": \"$AGENT_ID\",
    \"name\": \"demo_safety_$(date +%s)\",
    \"enabled\": true,
    \"allowed_actions\": [\"read_file\", \"query\", \"db.query\"],
    \"denied_actions\":  [\"delete_*\", \"external_http_calls\"],
    \"approval_required\": [\"payment_above_10000\"],
    \"max_runtime_seconds\": 20,
    \"max_tool_calls\": 10,
    \"max_cost_usd\": 50,
    \"max_autonomy_level\": 2,
    \"notes\": \"Demo safety contract\"
  }" | jq '.data | {id, name, version, allowed_actions, denied_actions, max_cost_usd}'
```

Expected: full contract object with a real UUID `id` and `version: 1`.

---

## 3b. Per-tenant quota + inference cost cap (Sprints 3.2 + 3.5) ⭐

Three-layer per-tenant rate limit (token-bucket rps+burst, UTC-day
counter, UTC-month counter) plus a daily USD cap on inference calls.
All four limits live on the tenant row and update via `POST /auth/tenants`.

```bash
# Configure quota for the demo tenant — gentle for the soak demo so we
# can demonstrate the 429 → audit row → /tenant/quota path quickly.
# IMPORTANT: this endpoint requires BOTH a valid admin JWT (from §1) AND
# the X-Internal-Secret header. Source $INTERNAL_SECRET from §0g.
curl -s -X POST "http://localhost:8000/auth/tenants" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Internal-Secret: $INTERNAL_SECRET" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$TENANT\", \"name\": \"demo\",
    \"tier\": \"enterprise\", \"rpm_limit\": 0,
    \"requests_per_second\": 10, \"burst\": 20,
    \"daily_request_cap\": 100000, \"monthly_request_cap\": 1000000,
    \"daily_inference_cost_cap_usd\": 5.00
  }" | jq '{status, tier, rps:.requests_per_second, burst, daily:.daily_request_cap, cost_cap:.daily_inference_cost_cap_usd}'

# The gateway caches tenant_meta for 10 minutes — invalidate so the
# new limits take effect immediately (otherwise /tenant/quota and the
# rate-limit middleware will see the previous values).
docker exec acp_redis redis-cli DEL "acp:tenant:meta:$TENANT"

# Read the live quota + usage view (Sprint 3.2 + 3.5)
curl -s "http://localhost:8000/tenant/quota" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{limits, usage}'
# Expected: limits.requests_per_second=10, limits.burst=20,
# limits.daily_inference_cost_cap_usd=5.0, usage shows live counters.
```

### Hot-config per-agent inference cost cap (Sprint 3.5)

Per-agent caps live in Redis so an operator can throttle a runaway
agent instantly without a DB migration:

```bash
# Throttle a specific agent to 1¢/day (emergency response)
docker exec acp_redis redis-cli SET "acp:agent_cost_cap:$AGENT_ID" 0.01
# Lift the cap
docker exec acp_redis redis-cli DEL "acp:agent_cost_cap:$AGENT_ID"
```

### Demo: hammer the rps cap → 429 with `Retry-After`

```bash
# Fire 30 requests as fast as possible; ~⅔ should return 429 since
# burst=20 + rps=10.
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code} " \
    -X POST "http://localhost:8000/execute/read_file" \
    -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
    -H "X-Agent-ID: $AGENT_ID" \
    -H "Content-Type: application/json" \
    -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"sample_$i.txt\"}}"
done; echo
# Expected: a mix of 200 and 429. Each 429 response body includes
# {limit_type:"rps", reset_at, retry_after_s}.

# Confirm audit rows landed with action="rate_limited"
docker exec acp_postgres psql -U postgres -d acp_audit -tA -c \
  "SELECT action, decision, COUNT(*) FROM audit_logs
   WHERE action='rate_limited' AND tenant_id='$TENANT'
   GROUP BY action, decision;"
```

## 4. Happy-path tool execution

```bash
# Single execution
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"sample.txt\"},
       \"metadata\":{\"tokens\":100}}" \
  | jq '{success, action, risk, request_id, reasons, signals}'

# 5 executions to populate Behavioral Flow + Identity Graph + Flight Recorder
for i in 1 2 3 4 5; do
  curl -s -X POST "http://localhost:8000/execute/read_file" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Tenant-ID: $TENANT" \
    -H "X-Agent-ID: $AGENT_ID" \
    -H "Content-Type: application/json" \
    -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"file_$i.txt\"},
         \"metadata\":{\"tokens\":100}}" \
  | jq -r "\"call $i: action=\(.action // \"?\") risk=\(.risk // \"?\") success=\(.success // false)\""
done
```

Expected: `success: true, action: "allow"`, `risk` around 0.27. Each
request_id is recorded in audit + flight_recorder + identity_graph.

### 4b. Decision response shape (Sprints 2.2 + 1.6) ⭐

Sprint 2.2 split the response into `findings` (canonical-vocabulary
findings only) + `signals_evaluated` (diagnostic per-classifier
{score, threshold, triggered}). The legacy `reasons` field remains
as a deprecated alias for one release; the gateway adds
`Deprecation: response-field=reasons; use=findings` to every response.
Sprint 1.6: `/execute` NEVER returns 202 — only 200 / 403 / 429 / 502 / 504.

> **C9 fix (2026-05-16)**: `findings` was previously null in all `/execute` responses
> because the gateway copied `reasons` into the response dict but omitted `findings`.
> This is now fixed — `findings` is fully populated on every response.

```bash
# Inspect the canonical shape — both findings and reasons should be populated
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"clean.txt\"}}" \
  | jq '{findings, reasons, action, risk}'
# Expected: findings is a non-null array (same values as reasons), action="allow"

# Verify the Deprecation header is present
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"clean.txt\"}}" \
  -D - | grep -i deprecation
# Expected: Deprecation: response-field=reasons; use=findings

# Pull the canonical vocabulary the server enforces
curl -s "http://localhost:8000/openapi.json" \
  | jq '.paths."/execute/{tool_name}".post.responses | keys'
# Expected: ["200","403","429","502","504"]   — never 202
```

---

## 5. Security demos (each produces a real-time Groq insight)

### 5a. Path traversal → 403 + Groq insight generated

```bash
for i in 1 2 3; do
  curl -s -X POST "http://localhost:8000/execute/read_file" \
    -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
    -H "X-Agent-ID: $AGENT_ID" \
    -H "Content-Type: application/json" \
    -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"../../etc/passwd_$i\"}}" \
    | jq '{success, error, code: .meta.code}'
done

# Verify Groq enrichment happened (within ~2s)
sleep 3
curl -s "http://localhost:8000/insights/recent?limit=3" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {threat_classification, confidence, narrative: (.narrative | .[0:90])}'
```

Expected (Groq enrichment populates within ~2 seconds):
```
{
  "threat_classification": "DATA_EXFILTRATION",
  "confidence": "HIGH",
  "narrative": "The blocked event indicates a potential data exfiltration attempt..."
}
```

### 5b. Autonomy contract block → 403

```bash
curl -s -X POST "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"name\":\"lockdown_$(date +%s)\",
       \"enabled\":true,\"denied_actions\":[\"read_file\"]}" \
  | jq '.data | {id, name, denied_actions}'

curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"sample.txt\"}}" \
  | jq '{success, error, code: .meta.code}'

# Disable the lockdown contract so the rest of the demo works
LOCKDOWN_ID=$(curl -s "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data[] | select(.name | startswith("lockdown_")) | .id' | head -1)
[ -n "$LOCKDOWN_ID" ] && curl -s -X DELETE "http://localhost:8000/autonomy/contracts/$LOCKDOWN_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{disabled: (.data.enabled == false)}'
```

### 5c. Behavior service degraded → fail-CLOSED

```bash
docker stop acp_behavior && sleep 6
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"sample.txt\"}}" \
  | jq '{action, risk, reasons}'
docker start acp_behavior
```

Reason list will include `"behavior_service_unavailable"`. UI `/observability` shows the yellow **BEHAVIOR DEGRADED — fail-closed mode** banner within 30s.

### 5d. Kill switch — Redis-resilient emergency stop ⭐ (C8 fix 2026-05-16)

Kill switches are persisted to the `kill_switches` table in `acp_audit` **and** cached in Redis.
A Redis FLUSHDB or container restart no longer clears active security blocks.

```bash
# Engage the kill switch for the demo tenant
curl -s -X POST "http://localhost:8000/decision/kill-switch/$TENANT" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"action":"engage"}' | jq '{status: .data.status}'
# Expected: {"status": "engaged"}

# Confirm /execute is blocked (403)
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"test.txt\"}}" \
  | jq '{success, error}'
# Expected: success=false, error contains "kill_switch"

# Prove it survives a Redis flush (DB persistence)
docker exec acp_redis redis-cli FLUSHDB
sleep 2
curl -s -X POST "http://localhost:8000/execute/read_file" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"parameters\":{\"path\":\"test.txt\"}}" \
  | jq '{success, error}'
# Expected: STILL blocked (gateway rehydrates Redis from kill_switches DB table)

# Disengage to restore demo access
curl -s -X POST "http://localhost:8000/decision/kill-switch/$TENANT" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"action":"disengage"}' | jq '{status: .data.status}'
```

---

## 6. Agent Identity Graph + Compromise Simulation (Feature 1 + 5)

```bash
# Live graph topology
curl -s "http://localhost:8000/graph/agents?limit=20" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | {nodes: (.nodes | length), edges: (.edges | length)}'

# Trust posture
curl -s "http://localhost:8000/graph/trust-boundaries" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data'

# Pick the agent node ID for the compromise simulation
export AGENT_NODE_ID=$(curl -s "http://localhost:8000/graph/agents?limit=20" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data.nodes[] | select(.node_type=="agent") | .id' | head -1)
echo "Actor node: $AGENT_NODE_ID"

# Run a stolen-token compromise simulation
curl -s -X POST "http://localhost:8000/graph/compromise/simulate" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"actor_node_id\":\"$AGENT_NODE_ID\",
       \"scenario\":\"stolen_token\",\"depth\":3}" \
  | jq '.data | {scenario, blast_radius, risk_score,
                 classification: .summary.risk_classification,
                 reachable: (.reachable_nodes | length)}'
```

---

## 7. Flight Recorder Replay (Feature 2)

```bash
# List recent timelines (2/5/15/60 minute windows in UI)
curl -s "http://localhost:8000/flight/timelines?minutes=60&limit=5" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {request_id, tool, final_decision, duration_ms, status}'

# Pull a single step-by-step replay
TL_ID=$(curl -s "http://localhost:8000/flight/timelines?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data[0].id')
curl -s "http://localhost:8000/flight/timeline/$TL_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | {tool: .timeline.tool, steps: (.steps | length),
                 snapshots: (.snapshots | length),
                 final_decision: .timeline.final_decision}'
```

---

## 7b. Cryptographic Receipts + Transparency Log ⭐

Every successful `execute_tool` produces an ed25519-signed receipt. A daily
Merkle root commits over every receipt for the day. **2026-05-15 crypto
sprint**: each daily root now also commits to the immediately previous day's
root_hash via `prev_root_hash`, so the daily roots form an append-only chain
— a customer who archives a single root can detect any retroactive rewrite
of the history afterwards.

```bash
# Pull the gateway's signing key (cache this client-side)
curl -s "http://localhost:8000/receipts/key" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{algorithm, public_key_fingerprint: (.fingerprint // .public_key_fingerprint), pem_present: (.public_key_pem != null)}'

# Grab a recent execution's request_id and fetch its signed receipt.
# 2026-05-15: response is FLATTENED at the gateway — top-level keys are
# `algorithm`, `signature`, `public_key_fingerprint`, `receipt`, plus a
# legacy `fingerprint` alias for older probe scripts.
EXEC_ID=$(curl -s "http://localhost:8000/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data.items[0].id')
curl -s "http://localhost:8000/receipts/$EXEC_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{algorithm, fingerprint, public_key_fingerprint, sig_len: (.signature | length)}'

# Today's signed daily Merkle root + the previous-day pointer (chain link).
# NOTE: the transparency scheduler runs hourly. If `data` is empty on a
# fresh stack, seed today's root manually first:
#   curl -X POST -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
#     http://localhost:8000/transparency/compute | jq '.data.root_hash'
curl -s "http://localhost:8000/transparency/roots?limit=2" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data[] | {root_date, root_hash: (.root_hash[0:16]), prev_root_hash: (.prev_root_hash // null | if . then .[0:16] else null end), leaf_count}'

# Inclusion proof for the execution against today's root
curl -s "http://localhost:8000/transparency/inclusion/$EXEC_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | {root_date, pending, siblings: (.proof.siblings | length)}'
```

Expected: `algorithm: "ed25519"`, `sig_len: 86`, transparency root for today
with `leaf_count > 0`, inclusion proof with non-zero siblings (or
`pending: true` if the day's root has not yet been committed).

### 7b.i. Four new verification endpoints (2026-05-15)

These are the endpoints external auditors / SIEM tooling hit to verify
ACP's claims without installing the SDK. Trust still flows from the crypto;
this is a network courtesy.

```bash
# Active + historical root-signing public keys (forward-compatible with rotation)
curl -s "http://localhost:8000/transparency/keys" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{active: .data.active | {algorithm, fingerprint}, historical: .data.historical}'

# Consistency proof — fetch the chain of (root_hash, prev_root_hash)
# records between two dates and verify it is append-only.
curl -s "http://localhost:8000/transparency/consistency?from_date=2026-05-14&to_date=2026-05-15" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{count: .data.count, consistent: .data.consistent}'

# Server-side receipt verification — POST a signed receipt, get yes/no back.
SIGNED=$(curl -s "http://localhost:8000/receipts/$EXEC_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT")
echo "$SIGNED" | curl -s -X POST "http://localhost:8000/receipts/verify" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" -d @- \
  | jq '{valid, algorithm, expected_fingerprint}'

# Same shape for signed daily roots
SIGNED_ROOT=$(curl -s "http://localhost:8000/transparency/roots?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" | jq '.data[0].signed')
echo "$SIGNED_ROOT" | curl -s -X POST "http://localhost:8000/transparency/verify-root" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" -d @- \
  | jq '{valid, algorithm, expected_fingerprint}'
```

Expected: `valid: true` from both verify endpoints; consistency proof
`consistent: true`.

To verify the receipt **offline** (no network, no trust in the gateway),
the Python SDK ships pure-function verifiers — see §7c below.

---

## 7c. Offline verification CLI ⭐ (2026-05-15)

The `acp` CLI ships two new commands that exercise the full verification
math without ACP infrastructure in the loop. Use these in a cron / CI job:
non-zero exit status indicates tampering.

```bash
# Re-derive every event_hash from /audit/export and detect any tampering.
# Exits non-zero on violation — alertable from cron.
.venv/bin/python -m sdk.acp_client.cli verify-chain \
  --base-url http://localhost:8000 \
  --token "$TOKEN" \
  --tenant "$TENANT" \
  --limit 1000 \
  --json | jq '{valid, processed, shards: (.shards | length), total_violations}'

# Pull the consistency chain between two dates and validate it client-side.
.venv/bin/python -m sdk.acp_client.cli verify-root \
  --base-url http://localhost:8000 \
  --token "$TOKEN" \
  --tenant "$TENANT" \
  --from 2026-05-14 \
  --to 2026-05-15 \
  --json | jq '{from_date, to_date, count, consistent}'
```

Expected: `valid: true, total_violations: 0`; consistency chain
`consistent: true`. If the count is 0 you have not yet generated a
transparency root (call `POST /transparency/compute` once or wait for the
scheduler's first pass — it runs on boot then hourly).

---

## 8. Audit Trail + Chain Integrity (sharded, H-2)

```bash
# Recent events
curl -s "http://localhost:8000/audit/logs?limit=10" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data.items[] | {action, decision, risk_score: (.metadata_json.risk_score // 0), timestamp}'

# Cryptographic chain verify (returns BOTH valid and is_integrous for back-compat)
curl -s "http://localhost:8000/audit/logs/verify" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | {valid, is_integrous, processed_count, violations: (.violations | length)}'
```

Expected `valid: true, is_integrous: true, violations: 0` — sharded per-(tenant, chain_shard) chains all verify.

---

## 8b. SIEM export + status page + SSE + OpenAPI ⭐

Every endpoint here is referenced by one of `docs/integrations/siem.md`, `docs/security.md`, `docs/dpa.md`, `docs/status.md`, `docs/compliance/caiq_lite.md`, or the UI's real-time feed. Running this section proves each doc-promised surface is live.

```bash
# Public OpenAPI (caiq_lite.md — programmatic discovery)
curl -s "http://localhost:8000/openapi.json" \
  | jq '{title: .info.title, paths: (.paths | keys | length)}'
# Expected: paths ≥ 80

# Public operator status page (no auth — k8s/ALB/Datadog/statuspage.io ingest).
# 2026-05-15: enriched with `uptime_seconds`, `p95_latency_ms`, and an
# aggregate `services: {total, healthy, degraded, unreachable}` block.
curl -s "http://localhost:8000/status" \
  | jq '{status, uptime_seconds, p95_latency_ms, services}'

# Dashboard state snapshot (UI homepage tiles, /dashboard/state)
curl -s "http://localhost:8000/dashboard/state" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | keys'

# NDJSON SIEM export (docs/integrations/siem.md, docs/dpa.md, docs/security.md)
#   Streams audit rows as application/x-ndjson — pipe straight to Splunk HEC / Datadog Logs / S3
SINCE=$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)
curl -s "http://localhost:8000/audit/export?since=$SINCE&limit=50" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Accept: application/x-ndjson" \
  | head -3 \
  | jq -c '{id, timestamp, action, decision, event_hash: (.event_hash[0:12]), chain_shard}'
# Expected: 3 lines of NDJSON with hash-chain fields populated

# Real-time decision feed (Observability page uses this — SSE).
# 2026-05-15: the SSE handler now accepts auth in THREE forms — pick whichever
# fits your client:
#   1. acp_token cookie (the dashboard's default flow)
#   2. Authorization: Bearer <jwt>  (SDK / curl / Locust)
#   3. ?token=<jwt> query string    (cookieless browsers — EventSource
#                                    cannot set custom headers)
curl -sN --max-time 4 "http://localhost:8000/events/stream" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | head -5
# Equivalent for browser-style EventSource clients (no Authorization header):
curl -sN --max-time 4 "http://localhost:8000/events/stream?token=$TOKEN" | head -5
# Expected: an `event: connected` frame, then `event: heartbeat` every 15s
```

---

## 9. Transactional Outbox — durability backstop ⭐

The outbox guarantees `usage_records` will eventually contain a row for every billable audit, even if the sync billing path drops one (container OOM, network blip, retry exhaustion).

```bash
# Real-time outbox health
docker exec acp_postgres psql -U postgres -d acp_audit -tA -c \
  "SELECT status, COUNT(*) FROM pending_usage_events GROUP BY status ORDER BY status;"

# Outbox depth via the operational health endpoint
curl -s "http://localhost:8000/system/health" \
  | jq '.queues | {outbox_pending, outbox_failed, billing_dlq_length, billing_retry_queue}'

# Audit→billing reconciliation (THE durability proof)
docker exec acp_postgres psql -U postgres -d acp_audit -tA -c \
  "SELECT COUNT(*) FROM audit_logs WHERE action='execute_tool' AND decision <> 'reject';"
docker exec acp_postgres psql -U postgres -d acp_usage -tA -c \
  "SELECT COUNT(*) FROM usage_records;"
# Expected: usage_records ≥ billable_audit (outbox guarantees no loss)

# Exactly-once verification (must always be 0 rows)
docker exec acp_postgres psql -U postgres -d acp_usage -tA -c \
  "SELECT audit_id, COUNT(*) FROM usage_records GROUP BY audit_id HAVING COUNT(*) > 1;"
```

### Chaos demo — kill usage mid-traffic, watch outbox heal

```bash
# Terminal 1: continuous load
.venv/bin/locust -f tests/load/locustfile.py --host http://localhost:8000 \
  --users 100 --spawn-rate 10 --run-time 300s --headless

# Terminal 2 (after ~25s)
docker kill acp_usage             # Simulate OOM / deploy / node-eviction
# Audit pipeline keeps running; pending_usage_events backlog grows.
docker exec acp_postgres psql -U postgres -d acp_audit -tA -c \
  "SELECT status, COUNT(*) FROM pending_usage_events GROUP BY status;"

# After ~60s of outage
docker start acp_usage
# Outbox worker drains backlog within minutes. failed=0 stays 0
# (network errors are TRANSIENT, do not poison events).`

# Final reconciliation
docker exec acp_postgres psql -U postgres -d acp_audit -tA -c \
  "SELECT COUNT(*) FROM audit_logs WHERE action='execute_tool' AND decision<>'reject';"
docker exec acp_postgres psql -U postgres -d acp_usage -tA -c \
  "SELECT COUNT(*) FROM usage_records;"
# Expected: equal (proves exactly-once)
```

---

## 9b. Reconciliation — symmetric audit↔usage diff (Sprint 1.4) ⭐

Replaces the legacy `audit_count - usage_count <= 50` weak check with
a true symmetric set diff. Exits non-zero on any gap. Run after every
load test; schedule with `--watch 300` to drive the per-tenant SLI
gauges (`acp_reconcile_audit_without_usage{tenant}` etc.).

```bash
# One-shot — JSON to stdout, exit code 0 only when status=VERIFIED
python scripts/ops/reconcile.py --json | jq '.'

# Scheduled mode — every 5 minutes, POSTs to /internal/reconciliation-report
# so the gateway updates the SLI gauges (alerts via prometheus-rules.yml).
python scripts/ops/reconcile.py --watch 300 &

# Force a gap (test the alerter) — insert a usage_record with a random audit_id
docker exec acp_postgres psql -U postgres -d acp_usage -c \
  "INSERT INTO usage_records (id, tenant_id, agent_id, tool, audit_id, units, cost, timestamp)
   VALUES (gen_random_uuid(), '$TENANT', '$AGENT_ID', 'test',
           '11111111-2222-3333-4444-555555555555', 1, 0.0, NOW());"

python scripts/ops/reconcile.py --tenant "$TENANT" --json \
  | jq '{status, usage_without_audit_count, usage_without_audit_sample}'
# Expected: status="GAP_DETECTED", the forced audit_id appears in the sample.
```

Authoritative definition of "billable": `docs/reconciliation.md`.

---

## 9c. Operational ops scripts (Sprint 3.4) ⭐

Four scripts under `scripts/ops/`. All accept `--dry-run` (default for
the destructive ones); all exit non-zero on failure so CI can catch
regressions.

### Backup — `scripts/ops/backup.sh`

```bash
# Dry-run prints the plan with no pg_dump / no upload
bash scripts/ops/backup.sh --dry-run

# Real run: encrypts every DB with age, uploads to S3, then verifies
# by listing the archive in a throwaway Postgres container.
bash scripts/ops/backup.sh
# Expected exit 0; one `<db>_<UTC>.dump.age` per database lands in
# $ACP_BACKUP_S3_BUCKET. Encryption key was NEVER written to disk in
# plaintext (the pg_dump file is shred-deleted after age encryption).
```

### Restore drill — `scripts/ops/restore_drill.sh`

Boots an ISOLATED `acp_drill_<UTC>` docker-compose project (separate
network — the drill containers cannot resolve `acp_postgres`),
restores the latest backups, runs `/audit/logs/verify` + the
reconciliation script against the drilled stack, writes a verdict
to `reports/restore_drill/<UTC>.json`.

```bash
# Dry-run — exercises the wiring, no docker
bash scripts/ops/restore_drill.sh --dry-run

# Full drill — quarterly cadence (docs/runbooks/restore_drill.md)
bash scripts/ops/restore_drill.sh
cat reports/restore_drill/*.json | jq '{status, checks}'
# Expected: status="PASS", checks.reconcile.status="VERIFIED"
```

### Tenant export (GDPR right-to-portability) — `scripts/ops/export_tenant.py`

Produces a TAR archive containing every audit/usage/flight/graph/
autonomy/transparency row for one tenant with a manifest.json
(per-file sha256 + row count + source DB).

```bash
# Preview row counts without writing the archive
python scripts/ops/export_tenant.py --tenant "$TENANT" --dry-run

# Real export
python scripts/ops/export_tenant.py --tenant "$TENANT" \
  --output reports/exports/${TENANT}.tar.gz
tar -tzf reports/exports/${TENANT}.tar.gz | head
# Expected: manifest.json first, then audit/, usage/, flight/, etc.
```

### Tenant redaction (GDPR right-to-erasure, audit-safe) — `scripts/ops/redact_tenant_pii.py`

NEVER mutates existing audit_logs rows (would break the chain).
Computes sha256 of each PII field; INSERTs one chain marker row
with `action="pii_redaction"`; writes a sealed legal record under
`reports/redactions/{redaction_id}.json`.

```bash
# Preview
python scripts/ops/redact_tenant_pii.py \
  --tenant "$TENANT" --reason "GDPR-2026-0042"

# Execute (after legal sign-off)
python scripts/ops/redact_tenant_pii.py \
  --tenant "$TENANT" --reason "GDPR-2026-0042" --execute
ls reports/redactions/
```

### Transparency-log key rotation — `scripts/maintenance/rotate_transparency_key.py`

```bash
# Dry-run — shows old/new fingerprint without modifying anything
python scripts/maintenance/rotate_transparency_key.py --dry-run

# Execute — moves old key into transparency_historical_keys, writes
# new key to disk with a timestamped backup of the old one. Restart
# the audit service so the singleton picks up the new key.
python scripts/maintenance/rotate_transparency_key.py \
  --execute --reason "scheduled-quarterly-rotation"
docker compose -f infra/docker-compose.yml restart audit

# Old receipts STILL verify via /receipts/verify (historical-key fallback).
curl -s "http://localhost:8000/transparency/keys" \
  | jq '{active: .data.active.fingerprint, historical: [.data.historical[].fingerprint]}'
```

### Flight Recorder timeline backfill — `scripts/maintenance/backfill_flight_timelines.py`

```bash
# Find timelines stuck in_progress > 5 minutes (the leaked-timeline SLI)
python scripts/maintenance/backfill_flight_timelines.py --dry-run

# Recover them: status → "recovered_backfill", tool inferred from
# the first step, duration_ms computed from started_at → last step.
python scripts/maintenance/backfill_flight_timelines.py --execute
```

Runbooks for each: `docs/runbooks/key_rotation.md`,
`docs/runbooks/audit_chain_violation.md`,
`docs/runbooks/tenant_data_request.md`,
`docs/runbooks/restore_drill.md`.

---

## 10. Data Consistency Snapshot

```bash
docker exec acp_postgres psql -U postgres -d acp_audit -c \
  "SELECT COUNT(*) AS audit_count FROM audit_logs WHERE tenant_id = '$TENANT';"
docker exec acp_postgres psql -U postgres -d acp_usage -c \
  "SELECT COUNT(*) AS usage_count FROM usage_records WHERE tenant_id = '$TENANT';"
docker exec acp_postgres psql -U postgres -d acp_identity_graph -c \
  "SELECT COUNT(*) AS nodes FROM graph_nodes WHERE tenant_id = '$TENANT';
   SELECT COUNT(*) AS edges FROM graph_edges WHERE tenant_id = '$TENANT';"
docker exec acp_postgres psql -U postgres -d acp_flight_recorder -c \
  "SELECT COUNT(*) AS timelines FROM execution_timelines WHERE tenant_id = '$TENANT';"
docker exec acp_postgres psql -U postgres -d acp_autonomy -c \
  "SELECT COUNT(*) AS contracts FROM autonomy_contracts WHERE tenant_id = '$TENANT';
   SELECT COUNT(*) AS violations FROM autonomy_contract_violations WHERE tenant_id = '$TENANT';"
```

Expectations:
- `audit_count` ≥ `usage_count` (audit covers denies + auth failures too)
- `usage_count` matches successful executes 1:1 (idempotency-protected via outbox)
- `nodes` ≥ 2 (agent + tools), `edges` ≥ number of executes
- `timelines` ≥ number of executes
- `contracts` ≥ 1, `violations` ≥ 1 (from phase 5b)

---

## 11. Observability — Prometheus + Grafana ⭐

```bash
# Prometheus targets — should be 17/17 up
# (14 ACP services + prometheus-self + alertmanager + OPA)
curl -s http://localhost:9090/api/v1/targets \
  | jq '.data.activeTargets | {total: length, up: ([.[] | select(.health=="up")] | length)}'

# Open Prometheus UI
open http://localhost:9090            # or: xdg-open / start

# Open Grafana
open http://localhost:3000            # admin / admin (anonymous viewer enabled)
```

In Grafana navigate to **Dashboards → ACP**. Sprint 3.5 added four
new dashboards alongside the existing **ACP Operations**:

| Dashboard | Source file | What it shows |
|---|---|---|
| ACP Operations         | `infra/grafana-dashboards/acp-operations.json`     | Per-service throughput / p95 / outbox / billing / fail-CLOSED rate |
| **ACP Platform SLO**   | `infra/grafana-dashboards/acp-platform-slo.json`   | /execute p50/p95/p99, error rate, rate-limited breakdown, decision-pipeline p95 |
| **ACP Trust Layers**   | `infra/grafana-dashboards/acp-trust-layers.json`   | Chain integrity, reconcile gap, behavior consult mix, transparency seal lag, flight close lag |
| **ACP Tenant Activity** | `infra/grafana-dashboards/acp-tenant-activity.json` | Per-tenant request rate, rate-limited (by limit_type), daily inference $, cost blocks |
| **ACP Queues**         | `infra/grafana-dashboards/acp-queues.json`         | Every queue: depth AND oldest-age side-by-side (Sprint 3.5 SLI) |

Sprint 3.5 added oldest-age gauges to every queue so a stuck 2-row
queue (constant depth, growing age) finally pages instead of looking
healthy:

| Metric | Trigger |
|---|---|
| `acp_outbox_oldest_pending_age_seconds{outbox_name}` | OutboxOldestPendingAgeHigh (>60s for 5m → critical) |
| `acp_audit_dlq_oldest_age_seconds`                   | AuditDLQGrowing (>60s for 5m → critical) |
| `acp_billing_dlq_oldest_age_seconds`                 | BillingDLQGrowing (>60s for 5m → critical) |
| `acp_insight_queue_oldest_age_seconds`               | InsightQueueAgeHigh (>60s for 5m → warning) |
| `acp_flight_timeline_in_progress_count`              | FlightTimelineLeak (>10 for 5m → warning) |
| `acp_inference_cost_blocked_total`                   | InferenceCostCapBlocking (rate>0 for 5m → warning) |
| `acp_audit_chain_violations_total`                   | **ChainViolationImmediate (for: 0m → page IMMEDIATELY)** |

AlertManager rules in `infra/prometheus-rules.yml` cover the full set:

| Alert | Severity | Trigger |
|---|---|---|
| ServiceUnavailable | critical | scrape down >2m |
| OutboxPoisonGrowing | critical | `acp_outbox_poison_total` rises in 5m |
| OutboxOldestPendingAgeHigh | critical | oldest pending outbox row >60s for 5m |
| AuditDLQGrowing | critical | audit DLQ head >60s for 5m |
| BillingDLQGrowing | critical | billing DLQ head >60s for 5m |
| FlightTimelineLeak | warning | in_progress count >10 for 5m |
| InferenceCostCapBlocking | warning | rate(blocked)>0 for 5m |
| **ChainViolationImmediate** | **page** | **violations>0, for: 0m (no window)** |
| ReconciliationGapSustained | critical | reconcile gap unresolved 15m |
| BehaviorFailClosedSustained | warning | fail-CLOSED rate >5% for 3m |
| P95LatencyBudgetBreach | warning | p95 >400ms for 5m |
| AuthFailureSpike | warning | audit duplicates spiking |

---

## 12. Load test (optional — 100 users / 300s)

```bash
.venv/bin/locust -f tests/load/locustfile.py --host http://localhost:8000 \
  --users 100 --spawn-rate 10 --run-time 120s --headless
```

| Target | Pass |
| --- | --- |
| Correctness | > 95 % |
| P50 latency (probe) | < 120 ms |
| P95 latency (probe) | < 400 ms |
| Audit-usage match | 100 % |
| Outbox `failed` count | 0 |
| Billing DLQ | < 10 |

Run `python3 scripts/reconcile_billing_gap.py --dry-run` if you ever see a non-zero gap; `--execute` to heal.

---

## 12a. Soak + fairness harness (Sprint 3.1) ⭐

The legacy locustfile is a 2-minute integration smoke. Sprint 3.1
shipped two CI-runnable harnesses for real soak + tenant isolation
testing. Both provision tenants, run locust headless, run the four
post-run checks (chain verify / reconcile / flight close / transparency
roots), write structured reports, then tear down. Exit non-zero on
any failure.

```bash
# ── CI smoke (≈6 min): 50u/5m/2 tenants, all four post-run checks fire
INTERNAL_SECRET="$INTERNAL_SECRET" python tests/load/soak.py \
  --users 50 --duration 5m --tenants 2 \
  --max-p99-ms 750

# ── Nightly soak (60 min): 1000u/60m/5 tenants, mixed traffic
#    (60% valid / 15% injection / 10% oversized / 10% bad_token / 5% no_auth)
INTERNAL_SECRET="$INTERNAL_SECRET" python tests/load/soak.py
# Reports land in reports/soak/<UTC>/: locust_stats.csv, checks.json, summary.json.

# ── Tenant fairness: 1 noisy tenant @ 500u + 4 quiet @ 50u each.
#    Fails if quiet-tenant p99 degrades >20% from baseline.
INTERNAL_SECRET="$INTERNAL_SECRET" python tests/load/fairness.py \
  --duration 5m --max-degradation-pct 20
# Report: reports/soak/<UTC>-fairness/fairness_report.json
```

Tear-down policy: audit + usage are append-only (chain integrity).
The harness sets `rpm_limit=0` on test tenants post-run instead of
DELETE so forgotten test tokens can't continue generating load. See
`docs/soak_runbook.md` for cadence + CI wiring.

---

## 12b. Python SDK quickstart — `examples/agent.py` ⭐

The fastest way to show a client how their code integrates: open a second terminal and run the example. It uses the production SDK (`sdk/acp_client`), hits the live gateway you've been demoing against, and demonstrates a denied call without you faking anything.

```bash
# 2026-05-15 (Gap 4 fix): the SDK now accepts EITHER an acp_* API key OR a
# JWT bearer as ACP_API_KEY / ACP_TOKEN (it sends `Authorization: Bearer
# <value>` either way). The SDK also auto-loads `.env` from CWD / parents
# so quickstart works without manual exports — opt out via ACP_NO_DOTENV=1.
export ACP_API_KEY="$TOKEN"
export ACP_BASE_URL="http://localhost:8000"

# Run the 60-line example — protect, allow, deny, optionally verify receipt
.venv/bin/python examples/agent.py
```

What the client sees in the output:

```
allow -> [{'row': '1', 'value': 'SELECT * FROM customers LIMIT 1'}]
deny  -> DeniedError(reason='policy_denied', ...)
receipt verifies -> skipped (set ACP_LAST_EXECUTION_ID to a real id)
```

To exercise the offline receipt verification too, point the example at a
real execution id from §4 or §7b:

```bash
export ACP_LAST_EXECUTION_ID="$EXEC_ID"
.venv/bin/python examples/agent.py
# Expected: `receipt verifies -> True`
```

Then walk to the UI's Flight Recorder — the same two calls appear with full
timelines and the same execution IDs the SDK printed. That's the integration
story in 60 seconds.

---

## 13. Web UI

```
http://localhost:5173
```
Login: `admin@acp.local` / `password`

**2026-05-15 — keyboard-first navigation (Linear-style)**: once logged in,
press `?` anywhere to see the cheatsheet. Linear-style `g <letter>` sequences:

| Shortcut | Destination |
|---|---|
| `?`      | Cheatsheet modal |
| `⌘K` / `Ctrl+K` | Command palette |
| `G F`    | Flight Recorder |
| `G P`    | Policies |
| `G A`    | Audit Trail |
| `G I`    | Incidents |
| `G G`    | Identity Graph |
| `G O`    | Observability |
| `G H`    | System Health |
| `G S`    | Settings |
| `G D`    | Developer Panel |

Modals are now portal-rendered with focus trap, body-scroll lock, and a
fixed z-index hierarchy (toast 80 > modal 60 > sidebar 30 > navbar 40) —
no more "modal appears under the navbar". New reusable primitives baked
into the UI: `PageShell`, `SectionHeader`, `EmptyState`, `ConfirmDialog`,
`ActivityFeed` (Datadog-style live rail), `InvestigationLayout`
(filters | list | detail), `DiffViewer` (unified + split LCS), `LiveKpiTile`
(sparkline + pulse-on-update).

| Page | What to show the client |
| --- | --- |
| **Overview / Dashboard** | Aggregated KPI tiles, 30s auto-refresh, real Groq insight cards |
| **Observability** | Live decision feed via SSE — every `/execute` flashes in real time |
| **System Health** ⭐ | 4-state classification banner, 12 service tiles, queue depths including outbox |
| **Identity Graph** | Live SVG graph; click any node → blast radius; compromise sim modal |
| **Flight Recorder** | Timeline list; click any → step-by-step scrubber with play/pause |
| **Autonomy** | Contracts CRUD + recent violations + 7-day human-override timeline |
| **Audit Logs** ⭐ | Searchable, per-shard chain verify (shows "Chain Valid · N entries"); now correctly reports valid chains as valid |
| **Risk Engine** ⭐ | Behavioral Flow chart (7-day zero-filled), High-Risk Agents, AI Threat Insights (Groq) — always renders, populated by real LLM calls |
| **Billing** ⭐ | Per-month Invoice Ledger with real `total_calls / threats_blocked / cost_usd`, ROI tiles, anomaly detector, error banners with Retry |
| **Flight Recorder · Receipt panel** ⭐ | Click any timeline → "Verify Receipt" → ed25519 signature + transparency inclusion proof rendered inline (was previously unreachable; nginx proxy now passes `/receipts/*` + `/transparency/*` through) |
| **Incidents / Security Ops / Forensics / Policy / RBAC / Attack Sim / Auto Response / Playground / Developer** | Full operational suite |

---

## 14. Pre-flight checklist (run before the client walks in)

```bash
# Re-login if the token expired
source /tmp/acp_session 2>/dev/null || {
  export TENANT="00000000-0000-0000-0000-000000000001"
  export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
    -H "Content-Type: application/json" -H "X-Tenant-ID: $TENANT" \
    -d '{"email":"admin@acp.local","password":"password"}' \
    | jq -r '.data.access_token')
}

echo "── Containers ──"
docker ps --format "{{.Names}}\t{{.Status}}" | grep -c "(healthy)"
echo "Expected: ≥ 22 healthy (a few sidecars run without explicit healthcheck)"

echo "── /system/health 4-state ──"
curl -s "http://localhost:8000/system/health" \
  | jq '"\(.status) — \(.healthy)/\(.total) services · p95 \(.summary.p95_latency_ms)ms"'

echo "── Outbox + DLQs (must all be 0 except outbox_pending which transient) ──"
curl -s "http://localhost:8000/system/health" \
  | jq '.queues'

echo "── Audit chain integrity ──"
curl -s "http://localhost:8000/audit/logs/verify" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | {valid, processed_count, violations: (.violations | length)}'

echo "── Crypto sanity (2026-05-15): receipt + root chain ──"
EXEC_ID=$(curl -s "http://localhost:8000/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq -r '.data.items[0].id')
SIGNED=$(curl -s "http://localhost:8000/receipts/$EXEC_ID" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT")
echo "$SIGNED" | curl -s -X POST "http://localhost:8000/receipts/verify" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" -d @- | jq '{receipt_valid: .valid}'
.venv/bin/python -m sdk.acp_client.cli verify-chain \
  --base-url http://localhost:8000 --token "$TOKEN" --tenant "$TENANT" \
  --limit 500 --json 2>/dev/null | jq '{chain_valid: .valid, processed}'

echo "── Public /status (no auth — k8s/Datadog ingest) ──"
curl -s "http://localhost:8000/status" \
  | jq '{status, uptime_seconds, p95_latency_ms, services}'

echo "── Groq enrichment ALIVE ──"
docker logs acp_groq_worker --tail 4 2>&1 | grep -E "started|insight_stored" | tail -3

echo "── Prometheus scraping ──"
curl -s http://localhost:9090/api/v1/targets \
  | jq '.data.activeTargets | map(.health) | group_by(.) | map({state: .[0], n: length})'
echo "Expected: 17 up (14 ACP services + prometheus-self + alertmanager + OPA)"

echo "── Reconciliation (Sprint 1.4): must be VERIFIED ──"
python scripts/ops/reconcile.py --json 2>/dev/null \
  | jq '{status, audit_without_usage_count, usage_without_audit_count}'

echo "── Per-tenant quota visible (Sprint 3.2) ──"
curl -s "http://localhost:8000/tenant/quota" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '{rps: .limits.requests_per_second, daily: .limits.daily_request_cap,
         monthly: .limits.monthly_request_cap,
         cost_cap: .limits.daily_inference_cost_cap_usd,
         daily_used: .usage.daily_used}'

echo "── Queue-age SLIs (Sprint 3.5) ──"
curl -s http://localhost:8000/metrics 2>/dev/null \
  | grep -E "^acp_(outbox_oldest_pending|audit_dlq_oldest|billing_dlq_oldest|flight_timeline_in_progress)" \
  | head -8

echo "── Backup keys provisioned ──"
test -n "$ACP_BACKUP_AGE_RECIPIENT" && test -f "$ACP_BACKUP_AGE_IDENTITY" \
  && echo "✓ age keys present" || echo "✗ set ACP_BACKUP_AGE_RECIPIENT + ACP_BACKUP_AGE_IDENTITY"

echo "── Graph + flight + autonomy alive ──"
curl -s "http://localhost:8000/graph/agents?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data.nodes | length'
curl -s "http://localhost:8000/flight/timelines?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | length'
curl -s "http://localhost:8000/autonomy/contracts" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | jq '.data | length'
```

---

## 15. Talking points for the client

1. **"Why ACP, not another observability tool?"** Observability shows what AI agents *did*. ACP **prevents** what they shouldn't do, **proves** what they did, and **bounds** what they're allowed to do — at sub-50ms p95 latency.

2. **"Show me a runtime compromise."** Open `/identity-graph`, click any agent node, hit **Run** → instant blast-radius modal with classification.

3. **"What if an agent goes rogue mid-execution?"** Open `/autonomy`, install a `denied_actions: [<bad tool>]` contract. Next `/execute` returns 403 with the violated rule logged in violations + identity_graph.

4. **"Can we replay any past execution?"** `/flight-recorder` → click a timeline → step-by-step scrubber with the policy decisions and risk scores at each frame.

5. **"What happens when the system itself degrades?"** Stop the Usage service (`docker kill acp_usage`) mid-traffic. The audit pipeline keeps running, the **Transactional Outbox** captures every billable event, and when Usage recovers the outbox worker drains the backlog with **exactly-once semantics** — zero data loss. Demonstrate by counting `audit_logs` vs `usage_records` before/after.

6. **"Is it SOC2-ready?"** Hash-chain audit (sharded), every contract change in `human_override_events`, every compromise sim in `compromise_simulations`. Audit integrity verifier returns `valid: true, violations: 0`. All exportable per tenant.

7. **"Where's my SOC view?"** Grafana at `:3000` → ACP Operations dashboard. AlertManager rules at `infra/prometheus-rules.yml` map to PagerDuty/Slack in production.

8. **"What's your disaster recovery story?"** `docs/dr_runbook.md` — RPO 15min / RTO 30min on RDS PITR. The PostgreSQL Outbox is the durability source of truth — even Redis loss does not lose billable events.

---

## 16. Reset between demos

```bash
# Cleanest reset: full down + up (preserves built images)
cd infra && docker compose down && docker compose up -d && sleep 90

# Or just clear demo data without restarting containers
docker exec acp_postgres psql -U postgres -c "
  TRUNCATE audit_logs, pending_usage_events CASCADE;
  TRUNCATE kill_switches;
" -d acp_audit
docker exec acp_postgres psql -U postgres -c "TRUNCATE usage_records;" -d acp_usage
docker exec acp_redis redis-cli FLUSHDB
# NOTE: FLUSHDB no longer clears kill switches — they are persisted to the
# kill_switches table in acp_audit (C8 fix). The TRUNCATE kill_switches above
# handles that. Redis is rehydrated from DB on next gateway startup.
```

---

## 17. Where things live

| Concern | Path |
|---|---|
| Compose stack | `infra/docker-compose.yml` |
| Prometheus config | `infra/prometheus.yml` |
| Alert rules | `infra/prometheus-rules.yml` |
| Grafana dashboards (5×) | `infra/grafana-dashboards/acp-{operations,platform-slo,trust-layers,tenant-activity,queues}.json` |
| DR / backup runbook | `docs/runbooks/restore_drill.md`, `docs/dr_runbook.md` |
| Audit-chain-violation runbook | `docs/runbooks/audit_chain_violation.md` |
| Key-rotation runbook | `docs/runbooks/key_rotation.md` + `key_rotation_drill_log.md` |
| GDPR / CCPA runbook | `docs/runbooks/tenant_data_request.md` |
| Soak harness runbook | `docs/soak_runbook.md` |
| Reconciliation script | `scripts/ops/reconcile.py` (Sprint 1.4 — supersedes `reconcile_billing_gap.py`) |
| Backup / restore drill | `scripts/ops/{backup.sh,restore_drill.sh}` |
| Tenant export / redaction | `scripts/ops/{export_tenant.py,redact_tenant_pii.py}` |
| Key rotation script | `scripts/maintenance/rotate_transparency_key.py` |
| Flight backfill | `scripts/maintenance/backfill_flight_timelines.py` |
| Soak / fairness harnesses | `tests/load/{soak.py,fairness.py,soak_user.py,post_run_checks.py}` |
| Findings vocabulary | `services/decision/findings.py` + `docs/risk_reasons.md` |
| Reconciliation spec | `docs/reconciliation.md` |
| Observability scopes | `docs/observability_endpoints.md` |
| Auto-memory (cross-session context) | `~/.claude/projects/.../memory/` |

---

## 18. Doc ↔ code parity matrix (verified 2026-05-15)

Every other `docs/*.md` file makes promises about endpoints, files, or behavior. This matrix records which doc → code link is verified, and the few items where the docs are drifted and should not be trusted verbatim.

### Verified — each doc's headline promise can be exercised by this runbook

| Doc | Headline promise | Section that exercises it |
|---|---|---|
| `docs/quickstart.md` | Five-line `@acp.protect` integration; allow + deny + offline receipt | §12b (runs `examples/agent.py`) |
| `docs/security.md` | Hash-chain audit, signed receipts, tenant isolation, fail-closed | §5c, §7b, §8 |
| `docs/integrations/siem.md` | NDJSON streaming export, chain fields per record | §8b (`/audit/export`) |
| `docs/dpa.md` | Audit export for DSR / 90-day retention | §8b (`/audit/export`) |
| `docs/sla.md` | p50/p95 budgets for execute, audit, auth | §11 (Grafana p95 panel), §12 (locust) |
| `docs/status.md` | `/status` 4-state classification + incidents stream | §8b (`/status`), §2 (`/system/health`) |
| `docs/dr_runbook.md` | Outbox durability, audit verify, replay scripts | §9 (chaos), §8 (verify), `scripts/replay_audit_dlq.py` |
| `docs/compliance/caiq_lite.md` | OpenAPI discovery, audit retention | §8b (`/openapi.json`), §8 |
| `docs/compliance/subprocessors.md` | Groq sub-processor disabled by default | §5a (Groq insights wired) |
| `docs/architecture_diagram.md` | Service map | §0 service map table |

### Known doc drift — don't follow these verbatim

| Doc | Claim | Reality | Workaround |
|---|---|---|---|
| `quickstart.md`, `sla.md`, `security.md`, `status.md`, `siem.md`, `dpa.md` | API paths prefixed `/v1/...` (`/v1/audit/export`, `/v1/health`, `/v1/status`, `/v1/policy/simulate`) | Real gateway is unversioned: `/audit/export`, `/health`, `/status`, `/policy/simulate` | Use the unprefixed paths in this runbook; treat `/v1/` in those docs as forward-looking versioning |
| `security.md` | `scripts/utils/tenant_delete.py` for right-to-erasure | File does not exist (only `seed_admin.py`, `test_jwt.py`) | Erasure via direct DB delete on `acp_*` schemas per tenant_id; tooling is open work |
| `dr_runbook.md` | `scripts/backup/pg_dump_all.sh`, `scripts/backup/verify_latest.sh` | Directory does not exist | Use `docker exec acp_postgres pg_dumpall -U postgres > backup.sql` for now |
| `dr_runbook.md` | Table names `pending_usage_event` (singular) and `usage_events` (plural) | Actual: `pending_usage_events` (plural) and `usage_records` | Use the schema names this runbook uses (§9, §10) |
| `dr_runbook.md` | `docker compose up -d --scale outbox_worker=3` | No standalone `outbox_worker` service — the worker runs inside `acp_audit` | Scale via `--scale audit=N` or run the worker as a separate compose service first |
| `dr_runbook.md` | `docs/templates/incident_email.md`, `docs/templates/postmortem.md` | Templates directory does not exist | Use any standard postmortem template; this is a docs gap, not a code gap |
| `security.md` | `docs/security.pgp.txt` PGP-signed policy digest | File does not exist | Open work for the compliance package |
| `quickstart.md` | `docs/policy_schema.md` | File does not exist | `examples/policy.yaml` is the de-facto schema reference |

These items are doc-side drift only — none affect the runtime behavior demoed in §1–§17.

---

**Status:** 🟢 **PRODUCTION-PILOT READY · NEXT-GEN RUNTIME TRUST PLATFORM**
**Last updated:** 2026-05-16 (Run-9: dual-audit remediation sprint.
All 20 Round 1 + 6 Round 2 audit findings resolved. Changes reflected in
this runbook:

* **C8 — Kill switch DB persistence**: Kill switches now survive Redis
  FLUSHDB. Engage/disengage writes to `kill_switches` table in `acp_audit`;
  gateway re-hydrates Redis on startup. New §5d demonstrates the persistence
  guarantee. Reset section updated: `TRUNCATE kill_switches` required for
  a full clean reset. Migration included in `alembic upgrade head` on audit.
* **C9 — findings field**: `findings` is now correctly populated on every
  `/execute` response (was null). §4b updated with verification command.
* **C10 — Auth backpressure**: `/auth/token` + `/auth/login` guarded by
  40-slot semaphore; prevents PgBouncer pool exhaustion at 500 concurrent logins.
* **C12 — reconcile.py**: Now accepts `ACP_AUDIT_DB` / `DATABASE_URL` env
  vars and defaults to `localhost:5433` (Docker host port). §9b commands work
  without manual overrides.
* **C13 + learning alembic**: `behavior_profiles` table migration now runs
  on first boot via `acp_behavior` container. §0e updated with the command.
* **Service count corrected**: 25 containers (not 26), 14 HTTP services
  (intelligence/learning are embedded modules, not deployed). Service map,
  container counts, and Prometheus target counts updated throughout.
* **Prometheus targets**: 17 total (14 ACP + prometheus-self + alertmanager +
  OPA). §11 and §14 updated.
* **Forensics cross-service import**: Removed `decision_engine` in-process
  import from forensics router. Replay endpoint now returns stored audit
  metadata instead of re-evaluating against the live decision model.
* **Autonomy create_task**: All 3 bare `asyncio.create_task` calls in
  autonomy router wrapped in `_safe_bg()` for consistent exception handling.

Verified clean: 25/25 containers healthy, 17/17 prometheus targets up,
all Sprint 3.5 oldest-age gauges surfacing on /metrics, /audit/logs/verify
is_integrous=true, /transparency/verify-root returns valid:true with
errors:[], soak harness completes a smoke run with chain_verify +
flight_timelines_closed + transparency_roots all OK.
99/99 unit tests passing. UI build clean (2276 modules).)
