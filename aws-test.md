# ACP Production Testing Guide
## Live Platform: https://aegisagent.in

End-to-end production verification, load testing, MTTR measurement,
and resume-ready metrics for the ACP AI Governance Platform.

---

## Quick Reference

```
Platform URL:     https://aegisagent.in
Admin login:      admin@acp.local / password
Demo login:       demo@aegisagent.in / demo
EC2 IP:           43.205.42.5
EC2 key:          ~/Downloads/acp-prod-key.pem
Grafana (tunnel): http://localhost:3000  (admin / ACP_Grafana_2026!)
```

---

## Phase 1 — First Boot: Seed Admin + Demo Data

Run once after the first deploy. Skip sections you've already done.

```bash
# SSH into EC2
ssh -i ~/Downloads/acp-prod-key.pem ubuntu@43.205.42.5
```

### 1.1 — Create Admin User

```bash
# Run from EC2. Uses pgbouncer inside the container — no SSL issue.
docker exec \
  -e DATABASE_URL="postgresql+asyncpg://identity_user:identity_prod_pwd@pgbouncer:6432/acp_identity" \
  acp_gateway python seed_admin.py
```

Expected output:
```
✅ Admin user created successfully
   Credentials: admin@acp.local / password
```
(If it says "already exists" — skip, already done.)

### 1.2 — Seed Demo Data (fills every UI chart with 30 days of history)

Run this on the **EC2 host** (not inside Docker).

```bash
cd ~/aegis

# Create Python venv if it doesn't exist yet
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies (safe to re-run)
pip install -e ".[server,dev]" -q

# Run seed script via pgbouncer
DATABASE_URL="postgresql+asyncpg://postgre:Acp2026Prod%23Rds%24Secure@localhost:6432/acp_audit" \
ACP_BASE_URL="http://localhost:8000" \
ACP_ADMIN_EMAIL="admin@acp.local" \
ACP_ADMIN_PASSWORD="password" \
python scripts/seed_demo_data.py
```

Expected output:
```
[1/4] Demo user (demo@aegisagent.in)...
  OK   — Created demo@aegisagent.in with VIEWER role
[2/4] Audit logs (acp_audit database)...
  OK   — 2000 audit log rows seeded (30 days)
[3/4] Incidents (acp_api database)...
  OK   — 35 incidents seeded
[4/4] Usage records (acp_usage database)...
  OK   — 2000 usage records seeded
```

Safe to re-run — existing data is detected and skipped.

### 1.3 — Verify Seeding Worked

```bash
# Run from EC2. Check admin login returns a token.
curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Token OK:', d['data']['access_token'][:30]+'...')"
```

---

## Phase 2 — System Health Verification

Run these from your **Mac** (no SSH needed).

```bash
export BASE=https://aegisagent.in
export TENANT="00000000-0000-0000-0000-000000000001"
```

### 2.1 — Gateway Health
```bash
curl -s $BASE/health
```
Expected:
```json
{"status":"healthy","service":"gateway","version":"1.0.0"}
```

### 2.2 — All Services Healthy
```bash
curl -s $BASE/system/health | python3 -c "
import sys, json
d = json.load(sys.stdin)
services = d.get('services', d.get('data', {}) and d.get('data', {}).get('services', {}) or {})
healthy = sum(1 for v in services.values() if v.get('status') == 'healthy')
total = len(services)
print(f'{healthy}/{total} services healthy')
for name, v in sorted(services.items()):
    status = v.get('status', 'unknown')
    mark = 'OK' if status == 'healthy' else 'FAIL'
    print(f'  [{mark}] {name}: {status}')
"
```
**Resume metric:** "12/12 microservices healthy on production boot"

### 2.3 — Get Auth Token
```bash
TOKEN=$(curl -s -X POST $BASE/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo "Token: ${TOKEN:0:40}..."
```

### 2.4 — Agent List
```bash
curl -s $BASE/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
agents = d.get('data', {}).get('items', [])
print(f'Agents registered: {len(agents)}')
for a in agents[:5]:
    print(f'  - {a[\"name\"]} ({a[\"agent_id\"][:8]}...)')
"
```

### 2.5 — Execute a Governance Decision (Core Test)
```bash
# Get first agent_id
AGENT_ID=$(curl -s $BASE/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['items'][0]['agent_id'])")

echo "Agent: $AGENT_ID"

# Fire a governance decision
curl -s -X POST $BASE/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{
    \"agent_id\": \"$AGENT_ID\",
    \"tool\": \"read_file\",
    \"input\": {\"path\": \"/data/report.csv\"},
    \"context\": {\"session_id\": \"test-$(date +%s)\"}
  }" | python3 -m json.tool
```
Expected: `"decision": "allow"` or `"decision": "block"` with a signed receipt.

**Resume metric:** "Platform governs 100% of AI agent tool calls with <300ms p50 latency"

### 2.6 — Audit Chain Integrity
```bash
curl -s $BASE/audit/verify-chain \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; d=json.load(sys.stdin).get('data',{}); print(f'Chain valid: {d.get(\"chain_valid\")}, Violations: {d.get(\"violations\",0)}')"
```
Expected: `Chain valid: True, Violations: 0`

**Resume metric:** "Cryptographic audit chain — 0 integrity violations across all decisions"

### 2.7 — Signed Receipt (Cryptographic Proof)
```bash
# Get most recent audit log ID
LOG_ID=$(curl -s "$BASE/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['items'][0]['id'])")

# Fetch the signed receipt
curl -s $BASE/receipts/$LOG_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -m json.tool
```
Expected: receipt with `"signature"`, `"merkle_root"`, `"verified": true`

### 2.8 — SSL Certificate
```bash
curl -vI https://aegisagent.in 2>&1 | grep -E "issuer|expire|SSL|subject"
```

---

## Phase 3 — Latency Benchmarking

### 3.1 — Gateway p50/p95 via /status
```bash
curl -s $BASE/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
lat = d.get('data', {}).get('latency', d.get('latency', {}))
print(f'p50:      {lat.get(\"p50_ms\", \"N/A\")} ms')
print(f'p95:      {lat.get(\"p95_ms\", \"N/A\")} ms')
print(f'p99:      {lat.get(\"p99_ms\", \"N/A\")} ms')
print(f'Requests: {lat.get(\"request_count\", 0)}')
"
```
**Target:** p50 < 300ms, p95 < 1500ms

### 3.2 — Execute Endpoint Latency (10 decisions, timed)
```bash
start=$(date +%s%N)
for i in $(seq 1 10); do
  curl -s -X POST $BASE/execute \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Tenant-ID: $TENANT" \
    -H "Content-Type: application/json" \
    -d "{\"agent_id\":\"$AGENT_ID\",\"tool\":\"read_file\",\"input\":{\"path\":\"/test-$i\"},\"context\":{\"session_id\":\"bench-$i\"}}" \
    -o /dev/null
done
end=$(date +%s%N)
total_ms=$(( (end - start) / 1000000 ))
avg_ms=$(( total_ms / 10 ))
echo "10 decisions in ${total_ms}ms — avg ${avg_ms}ms each"
```

---

## Phase 4 — Load Test (MTTR + Throughput)

Install locust on your Mac:
```bash
pip install locust
```

### 4.1 — Create Load Test Script

Save as `/tmp/locustfile.py`:
```python
from locust import HttpUser, task, between
import random, uuid

TENANT = "00000000-0000-0000-0000-000000000001"

class ACPUser(HttpUser):
    wait_time = between(0.5, 2)
    host = "https://aegisagent.in"
    token = None
    agent_id = None

    def on_start(self):
        r = self.client.post("/auth/token",
            json={"email": "admin@acp.local", "password": "password"},
            headers={"X-Tenant-ID": TENANT})
        self.token = r.json()["data"]["access_token"]
        agents = self.client.get("/agents",
            headers={"Authorization": f"Bearer {self.token}", "X-Tenant-ID": TENANT})
        items = agents.json().get("data", {}).get("items", [])
        self.agent_id = items[0]["agent_id"] if items else None

    @task(5)
    def execute_decision(self):
        if not self.agent_id:
            return
        self.client.post("/execute",
            json={
                "agent_id": self.agent_id,
                "tool": random.choice(["read_file", "list_dir", "query_db", "send_email"]),
                "input": {"path": f"/data/{uuid.uuid4().hex[:8]}"},
                "context": {"session_id": str(uuid.uuid4())}
            },
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Tenant-ID": TENANT,
                "Content-Type": "application/json"
            })

    @task(2)
    def check_health(self):
        self.client.get("/health")

    @task(1)
    def list_agents(self):
        self.client.get("/agents",
            headers={"Authorization": f"Bearer {self.token}", "X-Tenant-ID": TENANT})

    @task(1)
    def audit_logs(self):
        self.client.get("/audit/logs?limit=10",
            headers={"Authorization": f"Bearer {self.token}", "X-Tenant-ID": TENANT})
```

