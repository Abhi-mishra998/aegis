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
import time
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware

from sdk.common.background import safe_bg as _safe_bg
from sdk.common.config import settings
from sdk.common.ratelimit import RateLimiter
from services.decision.schemas import Decision, ExecutionAction
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
        "/health", "/docs", "/openapi.json", "/redoc", "/metrics",
        "/system/health",  # ops aggregate — must be reachable by k8s/ALB/Datadog probes (no tenant data)
        "/status",         # public customer-shareable status page (no tenant data)
        "/auth/token", "/auth/login", "/auth/agent/token",  # public auth endpoints
        "/events/stream",  # SSE — inline auth handled in the route handler
    ]
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

        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

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
            # Enforce ACP Rule: Check size BEFORE even wasting CPU on JWT validation
            raw_body = await request.body()
            body_hash = hashlib.sha256(raw_body).hexdigest() if raw_body else "empty"
            if len(raw_body) > settings.MAX_PAYLOAD_BYTES:
                action = "reject"
                raise HTTPException(status_code=413, detail="Payload exceeds absolute gateway limit")

            # --- 2. AUTHENTICATION & IDENTITY (PHASE 1) ---
            # MUST be the first line of defense after size check.
            identity = await self._handle_auth_phase(request)
            tenant_id, agent_id, t_id_str, tier = identity
            jti = getattr(request.state, "jti", None)

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

            tool_name = await self._get_tool_name(request)
            logger.info("policy_check_called", agent_id=str(agent_id), tool=tool_name, tenant_id=t_id_str)

            # Flight Recorder: OPEN as soon as we have the canonical tool name.
            # Doing this BEFORE the security/decision/autonomy phases means even
            # blocked requests get a finalised timeline (the previous design
            # only opened after autonomy approval, leaving block paths invisible
            # to the replay UI). The matching close lives in the `finally`
            # clause below — fire-and-forget by design; never blocks /execute.
            asyncio.create_task(_safe_bg(emit_timeline_start(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                agent_id=str(agent_id), tool=tool_name,
                metadata={"tier": tier},
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

            # Extract tool parameters for sensitivity checks (Issue #4, #6)
            tool_metadata = {}
            try:
                if raw_body:
                    body_dict = json.loads(raw_body)
                    if isinstance(body_dict, dict) and "parameters" in body_dict:
                        params = body_dict.get("parameters", {})
                        # M-2 FIX (2026-05-13): Path traversal hard-deny applies to ANY
                        # tool parameter that names a file/dir, not just read_file. We
                        # scan known path-like fields and any value that smells like a path.
                        _PATH_FIELDS = ("path", "file_path", "filename", "src", "dst", "destination", "target", "uri", "url")
                        for _k, _v in (params.items() if isinstance(params, dict) else []):
                            if not isinstance(_v, str):
                                continue
                            looks_path = _k.lower() in _PATH_FIELDS or _v.startswith("/") or "../" in _v
                            if not looks_path:
                                continue
                            _decoded_v = urllib.parse.unquote(_v).replace("\\", "/")
                            _vl = _v.lower()
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
                                # Extract the full traversal prefix for the error message
                                import re as _re
                                _m = _re.match(r"((?:\.\./)+)", _v)
                                _pt = _m.group(1) if _m else (_v[:20] if _v.startswith("/") else "../")
                                resp = self._deny(f"Security: Path traversal detected: '{_pt}'", 403)
                                await self._log_audit(
                                    t_id_str, agent_id, "execute_tool", tool_name, "block",
                                    "path_traversal_detected", request_id,
                                    {"blocked_field": _k, "blocked_path": _v[:100]},
                                )
                                # 2026-05-14: feed Groq pipeline — these are the
                                # textbook examples we want enriched insights for.
                                await self._emit_groq_event(
                                    event_id=request_id,
                                    tenant_id=t_id_str,
                                    agent_id=str(agent_id),
                                    tool=tool_name,
                                    decision="block",
                                    risk_score=1.0,
                                    signals={"blocked_field": _k},
                                    reasons=["path_traversal_detected"],
                                    source="path_traversal_hard_deny",
                                )
                                _flight_final["decision"] = "block"
                                _flight_final["risk"]     = 1.0
                                _flight_final["status"]   = "failed"
                                return resp
                        if tool_name == "read_file" and "path" in params:
                            tool_metadata["path"] = params["path"]
                        elif tool_name == "query" and "sql" in params:
                            tool_metadata["sql"] = params["sql"]
                    # Also extract top-level input/sql for db.* and query tools
                    _SQL_TOOL = (
                        tool_name in {"query", "db.query", "db.execute", "sql", "db.run"}
                        or tool_name.startswith("db.")
                    )
                    if _SQL_TOOL and "sql" not in tool_metadata:
                        _top_sql = (
                            body_dict.get("input") or body_dict.get("sql")
                            or body_dict.get("query") or ""
                        )
                        if isinstance(_top_sql, str) and _top_sql:
                            tool_metadata["input"] = _top_sql
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

            if decision.action in (ExecutionAction.KILL, ExecutionAction.DENY, ExecutionAction.ESCALATE):
                if decision.action == ExecutionAction.KILL:
                    await self._kill_token(request)
                if decision.action == ExecutionAction.ESCALATE:
                    resp = self._escalate(f"Action escalated. Reasons: {', '.join(reasons)}")
                else:
                    resp = self._deny(f"Security Block: {', '.join(reasons)}", 403)

                # Guaranteed Logging + Billing for Security Block (synchronous)
                await self._log_decision(t_id_str, agent_id, tool_name, decision, request_id, tokens)
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
            # 2026-05-13: Autonomy contracts are an additive layer evaluated AFTER
            # the security pipeline approves. Fail-open on autonomy service outage
            # (logged + visible in /system/health) so a degraded autonomy service
            # never silently widens trust — Policy + Decision retain fail-closed.
            ac = await check_autonomy_contract(
                tenant_id=t_id_str, agent_id=str(agent_id), action=tool_name,
                request_id=request_id, tool_calls_so_far=tokens,
                redis=self.redis,
            )
            if not ac.get("allowed", True):
                action = "deny"
                reason_detail = ac.get("reason") or "autonomy_contract_violation"
                await self._log_audit(
                    t_id_str, agent_id, "execute_tool", tool_name, "deny",
                    reason_detail, request_id,
                    {"status": 403, "risk_score": risk_score, "violated_rules": ac.get("violated_rules", [])},
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
                _flight_final["decision"] = "deny"
                _flight_final["risk"]     = risk_score or 0.0
                _flight_final["status"]   = "failed"
                return self._deny(f"Autonomy: {reason_detail}", 403)
            if ac.get("requires_approval"):
                # Approval is an out-of-band workflow (human-in-the-loop via
                # /autonomy/overrides). 2026-05-15: response was previously
                # 202 + an unrealised "polling URL"; /execute is now strictly
                # synchronous (no 202), so we return 403 with the same
                # `approval_required` reason. The SDK surfaces this as
                # EscalationRequiredError so callers can branch.
                asyncio.create_task(_safe_bg(emit_step(
                    self.redis, tenant_id=t_id_str, request_id=request_id,
                    step_index=2, step_type="autonomy",
                    summary="approval_required", status="pending",
                    payload={"contract_id": ac.get("contract_id")},
                )))
                _flight_final["decision"] = "escalate"
                _flight_final["risk"]     = risk_score or 0.0
                _flight_final["status"]   = "failed"
                return JSONResponse(
                    status_code=403,
                    content={
                        "success": False,
                        "error": "approval_required",
                        "detail": f"Action requires approval (contract {ac.get('contract_id')}).",
                        "meta": {
                            "code": 403,
                            "category": "escalation",
                            "request_id": request_id,
                            "contract_id": ac.get("contract_id"),
                        },
                    },
                )

            # Flight step — autonomy contract evaluated and allowed (or no
            # contract applied). Pairing with the deny branch above gives a
            # complete autonomy trace.
            asyncio.create_task(_safe_bg(emit_step(
                self.redis, tenant_id=t_id_str, request_id=request_id,
                step_index=2, step_type="autonomy",
                summary=ac.get("reason") or "autonomy_pass",
                status="ok",
            )))

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
            # Flight Recorder close — emitted from the `finally` clause below so
            # this single emission covers success, block, and exception paths.
            _flight_final["decision"] = action
            _flight_final["risk"]     = risk_score or 0.0
            _flight_final["status"]   = "ok" if response.status_code < 500 else "failed"
            return response

        except HTTPException as e:
            # Flight: emit a terminal error step + snapshot so timelines do
            # not end abruptly without explanation. Fire-and-forget; never
            # blocks the actual error response.
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

            # ACP Hardening: Ensure Audit + Billing for error outcomes
            try:
                # Recover tenant_id from header even on auth failure
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
                    # Log audit (synchronous to ensure persistence)
                    await self._log_audit(t_id_str, agent_id, "execute_tool", tool_name, action, e.detail, request_id, {"status": e.status_code, "risk_score": risk_score})
                    # Record billing synchronously with timeout + fallback
                    await self._record_billing_with_retry(
                        tenant_id=t_id_str,
                        action=action,
                        agent_id=agent_id,
                        tokens=tokens,
                        audit_id=request_id
                    )
            except Exception as _inner:
                logger.error("error_handler_failed", error=str(_inner))

            # HTTP-level rejections (auth, throttle, etc.) are blocks, not
            # platform errors. Bucket them under final_decision="block" unless
            # the success path already filled in a richer disposition.
            if _flight_final["status"] != "ok":
                if _flight_final["decision"] == "error":
                    _flight_final["decision"] = "block"
                _flight_final["status"] = "failed"
                _flight_final["risk"]   = risk_score or 0.0
            return self._deny(e.detail, e.status_code)
        except Exception as exc:
            # 2026-05-15: classify timeouts as 504, not 500. The synchronous
            # /execute contract permits a clean timeout response — what it
            # must never produce is 202 (no polling URL exists). Decision
            # service timeouts manifest as either `asyncio.TimeoutError` or
            # `httpx.TimeoutException` (ReadTimeout / ConnectTimeout etc.).
            is_timeout = isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException))
            logger.exception("gateway_unhandled_error", error=str(exc), is_timeout=is_timeout)
            # /execute contract: only 200/403/429/502/504. Timeouts → 504;
            # all other upstream failures → 403 fail-closed (can't make a
            # security decision → deny is the safe default).
            status_code = 504 if is_timeout else 403
            audit_reason = "decision_timeout" if is_timeout else f"fail_closed: {exc}"
            try:
                if t_id_str != "unknown":
                    # The audit row is the transparency-chain anchor for
                    # this 504 — without it a customer auditor can't tell a
                    # timed-out request from a dropped one.
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
            _flight_final["decision"] = "error"
            _flight_final["status"]   = "failed"
            _flight_final["risk"]     = risk_score or 0.0
            if is_timeout:
                return self._decision_timeout(request_id)
            return self._deny("Fail-Closed: decision service unavailable", 403)
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
            return self._deny(
                f"Security: {proxy_result.reason}", proxy_result.status_code
            )

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
