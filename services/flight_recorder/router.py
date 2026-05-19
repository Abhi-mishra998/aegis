"""
Flight Recorder — replay & inspection REST API.

GET    /flight/timelines                  — list (paginated, filtered)
GET    /flight/timeline/{id}              — full replay (timeline+steps+snapshots+artifacts)
GET    /flight/timeline/by-request/{rid}  — same as above but by request_id
GET    /flight/timeline/{id}/steps        — steps only (faster for scrubber)
POST   /flight/timeline/{id}/export       — exportable JSON for offline review
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.flight_recorder.models import (
    ExecutionArtifact,
    ExecutionSnapshot,
    ExecutionStep,
    ExecutionTimeline,
)
from services.flight_recorder.schemas import (
    ArtifactOut,
    ReplayOut,
    SnapshotOut,
    StepOut,
    TimelineOut,
)

router = APIRouter(
    prefix="/flight",
    tags=["flight_recorder"],
    dependencies=[Depends(verify_internal_secret)],
)


@router.get("/timelines", response_model=APIResponse[list[TimelineOut]])
async def list_timelines(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    minutes: int = Query(1440, ge=1, le=43200),
    agent_id: uuid.UUID | None = Query(None),
    tool: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> APIResponse[list[TimelineOut]]:
    since = datetime.now(tz=UTC) - timedelta(minutes=minutes)
    stmt = (
        select(ExecutionTimeline)
        .where(ExecutionTimeline.tenant_id == tenant_id, ExecutionTimeline.started_at >= since)
        .order_by(desc(ExecutionTimeline.started_at))
        .limit(limit)
    )
    if agent_id is not None:
        stmt = stmt.where(ExecutionTimeline.agent_id == agent_id)
    if tool:
        stmt = stmt.where(ExecutionTimeline.tool == tool)
    if status:
        stmt = stmt.where(ExecutionTimeline.status == status)
    rows = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[TimelineOut.model_validate(r) for r in rows])


async def _load_replay(
    db: AsyncSession, tenant_id: uuid.UUID, timeline: ExecutionTimeline
) -> ReplayOut:
    steps = list((await db.execute(
        select(ExecutionStep)
        .where(ExecutionStep.timeline_id == timeline.id, ExecutionStep.tenant_id == tenant_id)
        .order_by(ExecutionStep.step_index.asc())
    )).scalars().all())
    snapshots = list((await db.execute(
        select(ExecutionSnapshot)
        .where(ExecutionSnapshot.timeline_id == timeline.id, ExecutionSnapshot.tenant_id == tenant_id)
        .order_by(ExecutionSnapshot.step_index.asc())
    )).scalars().all())
    artifacts = list((await db.execute(
        select(ExecutionArtifact)
        .where(ExecutionArtifact.timeline_id == timeline.id, ExecutionArtifact.tenant_id == tenant_id)
        .order_by(ExecutionArtifact.created_at.asc())
    )).scalars().all())
    return ReplayOut(
        timeline=TimelineOut.model_validate(timeline),
        steps=[StepOut.model_validate(s) for s in steps],
        snapshots=[SnapshotOut.model_validate(s) for s in snapshots],
        artifacts=[ArtifactOut.model_validate(a) for a in artifacts],
    )


@router.get("/timeline/{timeline_id}", response_model=APIResponse[ReplayOut])
async def get_replay(
    timeline_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[ReplayOut]:
    t = (await db.execute(
        select(ExecutionTimeline).where(
            ExecutionTimeline.id == timeline_id,
            ExecutionTimeline.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    return APIResponse(data=await _load_replay(db, tenant_id, t))


@router.get("/timeline/by-request/{request_id}", response_model=APIResponse[ReplayOut])
async def get_replay_by_request(
    request_id: str,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[ReplayOut]:
    t = (await db.execute(
        select(ExecutionTimeline).where(
            ExecutionTimeline.request_id == request_id,
            ExecutionTimeline.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    return APIResponse(data=await _load_replay(db, tenant_id, t))


@router.get("/timeline/{timeline_id}/steps", response_model=APIResponse[list[StepOut]])
async def get_steps(
    timeline_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[list[StepOut]]:
    steps = list((await db.execute(
        select(ExecutionStep)
        .where(ExecutionStep.timeline_id == timeline_id, ExecutionStep.tenant_id == tenant_id)
        .order_by(ExecutionStep.step_index.asc())
    )).scalars().all())
    return APIResponse(data=[StepOut.model_validate(s) for s in steps])
