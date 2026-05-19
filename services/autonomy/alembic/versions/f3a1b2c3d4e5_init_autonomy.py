"""init autonomy schema

Revision ID: f3a1b2c3d4e5
Revises:
Create Date: 2026-05-13 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a1b2c3d4e5"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomy_contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name",      sa.String(128), nullable=False),
        sa.Column("enabled",   sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("version",   sa.Integer, nullable=False, server_default="1"),
        sa.Column("allowed_actions",    postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("denied_actions",     postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("approval_required",  postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_runtime_seconds", sa.Integer, nullable=True),
        sa.Column("max_tool_calls",     sa.Integer, nullable=True),
        sa.Column("max_cost_usd",       sa.Float, nullable=True),
        sa.Column("max_autonomy_level", sa.Integer, nullable=False, server_default="2"),
        sa.Column("escalation_triggers", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes",     sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "agent_id", "name", name="uq_autonomy_contracts_unique"),
    )
    op.create_index("ix_autonomy_contracts_tenant_agent", "autonomy_contracts", ["tenant_id", "agent_id"])

    op.create_table(
        "autonomy_contract_violations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("rule",      sa.String(64), nullable=False),
        sa.Column("detail",    postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_autonomy_violations_tenant_time", "autonomy_contract_violations", ["tenant_id", "detected_at"])

    op.create_table(
        "human_override_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id",    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor",     sa.String(255), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("reason",    sa.Text, nullable=True),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_overrides_tenant_time", "human_override_events", ["tenant_id", "occurred_at"])
    op.create_index("ix_overrides_tenant_target", "human_override_events", ["tenant_id", "target_kind", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_overrides_tenant_target", table_name="human_override_events")
    op.drop_index("ix_overrides_tenant_time", table_name="human_override_events")
    op.drop_table("human_override_events")
    op.drop_index("ix_autonomy_violations_tenant_time", table_name="autonomy_contract_violations")
    op.drop_table("autonomy_contract_violations")
    op.drop_index("ix_autonomy_contracts_tenant_agent", table_name="autonomy_contracts")
    op.drop_table("autonomy_contracts")
