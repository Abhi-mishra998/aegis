"""
Flight Recorder — execution timeline schema (Feature 2).
Replayable black-box for autonomous AI execution.

  execution_timelines  — one row per `request_id`; coarse metadata
  execution_steps      — fine-grained ordered steps (prompt, tool_call, policy,
                         decision, retry, failure)
  execution_snapshots  — periodic state captures (memory, context, tokens)
  execution_artifacts  — out-of-band payloads (prompts, responses) referenced
                         by step but stored separately so the step table stays
                         dense and queryable
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from sdk.common.db import Base, IdMixin, OrgMixin, TenantMixin, TimestampMixin


class ExecutionTimeline(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    __tablename__ = "execution_timelines"

    request_id:    Mapped[str] = mapped_column(String(64), nullable=False)
    # Sprint 3.5 — optional session/conversation grouping for the
    # Session Explorer. NULL on pre-Sprint-3 rows and on requests where
    # the client doesn't supply ``X-Session-ID``.
    session_id:    Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_id:      Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    tool:          Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    started_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms:   Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    final_risk:    Mapped[float | None] = mapped_column(Float, nullable=True)
    status:        Mapped[str] = mapped_column(String(32), nullable=False, server_default="in_progress")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    __table_args__ = (
        UniqueConstraint("tenant_id", "request_id", name="uq_timelines_tenant_request"),
        Index("ix_timelines_tenant_started", "tenant_id", "started_at"),
        Index(
            "ix_timelines_tenant_session",
            "tenant_id", "session_id", "started_at",
            postgresql_where="session_id IS NOT NULL",
        ),
    )


class ExecutionStep(Base, OrgMixin, TenantMixin, IdMixin):
    __tablename__ = "execution_steps"

    timeline_id:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    request_id:   Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    step_index:   Mapped[int] = mapped_column(Integer, nullable=False)
    step_type:    Mapped[str] = mapped_column(String(32), nullable=False)  # prompt|tool_call|policy|decision|retry|failure
    status:       Mapped[str] = mapped_column(String(32), nullable=False, server_default="ok")
    latency_ms:   Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_score:   Mapped[float | None] = mapped_column(Float, nullable=True)
    summary:      Mapped[str | None] = mapped_column(Text, nullable=True)
    payload:      Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    occurred_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_steps_timeline_order", "timeline_id", "step_index"),
        Index("ix_steps_tenant_request", "tenant_id", "request_id", "step_index"),
    )


class ExecutionSnapshot(Base, OrgMixin, TenantMixin, IdMixin):
    __tablename__ = "execution_snapshots"

    timeline_id:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    step_index:   Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot:     Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    tokens_in:    Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out:   Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExecutionArtifact(Base, OrgMixin, TenantMixin, IdMixin):
    __tablename__ = "execution_artifacts"

    timeline_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    step_id:     Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    kind:        Mapped[str] = mapped_column(String(32), nullable=False)  # prompt|response|tool_input|tool_output|memory
    sha256:      Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes:  Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    content:     Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_artifacts_timeline_kind", "timeline_id", "kind"),
    )
