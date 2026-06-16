"""sprint17.5 department + wider key_prefix

Sprint 17 left two rough edges that show up the moment a real customer
tries to mint an employee virtual key from the UI:

  1. api_keys.key_prefix was VARCHAR(10). The repo writes the first 12
     chars of the raw key as the display prefix — so for ``acp_…`` keys
     the leading "acp_" + 6 random chars = 10 chars (fits exactly) but
     for ``acp_emp_…`` keys the "acp_emp_" + 4 random = 12 chars
     overflows and Postgres rejects the insert with
     StringDataRightTruncationError. Bump the column to VARCHAR(16) so
     both shapes fit + leave headroom for any future subject_kind.

  2. No way to attribute employees to a department, which is the
     hero metric of Sprint 17.5's Department View ("which teams create
     risk"). Add a nullable VARCHAR(64) ``department`` column on the
     same row. Indexed for the per-department aggregation rollup
     endpoint.

Revision ID: i4d5e6f7g8h9
Revises: h3c4d5e6f7g8
Create Date: 2026-06-17 00:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'i4d5e6f7g8h9'
down_revision: str | Sequence[str] | None = 'h3c4d5e6f7g8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Postgres allows widening a VARCHAR in place without a rewrite.
    op.alter_column(
        'api_keys',
        'key_prefix',
        existing_type=sa.String(length=10),
        type_=sa.String(length=16),
        existing_nullable=False,
    )

    op.add_column(
        'api_keys',
        sa.Column('department', sa.String(length=64), nullable=True),
    )
    op.create_index(
        'ix_api_keys_department', 'api_keys', ['department'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_api_keys_department', table_name='api_keys')
    op.drop_column('api_keys', 'department')
    op.alter_column(
        'api_keys',
        'key_prefix',
        existing_type=sa.String(length=16),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
