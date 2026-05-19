"""init learning schema (behavior_profiles)

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-05-16 00:00:00.000000
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "behavior_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_usage_distribution", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("transition_matrix", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("avg_velocity", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("baseline_risk", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", name="uq_behavior_profiles_agent_id"),
    )
    op.create_index("ix_behavior_profiles_tenant_agent", "behavior_profiles", ["tenant_id", "agent_id"])


def downgrade() -> None:
    op.drop_index("ix_behavior_profiles_tenant_agent", table_name="behavior_profiles")
    op.drop_table("behavior_profiles")
