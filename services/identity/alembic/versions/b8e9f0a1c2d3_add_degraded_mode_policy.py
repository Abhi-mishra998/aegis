"""add degraded_mode_policy column to tenants

Revision ID: b8e9f0a1c2d3
Revises: a1b2c3d4e5f6
Create Date: 2026-05-15 16:00:00.000000

Per-tenant policy controlling decision-service behavior when the behavior
firewall service is unreachable. Default `block_high_risk` preserves a
safe-by-default posture: low-risk tools may still execute (with an explicit
audit reason) while high-risk tools are blocked. Tenants who need stricter
or looser handling can opt in.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b8e9f0a1c2d3'
down_revision: str | Sequence[str] | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ENUM_NAME = "degraded_mode_policy_enum"
_ENUM_VALUES = ("block_high_risk", "block_all", "allow_with_audit")


def upgrade() -> None:
    degraded_enum = sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME)
    degraded_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'tenants',
        sa.Column(
            'degraded_mode_policy',
            degraded_enum,
            nullable=False,
            server_default='block_high_risk',
        ),
    )


def downgrade() -> None:
    op.drop_column('tenants', 'degraded_mode_policy')
    sa.Enum(name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
