"""add chain_shard to audit_logs (H-2 sharded chain locks)

Revision ID: e9f1a2b3c4d5
Revises: d2e3f4a5b6c7
Create Date: 2026-05-13 00:00:00.000000

H-2 FIX: Adds chain_shard column so the audit chain can be sharded per-tenant.
Previously a single pg_advisory_xact_lock per tenant serialized ALL audit writes
for that tenant — a hard per-tenant throughput cap. Now the chain is split into
N shards keyed by hash(request_id); locks are taken per (tenant, shard), so
concurrent writes for a single tenant proceed in parallel while each shard
remains a verifiable chain.

Default 0 for existing rows preserves the legacy single-chain semantics. The
integrity verifier validates each shard independently.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e9f1a2b3c4d5'
down_revision: str | Sequence[str] | None = 'd2e3f4a5b6c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'audit_logs',
        sa.Column(
            'chain_shard',
            sa.SmallInteger(),
            nullable=False,
            server_default='0',
        ),
    )
    op.create_index(
        'ix_audit_logs_chain_shard',
        'audit_logs',
        ['tenant_id', 'chain_shard', 'timestamp'],
    )


def downgrade() -> None:
    op.drop_index('ix_audit_logs_chain_shard', table_name='audit_logs')
    op.drop_column('audit_logs', 'chain_shard')
