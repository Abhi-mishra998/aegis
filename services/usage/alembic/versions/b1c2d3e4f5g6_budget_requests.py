"""add budget_requests table

Revision ID: b1c2d3e4f5g6
Revises: c8d9e0f1a2b3
Create Date: 2026-05-26 00:00:00.000000

Adds the budget_requests table used by the budget approval workflow.
Agents approaching or hitting their daily/monthly cost cap can submit a
budget increase request; a human manager approves or rejects it in the
dashboard.  On approval the per-agent Redis cost-cap key is updated
atomically so the new limit takes effect immediately.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5g6"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_requests",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("agent_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("current_cap_usd", sa.Float, nullable=False),
        sa.Column("requested_cap_usd", sa.Float, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_budget_requests_tenant_status",
        "budget_requests",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_budget_requests_tenant_status", table_name="budget_requests")
    op.drop_table("budget_requests")
