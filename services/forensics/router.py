import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from services.audit.models import AuditLog

# Forensics is an analytics consumer that reads the audit DB directly —
# this is deliberate (see infra/docker-compose.yml forensics.environment).
# We never import from other service containers (decision, behavior, etc.)
# to keep the replay logic self-contained and deployable independently.

router = APIRouter(prefix="/forensics", tags=["Forensics"], dependencies=[Depends(verify_internal_secret)])

@router.get("/investigation", tags=["Forensics"])
async def list_investigations(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: AsyncSession = Depends(get_db),
    limit: int = 20
) -> dict[str, Any]:
    """Return list of recent high-risk investigations scoped to the authenticated tenant."""
    stmt = (
        select(AuditLog)
        .where(AuditLog.decision == "deny", AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return {
        "success": True,
        "data": [
            {
                "id": str(log.id),
                "agent_id": str(log.agent_id),
                "timestamp": log.timestamp.isoformat(),
                "tool": log.tool,
                "risk_score": log.metadata_json.get("risk_score", 0.0),
                "reason": log.metadata_json.get("reason", "Malicious intent")
            }
            for log in logs
        ]
    }

@router.get("/replay/{agent_id}")
async def replay_agent_behavior(
    agent_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit: int = 50,
    db: AsyncSession = Depends(get_db)  # noqa: B008
) -> dict[str, Any]:
    """
    Forensic Replay: returns historical audit events for an agent with stored
    risk signals. The risk scores are the values recorded at execution time —
    no live re-evaluation is performed so the replay is stable and independent
    of model version drift.
    """
    stmt = (
        select(AuditLog)
        .where(AuditLog.agent_id == agent_id, AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    if not logs:
        raise HTTPException(status_code=404, detail="No audit logs found for this agent.")

    return {
        "agent_id": str(agent_id),
        "replay_count": len(logs),
        "results": [
            {
                "event_id": str(entry.id),
                "timestamp": entry.timestamp,
                "tool": entry.tool,
                "decision": entry.decision.upper() if entry.decision else "UNKNOWN",
                "risk_score": (entry.metadata_json or {}).get("risk_score", 0.0),
                "reasons": (entry.metadata_json or {}).get("reasons", []),
                "request_id": entry.request_id,
            }
            for entry in logs
        ],
    }


@router.get("/investigation/{agent_id}")
async def get_investigation_report(
    agent_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """
    Build a full investigation profile for an agent combining audit history
    and risk signals stored at execution time.
    """
    total_stmt = select(func.count(AuditLog.id)).where(
        AuditLog.agent_id == agent_id, AuditLog.tenant_id == tenant_id
    )
    total_res = await db.execute(total_stmt)
    total_events = total_res.scalar_one_or_none() or 0

    recent_stmt = (
        select(AuditLog)
        .where(AuditLog.agent_id == agent_id, AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(20)
    )
    recent_res = await db.execute(recent_stmt)
    recent_logs = recent_res.scalars().all()

    if not recent_logs:
        raise HTTPException(status_code=404, detail="No data found for this agent.")

    decisions = {}
    for log in recent_logs:
        decisions[log.decision] = decisions.get(log.decision, 0) + 1

    avg_risk = (
        sum(log.metadata_json.get("risk_score", 0.0) for log in recent_logs)
        / len(recent_logs)
    )

    return {
        "agent_id": str(agent_id),
        "total_events": total_events,
        "avg_risk_score": round(avg_risk, 4),
        "decision_breakdown": decisions,
        "recent_events": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat(),
                "tool": log.tool,
                "decision": log.decision,
                "risk_score": log.metadata_json.get("risk_score", 0.0),
                "reasons": log.metadata_json.get("reasons", []),
            }
            for log in recent_logs
        ],
    }
