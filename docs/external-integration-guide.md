# Aegis External Integration Guide

> Audience: external developers, ops engineers, BI/analytics teams, third-party security auditors building against the live Aegis platform.

This file closes three findings from the 2026-06-18 Enterprise Security Review:
- **B-003 / F-S1 (HIGH)** — asyncpg + pgbouncer-transaction race
- **B-005 / F-S7 (INFO)** — Path A vs Path B governance depth
- **B-001 / F-S3 (MEDIUM)** — canonical audit-chain walking algorithm

---

## 1. Two execution paths — pick the right one

Aegis exposes **two** governance gates. Pick the one that matches what you're building.

### Path A — `POST /execute`

**For:** AI agents driving tool calls (SDK consumers: `aegis-anthropic`, `aegis-openai`, `aegis-bedrock`, `aegis-langchain`).

**Auth:** `Authorization: Bearer acp_emp_*` + `X-Tenant-ID: <uuid>`

**Governance depth:** **Full pipeline.** Every call flows through:
1. Auth + tenant binding
2. Signal Registry (34 MITRE ATT&CK-mapped signals)
3. OPA Rego policy bundle
4. 5-tier decision: `allow` / `monitor` / `escalate` / `deny` / `quarantine`
5. Audit row with `risk_score`, `findings[]`, `policy_id`, `decision`
6. Optional incident creation
7. Optional approval workflow

**Use this when:** the agent is about to take a *real-world action* (wire transfer, kubectl, file write, email send, DB query). This is the gate the customer is paying for.

### Path B — `POST /v1/messages` and `POST /v1/chat/completions`

**For:** LLM proxying. Anthropic SDK and OpenAI SDK consumers point their base URL at `https://aegisagent.in` and get token-budget enforcement + audit logging + content scanning.

**Auth:** `x-api-key: acp_emp_*` (Anthropic-style) or `Authorization: Bearer acp_emp_*` (OpenAI-style).

**Governance depth:** **Thinner gate.** Path B does:
- Employee virtual-key authentication
- Per-employee + per-tenant daily/monthly USD budget caps
- Content scanning for high-signal patterns (path traversal, SQL injection, AWS credential paths, "ignore previous instructions", "disable guardrails")
- Audit row tagged `action='llm_proxy_call'`, `tool='anthropic_messages'`, `decision='allow'|'deny'|'error'`

Path B does **NOT** run:
- The full Signal Registry (34 signals → risk score)
- The OPA Rego policy bundle
- The 5-tier decision engine
- MITRE ATT&CK classification

This is by design — Path B is on the *LLM round-trip* hot path; running the full pipeline on every chat completion would add 50-200 ms p95 to every Claude call. Customers who want full governance over LLM responses should pipe the response **through Path A** before acting on it.

**Use this when:** the agent is generating text, summarising, embedding, doing intermediate reasoning — anything that doesn't immediately produce a real-world side effect.

---

## 2. Connecting directly to the Aegis Postgres (analytics, BI, custom dashboards)

Aegis Postgres lives behind a pgbouncer running in `pool_mode=transaction`. This is **the most efficient pool mode** for short-lived OLTP queries — but it has one trap that bites every ad-hoc client.

### The trap

Default asyncpg installs a *prepared-statement cache* keyed by query text. asyncpg names cached statements sequentially: `__asyncpg_stmt_1__`, `__asyncpg_stmt_2__`, …

In `pool_mode=transaction`, pgbouncer can hand the same backend connection to a different client *after every transaction*. If client A leaves `__asyncpg_stmt_1__` prepared and client B then tries to prepare its own `__asyncpg_stmt_1__`, Postgres returns:

```
asyncpg.exceptions.DuplicatePreparedStatementError:
  prepared statement "__asyncpg_stmt_1__" already exists
```

This is **NOT** a bug in pgbouncer or asyncpg — they're both working as documented. It's a misconfiguration that every external integrator hits if they don't know about it.

### The one-line fix

Pass `statement_cache_size=0` when connecting:

```python
import asyncpg

conn = await asyncpg.connect(
    "postgresql://USER:PASS@HOST:PORT/DB",
    statement_cache_size=0,       # ← required for pgbouncer transaction mode
)
```

Or with a pool:

```python
pool = await asyncpg.create_pool(
    DATABASE_URL,
    statement_cache_size=0,
    min_size=4,
    max_size=20,
)
```

Or via SQLAlchemy asyncpg:

```python
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    "postgresql+asyncpg://USER:PASS@HOST:PORT/DB",
    connect_args={"statement_cache_size": 0},
)
```

### Why we can't fix this transparently

We could put `pgbouncer.ignore_startup_parameters = statement_cache_size` and pre-set the option on the backend — but that ships a server-side knob and asyncpg ignores it. The client has to opt in.

Document it loudly in every integration README. Add a preflight to the customer SDK that issues two SELECTs on the same connection and raises a friendly error if it hits the cache collision.

---

## 3. Walking the audit chain (for external verifiers / SOC 2 evidence collection)

