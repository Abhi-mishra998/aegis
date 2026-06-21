from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.auth import mesh_headers
from sdk.common.background import safe_bg as _safe_bg
from sdk.common.config import settings
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from sdk.utils import INCIDENT_MTTR_SECONDS  # global unlabelled MTTR gauge (seconds)
from services.api.repository.incident import IncidentRepository, StateTransitionError
from services.api.schemas.incident import (
    IncidentActionRequest,
    IncidentCreate,
    IncidentResponse,
    IncidentSummary,
    IncidentUpdate,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/incidents", tags=["Incidents"])

# ── Prometheus metrics ─────────────────────────────────────────────────────────
_INCIDENT_CREATED = Counter(
    "acp_incident_created_total",
    "Total incidents created",
    ["severity", "trigger"],
)
_INCIDENT_RESOLVED = Counter(
    "acp_incident_resolved_total",
    "Total incidents resolved",
    ["severity"],
)
_INCIDENT_RESOLUTION_SECONDS = Histogram(
    "acp_incident_resolution_seconds",
    "Time from incident creation to resolution (seconds)",
    ["severity"],
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400],
)
_INCIDENT_MTTR_HOURS = Gauge(
    "acp_incident_mttr_hours",
    "Rolling 7-day mean time to recovery in hours (per tenant)",
    ["tenant_id"],
)
# acp_incident_mttr_seconds — global unlabelled gauge for alerting rules —
# is defined in sdk/utils.py and imported as INCIDENT_MTTR_SECONDS above.


_SEV_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}


async def _fire_webhook(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload, headers={"Content-Type": "application/json"})
    except Exception as exc:
        logger.warning("incident_webhook_failed", url=url, error=str(exc))


