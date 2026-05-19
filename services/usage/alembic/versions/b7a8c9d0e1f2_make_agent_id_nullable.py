"""make agent_id nullable

Revision ID: b7a8c9d0e1f2
Revises: a6959f6b02bb
Create Date: 2026-05-03 20:15:00.000000

Auth failures and system events occur before agent_id is known.
Change agent_id from NOT NULL to nullable to support these cases.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7a8c9d0e1f2'
down_revision: str | Sequence[str] | None = 'a6959f6b02bb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('usage_records', 'agent_id',
               existing_type=sa.UUID(),
               nullable=True,
               existing_nullable=False)


def downgrade() -> None:
    op.alter_column('usage_records', 'agent_id',
               existing_type=sa.UUID(),
               nullable=False,
               existing_nullable=True)
