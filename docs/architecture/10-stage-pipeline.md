# Gateway Pipeline

*Every request that enters Aegis traverses an eleven-stage middleware pipeline. Each stage is a separate enforcement point with its own deny condition, its own observability, and its own bypass list.*

The pipeline is documented in source at the top of `services/gateway/middleware.py`. The canonical list of stages — including the stage-numbering that Aegis itself uses in logs, metrics, and audit rows — is reproduced and explained below.

> A naming note: the page title says "Pipeline" rather than "10-Stage" because there are eleven stages numbered 0 through 10. The earlier shorthand "10-stage pipeline" referred to the post-auth stages and persists in some marketing copy. The middleware source is the source of truth.

## The eleven stages

| # | Stage | What it does | If it denies |
|---|---|---|---|
| 0 | Kill Switch | Look up `acp:kill_switch:{tenant_id}` in Redis | HTTP 403 `kill_switch_engaged`, audit row written |
| 1 | Auth | Validate JWT signature, expiry, revocation, replay window | HTTP 401, no audit row |
| 2 | Rate Limit | Redis Lua atomic token bucket per (tenant, agent, IP) | HTTP 429 with `Retry-After` |
| 3 | Inference | Injection detection, tool-name guard, request-shape risk scoring | Contributes signal to stage 6, does not deny on its own |
| 4 | Policy | Redis-cached OPA evaluation; cache miss falls through to OPA | HTTP 403 with `rule_id`, audit row written |
| 5 | Behavior | Sequence, velocity, cost, cross-agent intelligence | Contributes signal to stage 6 |
| 6 | Decision | Unified DecisionEngine combines stages 3/4/5 into one action | Denies as `policy_denied` or `behavior_denied` |
| 7 | Enforcement | Maps DecisionEngine output to ALLOW / MONITOR / THROTTLE / ESCALATE / KILL; also checks autonomy contract | HTTP 403 for KILL / ESCALATE, 429 for THROTTLE |
| 8 | Execution | `await call_next(request)` — the request reaches the FastAPI route handler | N/A — the route handler may itself raise |
| 9 | Output Filter | Redact secrets from the response body before sending | Never denies; mutates the response only |
| 10 | Audit | Async push to Redis Stream `acp:audit_events`; worker writes the signed row | Never blocks the response — the response is already on its way back |

## Stage 0 — Kill Switch

**Code**: `services/gateway/middleware.py::dispatch` (top of the dispatch method); state managed by `services/decision/router.py::kill_switch_set` and `kill_switch_get`.

The first thing the middleware does is `await redis.get(f"acp:kill_switch:{tenant_id}")`. If a value is present, every subsequent request from that tenant returns 403 with body:

```json
{ "success": false, "error": "kill_switch_engaged", "data": { "engaged_at": "...", "engaged_by": "..." } }
```

The audit row is still written. Other stages are skipped. Engaging the switch on a tenant takes `POST /decision/kill-switch/{tenant_id}` with `ADMIN` or `SECURITY` role; the propagation lag from API call to every gateway worker observing the new state is bounded by the worker's Redis poll period and is under five seconds in production. See [Kill Switch](../security/kill-switch.md) for the runbook.

## Stage 1 — Auth

**Code**: `services/gateway/_mw_auth.py::_AuthMixin._authenticate` and `validate()` inside the active token validator (`services/gateway/auth.py`).

Three checks happen in order:

1. **JWT signature and expiry.** The token validator (`token_validator` global, initialized at process start) verifies the HS256 signature against `INTERNAL_SECRET` and the `exp` claim against `time.time()`. A failed signature or expired token returns 401 immediately. Per-fail counter `acp:auth_fail:{ip}` is incremented with a 5-minute expiry.
2. **Revocation check.** A SHA-256 fingerprint of the token is looked up in `acp:revoked_tokens:*`. A separate JTI-based revocation lookup (`acp:revoked_jti:{jti}`) covers the case where the operator wants to revoke without the original token in hand.
3. **Replay window.** Only for `/execute` paths: the JTI's last-used timestamp is stored in `acp:jti_last_used:{jti}`. A reuse within 1 millisecond returns 429 `Too many requests: burst replay detected`. This is tight on purpose — legitimate clients do not reuse a JTI in 1ms; an attacker replaying a stolen request often does.

The validated payload is stored on `request.state.role`, `request.state.tenant_id`, and `request.state.permissions`. The role-to-permission map is the source of truth for write-path enforcement:

```python
permissions_map = {
    "ADMIN":    ["*"],
    "SECURITY": ["kill_switch", "view_risk", "execute_agent"],
    "AUDITOR":  ["view_risk", "view_audit"],
    "VIEWER":   ["view_risk"],
    "agent":    ["execute_agent"],
}
```

Write-path enforcement runs at the bottom of stage 1 and denies any non-GET/HEAD/OPTIONS request where the role is not `ADMIN` or `SECURITY`. The one exception is the `agent` role on `/execute` — agents speak through `/execute` only, and that exception is bounded by the route prefix.

