import math
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.audit_stream import push_audit_event
from sdk.common.auth import mesh_headers
from sdk.common.auth import verify_internal_secret
from sdk.common.config import settings
from sdk.common.db import get_db, get_tenant_id
from sdk.common.deadline import check_deadline
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from services.registry.repository import AgentRepository, PermissionRepository
from services.registry.schemas import (
    AgentCreate,
    AgentListResponse,
    AgentResponse,
    AgentUpdate,
    PermissionCreate,
    PermissionResponse,
)
from services.registry.service import AgentService

router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(verify_internal_secret)])
logger = structlog.get_logger()

async def get_redis():
    r = get_redis_client(settings.REDIS_URL)
    try:
        yield r
    finally:
        await r.aclose()


def get_agent_service(db: Annotated[AsyncSession, Depends(get_db)]) -> AgentService:
    repo = AgentRepository(db)
    perm_repo = PermissionRepository(db)
    return AgentService(repo, perm_repo)


# =========================
# AGENTS
# =========================


@router.post(
    "", response_model=APIResponse[AgentResponse], status_code=status.HTTP_201_CREATED
)
async def create_agent(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    payload: AgentCreate,
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[AgentResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    bound_logger = logger.bind(
        request_id=request_id, tenant_id=str(tenant_id), owner_id=payload.owner_id
    )

    response = await service.create_agent(tenant_id, payload)

    # Enforce strict SaaS invariant: org_id == tenant_id
    from sdk.common.invariants import InvariantViolation, assert_org_consistency
    try:
        assert_org_consistency(response.org_id, tenant_id, "registry agent creation")
    except InvariantViolation as e:
        # P1: Immediate abort and DB rollback if invariant violated
        raise HTTPException(status_code=500, detail=str(e))

    # RULE 2: Every action is audited
    # P1-1 FIX: Removed __aenter__() anti-pattern; use client directly
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        await push_audit_event(
            redis=_redis,
            tenant_id=tenant_id,
            agent_id=response.id,
            action="agent_registration",
            request_id=request_id,
            metadata={"name": response.name, "owner_id": response.owner_id}
        )
    finally:
        await _redis.aclose()

    bound_logger.info("agent_created", agent_id=str(response.id), name=response.name)
    return APIResponse(data=response)


@router.get("", response_model=APIResponse[AgentListResponse])
async def list_agents(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    owner_id: str | None = None,
    status_val: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[AgentListResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    bound_logger = logger.bind(request_id=request_id, tenant_id=str(tenant_id))

    response = await service.list_agents(tenant_id, owner_id, status_val, page, size)
    bound_logger.info("agents_listed", count=len(response.items), total=response.total)
    return APIResponse(data=response)


@router.get("/summary", response_model=APIResponse[dict])
async def get_agent_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Fleet-wide summary: counts by status and by risk level."""
    from sqlalchemy import func, select

    from services.registry.models import Agent

    rows = (
        await db.execute(
            select(Agent.status, Agent.risk_level, func.count().label("cnt"))
            .where(Agent.tenant_id == tenant_id)
            .where(Agent.deleted_at.is_(None))
            .group_by(Agent.status, Agent.risk_level)
        )
    ).all()

    total = sum(r.cnt for r in rows)
    active = sum(r.cnt for r in rows if str(r.status).upper() == "ACTIVE")
    quarantined = sum(r.cnt for r in rows if str(r.status).upper() == "QUARANTINED")
    terminated = sum(r.cnt for r in rows if str(r.status).upper() == "TERMINATED")
    high_risk = sum(r.cnt for r in rows if str(r.risk_level).lower() in ("high", "critical"))

    return APIResponse(data={
        "total": total,
        "active": active,
        "quarantined": quarantined,
        "terminated": terminated,
        "high_risk": high_risk,
    })


@router.get("/tools", response_model=APIResponse[list[str]])
async def list_registered_tools(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[list[str]]:
    """Return deduplicated list of tool names across all registered agents for this tenant."""
    from sqlalchemy import select  # noqa: PLC0415

    from services.registry.models import Agent  # noqa: PLC0415

    stmt = select(Agent).where(Agent.tenant_id == tenant_id, Agent.deleted_at.is_(None))
    result = await db.execute(stmt)
    agents = result.scalars().all()

    tools: set[str] = set()
    for agent in agents:
        meta = agent.metadata_data or {}
        for t in (meta.get("tools") or meta.get("allowed_tools") or []):
            if isinstance(t, str) and t.strip():
                tools.add(t.strip())
        for t in (meta.get("tool_names") or []):
            if isinstance(t, str) and t.strip():
                tools.add(t.strip())

    # Also pull from the permissions table (tool_name column)
    from services.registry.models import AgentPermission  # noqa: PLC0415
    perm_stmt = select(AgentPermission.tool_name).where(AgentPermission.tenant_id == tenant_id)
    perm_result = await db.execute(perm_stmt)
    for (tool_name,) in perm_result.all():
        if tool_name and tool_name.strip():
            tools.add(tool_name.strip())

    DEFAULT_TOOLS = [
        "read_file", "write_file", "execute_code", "web_search",
        "send_email", "query_database", "call_api", "list_files",
    ]
    for t in DEFAULT_TOOLS:
        tools.add(t)

    return APIResponse(data=sorted(tools))


@router.get(
    "/{agent_id}",
    response_model=APIResponse[AgentResponse],
    summary="Get detailed agent metadata",
)
async def get_agent(
    agent_id: uuid.UUID,
    service: Annotated[AgentService, Depends(get_agent_service)],
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    request_id: str = Header(None, alias="X-Request-ID"),
) -> APIResponse[AgentResponse]:
    """Fetch metadata including current tool permissions and status."""
    agent_id_uuid = agent_id
    bound_logger = logger.bind(
        request_id=request_id, tenant_id=str(tenant_id), agent_id=str(agent_id)
    )

    response = await service.get_agent(tenant_id, agent_id_uuid)
    bound_logger.info("agent_retrieved")
    return APIResponse(data=response)


@router.get(
    "/{agent_id}/profile",
    summary="Get agent behavioral profile derived from audit logs",
)
async def get_agent_profile(
    agent_id: uuid.UUID,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    request_id: str = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """Fetch per-agent behavioral stats by querying the audit service."""
    bound_logger = logger.bind(
        request_id=request_id, tenant_id=str(tenant_id), agent_id=str(agent_id)
    )

    audit_url = settings.AUDIT_SERVICE_URL.rstrip("/")
    headers = {
        **mesh_headers("registry"),
        "X-Tenant-ID": str(tenant_id),
    }

    try:
        client = request.app.state.client
        resp = await client.get(
            f"{audit_url}/logs",
            params={"agent_id": str(agent_id), "limit": 200},
            headers=headers,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Audit service error")
        payload = resp.json()
        # Support both {data: {items: [...]}} and {data: [...]}
        data = payload.get("data", {})
        if isinstance(data, dict):
            logs = data.get("items", [])
        elif isinstance(data, list):
            logs = data
        else:
            logs = []
    except HTTPException:
        raise
    except Exception as exc:
        bound_logger.warning("agent_profile_audit_fetch_failed", error=str(exc))
        logs = []

    now_utc = datetime.now(UTC)

    total_decisions = len(logs)
    allowed = sum(1 for r in logs if (r.get("decision") or "").lower() in ("allow", "allowed"))
    blocked = total_decisions - allowed
    block_rate = round((blocked / total_decisions * 100), 2) if total_decisions else 0.0

    # Average risk score
    risk_scores = [
        float((r.get("metadata_json") or {}).get("risk_score", 0.0))
        for r in logs
    ]
    avg_risk_score = round(sum(risk_scores) / len(risk_scores), 4) if risk_scores else 0.0

    # Last 7 days avg risk per day
    day_scores: dict[str, list[float]] = {}
    for r in logs:
        ts_raw = r.get("timestamp") or r.get("created_at")
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                day_key = ts.strftime("%Y-%m-%d")
                score = float((r.get("metadata_json") or {}).get("risk_score", 0.0))
                day_scores.setdefault(day_key, []).append(score)
            except (ValueError, AttributeError):
                pass

    risk_trend: list[float] = []
    for i in range(6, -1, -1):
        day = (now_utc - timedelta(days=i)).strftime("%Y-%m-%d")
        scores = day_scores.get(day, [])
        risk_trend.append(round(sum(scores) / len(scores), 4) if scores else 0.0)

    # Top 5 tools
    tool_counts: Counter = Counter()
    for r in logs:
        tool = r.get("tool")
        if tool:
            tool_counts[tool] += 1
    top_tools = [
        {"tool": t, "count": c} for t, c in tool_counts.most_common(5)
    ]

    # Last active
    timestamps = []
    for r in logs:
        ts_raw = r.get("timestamp") or r.get("created_at")
        if ts_raw:
            try:
                timestamps.append(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")))
            except (ValueError, AttributeError):
                pass
    last_active = max(timestamps).isoformat() if timestamps else None

    # Behavioral drift: today's avg_risk > 7-day-avg * 1.3
    today_key = now_utc.strftime("%Y-%m-%d")
    today_scores = day_scores.get(today_key, [])
    today_avg = sum(today_scores) / len(today_scores) if today_scores else 0.0
    seven_day_avg = sum(risk_trend) / len(risk_trend) if risk_trend else 0.0
    behavioral_drift = today_avg > (seven_day_avg * 1.3) if seven_day_avg > 0 else False

    # Anomaly score: z-score of today vs 7-day window
    non_zero_trend = [v for v in risk_trend if v > 0]
    if len(non_zero_trend) >= 2:
        mean_trend = sum(non_zero_trend) / len(non_zero_trend)
        variance = sum((v - mean_trend) ** 2 for v in non_zero_trend) / len(non_zero_trend)
        std_dev = math.sqrt(variance) if variance > 0 else 0.0
        anomaly_score = round(abs(today_avg - mean_trend) / std_dev, 4) if std_dev > 0 else 0.0
    else:
        anomaly_score = 0.0

    bound_logger.info("agent_profile_computed", total_decisions=total_decisions)
    return {
        "agent_id": str(agent_id),
        "total_decisions": total_decisions,
        "allowed": allowed,
        "blocked": blocked,
        "block_rate": block_rate,
        "avg_risk_score": avg_risk_score,
        "risk_trend": risk_trend,
        "top_tools": top_tools,
        "last_active": last_active,
        "behavioral_drift": behavioral_drift,
        "anomaly_score": anomaly_score,
    }


@router.patch("/{agent_id}", response_model=APIResponse[AgentResponse])
async def update_agent(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    agent_id: uuid.UUID,
    payload: AgentUpdate,
) -> APIResponse[AgentResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    bound_logger = logger.bind(
        request_id=request_id, tenant_id=str(tenant_id), agent_id=str(agent_id)
    )

    response = await service.update_agent(tenant_id, agent_id, payload)

    # RULE 2: Every action is audited
    # P1-1 FIX: Removed __aenter__() anti-pattern; use client directly
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        await push_audit_event(
            redis=_redis,
            tenant_id=tenant_id,
            agent_id=agent_id,
            action="agent_update",
            request_id=request_id,
            metadata={"updates": payload.model_dump(exclude_unset=True)}
        )
    finally:
        await _redis.aclose()

    bound_logger.info("agent_updated")
    return APIResponse(data=response)


@router.delete(
    "/{agent_id}", response_model=APIResponse[None], status_code=status.HTTP_200_OK
)
async def delete_agent(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    agent_id: uuid.UUID,
) -> APIResponse[None]:
    request_id = getattr(request.state, "request_id", "unknown")
    bound_logger = logger.bind(
        request_id=request_id, tenant_id=str(tenant_id), agent_id=str(agent_id)
    )

    await service.delete_agent(tenant_id, agent_id)

    # arch-26 W1.3 2026-06-26 — audit emit cannot 500 a successful delete.
    # The row is already gone (service.delete_agent committed). If the
    # audit stream is unreachable / validation raises, log it and return
    # 200 anyway — the customer should not see "internal server error"
    # AND a ghost row both. The audit DLQ replay path picks this up.
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        try:
            await push_audit_event(
                redis=_redis,
                tenant_id=tenant_id,
                agent_id=agent_id,
                action="agent_delete",
                request_id=request_id,
                metadata={},
            )
        except Exception as audit_exc:
            bound_logger.warning(
                "agent_delete_audit_emit_failed",
                error_type=type(audit_exc).__name__,
                error=str(audit_exc)[:200],
            )
    finally:
        await _redis.aclose()

    bound_logger.info("agent_deleted")
    return APIResponse(data=None)


# =========================
# QUARANTINE (Sprint B 2026-06-14 — blast-radius layer)
# =========================
# A compromised agent should stop dead within seconds, not require
# operator log-on. POST /agents/{id}/quarantine sets a Redis flag the
# gateway middleware short-circuits on AND flips registry.agents.status
# to "quarantined" so the change survives a Redis flush + shows up in
# Fleet UI. DELETE clears both.


@router.post(
    "/{agent_id}/quarantine",
    response_model=APIResponse[dict],
    status_code=status.HTTP_200_OK,
)
async def quarantine_agent_endpoint(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    agent_id: uuid.UUID,
) -> APIResponse[dict]:
    """Mark agent quarantined: Redis flag + status='quarantined' + audit row.
    Idempotent — quarantining an already-quarantined agent is a no-op."""
    request_id = getattr(request.state, "request_id", "unknown")
    body = await request.json() if request.headers.get("content-length") else {}
    reason = (body.get("reason") if isinstance(body, dict) else None) or "manual"

    _redis = get_redis_client(settings.REDIS_URL)
    try:
        key = f"acp:quarantine:{tenant_id}:{agent_id}"
        await _redis.setex(key, 86_400, reason)

        # Persistent status flip (survives Redis flush).
        try:
            await service.update_agent(
                tenant_id, agent_id,
                AgentUpdate(status="quarantined"),
            )
        except Exception as exc:
            # If status enum doesn't accept the value we still hold the
            # Redis flag — gateway short-circuit is the source of truth.
            logger.warning("quarantine_status_update_skip", error=str(exc))

        await push_audit_event(
            redis=_redis, tenant_id=tenant_id, agent_id=agent_id,
            action="agent_quarantined",
            request_id=request_id, metadata={"reason": reason, "ttl_s": 86_400},
        )
    finally:
        await _redis.aclose()

    logger.warning("agent_quarantined", agent_id=str(agent_id), reason=reason)
    return APIResponse(data={"agent_id": str(agent_id),
                             "quarantined": True, "reason": reason,
                             "ttl_seconds": 86_400})


@router.delete(
    "/{agent_id}/quarantine",
    response_model=APIResponse[dict],
    status_code=status.HTTP_200_OK,
)
async def release_quarantine_endpoint(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    agent_id: uuid.UUID,
) -> APIResponse[dict]:
    request_id = getattr(request.state, "request_id", "unknown")
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        await _redis.delete(f"acp:quarantine:{tenant_id}:{agent_id}")
        try:
            await service.update_agent(
                tenant_id, agent_id,
                AgentUpdate(status="active"),
            )
        except Exception as exc:
            logger.warning("quarantine_release_status_update_skip", error=str(exc))
        await push_audit_event(
            redis=_redis, tenant_id=tenant_id, agent_id=agent_id,
            action="agent_quarantine_released",
            request_id=request_id, metadata={},
        )
    finally:
        await _redis.aclose()

    return APIResponse(data={"agent_id": str(agent_id), "quarantined": False})


# =========================
# PERMISSIONS
# =========================


@router.post(
    "/{agent_id}/permissions",
    response_model=APIResponse[PermissionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_permission(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    agent_id: uuid.UUID,
    payload: PermissionCreate,
) -> APIResponse[PermissionResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    bound_logger = logger.bind(
        request_id=request_id, tenant_id=str(tenant_id), agent_id=str(agent_id)
    )

    response = await service.add_permission(tenant_id, agent_id, payload)

    # RULE 2: Every action is audited
    # P1-1 FIX: Removed __aenter__() anti-pattern; use client directly
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        await push_audit_event(
            redis=_redis,
            tenant_id=tenant_id,
            agent_id=agent_id,
            action="permission_grant",
            tool=payload.tool_name,
            metadata={"permission_id": str(response.id), "action_type": payload.action}
        )
    finally:
        await _redis.aclose()

    bound_logger.info(
        "permission_added", permission_id=str(response.id), tool_name=response.tool_name
    )
    return APIResponse(data=response)


@router.get(
    "/{agent_id}/permissions",
    response_model=APIResponse[list[PermissionResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_permissions(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    agent_id: uuid.UUID,
) -> APIResponse[list[PermissionResponse]]:
    request_id = getattr(request.state, "request_id", "unknown")
    bound_logger = logger.bind(
        request_id=request_id, tenant_id=str(tenant_id), agent_id=str(agent_id)
    )

    response = await service.get_agent_permissions(tenant_id, agent_id)
    bound_logger.info("permissions_listed", count=len(response))
    return APIResponse(data=response)


@router.delete(
    "/{agent_id}/permissions/{permission_id}",
    response_model=APIResponse[None],
    status_code=status.HTTP_200_OK,
)
async def revoke_permission(
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    agent_id: uuid.UUID,
    permission_id: uuid.UUID,
) -> APIResponse[None]:
    request_id = getattr(request.state, "request_id", "unknown")
    bound_logger = logger.bind(
        request_id=request_id,
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        permission_id=str(permission_id),
    )

    await service.remove_permission(tenant_id, agent_id, permission_id)

    # RULE 2: Every action is audited
    # P1-1 FIX: Removed __aenter__() anti-pattern; use client directly
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        await push_audit_event(
            redis=_redis,
            tenant_id=tenant_id,
            agent_id=agent_id,
            action="permission_revoke",
            request_id=request_id,
            metadata={"permission_id": str(permission_id)}
        )
    finally:
        await _redis.aclose()

    bound_logger.info("permission_deleted")
    return APIResponse(data=None)
