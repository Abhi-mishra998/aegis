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
