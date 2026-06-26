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


# NB: alembic_version_audit.version_num is varchar(32) — keep this short.
# Original "a26_w4_5_audit_composite_decision" was 34 chars and crashed
# the audit container with StringDataRightTruncationError on the
# `UPDATE alembic_version_audit SET version_num=...` step (first deploy
# attempt of Wave 4b). Trimmed to "a26_w4_5_composite" = 18 chars.
revision: str = "a26_w4_5_composite"
down_revision: str | None = "a26_w4_4_metadata_gin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Skip-if-not-owner — see sibling a26_w4_4_metadata_gin_index.py for
    # the rationale. Same condition: audit service user is not the
    # audit_logs owner in prod-ha, so CREATE INDEX must be deferred to
    # a DBA with elevated creds.
    sql = (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_audit_logs_tenant_agent_decision_ts "
        "ON audit_logs (tenant_id, agent_id, decision, timestamp DESC)"
    )
    try:
        with op.get_context().autocommit_block():
            op.execute(sql)
    except Exception as exc:
        if "must be owner" in str(exc).lower() or "permission denied" in str(exc).lower() or "insufficientprivilege" in type(exc).__name__.lower():
            import logging
            logging.getLogger(__name__).warning(
                "a26_w4_5_composite: skipped CREATE INDEX (not table owner). "
                "Apply manually: %s", sql
            )
        else:
            raise


def downgrade() -> None:
    try:
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_tenant_agent_decision_ts")
    except Exception as exc:
        if "must be owner" in str(exc).lower() or "permission denied" in str(exc).lower():
            import logging
            logging.getLogger(__name__).warning(
                "a26_w4_5_composite: skipped DROP INDEX (not table owner)"
            )
        else:
            raise
