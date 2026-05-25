# ACP Production Testing Guide
## Live Platform: https://aegisagent.in

---

## Quick Reference

```
Platform URL:     https://aegisagent.in
Admin login:      admin@acp.local / password
Demo login:       demo@aegisagent.in / demo
EC2 IP:           43.205.42.5
EC2 key:          ~/Downloads/acp-prod-key.pem
Grafana (tunnel): http://localhost:3000  (admin / ACP_Grafana_2026!)
Default tenant:   00000000-0000-0000-0000-000000000001
```

---

## Phase 0 — Deploy Fresh Code (Run After Every Git Push)

SSH into EC2 and redeploy:

```bash
ssh -i ~/Downloads/acp-prod-key.pem ubuntu@43.205.42.5

cd ~/aegis

# Pull latest code
git pull origin main

# Rebuild and restart all services
cd infra
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build

# Wait 60s for services to start, then check health
sleep 60
docker ps --format "{{.Names}}\t{{.Status}}" | sort
```

Expected: all containers show `healthy` or `Up`.

---

## Phase 1 — First Boot: Seed Admin + Demo Data

Run once after the first deploy. Skip sections already completed.

### 1.1 — Create Admin User

```bash
# SSH into EC2 first, then run:
docker exec \
  -e DATABASE_URL="postgresql+asyncpg://identity_user:identity_prod_pwd@pgbouncer:6432/acp_identity" \
  acp_gateway python seed_admin.py
```

Expected output:
```
✅ Admin user created successfully
   Credentials: admin@acp.local / password
```

If it says "already exists" — skip, already done.

### 1.2 — Seed Demo Data

```bash
# Run from EC2 host
cd ~/aegis

# Create venv if not yet done
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[server,dev]" -q

# Run seed
DATABASE_URL="postgresql+asyncpg://postgre:Acp2026Prod%23Rds%24Secure@localhost:6432/acp_audit" \
ACP_BASE_URL="http://localhost:8000" \
ACP_ADMIN_EMAIL="admin@acp.local" \
ACP_ADMIN_PASSWORD="password" \
python scripts/seed_demo_data.py
```

Expected output:
```
[1/4] Demo user (demo@aegisagent.in)...  OK
[2/4] Audit logs (acp_audit database)... OK — 2000 rows seeded
[3/4] Incidents (acp_api database)...    OK — 35 incidents seeded
[4/4] Usage records (acp_usage database)... OK — 2000 records seeded
```

### 1.3 — Verify Login Works (from EC2)

```bash
curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Token OK:', d['data']['access_token'][:30]+'...')"
```

---

## Phase 2 — System Health (run from your Mac)

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
services = d.get('services', (d.get('data') or {}).get('services', {}))
healthy = sum(1 for v in services.values() if v.get('status') == 'healthy')
total = len(services)
print(f'{healthy}/{total} services healthy')
for name, v in sorted(services.items()):
    mark = 'OK' if v.get('status') == 'healthy' else 'FAIL'
    print(f'  [{mark}] {name}: {v.get(\"status\", \"unknown\")}')
"
```

Expected: `12/12 services healthy`

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
items = d.get('data', {}).get('items', [])
print(f'Agents registered: {len(items)}')
for a in items[:5]:
    print(f'  - {a[\"name\"]} ({a[\"agent_id\"][:8]}...)')
"
```

If this returns 0 agents — run Phase 1.2 seed first.

### 2.5 — Execute a Governance Decision

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
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
data=d.get('data') or d
print(f'Chain valid: {data.get(\"chain_valid\")}, Violations: {data.get(\"violations\",0)}')
"
```

Expected: `Chain valid: True, Violations: 0`

### 2.7 — Total Decisions

```bash
curl -s "$BASE/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Total decisions:', d['data']['total'])"
```

### 2.8 — Signed Receipt

```bash
LOG_ID=$(curl -s "$BASE/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['items'][0]['id'])")

curl -s $BASE/receipts/$LOG_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | python3 -m json.tool
```

Expected: `"signature"`, `"merkle_root"`, `"verified": true`

### 2.9 — SSL Certificate

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
lat = d.get('latency', (d.get('data') or {}).get('latency', {}))
print(f'p50:      {lat.get(\"p50_ms\", \"N/A\")} ms')
print(f'p95:      {lat.get(\"p95_ms\", \"N/A\")} ms')
print(f'p99:      {lat.get(\"p99_ms\", \"N/A\")} ms')
print(f'Requests: {lat.get(\"request_count\", 0)}')
"
```

