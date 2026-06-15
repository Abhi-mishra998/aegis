"""execution_timelines.session_id — Sprint 3 Session Explorer

Revision ID: g3c4d5e6f7a8
Revises: f2a1b2c3d4e5
Create Date: 2026-06-13

Sprint 3.5 — adds optional ``session_id`` to ``execution_timelines`` so the
Session Explorer can group decisions by conversation. Clients pass
``X-Session-ID`` on ``/execute`` and the gateway propagates it into the
flight-recorder timeline emit. Pre-Sprint-3 rows have NULL and are
filtered out of the Session Explorer; nothing else regresses.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "f2a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_timelines",
        sa.Column("session_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_timelines_tenant_session",
        "execution_timelines",
        ["tenant_id", "session_id", "started_at"],
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_timelines_tenant_session", table_name="execution_timelines")
    op.drop_column("execution_timelines", "session_id")