Skip list: the paths in `_SKIP_PATHS` at the top of `middleware.py` bypass authentication. These are the public health checks (`/health`, `/status`, `/metrics`) and the login endpoints themselves. SSO callbacks are bypassed by prefix (`_SKIP_PATH_PREFIXES`).

## Stage 2 — Rate Limit

**Code**: `services/gateway/_mw_rate_limit.py::_RateLimitMixin`, backed by `sdk/common/ratelimit.py::RateLimiter`.

A Redis Lua script implements a per-tenant token bucket atomically. The state lives under `acp:ratelimit:{tenant_id}:tokens` plus a refill timestamp. The script:

1. Reads current tokens and last refill.
2. Adds `(now - last_refill) * rate` tokens up to `burst`.
3. Decrements one token. If below zero, returns the time-until-next-token; otherwise returns 0.
4. Writes the new state.

A return value greater than 0 produces HTTP 429 with a `Retry-After` header in seconds. The body declares `limit_type: "tenant_rps"` or `"agent_rps"` so the caller knows which cap fired. Tenant-level rate, burst, daily, and monthly caps live in `acp_identity.tenants` and are loaded into Redis at first hit.

A separate counter handles **per-agent inference USD cost caps**: `acp:agent_cost_today:{agent_id}:{YYYYMMDD}` is incremented after each inference; the cap is read from `acp:agent_cost_cap:{agent_id}`. A breach produces an audit row with `action="inference_cost_cap_exceeded"` and a 429.

## Stage 3 — Inference

**Code**: `services/gateway/inference_proxy.py::inference_proxy.evaluate`.

Three lightweight checks pre-screen the request before the more expensive policy and behavior stages:

- **Prompt injection signatures** — regex catalog including `IGNORE PREVIOUS INSTRUCTIONS`, `SYSTEM:`, JSON-injection openers in user-controlled fields.
- **Tool-name allow-list** — the request's `tool_name` is checked against the agent's permission list from the registry cache. A miss does not deny here; it surfaces as a finding to stage 6.
- **Request-shape risk scoring** — payload size, depth of nested JSON, presence of high-risk fields (`password`, `secret`, encoded blobs over a length threshold).

This stage produces a `ProxyDecision` with `signal_count` and a `risk_contribution` in [0, 1]. It does not deny on its own — its purpose is to give the Decision Engine more to work with at stage 6.

## Stage 4 — Policy

**Code**: `services/policy/router.py::evaluate_policy` for the underlying call; `services/gateway/middleware.py` caches via Redis key `acp:policy_decision:{request_hash}` with a tier-dependent TTL.

OPA is the only place Rego is interpreted. The gateway never decides policy directly. The flow:

1. Compute a deterministic hash of `(tenant_id, agent_id, tool_name, payload_shape)`.
2. Check `acp:policy_decision:{hash}` in Redis. A hit returns instantly.
3. On a miss, POST the input to OPA's data API at `http://opa:8181/v1/data/aegis/decision`.
4. Store the response in Redis with TTL from `_TIER_TTL` (enterprise: 24h, premium: 1h, basic: 5m).

A `decision: deny` from OPA produces HTTP 403 with `rule_id` in the body. The audit row records the `rule_id` and the OPA cache state (hit/miss) so a forensic analyst can tell whether a deny was real-time or cached.

Hard-deny patterns (path traversal, `DROP TABLE` outside reporting, k8s production namespace operations) are encoded as Rego rules in `services/policy/policies/agent_policy.rego`. They are evaluated alongside soft rules.

## Stage 5 — Behavior

**Code**: `services/behavior/router.py::score_behavior`; gateway calls it with `service_client.behavior`.

The behavioral firewall computes four signals against the agent's rolling baseline (last 7 days):

- **Sequence** — does the call belong to a known good sequence of actions?
- **Velocity** — is the agent calling N× more often than its baseline?
- **Cost** — is this an expensive call relative to the agent's pattern?
- **Cross-agent intelligence** — has any other agent in the tenant tripped on a similar request shape?

Each signal returns a confidence in [0, 1]. The aggregate behavior score is the weighted sum.

Per-tenant **degraded-mode policy** kicks in when the behavior service is unreachable or slow. The policy is one of `block_high_risk` (default), `block_all`, or `allow_with_audit`, persisted on `acp_identity.tenants.degraded_mode_policy`. The gateway emits an unconditional `behavior_firewall_decision` audit row on every consult, including ones that fell back to degraded mode, so the operator can review what was decided when.

## Stage 6 — Decision

**Code**: `services/decision/engine.py::DecisionEngine.evaluate`.

The Decision Engine is the only place where signals are combined. It receives:

- The inference signal (stage 3)
- The policy outcome and any rule firings (stage 4)
- The behavior score (stage 5)
- The agent's risk_level from the registry (one of `low`, `medium`, `high`, `critical`)
- The current `acp:signal_weights:{tenant_id}` Redis configuration

It produces a single `Decision`:

```python
class Decision:
    action: ExecutionAction      # ALLOW | MONITOR | THROTTLE | ESCALATE | KILL
    score: float                 # 0.0 – 1.0
    findings: list[str]          # canonical vocabulary, 13 strings
    signals_evaluated: dict      # {name: {score, threshold, triggered}}
    confidence: float
    reasoning: str
```

