from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import Depends, FastAPI

from sdk.common.audit_stream import push_audit_event
from sdk.common.background import safe_bg as _safe_bg
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.decision.engine import decision_engine
from services.decision.router import router as decision_router
from services.decision.schemas import Decision, DecisionContext, OrchestrationRequest
from services.decision.behavior_consult import (
    DEFAULT_DEGRADED_MODE_POLICY,
    apply_degraded_mode_policy,
    classify_behavior_result,
)
from services.decision.intelligence import GroqSecurityBrain

logger = structlog.get_logger(__name__)

redis = get_redis_client(settings.REDIS_URL, decode_responses=False)

# Initialized in lifespan so the AsyncGroq SDK client is properly closed on shutdown
groq_brain: GroqSecurityBrain | None = None

# Module-level persistent HTTP client — avoids creating a new connection pool per request
_http_client: httpx.AsyncClient | None = None
_NIL_UUID = uuid.UUID(int=0)

# Per-call timeout budget so the decision service always responds well within the
# gateway's 2s asyncio.wait_for deadline:
#   registry  0.6s  (M-7 2026-05-13: bumped from 0.4s — was starving under load)
#   gather    1.0s  (policy + behavior in parallel, each bounded by client read=0.8s)
#   headroom  ~0.2s
_T_FAST = httpx.Timeout(
    connect=settings.DECISION_REGISTRY_TIMEOUT_CONNECT,
    read=settings.DECISION_REGISTRY_TIMEOUT_READ,
    write=settings.DECISION_REGISTRY_TIMEOUT_WRITE,
    pool=settings.DECISION_REGISTRY_TIMEOUT_POOL,
)
_T_GATHER = httpx.Timeout(
    connect=settings.DECISION_GATHER_TIMEOUT_CONNECT,
    read=settings.DECISION_GATHER_TIMEOUT_READ,
    write=settings.DECISION_GATHER_TIMEOUT_WRITE,
    pool=settings.DECISION_GATHER_TIMEOUT_POOL,
)
_T_GATHER_TOTAL = settings.DECISION_GATHER_TOTAL_TIMEOUT  # asyncio.wait_for cap on the parallel fan-out


async def _rehydrate_kill_switches() -> None:
    """Re-hydrate Redis kill switch keys from DB.

    C8 fix: Redis FLUSHDB or a pod restart clears in-memory kill switches.
    Reads every engaged kill_switch row from the DB and re-sets the Redis
    keys so the gateway enforces them immediately.

    Called at startup AND periodically (every 30s) by _kill_switch_poll_loop
    so a live FLUSHDB is healed within one poll interval.
    """
    try:
        from sqlalchemy import text as _text
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(
                _text("SELECT tenant_id, reason FROM kill_switches WHERE engaged = true")
            )
            rows = result.fetchall()
        await engine.dispose()
        for row in rows:
            key = f"acp:tenant_kill:{row.tenant_id}"
            reason = row.reason or "manual_admin_lockdown"
            await redis.setex(key, 86400 * 7, reason)
        if rows:
            logger.info("kill_switch_rehydrated", count=len(rows))
    except Exception as exc:
        logger.warning("kill_switch_rehydrate_failed", error=str(exc))


_KILL_SWITCH_POLL_INTERVAL: int = 30  # seconds between reconcile ticks


async def _kill_switch_poll_loop() -> None:
    """Periodic reconciler: DB → Redis every 30s.

    Closes the gap where a live `REDIS FLUSHDB` drops all kill-switch keys
    without a service restart. The poll re-asserts every engaged switch from
    the authoritative DB source within one poll interval.
    """
    while True:
        await asyncio.sleep(_KILL_SWITCH_POLL_INTERVAL)
        await _rehydrate_kill_switches()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _http_client, groq_brain
    _http_client = httpx.AsyncClient(timeout=_T_GATHER)
    try:
        groq_brain = GroqSecurityBrain(settings.GROQ_API_KEY)
        logger.info("groq_brain_initialized", model_fast=settings.GROQ_MODEL_FAST, model_deep=settings.GROQ_MODEL)
    except Exception as exc:
        logger.warning("groq_brain_init_failed", error=str(exc))
        groq_brain = None
    # C8: re-hydrate kill switches from DB in case Redis was flushed or restarted
    await _rehydrate_kill_switches()
    # Start periodic poll so live FLUSHDB is healed within 30s
    _poll_task = asyncio.create_task(_kill_switch_poll_loop(), name="kill_switch_poll")
    yield
    _poll_task.cancel()
    try:
        await _poll_task
    except asyncio.CancelledError:
        pass
    if groq_brain:
        await groq_brain.close()
    if _http_client:
        await _http_client.aclose()
    await redis.aclose()


