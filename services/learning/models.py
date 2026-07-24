from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from sdk.common.db import Base, IdMixin, TenantMixin


class BehaviorProfileModel(Base, IdMixin, TenantMixin):
    __tablename__ = "behavior_profiles"

    # audit S11h (P1-14): uniqueness is composite (tenant_id, agent_id) —
    # the DB constraint is created in migration
    # ``s11h_composite_tenant_agent_2026_07_21``. The column stays indexed
    # for the common ``WHERE tenant_id = ? AND agent_id = ?`` query shape;
    # the ``unique=True`` at column level is replaced by the __table_args__
    # UniqueConstraint below so the two match.
    agent_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False, index=True
    )

    # Distributions and Matrices stored as JSONB
    tool_usage_distribution: Mapped[dict] = mapped_column(
        postgresql.JSONB, server_default="{}", nullable=False
    )

    transition_matrix: Mapped[dict] = mapped_column(
        postgresql.JSONB, server_default="{}", nullable=False
    )

    avg_velocity: Mapped[float] = mapped_column(sa.Float, default=0.0, nullable=False)
    baseline_risk: Mapped[float] = mapped_column(sa.Float, default=0.0, nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, default=1, nullable=False)

    last_updated: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "agent_id",
            name="uq_behavior_profiles_tenant_agent",
        ),
    )