`audit_logs` is an append-only PostgreSQL table with a hash chain:
- Each row carries `event_hash` (SHA-256 of the row's canonical bytes).
- Each row carries `prev_hash` pointing back to the previous row in the **same `(tenant_id, chain_shard)` chain**.
- The chain is sharded into 16 partitions (`chain_shard 0..15`) for parallel writer throughput.

### The trap

A naïve auditor will write:

```sql
SELECT event_hash, prev_hash
FROM audit_logs
WHERE tenant_id = $1 AND chain_shard = $2
ORDER BY created_at ASC;     -- ⚠️ WRONG
```

This produces ~10% false-positive "chain breaks" because:
- `created_at` is set client-side via SQLAlchemy default, NOT by the database.
- Two writers a few microseconds apart can produce rows whose `created_at` values don't match the actual INSERT order.
- The chain was BUILT in INSERT order, so `ORDER BY created_at` can swap adjacent rows.

### The canonical chain walk

Use `chain_sequence` (added in migration `z1a2b3c4d5e6` on 2026-06-18). It's a `BIGINT GENERATED BY DEFAULT AS IDENTITY` column — Postgres assigns it at INSERT, monotonically per shard, no ties possible.

```sql
SELECT event_hash, prev_hash, chain_sequence
FROM audit_logs
WHERE tenant_id = $1 AND chain_shard = $2
ORDER BY chain_sequence ASC;     -- ✓ canonical
```

For rows inserted before the migration, `chain_sequence` is NULL. Fall back to:

```sql
-- Legacy rows only
ORDER BY chain_sequence ASC NULLS LAST, created_at ASC, id ASC;
```

### The verification algorithm (pseudocode)

```python
async def verify_chain(tenant_id, shard, conn):
    rows = await conn.fetch("""
        SELECT event_hash, prev_hash, chain_sequence
        FROM audit_logs
        WHERE tenant_id = $1 AND chain_shard = $2
        ORDER BY chain_sequence ASC NULLS LAST, created_at ASC, id ASC
    """, tenant_id, shard)

    prev = None
    for r in rows:
        if prev is not None and r["prev_hash"] != prev:
            return False, r["chain_sequence"]
        prev = r["event_hash"]
    return True, None
```

`aegis-verify --chain-only --tenant-id <uuid> --shard <0-15>` runs this for you.

---

## 4. Subscribing to live decision events (SSE)

`GET /events/stream` is a long-lived Server-Sent Events stream of every governance decision for the authenticated tenant.

**Auth:** session cookie (browser users) or `?token=<acp_emp_*>` query string (out-of-band consumers).

**Event types:**
- `policy_decision` — every `/execute` outcome (allow/deny/escalate)
- `llm_proxy_call` — every `/v1/messages` outcome
- `incident_created` — when policy_deny crosses the incident threshold
- `incident_resolved`
- `approval_pending` — when ESCALATE → human approval inbox
- `approval_resolved`
- `kill_switch_engaged` / `kill_switch_released`
- heartbeat every 15 s

The stream is gated by tenant — you'll never see another tenant's events.

---

## 5. Public AEVF transparency log (anonymous verification)

`s3://aegis-public-roots-628478946931/` is an unauthenticated, publicly readable S3 bucket containing:
- `keys/<sha256-fingerprint>.pem` — ed25519 public keys used to sign daily Merkle roots
- `roots/<tenant_uuid>/<YYYY-MM-DD>.json` — per-tenant signed daily root payload

Any third party can:
1. Download a published root for a given tenant + date.
2. Verify the ed25519 signature using the public key at `keys/<fingerprint>.pem`.
3. Walk `prev_root_hash` backward to detect retroactive tampering.

```bash
pip install aegis-aevf
aws s3 cp --no-sign-request s3://aegis-public-roots-628478946931/roots/<uuid>/2026-06-18.json /tmp/root.json
aegis-verify --root /tmp/root.json --verbose
```

Even if the root-signing key is later compromised, any customer who archived an earlier root can detect the break the moment the chain is rewritten.

---

## 6. Rate limits

| Path | Limit | Window | Surfacing |
|---|---|---|---|
| `/execute` | 100 req/s per tenant | rolling 1 s | 429 + `Retry-After` |
| Per-IP 401 (auth-fail) | 60 / min | rolling 1 min | 429 + `Retry-After` + `WWW-Authenticate: Bearer realm="rate_limited"` |
| Public health endpoints | 30 / s per IP | rolling 1 s | 429 |
| Anthropic upstream (Path B) | per-org Anthropic-imposed | their window | **Wrapped** (since B-006 closure) in `{success:false, error, meta:{code, upstream:"anthropic", upstream_error_type, upstream_body}}` |

If you see persistent 429s, contact us — we can raise the per-tenant cap.

---

## 7. SDK contract — the uniform error shape

After B-006 closure (2026-06-18), every Aegis non-2xx response shares this shape:

```json
{
  "success": false,
  "data": null,
  "error": "<human-readable summary>",
  "meta": {
    "code": <HTTP status code>,
    "upstream": "<upstream service name>",
    "upstream_error_type": "<vendor-specific error kind>",
    "upstream_body": <raw upstream JSON>,
    "decision": "<aegis decision tag if applicable>",
    "reject_reason": "<aegis reject reason if applicable>",
    "findings": ["<canonical-finding-vocab>"],
    "risk_score": <0-100 if available>,
    "policy_id": "<policy that fired>"
  }
}
```

Old Path B clients that grep'd Anthropic's raw `{"type":"error","error":{...}}` shape now find that body under `meta.upstream_body`.

---

*Last updated: 2026-06-18. See `during-testing.md` for the audit that triggered these docs and `validation-report.md` for the full engagement report.*
