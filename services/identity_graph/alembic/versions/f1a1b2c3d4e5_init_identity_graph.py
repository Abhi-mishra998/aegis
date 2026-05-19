"""init identity_graph schema

Revision ID: f1a1b2c3d4e5
Revises:
Create Date: 2026-05-13 00:00:00.000000
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a1b2c3d4e5"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",     postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_type",  sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("name",       sa.String(255), nullable=False),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("trust_score", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("drift_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "node_type", "external_id", name="uq_graph_nodes_tenant_external"),
    )
    op.create_index("ix_graph_nodes_tenant_type", "graph_nodes", ["tenant_id", "node_type"])

    op.create_table(
        "graph_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",     postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("src_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dst_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edge_type", sa.String(32), nullable=False),
        sa.Column("action",    sa.String(100), nullable=False),
        sa.Column("outcome",   sa.String(32), nullable=False),
        sa.Column("risk_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("attributes", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_graph_edges_tenant_src", "graph_edges", ["tenant_id", "src_node_id"])
    op.create_index("ix_graph_edges_tenant_dst", "graph_edges", ["tenant_id", "dst_node_id"])
    op.create_index("ix_graph_edges_occurred", "graph_edges", ["occurred_at"])

    op.create_table(
        "trust_score_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id",   postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score",     sa.Float, nullable=False),
        sa.Column("components", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason",    sa.Text, nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trust_score_history_node", "trust_score_history", ["node_id", "captured_at"])

    op.create_table(
        "drift_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id",   postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column("severity",  sa.String(16), nullable=False, server_default="info"),
        sa.Column("baseline",  postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("observed",  postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("delta",     sa.Float, nullable=False, server_default="0.0"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_drift_signals_tenant_time", "drift_signals", ["tenant_id", "detected_at"])

    op.create_table(
        "compromise_simulations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario",  sa.String(64), nullable=False),
        sa.Column("depth",     sa.Integer, nullable=False, server_default="3"),
        sa.Column("reachable_nodes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("affected_tenants", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("blast_radius", sa.Integer, nullable=False, server_default="0"),
        sa.Column("risk_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("summary",    postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_by", sa.String(128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_compromise_sims_tenant_time", "compromise_simulations", ["tenant_id", "completed_at"])


def downgrade() -> None:
    op.drop_index("ix_compromise_sims_tenant_time", table_name="compromise_simulations")
    op.drop_table("compromise_simulations")
    op.drop_index("ix_drift_signals_tenant_time", table_name="drift_signals")
    op.drop_table("drift_signals")
    op.drop_index("ix_trust_score_history_node", table_name="trust_score_history")
    op.drop_table("trust_score_history")
    op.drop_index("ix_graph_edges_occurred", table_name="graph_edges")
    op.drop_index("ix_graph_edges_tenant_dst", table_name="graph_edges")
    op.drop_index("ix_graph_edges_tenant_src", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index("ix_graph_nodes_tenant_type", table_name="graph_nodes")
    op.drop_table("graph_nodes")