**Target:** p50 < 300ms, p95 < 1500ms

### 3.2 — Execute Endpoint Latency (10 decisions)

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

## Phase 4 — Load Test (100 Users)

Install locust on your Mac:
```bash
pip install locust
```

### 4.1 — Create Locust File

Save as `/tmp/locustfile.py`:

```python
from locust import HttpUser, task, between
import random, uuid, time

TENANT = "00000000-0000-0000-0000-000000000001"

class ACPUser(HttpUser):
    wait_time = between(1, 3)
    host = "https://aegisagent.in"
    token = None
    agent_id = None

    def on_start(self):
        # Stagger logins to avoid thundering-herd on identity service
        time.sleep(random.uniform(0, 5))

        r = self.client.post("/auth/token",
            json={"email": "admin@acp.local", "password": "password"},
            headers={"Content-Type": "application/json",
                     "X-Tenant-ID": TENANT})
        if r.status_code != 200:
            self.environment.runner.quit()
            return
        self.token = r.json()["data"]["access_token"]

        agents = self.client.get("/agents",
            headers={"Authorization": f"Bearer {self.token}",
                     "X-Tenant-ID": TENANT})
        items = agents.json().get("data", {}).get("items", [])
        self.agent_id = items[0]["agent_id"] if items else None

    def _h(self):
        return {"Authorization": f"Bearer {self.token}",
                "X-Tenant-ID": TENANT,
                "Content-Type": "application/json"}

    @task(5)
    def execute_decision(self):
        if not self.agent_id or not self.token:
            return
        self.client.post("/execute",
            json={
                "agent_id": self.agent_id,
                "tool": random.choice(["read_file", "list_dir", "query_db", "send_email"]),
                "input": {"path": f"/data/{uuid.uuid4().hex[:8]}"},
                "context": {"session_id": str(uuid.uuid4())}
            },
            headers=self._h())

    @task(2)
    def check_health(self):
        self.client.get("/health")

    @task(1)
    def list_agents(self):
        if self.token:
            self.client.get("/agents",
                headers={"Authorization": f"Bearer {self.token}",
                         "X-Tenant-ID": TENANT})

    @task(1)
    def audit_logs(self):
        if self.token:
            self.client.get("/audit/logs?limit=10",
                headers={"Authorization": f"Bearer {self.token}",
                         "X-Tenant-ID": TENANT})
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
  --spawn-rate 5 \
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

**Targets:** RPS > 50, failure rate < 1%, p50 < 300ms

---

## Phase 5 — MTTR Testing

SSH into EC2. Open two terminals.

**Terminal 1 — Watch states:**
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

### Kill Audit Service

```bash
time docker kill acp_audit && \
  until docker inspect acp_audit --format '{{.State.Health.Status}}' 2>/dev/null | grep -q healthy; do
    sleep 1
  done && echo "AUDIT RECOVERED"
```

### Chaos: Kill 5 Services Simultaneously

```bash
docker kill acp_audit acp_identity acp_registry acp_decision acp_usage

start=$(date +%s)
until [ "$(docker ps --format '{{.Status}}' | grep -c '(healthy)')" -ge 18 ]; do
  sleep 2
done
end=$(date +%s)
echo "Full recovery: $((end - start)) seconds"
```

**Resume metric:** "Platform MTTR < 30s — self-heals via Docker restart policies"

---

## Phase 6 — Observability

### Grafana (SSH tunnel from Mac)

```bash
ssh -i ~/Downloads/acp-prod-key.pem -L 3000:localhost:3000 ubuntu@43.205.42.5 -N &
open http://localhost:3000
# Login: admin / ACP_Grafana_2026!
```

Dashboards to screenshot: Platform SLO, Trust Layers, Tenant Activity, Queues.

### Prometheus (SSH tunnel)

```bash
ssh -i ~/Downloads/acp-prod-key.pem -L 9090:localhost:9090 ubuntu@43.205.42.5 -N &
open http://localhost:9090
```

Key queries:
```
acp_decision_total
rate(acp_decision_total{outcome="block"}[5m])
histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))
up
```

### Jaeger Traces (SSH tunnel)

```bash
ssh -i ~/Downloads/acp-prod-key.pem -L 16686:localhost:16686 ubuntu@43.205.42.5 -N &
open http://localhost:16686
```

---

## Phase 7 — Security Verification

Set vars first (Phase 2 above).

### SQL Injection — Must Be Blocked

```bash
curl -s -X POST $BASE/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"tool\":\"query_db\",\"input\":{\"query\":\"'; DROP TABLE users; --\"},\"context\":{}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Decision:', d.get('decision','?'), '| Findings:', d.get('findings',[]))"
```

Expected: `decision: block`

### Path Traversal — Must Be Blocked

```bash
curl -s -X POST $BASE/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"tool\":\"read_file\",\"input\":{\"path\":\"../../../../etc/passwd\"},\"context\":{}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Decision:', d.get('decision','?'))"
```

Expected: `decision: block`

### Unauthenticated Access — Must Return 401

```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" $BASE/agents)
echo "Unauthenticated /agents: HTTP $STATUS"
```

Expected: `HTTP 401`

---

## Phase 8 — E2E Demo Scenarios (from EC2)

```bash
ssh -i ~/Downloads/acp-prod-key.pem ubuntu@43.205.42.5
cd ~/aegis
source .venv/bin/activate

