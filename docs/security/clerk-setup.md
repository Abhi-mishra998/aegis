# Clerk Authentication

*Aegis runs a dual-path token validator: Clerk-issued RS256 JWTs for browser SaaS users, and legacy HS256 self-issued JWTs for SDK / curl / server-to-server callers. This page documents the dispatcher, the `aegis_org_id == aegis_tenant_id` invariant enforced at three layers, the algorithm gate that defeats HS256-with-Clerk-iss downgrade attempts, and the operational knobs.*

## Why two paths

Aegis is a single platform but two trust roots:

| Token issuer | Algorithm | Carries | Caller |
|---|---|---|---|
| Clerk (`https://clerk.aegisagent.in`) | RS256 (JWKS) | `aegis_tenant_id`, `aegis_org_id`, `aegis_role`, `email` | Humans through the React UI |
| Aegis identity service | HS256 (`JWT_SECRET_KEY`) | `tenant_id`, `org_id`, `role`, `permissions[]` | SDKs (`aegis-sdk==1.1.0`), curl, agents |

Both paths emit identical canonical payloads downstream so the rest of the gateway, the decision engine, OPA, audit, and the trust layer never need to know which validator ran.

Operator switch: `ACP_AUTH_PROVIDER` in `services/gateway`:

| Value | Behavior |
|---|---|
| `legacy` | Only HS256 self-issued accepted (default for SDK-only deployments). |
| `clerk` | Only RS256 Clerk-issued accepted. |
| `both` | Accept either — the gateway picks the validator by token shape (the default for SaaS production). |

## The dispatcher

Source: `services/gateway/auth.py::LocalTokenValidator.validate` (lines 231-263).

```python
# Cache miss: dispatch by provider + token shape.
auth_provider = settings.ACP_AUTH_PROVIDER
is_clerk = (
    auth_provider in ("clerk", "both")
    and looks_like_clerk_token(token)
)

if is_clerk:
    clerk_validator = get_clerk_validator(self._redis)
    payload = await clerk_validator.validate(token)
elif auth_provider in ("legacy", "both"):
    payload = self._validate_signature(token)
    # ... + Identity active-key cross-check
else:
    raise ACPAuthError(...)
```

`looks_like_clerk_token` lives at `sdk/common/clerk_auth.py:325-338`:

```python
def looks_like_clerk_token(token: str) -> bool:
    """Cheap heuristic — does the unverified payload carry our Clerk issuer?"""
    if not token or not settings.CLERK_ISSUER:
        return False
    try:
        unverified = jwt.get_unverified_claims(token)
    except JWTError:
        return False
    iss = unverified.get("iss")
    return iss == settings.CLERK_ISSUER
```

The heuristic deliberately reads only the unverified payload — it is cheap (one base64 decode, no JWKS round-trip) and it is safe because the actual signature check happens on the dispatched path.

## The algorithm gate (downstream-attack guard)

Source: `services/gateway/auth.py:238-255`.

A naive dispatcher trusts the `iss` claim to pick the validator. That is unsafe: an attacker can mint an HS256 token signed with the leaked `JWT_SECRET_KEY` AND set its `iss` claim to `https://clerk.aegisagent.in`. Both validators are then reachable from the same token, and Aegis' rejection then depends on the Clerk path discovering the `kid` mismatch — which is later in the pipeline than necessary.

The hardening: before the dispatcher routes a token whose `iss` looks like Clerk, it reads `jwt.get_unverified_header(token)["alg"]` and requires it to be `RS256` or `RS512`. An HS256 token with a Clerk-shaped `iss` is rejected at the dispatch boundary with `ACPAuthError("Token alg=HS256 rejected: tokens claiming Clerk issuer must be RS256 or RS512")` before any cryptographic work runs.

Live evidence: `final-testing.md` finding L2 — *"HS256 + Clerk-iss → 401"*. The probe synthesises a token with `iss=$CLERK_ISSUER` signed by `JWT_SECRET_KEY` and confirms the gateway 401s on it.

Test: `tests/test_clerk_validator.py::test_hs256_token_with_clerk_iss_is_rejected_at_dispatch`. The same file's 14 cases all pass.

## The `aegis_org_id == aegis_tenant_id` invariant

Aegis is a strict-SaaS platform: every user belongs to exactly one organization, and that organization is also that user's tenant. The invariant `aegis_org_id == aegis_tenant_id` is enforced at **three layers**, deliberately redundant so no single bug can break tenant isolation.

### Layer 1 — Clerk webhook receiver

Source: `services/identity/webhooks_clerk.py::_handle_organization_created`.

When Clerk fires `organization.created`, the receiver:

