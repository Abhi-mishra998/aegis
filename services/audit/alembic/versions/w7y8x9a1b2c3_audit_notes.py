"""add audit_notes table for analyst investigation notes

Revision ID: w7y8x9a1b2c3
Revises: t3u4v5w6x7y8
Create Date: 2026-05-30 11:00:00.000000

The AuditNote model lives in services/audit/models.py and the routes
add_audit_note / list_audit_notes were shipped earlier, but no migration
ever created the table — so /audit/logs/{id}/notes returned 500 with
UndefinedTable. This migration creates the table and the supporting
(audit_id, tenant_id) lookup index used by the listing query.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w7y8x9a1b2c3"
down_revision: str | None = "t3u4v5w6x7y8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "note_type",
            sa.String(30),
            nullable=False,
            server_default="analysis",
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_audit_notes_audit_id", "audit_notes", ["audit_id"])
    op.create_index("ix_audit_notes_tenant_id", "audit_notes", ["tenant_id"])
    op.create_index(
        "ix_audit_notes_audit_id_tenant",
        "audit_notes",
        ["audit_id", "tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_notes_audit_id_tenant", table_name="audit_notes")
    op.drop_index("ix_audit_notes_tenant_id", table_name="audit_notes")
    op.drop_index("ix_audit_notes_audit_id", table_name="audit_notes")
    op.drop_table("audit_notes")
