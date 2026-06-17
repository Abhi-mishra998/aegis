"""
Audit Router — HTTP endpoints for the audit service.

FIX C-1 (downstream): create_log() now handles AuditWriter returning None (duplicate)
by raising HTTP 409 instead of crashing with PydanticUserError.
FIX: get_redis() now uses the shared settings.REDIS_URL constant.
"""

from __future__ import annotations

import json as _json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.config import settings
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.audit.integrity import verify_audit_chain
from services.audit.models import AuditLog, AuditNote, PendingUsageEvent
from services.audit.schemas import (
    AuditLogCreate,
    AuditLogListResponse,
    AuditLogResponse,
    AuditLogSearch,
    AuditSummaryResponse,
)
from services.audit.writer import AuditWriter

router = APIRouter(prefix="/logs", tags=["audit"], dependencies=[Depends(verify_internal_secret)])


async def get_redis() -> AsyncGenerator[Redis, None]:
    r: Redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)  # type: ignore[arg-type]
    try:
        yield r
    finally:
        await r.aclose()


async def _check_rate(redis: Redis, key: str, limit: int, window: int = 60) -> None:
    """Per-tenant DOS ceiling on state-mutating routes (token-bucket-lite)."""
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    if count > limit:
        ttl = await redis.ttl(key)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(1, ttl))},
        )


@router.post(
    "",
    response_model=APIResponse[AuditLogResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_log(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    payload: AuditLogCreate,
) -> APIResponse[AuditLogResponse]:
    """Internal log injection endpoint."""
    await _check_rate(
        redis,
        f"acp:ratelimit:audit:logs_create:{payload.tenant_id}",
        limit=600,
    )
    log_entry = await AuditWriter.log(db, redis, payload)

    # C-1 fix: log_entry is None when it was a duplicate — return 409
    if log_entry is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate audit event (request_id+event_hash already exists)",
        )

    # Update real-time metrics
    tid = str(payload.tenant_id)
    await redis.incr(f"acp:metrics:total_calls:{tid}")
    if payload.decision == "deny":
        await redis.incr(f"acp:metrics:total_denials:{tid}")
    await redis.sadd(f"acp:metrics:active_agents:{tid}", str(payload.agent_id))

    risk_level = payload.metadata_json.get("risk_level", "low")
    await redis.incr(f"acp:metrics:risk_distribution:{tid}:{risk_level}")

    # Publish deny / high-risk events to audit events stream for ARE ingestion
    risk_score = float(payload.metadata_json.get("risk_score", 0))
    if payload.decision in ("deny", "kill", "escalate") or risk_score >= 0.7:
        await redis.xadd(
            "acp:audit:events",
            {
                "data": _json.dumps({
                    "tenant_id":       tid,
                    "agent_id":        str(payload.agent_id),
                    "tool":            payload.tool or "unknown",
                    "severity":        payload.metadata_json.get("severity",
                                           "HIGH" if risk_score >= 0.8 else "MEDIUM"),
                    "risk_score":      risk_score,
                    "violation_count": payload.metadata_json.get("violation_count", 1),
                    "decision":        payload.decision,
                    "request_id":      str(payload.request_id) if payload.request_id else None,
                    "title":           payload.reason or "",
                    "source":          "audit_router",
                })
            },
            maxlen=100_000,
            approximate=True,
        )

    return APIResponse(data=AuditLogResponse.model_validate(log_entry))


from services.audit.aggregator import AuditAggregator


@router.get("/summary", response_model=APIResponse[AuditSummaryResponse])
async def get_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
) -> APIResponse[AuditSummaryResponse]:
    """Fast dashboard summary from Redis counters + Deep DB insights."""
    tid = str(tenant_id)
    # Sprint 1 BE — optional per-agent scope filter
    _agent_filter = (AuditLog.agent_id == agent_id) if agent_id is not None else sa.true()

    # Aggregate totals directly from DB so counts are always accurate,
    # regardless of whether Redis counters were populated (they're only
    # incremented at runtime and are reset on container restart).
    from sqlalchemy import case as sa_case
    execute_actions = {"execute_tool", "decision_evaluate"}
    count_q = await db.execute(
        select(
            func.count().label("total"),
            func.sum(sa_case((AuditLog.decision.in_(["deny", "block"]), 1), else_=0)).label("blocked"),
            func.sum(sa_case((AuditLog.decision == "allow", 1), else_=0)).label("allowed"),
            func.sum(sa_case((AuditLog.action.in_(execute_actions), 1), else_=0)).label("exec_total"),
        ).where(AuditLog.tenant_id == tenant_id).where(_agent_filter)
    )
    row = count_q.one()
    total_reqs  = int(row.exec_total or 0)
    blocked_reqs = int(row.blocked or 0)
    allowed_reqs = int(row.allowed or 0)
    agent_count = int(await redis.scard(f"acp:metrics:active_agents:{tid}") or 0)

    # Risk distribution from DB metadata_json
    risk_dist_q = await db.execute(
        select(
            func.sum(sa_case((sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float) >= 0.80, 1), else_=0)).label("critical"),
            func.sum(sa_case(
                (sa.and_(sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float) >= 0.60,
                         sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float) < 0.80), 1), else_=0)).label("high"),
            func.sum(sa_case(
                (sa.and_(sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float) >= 0.30,
                         sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float) < 0.60), 1), else_=0)).label("medium"),
            func.sum(sa_case((sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float) < 0.30, 1), else_=0)).label("low"),
        ).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.metadata_json["risk_score"].isnot(None),
        )
    )
    try:
        rd = risk_dist_q.one()
        risk_dist = {
            "critical": int(rd.critical or 0),
            "high": int(rd.high or 0),
            "medium": int(rd.medium or 0),
            "low": int(rd.low or 0),
        }
    except Exception:
        risk_dist = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    # Deep Insights from AuditAggregator (DB)
    top_risky = await AuditAggregator.get_top_risky_agents(db, tenant_id, limit=5, agent_id=agent_id)
    trends = await AuditAggregator.get_anomaly_trends(db, tenant_id, days=7, agent_id=agent_id)

    # Avg risk score from DB
    avg_risk_result = await db.execute(
        select(func.avg(
            sa.cast(AuditLog.metadata_json["risk_score"], sa.Float)
        )).where(AuditLog.tenant_id == tenant_id).where(_agent_filter)
    )
    avg_risk = float(avg_risk_result.scalar_one_or_none() or 0.0)

    return APIResponse(
        data=AuditSummaryResponse(
            total_calls=total_reqs,
            total_denials=blocked_reqs,
            active_agents_count=agent_count,
            total_requests=total_reqs,
            blocked_requests=blocked_reqs,
            allowed_requests=allowed_reqs,
            threats_blocked=blocked_reqs,
            high_risk_agents=risk_dist.get("critical", 0) + risk_dist.get("high", 0),
            avg_risk_score=round(avg_risk, 4),
            requests_by_hour=[],
            risk_distribution=risk_dist,
            metadata={
                "top_risky_agents": top_risky,
                "anomaly_trends": trends,
            }
        )
    )

