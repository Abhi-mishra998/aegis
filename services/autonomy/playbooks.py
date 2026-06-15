"""
Auto-remediation Playbooks Engine

A playbook is a named, ordered list of steps that execute automatically
in response to a trigger event (SOAR-style runbook).

Models:
  Playbook       — named runbook with trigger_conditions + steps (JSON)
  PlaybookRun    — execution record per trigger invocation

Action types (Sprint 2b — all seven are now wired to real executors in
``services/autonomy/webhook_executor.execute_step``):

  * ``KILL_AGENT``    — PATCH ``{registry}/agents/{id}`` with status=suspended.
  * ``ISOLATE_AGENT`` — PATCH same endpoint with status=isolated (rate-limit
    without full suspend).
  * ``BLOCK_TOOL``    — POST ``{registry}/agents/{id}/permissions`` with
    ``action=DENY`` for the named tool.
  * ``THROTTLE``      — POST ``{api}/internal/throttle`` with a rate string.
  * ``REVOKE_KEY``    — DELETE ``{api}/api-keys/{key_id}``.
  * ``SEND_ALERT``    — Slack incoming-webhook OR PagerDuty Events API v2.
  * ``WEBHOOK``       — Generic POST/GET to a user-supplied URL.

All HTTP calls carry ``X-Internal-Secret`` (the mesh-auth fallback) or the
ES256 mesh JWT (Sprint 1.4) so the registry / api services treat them as
trusted internal traffic.

The pre-Sprint-2b docstring claimed "v1 — logged only" — that label was
stale once the executors landed; this rewrite makes the contract accurate
again so the audit's C17 finding can be closed honestly.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from sdk.common.db import Base, IdMixin, TenantMixin, TimestampMixin

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------

ACTION_KILL_AGENT    = "KILL_AGENT"
ACTION_ISOLATE_AGENT = "ISOLATE_AGENT"
ACTION_BLOCK_TOOL    = "BLOCK_TOOL"
ACTION_THROTTLE      = "THROTTLE"
ACTION_REVOKE_KEY    = "REVOKE_KEY"
ACTION_SEND_ALERT    = "SEND_ALERT"
ACTION_WEBHOOK       = "WEBHOOK"

VALID_ACTION_TYPES = {
    ACTION_KILL_AGENT, ACTION_ISOLATE_AGENT, ACTION_BLOCK_TOOL,
    ACTION_THROTTLE, ACTION_REVOKE_KEY, ACTION_SEND_ALERT, ACTION_WEBHOOK,
}

# Playbook run statuses
STATUS_PENDING  = "pending"
STATUS_RUNNING  = "running"
STATUS_SUCCESS  = "success"
STATUS_FAILED   = "failed"
STATUS_PARTIAL  = "partial"

# Playbook modes
MODE_AUTO    = "auto"
MODE_MANUAL  = "manual"
MODE_SUGGEST = "suggest"


# ---------------------------------------------------------------------------
# SQLAlchemy Models
# ---------------------------------------------------------------------------

class Playbook(Base, TenantMixin, IdMixin, TimestampMixin):
    """Named, ordered remediation runbook stored per tenant."""
    __tablename__ = "playbooks"

    name:               Mapped[str]          = mapped_column(String(256), nullable=False)
    description:        Mapped[str | None]   = mapped_column(Text, nullable=True)
    trigger_conditions: Mapped[dict]         = mapped_column(JSONB, nullable=False, server_default="{}")
    # steps stored as JSON list of {order, action_type, params, timeout_seconds}
    steps:              Mapped[list]         = mapped_column(JSONB, nullable=False, server_default="[]")
    mode:               Mapped[str]          = mapped_column(String(16), nullable=False, server_default=MODE_AUTO)
    is_active:          Mapped[bool]         = mapped_column(Boolean, nullable=False, server_default="true")
    run_count:          Mapped[int]          = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        Index("ix_playbooks_tenant_active", "tenant_id", "is_active"),
    )


class PlaybookRun(Base, TenantMixin, IdMixin):
    """Immutable execution record for a single playbook invocation."""
    __tablename__ = "playbook_runs"

    playbook_id:    Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    triggered_by:   Mapped[str]          = mapped_column(Text, nullable=False)
    status:         Mapped[str]          = mapped_column(String(16), nullable=False, server_default=STATUS_PENDING)
    # list of {step_order, action_type, status, result, error, executed_at}
    steps_executed: Mapped[list]         = mapped_column(JSONB, nullable=False, server_default="[]")
    result:         Mapped[dict]         = mapped_column(JSONB, nullable=False, server_default="{}")
    started_at:     Mapped[datetime]     = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_playbook_runs_tenant_time", "tenant_id", "started_at"),
        Index("ix_playbook_runs_playbook_id", "playbook_id"),
    )


# ---------------------------------------------------------------------------
# Playbook Execution Engine
# ---------------------------------------------------------------------------


async def execute_playbook(
    playbook_id: uuid.UUID,
    context: dict[str, Any],
    db: AsyncSession,
    tenant_id: uuid.UUID | None = None,
    triggered_by: str = "manual",
) -> PlaybookRun:
    """
    Load the playbook, iterate its steps, record each result, and persist
    a PlaybookRun. Returns the completed run record.

    Sprint 2b — each step is dispatched to
    ``services/autonomy/webhook_executor.execute_step`` which calls the
    real downstream endpoint (registry, api, Slack, PagerDuty, …).
    Partial failures set status=partial if ≥1 step succeeded.
    """
    from sqlalchemy import select, update  # local to avoid circular at module load

    # ── Fetch playbook ────────────────────────────────────────────────────
    stmt = select(Playbook).where(Playbook.id == playbook_id)
    if tenant_id is not None:
        stmt = stmt.where(Playbook.tenant_id == tenant_id)
    pb = (await db.execute(stmt)).scalar_one_or_none()

    if pb is None:
        raise ValueError(f"Playbook {playbook_id} not found")

    run_tenant_id = tenant_id or pb.tenant_id

    # ── Create run record ────────────────────────────────────────────────
    run = PlaybookRun(
        id=uuid.uuid4(),
        tenant_id=run_tenant_id,
        playbook_id=playbook_id,
        triggered_by=triggered_by,
        status=STATUS_RUNNING,
        steps_executed=[],
        result={"context": context},
        started_at=datetime.now(tz=UTC),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    # ── Execute steps in order ───────────────────────────────────────────
    steps = sorted(pb.steps or [], key=lambda s: s.get("order", 0))
    executed: list[dict] = []
    success_count = 0
    failure_count = 0

    from services.autonomy.webhook_executor import (
        execute_step as _execute_step,  # noqa: PLC0415
    )

    for step in steps:
        step_record: dict[str, Any] = {
            "step_order":   step.get("order", 0),
            "action_type":  step.get("action_type", "UNKNOWN"),
            "executed_at":  datetime.now(tz=UTC).isoformat(),
            "status":       "pending",
            "result":       {},
            "error":        None,
        }
        try:
            action_result = await _execute_step(step, context=context)
            step_record["status"] = "success"
            step_record["result"] = action_result
            success_count += 1
        except Exception as exc:
            step_record["status"] = "failed"
            step_record["error"]  = str(exc)
            failure_count += 1
            logger.warning(
                "playbook_step_failed",
                playbook_id=str(playbook_id),
                step_order=step.get("order"),
                error=str(exc),
            )
        executed.append(step_record)

    # ── Determine final status ────────────────────────────────────────────
    if failure_count == 0:
        final_status = STATUS_SUCCESS
    elif success_count == 0:
        final_status = STATUS_FAILED
    else:
        final_status = STATUS_PARTIAL

    # ── Persist run result ────────────────────────────────────────────────
    run.status         = final_status
    run.steps_executed = executed
    run.result         = {
        "context":       context,
        "success_count": success_count,
        "failure_count": failure_count,
        "total_steps":   len(steps),
    }
    run.finished_at = datetime.now(tz=UTC)

    # Increment playbook run_count
    await db.execute(
        update(Playbook)
        .where(Playbook.id == playbook_id)
        .values(run_count=Playbook.run_count + 1)
    )
    await db.commit()
    await db.refresh(run)

    logger.info(
        "playbook_run_complete",
        playbook_id=str(playbook_id),
        run_id=str(run.id),
        status=final_status,
        success_count=success_count,
        failure_count=failure_count,
    )
    return run


# ---------------------------------------------------------------------------
# Pre-built Templates
# ---------------------------------------------------------------------------

def get_playbook_templates() -> list[dict[str, Any]]:
    """Return 4 pre-built SOAR-style playbook templates."""
    return [
        {
            "name": "High Risk Agent Quarantine",
            "description": (
                "Triggered when an agent's risk score reaches critical levels. "
                "Throttles the agent, sends a Slack alert, then terminates if the risk persists."
            ),
            "trigger_conditions": {
                "risk_score": {"gte": 0.9},
            },
            "mode": MODE_AUTO,
            "steps": [
                {
                    "order": 1,
                    "action_type": ACTION_THROTTLE,
                    "params": {"duration_seconds": 60, "target": "agent"},
                    "timeout_seconds": 10,
                },
                {
                    "order": 2,
                    "action_type": ACTION_SEND_ALERT,
                    "params": {
                        "channel": "slack",
                        "severity": "high",
                        "message": "Agent quarantined: risk_score >= 0.9",
                    },
                    "timeout_seconds": 10,
                },
                {
                    "order": 3,
                    "action_type": ACTION_KILL_AGENT,
                    "params": {"condition": "risk_persists", "reason": "High risk threshold exceeded"},
                    "timeout_seconds": 30,
                },
            ],
        },
        {
            "name": "SQL Injection Response",
            "description": (
                "Triggered when the run_sql tool is flagged with an sql_injection finding. "
                "Blocks the tool, sends an alert, and revokes the agent's active key."
            ),
            "trigger_conditions": {
                "tool":    "run_sql",
                "finding": "sql_injection",
            },
            "mode": MODE_AUTO,
            "steps": [
                {
                    "order": 1,
                    "action_type": ACTION_BLOCK_TOOL,
                    "params": {"tool": "run_sql", "scope": "agent"},
                    "timeout_seconds": 10,
                },
                {
                    "order": 2,
                    "action_type": ACTION_SEND_ALERT,
                    "params": {
                        "channel": "slack",
                        "severity": "critical",
                        "message": "SQL injection detected — run_sql blocked",
                    },
                    "timeout_seconds": 10,
                },
                {
                    "order": 3,
                    "action_type": ACTION_REVOKE_KEY,
                    "params": {"reason": "sql_injection finding"},
                    "timeout_seconds": 15,
                },
            ],
        },
        {
            "name": "Data Exfiltration Response",
            "description": (
                "Triggered when a data_exfiltration finding is detected. "
                "Isolates the agent, pages PagerDuty, and ships the event to the SIEM."
            ),
            "trigger_conditions": {
                "finding": "data_exfiltration",
            },
            "mode": MODE_AUTO,
            "steps": [
                {
                    "order": 1,
                    "action_type": ACTION_ISOLATE_AGENT,
                    "params": {"reason": "data_exfiltration finding"},
                    "timeout_seconds": 15,
                },
                {
                    "order": 2,
                    "action_type": ACTION_SEND_ALERT,
                    "params": {
                        "channel": "pagerduty",
                        "severity": "critical",
                        "message": "Data exfiltration detected — agent isolated",
                    },
                    "timeout_seconds": 10,
                },
                {
                    "order": 3,
                    "action_type": ACTION_WEBHOOK,
                    "params": {
                        "target": "SIEM",
                        "event_type": "data_exfiltration",
                    },
                    "timeout_seconds": 15,
                },
            ],
        },
        {
            "name": "Anomaly Spike Response",
            "description": (
                "Triggered when the anomaly score spikes above threshold. "
                "Throttles traffic and sends an alert for human review."
            ),
            "trigger_conditions": {
                "anomaly_score": {"gte": 0.85},
            },
            "mode": MODE_AUTO,
            "steps": [
                {
                    "order": 1,
                    "action_type": ACTION_THROTTLE,
                    "params": {"duration_seconds": 30, "target": "agent"},
                    "timeout_seconds": 10,
                },
                {
                    "order": 2,
                    "action_type": ACTION_SEND_ALERT,
                    "params": {
                        "channel": "slack",
                        "severity": "high",
                        "message": "Anomaly spike detected — traffic throttled for 30 s",
                    },
                    "timeout_seconds": 10,
                },
            ],
        },
    ]
