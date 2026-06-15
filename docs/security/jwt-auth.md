# JWT Authentication

*Every authenticated request in Aegis carries a JWT. This page documents how the token is issued, what is in it, how the gateway validates it, how revocation works, how the in-process LRU plus Redis cache reduce hot-path cost, and what a JWT does NOT carry.*

## Issuance

Source: `services/identity/token_service.py::TokenService.issue`.

Tokens are minted only by the identity service. The gateway is not authorized to mint tokens; downstream services trust the gateway's `X-Internal-Secret` plus the JWT.

Token contents:

```json
{
  "sub":        "<user_id or agent_id>",
  "tenant_id":  "<uuid>",
  "org_id":     "<uuid>",
  "role":       "ADMIN | SECURITY | AUDITOR | VIEWER | agent",
  "typ":        "ACP_ACCESS",
  "jti":        "<uuid>",
  "iat":        <unix epoch>,
  "exp":        <unix epoch>,
  "user_id":    "<uuid>"     // user tokens
  "agent_id":   "<uuid>",    // agent tokens
  "agent_status":  "active", // agent tokens
  "permissions":   [ ... ]   // agent tokens — embedded so the gateway doesn't call Registry on the hot path
}
```

Algorithm: HS256, signed with `JWT_SECRET_KEY` (an env var; never on disk).
TTL: 15 minutes by default (`JWT_EXPIRY_MINUTES`).

Agent tokens embed the permission list and the agent status at issuance so the gateway does not need to call Registry on every `/execute`. The trade-off is that a token issued before a permission revoke retains the old permissions for the rest of its TTL — partially mitigated by the per-JTI revocation path (below).

## Validation on the gateway hot path

Source: `services/gateway/_mw_auth.py::_AuthMixin._authenticate` plus `services/gateway/auth.py::TokenValidator`.

The validator runs at stage 1 of every authenticated request. Three checks in order:

1. **Signature and expiry**. Decode with HS256, check `exp` against `time.time()`. Failure → 401, increment per-IP `acp:auth_fail:{ip}` with 5-minute TTL.
2. **Revocation**. Look up `acp:revoked_jti:{jti}` and `acp:revoked_tokens:{sha256(token)}`. A hit on either → 401.
3. **Active-key cross-check**. The validator confirms an `Identity active_key` row exists in Redis. This stops a stolen `JWT_SECRET_KEY` from minting indefinite tokens — a forged token would not have a matching Identity active_key entry.

Source for the C-5 mitigation: `services/gateway/auth.py:12`.

## Caching to keep stage 1 fast

JWT decode at full concurrency adds latency. The validator uses a two-layer cache:

### Layer 1 — in-process LRU

Source: `services/gateway/auth.py::_LocalTokenLRU`.

- Threadsafe `OrderedDict` with TTL eviction.
- Defaults: 60-second TTL, 10,000 entries.
- Hit/miss counters surfaced via Prometheus so dashboards can prove the cache is hot.
- Returns a *copy* of the cached payload so a caller mutation cannot poison the cache.

The LRU sits in front of the Redis cache so the hot path can answer "is this token valid?" without a network round-trip when the same JWT comes through twice in 60 seconds.

### Layer 2 — Redis validation cache

Key: `acp:token_validation:{sha256(token)}`.

When the LRU misses, the validator checks Redis. If present, the decoded payload is returned and the LRU is warmed. Redis TTL matches the token expiry so the entry vanishes when the token does.

The Redis cache is shared across gateway workers and across both EC2 hosts in the prod-ha ASG via the ElastiCache replication group `acp-prodha-redis`. A token validated by one worker on one host is cached for every other worker on either host — keeps the cache hit rate high regardless of which ASG member handles the next call.

## Replay protection

Source: `services/gateway/_mw_auth.py:170-200`.

Only for `/execute` paths:

1. The validator extracts `jti` from the JWT.
2. `SETNX acp:jti_last_used:{jti} <timestamp>` with 1-second TTL.
3. If the SETNX returns 0, the JTI was used in the last 1 second.
4. If the cached timestamp is within 1 millisecond, the request is rejected with 429 `Too many requests: burst replay detected`.

