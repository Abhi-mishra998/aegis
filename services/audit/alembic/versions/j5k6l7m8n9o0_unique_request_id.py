"""add partial unique index on audit_logs.request_id (non-null)

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-05-25 00:00:00.000000

Root cause of 2026-05-24 chain violation incident: two EC2 instances raced
to write the same request_id. ON CONFLICT on (request_id, event_hash) did not
fire because each instance computed a different prev_hash → different
event_hash, so both rows were inserted. This partial unique index on request_id
(WHERE request_id IS NOT NULL) ensures exactly one row per request regardless
of how many service replicas are running.

NULL request_ids (legacy user_login events) are exempt — PostgreSQL does not
consider NULLs equal in unique indexes, but the partial predicate makes the
intent explicit and keeps the index small.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "j5k6l7m8n9o0"
down_revision: str | None = "i4j5k6l7m8n9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use raw SQL for IF EXISTS / IF NOT EXISTS — alembic op helpers don't
    # support these, which caused startup failures when the DDL was pre-applied.
    op.execute("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS uq_audit_request_event")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_request_id_notnull "
        "ON audit_logs (request_id) WHERE request_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_audit_request_id_notnull", table_name="audit_logs")
    op.create_unique_constraint(
        "uq_audit_request_event", "audit_logs", ["request_id", "event_hash"]
    )