1. Creates an Aegis organization row with `id = tenant_id` (the row's tenant_id and its primary key are equal).
2. Writes the `aegis_org_id` and `aegis_tenant_id` identifiers (equal by construction) into the Clerk organization's `public_metadata` via the Clerk Backend API.
3. SADDs the `clerk_org_id → aegis_tenant_id` mapping into Redis at `acp:clerk:org-tenant:{clerk_org_id}` for the JWT-canonicalize fallback path.

When Clerk later mints a JWT with the `aegis` template, the template reads from `public_metadata` and surfaces both identifiers — already equal at the source.

### Layer 2 — JWT canonicalize

Source: `sdk/common/clerk_auth.py::ClerkTokenValidator._canonicalize` and `services/gateway/auth.py::_validate_signature`.

For Clerk tokens, `_canonicalize` reads `aegis_tenant_id` and `aegis_org_id` from the claims and re-asserts equality before returning the canonical payload. If `aegis_tenant_id` is empty (default Clerk JWT, customer not yet using the `aegis` template), the canonicalize step falls back to the Redis `clerk_org_id → aegis_tenant_id` map written by the webhook receiver.

For HS256 self-issued tokens, `_validate_signature` runs:

```python
org_id_str = payload.get("org_id")
tenant_id_str = payload.get("tenant_id")
if org_id_str and tenant_id_str:
    from sdk.common.invariants import (
        InvariantViolation,
        assert_org_consistency,
    )
    try:
        assert_org_consistency(
            uuid.UUID(org_id_str),
            uuid.UUID(tenant_id_str),
            "gateway token validation",
        )
    except InvariantViolation as e:
        raise ACPAuthError(f"System Integrity Error: {e}")
```

`assert_org_consistency` lives at `sdk/common/invariants.py:76-90` and raises `InvariantViolation` if `org_id != tenant_id`. Both paths converge here.

### Layer 3 — DB CHECK constraint

Source: `services/identity/alembic/versions/a1b2c3d4e5f6_add_check_constraint_org_tenant_match.py`.

The Postgres tables enforce the invariant at the storage layer:

```python
op.create_check_constraint(
    "ck_users_org_tenant_match",
    "users",
    sa.column("org_id") == sa.column("tenant_id"),
)
op.create_check_constraint(
    "ck_agent_creds_org_tenant_match",
    "agent_credentials",
    sa.column("org_id") == sa.column("tenant_id"),
)
```

Any INSERT or UPDATE — from application code, raw SQL, a manual psql session, or a botched migration — that tries to set `org_id != tenant_id` on a user or agent credential row is rejected by Postgres before the transaction commits.

The combination: a malicious or buggy actor would have to defeat the Clerk webhook ingest, the JWT canonicalize step, AND the Postgres CHECK constraint to land a cross-tenant row.

## Provisioning a tenant from a Clerk JWT

Sign-up uses Clerk-hosted UI; the **first** authenticated request from a new tenant must hit the gateway-proxied identity endpoint:

```bash
curl -sS -X POST https://aegisagent.in/auth/clerk/provision \
  -H "Authorization: Bearer <clerk-jwt>"
```

Source: `services/identity/router.py::provision_from_clerk`.

What it does (idempotent — safe to call repeatedly):

1. Validates the Clerk JWT via JWKS (same `ClerkTokenValidator` as the gateway).
2. Reads `sub` and the native `org_id` claim.
3. Fetches the org name + slug from the Clerk Backend API (falls back to the org_id if the call fails).
4. Creates the Aegis organization, tenant, and user rows — `org.id == tenant.tenant_id == user.org_id == user.tenant_id`.
5. Writes the `aegis_org_id` + `aegis_tenant_id` back into Clerk org `public_metadata` so the JWT template surfaces them on subsequent logins.
6. Returns the provisioned identifiers + the shadow-mode expiry.

The same provisioning logic runs from the Clerk webhook (`organization.created`, `user.created`) so even users who skip the synchronous call get provisioned eventually.

## Required configuration

In `services/gateway/.env` and `services/identity/.env`:

| Variable | Example | Purpose |
|---|---|---|
| `ACP_AUTH_PROVIDER` | `both` | Dispatcher behavior — see table above |
| `CLERK_PUBLISHABLE_KEY` | `pk_test_…` | UI loads via `VITE_CLERK_PUBLISHABLE_KEY` |
| `CLERK_SECRET_KEY` | `sk_test_…` | Backend uses for Backend API calls (org provision, metadata write) |
| `CLERK_FRONTEND_API` | `https://clerk.aegisagent.in` | Base for JWKS + issuer |
| `CLERK_JWKS_URL` | `https://clerk.aegisagent.in/.well-known/jwks.json` | RS256 signature verification |
| `CLERK_ISSUER` | `https://clerk.aegisagent.in` | Expected `iss` claim — also used by `looks_like_clerk_token` |
| `CLERK_WEBHOOK_SECRET` | `whsec_…` | Svix signing secret for inbound webhooks |
| `CLERK_JWT_TEMPLATE` | `aegis` | Template name the frontend asks for: `getToken({template: "aegis"})` |
| `CLERK_JWKS_CACHE_SECONDS` | `3600` | JWKS cache TTL; Clerk rotates infrequently so 1h is a safe default |

## The Clerk JWT template

In the Clerk dashboard, the `aegis` JWT template emits:

```json
{
  "sub":              "<clerk user id>",
  "iss":              "https://clerk.aegisagent.in",
  "aegis_tenant_id":  "{{org.public_metadata.aegis_tenant_id}}",
  "aegis_org_id":     "{{org.public_metadata.aegis_org_id}}",
  "aegis_role":       "{{org.public_metadata.aegis_role || \"org:owner\"}}",
  "email":            "{{user.primary_email_address}}",
  "exp":              "<unix epoch>"
}
```

Both `aegis_tenant_id` and `aegis_org_id` come from the same `public_metadata` source the webhook receiver wrote, so they are equal at the JWT layer by construction. The JWT canonicalize step re-checks the equality anyway.

## Role mapping

Clerk emits roles in the `org:foo` shape; Aegis collapses them to the canonical UPPER_SNAKE_CASE vocabulary via `normalize_clerk_role` in `sdk/common/clerk_auth.py`:

| Clerk role | Aegis role | Permissions |
|---|---|---|
| `org:owner` | `OWNER` | Equivalent to ADMIN with billing rights |
| `org:admin` | `ADMIN` | Full read + write |
| `org:security_analyst` | `SECURITY_ANALYST` | Kill switch + audit + risk views |
| `org:developer` | `DEVELOPER` | Read + agent provisioning + policy sim |
| `org:read_only` | `READ_ONLY` | Risk + audit views, no write |

See [RBAC Roles](rbac-roles.md) for the write-path enforcement matrix.

## What the Clerk path canonicalizes

The downstream gateway middleware reads `request.state.tenant_id`, `request.state.role`, etc. The Clerk validator emits a payload shape **identical** to the HS256 path:

```json
{
  "sub":             "<clerk user_id>",
  "tenant_id":       "<aegis_tenant_id>",
  "org_id":          "<aegis_org_id>",
  "role":            "<canonical Aegis role>",
  "email":           "<email>",
  "exp":             <unix epoch>,
  "iat":             <unix epoch>,
  "jti":             "clerk-<sha256(sub:iat)[:32]>",
  "clerk_user_id":   "<clerk user_id>",
  "auth_provider":   "clerk"
}
```

The `jti` is synthesized from `sha256(sub:iat)` because Clerk does not mint `jti` claims by default. This jti is what the in-process LRU + Redis validation cache key on, and what the revocation listener invalidates.

## Caching and revocation

Caching is shared with the HS256 path: same in-process LRU (60s TTL, 10k entries) and the same Redis validation cache at `acp:token_validation:{sha256(token)}`. See [JWT Authentication](jwt-auth.md) for the deep dive on the two-tier cache.

Revocation differs:

| Path | Revocation surface |
|---|---|
| Clerk (RS256) | Revoke the Clerk session via Clerk dashboard / Backend API. The gateway cache holds for up to 60s; the in-process LRU is invalidated on the next request whose underlying Clerk session is gone (the validator gets back a 401 on the JWKS path). |
| Legacy (HS256) | `POST /auth/revoke` with the `jti` or token. SADDs into `acp:revoked_jti:{jti}`, broadcasts a Pub/Sub invalidate on `acp:token:revocations` so every gateway worker drops its LRU entry within milliseconds. |

For the SDK (HS256) revocation flow including the brand-new "kill an employee API key" path, see [API Keys](api-keys.md).

## Common 401 causes on the Clerk path

| Cause | Symptom | Fix |
|---|---|---|
| Clerk JWT has no `aegis_tenant_id` claim AND no `clerk_org_id → aegis_tenant_id` map | 401 `Token missing tenant binding` | Sign in to an organization (or create one) before the first request; run `/auth/clerk/provision` if the webhook receiver hasn't reached the new org yet |
| Clerk JWT carries `iss` that doesn't match `CLERK_ISSUER` | 401 `Clerk token signature invalid: iss mismatch` | Confirm the env var matches the Clerk frontend API base URL |
| Clerk JWT signed with a `kid` not in the cached JWKS | 401 after a single refresh attempt | Probably mid-key-rotation; `CLERK_JWKS_CACHE_SECONDS` defaults to 1h so the next refresh fetches the new key |
| HS256 token with `iss=https://clerk.aegisagent.in` (downgrade attempt) | 401 at the dispatch boundary | Expected behavior; see the algorithm gate section above |
| `ACP_AUTH_PROVIDER=legacy` but client sends a Clerk token | 401 `No validator enabled for this token` | Change the provider to `clerk` or `both` |

## Next

- [JWT Authentication](jwt-auth.md) — the HS256 path, caching, and replay protection
- [API Keys](api-keys.md) — the per-employee SDK credential, daily/monthly USD budgets, and the revoke → next-call-401 flow
- [RBAC Roles](rbac-roles.md) — what the role embedded in the canonicalized payload buys you
- [Multi-Tenancy](../architecture/multi-tenancy.md) — how `tenant_id` propagates downstream
