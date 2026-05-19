"""Pydantic schemas for autonomy service."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContractIn(BaseModel):
    agent_id: uuid.UUID
    name: str
    enabled: bool = True
    allowed_actions: list[str] = Field(default_factory=list)
    denied_actions:  list[str] = Field(default_factory=list)
    approval_required: list[str] = Field(default_factory=list)
    max_runtime_seconds: int | None = None
    max_tool_calls: int | None = None
    max_cost_usd: float | None = None
    max_autonomy_level: int = 2
    escalation_triggers: list[str] = Field(default_factory=list)
    notes: str | None = None


class ContractOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    enabled: bool
    version: int
    allowed_actions: list[str]
    denied_actions: list[str]
    approval_required: list[str]
    max_runtime_seconds: int | None = None
    max_tool_calls: int | None = None
    max_cost_usd: float | None = None
    max_autonomy_level: int
    escalation_triggers: list[str]
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class CheckRequest(BaseModel):
    agent_id: uuid.UUID
    action: str
    request_id: str | None = None
    cost_estimate_usd: float | None = None
    runtime_estimate_seconds: int | None = None
    tool_calls_so_far: int | None = None


class CheckResult(BaseModel):
    allowed: bool
    requires_approval: bool = False
    violated_rules: list[str] = Field(default_factory=list)
    contract_id: uuid.UUID | None = None
    contract_version: int | None = None
    reason: str | None = None


class ViolationOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    agent_id: uuid.UUID
    request_id: str | None = None
    rule: str
    detail: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime

    model_config = {"from_attributes": True}


class OverrideIn(BaseModel):
    actor: str
    actor_role: str | None = None
    event_type: str
    target_kind: str
    target_id: str
    request_id: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OverrideOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    actor: str
    actor_role: str | None = None
    event_type: str
    target_kind: str
    target_id: str
    request_id: str | None = None
    reason: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime

    model_config = {"from_attributes": True}
