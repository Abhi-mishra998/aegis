"""api_keys.agent_id — per-agent scoped API keys

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-13

Sprint 1.5 — closes audit S5 "API-key agent binding". Existing rows get NULL
(legacy tenant-scoped behavior preserved); new keys may be issued with an
explicit ``agent_id`` so the gateway enforces the binding against the
inbound ``X-Agent-ID`` header.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'g2b3c4d5e6f7'
down_revision: str | Sequence[str] | None = 'f1a2b3c4d5e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'api_keys',
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index('ix_api_keys_agent_id', 'api_keys', ['agent_id'])


def downgrade() -> None:
    op.drop_index('ix_api_keys_agent_id', table_name='api_keys')
    op.drop_column('api_keys', 'agent_id')
