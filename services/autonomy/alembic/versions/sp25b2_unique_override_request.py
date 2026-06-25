"""Sprint 25 B2 — unique (tenant_id, event_type, request_id) on human_override_events

Closes the approval double-execution race: two concurrent POSTs to
/autonomy/overrides with the same request_id + event_type both committed
their own row, fired two SSEs, and triggered the side-effects twice
(double-spend on the gated tool call).

NULL request_id is allowed (Postgres considers each NULL distinct), so
manual notes / stops without a request_id still work.

Revision ID: sp25b2_unique_override
Revises: p1q2r3s4t5u6
Create Date: 2026-06-25 00:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

revision: str = "sp25b2_unique_override"
down_revision: str | None = "p1q2r3s4t5u6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_human_override_request"


def upgrade() -> None:
    op.create_unique_constraint(
        _CONSTRAINT,
        "human_override_events",
        ["tenant_id", "event_type", "request_id"],
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "human_override_events", type_="unique")
