"""Sprint 6 — Pydantic schemas for the shadow-mode API surface."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ShadowPolicyCreate(BaseModel):
    name:         str
    agent_id:     str | None = None
    rules_json:   list[dict[str, Any]] = Field(default_factory=list)
    description:  str | None = None
    sample_rate:  float = Field(1.0, ge=0.0, le=1.0)


class ShadowPolicyEdit(BaseModel):
    name:         str | None = None
    rules_json:   list[dict[str, Any]] | None = None
    description:  str | None = None
    sample_rate:  float | None = Field(None, ge=0.0, le=1.0)


class ShadowPolicyResponse(BaseModel):
    id:           str
    tenant_id:    str
    agent_id:     str | None
    name:         str
    version:      int
    mode:         str
    rules_json:   list[dict[str, Any]]
    description:  str | None
    sample_rate:  float
    created_by:   str | None
    created_at:   str
    promoted_at:  str | None


class ShadowPromoteBody(BaseModel):
    """One of:
      target=shadow  — only valid when current mode is draft
      target=enforce — only valid when current mode is shadow
      target=archived — accepted from any mode
    """
    target: str = Field(..., description="shadow | enforce | archived")


class ShadowRollbackBody(BaseModel):
    target_version: int


class ShadowPolicyVersionResponse(BaseModel):
    id:           str
    policy_id:    str
    version:      int
    change_kind:  str
    mode_before:  str | None
    mode_after:   str
    rules_json:   list[dict[str, Any]]
    changed_by:   str | None
    changed_at:   str


class ShadowDecisionRow(BaseModel):
    id:                       str
    policy_id:                str
    policy_version:           int
    request_id:               str | None
    audit_id:                 str | None
    tool:                     str | None
    real_action:              str
    shadow_action:            str
    matched_rule_index:       int | None
    matched_rule_description: str | None
    payload_hash:             str | None
    risk_score:               float | None
    eval_latency_ms:          float
    created_at:               str


class WouldHaveDeniedReport(BaseModel):
    policy_id:                str
    policy_name:              str
    window_hours:             int
    decisions_seen:           int
    drift_count:              int
    would_have_denied_count:  int
    would_have_blocked_benign_count: int
    real_allow_count:         int
    real_deny_count:          int
    sample_drift:             list[ShadowDecisionRow]


class OnlineEvalConfigBody(BaseModel):
    enabled:               bool = True
    sample_rate:           float = Field(0.05, ge=0.0, le=1.0)
    fp_threshold:          float = Field(0.05, ge=0.0, le=1.0)
    poll_interval_seconds: int = Field(900, ge=60, le=86400)


class OnlineEvalConfigResponse(OnlineEvalConfigBody):
    id:           str
    tenant_id:    str
    last_run_at:  str | None
    created_at:   str
