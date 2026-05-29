# Error Codes

*The status codes Aegis returns and what to do about each. Aegis sticks to canonical HTTP semantics — no custom 2xx success codes for failures.*

## Status code summary

| Code | Meaning | Where it comes from | Action |
|---|---|---|---|
| 200 | OK | Successful read | Process the `data` field |
| 201 | Created | Successful POST that produced a row | Process the `data` field with the new id |
| 204 | No Content | Successful action with no return value (rare) | Treat as success |
| 400 | Bad Request | Missing required header or malformed body | Fix the request and retry |
| 401 | Unauthorized | Missing, expired, or invalid auth | Re-mint a token; see [Authentication](authentication.md) |
| 403 | Forbidden | Auth valid but caller is not permitted | Inspect `error` field for the specific reason |
| 404 | Not Found | The path or resource does not exist | Check the path; verify the resource id |
| 405 | Method Not Allowed | Path exists but the method doesn't | Use the right verb |
| 409 | Conflict | The action would violate a constraint | Inspect the body for the conflicting state |
| 422 | Validation Failed | Body did not match the schema | Inspect `meta.details` for the field path |
| 429 | Too Many Requests | Rate-limit or quota exceeded | Wait `Retry-After` seconds; see [rate-limit runbook](../operations/runbooks/rate-limit-spike.md) |
| 500 | Internal Server Error | Unhandled exception | Retry once; if persistent, file an issue |
| 502 | Bad Gateway | Upstream service returned non-200 | Retry; check Settings → System Health |
| 503 | Service Unavailable | Downstream temporarily down or capacity exceeded | Retry with backoff |
| 504 | Gateway Timeout | Decision pipeline exceeded the deadline | Retry once |

## Response envelope on errors

Every JSON response uses the same shape:

```json
{
  "success": false,
  "data":    null,
  "error":   "<short string identifier>",
  "meta":    { ... }   // optional structured detail
}
```

Some non-error paths (404, redirects) bypass the envelope and return FastAPI's default `{"detail": "..."}` shape — this is the FastAPI router-level default that fires before the application enters the response-wrapping middleware.

## Common error bodies

### 400 — `X-Tenant-ID required`

```json
{
  "success": false,
  "error": "X-Tenant-ID header required"
}
```

Cause: the request is missing the `X-Tenant-ID` header. Aegis enforces it on every authenticated path.

Fix: add the header. If the header is set but the value is unparseable as a UUID, the same path returns 400 with `error: "Invalid Tenant UUID"`.

### 401 — `Invalid or expired token`

```json
{
  "success": false,
  "error": "Invalid or expired token"
}
```

Cause: JWT expired (15-minute default), signature invalid, or revoked. The platform does not distinguish between these in the response (avoids leaking information).

Fix: re-mint via `/auth/token` or `/auth/refresh`.

### 401 — `tenant_mismatch`

```json
{
  "success": false,
  "error": "tenant_mismatch"
}
```

Cause: the `X-Tenant-ID` header value does not match the `tenant_id` claim in the JWT.

Fix: send the same tenant_id as in the JWT. If you genuinely need to act on behalf of a different tenant, the user must log in with the new tenant.

### 403 — `Write operations require ADMIN or SECURITY role`

```json
{
  "success": false,
  "error": "Write operations require ADMIN or SECURITY role"
}
```

Cause: a non-GET request from a user with role `VIEWER`, `AUDITOR`, or another non-write role.

Fix: re-login as `ADMIN` or `SECURITY`. The role rule is in `services/gateway/_mw_auth.py:163-169`. See [RBAC Roles](../security/rbac-roles.md).

### 403 — `kill_switch_engaged`

```json
{
  "success": false,
  "error": "kill_switch_engaged",
  "data": {
    "engaged_at": "2026-05-29T10:42:13Z",
    "engaged_by": "<user_id>",
    "reason":     "Suspected prompt injection campaign"
  }
}
```

Cause: the tenant's kill switch is engaged.

Fix: contact the operator who engaged it (the response carries `engaged_by`). See [Kill Switch runbook](../operations/runbooks/kill-switch-engaged.md).

### 403 — `policy_denied`

```json
{
  "success": false,
  "error": "policy_denied",
  "data": {
    "action":   "deny",
    "rule_id":  "agent.deny.destructive_sql",
    "findings": ["destructive_sql"],
    "score":    0.97,
    "audit_id": "<uuid>"
  }
}
```

Cause: the action matched a deny rule in OPA at stage 4 of the gateway pipeline.

Fix: inspect the `rule_id` in the Policy Builder UI. If the deny is expected (attack scenario), the result is the correct platform behavior. If it's a false positive, edit the rule via Policy Builder.

### 403 — `approval_required`

