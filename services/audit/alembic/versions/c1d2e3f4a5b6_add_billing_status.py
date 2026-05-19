"""add billing_status to audit_logs

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-05-02 19:00:00.000000

Adds billing_status column to audit_logs table to support the Write-Ahead
billing guarantee. Every audit log starts as 'pending' and is transitioned
to 'completed' after a usage_record is successfully inserted in the Usage Service.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c1d2e3f4a5b6'
down_revision: str | Sequence[str] | None = 'b1c2d3e4f5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'audit_logs',
        sa.Column(
            'billing_status',
            sa.String(length=20),
            nullable=False,
            server_default='pending',
        ),
    )
    op.create_index(
        'ix_audit_logs_billing_status',
        'audit_logs',
        ['billing_status'],
    )
    # Composite index for reconciliation query performance
    op.create_index(
        'ix_audit_logs_billing_status_timestamp',
        'audit_logs',
        ['billing_status', 'timestamp'],
    )


def downgrade() -> None:
    op.drop_index('ix_audit_logs_billing_status_timestamp', table_name='audit_logs')
    op.drop_index('ix_audit_logs_billing_status', table_name='audit_logs')
    op.drop_column('audit_logs', 'billing_status')
