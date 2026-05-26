"""add playbooks and playbook_runs tables

Revision ID: p1q2r3s4t5u6
Revises: f3a1b2c3d4e5
Create Date: 2026-05-26 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p1q2r3s4t5u6"
down_revision: str | None = "f3a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playbooks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "trigger_conditions",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "steps",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("mode", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("run_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_playbooks_tenant_id", "playbooks", ["tenant_id"])
    op.create_index("ix_playbooks_tenant_active", "playbooks", ["tenant_id", "is_active"])

    op.create_table(
        "playbook_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("triggered_by", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "steps_executed",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "result",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["playbooks.id"],
            name="fk_playbook_runs_playbook_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_playbook_runs_tenant_id", "playbook_runs", ["tenant_id"])
    op.create_index("ix_playbook_runs_playbook_id", "playbook_runs", ["playbook_id"])
    op.create_index(
        "ix_playbook_runs_tenant_time", "playbook_runs", ["tenant_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_playbook_runs_tenant_time", table_name="playbook_runs")
    op.drop_index("ix_playbook_runs_playbook_id", table_name="playbook_runs")
    op.drop_index("ix_playbook_runs_tenant_id", table_name="playbook_runs")
    op.drop_table("playbook_runs")
    op.drop_index("ix_playbooks_tenant_active", table_name="playbooks")
    op.drop_index("ix_playbooks_tenant_id", table_name="playbooks")
    op.drop_table("playbooks")
