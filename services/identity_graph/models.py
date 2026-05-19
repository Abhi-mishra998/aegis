"""
ACP Identity Graph — SQLAlchemy Models
======================================
Backing store for the agent identity graph, trust scores, and drift signals.
Every execution event observed by the gateway is collapsed into:
  • a node (agent / tool / resource)
  • an edge (caller → callee)
  • a risk projection on those edges (used for blast-radius queries)

All tables carry tenant_id + org_id for tenant isolation. The graph itself
is multi-tenant but never cross-tenant: blast-radius queries are *always*
scoped to a single tenant_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from sdk.common.db import Base, IdMixin, OrgMixin, TenantMixin, TimestampMixin


class GraphNode(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    """
    A vertex in the identity graph.
    node_type ∈ {'agent', 'tool', 'resource', 'tenant', 'human'}
    external_id is the stable identifier (agent_id UUID, tool name, etc.)
    """
    __tablename__ = "graph_nodes"

    node_type:   Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name:        Mapped[str] = mapped_column(String(255), nullable=False)
    attributes:  Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Continuously-updated by the trust-score worker (Feature 4).
    trust_score:    Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0", nullable=False)
    drift_score:    Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0", nullable=False)
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "node_type", "external_id", name="uq_graph_nodes_tenant_external"),
        Index("ix_graph_nodes_tenant_type", "tenant_id", "node_type"),
    )


class GraphEdge(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    """
    A directed call from `src_node_id` → `dst_node_id`.
    edge_type ∈ {'invokes', 'reads', 'writes', 'delegates', 'escalates'}
    Edges are append-only; aggregation lives in a derived view (graph_edge_stats).
    """
    __tablename__ = "graph_edges"

    src_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    dst_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)

    edge_type:   Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action:      Mapped[str] = mapped_column(String(100), nullable=False)
    outcome:     Mapped[str] = mapped_column(String(32), nullable=False)  # allow/deny/error
    risk_score:  Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0", nullable=False)
    request_id:  Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attributes:  Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )

    __table_args__ = (
        Index("ix_graph_edges_tenant_src", "tenant_id", "src_node_id"),
        Index("ix_graph_edges_tenant_dst", "tenant_id", "dst_node_id"),
        Index("ix_graph_edges_occurred", "occurred_at"),
    )


class TrustScoreHistory(Base, OrgMixin, TenantMixin, IdMixin):
    """
    Time series of trust scores for an agent. Feature 4.
    Append-only; the score itself is also denormalized on graph_nodes.trust_score.
    """
    __tablename__ = "trust_score_history"

    node_id:    Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    score:      Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    reason:     Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class DriftSignal(Base, OrgMixin, TenantMixin, IdMixin):
    """
    A detected behavior-drift event. Feature 7.
    """
    __tablename__ = "drift_signals"

    node_id:        Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    signal_type:    Mapped[str] = mapped_column(String(64), nullable=False)
    severity:       Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    baseline:       Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    observed:       Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    delta:          Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0", nullable=False)
    detected_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class CompromiseSimulation(Base, OrgMixin, TenantMixin, IdMixin):
    """
    Stored results of a compromise simulation. Feature 5.
    Operators run hypothetical attacks against the graph to size blast radius.
    """
    __tablename__ = "compromise_simulations"

    actor_node_id:   Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    scenario:        Mapped[str] = mapped_column(String(64), nullable=False)
    depth:           Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    reachable_nodes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    affected_tenants: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    blast_radius:    Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    risk_score:      Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0", nullable=False)
    summary:         Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    started_by:      Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )
