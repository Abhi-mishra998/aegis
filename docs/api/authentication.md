# Authentication

*Every authenticated request to Aegis carries either a `Authorization: Bearer <jwt>` header or an `acp_token` cookie. This page documents both, plus tenant binding, agent tokens, refresh, and revocation.*

## Token types

Aegis issues two kinds of JWTs from one endpoint family:

| Token | Caller | Issued by | Carries |
|---|---|---|---|
| User token | Humans (browser, curl, SDK) | `POST /auth/token` | `user_id`, `role`, `tenant_id`, `org_id` |
| Agent token | Programmatic agents | `POST /auth/agent/token` | `agent_id`, `role: "agent"`, `tenant_id`, `permissions[]` |

Both are HS256-signed with `JWT_SECRET_KEY`. Default TTL is 15 minutes (`JWT_EXPIRY_MINUTES`).

## User login

### Request

```bash
curl -sS -X POST https://ha.aegisagent.in/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <tenant-uuid>" \
  -d '{"email":"alice@acme.com","password":"<REDACTED>"}'
```

### Response

```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGc...",
    "token_type":   "bearer",
    "expires_in":   900,
    "tenant_id":    "<uuid>",
    "role":         "ADMIN"
  }
}
```

The response also sets an `acp_token` HTTPOnly cookie scoped to the platform's domain. Browsers carry the cookie automatically on subsequent requests; SDK / curl callers should use the `access_token` from the body via the `Authorization` header.

### What gets validated

`services/identity/router.py::login_user`:

1. Look up the user row by lowercased email.
2. Confirm `is_active`.
3. Verify the password with `bcrypt.checkpw` in a thread pool.
4. Confirm `user.tenant_id == request_header_tenant_id`. Mismatch → 401 `tenant_mismatch`.
5. Assert `org_id == tenant_id` invariant. NULL org_id → 500 `inconsistent account metadata`.
6. Mint the JWT.
7. Emit an audit row `action="user_login"`.

## Agent login

### Request

```bash
curl -sS -X POST https://ha.aegisagent.in/auth/agent/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <tenant-uuid>" \
  -H "X-Internal-Secret: <internal-secret>" \
  -d '{"agent_id":"<agent-uuid>","secret":"<agent-secret>"}'
```

Agent tokens require the `X-Internal-Secret` header because agent credentials are provisioned by the platform, not chosen by humans. The agent_id + secret pair is provisioned via `POST /auth/credentials` (ADMIN-gated).

### Response

Same shape as user login. The token carries `role: "agent"` and an embedded `permissions` array of `{tool_name, action}` so the gateway does not need to call Registry on every `/execute`.

## Authenticated request

### Headers

Every authenticated request requires:

```
Authorization: Bearer <access_token>
X-Tenant-ID:   <tenant-uuid>
Content-Type:  application/json    # for POST/PATCH/PUT
```

For `POST /execute`, additionally:

```
X-Agent-ID: <agent-uuid>
```

### Validation order

The gateway middleware runs the following at stage 1 (see [JWT Authentication security](../security/jwt-auth.md)):

1. **Signature and expiry.** HS256 against `JWT_SECRET_KEY`. Failure → 401.
2. **Revocation.** Look up `acp:revoked_jti:{jti}` and `acp:revoked_tokens:{sha256(token)}`. Hit → 401.
3. **Active-key cross-check.** Confirm an Identity-side `active_key` Redis entry exists. Prevents stolen `JWT_SECRET_KEY` from minting indefinite tokens.
4. **Header-claim match.** `X-Tenant-ID` header must equal the JWT's `tenant_id` claim. Mismatch → 401 `tenant_mismatch`.
5. **Role gate.** Non-GET requests require `ADMIN` or `SECURITY` role, except `/execute` for `agent` role.

The validated payload is stored on `request.state.role`, `request.state.tenant_id`, `request.state.permissions`. Downstream services read these via the forwarded headers.

## Refresh

```bash
curl -sS -X POST https://ha.aegisagent.in/auth/refresh \
  -H "Authorization: Bearer <old-token>" \
  -H "X-Tenant-ID: <tenant-uuid>"
```

Returns a new `access_token` with a fresh `jti` and reset `exp`. The old token continues to validate until its original `exp`; new requests should use the refreshed token.

SDKs handle refresh transparently on 401. Curl callers should re-login if the token has expired entirely.

## Revoke

### Revoke own token (logout)

```bash
curl -sS -X POST https://ha.aegisagent.in/auth/logout \
  -H "Authorization: Bearer <token>" \
  -H "X-Tenant-ID: <tenant-uuid>"
```

Adds the JTI to `acp:revoked_jti:{jti}` with TTL matching the remaining JWT expiry.

