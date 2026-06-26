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
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_audit_logs_metadata_json_gin "
            "ON audit_logs USING gin (metadata_json jsonb_path_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_metadata_json_gin")