### 4.2 — Run Load Test — 50 Users (2 minutes)
```bash
locust -f /tmp/locustfile.py \
  --headless \
  --users 50 \
  --spawn-rate 5 \
  --run-time 120s \
  --host https://aegisagent.in \
  --csv /tmp/acp_load_50
```

### 4.3 — Run Load Test — 100 Users (3 minutes)
```bash
locust -f /tmp/locustfile.py \
  --headless \
  --users 100 \
  --spawn-rate 10 \
  --run-time 180s \
  --host https://aegisagent.in \
  --csv /tmp/acp_load_100
```

### 4.4 — View Results
```bash
python3 -c "
import csv
with open('/tmp/acp_load_100_stats.csv') as f:
    for r in csv.DictReader(f):
        if r['Name'] != 'Aggregated':
            print(f\"{r['Name']:35s}  p50={r['50%']}ms  p95={r['95%']}ms  rps={float(r['Requests/s']):.1f}  fail={r['Failure Count']}\")
"
```

**Resume metrics to capture and fill in:**
- Total requests processed
- RPS at 100 concurrent users
- p50/p95 under load
- Failure rate (target: <1%)

---

## Phase 5 — MTTR Testing (Mean Time To Recovery)

Proves the platform self-heals. SSH into EC2 first.

```bash
ssh -i ~/Downloads/acp-prod-key.pem ubuntu@43.205.42.5
```

### 5.1 — Kill a Service + Time Recovery

Open **two terminals** on EC2.

**Terminal 1 — Watch container states:**
```bash
watch -n 1 'docker ps --format "{{.Names}}\t{{.Status}}" | sort'
```

**Terminal 2 — Kill gateway and time recovery:**
```bash
time docker kill acp_gateway && \
  until docker inspect acp_gateway --format '{{.State.Health.Status}}' 2>/dev/null | grep -q healthy; do
    sleep 1
  done && echo "GATEWAY RECOVERED"
```
Expected: **< 30 seconds**

### 5.2 — Kill Audit Service
```bash
time docker kill acp_audit && \
  until docker inspect acp_audit --format '{{.State.Health.Status}}' 2>/dev/null | grep -q healthy; do
    sleep 1
  done && echo "AUDIT RECOVERED"
```

### 5.3 — Kill PgBouncer (Connection Pool)
```bash
time docker kill acp_pgbouncer && \
  until docker inspect acp_pgbouncer --format '{{.State.Health.Status}}' 2>/dev/null | grep -q healthy; do
    sleep 1
  done && echo "PGBOUNCER RECOVERED"
```

### 5.4 — Chaos: Kill 5 Services Simultaneously
```bash
docker kill acp_audit acp_identity acp_registry acp_decision acp_usage

start=$(date +%s)
until [ "$(docker ps --format '{{.Status}}' | grep -c '(healthy)')" -ge 18 ]; do
  sleep 2
done
end=$(date +%s)
echo "Full recovery: $((end - start)) seconds"
```

**Resume metric:** "Platform MTTR < 30s — self-heals via Docker restart policies, zero manual intervention"

---

## Phase 6 — Observability Verification

### 6.1 — Grafana (SSH Tunnel from Mac)
```bash
# On your Mac
ssh -i ~/Downloads/acp-prod-key.pem -L 3000:localhost:3000 ubuntu@43.205.42.5 -N &
open http://localhost:3000
# Login: admin / ACP_Grafana_2026!
```