### Revoke someone else's token

```bash
curl -sS -X POST https://ha.aegisagent.in/auth/revoke \
  -H "Authorization: Bearer <admin-token>" \
  -H "X-Tenant-ID: <tenant-uuid>" \
  -H "Content-Type: application/json" \
  -d '{"jti":"<target-jti>"}'
```

Requires `ADMIN` or `SECURITY` role. The target token 401s on its next request.

For revocation by token hash (without the JTI in hand), include `{"token":"<the-target-token>"}` instead.

## Cookie vs Bearer

Aegis supports both auth methods on the same endpoints:

| Carrier | Set by | Read by | Use case |
|---|---|---|---|
| Cookie (`acp_token`, HTTPOnly) | `/auth/token` response | Browser auto-sends; gateway reads from `request.cookies` | Browser sessions |
| Bearer (`Authorization`) | Client-set | Gateway reads from `request.headers` | SDK / curl / server-to-server |

The gateway accepts either. If both are present, the `Authorization` header wins.

The cookie is HTTPOnly so JavaScript cannot read it (XSS-resistant). It is `Secure` in production (`ENVIRONMENT=production`) and uses `SameSite=strict`.

## SSO

When the tenant has SSO configured, users log in via the IdP rather than via local password.

```bash
# Start the flow — redirects to the IdP
curl -sS -L https://ha.aegisagent.in/auth/sso/google?tenant_id=<tenant-uuid>

# After IdP authentication, the user is redirected to the callback:
# /auth/sso/google/callback?code=...&state=...

# The callback handler exchanges the code for an ID token, maps to an
# Aegis user, and sets the acp_token cookie + redirects to the dashboard.
```

SSO providers are configured per-tenant via Settings → SSO ([SSO UI](../ui/settings/sso-settings.md)).

## Internal-secret authentication

Service-to-service calls within Aegis use a shared `INTERNAL_SECRET`:

```
X-Internal-Secret: <INTERNAL_SECRET>
```

Every downstream service verifies the header via `verify_internal_secret` dependency. The gateway is the only service that issues these calls; downstream services trust the secret implicitly.

The secret is rotated quarterly with an overlap window (`INTERNAL_SECRET_PREVIOUS`) so a partial rollout does not break inter-service calls mid-deploy.

## Tenant binding rule

The X-Tenant-ID header is **mandatory** on every authenticated request including login itself. The login path enforces `user.tenant_id == header_tenant_id`; the runtime path enforces `jwt.tenant_id == header_tenant_id`.

There is no path by which a caller can claim authority for a tenant other than the one in their JWT. See [Multi-Tenancy](../architecture/multi-tenancy.md).

## API keys (for SDK callers)

Aegis additionally supports `acp_*`-prefixed API keys for SDK integrations. The keys are bcrypt-hashed in `acp_api.api_keys`; the raw value is returned exactly once at creation.

Validation goes through `POST /api-keys/validate`:

```bash
curl -sS -X POST https://ha.aegisagent.in/api-keys/validate \
  -H "Content-Type: application/json" \
  -d '{"api_key":"acp_..."}'
```

Returns the resolved user_id and tenant_id, which the SDK uses to mint a JWT internally.

## Common 401 causes

| Cause | Symptom | Fix |
|---|---|---|
| JWT expired (15 min default) | `Invalid or expired token` | Refresh via `/auth/refresh` or re-login |
| JWT signature invalid (forgery attempt) | 401 immediately | Re-mint via `/auth/token` |
| Token revoked | 401 even with valid-looking exp | Mint a fresh token |
| `INTERNAL_SECRET` mismatch between services | Every internal call 401 | Confirm all services have the same env value |
| Header-claim mismatch | 401 `tenant_mismatch` | Send the same tenant_id as the JWT claim |
| Active-key cross-check failed (forged token) | 401 immediately | The token did not come from a real login; investigate |

## Common 403 causes

| Cause | Body | Fix |
|---|---|---|
| Role gate | `Write operations require ADMIN or SECURITY role` | Re-login as a higher role |
| Kill switch engaged | `error: "kill_switch_engaged"` | Investigate the engagement; see the [kill switch runbook](../operations/runbooks/kill-switch-engaged.md) |
| Policy denied | `error: "policy_denied"` with `rule_id` | The action matched a deny rule; verify expected behavior |
| Approval required | `error: "approval_required"` | Use the Auto Response approval flow |

## Next

- [Reference](reference.md) — every endpoint indexed
- [Error Codes](error-codes.md) — full status code matrix
- [Examples](examples.md) — curl / Python / Node sample for every common flow
- [Identity service](../services/identity.md) — token issuance internals
- [JWT Authentication security](../security/jwt-auth.md) — the deep dive
