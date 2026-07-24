"""split billing_status off audit_logs into audit_billing_status

Revision ID: s3_split_billing_status_2026_07_21
Revises: p2_11_scim_audit_2026_06_22
Create Date: 2026-07-21

Audit S3 / P0-5 closure: the sibling migration ``3a519b48a6f2_audit_log_
append_only_trigger`` installs a BEFORE UPDATE OR DELETE trigger on
``audit_logs`` with no allow-list. That guards the Merkle-chain source of
truth against silent tamper, but it also blocks the ONE application-level
UPDATE the codebase does today (see ``services/audit/router.py`` billing
closure). Moving billing-status lifecycle metadata into a sibling table
keeps the append-only invariant absolute AND lets the reconciler close out
completions.

Design:
  * New table ``audit_billing_status(audit_id PK, tenant_id, status,
    updated_at)`` holds the mutable billing lifecycle state.
  * Backfill: one row per existing ``audit_logs`` row, copying the current
    ``billing_status`` value and the row's ``timestamp`` as ``updated_at``.
  * AFTER INSERT trigger on ``audit_logs`` keeps the invariant "every
    audit_logs row has an audit_billing_status row" — new inserts get a
    matching row automatically. The trigger reads ``NEW.billing_status``
    so the SQL default + code paths that set ``dlq``/``pending`` at insert
    still work without touching every insert site.
  * ``audit_logs.billing_status`` column is preserved for one release so
    verifiers pinned to the old schema keep parsing. The next migration
    ("t_" prefix) drops the column and updates the ORM.

Application code changes shipped in the same commit:
  * ``services/audit/router.py`` bulk closure ``PATCH /billing-status/
    complete`` moves from ``UPDATE audit_logs`` to ``UPSERT
    audit_billing_status``.
  * All three ``WHERE billing_status = 'pending'`` reader queries switch
    to ``JOIN audit_billing_status``.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "s3_split_billing_status_2026_07_21"
down_revision: str | None = "p2_11_scim_audit_2026_06_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_billing_status (
            audit_id   UUID PRIMARY KEY,
            tenant_id  UUID NOT NULL,
            status     VARCHAR(20) NOT NULL DEFAULT 'completed',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_billing_status_tenant_status
            ON audit_billing_status (tenant_id, status);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_billing_status_status
            ON audit_billing_status (status)
            WHERE status <> 'completed';
    """)

    # Backfill: one row per audit_logs row, copying current lifecycle state.
    # `updated_at` uses the original insert timestamp so post-migration reports
    # showing "when did billing close?" still resolve to the truthful moment.
    op.execute("""
        INSERT INTO audit_billing_status (audit_id, tenant_id, status, updated_at)
        SELECT id, tenant_id, COALESCE(billing_status, 'completed'), timestamp
        FROM audit_logs
        ON CONFLICT (audit_id) DO NOTHING;
    """)

    # Sync trigger: every new INSERT into audit_logs creates the matching
    # audit_billing_status row. Reads NEW.billing_status so the SQL default
    # (`'completed'`) plus the two code paths that pass 'dlq' / 'pending'
    # at insert time all keep working with zero application changes at the
    # insert sites.
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_audit_billing_status()
        RETURNS trigger AS $$
        BEGIN
            INSERT INTO audit_billing_status (audit_id, tenant_id, status, updated_at)
            VALUES (NEW.id, NEW.tenant_id, COALESCE(NEW.billing_status, 'completed'), NOW())
            ON CONFLICT (audit_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS sync_audit_billing_status ON audit_logs;")
    op.execute("""
        CREATE TRIGGER sync_audit_billing_status
            AFTER INSERT ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION sync_audit_billing_status();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS sync_audit_billing_status ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS sync_audit_billing_status();")
    op.execute("DROP INDEX IF EXISTS ix_audit_billing_status_status;")
    op.execute("DROP INDEX IF EXISTS ix_audit_billing_status_tenant_status;")
    op.execute("DROP TABLE IF EXISTS audit_billing_status;")