app = FastAPI(
    title="ACP Decision Service",
    description="Global decision engine for Agent Control Plane",
    version="1.0.0",
    lifespan=lifespan,
)

setup_app(app, "decision")

from sdk.common.auth import verify_internal_secret


@app.post("/evaluate", response_model=Decision)
async def evaluate_decision(
    req: OrchestrationRequest,
    _: str = Depends(verify_internal_secret),
    x_agent_claims: str | None = None,
) -> Decision:
    """
    Orchestrates context evaluation:
    1. Agent status resolved from X-Agent-Claims header (zero Registry calls)
       or falls back to Registry HTTP if header absent (old tokens / admin path).
    2. Records Usage & Checks Budget (CostEngine)
    3. Fan-out: Policy + Behavior in parallel
    4. Computes final Decision via DecisionEngine
    """
    import json as _json

    headers = {
        "X-Internal-Secret": settings.INTERNAL_SECRET,
        "X-Tenant-ID": str(req.tenant_id)
    }

    if not _http_client:
        raise httpx.HTTPStatusError("Service Unavailable: HTTP Client not initialized", request=None, response=None)
    client: httpx.AsyncClient = _http_client

    # 1. Resolve agent data — prefer JWT claims over Registry HTTP
    agent_meta: dict = {}

    # Gateway injects X-Agent-Claims with JWT permissions when available
    raw_claims = req.metadata.get("agent_claims") if req.metadata else None
    if raw_claims:
        try:
            agent_meta = _json.loads(raw_claims) if isinstance(raw_claims, str) else raw_claims
        except (ValueError, TypeError) as exc:
            # Per production_hardening_spec: never silently swallow parse errors —
            # log so we can correlate decision drift with malformed JWT claims.
            logger.warning(
                "decision_agent_claims_parse_failed",
                agent_id=str(req.agent_id),
                tenant_id=str(req.tenant_id),
                error=str(exc),
            )

    if not agent_meta and req.agent_id != _NIL_UUID:
        registry_url = f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{req.agent_id}"
        try:
            agent_res = await client.get(registry_url, headers=headers, timeout=_T_FAST)
            if agent_res.status_code == 200:
                reg_json = agent_res.json()
                agent_meta = reg_json.get("data", reg_json) if reg_json.get("success") else reg_json
        except Exception as exc:
            logger.warning("registry_unreachable_in_decision", error=str(exc))

    agent_status = agent_meta.get("status", agent_meta.get("agent_status", "active"))
    if agent_status in ("quarantined", "terminated"):
        return Decision(action="deny", risk=1.0, reasons=[f"Agent is {agent_status.upper()}"])

    # Defense-in-depth permission check (uses embedded claims when available)
    allowed_tools = [
        p["tool_name"]
        for p in agent_meta.get("permissions", [])
        if str(p.get("action", "")).upper() == "ALLOW"
    ]
    if allowed_tools and req.tool not in allowed_tools and "*" not in allowed_tools:
        return Decision(action="deny", risk=1.0, reasons=[f"Tool '{req.tool}' not in agent permissions"])

    # 2. Fan-out: Policy (OPA) + Behavior in parallel.
    # Behavior is timed and classified independently so we can record
    # service_status + latency on every consult (the audit/metrics path
    # below depends on this even when the policy half is healthy).
    opa_payload = {
        "tenant_id": str(req.tenant_id),
        "agent_id": str(req.agent_id),
        "tool": req.tool,
        "risk_score": req.inference_risk,
        "behavior_history": [],
        "request_id": req.request_id,
        "metadata": {"client_ip": req.client_ip},
    }
    behavior_payload = {
        "tenant_id": str(req.tenant_id),
        "agent_id":  str(req.agent_id),
        "tool":      req.tool,
        "tokens":    req.tokens,
    }

    behavior_started = time.monotonic()
    fanout_timed_out = False
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                client.post(
                    f"{settings.POLICY_SERVICE_URL.rstrip('/')}/policy/evaluate",
                    json=opa_payload, headers=headers, timeout=_T_GATHER,
                ),
                client.post(
                    f"{settings.BEHAVIOR_SERVICE_URL.rstrip('/')}/analyze",
                    json=behavior_payload, headers=headers, timeout=_T_GATHER,
                ),
                return_exceptions=True,
            ),
            timeout=_T_GATHER_TOTAL,
        )
    except asyncio.TimeoutError:
        logger.warning("decision_fanout_timeout", agent_id=str(req.agent_id))
        results = [None, None]
        fanout_timed_out = True

    policy_res, behavior_res = results[0], results[1]
    behavior_latency_ms = int((time.monotonic() - behavior_started) * 1000)

    cost_risk = 0.0
    # Cost risk calculation moved to Behavior service or calculated here if needed.

    policy_data: dict = {"allowed": False, "reason": "policy_timeout", "risk_adjustment": 0.0}
    if isinstance(policy_res, httpx.Response) and policy_res.status_code == 200:
        policy_data.update(policy_res.json().get("data", {}))
    elif isinstance(policy_res, httpx.Response) and policy_res.status_code == 403:
        policy_data.update({"allowed": False, "reason": policy_res.json().get("detail", "Access Denied")})

    # Behavior consult — classify precisely so the audit row + metrics carry the
    # real service_status. The previous code routed every non-200 path through
    # the same "behavior_service_unavailable" branch which (a) lied about timeouts
    # vs errors and (b) silently fail-opened the engine when the floor wasn't
    # enough to cross a threshold.
    behavior_status, behavior_data, returned_score = classify_behavior_result(
        behavior_res, fanout_timed_out=fanout_timed_out
    )

    if behavior_status != "ok":
        logger.warning(
            "behavior_consult_degraded",
            service_status=behavior_status,
            latency_ms=behavior_latency_ms,
            agent_id=str(req.agent_id),
            tenant_id=str(req.tenant_id),
            tool=req.tool,
        )
        try:
            from sdk.utils import BEHAVIOR_FAIL_CLOSED_TOTAL
            BEHAVIOR_FAIL_CLOSED_TOTAL.inc()
        except ImportError as exc:
            logger.debug("behavior_metric_unavailable", error=str(exc))

    # Resolve tenant degraded_mode_policy — gateway threads it via metadata.
    degraded_mode_policy = (
        (req.metadata or {}).get("degraded_mode_policy") or DEFAULT_DEGRADED_MODE_POLICY
    )

    degraded = apply_degraded_mode_policy(
        degraded_mode_policy,
        tool=req.tool,
        inference_risk=float(req.inference_risk or 0.0),
        inference_flags=list(req.inference_flags or []),
        behavior_data=behavior_data,
        service_status=behavior_status,
    )
    behavior_data = degraded.behavior_data
    policy_applied = degraded.policy_applied if behavior_status != "ok" else "behavior_consulted"

    # Unconditional behavior_firewall_decision audit row — the source of truth
    # for the "we consulted behavior on every call" product claim. Synchronous
    # so a Redis stall surfaces as a 5xx instead of silently losing audit
    # evidence; the call site sits behind the 2.0s gateway SLA.
    behavior_audit_decision = (
        degraded.short_circuit.action.value if degraded.short_circuit is not None else "consulted"
    )
    behavior_audit_reason = (
        "; ".join(behavior_data.get("flags", []) or []) or None
    )
    try:
        await push_audit_event(
            redis=redis,
            tenant_id=req.tenant_id,
            agent_id=req.agent_id,
            action="behavior_firewall_decision",
            tool=req.tool,
            decision=behavior_audit_decision,
            reason=behavior_audit_reason,
            metadata={
                "service_status":  behavior_status,
                "latency_ms":      behavior_latency_ms,
                "returned_score":  returned_score,
                "policy_applied":  policy_applied,
                "request_id":      req.request_id,
                "behavior_flags":  list(behavior_data.get("flags", []) or []),
            },
            request_id=req.request_id,
        )
    except Exception as exc:
        # Audit emission failures are critical (this is the product-claim row)
        # but must not break the decision path. Log loudly so the operator can
        # correlate with a Redis incident.
        logger.error(
            "behavior_firewall_audit_failed",
            error=str(exc),
            request_id=req.request_id,
            service_status=behavior_status,
        )

    # Prometheus accounting — every consult, including short-circuited ones.
    try:
        from sdk.utils import (
            BEHAVIOR_FIREWALL_CONSULT_TOTAL,
            BEHAVIOR_FIREWALL_LATENCY_SECONDS,
        )
        BEHAVIOR_FIREWALL_CONSULT_TOTAL.labels(result=behavior_status).inc()
        BEHAVIOR_FIREWALL_LATENCY_SECONDS.observe(behavior_latency_ms / 1000.0)
    except ImportError as exc:
        logger.debug("behavior_firewall_metric_unavailable", error=str(exc))

    # If the degraded-mode policy short-circuits, also emit the extra
    # degraded_mode_fail_open row when applicable, then return early.
    if degraded.short_circuit is not None:
        asyncio.create_task(_safe_bg(push_audit_event(
            redis=redis,
            tenant_id=req.tenant_id,
            agent_id=req.agent_id,
            action="decision_evaluate",
            tool=req.tool,
            decision=degraded.short_circuit.action.value,
            reason="; ".join(degraded.short_circuit.reasons) if degraded.short_circuit.reasons else None,
            metadata={
                "risk_score":            degraded.short_circuit.risk,
                "request_id":            req.request_id,
                "degraded_mode_policy":  policy_applied,
                "behavior_service_status": behavior_status,
            },
            request_id=req.request_id,
        )))
        return degraded.short_circuit

    if degraded.emit_fail_open_audit:
        # `allow_with_audit` contract: every fail-open run leaves a dedicated
        # audit row so a separate query can count exactly how often we let a
        # call through without behavior signal.
        try:
            await push_audit_event(
                redis=redis,
                tenant_id=req.tenant_id,
                agent_id=req.agent_id,
                action="degraded_mode_fail_open",
                tool=req.tool,
                decision="allow",
                reason="behavior_degraded_fail_open",
                metadata={
                    "service_status":       behavior_status,
                    "latency_ms":           behavior_latency_ms,
                    "policy_applied":       policy_applied,
                    "request_id":           req.request_id,
                },
                request_id=req.request_id,
            )
        except Exception as exc:
            logger.error(
                "degraded_mode_audit_failed",
                error=str(exc),
                request_id=req.request_id,
            )

    # 4. Check path sensitivity for read_file operations (Issue #4)
    inference_flags = list(req.inference_flags) if req.inference_flags else []
    inference_risk = float(req.inference_risk or 0.0)

    # 4a-pre. PII indicator in non-SQL tool payloads (e.g. crm.get_customer with include_pii)
    if req.metadata and req.tool not in {"query", "db.query", "db.execute", "sql", "db.run"} and not req.tool.startswith("db."):
        if req.metadata.get("include_pii") is True:
            inference_flags.append("PII_ACCESS_REQUESTED")
            inference_risk = max(inference_risk, 0.25)
            logger.info("pii_access_requested", tool=req.tool)

    if req.tool == "read_file":
        file_path = req.metadata.get("path", "") if req.metadata else ""
        if file_path:
            sensitive_dirs = ["/etc/", "/proc/", "/root/", ".ssh/", "/var/log/", "/boot/"]
            if any(file_path.startswith(d) for d in sensitive_dirs):
                inference_flags.append("SENSITIVE_PATH_DETECTED")
                inference_risk = max(inference_risk, 0.75)
                logger.warning("sensitive_path_detected", path=file_path, tool=req.tool)

    # 4b. SQL governance — covers db.query, db.execute, query, and any db.* tool
    _SQL_TOOLS = {"query", "db.query", "db.execute", "sql", "db.run"}
    if req.tool in _SQL_TOOLS or req.tool.startswith("db."):
        sql_query = ""
        if req.metadata:
            sql_query = (
                req.metadata.get("sql") or req.metadata.get("input")
                or req.metadata.get("query") or ""
            )
        if sql_query:
            sql_lower = sql_query.strip().lower()
            # DDL destruction → KILL (inference_risk = 0.95)
            _DDL_HARD = (
                "drop table", "drop database", "drop schema", "drop view",
                "truncate table", "truncate ",
            )
            if any(p in sql_lower for p in _DDL_HARD):
                inference_flags.append("SQL_DDL_DESTRUCTION")
                inference_risk = max(inference_risk, 0.95)
                logger.warning("sql_ddl_detected", tool=req.tool)
            # Unguarded DML → ESCALATE (inference_risk = 0.85)
            elif (("delete from" in sql_lower or "update " in sql_lower)
                  and "where" not in sql_lower):
                inference_flags.append("SQL_UNGUARDED_MUTATION")
                inference_risk = max(inference_risk, 0.85)
                logger.warning("sql_unguarded_mutation", tool=req.tool)
            # SQL injection patterns → ESCALATE (inference_risk = 0.80)
            _INJECT = (
                "where 1=1", "where 1 = 1", "or 1=1", "or '1'='1'",
                "union select", "union all select", "; drop", "xp_", "sp_", "exec(",
            )
            if ("SQL_DDL_DESTRUCTION" not in inference_flags
                    and "SQL_UNGUARDED_MUTATION" not in inference_flags
                    and any(p in sql_lower for p in _INJECT)):
                inference_flags.append("SQL_INJECTION_PATTERN")
                inference_risk = max(inference_risk, 0.80)
                logger.warning("sql_injection_pattern_detected", tool=req.tool)
            # PII/bulk exfiltration — two severity tiers:
            #   explicit PII columns (ssn, credit_card, …) → inference_risk = 0.82
            #   SELECT * bulk read → inference_risk = 0.75
            _PII_COLS = (
                "ssn", "credit_card", "creditcard", "social_security",
                "passport", "salary", "password", "pin", "dob",
                "date_of_birth", "account_number",
            )
            _has_select_star = "select *" in sql_lower or "select\t*" in sql_lower
            _has_pii = any(col in sql_lower for col in _PII_COLS)
            if (not any(f in inference_flags for f in (
                    "SQL_DDL_DESTRUCTION", "SQL_UNGUARDED_MUTATION", "SQL_INJECTION_PATTERN"))
                    and (_has_pii or _has_select_star)):
                inference_flags.append("SQL_PII_EXFILTRATION")
                _pii_risk = 0.82 if _has_pii else 0.75
                inference_risk = max(inference_risk, _pii_risk)
                logger.info("sql_pii_pattern_detected", has_star=_has_select_star,
                            has_pii=_has_pii, tool=req.tool)

    # 5. Assemble DecisionContext and evaluate
    ctx = DecisionContext(
        tenant_id=req.tenant_id,
        agent_id=req.agent_id,
        tool=req.tool,
        request_id=req.request_id,
        policy_allowed=bool(policy_data.get("allowed", False)),
        policy_reason=policy_data.get("reason"),
        policy_risk_adjustment=float(policy_data.get("risk_adjustment", 0.0)),
        inference_risk=inference_risk,
        inference_flags=inference_flags,
        behavior_risk=float(behavior_data.get("behavior_risk", 0.0)),
        anomaly_score=float(behavior_data.get("anomaly_score", 0.0)),
        cost_risk=float(behavior_data.get("cost_risk", 0.0)),
        cross_agent_risk=float(behavior_data.get("cross_agent_risk", 0.0)),
        confidence=float(behavior_data.get("confidence", 1.0)),
        behavior_flags=list(behavior_data.get("flags", [])),
    )

    decision = decision_engine.evaluate(ctx)

    # Surface degraded-mode findings in the response so callers + auditors
    # can see, on every fall-through allow, that the behavior firewall did
    # not produce a fresh signal for this call. Sprint 2.2: `extra_reasons`
    # values are already canonical-vocabulary (Sprint 1.1 uses
    # ``behavior_degraded_*`` exclusively), so they pass validation.
    if degraded.extra_reasons:
        merged_findings = list(decision.findings or [])
        for extra in degraded.extra_reasons:
            if extra not in merged_findings:
                merged_findings.append(extra)
        # Keep findings + the deprecated reasons alias in lockstep.
        decision = decision.model_copy(update={
            "findings": merged_findings,
            "reasons":  list(merged_findings),
        })

    # AI-Powered Security Brain (Groq LLM enrichment — optional override)
    if groq_brain and (decision.risk >= 0.30 or decision.action.value != "allow"):
        try:
            ai_decision = await asyncio.wait_for(groq_brain.evaluate(ctx, decision), timeout=0.5)
            if ai_decision:
                decision = ai_decision
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("groq_brain_eval_failed", error=str(exc))

    # Async audit logging (non-blocking, best-effort) — wrapped to swallow
    # background task exceptions so a transient Redis blip cannot surface as
    # an "unhandled task exception" log line on the hot path.
    asyncio.create_task(_safe_bg(push_audit_event(
        redis=redis,
        tenant_id=req.tenant_id,
        agent_id=req.agent_id,
        action="decision_evaluate",
        tool=req.tool,
        decision=decision.action.value,
        reason="; ".join(str(r) for r in decision.reasons) if decision.reasons else None,
        metadata={
            "risk_score": decision.risk,
            "signals": getattr(decision, "signals", {}),
            "request_id": req.request_id,
        },
        request_id=req.request_id,
    )))

    # Push high-risk events to Groq analytics queue (async, best-effort)
    if decision.action.value in ("block", "kill", "escalate", "deny"):
        try:
            await redis.xadd(
                "acp:groq_queue",
                {
                    "event_id": str(uuid.uuid4()),
                    "agent_id": str(req.agent_id),
                    "tenant_id": str(req.tenant_id),
                    "risk_score": str(decision.risk),
                    "decision": decision.action.value,
                    "tool": req.tool,
                    "payload_hash": req.payload_hash,
                },
                maxlen=10_000,
            )
        except Exception as _xadd_err:
            logger.error("groq_queue_xadd_failed", error=str(_xadd_err))

    return decision

app.include_router(decision_router)
