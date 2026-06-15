"""
Flight Recorder — replay & inspection REST API.

GET    /flight/timelines                            — list (paginated, filtered)
GET    /flight/timeline/{id}                        — full replay (timeline+steps+snapshots+artifacts)
GET    /flight/timeline/by-request/{rid}            — same as above but by request_id
GET    /flight/timeline/{id}/steps                  — steps only (faster for scrubber)
POST   /flight/timeline/{id}/export                 — exportable JSON for offline review

Sprint 3:
GET    /flight/decision/{rid}/graph                 — Decision Explorer graph payload
GET    /flight/sessions                             — list sessions for a tenant
GET    /flight/sessions/{session_id}                — drill into one session
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
    DecisionGraphEdge,
    DecisionGraphNode,
    DecisionGraphOut,
    ReplayOut,
    SessionDetailOut,
    SessionSummary,
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


# ---------------------------------------------------------------------------
# Sprint 3.3 — Decision Explorer graph endpoint
# ---------------------------------------------------------------------------


# The 11 pipeline stages in canonical order. The Decision Explorer uses
# this same ordering for the React-Flow x-coordinates so stages always
# read left-to-right even when some are skipped.
_PIPELINE_STAGES: tuple[str, ...] = (
    "kill_switch",        # 0
    "auth",               # 1
    "rate_limit",         # 2
    "inference_proxy",    # 3
    "policy",             # 4
    "behavior",           # 5
    "decision",           # 6
    "autonomy",           # 7
    "execution",          # 8
    "output_filter",      # 9
    "audit",              # 10
)


def _step_type_to_stage(step_type: str, payload: dict) -> str:
    """Map an :class:`ExecutionStep.step_type` to its pipeline stage.

    The step writer at the gateway uses generic types (``policy``,
    ``tool_call``, …); the Decision Explorer needs the 11-stage name. The
    mapping is deterministic and lives here (rather than at the writer)
    so old timelines stamped under the legacy schema still render.
    """
    explicit = (payload or {}).get("stage")
    if explicit in _PIPELINE_STAGES:
        return explicit
    return {
        "prompt":     "inference_proxy",
        "tool_call":  "execution",
        "policy":     "policy",
        "decision":   "decision",
        "retry":      "execution",
        "failure":    "execution",
    }.get(step_type, "decision")


@router.get(
    "/decision/{request_id}/graph",
    response_model=APIResponse[DecisionGraphOut],
)
async def get_decision_graph(
    request_id: str,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[DecisionGraphOut]:
    """Return the Decision Explorer graph payload for one ``request_id``.

    Shape:
      * nodes: one per pipeline stage that has a recorded step
      * edges: stage(N) → stage(N+1) with the upstream stage's signal label
      * receipt_url: link to the signed audit row for offline verification
    """
    timeline = (await db.execute(
        select(ExecutionTimeline).where(
            ExecutionTimeline.request_id == request_id,
            ExecutionTimeline.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if timeline is None:
        raise HTTPException(status_code=404, detail="No flight timeline for that request_id")

    steps = list((await db.execute(
        select(ExecutionStep)
        .where(
            ExecutionStep.timeline_id == timeline.id,
            ExecutionStep.tenant_id == tenant_id,
        )
        .order_by(ExecutionStep.step_index.asc())
    )).scalars().all())

    # Bucket steps by stage. A stage with multiple recorded steps keeps the
    # most-impactful (highest risk_score, then latest occurred_at).
    by_stage: dict[str, ExecutionStep] = {}
    for s in steps:
        stage = _step_type_to_stage(s.step_type, s.payload or {})
        prev = by_stage.get(stage)
        if (
            prev is None
            or (s.risk_score or 0) > (prev.risk_score or 0)
            or (s.occurred_at > prev.occurred_at and (s.risk_score or 0) >= (prev.risk_score or 0))
        ):
            by_stage[stage] = s

    nodes: list[DecisionGraphNode] = []
    edges: list[DecisionGraphEdge] = []
    ordered_present = [s for s in _PIPELINE_STAGES if s in by_stage]

    for stage in ordered_present:
        step = by_stage[stage]
        payload = step.payload or {}
        nodes.append(DecisionGraphNode(
            id=f"stage:{stage}",
            label=stage.replace("_", " ").title(),
            stage=stage,
            status=step.status,
            outcome=payload.get("outcome") or payload.get("decision"),
            latency_ms=step.latency_ms,
            risk_score=step.risk_score,
            summary=step.summary,
            payload=payload,
        ))

    # Connect consecutive present stages; the signal label is the upstream's
    # outcome or summary.
    for prev_stage, next_stage in zip(ordered_present, ordered_present[1:]):
        prev_node = by_stage[prev_stage]
        edges.append(DecisionGraphEdge(
            source=f"stage:{prev_stage}",
            target=f"stage:{next_stage}",
            signal=(prev_node.payload or {}).get("outcome")
                or (prev_node.payload or {}).get("decision")
                or prev_node.summary,
            risk_contribution=prev_node.risk_score,
        ))

    # Token + cost totals from snapshots (used by the Trace Overview panel).
    snapshots = list((await db.execute(
        select(ExecutionSnapshot)
        .where(
            ExecutionSnapshot.timeline_id == timeline.id,
            ExecutionSnapshot.tenant_id == tenant_id,
        )
    )).scalars().all())
    tokens_in = sum((s.tokens_in or 0) for s in snapshots) or None
    tokens_out = sum((s.tokens_out or 0) for s in snapshots) or None
    estimated_usd: float | None = None
    if tokens_in or tokens_out:
        # Match sdk/common/inference_cost._DEFAULT_PRICE_TABLE so dashboards
        # don't drift between Sprint 2 and Sprint 3.
        estimated_usd = round(
            ((tokens_in or 0) + (tokens_out or 0)) / 1000.0 * 0.50,
            6,
        )

    receipt_url = (
        f"/receipts/{request_id}" if (timeline.metadata_json or {}).get("receipt_id") else None
    )

    return APIResponse(data=DecisionGraphOut(
        timeline=TimelineOut.model_validate(timeline),
        nodes=nodes,
        edges=edges,
        receipt_url=receipt_url,
        total_latency_ms=timeline.duration_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_usd=estimated_usd,
    ))


# ---------------------------------------------------------------------------
# Sprint 3.5 — Session Explorer endpoints
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=APIResponse[list[SessionSummary]])
async def list_sessions(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    minutes: int = Query(1440, ge=1, le=43200),
    limit: int = Query(100, ge=1, le=500),
) -> APIResponse[list[SessionSummary]]:
    """List active sessions for a tenant within the last ``minutes`` window.

    A session is the set of timelines that share a non-NULL ``session_id``.
    The risk trajectory is the per-decision ``final_risk`` in time order
    so the UI can render a sparkline without a second round-trip.
    """
    since = datetime.now(tz=UTC) - timedelta(minutes=minutes)
    rows = list((await db.execute(
        select(ExecutionTimeline)
        .where(
            ExecutionTimeline.tenant_id == tenant_id,
            ExecutionTimeline.session_id.is_not(None),
            ExecutionTimeline.started_at >= since,
        )
        .order_by(ExecutionTimeline.started_at.asc())
    )).scalars().all())

    bucket: dict[str, list[ExecutionTimeline]] = {}
    for r in rows:
        bucket.setdefault(r.session_id, []).append(r)

    summaries: list[SessionSummary] = []
    for session_id, timelines in bucket.items():
        timelines.sort(key=lambda t: t.started_at)
        agents = {str(t.agent_id) for t in timelines if t.agent_id is not None}
        tools = {t.tool for t in timelines if t.tool}
        risks = [t.final_risk for t in timelines if t.final_risk is not None]
        summaries.append(SessionSummary(
            session_id=session_id,
            tenant_id=tenant_id,
            decision_count=len(timelines),
            started_at=timelines[0].started_at,
            last_seen_at=timelines[-1].started_at,
            distinct_agents=len(agents),
            distinct_tools=len(tools),
            max_risk=max(risks) if risks else None,
            final_risk=timelines[-1].final_risk,
            risk_trajectory=risks,
        ))

    # Most recently active session first; the UI surfaces fresh activity at top.
    summaries.sort(key=lambda s: s.last_seen_at, reverse=True)
    return APIResponse(data=summaries[:limit])


@router.get("/sessions/{session_id}", response_model=APIResponse[SessionDetailOut])
async def get_session(
    session_id: str,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[SessionDetailOut]:
    rows = list((await db.execute(
        select(ExecutionTimeline)
        .where(
            ExecutionTimeline.tenant_id == tenant_id,
            ExecutionTimeline.session_id == session_id,
        )
        .order_by(ExecutionTimeline.started_at.asc())
    )).scalars().all())
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found for this tenant")

    return APIResponse(data=SessionDetailOut(
        session_id=session_id,
        tenant_id=tenant_id,
        timelines=[TimelineOut.model_validate(t) for t in rows],
        risk_trajectory=[t.final_risk for t in rows if t.final_risk is not None],
    ))
