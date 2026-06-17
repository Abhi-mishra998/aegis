"""audit_logs append-only enforcement (database-level trigger)

Revision ID: 3a519b48a6f2
Revises: y0a1b2c3d4e5
Create Date: 2026-06-17 00:00:00.000000

Adds a PostgreSQL trigger that raises on any UPDATE or DELETE against
``audit_logs``. The audit log is the cryptographic source of truth —
daily Merkle roots chain over its INSERT-only history. Every mutation
belongs in the chain head only as a new INSERT.

Application code already treats the table as append-only; this migration
moves the invariant into the database so a compromised admin, an ORM
bug, or a SQL-injection cannot silently mutate or delete chain rows.
The trigger BEFORE-fires per row, raises ``P0001``, and aborts the
transaction — Postgres rolls back any partial work.

To intentionally bypass during a destructive maintenance task, drop the
trigger inside the same transaction (``DROP TRIGGER deny_audit_log_mutation
ON audit_logs``), do the work, and re-create it. Any such operation must
be recorded in ``docs/runbooks/drill_log.md`` and chained into the next
Merkle root marker row.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "3a519b48a6f2"
down_revision: str | None = "y0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION deny_audit_log_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_logs is append-only; % is forbidden',
                TG_OP
                USING ERRCODE = 'P0001';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS deny_audit_log_mutation ON audit_logs;
    """)
    op.execute("""
        CREATE TRIGGER deny_audit_log_mutation
            BEFORE UPDATE OR DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION deny_audit_log_mutation();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS deny_audit_log_mutation ON audit_logs;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS deny_audit_log_mutation();"
    )
