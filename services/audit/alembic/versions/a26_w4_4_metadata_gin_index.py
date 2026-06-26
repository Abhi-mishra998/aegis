"""arch-26 W4.4 — GIN index on audit_logs.metadata_json (JSONB filter perf)

Revision ID: a26_w4_4_metadata_gin
Revises: p2_11_scim_audit_2026_06_22
Created for arch-26 Wave 4 perf fixes (2026-06-26).

The /audit/logs/search endpoint filters metadata_json with `@>` /
jsonpath expressions (e.g. find rows where metadata_json contains
`{"risk_score": ...}`). Without a JSONB GIN index, those filters
full-scan the per-tenant partition; at 10M rows × 100 tenants that
puts /audit/logs/search at p99 > 2s under any concurrent load.

jsonb_path_ops is the smaller-but-narrower GIN operator class —
covers `@>` containment (the only operator the filter uses today).
If we add jsonpath @? queries later, swap to default jsonb_ops.

CONCURRENTLY because audit_logs is the largest table; we cannot
hold a table lock during index build.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "a26_w4_4_metadata_gin"
down_revision: str | None = "p2_11_scim_audit_2026_06_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Skip-if-not-owner. In the prod-ha cluster the audit_logs table is
    # owned by the RDS master, not the application user the audit service
    # logs in as. `CREATE INDEX CONCURRENTLY` requires table ownership;
    # an unprivileged attempt fails with `InsufficientPrivilegeError:
    # must be owner of table audit_logs` and crashes the container loop
    # at every restart (the audit-Wave-4 first deploy attempt). We log
    # and continue; a DBA applies the index manually with elevated
    # creds: psql -c '<the CREATE INDEX statement below>'.
    sql = (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_audit_logs_metadata_json_gin "
        "ON audit_logs USING gin (metadata_json jsonb_path_ops)"
    )
    try:
        with op.get_context().autocommit_block():
            op.execute(sql)
    except Exception as exc:
        # Only swallow privilege errors. Anything else (syntax, dialect
        # mismatch, out-of-disk) should still crash so we notice.
        if "must be owner" in str(exc).lower() or "permission denied" in str(exc).lower() or "insufficientprivilege" in type(exc).__name__.lower():
            import logging
            logging.getLogger(__name__).warning(
                "a26_w4_4_metadata_gin: skipped CREATE INDEX (not table owner). "
                "Apply manually with elevated creds: %s", sql
            )
        else:
            raise


def downgrade() -> None:
    try:
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_metadata_json_gin")
    except Exception as exc:
        if "must be owner" in str(exc).lower() or "permission denied" in str(exc).lower():
            import logging
            logging.getLogger(__name__).warning(
                "a26_w4_4_metadata_gin: skipped DROP INDEX (not table owner)"
            )
        else:
            raise