The `findings` field is a canonical, finite vocabulary documented at `docs/risk_reasons.md` and mirrored in the SDK as `acp_client.FINDINGS`. The diagnostic flags from upstream (OPA `rule_id`, behavior signal names) are stored separately in `metadata.diagnostic_flags`.

Default weights live in `services/decision/router.py::DEFAULT_WEIGHTS` and are overridable per tenant via `PUT /decision/signal-weights` (ADMIN or SECURITY role).

## Stage 7 — Enforcement

**Code**: `services/gateway/middleware.py` (inside the same `dispatch` method).

Maps the Decision's `action` to HTTP semantics:

| Action | HTTP | Notes |
|---|---|---|
| ALLOW | proceed to stage 8 | The default fast path. |
| MONITOR | proceed to stage 8 | Same as ALLOW for execution. The audit row is tagged so reviewers can filter. |
| THROTTLE | 429 with `Retry-After` | Synthetic — caps a burst that has not yet hit the rate limit. |
| ESCALATE | 403 with `error: "approval_required"` | The SDK raises `EscalationRequiredError`. |
| KILL | 403 with `error: "policy_denied"` | The audit row carries `rule_id` and `findings`. |

This stage **also** evaluates the **autonomy contract** — cross-tenant access rules, time windows, delegation depth caps, daily cost caps. A contract violation downgrades a would-be ALLOW to ESCALATE or KILL. The check is done by `services/gateway/trust_emitter.py::check_autonomy_contract`.

## Stage 8 — Execution

The request reaches its route handler. For `/execute`, the route hands off to `services/policy/router.py::execute_tool` which proxies to the configured upstream tool with the agent's credentials and a deadline. For all other paths, the FastAPI route runs normally.

A semaphore in `_RateLimitMixin` caps concurrent executions per gateway worker at a configured value (default 100) to prevent cascade failure when a downstream tool is slow.

## Stage 9 — Output Filter

**Code**: `services/gateway/_mw_response.py::_ResponseMixin`.

Redacts secrets from the response body before it returns to the client. The redaction patterns include:

- Bearer tokens and API keys in any field
- Email addresses if `redact_pii_in_responses=true` on the tenant
- Credit-card and SSN regex matches

This stage never denies; it only mutates. The audit row records whether redactions happened and how many.

## Stage 10 — Audit

**Code**: `services/gateway/_mw_audit.py::_AuditMixin._finalize_request`; the audit worker is `services/audit/outbox_worker.py`.

The middleware composes the canonical audit record and pushes it into the Redis Stream `acp:audit_events` with `XADD`. The response is already on its way back at this point — this stage never blocks the client.

A separate worker process (the `audit-outbox-N` workers running inside `acp_audit`) drains the stream, writes the signed row to `audit_logs`, atomically writes the `pending_usage_event` for billing, and acknowledges the stream entry. Failures with retry counters above a threshold land in a dead-letter list.

The signed audit row is what every other guarantee in Aegis is built on. See [Cryptographic Audit Chain](../security/crypto-audit-chain.md) for the signing scheme, the prev_hash chain, and the daily Merkle root.

## Bypass paths

Two sets of paths take a partial trip through the pipeline:

- **`_SKIP_PATHS`** — fully bypass stages 0–10. The endpoint runs without auth or audit. Reserved for health, metrics, public status, and the login endpoints themselves.
- **Management paths under `_MANAGEMENT_PATH_PREFIXES`** — go through stages 0, 1, 2, 9, 10 but skip stages 3–8. They are human-operator CRUD endpoints (`/agents`, `/audit`, `/decision`, `/incidents`, etc.) that do not represent agent tool execution and so do not need the per-call policy and decision evaluation.

The distinction matters: every CRUD action by a human admin still produces a signed audit row (stage 10 runs), but the per-call OPA evaluation that exists for agent tool calls is skipped because it would not apply.

## Observability per stage

Each stage emits:

- A Prometheus histogram of its own latency: `acp_gateway_stage_{n}_latency_seconds`
- A counter of denies: `acp_gateway_stage_{n}_denied_total{tenant_id, reason}`
- A counter of skipped invocations: `acp_gateway_stage_{n}_skipped_total{tenant_id, reason}`
- A span inside the OpenTelemetry trace, attached to the request-level trace ID

These are scraped by Prometheus and visualized on the **platform-slo** Grafana dashboard under `infra/grafana-dashboards/platform_slo.json`.

## Source of truth

The authoritative description of the pipeline is the docstring at the top of `services/gateway/middleware.py`. The dispatch method itself is the executable implementation. When this page and the code disagree, the code wins — please file an issue or update the page.

## Next

- [Flow of a Decision](flow-of-a-decision.md) — a single `POST /execute` walked through all eleven stages with real values.
- [Data Model](data-model.md) — the Redis keys and Postgres tables each stage touches.
- [Multi-Tenancy](multi-tenancy.md) — how `X-Tenant-ID` is added to every Redis key and SQL query the pipeline emits.