The 1-millisecond burst window is tight on purpose. Legitimate clients do not reuse a JTI in 1 ms; an attacker replaying a stolen request often does.

## Revocation

Two paths.

### Revoke by JTI

`POST /auth/revoke` with the JTI:

1. Identity service inserts `acp:revoked_jti:{jti}` with TTL = remaining JWT expiry.
2. The gateway broadcasts the JTI to invalidate the in-process LRU on every worker.
3. Subsequent validation attempts for this JTI return 401.

### Revoke by token hash

`POST /auth/revoke` with the token:

1. The token is SHA-256 hashed.
2. The hash is added to `acp:revoked_tokens:{sha256}` with TTL = remaining expiry.
3. Validation hits the SISMEMBER and 401s.

Both paths produce an audit row `action="token_revoked"`.

## Role-to-permissions mapping

Source: `services/gateway/_mw_auth.py:151-158`.

After validation, the gateway maps `role` to a permission set:

```python
permissions_map = {
    "ADMIN":    ["*"],
    "SECURITY": ["kill_switch", "view_risk", "execute_agent"],
    "AUDITOR":  ["view_risk", "view_audit"],
    "VIEWER":   ["view_risk"],
    "agent":    ["execute_agent"],
}
```

The mapping drives the write-path enforcement at stage 1. See [RBAC Roles](rbac-roles.md).

## What a JWT does NOT carry

Two pieces of authority are deliberately NOT in the JWT:

1. **Per-agent tool permissions** (for user tokens). User tokens contain `role`; tool grants live in the `acp_registry.permissions` table and are read live on every `/execute`. A stale user token cannot use revoked grants.
2. **Kill switch state**. The kill switch lives in Redis and is checked at stage 0, not at JWT-mint time. A user with a fresh JWT still gets a 403 if the tenant is killed after the token was minted.

This is intentional: JWT contents do not change once issued. Anything that needs to change between issuance and the next request is checked at request time against fresh state.

## Tenant binding

The X-Tenant-ID header is mandatory on every authenticated request. The gateway:

1. Extracts `tenant_id` from the JWT claim.
2. Compares it to the `X-Tenant-ID` header.
3. Mismatch → 401 `tenant_mismatch`.

There is no path by which a caller can claim authority for a tenant other than the one in their JWT. See [Multi-Tenancy](../architecture/multi-tenancy.md) for the deeper model.

## Failure-counter rate limiting

Failed authentication attempts increment `acp:auth_fail:{ip}` with a 5-minute TTL. The rate limiter at stage 2 reads this counter and applies tighter limits to IPs with recent failures — limits the brute-force surface even before the password layer.

## When tokens expire under load

A long-running operator sometimes finds a 401 mid-session. The pattern is:

- Browser cookies are 24-hour but JWTs are 15-minute by default.
- The UI auto-refreshes through `/auth/refresh` on a timer.
- A laptop suspend longer than 15 minutes invalidates the in-flight JWT; the refresh fires on resume.

For SDK callers, the SDK transparently refreshes the token on 401 if it has a refresh token. For curl callers (see the Quickstart), expect to re-run `/auth/token` once every 15 minutes.

## Common 401 causes

| Cause | Symptom | Fix |
|---|---|---|
| JWT expired (15 min) | `Invalid or expired token` | Re-mint via `/auth/token` |
| `INTERNAL_SECRET` mismatch between gateway and identity | All tokens 401 | Rebuild both services with matching env |
| Token revoked by JTI | `Invalid or expired token` even with fresh-looking exp | Mint a new token |
| Active-key cross-check failed (forged token) | 401 immediately | Confirm the user actually logged in via identity; no path exists to bypass |
| Token from another tenant | `tenant_mismatch` | Header and claim must match |
| Per-IP fail counter triggered tighter rate limit | 429 with `Retry-After` | Wait the indicated seconds |

## Next

- [Identity service](../services/identity.md) — implementation detail
- [Gateway service](../services/gateway.md) — the validator at stage 1
- [RBAC Roles](rbac-roles.md) — the role matrix this JWT carries
- [Multi-Tenancy](../architecture/multi-tenancy.md) — how the JWT's tenant_id propagates
