"""Pydantic schemas for identity_graph service."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NodeOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    node_type: str
    external_id: str
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    trust_score: float
    drift_score: float
    last_scored_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class EdgeOut(BaseModel):
    id: uuid.UUID
    src_node_id: uuid.UUID
    dst_node_id: uuid.UUID
    edge_type: str
    action: str
    outcome: str
    risk_score: float
    occurred_at: datetime
    request_id: str | None = None

    model_config = {"from_attributes": True}


class GraphOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]


class BlastRadiusOut(BaseModel):
    actor: NodeOut
    depth: int
    reachable_nodes: list[NodeOut]
    edges_traversed: list[EdgeOut]
    affected_resources: int
    risk_score: float


class TrustScoreOut(BaseModel):
    node_id: uuid.UUID
    score: float
    components: dict[str, float]
    captured_at: datetime
    reason: str | None = None

    model_config = {"from_attributes": True}


class DriftOut(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    signal_type: str
    severity: str
    baseline: dict[str, Any]
    observed: dict[str, Any]
    delta: float
    detected_at: datetime

    model_config = {"from_attributes": True}


class NodeCreate(BaseModel):
    node_type: str
    external_id: str
    name: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class EdgeCreate(BaseModel):
    src_node_id: uuid.UUID
    dst_node_id: uuid.UUID
    edge_type: str
    action: str
    outcome: str = "success"
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    request_id: str | None = None


class CompromiseRequest(BaseModel):
    actor_node_id: uuid.UUID
    scenario: str = Field(default="stolen_token")
    depth: int = Field(default=3, ge=1, le=6)


class CompromiseOut(BaseModel):
    id: uuid.UUID
    actor_node_id: uuid.UUID
    scenario: str
    depth: int
    reachable_nodes: list[dict[str, Any]]
    affected_tenants: list[str]
    blast_radius: int
    risk_score: float
    summary: dict[str, Any]
    completed_at: datetime

    model_config = {"from_attributes": True}
