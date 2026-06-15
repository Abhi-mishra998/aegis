# Multi-Tenancy

*Every row in every Aegis table belongs to exactly one tenant. The path from the HTTP header `X-Tenant-ID` to the `WHERE tenant_id = ?` clause on the SQL query is enforced at four points in the request lifecycle.*

This page documents the isolation model. The goal is simple: a request authenticated for tenant A must never read or write data belonging to tenant B, regardless of what the caller asks for in headers, body, or path parameters.

## The model in one sentence

Aegis is a single-process, single-database, row-scoped multi-tenant SaaS. Every persistent record has a `tenant_id UUID NOT NULL` column, and every read or write filters on it. There is no per-tenant database, no per-tenant connection pool, and no per-tenant container.

This is the same model used by Stripe, Notion, Linear, and most B2B SaaS — chosen because the operational savings of a single fleet outweigh the cognitive cost of tenant-id discipline, provided the discipline is enforced in code rather than relying on convention.

## The four enforcement points

A tenant identifier flows through every request. The four points where it is checked:

1. **JWT claim** — every JWT carries `tenant_id` in its payload. Issued only by the identity service at `/auth/login`.
2. **Request header** — `X-Tenant-ID` is mandatory on every request after login. The gateway middleware extracts it and compares to the JWT claim.
3. **Internal service header** — when the gateway calls a downstream service, `X-Tenant-ID` is forwarded explicitly via `services/gateway/main.py::_internal_headers`.
4. **SQL filter** — every database query filters on `WHERE tenant_id = :t` where `:t` comes from a FastAPI `Depends(get_tenant_id)` that reads from the request scope.

A request that fails any of these is denied. A request that succeeds them all is mathematically constrained to operate on rows where `tenant_id` matches what the JWT said.

## Step by step: login through SQL

### Login

A user posts `POST /auth/token` to the gateway. The gateway proxies to `services/identity/router.py::login_user`. The identity service:

1. Looks up the user by email.
2. Compares the supplied password to the bcrypt hash on the row.
3. Reads `tenant_id`, `org_id`, and `role` from the user row.
4. Mints a JWT with those values in the claims:

   ```json
   {
     "sub": "<user_id>",
     "tenant_id": "...",
     "org_id": "...",
     "role": "ADMIN",
     "typ": "ACP_ACCESS",
     "jti": "...",
     "iat": ...,
     "exp": ...
   }
   ```

5. Returns the JWT to the client.

A pre-login `X-Tenant-ID` header is required because the user table is shared — the same email can theoretically exist in multiple tenants (we constrain it to be unique today, but the header still acts as a defense-in-depth check). On login the identity service verifies `user.tenant_id == request_header_tenant_id` and rejects with `401 Invalid credentials or tenant mismatch` if they differ.

### Authenticated request

The client sends `POST /execute` with `Authorization: Bearer <jwt>` and `X-Tenant-ID: <uuid>`. The gateway middleware at stage 1:

1. Validates the JWT signature and expiry.
2. Extracts `tenant_id` from the JWT claim.
3. Compares the JWT claim to the `X-Tenant-ID` header. If they differ, the request is rejected with `401 tenant_mismatch`. There is no path where a caller can claim authority for a tenant other than the one in their JWT.
4. Writes both into `request.state.tenant_id` and `request.state.tenant_id_from_header`.

### Internal service-to-service calls

When the gateway calls Policy, Behavior, Decision, Audit, etc., it does NOT trust the receiving service to look up the tenant. It forwards `X-Tenant-ID` explicitly:

```python
def _internal_headers(request: Request | None = None) -> dict[str, str]:
    headers = {"X-Internal-Secret": settings.INTERNAL_SECRET}
    if request is not None:
        for h in ("X-Tenant-ID", "X-Agent-ID", "Authorization", "X-Request-ID"):
            val = request.headers.get(h)
            if val:
                headers[h] = val
        if "X-Tenant-ID" not in headers and request.state.tenant_id is not None:
            headers["X-Tenant-ID"] = str(request.state.tenant_id)
    return headers
```

The receiving service has its own `Depends(get_tenant_id)` that reads from the header and rejects with 400 if missing. The internal secret check ensures only the gateway can issue these calls.

### SQL filtering

Every FastAPI route on every service uses one of two FastAPI dependencies:

- `services/{svc}/dependencies.py::get_tenant_id` — returns `uuid.UUID(request.headers["X-Tenant-ID"])` and raises 400 if missing or unparseable.
- `services/{svc}/dependencies.py::get_authenticated_tenant` — the same, but also requires a valid JWT.

The `tenant_id` returned by these dependencies is the only source for the SQL filter. Routes look like:

```python
@router.get("/agents")
async def list_agents(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = select(Agent).where(Agent.tenant_id == tenant_id, Agent.deleted_at.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=...)
```

No route accepts a `tenant_id` body parameter or query parameter that gets used in the `WHERE` clause. If such a parameter were ever added, the `tenant_id` from the header would still take precedence — but a code review would catch and reject the parameter before merge.

## What stops cross-tenant access

A summary of every check a malicious or buggy caller has to bypass to read data from a tenant they don't belong to:

