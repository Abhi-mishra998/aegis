"""Sprint EI-17 — external ITSM linkage columns on incidents.

Revision ID: k6f7g8h9i0j1
Revises: j5e6f7g8h9i0
Create Date: 2026-06-21

Adds four nullable columns to api.incidents — populated by the
incident_watcher.py auto-create hook (EI-2 + EI-6) when a Jira issue or
ServiceNow incident is successfully opened. The inbound /webhooks/jira
and /webhooks/servicenow endpoints index by these columns to find the
right Aegis incident to close.

All four are nullable + indexed (the webhook handler does a
SELECT … WHERE jira_issue_key = 'SEC-42' on every inbound event;
without the index this is a full table scan).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "k6f7g8h9i0j1"
down_revision = "j5e6f7g8h9i0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("jira_issue_key", sa.String(64), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("jira_issue_url", sa.String(512), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("servicenow_sys_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("servicenow_number", sa.String(32), nullable=True),
    )
    op.create_index(
        "ix_incidents_jira_issue_key",
        "incidents", ["jira_issue_key"],
        postgresql_where=sa.text("jira_issue_key IS NOT NULL"),
    )
    op.create_index(
        "ix_incidents_servicenow_sys_id",
        "incidents", ["servicenow_sys_id"],
        postgresql_where=sa.text("servicenow_sys_id IS NOT NULL"),
    )
    op.create_index(
        "ix_incidents_servicenow_number",
        "incidents", ["servicenow_number"],
        postgresql_where=sa.text("servicenow_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_servicenow_number", table_name="incidents")
    op.drop_index("ix_incidents_servicenow_sys_id", table_name="incidents")
    op.drop_index("ix_incidents_jira_issue_key", table_name="incidents")
    op.drop_column("incidents", "servicenow_number")
    op.drop_column("incidents", "servicenow_sys_id")
    op.drop_column("incidents", "jira_issue_url")
    op.drop_column("incidents", "jira_issue_key")