async def _fire_slack_alert(incident) -> None:
    """Send a richly-formatted Slack alert for CRITICAL/HIGH incidents (Fix 9)."""
    url = settings.SLACK_WEBHOOK_URL
    if not url:
        return
    sev   = incident.severity
    emoji = _SEV_EMOJI.get(sev, "⚠️")
    color = {"CRITICAL": "#ef4444", "HIGH": "#f97316", "MEDIUM": "#eab308", "LOW": "#22c55e"}.get(sev, "#6b7280")
    slack_body = {
        "text": f"{emoji} *{sev} Incident* — {incident.incident_number}",
        "attachments": [{
            "color": color,
            "blocks": [
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Incident:*\n{incident.incident_number}"},
                        {"type": "mrkdwn", "text": f"*Severity:*\n{emoji} {sev}"},
                        {"type": "mrkdwn", "text": f"*Trigger:*\n{incident.trigger}"},
                        {"type": "mrkdwn", "text": f"*Risk:*\n{incident.risk_score:.0%}"},
                        {"type": "mrkdwn", "text": f"*Agent:*\n`{incident.agent_id[:8]}`"},
                        {"type": "mrkdwn", "text": f"*Tool:*\n{incident.tool or 'N/A'}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Title:* {incident.title}"},
                },
            ] + ([{
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": incident.explanation}],
            }] if incident.explanation else []),
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=slack_body)
    except Exception as exc:
        logger.warning("slack_alert_failed", error=str(exc))


def _get_redis(request: Request):
    """Extract Redis from app state (injected by lifespan)."""
    return request.app.state.incident_redis


# ── INTERNAL: called by Redis stream consumer ─────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_internal_secret)])
async def create_incident(
    payload: IncidentCreate,
    request: Request,
    db:      AsyncSession = Depends(get_db),
) -> APIResponse[IncidentResponse]:
    repo     = IncidentRepository(db)
    incident = await repo.create(payload)

    _INCIDENT_CREATED.labels(
        severity=incident.severity,
        trigger=incident.trigger or "unknown",
    ).inc()

    webhook_url = settings.ALERT_WEBHOOK_URL
    if webhook_url:
        asyncio.create_task(_safe_bg(_fire_webhook(webhook_url, {
            "incident_id":     str(incident.id),
            "incident_number": incident.incident_number,
            "severity":        incident.severity,
            "status":          incident.status,
            "trigger":         incident.trigger,
            "title":           incident.title,
            "agent_id":        incident.agent_id,
            "risk_score":      incident.risk_score,
            "tool":            incident.tool,
            "explanation":     incident.explanation,
        })))

    # Slack alert for CRITICAL / HIGH only (Fix 9)
    if incident.severity in ("CRITICAL", "HIGH"):
        asyncio.create_task(_safe_bg(_fire_slack_alert(incident)))

    # Trigger ARE processing (Fix: publish manual incidents to the evaluation stream)
    try:
        r = _get_redis(request)
        await r.xadd("acp:incidents:queue", {"data": payload.model_dump_json()}, maxlen=10000, approximate=True)
    except Exception as exc:
        logger.warning("are_trigger_failed", error=str(exc))

    logger.info("incident_created",
        number=incident.incident_number, severity=incident.severity,
        agent_id=incident.agent_id, trigger=incident.trigger,
    )
    return APIResponse(data=IncidentResponse.model_validate(incident))


# ── TENANT-FACING ─────────────────────────────────────────────────────────────

@router.get("/summary")
async def get_summary(
    tenant_id: uuid.UUID  = Depends(get_tenant_id),
    db:        AsyncSession = Depends(get_db),
) -> APIResponse[IncidentSummary]:
    repo = IncidentRepository(db)
    raw  = await repo.summary(tenant_id)
    mttr_hours = raw.get("mttr_hours", 0.0)
    _INCIDENT_MTTR_HOURS.labels(tenant_id=str(tenant_id)).set(mttr_hours)
    # Also refresh global seconds gauge so alert rules always have an up-to-date value.
    INCIDENT_MTTR_SECONDS.set(mttr_hours * 3600)
    return APIResponse(data=IncidentSummary(**raw))


@router.get("")
async def list_incidents(
    status:    str | None = Query(None),
    severity:  str | None = Query(None),
    limit:     int        = Query(50, ge=1, le=200),
    offset:    int        = Query(0, ge=0),
    tenant_id: uuid.UUID  = Depends(get_tenant_id),
    db:        AsyncSession = Depends(get_db),
) -> APIResponse[dict]:
    repo         = IncidentRepository(db)
    items, total = await repo.list(tenant_id, status=status, severity=severity, limit=limit, offset=offset)
    return APIResponse(data={
        "items": [IncidentResponse.model_validate(i) for i in items],
        "total": total,
    })


@router.get("/{incident_id}")
async def get_incident(
    incident_id: uuid.UUID,
    tenant_id:   uuid.UUID  = Depends(get_tenant_id),
    db:          AsyncSession = Depends(get_db),
) -> APIResponse[IncidentResponse]:
    repo     = IncidentRepository(db)
    incident = await repo.get(incident_id, tenant_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return APIResponse(data=IncidentResponse.model_validate(incident))


@router.patch("/{incident_id}")
async def update_incident(
    incident_id: uuid.UUID,
    payload:     IncidentUpdate,
    tenant_id:   uuid.UUID  = Depends(get_tenant_id),
    db:          AsyncSession = Depends(get_db),
) -> APIResponse[IncidentResponse]:
    repo = IncidentRepository(db)
    try:
        incident = await repo.update_status(
            incident_id, tenant_id, payload, actor="dashboard-user"
        )
    except StateTransitionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if payload.status and payload.status.upper() == "RESOLVED":
        _INCIDENT_RESOLVED.labels(severity=incident.severity).inc()
        if incident.resolved_at and incident.created_at:
            created = (
                incident.created_at.replace(tzinfo=UTC)
                if incident.created_at.tzinfo is None
                else incident.created_at
            )
            elapsed_seconds = (incident.resolved_at - created).total_seconds()
            # Per-severity histogram: records exact resolution time for dashboards.
            _INCIDENT_RESOLUTION_SECONDS.labels(severity=incident.severity).observe(elapsed_seconds)
            try:
                summary_raw = await IncidentRepository(db).summary(tenant_id)
                mttr_hours = summary_raw.get("mttr_hours", 0.0)
                # Per-tenant gauge (hours) for the operator dashboard.
                _INCIDENT_MTTR_HOURS.labels(tenant_id=str(tenant_id)).set(mttr_hours)
                # Global unlabelled gauge (seconds) for Prometheus alerting rules.
                INCIDENT_MTTR_SECONDS.set(mttr_hours * 3600)
            except Exception as _exc:
                logger.warning("mttr_gauge_update_failed", error=str(_exc))

    logger.info("incident_updated", id=str(incident_id), status=payload.status)
    return APIResponse(data=IncidentResponse.model_validate(incident))


@router.post("/{incident_id}/actions")
async def add_action(
    incident_id: uuid.UUID,
    payload:     IncidentActionRequest,
    request:     Request,
    tenant_id:   uuid.UUID  = Depends(get_tenant_id),
    db:          AsyncSession = Depends(get_db),
) -> APIResponse[IncidentResponse]:
    repo     = IncidentRepository(db)
    incident = await repo.get(incident_id, tenant_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Fix 5: Bind actions to real system effects before recording
    agent_id = incident.agent_id
    try:
        await _apply_action_effect(
            request, payload.type, agent_id, str(tenant_id), str(incident_id)
        )
    except Exception as exc:
        logger.warning("action_effect_failed", type=payload.type, error=str(exc))
        # Don't block — still record the action, log the failure

    updated = await repo.add_action(incident_id, tenant_id, payload.type, payload.by, payload.note)
    logger.info("incident_action", id=str(incident_id), type=payload.type, by=payload.by)
    return APIResponse(data=IncidentResponse.model_validate(updated))


async def _apply_action_effect(
    request:     Request,
    action_type: str,
    agent_id:    str,
    tenant_id:   str,
    incident_id: str,
) -> None:
    """Execute the real system effect for each response action type."""
    redis = _get_redis(request)

    if action_type == "KILL_AGENT":
        # Tenant-scoped per-agent kill key (Multi-Tenant Isolation Fix)
        import time as _time
        kill_payload = json.dumps({"incident_id": incident_id, "ts": _time.time()})
        await redis.setex(f"acp:{tenant_id}:agent_kill:{agent_id}", 86400, kill_payload)
        logger.critical("agent_kill_applied", agent_id=agent_id, incident_id=incident_id)

    elif action_type == "BLOCK_AGENT":
        # Create a wildcard DENY permission in the registry — blocks all tool calls
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}/permissions",
                json={"tool_name": "*", "action": "DENY", "granted_by": f"incident:{incident_id}"},
                headers={**mesh_headers("api"),},
            )
        logger.warning("agent_blocked", agent_id=agent_id, incident_id=incident_id)

    elif action_type == "ISOLATE":
        # Suspend the agent in the registry — no new sessions allowed
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}",
                json={"status": "suspended"},
                headers={**mesh_headers("api"),},
            )
        logger.warning("agent_isolated", agent_id=agent_id, incident_id=incident_id)

    elif action_type == "ESCALATE":
        await redis.setex(f"acp:{tenant_id}:agent_escalated:{agent_id}", 3600, "1")
        logger.warning("agent_escalated", agent_id=agent_id, incident_id=incident_id)
