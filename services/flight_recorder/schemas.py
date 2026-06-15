"""Pydantic schemas for flight_recorder."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TimelineOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    request_id: str
    session_id: str | None = None    # Sprint 3.5
    agent_id: uuid.UUID | None = None
    tool: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    final_decision: str | None = None
    final_risk: float | None = None
    status: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    id: uuid.UUID
    timeline_id: uuid.UUID
    step_index: int
    step_type: str
    status: str
    latency_ms: int | None = None
    risk_score: float | None = None
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime

    model_config = {"from_attributes": True}


class SnapshotOut(BaseModel):
    id: uuid.UUID
    timeline_id: uuid.UUID
    step_index: int
    snapshot: dict[str, Any] = Field(default_factory=dict)
    tokens_in: int | None = None
    tokens_out: int | None = None
    captured_at: datetime

    model_config = {"from_attributes": True}


class ArtifactOut(BaseModel):
    id: uuid.UUID
    timeline_id: uuid.UUID
    step_id: uuid.UUID | None = None
    kind: str
    sha256: str
    size_bytes: int
    content: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReplayOut(BaseModel):
    timeline: TimelineOut
    steps: list[StepOut]
    snapshots: list[SnapshotOut]
    artifacts: list[ArtifactOut]


# ---------------------------------------------------------------------------
# Sprint 3 — Decision Explorer + Session Explorer shapes
# ---------------------------------------------------------------------------


class DecisionGraphNode(BaseModel):
    """One node in the Decision Explorer graph — represents a single pipeline
    stage (auth, rate_limit, policy, behavior, decision, …)."""

    id: str                              # stable id like "stage:policy"
    label: str                           # display label
    stage: str                           # one of the 11 pipeline stages
    status: str                          # ok | denied | error | skipped
    outcome: str | None = None           # allow | deny | throttle | escalate | kill
    latency_ms: int | None = None
    risk_score: float | None = None
    summary: str | None = None           # short human-readable
    payload: dict[str, Any] = Field(default_factory=dict)


class DecisionGraphEdge(BaseModel):
    """A directed edge between two stages. ``signal`` carries the named
    output the previous stage produced (e.g. ``risk=0.42`` or ``finding=SQL_DDL``)."""

    source: str
    target: str
    signal: str | None = None
    risk_contribution: float | None = None


class DecisionGraphOut(BaseModel):
    """The full Decision Explorer payload for one request_id."""

    timeline: TimelineOut
    nodes: list[DecisionGraphNode]
    edges: list[DecisionGraphEdge]
    receipt_url: str | None = None       # the signed-receipt URL when available
    total_latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    estimated_usd: float | None = None


class SessionSummary(BaseModel):
    """One row in the Session Explorer list view."""

    session_id: str
    tenant_id: uuid.UUID
    decision_count: int
    started_at: datetime
    last_seen_at: datetime
    distinct_agents: int
    distinct_tools: int
    max_risk: float | None = None
    final_risk: float | None = None
    risk_trajectory: list[float] = Field(default_factory=list)


class SessionDetailOut(BaseModel):
    """One session's drill-down: every decision in order + risk trajectory."""

    session_id: str
    tenant_id: uuid.UUID
    timelines: list[TimelineOut]
    risk_trajectory: list[float] = Field(default_factory=list)
