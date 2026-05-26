"""add acp_notifications table

Revision ID: s2t3u4v5w6x7
Revises: r1s2t3u4v5w6
Create Date: 2026-05-26 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s2t3u4v5w6x7"
down_revision: str | None = "r1s2t3u4v5w6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "acp_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="info"),
        sa.Column("category", sa.String(50), nullable=False, server_default="system"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("link", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_acp_notifications_tenant_id",
        "acp_notifications",
        ["tenant_id"],
    )
    op.create_index(
        "ix_acp_notifications_tenant_is_read",
        "acp_notifications",
        ["tenant_id", "is_read"],
    )


def downgrade() -> None:
    op.drop_index("ix_acp_notifications_tenant_is_read", table_name="acp_notifications")
    op.drop_index("ix_acp_notifications_tenant_id", table_name="acp_notifications")
    op.drop_table("acp_notifications")
