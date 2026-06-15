"""
Sprint 4 — Fleet dashboard API surface.

Lives in its own router (rather than the 1400-line ``services/audit/router.py``)
so the Sprint 4 endpoints are easy to find, easy to extend, and easy to
delete if the dashboard strategy shifts.

Endpoints:

    GET /audit/fleet/kpis             KPI cards for the Home dashboard
    GET /audit/fleet/timeseries       Per-metric time-bucketed series
    GET /audit/fleet/agent-health     Ranked agents (deny rate, error, volume…)
    GET /audit/fleet/recent-events    Recent denied/errored decisions

Tenant scope is enforced via ``get_tenant_id`` which reads from the
verified JWT — never the request header.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.audit.fleet_aggregator import FleetAggregator
from services.audit.fleet_schemas import (
    FleetAgentHealthRow,
    FleetKPIs,
    FleetRecentEvent,
    FleetTimeseriesPoint,
)

fleet_router = APIRouter(
    prefix="/fleet",
    tags=["fleet"],
    dependencies=[Depends(verify_internal_secret)],
)


@fleet_router.get(
    "/kpis",
    response_model=APIResponse[FleetKPIs],
    summary="KPI cards for the Fleet Home dashboard",
)
async def get_fleet_kpis(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    window_minutes: int = Query(60, ge=1, le=10080),
) -> APIResponse[FleetKPIs]:
    payload = await FleetAggregator.kpis(db, tenant_id, window_minutes=window_minutes)
    return APIResponse(data=FleetKPIs.model_validate(payload))


@fleet_router.get(
    "/timeseries",
    response_model=APIResponse[list[FleetTimeseriesPoint]],
    summary="Per-metric time-bucketed series for the dashboard charts",
)
async def get_fleet_timeseries(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    metric: str = Query("decisions", description="decisions|denied|errors|latency_ms"),
    window_minutes: int = Query(180, ge=1, le=10080),
    bucket_minutes: int = Query(5, ge=1, le=1440),
    agent_id: uuid.UUID | None = Query(None),
) -> APIResponse[list[FleetTimeseriesPoint]]:
    try:
        rows = await FleetAggregator.timeseries(
            db, tenant_id,
            metric=metric,          # type: ignore[arg-type]
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
            agent_id=agent_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return APIResponse(data=[FleetTimeseriesPoint(**r) for r in rows])


@fleet_router.get(
    "/agent-health",
    response_model=APIResponse[list[FleetAgentHealthRow]],
    summary="Ranked agents by deny rate / error / volume / avg risk",
)
async def get_fleet_agent_health(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    rank_by: str = Query("deny_rate", description="deny_rate|error_rate|volume|avg_risk"),
    window_minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(25, ge=1, le=200),
) -> APIResponse[list[FleetAgentHealthRow]]:
    try:
        rows = await FleetAggregator.agent_health(
            db, tenant_id,
            rank_by=rank_by,        # type: ignore[arg-type]
            window_minutes=window_minutes,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return APIResponse(data=[FleetAgentHealthRow(**r) for r in rows])


@fleet_router.get(
    "/recent-events",
    response_model=APIResponse[list[FleetRecentEvent]],
    summary="Recent denied / errored decisions for the activity table",
)
async def get_fleet_recent_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    kind: str = Query("denied", description="denied|errors|any"),
    limit: int = Query(25, ge=1, le=200),
) -> APIResponse[list[FleetRecentEvent]]:
    try:
        rows = await FleetAggregator.recent_events(
            db, tenant_id,
            kind=kind,              # type: ignore[arg-type]
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return APIResponse(data=[FleetRecentEvent(**r) for r in rows])
