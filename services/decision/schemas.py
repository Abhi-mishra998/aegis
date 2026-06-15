"""
ACP Decision Schemas
====================
Canonical input/output types for the unified DecisionEngine.

DecisionContext  → Input  (what the engine receives)
Decision         → Output (what the engine produces)
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionAction(StrEnum):
    ALLOW    = "allow"
    MONITOR  = "monitor"
    THROTTLE = "throttle"
    REDACT   = "redact"
    ESCALATE = "escalate"
    KILL     = "kill"
    DENY     = "deny"  # kept for backward compat with policy returns


class OrchestrationRequest(BaseModel):
    """
    Minimal context payload sent by the Gateway to prompt Decision Engine Orchestration.
    """
    tenant_id: uuid.UUID
    agent_id: uuid.UUID
    tool: str
    tokens: int = 0
    inference_risk: float = 0.0
    inference_flags: list[str] = Field(default_factory=list)
    request_id: str = ""
    payload_hash: str = ""
    cost_risk: float = 0.0
    client_ip: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionContext(BaseModel):
    """
    Full context provided to DecisionEngine.evaluate().
    All risk signals are normalised to [0.0, 1.0].
    """

    # Identity
    tenant_id:  uuid.UUID
    agent_id:   uuid.UUID
    tool:       str
    request_id: str = ""

    # Policy signal
    policy_allowed: bool    = True
    policy_reason:  str | None = None
    policy_risk_adjustment: float = 0.0
    # 2026-06-15 — distinguish hard-deny rules (DROP TABLE, /etc/passwd,
    # $25M wires above hard cap) from escalate-required rules
    # ($250K external wires, kubectl delete prod, terraform destroy).
    # When the policy port returns escalate_only=False AND denied=True, the
    # router stamps this flag so the decision engine maps to DENY instead
    # of ESCALATE. Without the flag, the engine's threshold table would
    # always send policy-denied actions to ESCALATE (0.70 band).
    policy_hard_deny: bool = False

    # Risk signals [0.0–1.0 each]
    inference_risk:   float = Field(default=0.0, ge=0.0, le=1.0)
    behavior_risk:    float = Field(default=0.0, ge=0.0, le=1.0)
    anomaly_score:    float = Field(default=0.0, ge=0.0, le=1.0)
    cost_risk:        float = Field(default=0.0, ge=0.0, le=1.0)
    cross_agent_risk: float = Field(default=0.0, ge=0.0, le=1.0)

    # Confidence and learning signals
    confidence:          float = Field(default=1.0, ge=0.0, le=1.0)
    false_positive_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    true_positive_count: int   = 0

    # Flags collected from sub-systems
    behavior_flags:  list[str]       = Field(default_factory=list)
    inference_flags: list[str]       = Field(default_factory=list)
    usage_metrics:   dict[str, Any]  = Field(default_factory=dict)

    model_config = ConfigDict(strict=False)


class SignalEvaluation(BaseModel):
    """Diagnostic snapshot of one classifier signal.

    Lives inside `Decision.signals_evaluated`. The point is to answer
    "did we run the behavior classifier?" with a yes/no plus the actual
    score the classifier returned — independently of whether that score
    crossed the trigger threshold.
    """

    score:     float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    triggered: bool

    model_config = ConfigDict(strict=False)


class Decision(BaseModel):
    """
    The verdict produced by DecisionEngine.evaluate().
    This is the canonical output for the entire ACP pipeline.

    Response-shape notes (2026-05-15 — Sprint 2.2):

    * `findings`        — canonical-vocabulary array of actual findings.
                          Every entry is a name from
                          ``services/decision/findings.CANONICAL_FINDINGS``.
                          Empty list = "we ran every classifier and nothing
                          triggered."
    * `signals_evaluated` — diagnostic map of `signal_name -> {score,
                          threshold, triggered}`. Always lists every
                          classifier we ran. "Did we evaluate behaviour?"
                          → look here. Distinct from `findings`, which is
                          "what did we conclude?".
    * `reasons`         — DEPRECATED alias of `findings`. Maintained for
                          one release so existing consumers keep parsing.
                          Migrate to `findings`. The gateway adds a
                          `Deprecation: response-field=reasons` header.
    * `signals`         — back-compat: raw `signal_name -> score` floats.
                          New consumers should use `signals_evaluated`
                          which carries threshold + triggered as well.
    """

    action:     ExecutionAction
    risk:       float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # NEW (Sprint 2.2): canonical-vocabulary findings.
    findings:           list[str]                       = Field(default_factory=list)
    signals_evaluated:  dict[str, SignalEvaluation]     = Field(default_factory=dict)

    # DEPRECATED — kept for back-compat. Engine sets `reasons = findings`.
    reasons:    list[str]        = Field(default_factory=list)
    # back-compat raw scores
    signals:    dict[str, float] = Field(default_factory=dict)
    metadata:   dict[str, Any]   = Field(default_factory=dict)

    model_config = ConfigDict(strict=False)


# ---------------------------------------------------------------------------
# Backward-Compat Alias (DecisionRequest → DecisionContext)
# Callers that still use DecisionRequest will continue to work.
# ---------------------------------------------------------------------------

class DecisionRequest(DecisionContext):
    """Deprecated alias for DecisionContext. Use DecisionContext in new code."""
    # Extra fields from old schema kept for replay compat
    drift_score: float = Field(default=0.0, ge=0.0, le=1.0)

    model_config = ConfigDict(strict=False)
