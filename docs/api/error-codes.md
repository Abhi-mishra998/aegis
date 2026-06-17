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

### Deny-path response shape

Denies (401 / 403 / 422) coming through the gateway security middleware (`services/gateway/_mw_response.py::_deny`) carry the canonical explainability fields so SDK callers don't have to regex reasons out of the `error` string. The full shape:

```json
{
  "success":    false,
  "error":      "<human-readable summary>",
  "meta":       { "code": 403 },
  "findings":   ["sql_injection", "ddl_destruction"],
  "reason":     "<rule identifier, e.g. dropped_table>",
  "policy_id":  "agent.deny.destructive_sql",
  "risk_score": 97,
  "explanation": "<one-sentence root-cause from /audit/{id}/explain>",
  "security":   { "engine_score": 97, "signals_triggered": [...] },
  "governance": { "engine_score": 88, "policy_applied": "..." },
  "mitre":      { "tactic_id": "TA0040", "technique_id": "T1485", "tactic_name": "Impact" }
}
```

| Field | Type | Notes |
|---|---|---|
| `success` | bool | Always `false` on the deny path |
| `error` | string | Human-readable summary; safe to surface to end users |
| `meta.code` | int | Mirrors the HTTP status |
| `findings` | string[] | Canonical signal IDs from `services/security/signal_registry.py`. Stable vocabulary — SDKs match on this, not the `error` string |
| `reason` | string | The rule identifier that fired (e.g. `dropped_table`, `path_traversal`, `wire_above_hard_cap`) |
| `policy_id` | string | Fully qualified Rego rule, e.g. `agent.deny.destructive_sql` |
| `risk_score` | int 0–100 | Composite score from the canonical action model |
| `explanation` | string | One-sentence root cause; same text shown on the `/audit/{id}/explain` panel |
| `security` | object | FUP-4 security engine slice (signal scores, triggered classifiers) |
| `governance` | object | FUP-4 governance engine slice (which Rego rule, what policy version) |
| `mitre` | object | Sprint 1 MITRE ATT&CK mapping for the primary finding |

Only fields with non-null values are emitted, so a simple `policy_denied` from the kill switch will have a shorter body than a multi-signal canonical decision. SDKs should treat absent fields as `null`, not error.

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
  "error": "Invalid or expired token",
  "meta": {"code": 401}
}
```

Cause: JWT expired (15-minute default), signature invalid, or revoked. The platform does not distinguish between these in the response (avoids leaking information).

Fix: re-mint via `/auth/token` or `/auth/refresh`.

### 401 — `Invalid Clerk token alg`

```json
{
  "success": false,
  "error": "Invalid Clerk token alg",
  "meta": {"code": 401}
}
```

Cause: a token with HS256 algorithm but a Clerk issuer (`iss: https://*.clerk.accounts.dev`) was presented. Clerk mints RS256 only — an HS256 with a Clerk `iss` is either a forgery or a misconfigured local tooling. The legacy ACP HS256 path stays open under `ACP_AUTH_PROVIDER=both`, but only for tokens whose `iss` is `aegisagent`.

Fix: the caller must pull a fresh Clerk session token via `/auth/me` (which now dispatches Clerk RS256 — see commit `e68b671`). If you're a backend agent, use the `acp_emp_…` API key with `Authorization: Bearer …` instead of a Clerk JWT.

### 401 — `tenant_mismatch`

```json
{
  "success": false,
  "error": "tenant_mismatch"
}
```

Cause: the `X-Tenant-ID` header value does not match the `tenant_id` claim in the JWT.

Fix: send the same tenant_id as in the JWT. If you genuinely need to act on behalf of a different tenant, the user must log in with the new tenant.

### 403 — `Tenant mismatch / Org consistency / RBAC`

Three sibling causes share the `403 Forbidden` status — the body's `error` field discriminates:

```json
{
  "success": false,
  "error": "Tenant mismatch",
  "meta": {"code": 403}
}
```

