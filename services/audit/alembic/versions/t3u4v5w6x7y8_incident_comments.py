"""add acp_incidents and acp_incident_comments tables

Revision ID: t3u4v5w6x7y8
Revises: s2t3u4v5w6x7
Create Date: 2026-05-26 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t3u4v5w6x7y8"
down_revision: str | None = "s2t3u4v5w6x7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── acp_incidents ────────────────────────────────────────────────────────
    op.create_table(
        "acp_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("assignee", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_audit_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_acp_incidents_tenant_id", "acp_incidents", ["tenant_id"])
    op.create_index("ix_acp_incidents_status", "acp_incidents", ["status"])
    op.create_index("ix_acp_incidents_created_at", "acp_incidents", ["created_at"])
    op.create_index("ix_acp_incidents_source_audit_id", "acp_incidents", ["source_audit_id"])

    # ── acp_incident_comments ────────────────────────────────────────────────
    op.create_table(
        "acp_incident_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["acp_incidents.id"],
            name="fk_incident_comments_incident_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_incident_comments_incident_created",
        "acp_incident_comments",
        ["incident_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_incident_comments_incident_created", table_name="acp_incident_comments")
    op.drop_table("acp_incident_comments")

    op.drop_index("ix_acp_incidents_source_audit_id", table_name="acp_incidents")
    op.drop_index("ix_acp_incidents_created_at", table_name="acp_incidents")
    op.drop_index("ix_acp_incidents_status", table_name="acp_incidents")
    op.drop_index("ix_acp_incidents_tenant_id", table_name="acp_incidents")
    op.drop_table("acp_incidents")