| Check | Where | What happens on failure |
|---|---|---|
| JWT signature | Stage 1 — `services/gateway/auth.py::TokenValidator.validate` | 401 immediately; the token cannot have been forged |
| JWT expiry | Stage 1 — same | 401; tokens last 15 minutes |
| JWT revocation | Stage 1 — Redis lookup | 401; revoked tokens stay revoked until expiry |
| Header-claim match | Stage 1 — `request.state.tenant_id` vs header | 401 `tenant_mismatch` |
| Role check on writes | Stage 1 — `permissions_map` | 403 `Write operations require ADMIN or SECURITY role` |
| Internal-secret check | Receiving service — `verify_internal_secret` dependency | 401 from the downstream service; the gateway never bypasses this check |
| SQL `WHERE tenant_id =` | Every query | Wrong tenant_id → zero rows returned, never the wrong tenant's rows |

For a row from tenant B to surface in tenant A's response, all seven checks must fail. The first three are cryptographic; the next four are application logic.

## What the JWT does NOT carry

Two pieces of authority are deliberately NOT in the JWT:

- **Per-agent permissions** — the agent's allowed tools live in the `acp_registry.permissions` table and are read live on every `/execute`. Stale JWTs cannot use revoked tool permissions.
- **Kill switch state** — the kill switch lives in Redis and is checked at stage 0, not at JWT-mint time. A user with a fresh JWT still gets a 403 if the tenant is killed.

This is intentional: JWT contents do not change once issued. Anything that needs to change between issuance and the next request is checked at request time against fresh state.

## Edge cases

### A user belonging to multiple tenants

Today, one email maps to one user row, which has one `tenant_id`. A future enhancement would require either issuing a separate user row per tenant or storing a list of tenants in the user row plus an explicit tenant-select step at login. The architecture supports both; only the chosen path needs implementation.

### Cross-tenant access by intent

Some tenants legitimately need to read each other's data — typically managed service providers acting on a client's behalf. This goes through the **autonomy contract** mechanism (`services/autonomy/`), not through bypassing the tenant filter. A contract declares which source-tenant agents may invoke which destination-tenant tools, with time windows and audit requirements. The contract is consulted at stage 7 of the gateway pipeline and explicitly approved every call.

### Admin-only views across all tenants

Aegis includes a small `/admin/tenants` endpoint for the platform operator. It is gated by `ADMIN` role AND a global flag `IS_PLATFORM_ADMIN` on the user row. The default admin in any tenant does NOT have this flag — they manage their own tenant only. The platform-admin path uses a separate non-tenant-scoped query and is audited as a `platform_admin_*` action.

### The "default" tenant in demos

The public demo at `https://ha.aegisagent.in` uses the canonical default tenant UUID `00000000-0000-0000-0000-000000000001`. This is just a UUID like any other from the platform's perspective — it has no special privileges. The choice is purely a convention for demos and tests so credentials and curl examples can be shared.

## Quotas and rate limits per tenant

Tenant-level enforcement is co-located with the tenant row:

| Column on `acp_identity.tenants` | Enforced at | Default |
|---|---|---|
| `requests_per_second` | Stage 2 rate limit Lua | 50 |
| `burst` | Stage 2 | 100 |
| `daily_request_cap` | Stage 2 (separate counter `acp:tenant_daily_req:{tenant}:{YYYYMMDD}`) | 10,000,000 |
| `monthly_request_cap` | Stage 2 (monthly counter, with 80% warning emit) | 100,000,000 |
| `daily_inference_cost_cap_usd` | Stage 2 (cost accumulator) | 1,000 |
| `degraded_mode_policy` | Stage 5 — `block_high_risk` \| `block_all` \| `allow_with_audit` | `block_high_risk` |
| `rpm_limit` | Stage 2 (legacy, mostly superseded by RPS) | 0 (uncapped) |

Per-agent caps override or extend these via Redis keys `acp:agent_cost_cap:{agent_id}` and the per-agent fields on the agent row. Per-agent caps cannot loosen tenant caps — they can only be tighter.

## What "tenant isolation" does NOT cover

Multi-tenancy in Aegis isolates **rows**. It does not isolate:

- **CPU and memory** — a noisy tenant can degrade latency for a quiet tenant if the noisy tenant uses up gateway worker concurrency. The fairness harness at `tests/load/fairness.py` measures this; the current SLO budgets a quiet-tenant p99 degradation of no more than 20% under burst. See `docs/soak_runbook.md`.
- **Postgres connections** — all tenants share the PgBouncer pool. The pool is sized to handle the worst expected concurrent demand across tenants.
- **Redis memory** — all tenants share the ElastiCache cluster. Memory is monitored; the cluster is sized for headroom.

True per-tenant capacity isolation requires deploying a separate stack per tenant. That is a supported but customer-funded deployment model and is out of scope for the public demo.

## What an auditor should verify

For a SOC 2 or EU AI Act assessor, the things to inspect to confirm tenant isolation:

1. Every `models.py` file has `tenant_id: Mapped[uuid.UUID] = mapped_column(... nullable=False, index=True)` on every table. Run: `grep -L "tenant_id" services/*/models.py` — should return nothing.
2. Every router file uses `Depends(get_tenant_id)`. Run: `grep -L "get_tenant_id\|get_authenticated_tenant" services/*/router.py` — should return only routes that are themselves tenant-scoping endpoints (login, register).
3. The gateway forwards `X-Tenant-ID` on every internal call. Inspect `services/gateway/main.py::_internal_headers`.
4. The CI test suite includes a cross-tenant smoke. Run: `pytest tests/security/test_cross_tenant.py`.

## Next

- [System Overview](system-overview.md) — the services that participate in this model.
- [10-Stage Pipeline](10-stage-pipeline.md) — where the tenant check happens in the middleware order.
- [Data Model](data-model.md) — every table with its tenant_id column.
- [RBAC](../security/rbac-roles.md) — the role matrix that the write-path enforcement checks against.
