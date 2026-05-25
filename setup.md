# Aegis — Setup Guide

> **Runtime security control plane for AI agents.**
> Blocks prompt injection, PII exfiltration, and cross-tenant attacks before they execute. Every decision is signed with ed25519 and anchored into a Merkle transparency log.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Repository Setup](#repository-setup)
3. [Environment Configuration](#environment-configuration)
4. [Bringing Up the Stack](#bringing-up-the-stack)
5. [Database Migrations](#database-migrations)
6. [Admin User Seed](#admin-user-seed)
7. [Demo Pack Provisioning](#demo-pack-provisioning)
8. [Verifying the Setup](#verifying-the-setup)
9. [Running the Demos](#running-the-demos)
10. [Python SDK Quickstart](#python-sdk-quickstart)
11. [Backups & Operations](#backups--operations)
12. [Resetting the Stack](#resetting-the-stack)
13. [Service Map](#service-map)
14. [Troubleshooting](#troubleshooting)

---

## Prerequisites

You need the following installed on your host machine before starting:

| Tool | Minimum version | Purpose |
|---|---|---|
| Docker | 24.x | Container runtime for all 27 containers |
| Docker Compose | v2 | Stack orchestration |
| Python | 3.11+ | Ops scripts, demo runners, SDK |
| Node.js | 20+ | UI build (optional — UI ships in a container too) |
| `jq` | 1.6+ | JSON parsing in setup commands |
| `curl` | any | HTTP requests during setup |
| `age` | 1.1+ | Backup encryption (only needed for Phase 11) |
| `openssl` | any | Generating internal secrets |
| `psql` | 14+ | Optional — direct database inspection |

Verify each tool exists:

```bash
docker --version
docker compose version
python3 --version
node --version
jq --version
age --version
openssl version
```

Expected: every command prints a version string. If anything fails, install it first.

**System resource requirements:**
- 8 GB RAM minimum (16 GB recommended for the full stack with observability)
- 20 GB free disk for Docker volumes
- macOS, Linux, or WSL2 on Windows

---

## Repository Setup

### Clone the repository

```bash
git clone https://github.com/Abhi-mishra998/aegis.git
cd aegis
```

### Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
.venv/bin/python -m pip install --upgrade pip
```

### Install Python dependencies for the host-side tooling

The host needs dependencies for the ops scripts, demo runners, and SDK example. Container dependencies are installed automatically inside Docker.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install psycopg2-binary redis locust httpx
.venv/bin/python -c "import psycopg2, redis, httpx; print('host deps ok')"
```

Expected output: `host deps ok`

---

## Environment Configuration

### Generate the internal secret and required credentials

The internal secret is used for service-to-service authentication. Generate it once and place it in `infra/.env`. The Grafana admin password is also required — docker-compose refuses to start without it.

```bash
cd infra
cp .env.example .env

# Generate and inject the internal secret
INTERNAL_SECRET=$(openssl rand -hex 32)
echo "INTERNAL_SECRET=$INTERNAL_SECRET" >> .env

# Set the required Grafana admin password (any strong password; no default)
echo "GRAFANA_ADMIN_PASSWORD=CHANGE_ME_$(openssl rand -hex 8)" >> .env

cd ..
```

> **Required:** `GRAFANA_ADMIN_PASSWORD` must be set before running `docker compose up`. The compose file uses `${GRAFANA_ADMIN_PASSWORD:?}` which causes the compose command to exit with an error message if the variable is empty or unset.

### Configure host-side environment variables

Add these to your shell profile (`~/.zshrc`, `~/.bashrc`, or equivalent), or `source` them before running ops scripts:

```bash
# Default tenant for all demos
export TENANT="00000000-0000-0000-0000-000000000001"

# Gateway URL
export GATEWAY_URL="http://localhost:8000"

# Per-database DSNs (used by reconciliation, export, and backup scripts)
export ACP_AUDIT_DB="postgresql://postgres:postgres@localhost:5433/acp_audit"
export ACP_USAGE_DB="postgresql://postgres:postgres@localhost:5433/acp_usage"
export ACP_IDENTITY_DB="postgresql://postgres:postgres@localhost:5433/acp_identity"
export ACP_FLIGHT_DB="postgresql://postgres:postgres@localhost:5433/acp_flight_recorder"
export ACP_GRAPH_DB="postgresql://postgres:postgres@localhost:5433/acp_identity_graph"
export ACP_AUTONOMY_DB="postgresql://postgres:postgres@localhost:5433/acp_autonomy"
```

### Optional: Slack webhook for security alerts

If you want auto-response rules to fire real Slack notifications, set the webhook URL in `infra/.env`:

```bash
echo "SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL" >> infra/.env
```

Aegis works without this — alerts are still recorded in the audit log and incidents API.

### Optional: Backup encryption keys

Required only if you plan to test the backup workflow (Phase 11). Generate an age keypair:

```bash
mkdir -p ~/.acp && chmod 700 ~/.acp
age-keygen -o ~/.acp/backup-private.age
chmod 600 ~/.acp/backup-private.age

# Extract public key
PUBLIC_KEY=$(grep '^# public key:' ~/.acp/backup-private.age | awk '{print $4}')
echo "Public key: $PUBLIC_KEY"

# Add to your shell profile
echo "export ACP_BACKUP_AGE_RECIPIENT=\"$PUBLIC_KEY\"" >> ~/.zshrc
echo "export ACP_BACKUP_AGE_IDENTITY=\"$HOME/.acp/backup-private.age\"" >> ~/.zshrc

source ~/.zshrc
```

Verify the keys round-trip:

```bash
echo "hello backup" > /tmp/test.txt
age -r "$ACP_BACKUP_AGE_RECIPIENT" -o /tmp/test.txt.age /tmp/test.txt
age -d -i "$ACP_BACKUP_AGE_IDENTITY" -o /tmp/restored.txt /tmp/test.txt.age
cat /tmp/restored.txt
rm /tmp/test.txt /tmp/test.txt.age /tmp/restored.txt
```

Expected output: `hello backup`

---

## Bringing Up the Stack

### Start all containers

From the repository root:

```bash
cd infra
docker compose down -v
docker compose up --build -d
```

The first build takes 4–8 minutes depending on your machine. Subsequent boots take under a minute.

### Wait for health checks

The stack runs 27 containers across edge, core services, intelligence, cryptographic trust, data, and observability tiers. Wait about 90 seconds for all health checks to pass:

```bash
sleep 90
docker ps --format "{{.Names}}\t{{.Status}}" | grep "(healthy)" | wc -l
```

Expected: **22 or more healthy containers** (a few sidecars run without explicit health checks).

If any container is unhealthy:

```bash
docker ps --format "{{.Names}}\t{{.Status}}" | grep -v "(healthy)"
docker logs <container_name> --tail 40
```

### Resolve the internal secret on the host

Pull whatever the running gateway container has so the host shell and the container agree:

```bash
cd ..
export INTERNAL_SECRET=$(docker exec acp_gateway sh -c 'echo $INTERNAL_SECRET')
[ -n "$INTERNAL_SECRET" ] && echo "✓ INTERNAL_SECRET (${#INTERNAL_SECRET} chars)" || echo "✗ empty — check infra/.env"
```

---

## Database Migrations

Run Alembic migrations against the three databases that own schemas. These are idempotent — running them twice does nothing the second time.

```bash
docker exec acp_audit bash -lc "cd /app/services/audit && python -m alembic upgrade head"
docker exec acp_identity bash -lc "cd /app/services/identity && python -m alembic upgrade head"
docker exec acp_behavior bash -lc "cd /app/services/learning && python -m alembic upgrade head"
```

Expected: each prints `INFO [alembic.runtime.migration] Running upgrade ... done` or no upgrade messages if already migrated.

---

## Admin User Seed

Aegis ships with no users by default. Seed the default admin account — every demo and login path uses this account:

```bash
.venv/bin/python scripts/utils/seed_admin.py
```

Expected output:
```
🔗 Using DB: postgresql+asyncpg://postgres:postgres@localhost:5433/acp_identity
✅ Default tenant already exists
🌱 Seeding admin user...
✅ Admin user created successfully
   Credentials: admin@acp.local / password
```

The seed script is idempotent — running it twice prints "Admin user already exists."

---

## Demo Pack Provisioning

Aegis ships with three demo packs. Each one registers a realistic agent, grants permissions, provisions credentials, and seeds the identity graph.

Run all three:

```bash
.venv/bin/python demos/db_copilot/setup_demo.py
.venv/bin/python demos/devops_agent/setup_demo.py
.venv/bin/python demos/support_agent/setup_demo.py
```

Expected: each prints a setup banner and ends with `✅ Setup complete` plus the agent ID and credentials path.

The three demo packs are:

| Pack | What it provisions |
|---|---|
| **DB Copilot** | A SQL-aware AI agent with `db.query`, `db.execute`, `execute_agent` permissions plus 500 customers + 750 orders of seed data |
| **DevOps Agent** | A Kubernetes operator agent with 23 K8s tool permissions, an autonomy contract limiting destructive ops, and a 20-node identity graph |
| **Support Agent** | A customer-service automation with 9 support-tool permissions, cross-tenant isolation rules, and bulk-export denials |

Credentials for each demo are written to `demos/<pack>/.demo_creds.json` — these are read by the demo runners in Phase 9.

---

## Verifying the Setup

### Authenticate as admin

```bash
export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')

[ "$TOKEN" = "null" ] || [ -z "$TOKEN" ] \
  && echo "✗ login failed — did you run seed_admin.py?" \
  || echo "✓ Token: ${TOKEN:0:40}…"
```

### Check system health

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
  "p95_ms": 15
}
```

If `status` is anything other than `operational`, investigate via `docker logs`.

### Seed the transparency root

The daily Merkle root scheduler runs hourly. On a fresh stack, seed today's root manually so the cryptographic verification works immediately:

```bash
curl -s -X POST "http://localhost:8000/transparency/compute" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '{root_date: .data.root_date, root_hash: (.data.root_hash // .data.signed.root_hash)}'
```

Expected: a JSON object with today's date and a 64-character hex root hash.

If the response is null, there are no audit rows yet — run the demos first, then re-run this command.

### Verify the audit chain

```bash
.venv/bin/acp verify-chain \
  --base-url http://localhost:8000 \
  --token "$TOKEN" \
  --tenant "$TENANT" \
  --json \
  | jq '{valid: .valid, processed, errors: .total_violations}'
```

Expected: `"valid": true` with zero errors.

---

## Running the Demos

### Dry run (offline, ~10 seconds)

Walks through every scenario without hitting the live stack. Use this to verify the demo packs are wired correctly:

```bash
ACP_DRY_RUN=1 .venv/bin/python demos/run_all_demos.py
```

Expected: all three packs print `PASS`.

### Live run (against the running stack, ~45 seconds)

Hits the live gateway, produces real audit records, signed receipts, and Slack alerts (if configured):

```bash
.venv/bin/python demos/run_all_demos.py
```

Expected: all three packs print `PASS` with real risk scores, signed receipts, and a final chain verification showing zero violations.

### What each demo proves

| Pack | Key scenarios | What Aegis enforces |
|---|---|---|
| DB Copilot | Safe SELECT, bulk SELECT, PII exfiltration, DROP TABLE, kill switch | DDL hard-deny, token revocation, PII column filter, tenant-wide kill |
| DevOps Agent | Safe reads, scaling, namespace deletion, privilege escalation, delete storm | K8s hard-deny, autonomy contract, rate limiting, blast-radius analysis |
| Support Agent | Ticket lookup, single-customer PII, cross-tenant access, bulk PII export, email exfiltration | Tenant isolation, email hard-deny via OPA, behavioral throttling |

---

## Python SDK Quickstart

Aegis ships with a Python SDK at `sdk/acp_client` and a runnable example at `examples/agent.py`.

### Provision an agent for the SDK

First, create an agent and get its runtime token:

```bash
# Create the agent
export AID=$(curl -s -X POST "http://localhost:8000/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "sdk-example-agent",
        "description": "Agent used by examples/agent.py",
        "owner_id": "sdk-user",
        "risk_level": "low"
      }' \
  | jq -r '.data.id')

# Grant permissions
for tool in read_file query db.query; do
  curl -s -X POST "http://localhost:8000/agents/$AID/permissions" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Tenant-ID: $TENANT" \
    -H "Content-Type: application/json" \
    -d "{\"tool_name\":\"$tool\",\"action\":\"ALLOW\"}" >/dev/null
done

# Provision credentials
export AGENT_SECRET="sdk-example-$(date +%s)"
curl -s -X POST "http://localhost:8002/auth/credentials" \
  -H "X-Internal-Secret: $INTERNAL_SECRET" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"secret\":\"$AGENT_SECRET\"}" >/dev/null

# Issue the runtime JWT
export AGENT_TOKEN=$(curl -s -X POST "http://localhost:8000/auth/agent/token" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AID\",\"secret\":\"$AGENT_SECRET\"}" \
  | jq -r '.data.access_token')

echo "Agent ID:    $AID"
echo "Agent token: ${AGENT_TOKEN:0:40}…"
```

### Run the SDK example

```bash
export ACP_AGENT_ID="$AID"
export ACP_TOKEN="$AGENT_TOKEN"
.venv/bin/python examples/agent.py
```

Expected output:
```
allow -> [{'row': '1', 'value': 'SELECT * FROM customers LIMIT 1'}]
deny  -> Security: Tool 'shell.exec' not in agent's allow-list
```

### Five-line integration in your own code

```python
import acp

client = acp.Client()  # reads ACP_TOKEN + ACP_BASE_URL from env

@client.protect(agent_id=ACP_AGENT_ID, tool="db.query")
def query_database(sql: str) -> list[dict]:
    return db.execute(sql)  # only runs if Aegis allows it
```

The decorator handles authentication, policy checks, behavioral scoring, audit logging, and signed receipts — your code stays unchanged.

---

## Backups & Operations

### Encrypted offsite backup

Aegis ships with a backup script that dumps every database, encrypts each dump with age, and uploads them to an S3-compatible bucket.

Prerequisites: `ACP_BACKUP_AGE_RECIPIENT` and `ACP_BACKUP_AGE_IDENTITY` set (see [Environment Configuration](#environment-configuration)), plus AWS credentials configured.

```bash
export ACP_BACKUP_S3_BUCKET="s3://your-backup-bucket/aegis"
export ACP_BACKUP_S3_ENDPOINT="https://s3.amazonaws.com"  # or your MinIO/S3-compatible endpoint
export AWS_DEFAULT_REGION="us-east-1"

PGPASSWORD=postgres POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
  bash scripts/ops/backup.sh
```

Expected: `✓ PASSED — 8 databases backed up to s3://your-backup-bucket/aegis`

### Audit/billing reconciliation

The reconciliation script verifies that every audit row has a corresponding billing record (and vice versa). It exits non-zero on any gap, so it works as a cron-driven SLI:

```bash
.venv/bin/python scripts/ops/reconcile.py \
  --tenant "$TENANT" \
  --json \
  | jq '{status, audit_without_usage_count, usage_without_audit_count}'
```

Expected: `"status": "VERIFIED"`, both counts zero.

### Tenant export (GDPR portability)

Produces a TAR archive of every audit, usage, flight, graph, autonomy, and transparency row for one tenant:

```bash
.venv/bin/python scripts/ops/export_tenant.py \
  --tenant "$TENANT" \
  --output reports/exports/${TENANT}.tar.gz
```

### Restore drill

Boots an isolated stack on a separate Docker network, restores the latest backups, runs chain verification + reconciliation, and writes a verdict to `reports/restore_drill/`:

```bash
bash scripts/ops/restore_drill.sh --dry-run   # exercises wiring, no docker
bash scripts/ops/restore_drill.sh             # full drill
```

---

## Resetting the Stack

### Clean reset (preserves built images)

```bash
cd infra
docker compose down
docker compose up -d
sleep 90
cd ..
```

Then re-run the seed and demo provisioning:

```bash
.venv/bin/python scripts/utils/seed_admin.py
.venv/bin/python demos/db_copilot/setup_demo.py
.venv/bin/python demos/devops_agent/setup_demo.py
.venv/bin/python demos/support_agent/setup_demo.py
```

### Nuclear reset (wipes all volumes)

```bash
cd infra
docker compose down -v
docker compose up --build -d
sleep 90
cd ..
```

Then run the full setup again from [Database Migrations](#database-migrations) onwards.

### Wipe demo data without restarting containers

```bash
docker exec acp_postgres psql -U postgres -d acp_audit -c \
  "TRUNCATE audit_logs, pending_usage_events, kill_switches CASCADE;"
docker exec acp_postgres psql -U postgres -d acp_usage -c \
  "TRUNCATE usage_records;"
docker exec acp_redis redis-cli FLUSHDB
```

Note: kill switches are double-written to Postgres for durability. The `TRUNCATE kill_switches` above handles the database side. Redis is rehydrated from the database on the next gateway request.

---

## End-to-End System Verification

Run this after every significant change or new service addition to confirm the full stack is wired correctly. Does not require a live Docker stack — uses the offline test suite.

### Unit + integration tests (no containers required)

```bash
# Core test suites — security fixes, crypto chain, decision engine, gateway proxy
python3 -m pytest \
  tests/test_security_fixes.py \
  tests/test_findings_vocabulary.py \
  tests/test_decision_engine.py \
  tests/test_verifier.py \
  services/audit/tests/ \
  services/gateway/tests/ \
  sdk/ \
  -v --tb=short

# Expected: 170+ tests passing, 0 failures
# Note: sdk/acp_client/tests/test_init_project.py requires PyYAML.
#   Install with: pip install pyyaml   — or skip: add --ignore=sdk/acp_client/tests/test_init_project.py
# Note: tests/test_audit_chain_properties.py and test_decision_engine_properties.py
#   require hypothesis: pip install hypothesis
```

### Anomaly detector eval (optional — requires scikit-learn)

```bash
# Reproducible evaluation harness — deterministic seed(42)
python3 tests/eval/anomaly_eval.py

# Expected output:
#   Isolation Forest:   Precision=0.71, Recall=0.70, F1=0.71
#   Heuristic fallback: Precision=1.00, Recall=0.60, F1=0.75
```

### Demo dry-run (no containers, ~10 seconds)

```bash
ACP_DRY_RUN=1 .venv/bin/python demos/run_all_demos.py

# Expected: all 3 packs print PASS
```

### Live stack verification (requires running containers)

```bash
# 1. All containers healthy
docker ps --format "{{.Names}}\t{{.Status}}" | grep "(healthy)" | wc -l
# Expected: 22+

# 2. Gateway health
curl -s http://localhost:8000/system/health | jq '{status, healthy, total}'
# Expected: {"status": "operational", "healthy": 12, "total": 12}

# 3. Authenticate
export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')

# 4. Audit chain integrity
.venv/bin/acp verify-chain \
  --base-url http://localhost:8000 \
  --token "$TOKEN" \
  --tenant "00000000-0000-0000-0000-000000000001" \
  --json | jq '{valid: .ok, processed, errors: .total_violations}'
# Expected: {"valid": true, "errors": 0}

# 5. Billing reconciliation
.venv/bin/python scripts/ops/reconcile.py \
  --tenant "00000000-0000-0000-0000-000000000001" \
  --json | jq '{status, audit_without_usage_count, usage_without_audit_count}'
# Expected: {"status": "VERIFIED", ...}

# 6. UI build (no browser required — catches import/proxy errors)
cd ui && npm run build 2>&1 | tail -5
# Expected: "built in Xs" with no errors
```

### Offline chain verifier (new subcommands)

```bash
# If you have an exported audit bundle:
acp verify receipt receipt.json --pubkey ./aegis_public.pem   # single receipt
acp verify export ./audit_export/                              # full bundle
acp verify chain ./roots_dir/                                  # root chain
acp verify inclusion receipt.json proof.json                   # Merkle proof
# All commands: exit 0 = valid, exit 1 = tamper detected; --json for machine output
```

---

## Service Map

| Tier | Service | Port | Container |
|---|---|---|---|
| Edge | Gateway | 8000 | acp_gateway |
| Edge | UI Dashboard | 5173 | acp_ui |
| Identity | Identity | 8002 | acp_identity |
| Identity | Registry | 8001 | acp_registry |
| Decision | Policy (OPA) | 8003 | acp_policy |
| Decision | Decision Engine | 8010 | acp_decision |
| Audit | Audit | 8004 | acp_audit |
| Billing | Usage | 8006 | acp_usage |
| Intelligence | Behavior Engine | 8007 | acp_behavior |
| Intelligence | Insight / Groq Worker | 8011 | acp_insight |
| Forensics | Forensics | 8012 | acp_forensics |
| Runtime Trust | Identity Graph | 8013 | acp_identity_graph |
| Runtime Trust | Flight Recorder | 8014 | acp_flight_recorder |
| Runtime Trust | Autonomy | 8015 | acp_autonomy |
| API | Incidents + ARE | 8005 | acp_api |
| Data | PostgreSQL | 5433 | acp_postgres |
| Data | Redis | 6379 | acp_redis |
| Data | PgBouncer | 6432 | acp_pgbouncer |
| Data | OPA Bundle Server | 8181 / 8182 | acp_opa |
| Observability | Prometheus | 9090 | acp_prometheus |
| Observability | AlertManager | 9093 | acp_alertmanager |
| Observability | Grafana | 3000 | acp_grafana |
| Observability | Jaeger | 16686 | acp_jaeger |

Default credentials for observability tools:

- Grafana: `admin` / `$GRAFANA_ADMIN_PASSWORD` — **must be set in `infra/.env` before boot** (docker-compose will refuse to start without it)
- UI dashboard: `admin@acp.local` / `password`

---

## Troubleshooting

### Stack won't start: "address already in use"

A previous run is still bound to one of the ports. Find and kill it:

```bash
lsof -i :8000
lsof -i :5433
```

Or run `docker compose down` from `infra/` to ensure all Aegis containers are stopped.

### `seed_admin.py` fails with "could not connect"

The Postgres container hasn't finished booting. Wait 30 seconds and try again:

```bash
docker logs acp_postgres --tail 20
```

Look for `database system is ready to accept connections`.

### `/auth/token` returns "Invalid credentials"

The admin user wasn't seeded. Run:

```bash
.venv/bin/python scripts/utils/seed_admin.py
```

### Token returns 401 unexpectedly

Admin JWTs expire after 15 minutes. Re-authenticate:

```bash
export TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | jq -r '.data.access_token')
```

### Demo runner can't find `.demo_creds.json`

The setup script for that pack wasn't run, or it failed silently. Re-run:

```bash
.venv/bin/python demos/<pack_name>/setup_demo.py
```

### `acp verify-chain` returns `valid: false`

Either an audit log has been tampered with, or the chain rebuild logic encountered a database shard it doesn't recognize. Inspect:

```bash
.venv/bin/acp verify-chain \
  --base-url http://localhost:8000 \
  --token "$TOKEN" \
  --tenant "$TENANT" \
  --json | jq '.violations'
```

If you intentionally tampered with the audit log (testing), reset with `TRUNCATE audit_logs` from the [Resetting the Stack](#resetting-the-stack) section.

### Containers report healthy but the UI shows "service unreachable"

The gateway's tenant-meta cache may be stale. Invalidate it:

```bash
docker exec acp_redis redis-cli DEL "acp:tenant:meta:$TENANT"
```

Then refresh the UI.

### Grafana dashboards show "no data"

Prometheus needs ~30 seconds after boot to scrape all targets. Check target status:

```bash
curl -s http://localhost:9090/api/v1/targets \
  | jq '.data.activeTargets | map(.health) | group_by(.) | map({state: .[0], n: length})'
```

Expected: 17 targets, all `up`.

### Slack alerts not firing

Check the API service logs for the webhook response:

```bash
docker logs acp_api 2>&1 | grep "hooks.slack.com" | tail -5
```

A successful fire prints `HTTP/1.1 200 OK`. If you see 403 or 404, the webhook URL is wrong — update `SLACK_WEBHOOK_URL` in `infra/.env` and restart the API service.

---

## Verifying the Complete Setup

If everything above is configured correctly, this single command should succeed:

```bash
curl -s "http://localhost:8000/system/health" | jq '.status' && \
  .venv/bin/python scripts/ops/reconcile.py --tenant "$TENANT" --json | jq '.status' && \
  .venv/bin/acp verify-chain --base-url http://localhost:8000 --token "$TOKEN" --tenant "$TENANT" --json | jq '.valid'
```

Expected output:
```
"operational"
"VERIFIED"
true
```

Three checks: stack is up, billing reconciliation is clean, audit chain is cryptographically intact. If you see all three, Aegis is fully operational and ready to govern AI agents.

---

## Next Steps

- **Explore the UI:** Open [http://localhost:5173](http://localhost:5173) and walk through Flight Recorder, Identity Graph, Audit Trail, and Autonomy Contracts.
- **Read the architecture docs:** Detailed diagrams and design rationale live in `docs/architecture/`.
- **Integrate the SDK:** Follow the [Python SDK Quickstart](#python-sdk-quickstart) to protect your own agent code in five lines.
- **Run a load test:** `tests/load/locustfile.py` simulates concurrent users against the full pipeline. Launch with the Locust web UI on port 8090: `.venv/bin/locust -f tests/load/locustfile.py --host http://localhost:8000 --web-port 8090`
- **Review the demo scripts:** Each `demos/<pack>/scripted_demo.py` is a readable end-to-end scenario you can adapt to your own threat model.

---

**Aegis — Every agent action governed. Every decision proved.**