@router.get("/heatmap", response_model=APIResponse[dict])
async def get_heatmap(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
) -> APIResponse[dict]:
    """Return request-volume heatmap: {day_name: [count_h0..count_h23]} for last 7 days."""
    import datetime as _dt
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=7)
    _agent_filter = (AuditLog.agent_id == agent_id) if agent_id is not None else sa.true()
    dow_col  = func.extract("dow",  AuditLog.timestamp).label("dow")
    hour_col = func.extract("hour", AuditLog.timestamp).label("hour")
    q = (
        select(dow_col, hour_col, func.count().label("cnt"))
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= cutoff)
        .where(_agent_filter)
        .group_by(dow_col, hour_col)
    )
    rows = (await db.execute(q)).all()
    # PostgreSQL DOW: 0=Sun, 1=Mon … 6=Sat
    _dow_map = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 0: "Sun"}
    _days    = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    heatmap: dict[str, list[int]] = {d: [0] * 24 for d in _days}
    for row in rows:
        day_name = _dow_map.get(int(row.dow), "Mon")
        heatmap[day_name][int(row.hour)] = int(row.cnt)
    return APIResponse(data=heatmap)


@router.get("/trends", response_model=APIResponse[list[dict]])
async def get_trends(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days: int = Query(7, ge=1, le=30),
) -> APIResponse[list[dict]]:
    """Get time-series anomaly trends for UI charts."""
    trends = await AuditAggregator.get_anomaly_trends(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=trends)


@router.get("/risk/timeline", response_model=APIResponse[list[dict]])
async def risk_timeline(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days: int = Query(7, ge=1, le=30),
) -> APIResponse[list[dict]]:
    """Return 7-day risk timeline trends."""
    trends = await AuditAggregator.get_anomaly_trends(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=trends)


@router.get("/risk/top-threats", response_model=APIResponse[list[dict]])
async def risk_top_threats(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
) -> APIResponse[list[dict]]:
    """Return top high-risk agents in the specified window."""
    agents = await AuditAggregator.get_top_risky_agents(db, tenant_id, limit=limit, agent_id=agent_id)
    return APIResponse(data=agents)


@router.get("", response_model=APIResponse[AuditLogListResponse])
async def list_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = None,
    action: str | None = None,
    decision: str | None = None,
    tool: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(10, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> APIResponse[AuditLogListResponse]:
    """List audit logs with filtering and pagination.

    Supports the same filter set as the POST /search variant so the UI
    can stay on GET. Reason: AWS WAFv2 SQLi managed rule blocks JSON
    bodies that contain ``"limit":<n>`` (it reads ``LIMIT N`` as SQL
    injection), so the search-by-POST path returned HTML 403 from the
    edge. GET query params bypass body inspection.
    """
    query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

    if agent_id:
        query = query.where(AuditLog.agent_id == agent_id)
    if action:
        query = query.where(AuditLog.action == action)
    if decision:
        query = query.where(AuditLog.decision == decision)
    if tool:
        query = query.where(AuditLog.tool == tool)
    if start_date:
        query = query.where(AuditLog.timestamp >= start_date)
    if end_date:
        query = query.where(AuditLog.timestamp <= end_date)

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    items = result.scalars().all()

    # Ensure response is always properly structured (not a scalar)
    items_list = [AuditLogResponse.model_validate(item) for item in items]
    assert isinstance(items_list, list), "Items must be a list"

    response_data = AuditLogListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=items_list,
    )
    assert isinstance(response_data.items, list), "Response.items must be a list"

    return APIResponse(data=response_data)


