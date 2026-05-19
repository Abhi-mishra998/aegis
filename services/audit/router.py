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
from services.audit.models import AuditLog, PendingUsageEvent
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
) -> APIResponse[AuditSummaryResponse]:
    """Fast dashboard summary from Redis counters + Deep DB insights."""
    tid = str(tenant_id)

    # 1. Real-time counters from Redis
    total_calls = await redis.get(f"acp:metrics:total_calls:{tid}") or 0
    total_denials = await redis.get(f"acp:metrics:total_denials:{tid}") or 0
    agent_count = await redis.scard(f"acp:metrics:active_agents:{tid}") or 0

    total_reqs = int(total_calls)
    blocked_reqs = int(total_denials)
    allowed_reqs = total_reqs - blocked_reqs

    risk_dist = {
        "critical": int(await redis.get(f"acp:metrics:risk_distribution:{tid}:critical") or 0),
        "high": int(await redis.get(f"acp:metrics:risk_distribution:{tid}:high") or 0),
        "medium": int(await redis.get(f"acp:metrics:risk_distribution:{tid}:medium") or 0),
        "low": int(await redis.get(f"acp:metrics:risk_distribution:{tid}:low") or 0),
    }

    # 2. Deep Insights from AuditAggregator (DB)
    top_risky = await AuditAggregator.get_top_risky_agents(db, tenant_id, limit=5)
    trends = await AuditAggregator.get_anomaly_trends(db, tenant_id, days=7)

    # 3. Avg risk score from DB
    avg_risk_result = await db.execute(
        select(func.avg(
            sa.cast(AuditLog.metadata_json["risk_score"], sa.Float)
        )).where(AuditLog.tenant_id == tenant_id)
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

@router.get("/trends", response_model=APIResponse[list[dict]])
async def get_trends(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days: int = Query(7, ge=1, le=30),
) -> APIResponse[list[dict]]:
    """Get time-series anomaly trends for UI charts."""
    trends = await AuditAggregator.get_anomaly_trends(db, tenant_id, days=days)
    return APIResponse(data=trends)


@router.get("/risk/timeline", response_model=APIResponse[list[dict]])
async def risk_timeline(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    days: int = Query(7, ge=1, le=30),
) -> APIResponse[list[dict]]:
    """Return 7-day risk timeline trends."""
    trends = await AuditAggregator.get_anomaly_trends(db, tenant_id, days=days)
    return APIResponse(data=trends)


@router.get("/risk/top-threats", response_model=APIResponse[list[dict]])
async def risk_top_threats(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit: int = Query(10, ge=1, le=50),
) -> APIResponse[list[dict]]:
    """Return top high-risk agents in the specified window."""
    agents = await AuditAggregator.get_top_risky_agents(db, tenant_id, limit=limit)
    return APIResponse(data=agents)


@router.get("", response_model=APIResponse[AuditLogListResponse])
async def list_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = None,
    action: str | None = None,
    decision: str | None = None,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> APIResponse[AuditLogListResponse]:
    """List audit logs with filtering and pagination."""
    query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

    if agent_id:
        query = query.where(AuditLog.agent_id == agent_id)
    if action:
        query = query.where(AuditLog.action == action)
    if decision:
        query = query.where(AuditLog.decision == decision)

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

    tid = str(tenant_id)
    int(await redis.get(f"acp:metrics:total_denials:{tid}") or 0)

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
    """Perform a cryptographic integrity check on the audit log chain."""
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