Dashboards to screenshot:
- **Platform SLO** — p50/p95/p99 latency, error rate
- **Trust Layers** — decisions allowed / blocked / escalated
- **Tenant Activity** — per-tenant request volume
- **Queues** — outbox depth, DLQ count

### 6.2 — Prometheus (SSH Tunnel)
```bash
ssh -i ~/Downloads/acp-prod-key.pem -L 9090:localhost:9090 ubuntu@43.205.42.5 -N &
open http://localhost:9090
```

Key queries in Prometheus:
```
# Total governance decisions
acp_decision_total

# Block rate
rate(acp_decision_total{outcome="block"}[5m])

# Gateway latency p99
histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))

# All scraped targets up
up
```

### 6.3 — Jaeger Traces (SSH Tunnel)
```bash
ssh -i ~/Downloads/acp-prod-key.pem -L 16686:localhost:16686 ubuntu@43.205.42.5 -N &
open http://localhost:16686
# Select service: gateway → Find Traces
```

---

## Phase 7 — Security Verification

Set `BASE`, `TENANT`, `TOKEN`, `AGENT_ID` first (see Phase 2).

### 7.1 — SQL Injection Must Be Blocked
```bash
curl -s -X POST $BASE/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"tool\":\"query_db\",\"input\":{\"query\":\"'; DROP TABLE users; --\"},\"context\":{}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Decision:', d.get('decision','?'), '| Findings:', d.get('findings',[]))"
```

### 7.2 — Path Traversal Must Be Blocked
```bash
curl -s -X POST $BASE/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"tool\":\"read_file\",\"input\":{\"path\":\"../../../../etc/passwd\"},\"context\":{}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Decision:', d.get('decision','?'))"
```

### 7.3 — Unauthenticated Access Returns 401
```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" $BASE/agents)
echo "Unauthenticated /agents: HTTP $STATUS"
# Expected: 401
```

### 7.4 — Rate Limit Test (Rapid Burst)
```bash
for i in $(seq 1 15); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" $BASE/health)
  echo "Request $i: HTTP $STATUS"
done
```

---

## Phase 8 — End-to-End Demo Scenarios

Run from EC2:
```bash
ssh -i ~/Downloads/acp-prod-key.pem ubuntu@43.205.42.5
cd ~/aegis
source .venv/bin/activate
```

### 8.1 — Run All 3 Demo Agents Against Production
```bash
ACP_GATEWAY_URL=https://aegisagent.in python demos/db_copilot/scripted_demo.py
ACP_GATEWAY_URL=https://aegisagent.in python demos/devops_agent/scripted_demo.py
ACP_GATEWAY_URL=https://aegisagent.in python demos/support_agent/scripted_demo.py
```
Expected: All 3 show PASS — real governance decisions in production.

### 8.2 — Count Total Production Decisions
```bash
# From Mac (after setting TOKEN above)
curl -s "$BASE/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; print('Total decisions:', json.load(sys.stdin)['data']['total'])"
```
**Resume metric:** "X,000+ AI governance decisions processed in production with cryptographic audit trail"

---

## Phase 9 — Full Checklist (Screenshot Each)

Run these all at once from Mac.

```bash
export BASE=https://aegisagent.in
export TENANT="00000000-0000-0000-0000-000000000001"

TOKEN=$(curl -s -X POST $BASE/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

AGENT_ID=$(curl -s $BASE/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['items'][0]['agent_id'])")

echo "=== 1. Gateway health ==="
curl -s $BASE/health

echo ""
echo "=== 2. Services healthy ==="
curl -s $BASE/system/health | python3 -c "
import sys,json
d=json.load(sys.stdin)
s=d.get('services', (d.get('data') or {}).get('services', {}))
healthy=sum(1 for v in s.values() if v.get('status')=='healthy')
print(f'{healthy}/{len(s)} healthy')"

echo ""
echo "=== 3. SSL ==="
curl -vI $BASE 2>&1 | grep -E "issuer|subject|expire"

echo ""
echo "=== 4. Audit chain integrity ==="
curl -s $BASE/audit/verify-chain \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; d=json.load(sys.stdin).get('data',{}); print(f'Chain valid: {d.get(\"chain_valid\")}, Violations: {d.get(\"violations\",0)}')"

echo ""
echo "=== 5. Total decisions ==="
curl -s "$BASE/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; print('Total:', json.load(sys.stdin)['data']['total'])"

echo ""
echo "=== 6. Execute a governance decision ==="
curl -s -X POST $BASE/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"tool\":\"read_file\",\"input\":{\"path\":\"/data/test.csv\"},\"context\":{\"session_id\":\"checklist-$(date +%s)\"}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Decision: {d.get(\"decision\",\"?\")} | Risk: {d.get(\"risk_score\",\"?\")}')"

echo ""
echo "=== 7. Latency ==="
curl -s $BASE/status | python3 -c "
import sys,json
d=json.load(sys.stdin)
lat=d.get('latency', (d.get('data') or {}).get('latency', {}))
print(f'p50={lat.get(\"p50_ms\",\"N/A\")}ms  p95={lat.get(\"p95_ms\",\"N/A\")}ms  p99={lat.get(\"p99_ms\",\"N/A\")}ms')"

echo ""
echo "=== Done ==="
```

