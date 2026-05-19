# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

ACP is a **runtime security gateway for AI agents** — it sits between agent code and the world as a reverse proxy + security enforcement layer. Its two jobs: (1) deny dangerous actions before execution via a multi-phase pipeline (auth → rate limit → OPA → AI risk scoring → kill-switch → audit), and (2) prove what happened via a tamper-evident, HMAC hash-chained audit log with ed25519 cryptographic receipts.

**Not** an agent framework, LLM provider, or APM tool.

## Commands

### Running the Full Stack

```bash
# Start all 26 containers (requires .env in root and infra/)
cd infra && docker compose up --build -d
sleep 90   # wait for health checks

# Provision admin user (first boot only)
.venv/bin/python scripts/utils/seed_admin.py

# Run database migrations
docker exec acp_audit bash -lc "cd /app/services/audit && alembic upgrade head"
# Repeat for each service DB: acp_identity, acp_registry, acp_api, acp_usage, etc.
```

### Python Environment

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Tests

```bash
# Unit tests (no stack required)
.venv/bin/python -m pytest tests/test_are.py tests/test_audit_fixes.py \
  tests/test_decision_engine.py tests/test_production_readiness.py \
  tests/chaos/test_resilience.py -v

# Single test file
.venv/bin/python -m pytest tests/test_are.py -v

# Single test by name
.venv/bin/python -m pytest tests/test_are.py::test_name -v

# E2E tests (requires running stack)
.venv/bin/python -m pytest tests/e2e/ -v
```

### Linting and Type Checking

```bash
.venv/bin/ruff check .           # linter (config: ruff.toml)
.venv/bin/mypy services/         # type checker (config: mypy.ini)
```

### Load Testing

```bash
.venv/bin/locust -f tests/load/locustfile.py --host http://localhost:8000 \
  --users 100 --spawn-rate 10 --run-time 120s --headless
# Expected baselines: p95 < 400ms, correctness > 95%, audit-usage 100% match
```

### UI Development

```bash
cd ui && npm install && npm run dev   # → http://localhost:5173
```

### ACP CLI

```bash
acp verify-chain   # cryptographic audit chain verification
acp verify-root    # transparency root signature check
```

## Architecture

### Service Topology

14 FastAPI microservices across 6 tiers:

| Port | Service | Role | Database |
|------|---------|------|----------|
| 8000 | Gateway | Edge proxy, auth, kill-switch, SSE | — |
| 8001 | Registry | Agent CRUD, tool permissions | acp_registry |
| 8002 | Identity | JWT issuance, token revocation | acp_identity |
| 8003 | Policy | OPA policy evaluation | — |
| 8004 | Audit | Immutable hash-chained events, receipts | acp_audit |
| 8005 | API | Incidents, ARE rules, API keys | acp_api |
| 8006 | Usage | Billing, outbox pattern, reconciliation | acp_usage |
| 8007 | Behavior | Anomaly scoring, behavioral baselines | Redis |
| 8010 | Decision | Risk aggregation, Groq routing | — |
| 8011 | Insight | Groq narrative explanations (background) | Redis |
| 8012 | Forensics | Timeline replay, investigation profiles | — |
| 8013 | Identity Graph | Agent correlation, compromise simulation | acp_identity_graph |
| 8014 | Flight Recorder | Execution timelines, step snapshots | acp_flight_recorder |
| 8015 | Autonomy | Contract enforcement, bounded autonomy | acp_autonomy |

Infrastructure: PostgreSQL :5433, PgBouncer :6432, Redis :6379, OPA :8181.

### `/execute` Request Lifecycle

```
1. Per-IP rate limit (Redis)
2. JWT auth → cookie or Bearer, revocation check (SHA-256 hash in Redis)
3. JTI replay dedup (SETNX 50ms window)
4. Kill-switch check (tenant/agent Redis keys)
5. Security signals: behavior anomaly + Registry allowed_tools + OPA eval
6. Decision Engine: weighted risk aggregation → Groq fast (≤0.75) or deep (≥0.75)
7. HMAC hash-chain write to Postgres + Redis XADD + SSE publish
```

### Billing Durability (Transactional Outbox)

Every `/execute` call atomically writes an audit row + `pending_usage_event` in one DB transaction. A background worker drains the outbox to the Usage service. This is the **mandatory** pattern — no fire-and-forget billing calls. `scripts/ops/reconcile.py` verifies audit↔usage set parity and exits non-zero on any gap.

### Autonomous Response Engine (ARE)

Consumes two Redis streams: `acp:incidents:queue` and `acp:audit:events`. Evaluation: enabled check → backpressure → SHA-256 dedup → load rules → pre-filter → per-rule (suppression, cooldown, rate limit, window count, condition trace). Action types: `KILL_AGENT`, `ISOLATE_AGENT`, `BLOCK_TOOL`, `THROTTLE`, `ALERT`. Modes: `auto` (immediate), `manual` (queue for human), `suggest` (SSE only).

### Frontend

React 18 + Vite + Tailwind CSS + React Router v6 + Zod v4.3.6. JWT lives in httpOnly cookie only (never localStorage). Real-time via `useSSE()` (EventSource with exponential backoff) + 30s polling fallback. `eventBus.js` translates SSE events to typed CustomEvents. All API response shapes validated with Zod schemas in `ui/src/lib/schemas.js`.

### SDK

`sdk/acp_client/` — Python SDK. `sdk/common/` — shared config, Redis client, settings (canonical import: `sdk.common.config`). `sdk/acp-js/` — TypeScript SDK.

## Key Invariants

- **Tool permission `action` must be uppercase** (`ALLOW`/`DENY`). Lowercase is silently normalized to uppercase on write — no 422. The required execute permission is `execute_agent`, not `execute_tool`.
- **OPA fail-closed**: `OPA_FAIL_MODE=closed` — OPA failure → deny, not allow. OPA deny floors risk at 0.70 → `ESCALATE` (HTTP 403, `"error": "approval_required"`); it does NOT hard-kill. **Admin bypass by design**: agents with an `*` ALLOW permission skip OPA tool evaluation entirely — this is an explicit design choice, not a bug.
- **Decision fail-closed**: any exception in the decision pipeline → 403, never 500.
- **`/execute` returns only 200/403/429/502/504** — no 202. `ESCALATE` and approval-required return 403 with `error: "approval_required"`.
- **Audit chain is append-only** — never mutate audit content columns in `audit_logs`. The one permitted exception: `billing_status` is updated by the Usage service after usage_record insertion (HMAC chain does not cover this column). Redaction writes a sha256-hashed record + chain marker row.
- **5 isolated DB users** — services communicate cross-service via HTTP only, never direct DB access.
- **All containers run as user 999:999** (non-root).
- **`_safe_bg(coro)`** wraps fire-and-forget `create_task()` calls (all emit_* calls on the /execute hot path, webhook/slack fire-and-forgets) to prevent silent coroutine exceptions. Long-lived lifespan workers are assigned to task variables and cancelled on shutdown instead.
- **Auth backpressure**: `/auth/token` and `/auth/login` are guarded by `_auth_semaphore` (40 slots) to prevent PgBouncer pool exhaustion under burst login load.
- **Transparency key rotation**: `scripts/maintenance/rotate_transparency_key.py` — promotes current key to `transparency_historical_keys` so old receipts remain verifiable.

## Configuration

Two `.env` files must be kept in sync:
- `.env` (root) — used by scripts and local dev
- `infra/.env` — loaded by Docker Compose containers

Critical variables: `JWT_SECRET_KEY` (32-byte hex, must match across all services), `INTERNAL_SECRET` (service-to-service auth), `ENVIRONMENT` (controls secure cookie flag).

## Key Docs

- `setup.md` — 1,400-line live demo runbook with copy-paste tested steps
- `docs/reconciliation.md` — authoritative billing durability definition
- `docs/risk_reasons.md` — canonical `findings` vocabulary (13 strings)
- `docs/observability_endpoints.md` — `/status` and `/system/health` latency block contract
- `docs/runbooks/` — key rotation, restore drill, audit chain violation, tenant data request
- `docs/dr_runbook.md` — RPO 15min / RTO 30min disaster recovery
