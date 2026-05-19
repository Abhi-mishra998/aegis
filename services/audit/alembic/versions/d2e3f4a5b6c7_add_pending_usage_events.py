"""add pending_usage_events table (outbox pattern)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-05-03 20:00:00.000000

Implements the outbox pattern for guaranteed billing delivery:
- PendingUsageEvent table queues usage records atomically with audit logs
- Gateway writes audit_log + pending_usage_event in single transaction
- Background worker processes pending events and writes to usage_records
- Guarantees zero orphaned audits (100% audit = 100% usage)
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd2e3f4a5b6c7'
down_revision: str | Sequence[str] | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'pending_usage_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('audit_id', sa.UUID(), nullable=False, unique=True),
        sa.Column('agent_id', sa.UUID(), nullable=True),
        sa.Column('tool', sa.String(length=255), nullable=False),
        sa.Column('units', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes for worker query patterns
    op.create_index(
        'ix_pending_usage_events_status',
        'pending_usage_events',
        ['status'],
    )
    op.create_index(
        'ix_pending_usage_events_tenant_id',
        'pending_usage_events',
        ['tenant_id'],
    )
    op.create_index(
        'ix_pending_usage_events_audit_id',
        'pending_usage_events',
        ['audit_id'],
        unique=True,
    )
    op.create_index(
        'ix_pending_usage_events_status_created_at',
        'pending_usage_events',
        ['status', 'created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_pending_usage_events_status_created_at', table_name='pending_usage_events')
    op.drop_index('ix_pending_usage_events_audit_id', table_name='pending_usage_events')
    op.drop_index('ix_pending_usage_events_tenant_id', table_name='pending_usage_events')
    op.drop_index('ix_pending_usage_events_status', table_name='pending_usage_events')
    op.drop_table('pending_usage_events')
