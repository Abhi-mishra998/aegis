# API Keys

*Aegis issues `acp_emp_<digits>` employee-scoped API keys for SDK callers. Each key is bound to a single employee, carries a daily and monthly USD budget, and is revocable in under one second — no 60-second cache window. This page documents the shape, the budget enforcement, the per-employee attribution, and the revocation flow.*

## Shape

```
acp_emp_<digits>
```

- `acp_emp_` prefix identifies the key as employee-scoped (programmatic, single-human-owner, budgeted).
- `<digits>` is a 32+ character cryptographically random suffix issued by `secrets.token_urlsafe` then digit-canonicalized.
- The key is shown **exactly once** at creation; only the bcrypt hash is persisted in `acp_api.api_keys.key_hash`.
- The first 8 characters become `key_prefix` and are displayed in the UI for identification.

Tenant-scoped legacy keys (`acp_<digits>`, no `emp_` segment) remain accepted for back-compat — the gateway falls back to tenant-level enforcement when the key is not bound to an employee.

## Per-employee attribution

Every audit row that records a tool execution carries the resolved `employee_id` and `key_id` so a per-employee usage and risk view is available at:

- **UI → Settings → User Management** — total spend per employee, risk score, last-used timestamp.
- **`GET /api-keys`** — list every key for the tenant with its bound employee.
- **`GET /audit/logs?employee_id=<uuid>`** — every action that key authorized.

The attribution is enforced at validation time: the gateway resolves the employee from the key, stamps `request.state.employee_id` onto the request, and the audit writer SQL writes it into the `acp_audit.audit_logs` row. There is no path by which an employee can act anonymously through a tenant-scoped fallback once the key is `acp_emp_*`-shaped.

## Daily and monthly USD budgets

Each employee key carries two budget knobs persisted on the `acp_identity.tenants` row (per-employee override available in `acp:agent_cost_cap:{employee_id}` Redis key for hot-path tuning):

| Knob | Default | Enforced where |
|---|---|---|
| Daily USD cap | $5 | `services/gateway/_mw_cost.py` — UTC-day rolling counter |
| Monthly USD cap | $100 | UTC-month rolling counter, refreshes 1st of month UTC |

Behavior on cap-exceeded:

1. The request returns 429 `inference_cost_cap_exceeded` with body `{error, limit_type: "daily" | "monthly", reset_at}`.
2. An audit row `action="inference_cost_cap_exceeded"` is written.
3. At 80% of the monthly cap, a one-shot warning fires to the tenant's `acp:billing_alerts` Redis pub/sub channel.

The budget enforcement runs at gateway middleware stage 4 (after auth + tenant binding), so a key blocked by budget still produces a fully attributed audit row.

## Creating a key

```bash
curl -sS -X POST https://aegisagent.in/api-keys \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "X-Tenant-ID: <tenant-uuid>" \
  -H "Content-Type: application/json" \
  -d '{
        "name":          "alice@acme.com — laptop SDK",
        "employee_id":   "<employee-uuid>",
        "expires_at":    null
      }'
```

Response (only time the raw key is ever returned):

```json
{
  "success": true,
  "data": {
    "id":           "<key-uuid>",
    "tenant_id":    "<tenant-uuid>",
    "name":         "alice@acme.com — laptop SDK",
    "api_key":      "acp_emp_8K3jH9xR2vN7pQ4mL1wB6tY5cF0sA8dE2",
    "key_prefix":   "acp_emp_",
    "created_at":   "2026-06-17T11:02:14.521Z",
    "expires_at":   null
  }
}
```

Roles allowed to create keys: `ADMIN`, `OWNER`, `SECURITY_ANALYST`. Other roles get 403.

## Validating on the SDK hot path

Source: `services/gateway/_mw_auth.py::_AuthMixin._validate_api_key_cached`.

Every SDK request runs:

1. Read `X-API-Key` (or `Authorization: Bearer acp_emp_…`) header.
2. SHA-256 hash the raw key to a 64-character cache key.
3. **Cache hit** (60s LRU at `acp:apikey:valid:{sha256}`): return the cached `{tenant_id, employee_id, is_active, agent_id}` payload — but only after the post-cache safety checks below.
4. **Cache miss**: call the api service's `POST /api-keys/validate`, store the response in Redis with 60s TTL, return it.

The 60-second cache exists because per-request DB hits at SDK scale were 1.4× the gateway's CPU budget. The two safety checks below close the revocation latency window the cache otherwise opens.

### Safety check 1 — `is_active: false` hard reject

Source: `services/gateway/_mw_auth.py::_validate_api_key_cached`.

Even on a cache hit, the cached payload is inspected:

```python
if cached_payload.get("is_active") is False:
    # Stale cache from before the revoke. Evict and 401.
    await self.redis.delete(cache_key)
    raise HTTPException(status_code=401, detail="API key revoked")
```

This guards the edge case where a key was DELETEd, the revoke updated the DB row but the cache hadn't yet expired. The hard reject happens before any downstream work runs.

### Safety check 2 — revoked-set membership

Source: `services/gateway/_mw_auth.py` — runs on **every** request, not just on cache misses.

A second Redis check confirms the key is not in the revoked set:

```python
if await self.redis.sismember("acp:apikey:revoked", key_id):
    await self.redis.delete(cache_key)
    raise HTTPException(status_code=401, detail="API key revoked")
```

`acp:apikey:revoked` is a global Redis SET populated by the DELETE proxy (see below). One `SISMEMBER` per request, O(1), shared across every gateway worker on every EC2 host in the prod-ha ASG.

