"""add kill_switches table for Redis-resilient persistence

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-05-16 00:00:00.000000

C8 fix: persist kill switch engage/disengage to DB so Redis FLUSHDB or
restart cannot clear active security blocks. Decision service re-hydrates
Redis from this table on startup.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "i4j5k6l7m8n9"
down_revision: Union[str, None] = "h3i4j5k6l7m8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kill_switches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("engaged", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("engaged_by", sa.String(64), nullable=True),
        sa.Column("engaged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("disengaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_kill_switches_tenant"),
    )


def downgrade() -> None:
    op.drop_table("kill_switches")