@router.post("/search", response_model=APIResponse[AuditLogListResponse])
async def search_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    payload: AuditLogSearch,
) -> APIResponse[AuditLogListResponse]:
    """Advanced search for audit logs with date ranging and metadata filtering."""
    query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

    if payload.agent_id:
        query = query.where(AuditLog.agent_id == payload.agent_id)
    if payload.action:
        query = query.where(AuditLog.action == payload.action)
    if payload.decision:
        query = query.where(AuditLog.decision == payload.decision)
    if payload.tool:
        query = query.where(AuditLog.tool == payload.tool)
    if payload.start_date:
        query = query.where(AuditLog.timestamp >= payload.start_date)
    if payload.end_date:
        query = query.where(AuditLog.timestamp <= payload.end_date)
    if payload.metadata_filter:
        query = query.where(AuditLog.metadata_json.contains(payload.metadata_filter))

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        query.order_by(AuditLog.timestamp.desc())
        .offset(payload.offset)
        .limit(payload.limit)
    )
    result = await db.execute(query)
    items = result.scalars().all()

    # Ensure response is always properly structured (not a scalar)
    items_list = [AuditLogResponse.model_validate(item) for item in items]
    assert isinstance(items_list, list), "Items must be a list"

    response_data = AuditLogListResponse(
        total=total,
        limit=payload.limit,
        offset=payload.offset,
        items=items_list,
    )
    assert isinstance(response_data.items, list), "Response.items must be a list"

    return APIResponse(data=response_data)


