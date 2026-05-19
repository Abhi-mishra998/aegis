"""add transparency_roots table (daily Merkle root commitment)

Revision ID: f1a2b3c4d5e6
Revises: e9f1a2b3c4d5
Create Date: 2026-05-14 12:00:00.000000

Daily commitment: for each (tenant_id, root_date) we persist the Merkle root
over every signed receipt in that day. Customers who archive the root at
end-of-day can later detect retroactive deletion or reordering — the
recomputed root would no longer match.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = 'f1a2b3c4d5e6'
down_revision: str | Sequence[str] | None = 'e9f1a2b3c4d5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'transparency_roots',
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('root_date', sa.Date(), nullable=False),
        sa.Column('root_hash', sa.String(length=64), nullable=False),
        sa.Column('leaf_count', sa.Integer(), nullable=False),
        sa.Column('signed_root_payload', JSONB(), nullable=False),
        sa.Column(
            'computed_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('tenant_id', 'root_date'),
    )
    op.create_index(
        'ix_transparency_roots_date',
        'transparency_roots',
        ['root_date', 'tenant_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_transparency_roots_date', table_name='transparency_roots')
    op.drop_table('transparency_roots')