ACP_GATEWAY_URL=https://aegisagent.in python demos/db_copilot/scripted_demo.py
ACP_GATEWAY_URL=https://aegisagent.in python demos/devops_agent/scripted_demo.py
ACP_GATEWAY_URL=https://aegisagent.in python demos/support_agent/scripted_demo.py
```

Expected: All 3 show PASS.

---

## Phase 9 — Full Checklist (one-shot)

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
  | python3 -c "import sys,json; items=json.load(sys.stdin)['data']['items']; print(items[0]['agent_id']) if items else print('')")

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
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
data=d.get('data') or d
print(f'Chain valid: {data.get(\"chain_valid\")}, Violations: {data.get(\"violations\",0)}')"

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

## Troubleshooting

### UI Login Fails ("Invalid credentials")

The identity service requires `X-Tenant-ID` on `/auth/token`. This is fixed in the latest code — deploy it:

```bash
cd ~/aegis && git pull origin main
cd infra && docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d identity
```

### Most API Calls Return 503 or Hang

The auth middleware does a Redis revocation check on every request. If Redis is unreachable, every authenticated call returns 503. Check Redis TLS:

```bash
# On EC2 — test TLS connection
redis-cli --tls \
  -h master.acp-redis-prod.1gloza.aps1.cache.amazonaws.com \
  -p 6379 ping
```

Expected: `PONG`. If this fails, check `infra/.env`:

```bash
grep REDIS_URL ~/aegis/infra/.env
# Must be: rediss://master.acp-redis-prod.1gloza.aps1.cache.amazonaws.com:6379/0
#            ^^^^^ note the double-s (TLS)
```

If wrong, fix it and pull from S3:

```bash
aws s3 cp s3://acp-backups-prod-am/config/.env ~/aegis/infra/.env
cd ~/aegis/infra
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d
```

### `/status` Returns HTML Instead of JSON

nginx is not proxying `/status`. Fix is in the latest code (nginx.conf updated). Deploy it:

```bash
cd ~/aegis && git pull origin main
cd infra && docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d ui
```

### Containers Restarting

```bash
docker logs acp_<service> --tail 50
docker ps --format "{{.Names}}\t{{.Status}}" | sort
```

### RDS Connectivity

```bash
PGPASSWORD='Acp2026Prod#Rds$Secure' psql \
  -h acp-postgres-prod.cz0qqg60keaj.ap-south-1.rds.amazonaws.com \
  -U postgre -d postgres -c "SELECT count(*) FROM pg_stat_activity;"

# Via pgbouncer (faster, no SSL overhead)
PGPASSWORD='Acp2026Prod#Rds$Secure' psql \
  -h localhost -p 6432 -U postgre -d acp_audit \
  -c "SELECT count(*) FROM audit_logs;"
```

### Full Restart from Scratch

```bash
cd ~/aegis/infra
docker compose -f docker-compose.yml -f docker-compose.aws.yml down
aws s3 cp s3://acp-backups-prod-am/config/.env ~/aegis/infra/.env
aws s3 cp s3://acp-backups-prod-am/config/pgbouncer.aws.ini ~/aegis/infra/pgbouncer.aws.ini
aws s3 cp s3://acp-backups-prod-am/config/userlist.txt ~/aegis/infra/userlist.txt
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d
```

### Re-seed Charts

```bash
cd ~/aegis && source .venv/bin/activate
DATABASE_URL="postgresql+asyncpg://postgre:Acp2026Prod%23Rds%24Secure@localhost:6432/acp_audit" \
ACP_BASE_URL="http://localhost:8000" \
ACP_ADMIN_EMAIL="admin@acp.local" \
ACP_ADMIN_PASSWORD="password" \
python scripts/seed_demo_data.py
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
