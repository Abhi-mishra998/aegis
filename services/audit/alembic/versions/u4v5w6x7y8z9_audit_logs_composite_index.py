"""audit_logs (tenant_id, timestamp DESC) composite index + outbox/usage retention helpers

Revision ID: u4v5w6x7y8z9
Revises: t3u4v5w6x7y8
Created for sprint-1 fixes (audit-30, audit-v2, principal-engineer-review):
- Add composite index that aggregator.py queries actually need.
- Add retention helper functions for outbox / usage_events cleanup.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "u4v5w6x7y8z9"
down_revision: str | None = "w7y8x9a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # audit_logs filter pattern is always (tenant_id, timestamp >= since).
    # The single-column indexes from init_audit cannot serve both at once;
    # this composite covers it with timestamp DESC for /logs and /history
    # which page newest-first. CONCURRENTLY avoids locking the table.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_audit_logs_tenant_ts ON audit_logs (tenant_id, timestamp DESC)"
        )
        # action filter is common on /logs (e.g. action='execute_tool').
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_audit_logs_tenant_action_ts ON audit_logs (tenant_id, action, timestamp DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_tenant_action_ts")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_tenant_ts")
