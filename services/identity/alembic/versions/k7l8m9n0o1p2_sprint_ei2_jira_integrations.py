"""Sprint EI-2 — per-tenant Jira ITSM integration.

Revision ID: k7l8m9n0o1p2
Revises: e2f3a4b5c6d7
Create Date: 2026-06-20

One row per tenant carrying the Cloud Jira base_url + project_key + the
service account's email + API token. The autonomy webhook executor reads
this row when CREATE_JIRA_ISSUE fires and the incident_watcher reads it
to auto-open a ticket on every new incident if `auto_create_on_incident`
is true.

Per-tenant uniqueness is the application's responsibility (the upsert
handler in services/identity/router.py picks the existing row before
inserting) — we also add a UniqueConstraint here so a buggy code path
that tries to insert two rows for the same tenant fails fast at the DB.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "k7l8m9n0o1p2"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jira_integrations",
        sa.Column("id",            sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",     sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",        sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_url",      sa.String(255), nullable=False),
        sa.Column("project_key",   sa.String(32),  nullable=False),
        sa.Column("account_email", sa.String(255), nullable=False),
        sa.Column("api_token",     sa.String(512), nullable=False),
        sa.Column("default_issue_type", sa.String(32), nullable=False,
                  server_default=sa.text("'Bug'")),
        sa.Column("default_priority",   sa.String(32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("auto_create_on_incident", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_jira_integrations_tenant"),
        sa.CheckConstraint("org_id = tenant_id",
                           name="ck_jira_integrations_org_tenant_match"),
    )
    op.create_index(
        "ix_jira_integrations_tenant",
        "jira_integrations",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_jira_integrations_tenant", table_name="jira_integrations")
    op.drop_table("jira_integrations")
