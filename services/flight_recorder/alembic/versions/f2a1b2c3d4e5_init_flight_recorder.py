"""init flight_recorder schema

Revision ID: f2a1b2c3d4e5
Revises:
Create Date: 2026-05-13 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a1b2c3d4e5"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_timelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("agent_id",  postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool",      sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("final_decision", sa.String(32), nullable=True),
        sa.Column("final_risk", sa.Float, nullable=True),
        sa.Column("status",    sa.String(32), nullable=False, server_default="in_progress"),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "request_id", name="uq_timelines_tenant_request"),
    )
    op.create_index("ix_timelines_tenant_started", "execution_timelines", ["tenant_id", "started_at"])

    op.create_table(
        "execution_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("step_type",  sa.String(32), nullable=False),
        sa.Column("status",     sa.String(32), nullable=False, server_default="ok"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("risk_score", sa.Float, nullable=True),
        sa.Column("summary",    sa.Text, nullable=True),
        sa.Column("payload",    postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_steps_timeline_order", "execution_steps", ["timeline_id", "step_index"])
    op.create_index("ix_steps_tenant_request", "execution_steps", ["tenant_id", "request_id", "step_index"])

    op.create_table(
        "execution_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("snapshot",   postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tokens_in",  sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_snapshots_timeline", "execution_snapshots", ["timeline_id", "step_index"])

    op.create_table(
        "execution_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id",   postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind",      sa.String(32), nullable=False),
        sa.Column("sha256",    sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("content",   sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_artifacts_timeline_kind", "execution_artifacts", ["timeline_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_timeline_kind", table_name="execution_artifacts")
    op.drop_table("execution_artifacts")
    op.drop_index("ix_snapshots_timeline", table_name="execution_snapshots")
    op.drop_table("execution_snapshots")
    op.drop_index("ix_steps_tenant_request", table_name="execution_steps")
    op.drop_index("ix_steps_timeline_order", table_name="execution_steps")
    op.drop_table("execution_steps")
    op.drop_index("ix_timelines_tenant_started", table_name="execution_timelines")
    op.drop_table("execution_timelines")