```json
{
  "success": false,
  "error": "Org consistency: token org_id does not match X-Tenant-ID",
  "meta": {"code": 403}
}
```

```json
{
  "success": false,
  "error": "Write operations require ADMIN or SECURITY role",
  "meta": {"code": 403}
}
```

Causes:

- **Tenant mismatch.** `X-Tenant-ID` header doesn't match the `tenant_id` claim in the JWT. Different from the 401 of the same name only in legacy paths — Sprint-1 SaaS-invariant work consolidated it into a hard 403 because the auth itself is valid, the request just targets the wrong tenant.
- **Org consistency.** Sprint-1 SaaS-invariant: `user.org_id` must equal `tenant.tenant_id` for Clerk-minted sessions (see commit `dc9ed39`). A Clerk org-switch in the browser without re-pulling the session fires this.
- **RBAC.** A non-GET request from `VIEWER`, `AUDITOR`, or another non-write role. Source: `services/gateway/_mw_auth.py:163-169`.

Fix: re-login under the intended tenant/org, or upgrade the user's role. See [RBAC Roles](../security/rbac-roles.md).

### 403 — `Security: <signal-name>`

```json
{
  "success":     false,
  "error":       "Security: Path traversal detected: '/etc/passwd'",
  "meta":        {"code": 403},
  "findings":    ["path_traversal"],
  "reason":      "path_traversal",
  "policy_id":   "agent.deny.sensitive_path",
  "risk_score":  97,
  "explanation": "File path traversal attempt (e.g. ../etc/passwd)",
  "mitre":       {"tactic_id": "TA0009", "technique_id": "T1005", "tactic_name": "Collection"}
}
```

Cause: the security middleware caught a high-risk signal before the request hit the decision pipeline. The `findings` array holds the canonical signal id from `services/security/signal_registry.py` (34 signals as of Sprint 1 — `path_traversal`, `sql_injection`, `ddl_destruction`, `pii_exfiltration`, `prompt_injection`, …). SDK callers should match on `findings`, not the `error` string — the `error` is a friendly UI message that can be reworded.

Fix: if the deny is correct (it usually is — this fires on actual attacks), the platform behaved as designed. If you believe it's a false positive, inspect the signal id in the Risk → Signal Weights UI and adjust the threshold, or add a per-agent exception via Policy Builder.

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
  "success":     false,
  "error":       "approval_required",
  "meta":        {"code": 403},
  "findings":    ["wire_above_hard_cap"],
  "reason":      "wire_above_hard_cap",
  "policy_id":   "agent.escalate.wire_25m",
  "risk_score":  88,
  "explanation": "Wire transfer above hard cap requires SECURITY sign-off",
  "data": {
    "approval_key": "<key>",
    "reason":       "Auto Response rule X requires human review"
  }
}
```

Cause: the decision pipeline escalated the call — either an Auto Response rule with `approval_required=true` matched, or the canonical action model produced action=`escalate` (`/execute` was previously a 202; since 2026-05-15 it's a synchronous 403 — see SDK note below). The `/v1/messages` and `/v1/chat/completions` proxy endpoints emit the same shape.

Fix: an operator approves via the Approval Inbox UI (or `POST /autonomy/approvals/{key}/approve`). The approval response includes an **approval ID**; the original caller replays the same request with `X-Aegis-Approval-ID: <id>` header. The approval is valid for 5 minutes and is invalidated if `POST /policy/upload` ran in the window (the `acp:tenant:policy_version:{tenant_id}` counter must match). See [SDK Wrappers — `X-Aegis-Approval-ID` contract](../integrations/sdk-wrappers.md#the-x-aegis-approval-id-header-contract).

### 422 — `Validation failed`

```json
{
  "success": false,
  "error": "Validation failed",
  "meta": {
    "code": 422,
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

Cause: the request body or query parameters did not match the schema. Common offenders:

- **`POST /audit/logs/search` with `"limit": > 100`.** The endpoint hard-caps `limit` at 100 (`services/audit/router.py:525`, `le=100`). A request body `{"limit": 500}` returns 422 with `loc=["body","limit"]`. The browser UI worked around this by switching to `GET /audit/logs?limit=N` — that path accepts `limit ≤ 1000`.
- **`POST /iag/refresh?days=200`.** `days` is bounded `1..90`; out-of-range values produce 422 with `loc=["query","days"]`.
- **Missing required field in `POST /execute`.** `agent_id`, `tool`, `arguments` are all required; absent fields produce 422 with `loc=["body","<field>"]`.

Fix: the `meta.details` array has one entry per validation error with `loc` (the field path) and `msg`. Inspect the OpenAPI spec at `/openapi.json` for the expected schema.

### 429 — quota or rate limit

```json
{
  "success": false,
  "error": "Rate limit exceeded",
  "meta": {"code": 429},
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
| `tenant_rps` | Per-tenant requests-per-second cap (token bucket) |
| `tenant_burst` | Per-tenant burst capacity exhausted |
| `tenant_daily_request` | Daily request cap exceeded |
| `tenant_monthly_request` | Monthly request cap exceeded |
| `agent_cost` | Per-agent daily inference USD cap exceeded |
| `ip_per_minute` | Per-IP throttle (sustained probing, no tenant context) |

Fix: wait `Retry-After` seconds or raise the cap. The per-IP throttle does not raise the cap — it relaxes after the requesting IP backs off for ~60s. See [Rate Limit Spike runbook](../operations/runbooks/rate-limit-spike.md).

### 502 — `anthropic_upstream_unreachable` (and other upstream errors)

```json
{
  "success": false,
  "error": "Anthropic upstream unreachable",
  "meta": {"code": 502, "category": "upstream", "provider": "anthropic"}
}
```

Cause: the LLM proxy path (`/v1/messages`, `/v1/chat/completions`) failed to reach the upstream provider — connect timeout, TLS error, or upstream 5xx. The same body shape is emitted for `provider=openai`, `provider=azure_openai`, `provider=bedrock`, `provider=groq`.

Fix: retry once with exponential backoff. The audit row still records the attempt (`decision=upstream_error`) so the call is reflected in usage even though no upstream tokens were consumed.

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

Aegis SDKs (1.1.0 — `aegis-anthropic`, `aegis-openai`, `aegis-bedrock`, `aegis-langchain`) map HTTP status codes to typed exceptions:

| HTTP | SDK exception |
|---|---|
| 401 (any reason) | `AuthError` |
| 403 `Tenant mismatch` / `Org consistency` / RBAC | `AuthError` (subclass `ForbiddenError`) |
| 403 `Security: <signal>` | `PolicyDeniedError` (carries `findings`, `policy_id`, `mitre`) |
| 403 `policy_denied` | `PolicyDeniedError` (carries `rule_id`, `findings`) |
| 403 `kill_switch_engaged` | `KillSwitchEngagedError` |
| 403 `approval_required` | `EscalationRequiredError` (carries `approval_key`; replay with `X-Aegis-Approval-ID`) |
| 422 | `ValidationError` (carries `meta.details`) |
| 429 | `RateLimitError` (carries `retry_after`, `limit_type`) |
| 502 | `ServiceUnavailableError` (carries `meta.provider` on LLM-proxy errors) |
| 503 | `ServiceUnavailableError` |
| 504 `decision_timeout` | `DecisionTimeoutError` |

Applications should catch the specific subclass — different remediation per kind. The Path-B proxy clients (raw `openai`/`anthropic` SDKs with `base_url=https://aegisagent.in/v1`) won't raise these typed exceptions — they raise the provider SDK's own exception type with the Aegis envelope body in `response.body`. Inspect `body["findings"]` and `body["error"]` to recover the deny reason.

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