---

## Resume Bullets (Fill In After Testing)

```
Production Infrastructure:
• Deployed 24-container AI governance platform on AWS EC2 t3.2xlarge — live at aegisagent.in
• AWS RDS PostgreSQL + ElastiCache Redis (TLS), CI/CD via GitHub Actions → SSH → docker-compose rebuild
• All 24 containers healthy including PgBouncer, Jaeger, Prometheus, Grafana

Platform Performance (measured in production):
• X,000+ AI agent governance decisions with <300ms p50 latency
• Platform MTTR < 30s — Docker restart policies, zero manual intervention
• XXX RPS at 100 concurrent users, <1% error rate (locust load test)
• 12/12 microservices healthy; cryptographic audit chain — 0 violations

Security & Compliance:
• Tamper-evident audit log: ed25519-signed Merkle roots — any deletion publicly detectable
• OPA policy engine enforces governance rules on 100% of AI tool calls
• SQL injection, path traversal, PII exfiltration all blocked in production
• JWT auth with per-tenant rate limiting, zero unauthenticated access
```

---

## Troubleshooting

```bash
# Container restarting — check why
docker logs acp_<service> --tail 50

# Check all containers
docker ps --format "{{.Names}}\t{{.Status}}" | sort

# Test RDS connectivity (from EC2)
PGPASSWORD='Acp2026Prod#Rds$Secure' psql \
  -h acp-postgres-prod.cz0qqg60keaj.ap-south-1.rds.amazonaws.com \
  -U postgre -d postgres -c "SELECT count(*) FROM pg_stat_activity;"

# Test pgbouncer from EC2 host (no SSL — pgbouncer terminates SSL to RDS)
PGPASSWORD='Acp2026Prod#Rds$Secure' psql \
  -h localhost -p 6432 -U postgre -d acp_audit \
  -c "SELECT count(*) FROM audit_logs;"

# Test ElastiCache Redis (TLS required — note the --tls flag)
redis-cli --tls \
  -h master.acp-redis-prod.1gloza.aps1.cache.amazonaws.com \
  -p 6379 ping

# Restart everything cleanly
cd ~/aegis/infra
docker-compose -f docker-compose.yml -f docker-compose.aws.yml down
aws s3 cp s3://acp-backups-prod-am/config/.env ~/aegis/infra/.env
aws s3 cp s3://acp-backups-prod-am/config/pgbouncer.aws.ini ~/aegis/infra/pgbouncer.aws.ini
aws s3 cp s3://acp-backups-prod-am/config/userlist.txt ~/aegis/infra/userlist.txt
docker-compose -f docker-compose.yml -f docker-compose.aws.yml up -d

# Follow gateway logs live
docker logs -f acp_gateway

# Re-seed if charts are empty
cd ~/aegis && source .venv/bin/activate
DATABASE_URL="postgresql+asyncpg://postgre:Acp2026Prod%23Rds%24Secure@localhost:6432/acp_audit" \
ACP_BASE_URL="http://localhost:8000" \
ACP_ADMIN_EMAIL="admin@acp.local" \
ACP_ADMIN_PASSWORD="password" \
python scripts/seed_demo_data.py
```
