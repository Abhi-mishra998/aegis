"""
ACP Gateway — Security Middleware
==================================
Enforces the complete request pipeline:

  Request
    ↓ 0. Kill Switch   — tenant emergency blockade check
    ↓ 1. Auth          — local JWT + SHA-256 revocation + jti check
    ↓ 2. Rate Limit    — Redis Lua atomic per-token + per-agent
    ↓ 3. Inference     — injection detection, tool guard, risk scoring
    ↓ 4. Policy        — Redis cache → OPA (cache miss only)
    ↓ 5. Behavior      — sequence, velocity, cost, cross-agent intelligence
    ↓ 6. Decision      — unified DecisionEngine (ONE formula, ONE threshold table)
    ↓ 7. Enforcement   — ALLOW/MONITOR/THROTTLE/ESCALATE/KILL
    ↓ 8. Execution     — call_next(request)
    ↓ 9. Output Filter — redact secrets from response
    ↓ 10. Audit        — async Redis Stream (non-blocking)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware

from sdk.common.background import safe_bg as _safe_bg
from sdk.common.config import settings
from sdk.common.ratelimit import RateLimiter
from services.decision.schemas import Decision, ExecutionAction
from services.gateway._helpers import publish_event
from services.gateway._mw_audit import _AuditMixin
from services.gateway._mw_auth import _AuthMixin
from services.gateway._mw_rate_limit import _RateLimitMixin
from services.gateway._mw_response import _ResponseMixin
from services.gateway.client import service_client
from services.gateway.inference_proxy import ProxyDecision, inference_proxy
from services.gateway.trust_emitter import (
    check_autonomy_contract,
    emit_graph_event,
    emit_snapshot,
    emit_step,
    emit_timeline_end,
    emit_timeline_start,
    map_decision_to_outcome,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

_SKIP_PATHS = frozenset(
    [
        # `/metrics` is intentionally NOT here — it leaks tenant-labelled
        # Prom series. It's gated by an in-process X-Internal-Secret check
        # inside the middleware dispatch (see _maybe_allow_metrics below).
        "/health", "/docs", "/openapi.json", "/redoc",
        "/system/health",  # ops aggregate — must be reachable by k8s/ALB/Datadog probes (no tenant data)
        "/status",         # public customer-shareable status page (no tenant data)
        "/auth/token", "/auth/login", "/auth/agent/token",  # public auth endpoints
        "/auth/sso/providers",  # SSO provider list (public — drives login UI buttons)
        "/events/stream",  # SSE — inline auth handled in the route handler
        # sprint-5.3: Stripe webhook authenticates via Stripe-Signature, not JWT.
        # The handler verifies the signature before processing the event.
        "/billing/stripe/webhook",
        # Sprint 1 — Clerk integration surface. The webhook authenticates
        # via Svix signature (verified upstream in identity); the
        # provision endpoint authenticates via Clerk Bearer JWT validated
        # against Clerk's JWKS. Neither carries an Aegis-issued token.
        "/webhooks/clerk",
        "/auth/clerk/provision",
        # Sprint S4 — anonymous demo workspace. Marketing "Try live demo"
        # CTA hits this from an unauthed browser. Rate-limited per IP at
        # the gateway; mints a 30-min read-only JWT scoped to a freshly
        # seeded is_demo=true tenant.
        "/demo/spawn-workspace",
        # Scenario catalog is read-only metadata for the demo picker.
        "/demo/scenarios",
    ]
)

# SSO callback paths are public (browser redirects from OAuth providers carry no auth headers).
# They are matched by prefix rather than exact path because they include /{provider}/callback.
#
# Sprint 17 — `/v1/messages` is the Aegis-for-Teams Anthropic-compatible
# proxy. Auth is `x-api-key: acp_emp_…` (Anthropic SDK shape, NOT Aegis
# JWT), so the gateway middleware must skip its standard JWT auth here.
# The router does its own validation against the api_keys table + the
# subject_kind='employee' constraint, so the path is NOT actually public
# even though it's skip-listed.
_SKIP_PATH_PREFIXES = (
    "/auth/sso/",
    "/v1/messages",
    # Sprint 22 — OpenAI-compatible /v1/chat/completions proxy. Same
    # auth model as /v1/messages: x-api-key (or Authorization: Bearer)
    # carrying an acp_emp_ employee virtual key, validated by the
    # handler against the api-svc.
    "/v1/chat/completions",
    # Sprint 20 — SDK approval poll uses x-api-key (acp_emp_…); the
    # handler does its own key validation + tenant scoping, same as
    # /v1/messages. Skip-list lets the request through middleware so
    # the handler can dual-auth (employee key OR JWT).
    "/v1/approvals",
    # Sprint 21 — Slack approval callback links carry an HMAC
    # signature in the query string instead of a JWT. The handler
    # verifies the signature itself before touching autonomy-svc.
    "/slack/approve/",
    "/slack/reject/",
)

# Management paths: require auth + rate-limiting, but bypass the agent
# tool-execution security pipeline (OPA policy + Decision Engine).
# These are internal CRUD endpoints for human admin/SOC operators.
_MANAGEMENT_PATH_PREFIXES = (
    "/agents",
    "/logs",
    "/audit",
    "/decision",
    "/insights",
    "/forensics",
    "/usage",
    "/billing",
    "/incidents",
    # Sprint 4 — kill-chain storyline read API. Detection-side, no tool
    # execution semantics; pairs with /incidents (which is operations-side).
    "/storylines",
    # Sprint 5 — Identity & Access Graph + Blast Radius read API.
    # Analytics surface; no tool execution semantics.
    "/iag",
    # Sprint 6 — Auto-Remediation read + control API. Operator surface.
    "/remediation",
    # Sprint 7 — Threat-Intel IOC + feed control API. Operator surface.
    "/threat-intel",
    # Sprint 3 — Workspace identity (/workspace/me, /workspace/inventory,
    # /workspace/system-values, /workspace/exit-shadow-mode). Operator
    # CRUD surface for the post-Clerk-signup dashboard; NOT an agent
    # tool-execution path, so it must bypass tool-name extraction.
    "/workspace",
    # Sprint 17 — Aegis for Teams employee + spend rollup UI surface.
    # The `/v1/messages` LLM proxy is skip-listed separately because it
    # uses x-api-key auth; `/team` paths use the standard JWT.
    "/team",
    # Sprint 12 — mandate-KPI rollup for the post-login Dashboard hero.
    # Read-only management surface, audit-log fan-out under the hood.
    "/dashboard",
    # Sprint 19 follow-up — approval resume API. The SDK polls
    # /approvals/{id}/status and the management Inbox might also
    # consume the same endpoint; both go through the standard JWT.
    "/approvals",
    # Sprint 15 — unified replay surface. /replay/{request_id} is a
    # read-only audit-trail join the UI Incidents + Approval Inbox
    # link to. JWT-auth, tenant-scoped by the handler.
    "/replay",
    "/metrics",
    "/risk",
    "/stream",
    "/auto-response",
    "/api-keys",
    "/system",
    "/auth",
    "/tenant",
    "/status",
    # Cryptographic metadata — read-only, no tool execution semantics
    "/receipts",
    "/transparency",
    # 2026-05-13 — Runtime Trust Infrastructure proxy paths
    "/graph",
    "/flight",
    "/autonomy",
    # Management/reporting surfaces — human operator paths, not agent execution
    "/compliance",
    "/notifications",
    "/playbooks",
    "/registry",
    "/reports",
    "/security",
    "/siem",
    "/threat-intel",
    "/users",
    "/webhooks",
    "/admin",
    "/dashboard",
    "/policy",
    # Voice Guide bridge — mints LiveKit JWTs and reports worker status.
    # Pure read-only management surface, no agent execution semantics.
    "/voice",
    # /demo/groq-agent calls Groq + loops back to /execute on the operator's
    # behalf. The outer call itself isn't an agent tool invocation, so it
    # must skip the tool-name extraction; the inner /execute calls take the
    # normal hot path.
    "/demo",
)

# Configuration from global settings
_GLOBAL_RATE_LIMIT = settings.GLOBAL_RATE_LIMIT
_IP_RATE_LIMIT = settings.IP_RATE_LIMIT
_TENANT_RATE_LIMIT = settings.TENANT_RATE_LIMIT
_AGENT_RATE_LIMIT = settings.AGENT_RATE_LIMIT
_TOKEN_RATE_LIMIT = settings.TOKEN_RATE_LIMIT
_RATE_WINDOW = 60  # seconds

_IDEMPOTENCY_TTL_MAP = {
    "enterprise": 86400,  # 24 hours
    "premium": 3600,  # 1 hour
    "basic": 300,  # 5 minutes
}
_IDEMPOTENCY_PREFIX = "acp:idempotency:"
_GLOBAL_SLA_BUDGET = 2.0  # seconds — caps P99 at ~2s; fail-fast beats retrying into a dead downstream


# Sprint 3 — Shadow mode helpers.
#
# `would_have_blocked` is the canonical action name written to audit rows
# when the policy engine returned deny/escalate but the workspace was
# still inside its 14-day shadow window. The gateway short-circuits the
# normal deny response, lets the SDK proceed to execute the tool, and
# records the would-have-blocked event so the operator can review on the
# Shadow Review page.
SHADOW_DOWNGRADES_TOTAL = Counter(
    "acp_shadow_downgrades_total",
    "Policy deny/escalate decisions downgraded to would_have_blocked under shadow mode",
    ["tenant_id", "original_action"],
)

# ── EH-3: security-specific counters (separate from operational ones) ──
# These get their own Prometheus rule group so the SOC has one place to
# look. Wiring sites are documented in each counter's labelname comment.
AUTH_FAILURES_TOTAL = Counter(
    "acp_auth_failures_total",
    "Authentication failures — bad/expired JWT, bad API key, revoked token",
    # reason ∈ {invalid_token, expired_token, revoked_token, no_token, bad_api_key, validator_error}
    ["reason"],
)
TENANT_ISOLATION_VIOLATIONS_TOTAL = Counter(
    "acp_tenant_isolation_violation_total",
    "Header X-Tenant-ID did not match JWT tenant_id claim — attempted spoof",
    [],
)
REVOKED_TOKEN_ATTEMPTS_TOTAL = Counter(
    "acp_revoked_token_attempts_total",
    "Bearer of a revoked token attempted to authenticate",
    [],
)
RBAC_DENIED_TOTAL = Counter(
    "acp_rbac_denied_total",
    "Authenticated principal blocked by the gateway RBAC matrix (EH-1)",
    # role = the principal's actual role; path_prefix = first 2 segments of the rejected path
    ["role", "path_prefix"],
)
MASS_EXPORT_ATTEMPTS_TOTAL = Counter(
    "acp_mass_export_attempts_total",
    "POST /audit/logs/export OR POST /compliance/export call rate",
    ["endpoint", "tenant_id"],
)
ADMIN_ACTION_TOTAL = Counter(
    "acp_admin_action_total",
    "Sensitive admin mutation (kill switch, role change, key mint, tenant delete)",
    ["action"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Sprint 10 — Production hardening (PRODUCT_PLAN.md §14).

    Adds the security headers to every gateway response so JSON API
    callers (SDKs, curl, browser fetches) get the same hardening that
    nginx applies on its HTML responses. Mounted OUTERMOST so the
    headers ride on every code path, including 4xx/5xx generated by
    SecurityMiddleware itself.

    CSP is intentionally LESS strict on JSON than on the SPA HTML —
    JSON responses don't load scripts, so a permissive script-src is
    fine; we still set HSTS / Permissions-Policy / Referrer-Policy /
    X-Content-Type-Options on every reply.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        # Strict-Transport-Security — TLS-only once seen.
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        # Don't leak full URLs on outbound links.
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin",
        )
        # MIME-type sniffing off.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Browser permissions Aegis never needs.
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(self), usb=()",
        )
        # CSP for any HTML the gateway might serve (SDK demo pages, etc.).
        # nginx's CSP wins on the SPA path because it sets the header before
        # the gateway sees the response.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; frame-ancestors 'none'; base-uri 'self';",
        )
        return response


def _shadow_mode_active(request: Request) -> bool:
    """
    True when the workspace's `shadow_mode_until` value is still in the
    future. Tolerates string (ISO-8601) and datetime values for the
    state attribute — the tenant metadata fetch can return either
    depending on whether the cache deserializes timestamps.
    """
    raw = getattr(request.state, "shadow_mode_until", None)
    if raw is None:
        return False
    if isinstance(raw, str):
        try:
            # Accept both ``...+00:00`` and ``...Z`` suffixes.
            normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
            until = datetime.fromisoformat(normalized)
        except Exception:
            return False
    elif isinstance(raw, datetime):
        until = raw
    else:
        return False
    # Compare in UTC. If the stored value is naive we treat it as UTC by
    # convention (the migration's server_default uses now() which is
    # always UTC in our deploys).
    now = datetime.now(tz=until.tzinfo) if until.tzinfo else datetime.utcnow()
    return until > now


class SecurityMiddleware(_AuthMixin, _RateLimitMixin, _AuditMixin, _ResponseMixin, BaseHTTPMiddleware):
    """
    Single-pass security enforcement for all ACP Gateway requests.
    Enforces:
    1. Global/IP Rate Limiting
    2. Idempotency (Post-Auth)
    3. Hierarchical Rate Limiting (Tenant/Agent/Token)
    4. Inference Proxy & OPA Policy
    5. Output Redaction
    6. Audit Logging
    """

    def __init__(self, app: FastAPI, redis: Redis) -> None:
        super().__init__(app)
        self.redis = redis
        self.limiter = RateLimiter(redis)
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_EXECUTION)  # Backpressure: cap concurrent requests
        service_client.set_redis(redis)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Sprint 3.2 — open the OpenTelemetry root span for this /execute
        # decision. The span context flows into every downstream client
        # via the FastAPI / httpx instrumentation already loaded by the
        # service boot, and into the 11-stage child spans the middleware
        # emits from this point on. Vendor-neutral by design — Sprint 8
        # ships CloudWatch / Datadog / Grafana exporters that consume the
        # same span tree without any code change here.
        from sdk.common.otel_pipeline import decision_span  # noqa: PLC0415
        request_id_hdr = request.headers.get("X-Request-ID")
        with decision_span(
            request_id=request_id_hdr or "pending",
            tenant_id=request.headers.get("X-Tenant-ID"),
            agent_id=request.headers.get("X-Agent-ID"),
            tool=None,                                        # filled in by stage spans
            session_id=request.headers.get("X-Session-ID"),
        ):
            async with self.semaphore:
                return await self._dispatch_with_resilience(request, call_next)

    async def _dispatch_with_resilience(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start_time = time.time()
        structlog.contextvars.clear_contextvars()

        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        deadline = start_time + _GLOBAL_SLA_BUDGET
        self._init_context(request, request_id, deadline)

        # /auth/sso/* is mostly public (OAuth redirect dance with no
        # Aegis token), but /auth/sso/config + /auth/sso/config/test are
        # the tenant-scoped CRUD endpoints — they MUST stay behind auth
        # so the gateway can stamp X-Tenant-ID + X-ACP-Role from the
        # validated JWT. Skipping auth on them meant the SSO Settings
        # tab in the UI got "Failed to load configuration" whenever the
        # client didn't pre-populate X-Tenant-ID (the new login flow
        # populates it via Clerk session sync, but during the bridge
        # window the header is briefly missing and identity 400s).
        _path = request.url.path
        _prefix_skip = (
            _path.startswith(_SKIP_PATH_PREFIXES)
            and not _path.startswith("/auth/sso/config")
        )
        if _path in _SKIP_PATHS or _prefix_skip:
            return await call_next(request)

        # /metrics: require internal secret. Prometheus inside the VPC
        # sends X-Internal-Secret in its scrape_configs; public ALB
        # callers don't carry it and get 401.
        if _path == "/metrics":
            if request.headers.get("X-Internal-Secret") == settings.INTERNAL_SECRET:
                return await call_next(request)
            from fastapi.responses import JSONResponse as _JSON
            return _JSON(
                {"error": "Unauthorized", "detail": "metrics endpoint is internal-only"},
                status_code=401,
                headers={"WWW-Authenticate": 'X-Internal-Secret realm="metrics"'},
            )

        client_ip = request.client.host if request.client else "unknown"
        t_id_str: str = "unknown"
        agent_id: uuid.UUID = uuid.UUID(int=0)
        tool_name: str = "unknown"
        tokens: int = 1
        tier: str = "basic"
        risk_score: float = 0.0
        reasons: list[str] = []
        action: str = "deny"
        body_hash: str = "empty"

        # Flight Recorder lifecycle state. We track whether a timeline was
        # opened so the `finally` clause can guarantee a matching close even
        # on the early-return paths (security block, autonomy deny,
        # HTTPException) that previously left timelines wedged at
        # status=in_progress forever. `_flight_final` carries the disposition
        # the finally block will emit; each branch that knows the outcome
        # updates it before returning. Defaults intentionally describe a
        # "we never reached the success path" outcome.
        _flight_opened = False
        _flight_closed = False
        _flight_final = {
            "decision": "error",
            "risk":     0.0,
            "status":   "failed",
        }

        try:
            # --- 1. PAYLOAD SIZE CHECK (PHASE 0 - FAIL FAST) ---
            raw_body, body_hash = await self._check_payload_size(request)

            # --- 2. AUTHENTICATION & IDENTITY (PHASE 1) ---
            # MUST be the first line of defense after size check.
            identity = await self._handle_auth_phase(request)
            tenant_id, agent_id, t_id_str, tier = identity
            jti = getattr(request.state, "jti", None)

            # --- 2.1 AUTHORIZATION (Sprint EH-1) ---
            # Centralised path -> role enforcement. Spec lives in
            # docs/security/rbac_matrix.md, code in _rbac_map.py.
            # Runs after auth populates request.state.role so the lookup
            # is against the canonical role (not a client-forged header).
            from services.gateway._rbac_map import is_authorized  # noqa: PLC0415
            _actual_role = getattr(request.state, "role", "READ_ONLY") or "READ_ONLY"
            _ok, _reason = is_authorized(_path, request.method, _actual_role)
            if not _ok:
                _path_prefix = "/" + "/".join(p for p in _path.split("/")[:3] if p)
                RBAC_DENIED_TOTAL.labels(role=_actual_role, path_prefix=_path_prefix).inc()
                logger.warning(
                    "rbac_denied",
                    path=_path, method=request.method,
                    actual_role=_actual_role, reason=_reason,
                    tenant_id=t_id_str,
                )
                from fastapi.responses import JSONResponse as _JSON  # noqa: PLC0415
                return _JSON(
                    {"error": "Forbidden", "detail": _reason},
                    status_code=403,
                )

            # EH-3: mass-export watch. Both /audit/logs/export and
            # /compliance/export are read-once-write-many: a customer
            # legitimately calls each once per audit period (≤1/day).
            # Anything above that is exfil pattern — Prometheus alert
            # picks it up.
            if request.method == "POST" and _path in ("/audit/logs/export", "/compliance/export"):
                MASS_EXPORT_ATTEMPTS_TOTAL.labels(endpoint=_path, tenant_id=t_id_str).inc()

            # --- 2.5 PER-TENANT QUOTA (Sprint 3.2) ---
            # Token-bucket rps+burst + UTC daily/monthly counters from the
            # tenant row. Runs BEFORE idempotency / security so noisy
            # neighbours can't even consume the gateway's CPU on those
            # phases. On block: HTTP 429 with Retry-After, structured
            # body, and an audit row with action="rate_limited" so the
            # operator can see who got throttled and on which limit_type.
            quota_resp = await self._enforce_tenant_quota(
                request, t_id_str, agent_id, request_id,
            )
            if quota_resp is not None:
                return quota_resp

            # --- 3. IDEMPOTENCY (PHASE 2) ---
            idem_resp = await self._check_idempotency(request, t_id_str, body_hash)
            if idem_resp:
                return idem_resp

            # --- 4. MANAGEMENT FAST PATH ---
            is_management = any(request.url.path.startswith(p) for p in _MANAGEMENT_PATH_PREFIXES)
            if is_management:
                tool_name = "management_api"
                action = "allow"
                response = await call_next(request)
                await self._finalize_request(request, response, t_id_str, agent_id, tool_name, body_hash, tier, start_time, request_id, 0.0, 1)
                return response

            # --- 5. SECURITY & POLICY (PHASE 3) ---
            # MUST run before Rate Limiting to ensure 403 blocks take precedence.
            if await self.redis.get(f"acp:tenant_kill:{t_id_str}"):
                action = "kill"
                raise HTTPException(status_code=403, detail="Tenant blocked due to security violation")

            # --- 5.0 SPRINT B — agent quarantine short-circuit ---
            # When a compromised agent is auto-quarantined (runaway loop, slow
            # exfil pattern, manual operator action), every subsequent /execute
            # returns 403 immediately without burning policy/decision/behavior
            # CPU. The flag is a Redis-backed setex so quarantine survives a
            # gateway restart and auto-clears after 24h unless re-armed.
            try:
                from services.gateway._behavior_aggregator import is_quarantined
                _q, _q_reason = await is_quarantined(self.redis, t_id_str, str(agent_id))
            except Exception:
                _q, _q_reason = False, ""
            if _q:
                logger.warning(
                    "agent_quarantine_short_circuit",
                    agent_id=str(agent_id), reason=_q_reason,
                )
                await self._log_audit(
                    t_id_str, agent_id, "execute_tool", "agent_quarantined",
                    "block", _q_reason or "agent quarantined", request_id,
                    {"trigger": _q_reason},
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"agent_quarantined: {_q_reason or 'compromised behavior pattern'}",
                )

            # --- 5a. /execute sliding-window rate limit (sequential burst) ---
            # The 60-second token bucket (10 000 rpm) is too coarse to catch
            # rapid sequential probing. This 10-second INCR window fires first
            # so an attacker sending 31+ requests in 10 seconds gets a 429
            # before any governance CPU is spent. Fail-open on Redis errors.
            if request.url.path.rstrip("/") in ("/execute", "/v1/execute"):
                jti = getattr(request.state, "jti", None)
                sw_resp = await self._check_execute_sliding_window(t_id_str, jti)
                if sw_resp is not None:
                    return sw_resp

            # --- 5b. Agent ID body validation ---
            # Validate that the agent_id in the request body is registered.
            # Uses a 150ms timeout and fails-open (registry downtime must not
            # block real governance traffic). Unregistered agent → 403.
            #
            # Also promotes the body's agent_id into the local `agent_id` variable
            # when the JWT carries no agent (human SECURITY/ADMIN callers). This
            # ensures hard-deny audit records (PII/RCE/SQL) are attributed to the
            # correct agent_id instead of the zero UUID.
            agent_id, _aid_resp = await self._validate_execute_agent_id(
                request, raw_body, agent_id, tenant_id,
            )
            if _aid_resp is not None:
                return _aid_resp

            # --- 5c. Per-agent cost cap (Sprint 5) -----------------------
            _cost_resp = await self._check_per_agent_cost_cap(
                request, agent_id, request_id, _flight_final,
            )
            if _cost_resp is not None:
                return _cost_resp

            tool_name = await self._get_tool_name(request)
            logger.info("policy_check_called", agent_id=str(agent_id), tool=tool_name, tenant_id=t_id_str)
            # Sprint B follow-up: bind tool to contextvars so _deny() can
            # bucket the runaway-loop counter per (agent, tool) instead of
            # blending all tools under "unknown_tool".
            structlog.contextvars.bind_contextvars(tool=tool_name)

            # Flight Recorder: OPEN as soon as we have the canonical tool name.
            # Doing this BEFORE the security/decision/autonomy phases means even
            # blocked requests get a finalised timeline (the previous design
            # only opened after autonomy approval, leaving block paths invisible
            # to the replay UI). The matching close lives in the `finally`
            # clause below — fire-and-forget by design; never blocks /execute.
            # Sprint 3.5 — accept X-Session-ID at the gateway so consecutive
            # /execute calls land in the same Session Explorer row. The header
            # is informational (no auth load); a missing header just means
            # the timeline is not part of a multi-turn session.
            session_id_hdr = request.headers.get("X-Session-ID")
            asyncio.create_task(_safe_bg(emit_timeline_start(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                agent_id=str(agent_id), tool=tool_name,
                metadata={"tier": tier},
                session_id=session_id_hdr,
            )))
            _flight_opened = True

            proxy_res = await self._handle_security_phase(request, tool_name, tenant_id, agent_id, t_id_str, request_id)
            if isinstance(proxy_res, Response):
                # Security phase blocked the request — emit a terminal step
                # so the Flight Recorder shows the rejection cause, then exit.
                asyncio.create_task(_safe_bg(emit_step(
                    self.redis, tenant_id=t_id_str, request_id=request_id,
                    step_index=0, step_type="inference_proxy",
                    summary="security_block", status="deny",
                )))
                _flight_final["decision"] = "block"
                _flight_final["status"]   = "failed"
                return proxy_res

            _raw_tokens = proxy_res.metadata.get("tokens")
            if _raw_tokens is None:
                logger.warning("inference_tokens_missing", request_id=request_id, tenant_id=t_id_str, agent_id=str(agent_id))
                _raw_tokens = 1
            tokens = int(_raw_tokens)
            risk_score = proxy_res.risk_score

            # Sprint 3.5 — inference dollar cap. Token estimate comes from
            # the InputValidator (`len(body)//4`); cost is computed via
            # the configurable price table. Block path returns 429 with
            # limit_type="inference_cost" + audit row
            # action="inference_cost_cap_exceeded".
            cost_resp = await self._enforce_inference_cost_cap(
                request, t_id_str, agent_id, request_id, tokens=tokens,
            )
            if cost_resp is not None:
                _flight_final["decision"] = "block"
                _flight_final["status"]   = "failed"
                return cost_resp

            # Flight step — inference proxy passed. Records risk floor and
            # any inference-side flags so replay shows the full reasoning
            # chain instead of a single "decision" event.
            asyncio.create_task(_safe_bg(emit_step(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=0, step_type="inference_proxy",
                summary=f"inference_pass risk={risk_score:.3f}",
                payload={"flags": list(getattr(proxy_res, "flags", []) or [])[:8]},
                risk_score=risk_score, status="ok",
            )))

            # Extract tool parameters for security checks and metadata.
            # Scans BOTH body["input"] (ACP execute format) and body["parameters"]
            # (legacy SDK format) so hard-deny rules fire regardless of which
            # field the caller uses.
            tool_metadata = {}
            try:
                import re as _re
                if raw_body:
                    body_dict = json.loads(raw_body)
                    if isinstance(body_dict, dict):
                        # Collect all string values from input, parameters, payload,
                        # AND arguments — the canonical /execute envelope is
                        # `{tool, arguments}` (as the gateway docs + the
                        # integration SDKs all use). Without `arguments` in this
                        # list, every action-semantics check downstream sees an
                        # empty arg block and lets destructive calls through.
                        _input_dict = body_dict.get("input") or {}
                        _params_dict = body_dict.get("parameters") or {}
                        _payload_dict = body_dict.get("payload") or {}
                        _args_dict   = body_dict.get("arguments") or {}
                        _all_params: dict = {}
                        if isinstance(_input_dict, dict):
                            _all_params.update(_input_dict)
                        if isinstance(_params_dict, dict):
                            _all_params.update(_params_dict)
                        if isinstance(_payload_dict, dict):
                            _all_params.update(_payload_dict)
                        if isinstance(_args_dict, dict):
                            _all_params.update(_args_dict)
                        # Also check top-level string fields (query, sql, path)
                        for _top_k in ("query", "sql", "path", "command", "cmd"):
                            _top_v = body_dict.get(_top_k)
                            if isinstance(_top_v, str):
                                _all_params.setdefault(_top_k, _top_v)

                        _PATH_FIELDS = ("path", "file_path", "filename", "src", "dst", "destination", "target", "uri", "url")
                        _SQL_FIELDS  = ("query", "sql", "statement", "command", "q")
                        _CODE_FIELDS = ("code", "script", "source", "program", "exec", "cmd", "shell", "bash", "python", "js")
                        _TEXT_FIELDS = ("body", "content", "text", "message", "subject", "description", "note", "email_body", "payload")

                        for _k, _v in _all_params.items():
                            if not isinstance(_v, str) or not _v:
                                continue

                            # --- PATH TRAVERSAL HARD DENY ---
                            _decoded_v = urllib.parse.unquote(_v).replace("\\", "/")
                            _vl = _v.lower()
                            _looks_path = _k.lower() in _PATH_FIELDS or _v.startswith("/") or "../" in _v
                            if _looks_path:
                                _path_attack = (
                                    "../" in _v
                                    or "../" in _decoded_v
                                    or "..%2f" in _vl
                                    or "..%5c" in _vl
                                    or "\x00" in _v
                                    or _decoded_v.startswith("/etc")
                                    or _decoded_v.startswith("/root")
                                    or _decoded_v.startswith("/proc")
                                    or _decoded_v.startswith("/sys")
                                    or _v.startswith("/etc")
                                    or _v.startswith("/root")
                                    or _v.startswith("/proc")
                                    or _v.startswith("/sys")
                                )
                                if _path_attack:
                                    logger.warning(
                                        "path_traversal_blocked",
                                        tool=tool_name, field=_k, path=_v[:100], request_id=request_id,
                                    )
                                    _m = _re.match(r"((?:\.\./)+)", _v)
                                    _pt = _m.group(1) if _m else (_v[:20] if _v.startswith("/") else "../")
                                    # ARCH-4 2026-06-15 — pre-policy file-read
                                    # denies (path traversal + sensitive paths)
                                    # must also surface canonical findings +
                                    # policy_id so the SOC sees WHY. Before,
                                    # /etc/passwd blocked with findings=[].
                                    _pre_finding = (
                                        "system_sensitive_path" if (
                                            _decoded_v.startswith(("/etc/", "/proc/", "/sys/"))
                                            or _v.startswith(("/etc/", "/proc/", "/sys/"))
                                        ) else
                                        "cloud_credential_path" if (
                                            "aws" in _vl or "kube" in _vl or "docker" in _vl
                                        ) else
                                        "ssh_credential_path" if (
                                            "/root/" in _vl or ".ssh" in _vl or "id_rsa" in _vl
                                        ) else
                                        "path_traversal_detected"
                                    )
                                    _pre_policy_id = {
                                        "system_sensitive_path":   "SEC-PATH-001",
                                        "cloud_credential_path":   "SEC-CRED-001",
                                        "ssh_credential_path":     "SEC-CRED-001",
                                        "path_traversal_detected": "SEC-PATH-002",
                                    }[_pre_finding]
                                    resp = self._deny(
                                        f"Security: Path traversal detected: '{_pt}'", 403,
                                        findings=[_pre_finding],
                                        reason=_pre_finding,
                                        policy_id=_pre_policy_id,
                                        risk_score=95,
                                        explanation=f"Pre-policy block: '{_v[:80]}' matches {_pre_finding}.",
                                    )
                                    await self._log_audit(
                                        t_id_str, agent_id, "execute_tool", tool_name, "block",
                                        # Use the canonical signal id (one of
                                        # system_sensitive_path / cloud_credential_path /
                                        # ssh_credential_path / path_traversal_detected)
                                        # so /logs/agent-findings + the IAG MITRE coverage
                                        # endpoint can roll this row up by tactic.
                                        _pre_finding, request_id,
                                        {"blocked_field": _k, "blocked_path": _v[:100],
                                         "policy_id":     _pre_policy_id},
                                    )
                                    await self._emit_groq_event(
                                        event_id=request_id, tenant_id=t_id_str,
                                        agent_id=str(agent_id), tool=tool_name,
                                        decision="block", risk_score=1.0,
                                        signals={"blocked_field": _k},
                                        reasons=["path_traversal_detected"],
                                        source="path_traversal_hard_deny",
                                    )
                                    _flight_final["decision"] = "block"
                                    _flight_final["risk"]     = 1.0
                                    _flight_final["status"]   = "failed"
                                    return resp

                            # --- SQL INJECTION HARD DENY ---
                            # Targets real injection signatures only, not every
                            # SELECT ... FROM (which would block any legit query).
                            # Patterns:
                            #   1. Stacked statement: ; followed by DROP/DELETE/UNION/EXEC/etc
                            #   2. UNION-based: UNION (ALL) SELECT
                            #   3. Boolean blind: OR/AND <digit>=<digit>  or  OR '1'='1'
                            #   4. Comment evasion: -- or /* */ followed by destructive keyword
                            #   5. Quote-break with terminator: '; or "; followed by --/SQL keyword
                            #   6. Explicit DROP/TRUNCATE TABLE/DATABASE/SCHEMA
                            #   7. SQL Server extended procs: xp_cmdshell, sp_executesql
                            _looks_sql = _k.lower() in _SQL_FIELDS
                            if _looks_sql:
                                _SQL_PATTERN = _re.compile(
                                    r"(;\s*(DROP|TRUNCATE|DELETE|INSERT|UPDATE|UNION|EXEC|EXECUTE|CREATE|ALTER|SHUTDOWN|GRANT|REVOKE)\b"
                                    r"|\bUNION\s+(ALL\s+)?SELECT\b"
                                    r"|\b(OR|AND)\b\s+['\"]?\s*\d+\s*['\"]?\s*=\s*['\"]?\s*\d+\s*['\"]?"
                                    r"|\b(OR|AND)\b\s+['\"]\s*\w*\s*['\"]\s*=\s*['\"]\s*\w*\s*['\"]"
                                    r"|--[^\n]*\b(DROP|TRUNCATE|DELETE|UNION|EXEC|CREATE|ALTER|GRANT)\b"
                                    r"|/\*.*?\*/.*?\b(DROP|TRUNCATE|DELETE|UNION|EXEC|CREATE|ALTER)\b"
                                    r"|['\"];\s*(--|DROP|TRUNCATE|DELETE|INSERT|UNION|EXEC|EXECUTE|CREATE|ALTER)"
                                    r"|\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA|INDEX)\b"
                                    r"|\b(xp_cmdshell|sp_executesql|xp_regwrite|xp_regread)\b"
                                    r"|\bSELECT\b[^;]*\bFROM\b\s+\w+\s+WHERE\s+1\s*=\s*1\b)",
                                    _re.IGNORECASE | _re.DOTALL,
                                )
                                if _SQL_PATTERN.search(_v):
                                    logger.warning(
                                        "sql_injection_blocked",
                                        tool=tool_name, field=_k, value=_v[:100], request_id=request_id,
                                    )
                                    resp = self._deny("Security: SQL injection detected in tool input", 403)
                                    await self._log_audit(
                                        t_id_str, agent_id, "execute_tool", tool_name, "block",
                                        "sql_injection_detected", request_id,
                                        {"blocked_field": _k, "blocked_value": _v[:100]},
                                    )
                                    await self._emit_groq_event(
                                        event_id=request_id, tenant_id=t_id_str,
                                        agent_id=str(agent_id), tool=tool_name,
                                        decision="block", risk_score=1.0,
                                        signals={"blocked_field": _k},
                                        reasons=["sql_injection_detected"],
                                        source="sql_injection_hard_deny",
                                    )
                                    _flight_final["decision"] = "block"
                                    _flight_final["risk"]     = 1.0
                                    _flight_final["status"]   = "failed"
                                    return resp

                            # --- K8S DESTRUCTIVE OPS ON PRODUCTION HARD DENY ---
                            # Fires when a kubectl/k8s delete tool targets a
                            # production-class namespace OR a broad resource
                            # selector ("all", "*", "deployments"+namespace).
                            # Catches the "delete all in production" demo case
                            # and any future DevOps-agent destructive blast-radius
                            # attempt regardless of OPA bundle state.
                            _tool_l = tool_name.lower()
                            _is_k8s_destructive = (
                                _tool_l in ("kubectl_delete", "k8s_delete", "kubectl_drain")
                                or _tool_l.startswith("k8s.delete.")
                                or _tool_l.startswith("kubectl.delete")
                            )
                            if _is_k8s_destructive:
                                # Aggregate the WHOLE payload so we catch nested params
                                _payload_str = json.dumps(body_dict, default=str).lower()
                                _PROD_NS_PATTERN = _re.compile(
                                    r'"namespace"\s*:\s*"[^"]*(prod(uction)?|prd|live)[^"]*"',
                                    _re.IGNORECASE,
                                )
                                _BROAD_RESOURCE_PATTERN = _re.compile(
                                    r'"resource"\s*:\s*"(all|\*|deployments|services|nodes|namespaces|secrets)"',
                                    _re.IGNORECASE,
                                )
                                _hits_prod = bool(_PROD_NS_PATTERN.search(_payload_str))
                                _hits_broad = bool(_BROAD_RESOURCE_PATTERN.search(_payload_str))
                                if _hits_prod or _hits_broad:
                                    reason_summary = (
                                        "destructive k8s op on production namespace" if _hits_prod
                                        else "destructive k8s op with broad resource selector"
                                    )
                                    logger.warning(
                                        "k8s_destructive_blocked",
                                        tool=tool_name,
                                        prod_namespace=_hits_prod,
                                        broad_resource=_hits_broad,
                                        request_id=request_id,
                                    )
                                    resp = self._deny(f"Security: {reason_summary}", 403)
                                    await self._log_audit(
                                        t_id_str, agent_id, "execute_tool", tool_name, "block",
                                        "k8s_destructive_pattern_detected", request_id,
                                        {"prod_namespace": _hits_prod, "broad_resource": _hits_broad},
                                    )
                                    await self._emit_groq_event(
                                        event_id=request_id, tenant_id=t_id_str,
                                        agent_id=str(agent_id), tool=tool_name,
                                        decision="block", risk_score=1.0,
                                        signals={"prod_namespace": _hits_prod, "broad_resource": _hits_broad},
                                        reasons=["k8s_destructive_pattern_detected"],
                                        source="k8s_destructive_hard_deny",
                                    )
                                    _flight_final["decision"] = "block"
                                    _flight_final["risk"]     = 1.0
                                    _flight_final["status"]   = "failed"
                                    return resp

                            # --- RCE / DANGEROUS CODE HARD DENY ---
                            _looks_code = _k.lower() in _CODE_FIELDS
                            if _looks_code:
                                _RCE_PATTERN = _re.compile(
                                    r"(os\.system\s*\(|subprocess\.|exec\s*\(|eval\s*\(|__import__\s*\(|"
                                    r"rm\s+-rf\s+/|mkfs\s*\.|dd\s+if=/dev/zero|curl\s+.*\|\s*sh|wget\s+.*\|\s*sh|"
                                    r"chmod\s+777|/etc/shadow|/etc/sudoers|nc\s+-[lnvke]|netcat\s+-|"
                                    r"base64\s+-d\s*\||python\s+-c\s*['\"]import\s+os|"
                                    r"powershell\s+-[eE]|cmd\.exe\s*/[cC]|&\s*\w+\s*=\s*\w+\s*&)",
                                    _re.IGNORECASE | _re.DOTALL,
                                )
                                if _RCE_PATTERN.search(_v):
                                    logger.warning(
                                        "rce_blocked",
                                        tool=tool_name, field=_k, value=_v[:80], request_id=request_id,
                                    )
                                    resp = self._deny("Security: Dangerous code pattern detected in tool input", 403)
                                    await self._log_audit(
                                        t_id_str, agent_id, "execute_tool", tool_name, "block",
                                        "rce_pattern_detected", request_id,
                                        {"blocked_field": _k, "blocked_value": _v[:80]},
                                    )
                                    await self._emit_groq_event(
                                        event_id=request_id, tenant_id=t_id_str,
                                        agent_id=str(agent_id), tool=tool_name,
                                        decision="block", risk_score=1.0,
                                        signals={"blocked_field": _k},
                                        reasons=["rce_pattern_detected"],
                                        source="rce_hard_deny",
                                    )
                                    _flight_final["decision"] = "block"
                                    _flight_final["risk"]     = 1.0
                                    _flight_final["status"]   = "failed"
                                    return resp

                            # --- PII EXFILTRATION HARD DENY ---
                            _looks_text = _k.lower() in _TEXT_FIELDS or tool_name.lower() in ("send_email", "send_message", "post_slack", "webhook", "http_request", "send_notification")
                            if _looks_text:
                                _PII_PATTERN = _re.compile(
                                    r"(\b\d{3}-\d{2}-\d{4}\b"                           # SSN
                                    r"|\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b"  # credit card
                                    r"|\b[A-Z]{2}\d{6}[A-Z]?\b"                          # passport-like
                                    r"|(?:password|passwd|secret|api[_\-]?key)\s*[:=]\s*\S+"  # credential leak
                                    r"|\bDOB\s*:\s*\d{2}[/-]\d{2}[/-]\d{4}\b"           # DOB
                                    r"|\b(?:SSN|social.security)\s*:?\s*\d{3}[-\s]\d{2}[-\s]\d{4}\b"  # SSN label
                                    r"|\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b.*\b(?:password|pwd|token)\b"  # email+password combo
                                    r")",
                                    _re.IGNORECASE | _re.DOTALL,
                                )
                                if _PII_PATTERN.search(_v):
                                    logger.warning(
                                        "pii_exfiltration_blocked",
                                        tool=tool_name, field=_k, request_id=request_id,
                                    )
                                    resp = self._deny("Security: PII or credential data detected in tool output", 403)
                                    await self._log_audit(
                                        t_id_str, agent_id, "execute_tool", tool_name, "block",
                                        "pii_exfiltration_detected", request_id,
                                        {"blocked_field": _k},
                                    )
                                    await self._emit_groq_event(
                                        event_id=request_id, tenant_id=t_id_str,
                                        agent_id=str(agent_id), tool=tool_name,
                                        decision="block", risk_score=1.0,
                                        signals={"blocked_field": _k},
                                        reasons=["pii_exfiltration_detected"],
                                        source="pii_hard_deny",
                                    )
                                    _flight_final["decision"] = "block"
                                    _flight_final["risk"]     = 1.0
                                    _flight_final["status"]   = "failed"
                                    return resp

                        # Populate tool_metadata for downstream signals
                        if "path" in _all_params:
                            tool_metadata["path"] = _all_params["path"]
                        for _sk in _SQL_FIELDS:
                            if _sk in _all_params:
                                tool_metadata["sql"] = _all_params[_sk]
                                break

                        # R0 — Sprint refactor: pass the whole action shape
                        # to OPA via metadata.arguments so the rego rule can
                        # key destructive denials off CONTENT (the command,
                        # the query, the path, the URL), not off tool name
                        # or hardcoded agent risk level.
                        #
                        # _normalize_for_match: lowercase + strip SQL inline
                        # comments + collapse whitespace + URL-decode once.
                        # Defeats `DROP/**/TABLE`, `DrOp%20TaBlE`, `rm  -rf`.
                        def _normalize_for_match(s: str) -> str:
                            if not isinstance(s, str):
                                return ""
                            try:
                                _s = urllib.parse.unquote(s)
                            except Exception:
                                _s = s
                            _s = _re.sub(r"/\*.*?\*/", " ", _s, flags=_re.DOTALL)
                            _s = _re.sub(r"--[^\n]*", " ", _s)
                            _s = _re.sub(r"\s+", " ", _s)
                            return _s.strip().lower()

                        _cmd = next(
                            (str(_all_params[k]) for k in ("command", "cmd", "shell", "bash")
                             if isinstance(_all_params.get(k), str)),
                            "",
                        )
                        _qry = next(
                            (str(_all_params[k]) for k in _SQL_FIELDS
                             if isinstance(_all_params.get(k), str)),
                            "",
                        )
                        _url = next(
                            (str(_all_params[k]) for k in ("url", "uri", "endpoint")
                             if isinstance(_all_params.get(k), str)),
                            "",
                        )
                        # final-sprint v3 R0 deep-fix: row_limit + k8s namespace
                        # ─────────────────────────────────────────────────────────────
                        # row_limit: parsed from SQL `LIMIT N` so the rego bulk-PII
                        #   rule can deny when the LLM does not bound the read.
                        #   -1 sentinel = no explicit LIMIT in query (treat as
                        #   "unbounded" = exceeds every risk-level threshold).
                        # k8s_namespace: substring extracted from kubectl/helm
                        #   commands so the rego prod-namespace rule can fire on
                        #   prod-shaped targets only, instead of blanket-denying
                        #   every `kubectl delete`.
                        _qry_norm = _normalize_for_match(_qry)
                        _row_limit: int = -1
                        _m_limit = re.search(r"\blimit\s+(\d+)", _qry_norm)
                        if _m_limit:
                            try:
                                _row_limit = int(_m_limit.group(1))
                            except ValueError:
                                _row_limit = -1
                        _cmd_norm = _normalize_for_match(_cmd)
                        _k8s_ns = ""
                        # `kubectl delete ns <name>` / `kubectl delete namespace <name>` /
                        # `kubectl -n <name> delete ...` / `helm uninstall <release> -n <name>`.
                        for _pat in (
                            r"kubectl\s+(?:-n|--namespace=?)\s+(\S+)",
                            r"kubectl\s+delete\s+(?:ns|namespace)\s+(\S+)",
                            r"kubectl\s+delete\s+\S+\s+(?:-n|--namespace=?)\s+(\S+)",
                            r"helm\s+(?:uninstall|delete)\s+\S+(?:\s+-n|\s+--namespace=?)\s+(\S+)",
                        ):
                            _mns = re.search(_pat, _cmd_norm)
                            if _mns:
                                _k8s_ns = _mns.group(1).strip("'\"")
                                break

                        # SPRINT enterprise-grade 2026-06-14:
                        # Action-normalization fields. Convert the canonical
                        # SDK tool call into business-intent fields so the
                        # action-semantics layer can ladder by intent, not
                        # by raw string match.
                        #
                        # k8s_verb: get / logs / scale / delete / drain / …
                        _k8s_verb = ""
                        _mkv = re.search(r"kubectl\s+(\w+)", _cmd_norm)
                        if _mkv:
                            _k8s_verb = _mkv.group(1)

                        # iac_tool + iac_action: terraform/pulumi/cdk +
                        # apply/plan/destroy/down. Tier-1 destructive.
                        _iac_tool = ""
                        _iac_action = ""
                        _mi = re.search(r"\b(terraform|pulumi|cdk)\s+(\w[\w\-]*)", _cmd_norm)
                        if _mi:
                            _iac_tool = _mi.group(1)
                            _iac_action = _mi.group(2)
                        elif " cloudformation " in _cmd_norm:
                            _mi2 = re.search(r"cloudformation\s+(\S+)", _cmd_norm)
                            if _mi2:
                                _iac_tool = "aws cloudformation"
                                _iac_action = _mi2.group(1)

                        # amount_usd: pull a numeric amount from common body
                        # field names — money-movement http_request shapes.
                        # ALSO recurse one level into `body` because canonical
                        # http_request callers nest the payload there.
                        _amount_usd: int = 0
                        _amt_search_scopes = [_all_params]
                        _nested_body = _all_params.get("body")
                        if isinstance(_nested_body, dict):
                            _amt_search_scopes.append(_nested_body)
                        for _scope in _amt_search_scopes:
                            if _amount_usd:
                                break
                            for _amt_k in ("amount_usd", "amount", "value",
                                           "total", "settlement_amount"):
                                _amt_v = _scope.get(_amt_k)
                                if isinstance(_amt_v, (int, float)):
                                    _amount_usd = int(_amt_v)
                                    break
                                if isinstance(_amt_v, str):
                                    _amt_clean = re.sub(r"[\$,\s]", "", _amt_v)
                                    try:
                                        _amount_usd = int(float(_amt_clean))
                                        break
                                    except ValueError:
                                        continue

                        # recipient_domain + recipient_kind. Recipient kind
                        # is "external"/"offshore" if the recipient string
                        # carries those tokens; "internal" if it matches a
                        # canonical internal pattern; else "unknown".
                        _recipient_dom = ""
                        _url_host_match = re.search(
                            r"https?://([^/]+)", _url or "")
                        if _url_host_match:
                            _recipient_dom = _url_host_match.group(1).lower()
                        if not _recipient_dom:
                            for _rk in ("to", "recipient", "destination",
                                        "beneficiary"):
                                _rv = _all_params.get(_rk)
                                if isinstance(_rv, str) and "@" in _rv:
                                    _recipient_dom = _rv.split("@", 1)[1].lower()
                                    break
                        _recipient_kind = "unknown"
                        _payload_blob = (
                            json.dumps(_all_params, default=str).lower()
                        )
                        if any(t in _payload_blob for t in
                                ("offshore", "external", "beneficiary-offshore")):
                            _recipient_kind = "offshore"
                        elif any(t in _payload_blob for t in
                                ("internal", "acme-ops", "@apexbank.internal")):
                            _recipient_kind = "internal"

                        # contains_pii: heuristic on column names + body
                        # blob for ssn / credit_card / dob / passport.
                        _pii_markers = (
                            "ssn", "social_security_number",
                            "credit_card", "creditcard", "card_number",
                            "passport", "drivers_license", "tax_id",
                            "date_of_birth", "dob ",
                            "medical_record", "diagnosis", "phi",
                        )
                        _contains_pii = any(
                            m in _qry_norm or m in _payload_blob
                            for m in _pii_markers
                        )

                        # SPRINT B 2026-06-14 (L3 behavior) — slow-exfil
                        # detector. Aggregates row_count over a 1h sliding
                        # window per (tenant, agent, table). L2 only saw
                        # `row_limit` per call; this is the cumulative.
                        from services.gateway._behavior_aggregator import (
                            extract_table_norm, record_and_sum_rows,
                        )
                        _table_norm = extract_table_norm(_qry_norm)
                        _cumulative_rows_1h = 0
                        if _table_norm and _row_limit > 0:
                            try:
                                _cumulative_rows_1h = await record_and_sum_rows(
                                    self.redis, t_id_str, agent_id,
                                    _table_norm, _row_limit,
                                )
                            except Exception as _bx:
                                logger.warning(
                                    "behavior_agg_unavailable", error=str(_bx),
                                )

                        # ADR-shift 2026-06-15 (L4 session intelligence) —
                        # classify this action and check if the trailing
                        # session sequence forms a known attack chain.
                        # When it does, inject `attack_chain` into the
                        # tool_metadata so the policy layer can deny based
                        # on the kill chain, not just the tool call.
                        _attack_chain = ""
                        _attack_chain_severity = ""
                        _action_class = "benign"
                        try:
                            from services.gateway._session_intelligence import (
                                classify_action, match_attack_chain,
                                record_session_action,
                            )
                            _action_class = classify_action(
                                tool=tool_name,
                                query_norm=_qry_norm,
                                command_norm=_cmd_norm,
                                path=str(_all_params.get("path", "")),
                                url=_url,
                                raw_norm=_normalize_for_match(
                                    json.dumps(_all_params, default=str)[:2000]),
                                row_limit=_row_limit,
                                contains_pii=False,  # filled below
                            )
                            _session_id_hdr = request.headers.get("X-Session-ID") or ""
                            if _session_id_hdr:
                                _seq = await record_session_action(
                                    self.redis,
                                    session_id=_session_id_hdr,
                                    action_class=_action_class,
                                )
                                _chain = match_attack_chain(_seq)
                                if _chain is not None:
                                    _attack_chain, _attack_chain_severity = _chain
                                    logger.critical(
                                        "session_attack_chain_detected",
                                        session_id=_session_id_hdr,
                                        chain=_attack_chain,
                                        severity=_attack_chain_severity,
                                        agent_id=str(agent_id),
                                    )
                                    # ADR-shift 2026-06-15 (P2) — when the
                                    # chain hits `deny` severity (clear
                                    # exfiltration intent), auto-contain
                                    # the agent in addition to denying this
                                    # call. The cascade: Redis quarantine
                                    # flag set, audit row emitted, incident
                                    # published, SOC webhook fired (the
                                    # existing autonomy webhook pipeline
                                    # already handles Slack / PagerDuty).
                                    if _attack_chain_severity == "deny":
                                        try:
                                            from services.gateway._behavior_aggregator import (
                                                quarantine_agent,
                                            )
                                            await quarantine_agent(
                                                self.redis, t_id_str, str(agent_id),
                                                f"attack_chain:{_attack_chain}",
                                            )
                                            # incident publish + SOC notify
                                            await self._log_audit(
                                                t_id_str, agent_id,
                                                "agent_quarantined",
                                                tool_name, "block",
                                                f"attack_chain_auto_contain:{_attack_chain}",
                                                request_id,
                                                {"chain": _attack_chain,
                                                 "severity": _attack_chain_severity,
                                                 "session_id": _session_id_hdr},
                                            )
                                            asyncio.create_task(_safe_bg(
                                                service_client.publish_incident_event(
                                                    tenant_id=t_id_str,
                                                    agent_id=str(agent_id),
                                                    severity="CRITICAL",
                                                    trigger="attack_chain_detected",
                                                    title=f"Attack chain auto-contained: {_attack_chain}",
                                                    risk_score=1.0,
                                                    tool=tool_name,
                                                    request_id=request_id,
                                                    reasons=[
                                                        f"chain:{_attack_chain}",
                                                        f"session:{_session_id_hdr[:16]}",
                                                    ],
                                                )))
                                        except Exception as _qx:
                                            logger.warning(
                                                "auto_contain_failed",
                                                error=str(_qx),
                                            )
                        except Exception as _six:
                            logger.warning("session_intel_failed", error=str(_six))

                        # ADR-shift 2026-06-15 (P1 baseline) — every call
                        # also bumps the per-agent baseline (tool freq,
                        # hour-of-day, daily count, target table) and
                        # surfaces deviation findings (unusual_tool,
                        # unusual_hour, burst_3sigma, unusual_target).
                        _baseline_findings: list[str] = []
                        try:
                            from services.behavior._baseline import (
                                record_and_score,
                                record_risk_score,
                            )
                            _baseline_findings = await record_and_score(
                                self.redis,
                                tenant_id=t_id_str,
                                agent_id=str(agent_id),
                                tool=tool_name,
                                table_norm=_table_norm or None,
                            )
                            # ARCH-5 2026-06-15 — also record the per-call
                            # inherent risk score and surface drift findings
                            # when the agent's behaviour deviates from its
                            # 100-call rolling baseline by >3σ.
                            try:
                                from services.policy.canonical import normalize as _cn_for_drift
                                _c_for_drift = _cn_for_drift(tool_name, _all_params)
                                _inh = int(_c_for_drift.get("risk_score_inherent") or 0)
                                _drift = await record_risk_score(
                                    self.redis,
                                    agent_id=str(agent_id),
                                    risk_score=_inh,
                                )
                                _baseline_findings.extend(_drift)
                            except Exception as _drx:
                                logger.warning("baseline_drift_failed", error=str(_drx))
                        except Exception as _bx:
                            logger.warning("baseline_record_failed", error=str(_bx))

                        tool_metadata["arguments"] = {
                            "command":         _cmd,
                            "command_norm":    _cmd_norm,
                            "query":           _qry,
                            "query_norm":      _qry_norm,
                            "path":            _all_params.get("path", ""),
                            "url":             _url,
                            "raw_norm":        _normalize_for_match(
                                json.dumps(_all_params, default=str)[:2000]
                            ),
                            "row_limit":       _row_limit,
                            "table_norm":      _table_norm,
                            "cumulative_rows_1h": _cumulative_rows_1h,
                            "k8s_namespace":   _k8s_ns,
                            "k8s_verb":        _k8s_verb,
                            "iac_tool":        _iac_tool,
                            "iac_action":      _iac_action,
                            "amount_usd":      _amount_usd,
                            "recipient_domain": _recipient_dom,
                            "recipient_kind":  _recipient_kind,
                            "contains_pii":    _contains_pii,
                            # ADR-shift 2026-06-15 — surface the
                            # session-intel classification + chain match
                            # so the policy layer can deny on the kill
                            # chain instead of the individual tool call.
                            "action_class":    _action_class,
                            "attack_chain":    _attack_chain,
                            "attack_chain_severity": _attack_chain_severity,
                            "baseline_findings": _baseline_findings,
                        }
                        # ARCH-1 2026-06-15 — Canonical Action Model.
                        # Attach a single normalized view of WHAT this tool
                        # call is actually trying to do, regardless of which
                        # tool name or argument shape the SDK used. Policy
                        # rules read canonical.* instead of fishing through
                        # raw arg paths. Closes the entire class of
                        # "rule reads x.y, gateway puts at x.z" bugs.
                        try:
                            from services.policy.canonical import normalize as _canonical_normalize
                            _canonical = _canonical_normalize(tool_name, _all_params)
                            # Carry the session-intel chain + baseline into
                            # the canonical view so all signals live in one
                            # bag.
                            if _attack_chain:
                                _canonical["attack_chain"] = _attack_chain
                                _canonical["attack_chain_severity"] = _attack_chain_severity
                            if _baseline_findings:
                                _canonical["baseline_findings"] = _baseline_findings
                            tool_metadata["arguments"]["canonical"] = _canonical
                        except Exception as _cnx:
                            logger.warning("canonical_normalize_failed", error=str(_cnx))

                        # Surface behavior-side findings to the LiveFeed as
                        # `behavior_flagged` whenever the baseline + canonical
                        # evaluation produced ANY non-empty finding —
                        # independent of the policy verdict — so operators
                        # see the signal even when the decision engine still
                        # allows. Fire-and-forget; never delays the request.
                        _behavior_flags = list(_baseline_findings or [])
                        if _attack_chain:
                            _behavior_flags.append(f"attack_chain:{_attack_chain}")
                        if _behavior_flags:
                            asyncio.create_task(_safe_bg(publish_event(
                                self.redis, t_id_str, "behavior_flagged",
                                {
                                    "agent_id":   str(agent_id),
                                    "tool":       tool_name,
                                    "flags":      _behavior_flags[:8],
                                    "attack_chain": _attack_chain or None,
                                    "attack_chain_severity": _attack_chain_severity or None,
                                    "request_id": request_id,
                                },
                                agent_id=str(agent_id),
                            )))

                        # GAP-5 2026-06-15 — Cross-agent kill-chain detector.
                        # Record this action_type + target into the per-tenant
                        # window. If the trailing 15min contains a kill chain
                        # across 2+ distinct agents, stamp `cross_agent_chain`
                        # into the canonical so the policy engine quarantines.
                        try:
                            from services.policy.cross_agent_correlation import (
                                record_action as _xa_record,
                                detect_chain as _xa_detect,
                                derive_target_key as _xa_target,
                                flag_agents as _xa_flag,
                                is_flagged as _xa_is_flagged,
                            )
                            _xa_already = await _xa_is_flagged(
                                self.redis, t_id_str, str(agent_id),
                            )
                            await _xa_record(
                                self.redis,
                                tenant_id=t_id_str,
                                agent_id=str(agent_id),
                                action_type=_canonical.get("action_type") or "",
                                target_key=_xa_target(_canonical),
                                pii_present=bool(_canonical.get("contains_pii_columns")),
                            )
                            # GAP-5 v4 — only consult the detector when the
                            # current call itself looks like exfil (the
                            # completing step IS the data leaving). A
                            # benign external POST (S3 archive, weather
                            # API, internal-API POST) doesn't complete a
                            # kill chain even if other tenant agents did
                            # PII reads earlier.
                            _cur_findings = set(_canonical.get("signal_findings") or [])
                            _is_exfil_completing = bool(_cur_findings & {
                                "known_exfil_destination",
                                "external_pii_exfil",
                                "external_post_pii_unknown_dest",
                            })
                            if _is_exfil_completing:
                                _xa_chain = await _xa_detect(
                                    self.redis, tenant_id=t_id_str,
                                    current_agent_id=str(agent_id),
                                    current_action_type=_canonical.get("action_type") or "",
                                    current_target_key=_xa_target(_canonical),
                                )
                            else:
                                _xa_chain = None
                            if _xa_already or _xa_chain:
                                _canonical["cross_agent_chain"] = _xa_chain or {"already_flagged": True}
                                sigfs = list(_canonical.get("signal_findings") or [])
                                if "cross_agent_kill_chain" not in sigfs:
                                    sigfs.append("cross_agent_kill_chain")
                                    _canonical["signal_findings"] = sigfs
                                    _canonical["risk_score_inherent"] = max(
                                        int(_canonical.get("risk_score_inherent") or 0),
                                        95,
                                    )
                            if _xa_chain and not _xa_already:
                                # Mark every participating agent so subsequent
                                # calls from any of them also short-circuit.
                                await _xa_flag(
                                    self.redis, t_id_str, _xa_chain.get("agent_ids") or [],
                                )
                        except Exception as _xax:
                            logger.warning("cross_agent_corr_failed", error=str(_xax))

                        # ARCH-2 2026-06-15 — Signal → Finding → Risk pipeline.
                        # Record this call's inherent findings into the
                        # session + agent risk buckets, then read the
                        # cumulative score. Policy reads metadata.cumulative
                        # and folds it into the tier decision.
                        try:
                            from services.policy.risk_pipeline import (
                                record_signals as _rp_record,
                                cumulative_scores as _rp_scores,
                                combine_scores as _rp_combine,
                                tier_from_score as _rp_tier,
                                explain_cumulative as _rp_explain,
                            )
                            _per_call_findings = list(_canonical.get("signal_findings") or [])
                            if _attack_chain:
                                _per_call_findings.append(f"attack_chain:{_attack_chain}")
                            await _rp_record(
                                self.redis,
                                tenant_id=t_id_str,
                                agent_id=str(agent_id),
                                session_id=_session_id_hdr or None,
                                findings=_per_call_findings,
                            )
                            # GAP-2 — 4-value return: session, agent (60min), agent_long (7d), recent.
                            _ss, _as, _al, _recent = await _rp_scores(
                                self.redis,
                                tenant_id=t_id_str,
                                agent_id=str(agent_id),
                                session_id=_session_id_hdr or None,
                            )
                            _per_call = int(_canonical.get("risk_score_inherent") or 0)
                            _effective = _rp_combine(_per_call, _ss, _as, _al)
                            _cum_tier = _rp_tier(_effective)
                            tool_metadata["arguments"]["cumulative"] = {
                                "per_call_score":  _per_call,
                                "session_score":   _ss,
                                "agent_score":     _as,
                                "agent_long_score": _al,
                                "effective_score": _effective,
                                "tier":            _cum_tier,
                                "recent_findings": _recent,
                                "explanation":     _rp_explain(
                                    _per_call, _ss, _as, _effective, _cum_tier, _recent, _al,
                                ),
                            }
                        except Exception as _rpx:
                            logger.warning("risk_pipeline_failed", error=str(_rpx))
            except json.JSONDecodeError:
                pass

            tool_metadata["degraded_mode_policy"] = getattr(
                request.state, "degraded_mode_policy", "block_high_risk"
            )
            decision_data = await service_client.evaluate_decision({
                "tenant_id":      str(tenant_id),
                "agent_id":       str(agent_id),
                "tool":           tool_name,
                "tokens":         tokens,
                "inference_risk": risk_score,
                "inference_flags": proxy_res.flags,
                "request_id":     request_id,
                "payload_hash":   proxy_res.prompt_hash,
                "client_ip":      client_ip,
                "metadata":       tool_metadata,
            })
            decision = Decision(**(decision_data or {"action": "allow", "risk": 0.0}))
            request.state.decision = decision
            action = decision.action.value if hasattr(decision.action, "value") else str(decision.action)
            reasons = decision.reasons

            # Flight step — policy + behavior fan-out finished. Captures the
            # decision engine's intermediate risk + flags so replay shows the
            # raw decision context (not just the final allow/deny).
            asyncio.create_task(_safe_bg(emit_step(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=1, step_type="policy",
                summary=f"decision_engine action={action} risk={float(getattr(decision, 'risk', 0.0)):.3f}",
                payload={"reasons": list(reasons or [])[:5]},
                risk_score=float(getattr(decision, "risk", 0.0)),
                status="ok" if action in ("allow", "monitor") else "deny",
            )))

            # Sprint 6 — Shadow-mode hook. Fire-and-forget; NEVER awaited,
            # NEVER alters `decision` or `action`, NEVER raises into the
            # request handler. Records what each shadow-mode policy WOULD
            # have decided so an operator can review before promoting it
            # to enforce. Disabled in environments without the audit DB by
            # design — the schedule() helper returns None and we move on.
            try:
                from services.gateway.shadow_eval_hook import schedule as _schedule_shadow
                _payload_for_shadow = None
                try:
                    _payload_for_shadow = (
                        request.state.tool_input
                        if hasattr(request.state, "tool_input") else None
                    )
                    if _payload_for_shadow is None and isinstance(tool_metadata, dict):
                        _payload_for_shadow = tool_metadata.get("payload")
                except Exception:
                    _payload_for_shadow = None
                _schedule_shadow(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    request_id=request_id,
                    audit_id=None,
                    tool=tool_name,
                    payload=str(_payload_for_shadow) if _payload_for_shadow else None,
                    payload_hash=getattr(proxy_res, "prompt_hash", None),
                    real_action=action,
                    risk_score=float(getattr(decision, "risk", 0.0) or 0.0),
                    inference_risk=float(risk_score) if risk_score is not None else None,
                    behavior_risk=None,
                )
            except Exception:
                logger.exception("shadow_eval_dispatch_failed")

            # Sprint 3 — Shadow Mode pre-check.
            # If the workspace is inside its 14-day observe-only window AND
            # the policy returned DENY or ESCALATE (KILL never downgrades —
            # it represents an active threat being terminated), record a
            # would_have_blocked audit + SSE event + counter bump, then
            # mutate decision.action to ALLOW so the deny/escalate block
            # below simply doesn't fire. The SDK gets a normal allow back
            # and the customer's production agent traffic is uninterrupted.
            #
            # Implementation note: mutating `decision.action` is safe here
            # because services.decision.schemas.Decision has
            # ConfigDict(strict=False) — the model permits attribute writes
            # — and the only downstream readers (emit_graph_event,
            # _flight_final assignment) use the local `action` variable
            # which we also update.
            if (
                decision.action in (ExecutionAction.DENY, ExecutionAction.ESCALATE)
                and _shadow_mode_active(request)
            ):
                _shadow_orig = (
                    "escalate" if decision.action == ExecutionAction.ESCALATE else "deny"
                )
                try:
                    SHADOW_DOWNGRADES_TOTAL.labels(
                        tenant_id=t_id_str, original_action=_shadow_orig,
                    ).inc()
                except Exception:
                    pass
                _decision_findings = list(getattr(decision, "findings", []) or [])
                _decision_reasons  = list(reasons or [])
                _decision_meta_for_shadow = (
                    decision.metadata if isinstance(decision.metadata, dict) else {}
                )
                _policy_id_for_shadow = str(
                    _decision_meta_for_shadow.get("policy_id") or
                    _decision_meta_for_shadow.get("policy_reason") or ""
                ) or None
                try:
                    await self._log_audit(
                        t_id_str, agent_id, "execute_tool", tool_name,
                        "would_have_blocked",
                        (
                            f"Shadow mode: would have {_shadow_orig}d — "
                            f"{', '.join(_decision_reasons[:2]) if _decision_reasons else 'policy block'}"
                        ),
                        request_id,
                        {
                            "status":          200,
                            "risk_score":      risk_score,
                            "original_action": _shadow_orig,
                            "reasons":         _decision_reasons,
                            "findings":        _decision_findings,
                            "policy_id":       _policy_id_for_shadow,
                        },
                    )
                except Exception as _audit_exc:
                    logger.warning(
                        "shadow_downgrade_audit_failed",
                        error=str(_audit_exc), request_id=request_id,
                    )
                asyncio.create_task(_safe_bg(publish_event(
                    self.redis, t_id_str, "would_have_blocked",
                    {
                        "agent_id":        str(agent_id),
                        "tool":            tool_name,
                        "original_action": _shadow_orig,
                        "reasons":         _decision_reasons[:5],
                        "findings":        _decision_findings[:5],
                        "request_id":      request_id,
                        "risk_score":      risk_score,
                        "policy_id":       _policy_id_for_shadow,
                    },
                    agent_id=str(agent_id),
                )))
                request.state.shadow_downgraded = True
                request.state.shadow_original_action = _shadow_orig
                try:
                    decision.action = ExecutionAction.ALLOW
                except Exception:
                    # Best-effort — if the Pydantic model has been frozen
                    # by a future change, fall back to the deny path.
                    logger.warning(
                        "shadow_downgrade_decision_mutate_failed",
                        request_id=request_id,
                    )
                else:
                    action = "allow"

            if decision.action in (ExecutionAction.KILL, ExecutionAction.DENY, ExecutionAction.ESCALATE):
                if decision.action == ExecutionAction.KILL:
                    await self._kill_token(request)
                # 2026-06-15 — surface BOTH the canonical findings (for
                # SDK-side branching on a stable vocabulary) AND the
                # rule-specific policy_reason from the decision metadata
                # (so a buyer's audit log sees "wire_above_hard_cap" not
                # the opaque "policy_deny"). The engine stamps the raw
                # rego/Python-port reason into decision.metadata.
                policy_reason_raw = (
                    decision.metadata.get("policy_reason")
                    if isinstance(decision.metadata, dict) else None
                )
                # Strip the internal __escalate suffix before exposing.
                if isinstance(policy_reason_raw, str):
                    policy_reason_raw = policy_reason_raw.replace("__escalate", "")
                # findings = canonical + the raw rule name appended so SDKs
                # have BOTH the vocabulary entry and the specific reason.
                response_findings = list(decision.findings or []) or list(reasons)
                if policy_reason_raw and policy_reason_raw not in response_findings:
                    response_findings.append(policy_reason_raw)
                primary_reason = policy_reason_raw or (
                    decision.findings[0] if decision.findings
                    else (reasons[0] if reasons else None)
                )
                # ARCH-4 2026-06-15 — pull explainability fields off
                # decision.metadata (decision service stamped them there
                # from the policy fast-path response).
                _decision_meta = decision.metadata if isinstance(decision.metadata, dict) else {}
                _resp_policy_id   = str(_decision_meta.get("policy_id") or "")
                _resp_explanation = str(_decision_meta.get("explanation") or "")
                _resp_risk_score  = int(_decision_meta.get("policy_risk_score") or 0)
                # FUP-4 2026-06-15 — surface SEC + GOV engine slices.
                _resp_security    = _decision_meta.get("security") or None
                _resp_governance  = _decision_meta.get("governance") or None
                # Sprint 1 2026-06-15 — MITRE ATT&CK tag. Looked up against
                # the central signal registry from the canonical findings
                # we already have on hand; no extra Redis or HTTP cost.
                _resp_mitre = None
                try:
                    from services.security.signal_registry import mitre_for_finding
                    for _f in response_findings:
                        _m = mitre_for_finding(_f)
                        if _m:
                            _resp_mitre = _m
                            break
                except Exception:
                    pass
                if decision.action == ExecutionAction.ESCALATE:
                    resp = self._escalate(
                        f"Action escalated. Reasons: {', '.join(reasons)}",
                        findings=response_findings,
                        reason=primary_reason,
                        policy_id=_resp_policy_id or None,
                        risk_score=_resp_risk_score or None,
                        explanation=_resp_explanation or None,
                        security=_resp_security,
                        governance=_resp_governance,
                        mitre=_resp_mitre,
                    )
                else:
                    resp = self._deny(
                        f"Security Block: {', '.join(reasons)}", 403,
                        findings=response_findings,
                        reason=primary_reason,
                        policy_id=_resp_policy_id or None,
                        risk_score=_resp_risk_score or None,
                        explanation=_resp_explanation or None,
                        security=_resp_security,
                        governance=_resp_governance,
                        mitre=_resp_mitre,
                    )

                # Guaranteed Logging + Billing for Security Block (synchronous)
                await self._log_decision(t_id_str, agent_id, tool_name, decision, request_id, tokens)

                # R4: publish to the incident-event queue so Incidents/UI
                # populates from real denials, not a seeded fixture. Severity
                # maps off the gateway decision: KILL → CRITICAL (the agent
                # got pulled), DENY → HIGH, ESCALATE → MEDIUM (pending
                # operator approval). Fire-and-forget; the request path
                # must never block on the alert publish.
                _sev = (
                    "CRITICAL" if decision.action == ExecutionAction.KILL
                    else "MEDIUM" if decision.action == ExecutionAction.ESCALATE
                    else "HIGH"
                )
                _trigger = (
                    "agent_killed" if decision.action == ExecutionAction.KILL
                    else "escalation_required" if decision.action == ExecutionAction.ESCALATE
                    else "policy_denied"
                )
                asyncio.create_task(_safe_bg(service_client.publish_incident_event(
                    tenant_id=t_id_str,
                    agent_id=str(agent_id),
                    severity=_sev,
                    trigger=_trigger,
                    title=f"{decision.action.name}: {tool_name}",
                    risk_score=float(getattr(decision, "risk", 0.0) or 0.0),
                    tool=tool_name,
                    request_id=request_id,
                    reasons=list(reasons) if reasons else [],
                )))

                # Sprint 4 — Incident Storyline. Append this enforcement
                # outcome onto the storyline keyed by (tenant, session) →
                # cross-agent chain → (tenant, agent) fallback. Best-effort:
                # any Redis failure inside recorder.record_step is swallowed
                # so the user response is never blocked on storyline
                # recording. The session id comes from the gateway's auth
                # middleware via request.state.session_id (set when the JWT
                # carries one, empty otherwise).
                try:
                    from services.security.incidents import recorder as _storyline_recorder
                    from services.security import signal_registry as _sigreg

                    _findings_for_story = list(
                        (decision.findings or _canonical.get("signal_findings") or [])
                    )
                    # Primary finding = first non-attack-chain finding; falls
                    # back to the attack_chain wrapper itself if that's all
                    # we have.
                    _primary = next(
                        (f for f in _findings_for_story
                         if not str(f).startswith("attack_chain:")),
                        _findings_for_story[0] if _findings_for_story else "",
                    )
                    _mitre = _sigreg.mitre_for_finding(_primary) if _primary else {}
                    _xagent_chain = _canonical.get("cross_agent_chain") if isinstance(_canonical, dict) else None
                    _tier_str = (
                        "kill" if decision.action == ExecutionAction.KILL
                        else "deny" if decision.action == ExecutionAction.DENY
                        else "escalate"
                    )
                    _policy_id_for_story = (
                        decision.metadata.get("policy_reason")
                        if isinstance(decision.metadata, dict) else ""
                    ) or ""
                    _explanation = ""
                    if isinstance(decision.metadata, dict):
                        _explanation = str(
                            decision.metadata.get("policy_explanation") or
                            decision.metadata.get("policy_reason") or
                            ""
                        )
                    asyncio.create_task(_safe_bg(_storyline_recorder.record_step(
                        self.redis,
                        tenant_id=t_id_str,
                        agent_id=str(agent_id),
                        session_id=getattr(request.state, "session_id", "") or "",
                        signal_id=str(_primary),
                        mitre_tactic=str(_mitre.get("tactic") or ""),
                        mitre_technique=str(_mitre.get("technique") or ""),
                        objective=str(_mitre.get("objective") or ""),
                        tier=_tier_str,
                        policy_id=str(_policy_id_for_story),
                        target=str(_canonical.get("target") or "") if isinstance(_canonical, dict) else "",
                        explanation=_explanation,
                        risk_score=int(float(getattr(decision, "risk", 0.0) or 0.0) * 100),
                        cross_agent_chain=_xagent_chain if isinstance(_xagent_chain, dict) else None,
                    )))
                except Exception as _sx:
                    logger.warning("storyline_record_step_failed", error=str(_sx))

                # U2 2026-06-17 — SSE notify LiveFeed on the deny/kill/escalate
                # chokepoint. Mirrors the allow-path `tool_executed` publish in
                # gateway/main.py (lines ~1308-1325) so the dashboard surfaces
                # blocked invocations in real time, not just allowed ones. The
                # audit row was just written (above via _log_decision); this
                # is the live-channel companion. Best-effort: any failure here
                # MUST NOT affect the response to the agent.
                try:
                    asyncio.create_task(_safe_bg(publish_event(
                        self.redis,
                        t_id_str,
                        "policy_decision",
                        {
                            "decision":   action,
                            "request_id": request_id,
                            "agent_id":   str(agent_id),
                            "tool":       tool_name,
                            "risk":       float(getattr(decision, "risk", 0.0) or 0.0),
                            "findings":   list(response_findings or [])[:5],
                            "reasons":    list(reasons or [])[:5],
                            "policy_id":  _resp_policy_id or None,
                        },
                        agent_id=str(agent_id),
                    )))
                except Exception:
                    pass

                _flight_final["decision"] = action
                _flight_final["risk"]     = float(getattr(decision, "risk", 0.0) or 0.0)
                _flight_final["status"]   = "failed"
                return resp

            # --- 6. RATE LIMITING (PHASE 4) ---
            # Apply rate limiting ONLY to valid traffic that passed security.
            # Step 0 (Defense) + Step 4 (Limits)
            defense_resp = await self._check_early_defense(client_ip)
            if defense_resp:
                action = "throttle"
                # We still log/bill throttled requests per ACP contract
                await self._log_audit(t_id_str, agent_id, "execute_tool", tool_name, "throttle", "Global/IP Rate Limit", request_id, {"status": 429, "risk_score": risk_score})
                await self._record_billing_with_retry(
                    tenant_id=t_id_str,
                    action="throttle",
                    agent_id=agent_id,
                    tokens=tokens,
                    audit_id=request_id
                )
                _flight_final["decision"] = "throttle"
                _flight_final["status"]   = "failed"
                return defense_resp

            try:
                rpm_limit = getattr(request.state, "rpm_limit", 0)
                await self._check_rate_limits(t_id_str, agent_id, jti, tier, rpm_limit=rpm_limit)
            except HTTPException as e:
                if e.status_code == 429:
                    action = "throttle"
                    await self._log_audit(t_id_str, agent_id, "execute_tool", tool_name, "throttle", e.detail, request_id, {"status": 429, "risk_score": risk_score})
                    await self._record_billing_with_retry(
                        tenant_id=t_id_str,
                        action="throttle",
                        agent_id=agent_id,
                        tokens=tokens,
                        audit_id=request_id
                    )
                raise

            # --- 6.5 BOUNDED AUTONOMY (F3) ----------------------------------
            _autonomy_resp, _autonomy_action = await self._enforce_bounded_autonomy(
                request,
                t_id_str=t_id_str,
                tenant_id=tenant_id,
                agent_id=agent_id,
                tool_name=tool_name,
                request_id=request_id,
                tokens=tokens,
                risk_score=risk_score,
                flight_final=_flight_final,
            )
            if _autonomy_action is not None:
                action = _autonomy_action
            if _autonomy_resp is not None:
                return _autonomy_resp

            # --- 7. EXECUTION ---
            action = "allow"
            asyncio.create_task(_safe_bg(emit_step(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=3, step_type="decision",
                summary=f"decision={action} risk={risk_score:.3f}",
                payload={"action": action, "risk_score": risk_score},
                risk_score=risk_score,
            )))
            # State snapshot right before tool execution — captures the
            # gateway's full reasoning context so forensic replay can show
            # what was approved, by which path, with which signals.
            asyncio.create_task(_safe_bg(emit_snapshot(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=3,
                snapshot={
                    "phase": "pre_execute",
                    "tool": tool_name,
                    "agent_id": str(agent_id),
                    "tier": tier,
                    "risk_score": risk_score,
                    "action": action,
                    "reasons": list(reasons or [])[:5],
                },
                tokens_in=int(tokens or 0),
            )))
            response = await call_next(request)
            # Post-execution snapshot — records the upstream tool's outcome
            # status so timelines show the actual execution result, not just
            # the policy decision.
            asyncio.create_task(_safe_bg(emit_snapshot(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=4,
                snapshot={
                    "phase": "post_execute",
                    "tool": tool_name,
                    "status_code": int(getattr(response, "status_code", 0) or 0),
                },
            )))
            # Step 4 — execution outcome (records HTTP status so Flight Recorder
            # shows the full pipeline: auth→rate-limit→security→policy→execute).
            _exec_status_code = int(getattr(response, "status_code", 200) or 200)
            asyncio.create_task(_safe_bg(emit_step(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=4, step_type="execution",
                summary=f"tool_executed status={_exec_status_code}",
                payload={"status_code": _exec_status_code},
                risk_score=risk_score,
                status="ok" if _exec_status_code < 400 else "error",
            )))
            # _finalize_request may return a different response (500) if billing fails
            response = await self._finalize_request(request, response, t_id_str, agent_id, tool_name, body_hash, tier, start_time, request_id, risk_score, tokens)
            # Emit identity-graph edge + close timeline (fire-and-forget).
            asyncio.create_task(_safe_bg(emit_graph_event(
                self.redis,
                tenant_id=t_id_str,
                src_id=str(agent_id), src_type="agent",
                src_name=getattr(request.state, "actor", None) or str(agent_id),
                dst_id=tool_name, dst_type="tool", dst_name=tool_name,
                edge_type="invokes", action="execute_tool",
                outcome=map_decision_to_outcome(action),
                risk_score=risk_score, request_id=request_id,
                attributes={"tier": tier, "status": response.status_code},
            )))
            # SPRINT B — L3 runaway-loop detector. If this agent is on a
            # treadmill of failures (50+ 4xx/5xx in 5 minutes on the same
            # tool), auto-quarantine. The next /execute is short-circuited
            # by the check at the top of dispatch. This means a compromised
            # agent stops dead within seconds, instead of burning policy/
            # decision CPU for the rest of the attacker's loop.
            if response.status_code >= 400:
                try:
                    from services.gateway._behavior_aggregator import (
                        record_failure, quarantine_agent,
                        RUNAWAY_FAILURE_THRESHOLD,
                    )
                    cumulative_failures = await record_failure(
                        self.redis, t_id_str, str(agent_id), tool_name,
                    )
                    if cumulative_failures > RUNAWAY_FAILURE_THRESHOLD:
                        await quarantine_agent(
                            self.redis, t_id_str, str(agent_id),
                            f"runaway_loop:{tool_name}:{cumulative_failures}_failures_5m",
                        )
                        logger.critical(
                            "agent_auto_quarantined_runaway_loop",
                            agent_id=str(agent_id), tool=tool_name,
                            failures=cumulative_failures,
                        )
                        await self._log_audit(
                            t_id_str, agent_id, "agent_quarantined",
                            tool_name, "block",
                            "runaway_loop_auto_quarantine",
                            request_id,
                            {"failures_5m": cumulative_failures},
                        )
                except Exception as _bx:
                    logger.warning("runaway_loop_record_failed", error=str(_bx))

            # Flight Recorder close — emitted from the `finally` clause below so
            # this single emission covers success, block, and exception paths.
            _flight_final["decision"] = action
            _flight_final["risk"]     = risk_score or 0.0
            _flight_final["status"]   = "ok" if response.status_code < 500 else "failed"
            return response

        except HTTPException as e:
            return await self._handle_http_exception(
                e, request,
                t_id_str=t_id_str,
                agent_id=agent_id,
                tool_name=tool_name,
                action=action,
                risk_score=risk_score,
                tokens=tokens,
                request_id=request_id,
                flight_final=_flight_final,
            )
        except Exception as exc:
            return await self._handle_unhandled_exception(
                exc,
                t_id_str=t_id_str,
                agent_id=agent_id,
                tool_name=tool_name,
                risk_score=risk_score,
                tokens=tokens,
                request_id=request_id,
                flight_final=_flight_final,
            )
        finally:
            # Guaranteed timeline close. Every /execute that reached
            # tool_name resolution set `_flight_opened=True`; the matching
            # close emission lives here exactly once so the operator's
            # `open_total - closed_total` SLI stays at 0 under steady state.
            # Fire-and-forget so a Redis stall on the close path does not
            # leak into the client-visible response.
            if _flight_opened and not _flight_closed:
                _flight_closed = True
                asyncio.create_task(_safe_bg(emit_timeline_end(
                    self.redis, tenant_id=t_id_str, request_id=request_id,
                    final_decision=_flight_final["decision"],
                    final_risk=_flight_final["risk"],
                    status=_flight_final["status"],
                )))

            # Gateway-internal latency: request received → finally clause.
            # Recorded once per request regardless of outcome so /status
            # reports a true gateway-process p95, distinct from the
            # end-to-end probe latency reported by /system/health.
            try:
                from services.gateway.latency_window import gateway_internal_window
                gateway_internal_window.record((time.time() - start_time) * 1000.0)
            except Exception:
                # Latency tracking must never break a request.
                pass

    async def _check_payload_size(
        self, request: Request,
    ) -> tuple[bytes, str]:
        """PHASE 0 — fail-fast payload size check + body hash.

        Returns ``(raw_body, body_hash)`` on success. On oversize, raises
        ``HTTPException(413)`` with an ``X-ACP-Audit-Action: reject``
        header. The downstream ``_handle_http_exception`` reads that
        header and uses it as the audit row's action label so the row
        reflects the canonical "reject" disposition (distinct from the
        default "deny"). Headers are the only metadata channel
        ``HTTPException`` exposes; using them keeps the action label
        encapsulated in the helper instead of leaking back into the
        dispatcher's closure.

        Body hash is computed even on the success path because every
        downstream phase (idempotency, audit) needs it.
        """
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body).hexdigest() if raw_body else "empty"
        if len(raw_body) > settings.MAX_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Payload exceeds absolute gateway limit",
                headers={"X-ACP-Audit-Action": "reject"},
            )
        return raw_body, body_hash

    async def _handle_unhandled_exception(
        self,
        exc: BaseException,
        *,
        t_id_str: str,
        agent_id: uuid.UUID,
        tool_name: str,
        risk_score: float,
        tokens: int,
        request_id: str,
        flight_final: dict,
    ) -> Response:
        """Finalise a non-HTTPException-terminated request.

        Classifies the exception:
          * ``asyncio.TimeoutError`` or ``httpx.TimeoutException`` → 504
            with category=``decision_timeout``. The /execute contract is
            strictly synchronous so a timeout is the cleanest possible
            response (the alternative was 202 + an unrealised polling URL).
          * everything else → 403 fail-closed. We can't make a security
            decision so deny is the only safe default; /execute's response
            contract is 200/403/429/502/504 only.

        Always writes an audit + billing row when the tenant is known —
        the audit row is the transparency-chain anchor for the 5xx and
        without it an auditor can't tell a timed-out request from a
        dropped one.
        """
        is_timeout = isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException))
        logger.exception("gateway_unhandled_error", error=str(exc), is_timeout=is_timeout)
        status_code = 504 if is_timeout else 403
        audit_reason = "decision_timeout" if is_timeout else f"fail_closed: {exc}"
        try:
            if t_id_str != "unknown":
                await self._log_audit(
                    t_id_str, agent_id, "execute_tool", tool_name,
                    "error", audit_reason, request_id,
                    {"status": status_code, "risk_score": risk_score,
                     "category": "decision_timeout" if is_timeout else "fail_closed"},
                )
                await self._record_billing_with_retry(
                    tenant_id=t_id_str,
                    action="error",
                    agent_id=agent_id,
                    tokens=tokens,
                    audit_id=request_id,
                )
        except Exception as _err_inner:
            logger.error("error_path_finalize_failed", error=str(_err_inner))

        flight_final["decision"] = "error"
        flight_final["status"]   = "failed"
        flight_final["risk"]     = risk_score or 0.0

        if is_timeout:
            return self._decision_timeout(request_id)
        return self._deny("Fail-Closed: decision service unavailable", 403)

    async def _handle_http_exception(
        self,
        e: HTTPException,
        request: Request,
        *,
        t_id_str: str,
        agent_id: uuid.UUID,
        tool_name: str,
        action: str,
        risk_score: float,
        tokens: int,
        request_id: str,
        flight_final: dict,
    ) -> Response:
        """Finalise an HTTPException-terminated request.

        Three jobs, all best-effort:
          1. Emit a terminal flight step + snapshot so the timeline doesn't
             end abruptly. Fire-and-forget; never blocks the response.
          2. Audit + billing the error outcome. Recovers ``t_id_str`` /
             ``agent_id`` from headers when auth failed before the
             middleware bound them — without this, auth-failure rows would
             be attributed to the zero UUID and slip through tenant-scoped
             queries.
          3. Bucket the flight_final disposition: HTTP-level rejections
             (auth, throttle, etc.) are blocks, not platform errors.

        Returns the final 4xx/5xx response built from the exception.
        """
        # 1. Flight terminal step + snapshot
        try:
            if t_id_str != "unknown":
                asyncio.create_task(_safe_bg(emit_step(
                    self.redis, tenant_id=t_id_str, request_id=request_id,
                    step_index=99, step_type="failure",
                    summary=f"{e.status_code}:{(e.detail or '')[:80]}",
                    status="error",
                    payload={"status_code": e.status_code},
                )))
                asyncio.create_task(_safe_bg(emit_snapshot(
                    self.redis, tenant_id=t_id_str, request_id=request_id,
                    step_index=99,
                    snapshot={
                        "phase": "http_exception",
                        "status_code": e.status_code,
                        "detail": (e.detail or "")[:200],
                        "tool": tool_name,
                    },
                )))
        except Exception as _flight_exc:
            logger.debug("flight_error_emit_failed", error=str(_flight_exc))

        # 2. Audit + billing — recover identity from headers if auth failed.
        # If the originating helper set X-ACP-Audit-Action in the
        # HTTPException headers (e.g. PHASE 0 sets "reject" on 413),
        # honour that as the canonical audit disposition; otherwise fall
        # back to the dispatcher's closure-level `action` (default "deny").
        effective_action = (e.headers or {}).get("x-acp-audit-action") \
            or (e.headers or {}).get("X-ACP-Audit-Action") \
            or action
        try:
            if t_id_str == "unknown":
                t_id_hdr = request.headers.get("X-Tenant-ID")
                if t_id_hdr:
                    t_id_str = t_id_hdr
                    try:
                        agent_id_hdr = request.headers.get("X-Agent-ID")
                        if agent_id_hdr:
                            agent_id = uuid.UUID(agent_id_hdr)
                    except (ValueError, TypeError):
                        pass

            if t_id_str != "unknown":
                await self._log_audit(
                    t_id_str, agent_id, "execute_tool", tool_name,
                    effective_action, e.detail, request_id,
                    {"status": e.status_code, "risk_score": risk_score},
                )
                await self._record_billing_with_retry(
                    tenant_id=t_id_str,
                    action=effective_action,
                    agent_id=agent_id,
                    tokens=tokens,
                    audit_id=request_id,
                )
        except Exception as _inner:
            logger.error("error_handler_failed", error=str(_inner))

        # 3. Bucket flight disposition: HTTP rejection = block, not error
        if flight_final["status"] != "ok":
            if flight_final["decision"] == "error":
                flight_final["decision"] = "block"
            flight_final["status"] = "failed"
            flight_final["risk"]   = risk_score or 0.0

        # 4. Sprint B follow-up 2026-06-14 — runaway-loop counter. Every
        # 4xx/5xx response (whether returned via HTTPException, autonomy
        # deny, or policy escalate) flows through here. Tick the per-
        # (agent, tool) failure window; once it crosses
        # RUNAWAY_FAILURE_THRESHOLD inside 5 minutes the agent is auto-
        # quarantined and every subsequent /execute short-circuits in the
        # dispatch entry. The success-path-only hook in the prior sprint
        # missed this — denies never went through it.
        try:
            if e.status_code >= 400 and t_id_str != "unknown" and tool_name and tool_name != "unknown_tool":
                from services.gateway._behavior_aggregator import (
                    record_failure, quarantine_agent, is_quarantined,
                    RUNAWAY_FAILURE_THRESHOLD,
                )
                cumulative = await record_failure(
                    self.redis, t_id_str, str(agent_id), tool_name,
                )
                if cumulative > RUNAWAY_FAILURE_THRESHOLD:
                    already, _ = await is_quarantined(self.redis, t_id_str, str(agent_id))
                    if not already:
                        await quarantine_agent(
                            self.redis, t_id_str, str(agent_id),
                            f"runaway_loop:{tool_name}:{cumulative}_failures_5m",
                        )
                        logger.critical(
                            "agent_auto_quarantined_runaway_loop",
                            agent_id=str(agent_id), tool=tool_name,
                            failures=cumulative,
                        )
                        await self._log_audit(
                            t_id_str, agent_id, "agent_quarantined",
                            tool_name, "block",
                            "runaway_loop_auto_quarantine",
                            request_id,
                            {"failures_5m": cumulative},
                        )
        except Exception as _runx:
            logger.warning("runaway_loop_record_failed", error=str(_runx))

        # H1-deeper closure 2026-06-18: forward e.headers (WWW-Authenticate
        # realm hints set in _mw_auth.py:247/281/358/366) into _deny so the
        # UI receives "Bearer realm=session_expired|invalid_token|insufficient_role".
        return self._deny(e.detail, e.status_code, headers=e.headers)

    async def _enforce_bounded_autonomy(
        self,
        request: Request,
        *,
        t_id_str: str,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        tool_name: str,
        request_id: str,
        tokens: int,
        risk_score: float,
        flight_final: dict,
    ) -> tuple[Response | None, str | None]:
        """PHASE 6.5 — bounded-autonomy contract check (F3).

        Autonomy contracts are an additive layer evaluated AFTER the
        security pipeline approves. Fail-open on autonomy service outage
        (logged + visible in /system/health) so a degraded autonomy
        service never silently widens trust — Policy + Decision retain
        fail-closed.

        Three outcomes:
          * ``allowed`` → return ``(None, None)`` so dispatcher continues
          * ``deny``    → audit + billing recorded, graph event emitted,
                          flight_final mutated, return 403 + ``"deny"``
          * ``requires_approval`` → flight_final mutated, return a 403
                          with structured ``approval_required`` body and
                          ``"escalate"`` as the new action label

        The /execute contract is strictly synchronous so the
        approval-required path returns 403 (with the SDK mapping it to
        EscalationRequiredError), not 202 + polling URL.
        """
        ac = await check_autonomy_contract(
            tenant_id=t_id_str, agent_id=str(agent_id), action=tool_name,
            request_id=request_id, tool_calls_so_far=tokens,
            redis=self.redis,
        )
        if not ac.get("allowed", True):
            reason_detail = ac.get("reason") or "autonomy_contract_violation"
            await self._log_audit(
                t_id_str, agent_id, "execute_tool", tool_name, "deny",
                reason_detail, request_id,
                {"status": 403, "risk_score": risk_score,
                 "violated_rules": ac.get("violated_rules", [])},
            )
            await self._record_billing_with_retry(
                tenant_id=t_id_str, action="deny", agent_id=agent_id,
                tokens=tokens, audit_id=request_id,
            )
            asyncio.create_task(_safe_bg(emit_graph_event(
                self.redis,
                tenant_id=t_id_str,
                src_id=str(agent_id), src_type="agent",
                src_name=getattr(request.state, "actor", None) or str(agent_id),
                dst_id=tool_name, dst_type="tool", dst_name=tool_name,
                edge_type="invokes", action="execute_tool", outcome="deny",
                risk_score=risk_score, request_id=request_id,
                attributes={"layer": "autonomy", "rules": ac.get("violated_rules", [])},
            )))
            flight_final["decision"] = "deny"
            flight_final["risk"]     = risk_score or 0.0
            flight_final["status"]   = "failed"
            return self._deny(f"Autonomy: {reason_detail}", 403), "deny"

        if ac.get("requires_approval"):
            asyncio.create_task(_safe_bg(emit_step(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=2, step_type="autonomy",
                summary="approval_required", status="pending",
                payload={"contract_id": ac.get("contract_id")},
            )))
            # Surface the approval requirement so the LiveFeed + Approvals
            # inbox light up in real time instead of waiting for the
            # operator to poll /auto-response/pending.
            asyncio.create_task(_safe_bg(publish_event(
                self.redis, t_id_str, "approval_required",
                {
                    "agent_id":    str(agent_id),
                    "tool":        tool_name,
                    "contract_id": ac.get("contract_id"),
                    "reason":      ac.get("reason") or "autonomy_contract_approval",
                    "request_id":  request_id,
                    "risk_score":  risk_score,
                },
                agent_id=str(agent_id),
            )))
            flight_final["decision"] = "escalate"
            flight_final["risk"]     = risk_score or 0.0
            flight_final["status"]   = "failed"
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error":   "approval_required",
                    "detail":  f"Action requires approval (contract {ac.get('contract_id')}).",
                    "meta": {
                        "code":        403,
                        "category":    "escalation",
                        "request_id":  request_id,
                        "contract_id": ac.get("contract_id"),
                    },
                },
            ), "escalate"

        # Allowed (or no contract applied) — emit the pairing autonomy
        # step so the Flight Recorder timeline shows the trace.
        asyncio.create_task(_safe_bg(emit_step(
            self.redis, tenant_id=t_id_str, request_id=request_id,
            step_index=2, step_type="autonomy",
            summary=ac.get("reason") or "autonomy_pass",
            status="ok",
        )))
        return None, None

    async def _check_per_agent_cost_cap(
        self,
        request: Request,
        agent_id: uuid.UUID,
        request_id: str,
        flight_final: dict,
    ) -> Response | None:
        """PHASE 5c — best-effort per-agent runtime spend check.

        Reads two Redis keys:
          ``acp:agent_cost_cap:{agent_id}``          float USD cap (0/absent = no cap)
          ``acp:agent_cost_today:{agent_id}:{YMD}``  float USD spent today

        If ``spent >= cap`` returns a 402 Payment Required ``JSONResponse``
        with a structured body (cap_usd, spent_usd, reset_at, meta block).
        Otherwise returns ``None`` so the dispatcher continues.

        Cost cap is a budget control, NOT a security boundary, so any
        Redis or parsing error is swallowed and the request is allowed
        through — a cache stall must never reject paid traffic.

        Mutates ``flight_final["decision"]`` and ``["status"]`` on the 402
        path so the Flight Recorder bucket reflects the block.
        """
        if request.url.path.rstrip("/") not in ("/execute", "/v1/execute"):
            return None
        if agent_id == uuid.UUID(int=0):
            return None

        try:
            cap_raw = await self.redis.get(f"acp:agent_cost_cap:{agent_id}")
            if cap_raw is None:
                return None
            if isinstance(cap_raw, (bytes, bytearray)):
                cap_raw = cap_raw.decode("ascii", errors="replace")
            try:
                cap_usd = float(cap_raw or 0)
            except (TypeError, ValueError):
                cap_usd = 0.0
            if cap_usd <= 0:
                return None

            from datetime import UTC
            from datetime import datetime as _dt
            from datetime import timedelta as _td
            _now = _dt.now(UTC)
            _day_key = _now.strftime("%Y%m%d")
            spent_raw = await self.redis.get(
                f"acp:agent_cost_today:{agent_id}:{_day_key}"
            )
            if isinstance(spent_raw, (bytes, bytearray)):
                spent_raw = spent_raw.decode("ascii", errors="replace")
            try:
                spent_usd = float(spent_raw or 0)
            except (TypeError, ValueError):
                spent_usd = 0.0
            if spent_usd < cap_usd:
                return None

            reset_at = (
                (_now + _td(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            ).isoformat().replace("+00:00", "Z")
            flight_final["decision"] = "block"
            flight_final["status"]   = "failed"
            return JSONResponse(
                status_code=402,
                content={
                    "success":   False,
                    "error":     "agent_cost_cap_exceeded",
                    "cap_usd":   cap_usd,
                    "spent_usd": spent_usd,
                    "reset_at":  reset_at,
                    "meta": {
                        "code":       402,
                        "category":   "cost_cap",
                        "request_id": request_id,
                        "agent_id":   str(agent_id),
                    },
                },
            )
        except Exception as _cost_cap_exc:
            # Fail-open: cost cap is best-effort, not a boundary.
            logger.warning(
                "agent_cost_cap_check_failed",
                error=str(_cost_cap_exc),
                agent_id=str(agent_id),
            )
            return None

    async def _validate_execute_agent_id(
        self,
        request: Request,
        raw_body: bytes,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> tuple[uuid.UUID, Response | None]:
        """PHASE 5b — agent_id body validation for /execute calls.

        Returns ``(maybe_promoted_agent_id, response_or_none)``:
          * On parse failure of the JSON body or any registry error, returns
            ``(agent_id, None)`` — fail-open per the original code, so registry
            outages do not block real governance traffic.
          * On a malformed body ``agent_id``, returns ``(agent_id, 400 deny)``.
          * On a body ``agent_id`` that the registry doesn't recognise for the
            current tenant, returns ``(agent_id, 403 deny)``.
          * On success, promotes the body's agent_id into the local one when
            the JWT carried no agent identity (human SECURITY / ADMIN callers).

        This makes the audit attribution land on the correct agent_id even for
        admin-issued /execute calls.
        """
        if request.url.path.rstrip("/") not in ("/execute", "/v1/execute") or not raw_body:
            return agent_id, None

        try:
            _bd = json.loads(raw_body)
            _body_agent_id = _bd.get("agent_id") if isinstance(_bd, dict) else None
            if _body_agent_id and isinstance(_body_agent_id, str):
                try:
                    _parsed_aid = uuid.UUID(_body_agent_id)
                except (ValueError, AttributeError):
                    _parsed_aid = None
                if _parsed_aid is None or _parsed_aid == uuid.UUID(int=0):
                    return agent_id, self._deny("Invalid agent_id format", 400)
                _reg_data = await service_client.get_agent_metadata(
                    _parsed_aid, tenant_id, jwt_claims={}
                )
                if _reg_data is None:
                    return agent_id, self._deny("Unknown agent — not registered in this tenant", 403)
                # Promote body agent_id when JWT has no agent identity
                if agent_id == uuid.UUID(int=0) and _parsed_aid:
                    return _parsed_aid, None
        except Exception:
            # fail-open — don't block on registry timeout
            pass

        return agent_id, None

    async def _handle_auth_phase(
        self, request: Request
    ) -> tuple[uuid.UUID, uuid.UUID, str, str]:
        """Verify identity and bind context."""
        is_execute = request.url.path.startswith("/execute")
        tenant_info = await self._authenticate(request, is_execute_path=is_execute)
        tenant_id, agent_id, t_id_str, _, jti = tenant_info
        request.state.jti = jti

        # On the execute path, if the caller is an admin (agent_id == UUID(0))
        # and an explicit X-Agent-ID is provided, use that agent's context so
        # the security pipeline evaluates the SELECTED agent's permissions — not
        # a wildcard-admin override.  This makes attack simulation accurate.
        request.state.agent_via_header = False
        if is_execute and agent_id == uuid.UUID(int=0):
            x_agent_hdr = request.headers.get("X-Agent-ID", "").strip()
            if x_agent_hdr:
                try:
                    agent_id = uuid.UUID(x_agent_hdr)
                    request.state.agent_via_header = True
                except ValueError:
                    pass

        tenant_meta = await service_client.get_tenant_metadata(tenant_id)
        tier: str = tenant_meta.get("tier", "basic")
        rpm_limit: int = int(tenant_meta.get("rpm_limit", 0))
        # Tenant-controlled posture when the behavior firewall is unreachable.
        # Decision service applies this; gateway just passes it through.
        request.state.degraded_mode_policy = tenant_meta.get(
            "degraded_mode_policy", "block_high_risk"
        )
        request.state.tier = tier
        request.state.rpm_limit = rpm_limit
        # Sprint 3 — Shadow mode. ISO-8601 timestamp the workspace exits
        # observe-only mode. The downgrade hook (above the deny/escalate
        # response build) checks this against now() and, when the window
        # is open, records `would_have_blocked` audit + SSE events
        # instead of actually blocking the agent's tool call. NULL on
        # legacy tenants (=> never in shadow mode).
        request.state.shadow_mode_until = tenant_meta.get("shadow_mode_until")
        # Sprint 3.2 — per-tenant quota fields stashed for the quota
        # check below + the /tenant/quota endpoint.
        request.state.quota_limits = {
            "requests_per_second":  int(tenant_meta.get("requests_per_second", 50) or 50),
            "burst":                int(tenant_meta.get("burst", 100) or 100),
            "daily_request_cap":    int(tenant_meta.get("daily_request_cap", 1_000_000) or 1_000_000),
            "monthly_request_cap":  tenant_meta.get("monthly_request_cap"),
            "daily_inference_cost_cap_usd": tenant_meta.get("daily_inference_cost_cap_usd"),
        }

        structlog.contextvars.bind_contextvars(
            tenant_id=t_id_str, agent_id=str(agent_id), tier=tier, actor=getattr(request.state, "actor", "unknown")
        )
        request.state.tenant_id = tenant_id
        request.state.agent_id = agent_id
        return tenant_id, agent_id, t_id_str, tier

    async def _handle_idempotency_phase(
        self, request: Request, t_id_str: str
    ) -> str | Response:
        """Prevent double-execution early."""
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body).hexdigest() if raw_body else "empty"

        idem_resp = await self._check_idempotency(request, t_id_str, body_hash)
        if idem_resp:
            return idem_resp

        return body_hash

    async def _handle_security_phase(
        self,
        request: Request,
        tool_name: str,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        t_id_str: str,
        request_id: str,
    ) -> ProxyDecision | Response:
        """Run Inference Proxy and OPA policy."""
        raw_body   = await request.body()
        jwt_claims = getattr(request.state, "jwt_claims", None)
        agent_meta = await service_client.get_agent_metadata(agent_id, tenant_id, jwt_claims=jwt_claims)
        allowed_tools = self._extract_allowed_tools(agent_meta)

        # ADMIN Override: grant wildcard only when admin is NOT targeting a specific
        # agent via X-Agent-ID (attack simulation, playground).  When agent_via_header
        # is True the selected agent's real permissions are enforced so the pipeline
        # tests actual policy, not a blanket bypass.
        user_perms = getattr(request.state, "permissions", [])
        if "*" in user_perms and not getattr(request.state, "agent_via_header", False):
            if allowed_tools is None:
                allowed_tools = ["*"]
            elif "*" not in allowed_tools:
                allowed_tools.append("*")

        proxy_result = await self._run_inference_proxy(
            request, raw_body, tool_name, allowed_tools, tenant_id, agent_id
        )
        if not proxy_result.allowed:
            await self._log_block(t_id_str, agent_id, tool_name, proxy_result, request_id, tokens=proxy_result.metadata.get("tokens", 1))
            # Surface the inference-proxy block as `llm_proxy_escalate` so
            # the LiveFeed shows when the gateway short-circuited an LLM
            # request (injection / risk-score / tool-guard / tenant-iso).
            # Fire-and-forget — a Redis stall must not block the deny path.
            asyncio.create_task(_safe_bg(publish_event(
                self.redis, t_id_str, "llm_proxy_escalate",
                {
                    "agent_id":   str(agent_id),
                    "tool":       tool_name,
                    "reason":     proxy_result.reason,
                    "status":     proxy_result.status_code,
                    "risk_score": float(getattr(proxy_result, "risk_score", 0.0) or 0.0),
                    "risk_level": getattr(proxy_result, "risk_level", "low"),
                    "flags":      (getattr(proxy_result, "flags", []) or [])[:5],
                    "request_id": request_id,
                },
                agent_id=str(agent_id),
            )))
            return self._deny(
                f"Security: {proxy_result.reason}", proxy_result.status_code
            )

        # Surface the inference-proxy admit-decision so the LiveFeed
        # renders the request-side LLM hand-off. The downstream
        # `tool_executed` event covers the response-side success.
        asyncio.create_task(_safe_bg(publish_event(
            self.redis, t_id_str, "llm_proxy_call",
            {
                "agent_id":   str(agent_id),
                "tool":       tool_name,
                "risk_score": float(getattr(proxy_result, "risk_score", 0.0) or 0.0),
                "risk_level": getattr(proxy_result, "risk_level", "low"),
                "tokens":     int(proxy_result.metadata.get("tokens", 1) or 1) if isinstance(proxy_result.metadata, dict) else 1,
                "request_id": request_id,
            },
            agent_id=str(agent_id),
        )))

        return proxy_result

    def _init_context(self, request: Request, request_id: str, deadline: float) -> None:
        """Initialize request state and context metadata."""
        request.state.deadline = deadline
        request.state.request_id = request_id

        trace_id = request.headers.get("X-Trace-ID", request_id)
        request.state.trace_id = trace_id
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("User-Agent", "unknown")

        request.state.tenant_id = None
        request.state.tier = "basic"
        structlog.contextvars.bind_contextvars(request_id=request_id, trace_id=trace_id, deadline=deadline, client_ip=client_ip, user_agent=user_agent)

    async def _get_tool_name(self, request: Request) -> str:
        """Extract tool name from headers, path, or body."""
        tool_name = request.headers.get("X-ACP-Tool")
        if not tool_name:
            path_parts = request.url.path.strip("/").split("/")
            if len(path_parts) >= 2 and path_parts[0] == "execute":
                tool_name = path_parts[1]
        if not tool_name and request.method == "POST":
            try:
                body = await request.json()
                if isinstance(body, dict):
                    tool_name = body.get("tool_name") or body.get("tool")
            except Exception:
                pass
        if not tool_name:
            raise HTTPException(status_code=400, detail="Tool name is required (provide via X-ACP-Tool header, path, or request body)")
        return tool_name

    def _extract_allowed_tools(self, agent_meta: dict | None) -> list[str] | None:
        """Extract allowed tool list from agent metadata."""
        if not agent_meta:
            return None
        return [
            p["tool_name"]
            for p in agent_meta.get("permissions", [])
            if str(p.get("action", "")).upper() == "ALLOW"
        ]

    async def _run_inference_proxy(
        self,
        request: Request,
        raw_body: bytes,
        tool_name: str,
        allowed_tools: list[str] | None,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> ProxyDecision:
        """Execute inference proxy checks."""
        x_tenant_header = request.headers.get("X-Tenant-ID")
        try:
            request_tenant_id = (
                uuid.UUID(x_tenant_header) if x_tenant_header else tenant_id
            )
        except (ValueError, TypeError):
            request_tenant_id = tenant_id

        # Detect cross-tenant access attempts embedded in the request body.
        # Agents sometimes pass tenant_id/target_tenant in the payload (or
        # nested under a "parameters" key) to reference another tenant's
        # resources; treat any mismatch as an isolation violation.
        if raw_body:
            try:
                body_json = json.loads(raw_body)
                if isinstance(body_json, dict):
                    # Check top-level AND nested under "parameters"
                    _search_scopes = [body_json]
                    params = body_json.get("parameters")
                    if isinstance(params, dict):
                        _search_scopes.append(params)
                    _found = False
                    for _scope in _search_scopes:
                        if _found:
                            break
                        for _key in ("tenant_id", "target_tenant"):
                            body_tid = _scope.get(_key)
                            if body_tid:
                                try:
                                    body_tenant_uuid = uuid.UUID(str(body_tid))
                                    if body_tenant_uuid != tenant_id:
                                        request_tenant_id = body_tenant_uuid
                                        _found = True
                                        break
                                except ValueError:
                                    pass
            except (json.JSONDecodeError, ValueError):
                pass

        return inference_proxy.check_input(
            raw_body=raw_body,
            content_type=request.headers.get("content-type", ""),
            tool_name=tool_name,
            allowed_tools=allowed_tools,
            request_tenant_id=request_tenant_id,
            token_tenant_id=tenant_id,
            agent_id=agent_id,
        )

    async def _emit_groq_event(
        self,
        *,
        event_id: str,
        tenant_id: str,
        agent_id: str,
        tool: str,
        decision: str,
        risk_score: float,
        signals: dict | None = None,
        reasons: list | None = None,
        source: str = "gateway",
    ) -> None:
        """Emit a decision event to the Groq intelligence queue (best-effort).

        2026-05-14 — was dead code; now called from every block path (inference
        proxy, autonomy, security middleware deny) so the Groq worker has real
        events to enrich. UI's Risk Engine "AI Threat Insights" panel reads
        from the resulting insights timeline.
        """
        try:
            payload = {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "tool": tool,
                "decision": decision,
                "risk_score": risk_score,
                "signals": signals or {},
                "reasons": reasons or [],
                "source": source,
            }
            await self.redis.xadd(
                "acp:groq_queue",
                {"data": json.dumps(payload, default=str)},
                maxlen=10000,
                approximate=True,
            )
        except (ConnectionError, TimeoutError, OSError) as exc:
            # M-6 (2026-05-13): expose previously-silent failures as a metric so
            # we don't lose observability of the intelligence-queue feed.
            try:
                from sdk.utils import GROQ_EVENT_FAILURES_TOTAL
                GROQ_EVENT_FAILURES_TOTAL.inc()
            except ImportError as imp_exc:
                logger.debug("groq_metric_unavailable", error=str(imp_exc))
            logger.warning("groq_event_emit_failed", error=str(exc), exc_type=type(exc).__name__)

    def _process_autonomous_abuse(self, tenant_id: str, client_ip: str, user_agent: str) -> None:
        async def _incr() -> None:
            abuse_key = f"acp:abuse:{tenant_id}"
            ip_key = f"acp:abuse:ips:{tenant_id}"
            ua_key = f"acp:abuse:uas:{tenant_id}"

            count = await self.redis.incr(abuse_key)
            await self.redis.sadd(ip_key, client_ip)  # type: ignore[not-async]
            await self.redis.sadd(ua_key, user_agent)  # type: ignore[not-async]

            if count == 1:
                await self.redis.expire(abuse_key, 300)
                await self.redis.expire(ip_key, 300)
                await self.redis.expire(ua_key, 300)

            unique_ips = await self.redis.scard(ip_key)  # type: ignore[not-async]
            unique_uas = await self.redis.scard(ua_key)  # type: ignore[not-async]

            # Enterprise NAT handling uses UA entropy explicitly
            if count > 50 and (unique_ips > 3 or unique_uas > 3):
                # Cooldown override check to prevent repetitive locks bypassing mitigation logs
                if await self.redis.get(f"acp:tenant_kill_reason:{tenant_id}"):
                    return

                await self.redis.setex(f"acp:tenant_kill:{tenant_id}", 86400, "1")
                await self.redis.setex(f"acp:tenant_kill_reason:{tenant_id}", 86400, "System engaged automatic blocking due to distributed multi-IP anomaly.")
                logger.critical("autonomous_abuse_kill_engaged", tenant_id=tenant_id)
        asyncio.create_task(_safe_bg(_incr()))