```json
{
  "success": false,
  "error": "approval_required",
  "data": {
    "approval_key": "<key>",
    "reason":       "Auto Response rule X requires human review"
  }
}
```

Cause: an Auto Response rule with `approval_required=true` matched.

Fix: an operator approves via `POST /auto-response/pending/{approval_key}/approve` from the Auto Response UI.

### 422 — `Validation failed`

```json
{
  "success": false,
  "error": "Validation failed",
  "meta": {
    "details": [
      {
        "type": "missing",
        "loc":  ["body", "tool_name"],
        "msg":  "Field required",
        "input": null
      }
    ]
  }
}
```

Cause: the request body or query parameters did not match the schema.

Fix: the `meta.details` array has one entry per validation error with `loc` (the field path) and `msg`. Inspect the OpenAPI spec at `/openapi.json` for the expected schema.

### 429 — quota or rate limit

```json
{
  "success": false,
  "error": "Rate limit exceeded",
  "data": {
    "limit_type": "tenant_rps",
    "reset_at":   "2026-05-29T10:43:00Z"
  }
}
```

Headers include `Retry-After: <seconds>`.

`limit_type` values:

| Value | Cause |
|---|---|
| `tenant_rps` | Per-tenant requests-per-second cap exceeded |
| `tenant_burst` | Per-tenant burst capacity exhausted |
| `tenant_daily_request` | Daily request cap exceeded |
| `tenant_monthly_request` | Monthly request cap exceeded |
| `agent_cost` | Per-agent daily inference USD cap exceeded |

Fix: wait `Retry-After` seconds or raise the cap. See [Rate Limit Spike runbook](../operations/runbooks/rate-limit-spike.md).

### 504 — `decision_timeout`

```json
{
  "success": false,
  "error": "decision_timeout",
  "data": {
    "deadline_seconds": 1.5,
    "request_id":       "<uuid>"
  }
}
```

Cause: the decision pipeline (stages 4–7) exceeded `DECISION_GATHER_TOTAL_TIMEOUT` (default 1.5 seconds).

Fix: retry once. The audit chain still records the timeout. Persistent 504s indicate a downstream service issue; inspect Settings → System Health.

## SDK error mapping

Aegis SDKs map HTTP status codes to typed exceptions:

| HTTP | SDK exception |
|---|---|
| 401 | `AuthError` |
| 403 `policy_denied` | `PolicyDeniedError` (carries `rule_id`, `findings`) |
| 403 `kill_switch_engaged` | `KillSwitchEngagedError` |
| 403 `approval_required` | `EscalationRequiredError` |
| 429 | `RateLimitError` (carries `retry_after`) |
| 502, 503 | `ServiceUnavailableError` |
| 504 `decision_timeout` | `DecisionTimeoutError` |

Applications should catch the specific subclass — different remediation per kind.

## Retry policy

| Code | Retry? | How |
|---|---|---|
| 200, 201 | n/a | Success |
| 400, 401, 403, 404, 422 | No | Fix the request |
| 429 | Yes | Honor `Retry-After`; exponential backoff if absent |
| 500, 502, 503 | Yes | Exponential backoff with jitter; max 3 attempts |
| 504 | Yes | Single retry; persistent 504 indicates infrastructure |

The SDKs implement the above. Custom callers should follow the same.

## What is NOT an error

Some response patterns look like errors but are not:

- **`GET /audit/logs/verify` returns 200 with body `{ "valid": false, "violations": [...] }`.** This is a healthy non-error response indicating the chain has a violation. The endpoint always returns 200 unless the gateway itself fails; the violation status is in the body. Earlier behavior was 409, which the SDK threw as an error; the contract is now 200-with-body.
- **`GET /decision/kill-switch/{tenant_id}` returns 200 with body `{ "engaged": true }`.** Reading state is always 200, even if the state is "engaged".
- **`GET /audit/logs?limit=5` returns 200 with empty `items: []`.** No matches is not an error.

## Idempotency

Aegis honors the `X-Idempotency-Key` header on `POST /execute`. The gateway dedupes within a 60-second window via `acp:idempotency:{key}`. A duplicate request returns the original response (200 or whatever); not a 409.

For other POST paths (e.g. `/agents/{id}/permissions`), idempotency is enforced by unique constraints; a duplicate returns 409 with the conflicting field.

## Next

- [Reference](reference.md) — full endpoint inventory
- [Authentication](authentication.md) — token issuance and validation
- [Examples](examples.md) — happy-path and error-path samples
- [Rate Limit Spike runbook](../operations/runbooks/rate-limit-spike.md) — how to triage 429s
- [Kill Switch runbook](../operations/runbooks/kill-switch-engaged.md) — how to triage `kill_switch_engaged`
