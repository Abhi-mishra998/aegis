"""add pending_billing_events table

Revision ID: c8d9e0f1a2b3
Revises: b7a8c9d0e1f2
Create Date: 2026-05-24 00:00:00.000000

Adds a durable PostgreSQL-backed store for billing events that cannot be
delivered to the usage service immediately.  This supplements (not replaces)
the Redis DLQ (acp:billing_retry_queue) with crash-safe persistence so events
survive a Redis FLUSHDB or node failure.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b7a8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_billing_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audit_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audit_id", name="uq_pending_billing_events_audit_id"),
    )
    op.create_index(
        op.f("ix_pending_billing_events_tenant_id"),
        "pending_billing_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pending_billing_events_processed_at"),
        "pending_billing_events",
        ["processed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pending_billing_events_audit_id"),
        "pending_billing_events",
        ["audit_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_pending_billing_events_audit_id"),
        table_name="pending_billing_events",
    )
    op.drop_index(
        op.f("ix_pending_billing_events_processed_at"),
        table_name="pending_billing_events",
    )
    op.drop_index(
        op.f("ix_pending_billing_events_tenant_id"),
        table_name="pending_billing_events",
    )
    op.drop_table("pending_billing_events")
