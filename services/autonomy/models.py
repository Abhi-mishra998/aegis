"""
Bounded Autonomy Contracts (F3) + Human Override Timeline (F6).

  autonomy_contracts             — declared boundaries per agent
  autonomy_contract_violations   — history of attempts that breached a contract
  human_override_events          — manual approvals, overrides, emergency stops
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from sdk.common.db import Base, IdMixin, OrgMixin, TenantMixin, TimestampMixin


class AutonomyContract(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    __tablename__ = "autonomy_contracts"

    agent_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name:         Mapped[str] = mapped_column(String(128), nullable=False)
    enabled:      Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    version:      Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Lists kept as JSONB so contracts can evolve without migration churn.
    allowed_actions:    Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    denied_actions:     Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    approval_required:  Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    # Numeric ceilings — None means "no limit"
    max_runtime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tool_calls:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_usd:       Mapped[float | None] = mapped_column(Float, nullable=True)
    max_autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    escalation_triggers: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    notes:        Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "name", name="uq_autonomy_contracts_unique"),
        Index("ix_autonomy_contracts_tenant_agent", "tenant_id", "agent_id"),
    )


class AutonomyViolation(Base, OrgMixin, TenantMixin, IdMixin):
    __tablename__ = "autonomy_contract_violations"

    contract_id:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    agent_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    request_id:   Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    rule:         Mapped[str] = mapped_column(String(64), nullable=False)
    detail:       Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    detected_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_autonomy_violations_tenant_time", "tenant_id", "detected_at"),
    )


class HumanOverrideEvent(Base, OrgMixin, TenantMixin, IdMixin):
    """Feature 6 — durable timeline of human interventions in agent runtime."""
    __tablename__ = "human_override_events"

    actor:        Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role:   Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type:   Mapped[str] = mapped_column(String(32), nullable=False)  # approval|override|stop|escalation|note
    target_kind:  Mapped[str] = mapped_column(String(32), nullable=False)  # agent|tenant|tool|request
    target_id:    Mapped[str] = mapped_column(String(128), nullable=False)
    request_id:   Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reason:       Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    occurred_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_overrides_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_overrides_tenant_target", "tenant_id", "target_kind", "target_id"),
    )
