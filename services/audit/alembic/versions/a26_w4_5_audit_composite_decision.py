"""arch-26 W4.5 — audit_logs (tenant_id, agent_id, decision, timestamp DESC) composite index

Revision ID: a26_w4_5_audit_composite_decision
Revises: a26_w4_4_metadata_gin
Created for arch-26 Wave 4 perf fixes (2026-06-26).

The /audit/logs filter pattern customers actually use is:
   WHERE tenant_id=$1 AND agent_id=$2 AND decision='deny'
   ORDER BY timestamp DESC LIMIT 100

The existing composite ix_audit_logs_tenant_ts covers (tenant_id, timestamp)
but the agent_id + decision filter still scans the per-tenant partition.
At 10M rows × 100 tenants, this puts the Incidents drill-down at p99 > 1s.

This index covers all four columns the filter touches, with timestamp DESC
so the ORDER BY is a free trailing read.

CONCURRENTLY because audit_logs is the largest table.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "a26_w4_5_audit_composite_decision"
down_revision: str | None = "a26_w4_4_metadata_gin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_audit_logs_tenant_agent_decision_ts "
            "ON audit_logs (tenant_id, agent_id, decision, timestamp DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_tenant_agent_decision_ts")
