"""sprint17 employee keys

Adds the columns the Sprint 17 "Aegis for Teams" LLM-proxy mode needs on
``api_keys``:

  - subject_kind   VARCHAR(16) NOT NULL DEFAULT 'tenant'
                   ∈ {'tenant', 'agent', 'employee'}
  - subject_email  VARCHAR(255) NULL  (employee key spend-rollup identity)
  - daily_budget_usd   NUMERIC(10,2) NULL
  - monthly_budget_usd NUMERIC(10,2) NULL

Legacy rows are backfilled to ``subject_kind='tenant'`` so existing keys
keep behaving exactly as they did. Indexes on subject_kind + subject_email
support the /team-page list query (filter by kind='employee', join by
email) without a sequential scan.

Revision ID: h3c4d5e6f7g8
Revises: g2b3c4d5e6f7
Create Date: 2026-06-16 17:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'h3c4d5e6f7g8'
down_revision: str | Sequence[str] | None = 'g2b3c4d5e6f7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'api_keys',
        sa.Column(
            'subject_kind',
            sa.String(length=16),
            nullable=False,
            server_default='tenant',
        ),
    )
    op.add_column(
        'api_keys',
        sa.Column('subject_email', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'api_keys',
        sa.Column('daily_budget_usd', sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        'api_keys',
        sa.Column('monthly_budget_usd', sa.Numeric(10, 2), nullable=True),
    )
    op.create_index(
        'ix_api_keys_subject_kind', 'api_keys', ['subject_kind'], unique=False,
    )
    op.create_index(
        'ix_api_keys_subject_email', 'api_keys', ['subject_email'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_api_keys_subject_email', table_name='api_keys')
    op.drop_index('ix_api_keys_subject_kind', table_name='api_keys')
    op.drop_column('api_keys', 'monthly_budget_usd')
    op.drop_column('api_keys', 'daily_budget_usd')
    op.drop_column('api_keys', 'subject_email')
    op.drop_column('api_keys', 'subject_kind')