@router.get("/soc-timeline", response_model=APIResponse[list[dict]])
async def soc_timeline(
    db:        Annotated[AsyncSession, Depends(get_db)],
    redis:     Annotated[Redis, Depends(get_redis)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit:     int = Query(60, ge=1, le=200),
) -> APIResponse[list[dict]]:
    """
    Aggregated SOC event feed. Merges:
    - Audit log deny/kill/escalate decisions
    - High-risk events (risk_score >= 0.7)
    Returns a unified timeline sorted newest-first.
    """
    # Fetch security-relevant audit events
    q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(
            sa.or_(
                AuditLog.decision.in_(["deny", "kill", "escalate"]),
                sa.cast(AuditLog.metadata_json["risk_score"], sa.Float) >= 0.7,
            )
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    rows = (await db.execute(q)).scalars().all()

    def _sev(row: AuditLog) -> str:
        risk = float((row.metadata_json or {}).get("risk_score", 0))
        dec  = (row.decision or "").lower()
        if dec == "kill" or risk >= 0.90:
            return "CRITICAL"
        if dec == "deny" or risk >= 0.70:
            return "HIGH"
        if risk >= 0.50:
            return "MEDIUM"
        return "LOW"

    def _type(row: AuditLog) -> str:
        dec = (row.decision or "").lower()
        if dec == "kill":
            return "agent_kill"
        if dec == "escalate":
            return "escalation"
        if dec == "deny":
            return "policy_deny"
        return "high_risk"

    def _msg(row: AuditLog) -> str:
        dec  = (row.decision or "allow").upper()
        tool = row.tool or "unknown"
        risk = float((row.metadata_json or {}).get("risk_score", 0))
        reason = (row.reason or "")[:80]
        return f"{dec} — {tool} (risk {risk:.0%}){f': {reason}' if reason else ''}"

    events = [
        {
            "id":        str(row.id),
            "type":      _type(row),
            "severity":  _sev(row),
            "agent_id":  str(row.agent_id),
            "timestamp": row.timestamp.isoformat() if row.timestamp else "",
            "message":   _msg(row),
            "tool":      row.tool,
            "decision":  row.decision,
            "risk_score": float((row.metadata_json or {}).get("risk_score", 0)),
        }
        for row in rows
    ]

    return APIResponse(data=events)


@router.get("/verify", response_model=APIResponse[dict])
async def verify_integrity(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Perform a cryptographic integrity check on the audit log chain.

    Always returns 200; the boolean result lives in the payload as `valid`
    (mirrored to `is_integrous` for back-compat). The UI uses these fields
    to render "Chain Valid" vs "Chain Broken" — returning a non-2xx status
    causes generic error handling to mask the violation count, so we keep
    the HTTP semantics and let the body carry the verdict.
    """
    result = await verify_audit_chain(db, tenant_id)
    return APIResponse(data=result)


# ---------------------------------------------------------------------------
# Cryptographic execution receipts (ed25519). The audit row IS the source of
# truth; the receipt is a signed projection of it. Anyone with the public key
# can verify offline.
# ---------------------------------------------------------------------------

@router.get("/{execution_id}/receipt", response_model=APIResponse[dict])
async def get_receipt(
    execution_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Return a signed, verifiable receipt for one audit row.

    `execution_id` may be either the audit row UUID or the upstream
    `request_id` string (carried by Flight Recorder timelines). UUID is
    tried first; on parse failure, the value is treated as a request_id
    and the most recent matching row is returned.

    Response shape:
        { receipt, signature, algorithm, public_key_fingerprint }
    """
    from services.audit.signer import get_signer

    row = None
    try:
        as_uuid = uuid.UUID(execution_id)
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.id == as_uuid, AuditLog.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        pass

    if row is None:
        row = (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.request_id == execution_id,
                    AuditLog.tenant_id == tenant_id,
                )
                .order_by(AuditLog.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="no audit row matches the given execution_id")

    return APIResponse(data=get_signer().sign(row))


# ---------------------------------------------------------------------------
# Decision Root Cause — human-readable explanation of why a decision was made
# ---------------------------------------------------------------------------

_FINDING_LABELS: dict[str, str] = {
    "sql_injection":        "SQL injection pattern detected in query",
    "ddl_destruction":      "DDL statement that would destroy or truncate a table",
    "path_traversal":       "File path traversal attempt (e.g. ../etc/passwd)",
    "pii_exfiltration":     "Potential personally-identifiable data exfiltration",
    "data_exfiltration":    "Bulk data read consistent with exfiltration",
    "anomaly":              "Statistical anomaly in tool call frequency/pattern",
    "high_risk":            "Composite risk score exceeds policy threshold",
    "bulk_operation":       "Operation targets an unusually large number of records",
    "unguarded_mutation":   "Write/delete without a WHERE clause guard",
    "prompt_injection":     "Adversarial content attempting to override system prompt",
    "sensitive_path":       "Access to a sensitive filesystem path",
    "rate_limit":           "Request rate exceeded tenant or agent quota",
    "cost_cap":             "Inference cost cap reached for this agent",
}


@router.get("/{audit_id}/explain", response_model=APIResponse[dict])
async def explain_decision(
    audit_id: str,
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """
    Return a structured root-cause explanation for one audit decision.

    Accepts either the row UUID or the upstream request_id.
    Response:
      audit_id, decision, risk_score, findings, explanation, signals,
      policy_context, timeline (last 5 decisions by same agent)
    """
    # ── Fetch target row ─────────────────────────────────────────────────────
    row: AuditLog | None = None
    try:
        as_uuid = uuid.UUID(audit_id)
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.id == as_uuid,
                    AuditLog.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        pass

    if row is None:
        row = (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.request_id == audit_id,
                    AuditLog.tenant_id == tenant_id,
                )
                .order_by(AuditLog.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="audit row not found")

    # ── Extract signals from metadata_json ───────────────────────────────────
    meta = row.metadata_json or {}
    if isinstance(meta, str):
        try:
            meta = _json.loads(meta)
        except Exception:
            meta = {}

    risk_score = float(meta.get("risk_score") or 0)
    raw_reasons = meta.get("reasons") or []
    if isinstance(raw_reasons, str):
        raw_reasons = [r.strip() for r in raw_reasons.split(";") if r.strip()]
    findings = [str(r) for r in raw_reasons] if raw_reasons else []

    # Also pull findings from row.reason field
    if not findings and row.reason:
        findings = [r.strip() for r in row.reason.split(";") if r.strip()]

    signals = [
        {
            "finding": f,
            "label": _FINDING_LABELS.get(f, f.replace("_", " ").title()),
            "triggered": True,
        }
        for f in findings
    ]

    # ── Build human explanation ───────────────────────────────────────────────
    decision = row.decision or "unknown"

    if decision in ("deny", "block"):
        if risk_score >= 0.9:
            risk_phrase = f"a critical risk score of {risk_score:.2f}"
        elif risk_score >= 0.7:
            risk_phrase = f"a high risk score of {risk_score:.2f}"
        else:
            risk_phrase = f"a risk score of {risk_score:.2f}"

        if findings:
            finding_summary = ", ".join(
                _FINDING_LABELS.get(f, f) for f in findings[:3]
            )
            explanation = (
                f"This {row.action or 'operation'} on tool '{row.tool}' was denied "
                f"because the Aegis policy engine detected {risk_phrase}. "
                f"Triggered findings: {finding_summary}."
            )
        else:
            explanation = (
                f"This {row.action or 'operation'} on tool '{row.tool}' was denied "
                f"because the Aegis policy engine detected {risk_phrase}. "
                f"The OPA policy enforced a block at this risk level."
            )

    elif decision == "escalate":
        explanation = (
            f"This operation on tool '{row.tool}' required human approval "
            f"(risk score {risk_score:.2f}). It was escalated to a reviewer "
            f"before execution was permitted."
        )

    elif decision in ("allow", "monitor"):
        explanation = (
            f"This operation on tool '{row.tool}' was permitted "
            f"(risk score {risk_score:.2f}). "
            + (f"Monitoring findings: {', '.join(findings[:3])}." if findings else
               "No policy violations detected.")
        )

    else:
        explanation = (
            f"Decision '{decision}' recorded for tool '{row.tool}' "
            f"with risk score {risk_score:.2f}."
        )

    # ── Contextual timeline: last 5 decisions by same agent ──────────────────
    timeline_rows = (
        await db.execute(
            select(AuditLog)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.agent_id == row.agent_id,
                AuditLog.id != row.id,
            )
            .order_by(AuditLog.timestamp.desc())
            .limit(5)
        )
    ).scalars().all()

    timeline = [
        {
            "audit_id":   str(r.id),
            "action":     r.action,
            "tool":       r.tool,
            "decision":   r.decision,
            "timestamp":  r.timestamp.isoformat() if r.timestamp else None,
            "risk_score": float(
                (r.metadata_json or {}).get("risk_score", 0)
                if isinstance(r.metadata_json, dict) else 0
            ),
        }
        for r in timeline_rows
    ]

    return APIResponse(data={
        "audit_id":       str(row.id),
        "request_id":     row.request_id,
        "agent_id":       str(row.agent_id),
        "action":         row.action,
        "tool":           row.tool,
        "decision":       decision,
        "risk_score":     risk_score,
        "findings":       findings,
        "signals":        signals,
        "explanation":    explanation,
        "policy_context": {
            "framework": "OPA",
            "version":   meta.get("policy_version", "v1"),
        },
        "timestamp":      row.timestamp.isoformat() if row.timestamp else None,
        "timeline":       timeline,
    })


# ---------------------------------------------------------------------------
# Analyst Notes — add / list investigation annotations on audit entries
# ---------------------------------------------------------------------------

_NOTE_TYPES = {"analysis", "false_positive", "confirmed_threat", "escalated"}


class _NoteCreate(BaseModel):
    note_type: str = "analysis"
    body: str
    created_by: str = "analyst"


@router.post("/{audit_id}/notes", response_model=APIResponse[dict], status_code=201)
async def add_audit_note(
    audit_id: str,
    payload: _NoteCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Append an analyst note to an audit log entry."""
    await _check_rate(
        redis,
        f"acp:ratelimit:audit:notes_create:{tenant_id}",
        limit=600,
    )
    try:
        audit_uuid = uuid.UUID(audit_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="audit_id must be a valid UUID")

    if payload.note_type not in _NOTE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"note_type must be one of: {', '.join(sorted(_NOTE_TYPES))}",
        )
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="body must not be empty")

    # Verify the audit row belongs to this tenant
    row = (await db.execute(
        select(AuditLog)
        .where(AuditLog.id == audit_uuid, AuditLog.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="audit row not found")

    note = AuditNote(
        id=uuid.uuid4(),
        audit_id=audit_uuid,
        tenant_id=tenant_id,
        created_by=payload.created_by[:255],
        note_type=payload.note_type,
        body=payload.body.strip(),
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)

    return APIResponse(data={
        "id":         str(note.id),
        "audit_id":   str(note.audit_id),
        "note_type":  note.note_type,
        "body":       note.body,
        "created_by": note.created_by,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    })


@router.get("/{audit_id}/notes", response_model=APIResponse[list])
async def list_audit_notes(
    audit_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[list]:
    """Return all analyst notes for one audit log entry, oldest first."""
    try:
        audit_uuid = uuid.UUID(audit_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="audit_id must be a valid UUID")

    notes = (await db.execute(
        select(AuditNote)
        .where(AuditNote.audit_id == audit_uuid, AuditNote.tenant_id == tenant_id)
        .order_by(AuditNote.created_at.asc())
    )).scalars().all()

    return APIResponse(data=[
        {
            "id":         str(n.id),
            "audit_id":   str(n.audit_id),
            "note_type":  n.note_type,
            "body":       n.body,
            "created_by": n.created_by,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notes
    ])


# ---------------------------------------------------------------------------
# Agent Behavioral Drift Detection
# ---------------------------------------------------------------------------

@router.get("/drift/{agent_id}", response_model=APIResponse[dict])
async def get_agent_drift(
    agent_id:         str,
    db:               Annotated[AsyncSession, Depends(get_db)],
    tenant_id:        Annotated[uuid.UUID, Depends(get_tenant_id)],
    baseline_days:    int = Query(7, ge=1, le=30),
    comparison_hours: int = Query(24, ge=1, le=168),
) -> APIResponse[dict]:
    """
    Return a behavioral drift report for one agent.

    Compares the agent's rolling *baseline* (last ``baseline_days`` days)
    against its *recent* activity (last ``comparison_hours`` hours).

    Response fields:
      drift_score      — composite 0–1 (0 = identical to baseline)
      drift_level      — low / medium / high / critical
      baseline         — avg_risk, deny_rate, call_volume, unique_tools
      recent           — same metrics for the comparison window
      metrics          — per-signal drift components
    """
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="agent_id must be a valid UUID")

    report = await AuditAggregator.get_agent_drift_report(
        db,
        agent_id=agent_uuid,
        tenant_id=tenant_id,
        baseline_days=baseline_days,
        comparison_hours=comparison_hours,
    )
    return APIResponse(data=report)


# ---------------------------------------------------------------------------
# Top Security Findings Frequency
# ---------------------------------------------------------------------------

@router.get("/top-findings", response_model=APIResponse[dict])
async def get_top_findings(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days:      int = Query(30, ge=1, le=90),
    limit:     int = Query(15, ge=1, le=50),
) -> APIResponse[dict]:
    """Frequency ranking of canonical security findings over the past ``days`` days.

    Unnests ``metadata_json->findings[]`` and counts each finding code.
    Returns top ``limit`` findings sorted by occurrence count descending.
    """
    result = await AuditAggregator.get_top_findings(
        db, tenant_id=tenant_id, days=days, limit=limit,
    )
    return APIResponse(data=result)


# ---------------------------------------------------------------------------
# Agent Peer Benchmarking
# ---------------------------------------------------------------------------

@router.get("/peer-benchmark/{agent_id}", response_model=APIResponse[dict])
async def get_agent_peer_benchmark(
    agent_id:  str,
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Percentile rank of one agent vs. all others in the tenant.

    Returns deny_rate, avg_risk, and call_volume percentile ranks (0-100)
    plus tenant-wide p50/p75/p95 reference values.
    """
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="agent_id must be a valid UUID")

    benchmark = await AuditAggregator.get_agent_peer_benchmark(
        db, agent_id=agent_uuid, tenant_id=tenant_id, days=days,
    )
    return APIResponse(data=benchmark)


# ---------------------------------------------------------------------------
# Tool-Level Risk Breakdown
# ---------------------------------------------------------------------------

@router.get("/tool-breakdown", response_model=APIResponse[dict])
async def get_tool_risk_breakdown(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=1, le=90),
    limit:     int = Query(20, ge=1, le=100),
) -> APIResponse[dict]:
    """Top tools by denial rate and average risk score over the past ``days`` days.

    Response: list of tools with total_calls, denied_calls, deny_rate, avg_risk.
    """
    breakdown = await AuditAggregator.get_tool_risk_breakdown(
        db, tenant_id=tenant_id, limit=limit, days=days, agent_id=agent_id,
    )
    return APIResponse(data=breakdown)


# ---------------------------------------------------------------------------
# Per-Agent Risk Score Trend (30-day rolling daily series)
# ---------------------------------------------------------------------------

@router.get("/risk-trend/{agent_id}", response_model=APIResponse[dict])
async def get_agent_risk_trend(
    agent_id:  str,
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Daily risk score trend for one agent over the past ``days`` days.

    Response: series (list of daily buckets) + summary (max_risk, avg_risk,
    total_denials, active_days).
    """
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="agent_id must be a valid UUID")

    trend = await AuditAggregator.get_agent_risk_trend(
        db,
        agent_id=agent_uuid,
        tenant_id=tenant_id,
        days=days,
    )
    return APIResponse(data=trend)


@router.get("/hourly-activity", response_model=APIResponse[dict])
async def get_hourly_activity(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(7, ge=1, le=30),
) -> APIResponse[dict]:
    """24-bucket hour-of-day activity breakdown — request count, deny count, avg risk."""
    result = await AuditAggregator.get_hourly_activity(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/risk-histogram", response_model=APIResponse[dict])
async def get_risk_histogram(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=1, le=90),
    bins:      int = Query(10, ge=5, le=20),
) -> APIResponse[dict]:
    """Risk score frequency distribution — equal-width histogram over [0, 1]."""
    result = await AuditAggregator.get_risk_histogram(db, tenant_id, days=days, bins=bins, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/weekly-heatmap", response_model=APIResponse[dict])
async def get_weekly_heatmap(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(28, ge=7, le=90),
) -> APIResponse[dict]:
    """7×24 request count grid by day-of-week × hour-of-day."""
    result = await AuditAggregator.get_weekly_heatmap(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/decision-trend", response_model=APIResponse[dict])
async def get_decision_trend(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Daily decision outcome breakdown — allow/deny/escalate/monitor/kill."""
    result = await AuditAggregator.get_decision_trend(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/agent-activity", response_model=APIResponse[dict])
async def get_agent_activity(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit:     int = Query(20, ge=5, le=100),
) -> APIResponse[dict]:
    """Per-agent activity summary — first/last seen, call count, deny rate, avg risk."""
    result = await AuditAggregator.get_agent_activity_summary(db, tenant_id, limit=limit)
    return APIResponse(data=result)


@router.get("/high-risk-events", response_model=APIResponse[dict])
async def get_high_risk_events(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int   = Query(7,   ge=1,   le=30),
    limit:     int   = Query(20,  ge=5,   le=100),
    threshold: float = Query(0.7, ge=0.0, le=1.0),
) -> APIResponse[dict]:
    """Most recent audit events at or above the risk score threshold."""
    result = await AuditAggregator.get_high_risk_events(
        db, tenant_id, days=days, limit=limit, threshold=threshold, agent_id=agent_id,
    )
    return APIResponse(data=result)


@router.get("/deny-reasons", response_model=APIResponse[dict])
async def get_deny_reasons(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=1, le=90),
    limit:     int = Query(15, ge=5, le=50),
) -> APIResponse[dict]:
    """Top deny/kill reason strings by frequency."""
    result = await AuditAggregator.get_deny_reasons(db, tenant_id, days=days, limit=limit, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/tool-usage/{agent_id}", response_model=APIResponse[dict])
async def get_agent_tool_usage(
    agent_id: uuid.UUID,
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days:      int = Query(30, ge=1, le=90),
) -> APIResponse[dict]:
    """Per-tool call stats (calls, deny_rate, avg_risk) for a single agent."""
    result = await AuditAggregator.get_agent_tool_usage(db, agent_id, tenant_id, days=days)
    return APIResponse(data=result)


@router.get("/tool-risk", response_model=APIResponse[dict])
async def get_tool_risk_leaderboard(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=1, le=90),
    limit:     int = Query(20, ge=5, le=50),
) -> APIResponse[dict]:
    """Cross-agent tool risk leaderboard ordered by deny count."""
    result = await AuditAggregator.get_tool_risk_leaderboard(db, tenant_id, days=days, limit=limit, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/risk-percentile-trend", response_model=APIResponse[dict])
async def get_risk_percentile_trend(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Daily p50/p75/p95 risk score percentiles over the given window."""
    result = await AuditAggregator.get_risk_percentile_trend(db, tenant_id, days=days)
    return APIResponse(data=result)


@router.get("/daily-active-agents", response_model=APIResponse[dict])
async def get_daily_active_agents(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Distinct active agents per day over the given window."""
    result = await AuditAggregator.get_daily_active_agents(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/finding-breakdown", response_model=APIResponse[dict])
async def get_finding_breakdown(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=1, le=90),
    limit:     int = Query(20, ge=5, le=50),
) -> APIResponse[dict]:
    """Ranked frequency of canonical finding types."""
    result = await AuditAggregator.get_finding_breakdown(db, tenant_id, days=days, limit=limit, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/agent-daily-decisions/{agent_id}", response_model=APIResponse[dict])
async def get_agent_daily_decisions(
    agent_id:  uuid.UUID,
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Daily allow/deny counts for a single agent."""
    result = await AuditAggregator.get_agent_daily_decisions(db, agent_id, tenant_id, days=days)
    return APIResponse(data=result)


@router.get("/agent-findings/{agent_id}", response_model=APIResponse[dict])
async def get_agent_finding_breakdown(
    agent_id:  uuid.UUID,
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days:      int = Query(30, ge=1, le=90),
    limit:     int = Query(15, ge=5, le=50),
) -> APIResponse[dict]:
    """Ranked finding type frequency for a single agent."""
    result = await AuditAggregator.get_agent_finding_breakdown(
        db, agent_id, tenant_id, days=days, limit=limit
    )
    return APIResponse(data=result)


@router.get("/posture-score-trend", response_model=APIResponse[dict])
async def get_posture_score_trend(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Daily tenant posture score (allow% of total decisions)."""
    result = await AuditAggregator.get_posture_score_trend(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=result)


@router.get("/escalation-rate-trend", response_model=APIResponse[dict])
async def get_escalation_rate_trend(
    db:        Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
    days:      int = Query(30, ge=7, le=90),
) -> APIResponse[dict]:
    """Daily escalation rate (escalate% of total decisions)."""
    result = await AuditAggregator.get_escalation_rate_trend(db, tenant_id, days=days, agent_id=agent_id)
    return APIResponse(data=result)


# ---------------------------------------------------------------------------
# Tamper-evident export — NDJSON stream for SIEM ingest (Splunk / Datadog / S3)
# ---------------------------------------------------------------------------

@router.get("/export")
async def export_chain(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    since: str | None = Query(None, description="ISO-8601 lower bound on timestamp (inclusive)"),
    until: str | None = Query(None, description="ISO-8601 upper bound on timestamp (exclusive)"),
    agent_id: uuid.UUID | None = None,
    chain_shard: int | None = Query(None, ge=0),
    limit: int = Query(10000, ge=1, le=100000),
):
    """Stream the tamper-evident audit chain as NDJSON.

    Each line is one audit record with its integrity fields (`event_hash`,
    `prev_hash`, `chain_shard`) so a downstream SIEM can re-verify the chain
    on its own. Designed for Splunk HEC, Datadog Logs ingest, and S3 Object
    Lock archives. Newest rows first.

    Content-Type: `application/x-ndjson`
    """
    from datetime import datetime as _dt

    from fastapi.responses import StreamingResponse

    query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if agent_id:
        query = query.where(AuditLog.agent_id == agent_id)
    if chain_shard is not None:
        query = query.where(AuditLog.chain_shard == chain_shard)
    if since:
        try:
            query = query.where(AuditLog.timestamp >= _dt.fromisoformat(since))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid `since` (need ISO-8601): {since}")
    if until:
        try:
            query = query.where(AuditLog.timestamp < _dt.fromisoformat(until))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid `until` (need ISO-8601): {until}")

    query = query.order_by(AuditLog.timestamp.desc()).limit(limit)

    async def _iter():
        # Stream in batches so a large tenant doesn't pin all rows in memory.
        # SQLAlchemy async result streaming chunks rows by 100 per yield_per.
        result = await db.stream(query.execution_options(yield_per=500))
        async for row in result.scalars():
            payload = {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "agent_id": str(row.agent_id) if row.agent_id else None,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "action": row.action,
                "tool": row.tool,
                "decision": row.decision,
                "reason": row.reason,
                "request_id": row.request_id,
                "event_hash": row.event_hash,
                "prev_hash": row.prev_hash,
                "chain_shard": row.chain_shard,
                "billing_status": row.billing_status,
                "metadata": row.metadata_json or {},
            }
            yield (_json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

    return StreamingResponse(
        _iter(),
        media_type="application/x-ndjson",
        headers={
            "X-ACP-Chain-Format": "ndjson-v1",
            "Cache-Control": "no-store",
        },
    )


@router.get("/outbox-depth", response_model=APIResponse[dict], dependencies=[Depends(verify_internal_secret)])
async def outbox_depth(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    """
    Aggregate counts of pending/failed/completed rows in the audit outbox.
    Consumed by /system/health on the gateway so operators see the durability
    backlog in real time (Transactional Outbox visibility per the production
    hardening spec).
    """
    from services.audit.models import PendingUsageEvent
    stmt = (
        select(PendingUsageEvent.status, func.count(PendingUsageEvent.id))
        .group_by(PendingUsageEvent.status)
    )
    result = await db.execute(stmt)
    counts = {row[0]: int(row[1]) for row in result.all()}
    return APIResponse(data={
        "pending":   counts.get("pending",   0),
        "completed": counts.get("completed", 0),
        "failed":    counts.get("failed",    0),
    })


# ---------------------------------------------------------------------------
# BILLING RECONCILIATION SUPPORT ENDPOINTS
# Used by the Usage Service to query billing gaps and mark records complete
# without cross-database SQL.
# ---------------------------------------------------------------------------

@router.get("/billing-gaps", response_model=APIResponse[list[dict]])
async def get_billing_gaps(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit: int = 1000,
    sla_seconds: int = 60,
) -> APIResponse[list[dict]]:
    """Returns pending audit logs older than sla_seconds for a specific tenant."""
    from sqlalchemy import text
    stmt = text("""
        SELECT id, tenant_id, agent_id, tool
        FROM audit_logs
        WHERE billing_status = 'pending'
          AND action != 'management_api'
          AND tenant_id = :tid
          AND timestamp > NOW() - INTERVAL '5 minutes'
          AND timestamp < NOW() - make_interval(secs => :sla)
        LIMIT :lim
    """)
    res = await db.execute(stmt, {"tid": tenant_id, "sla": sla_seconds, "lim": limit})
    rows = res.fetchall()
    return APIResponse(data=[
        {"id": str(r.id), "tenant_id": str(r.tenant_id),
         "agent_id": str(r.agent_id), "tool": r.tool}
        for r in rows
    ])


@router.get("/billing-gaps/all", response_model=APIResponse[list[dict]])
async def get_all_billing_gaps(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 1000,
    sla_seconds: int = 60,
) -> APIResponse[list[dict]]:
    """All-tenant billing gaps for the reconciliation worker (internal-secret only)."""
    from sqlalchemy import text
    stmt = text("""
        SELECT id, tenant_id, agent_id, tool
        FROM audit_logs
        WHERE billing_status = 'pending'
          AND action != 'management_api'
          AND timestamp > NOW() - INTERVAL '5 minutes'
          AND timestamp < NOW() - make_interval(secs => :sla)
        LIMIT :lim
    """)
    res = await db.execute(stmt, {"sla": sla_seconds, "lim": limit})
    rows = res.fetchall()
    return APIResponse(data=[
        {"id": str(r.id), "tenant_id": str(r.tenant_id),
         "agent_id": str(r.agent_id), "tool": r.tool}
        for r in rows
    ])


class BillingStatusUpdate(BaseModel):
    audit_ids: list[uuid.UUID]


@router.patch("/billing-status/complete", response_model=APIResponse[dict])
async def mark_billing_complete(
    payload: BillingStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    """
    Bulk-mark audit logs as billing_status='completed'.
    Called by Usage Service after successful usage_record insertion.

    Note: This is the ONLY permitted mutation of audit_logs rows. The HMAC
    chain covers the audit content columns (action, tool, decision, metadata_json,
    prev_hash) but not this billing-metadata column, so cryptographic integrity
    of the audit chain is maintained. The "append-only" invariant refers to
    audit content, not billing lifecycle metadata.
    """
    from sqlalchemy import text
    stmt = text("""
        UPDATE audit_logs
        SET billing_status = 'completed'
        WHERE id = ANY(:ids)
    """)
    await db.execute(stmt, {"ids": payload.audit_ids})
    await db.commit()
    return APIResponse(data={"updated": len(payload.audit_ids)})


@router.get("/billing-stats", response_model=APIResponse[dict])
async def get_billing_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """
    Returns unbilled_events_sla and pending_events counts for dashboard.
    Runs on the Audit Service DB (acp_audit) where audit_logs actually lives.
    """
    from sqlalchemy import text
    sla_stmt = text("""
        SELECT
            COUNT(*) FILTER (WHERE timestamp < NOW() - INTERVAL '60 seconds') AS unbilled_sla,
            COUNT(*) FILTER (WHERE timestamp >= NOW() - INTERVAL '60 seconds') AS pending
        FROM audit_logs
        WHERE billing_status = 'pending'
          AND action != 'management_api'
          AND tenant_id = :tid
    """)
    res = await db.execute(sla_stmt, {"tid": tenant_id})
    row = res.fetchone()
    return APIResponse(data={
        "unbilled_events_sla": int(row[0] or 0) if row else 0,
        "pending_events": int(row[1] or 0) if row else 0,
        "total_audit_logs": 0,  # populated below
    })


# ============================================================================
# OUTBOX PATTERN: Pending Usage Events (consumed by usage service worker)
# ============================================================================

pending_router = APIRouter(prefix="/pending-usage-events", tags=["outbox"], dependencies=[Depends(verify_internal_secret)])


@pending_router.get("", response_model=APIResponse[list[dict]])
async def list_pending_usage_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str, Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> APIResponse[list[dict]]:
    """List pending usage events from the outbox (called by usage service worker)."""
    stmt = (
        select(PendingUsageEvent)
        .where(PendingUsageEvent.status == status)
        .order_by(PendingUsageEvent.created_at)
        .limit(limit)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()

    return APIResponse(data=[
        {
            "id": str(e.id),
            "tenant_id": str(e.tenant_id),
            "audit_id": str(e.audit_id),
            "agent_id": str(e.agent_id) if e.agent_id else None,
            "tool": e.tool,
            "units": e.units,
            "cost": e.cost,
            "status": e.status,
            "retry_count": e.retry_count,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ])


class MarkPendingCompleteRequest(BaseModel):
    """Request to mark pending events as completed."""
    event_ids: list[str]


@pending_router.patch("/complete", response_model=APIResponse[dict])
async def mark_pending_events_complete(
    db: Annotated[AsyncSession, Depends(get_db)],
    req: MarkPendingCompleteRequest,
) -> APIResponse[dict]:
    """Mark pending usage events as completed (called by usage service worker after processing)."""
    if not req.event_ids:
        return APIResponse(data={"updated": 0})

    # Convert string IDs to UUIDs
    audit_ids = [uuid.UUID(id_str) for id_str in req.event_ids]

    # Update pending_usage_events: status = 'completed', processed_at = now()
    stmt = (
        sa.update(PendingUsageEvent)
        .where(PendingUsageEvent.audit_id.in_(audit_ids))
        .values(status="completed", processed_at=func.now())
    )
    result = await db.execute(stmt)
    await db.commit()

    return APIResponse(data={"updated": result.rowcount or 0})