The combination — `is_active: false` hard reject + global revoked SET — guarantees that a successful revoke takes effect on the *very next* SDK call, not 60 seconds later.

## Revoking a key

```bash
curl -sS -X DELETE https://aegisagent.in/api-keys/<key-uuid> \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "X-Tenant-ID: <tenant-uuid>"
```

Roles allowed: `ADMIN`, `OWNER`, `SECURITY_ANALYST`. Other roles get 403.

### The revocation flow

Source: `services/gateway/routers/users.py::revoke_api_key` (proxy) and `services/api/router/api_key.py::revoke_api_key` (worker).

End-to-end:

1. **Gateway proxy** at `DELETE /api-keys/{key_id}` forwards to the api service.
2. **API service** runs `APIKeyRepository.deactivate` — issues `UPDATE acp_api.api_keys SET is_active=false WHERE id=... AND tenant_id=...`.
3. **On success** (rowcount > 0), the gateway proxy SADDs the `key_id` into the global `acp:apikey:revoked` Redis set:

```python
await request.app.state.redis.sadd("acp:apikey:revoked", str(key_id))
```

4. **Response**: `{"success": true, "data": null}` with HTTP 200.

### Why a next-request 401 is guaranteed

Without the global revoked SET, the gateway's 60-second LRU at `acp:apikey:valid:{sha256(key)}` would continue to authenticate the revoked key for up to 60 seconds. With it, the very next request executes:

1. `SISMEMBER acp:apikey:revoked <key_id>` — returns `1`.
2. Gateway evicts the stale `acp:apikey:valid:*` entry.
3. Gateway raises 401.

Live evidence: `final-testing.md` finding C5 — *"DELETE 200 → POST 401 (no 60-second cache window)"*. The probe creates a key, exercises it once to warm the cache, DELETEs it, then immediately calls `POST /execute` again — observing 401 on the **very next** call, not 60 seconds later.

Tests: `tests/test_api_key_is_active.py` (9 cases) cover:

- Cache populated → revoke → next call rejected.
- Stale `is_active: true` cache payload → revoked SET check still rejects.
- Stale `is_active: false` cache payload → rejected without consulting Redis SET.
- Cache miss + DB returns `is_active: false` → rejected.
- Cache eviction happens on every rejection path (otherwise a misconfigured proxy could re-populate the cache).
- The revoked SET is global, not per-tenant — a leaked key cannot be re-authenticated by hitting a different tenant.
- Revoked SET tolerates Redis transient errors (fails closed: any `SISMEMBER` exception 401s instead of admitting the key).
- The api service `deactivate` is tenant-scoped — an attacker holding admin role on tenant B cannot revoke tenant A's key.
- The gateway records an audit row `action="api_key_revoked"` with the `key_id` and the actor's `user_id`.

All 9 tests pass.

## Listing and inspection

```bash
curl -sS https://aegisagent.in/api-keys \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "X-Tenant-ID: <tenant-uuid>"
```

Returns every **active** key for the tenant. The raw secret is never returned; only `key_prefix`, `name`, `employee_id`, `is_active`, `created_at`, `last_used_at`, `expires_at`.

Revoked keys are not returned by this endpoint (only `is_active=true` rows are selected). For audit purposes, query the audit log:

```bash
curl -sS "https://aegisagent.in/audit/logs?action=api_key_revoked&tenant_id=<tenant-uuid>" \
  -H "Authorization: Bearer <auditor-jwt>"
```

## Expiration

Keys may carry an optional `expires_at`. The validator checks this on every request (post-cache, post-revoked-SET):

```python
if api_key.expires_at and api_key.expires_at < datetime.now(tz=...):
    return None  # treated as invalid
```

An expired key auto-fails authentication; there is no need to explicitly revoke it. For belt-and-braces, the api service runs a daily sweep that flips expired rows to `is_active=false`.

## Common 401 causes

| Cause | Symptom | Fix |
|---|---|---|
| Key shape is not `acp_emp_*` or `acp_*` | 401 `Invalid API key shape` | Re-generate the key |
| Key was DELETEd | 401 `API key revoked` on the next request | Expected; rotate to a new key |
| Key expired | 401 `Invalid or expired API key` | Generate a new one with a future `expires_at` |
| Budget exceeded | 429 `inference_cost_cap_exceeded` (not 401) | Wait for cap reset or raise the budget |
| `X-Tenant-ID` header missing | 401 `tenant_mismatch` | Send the tenant_id the key is bound to |
| Redis unavailable (revoked-SET check fails) | 401 `Authentication infrastructure unavailable` | Fail-closed by design; investigate Redis health |

## SDK usage

Pin `aegis-sdk==1.1.0`. The SDK accepts the API key via the `AEGIS_API_KEY` env var or constructor arg:

```python
from aegis_sdk import AegisClient

client = AegisClient(
    base_url="https://aegisagent.in",
    api_key="acp_emp_8K3jH9xR2vN7pQ4mL1wB6tY5cF0sA8dE2",
)
result = client.execute(agent_id="...", tool="...", payload={...})
```

The SDK mints an internal JWT from the API key automatically; it handles the 401-on-revoke by raising `aegis_sdk.exceptions.APIKeyRevokedError`. The caller is expected to surface this to the human operator (the key is dead — generate a new one).

## Next

- [Clerk Authentication](clerk-setup.md) — the human-side, browser SaaS sign-in path
- [JWT Authentication](jwt-auth.md) — the underlying token validator the SDK feeds
- [RBAC Roles](rbac-roles.md) — which roles can create and revoke API keys
- [Identity service](../services/identity.md) — token issuance internals
