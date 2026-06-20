"""Sprint EI-6 — per-tenant ServiceNow ITSM integration.

Revision ID: m9n0o1p2q3r4
Revises: l8m9n0o1p2q3
Create Date: 2026-06-20

One row per tenant. Mirrors the jira_integrations table shape (Sprint EI-2
revision k7l8m9n0o1p2). The autonomy webhook executor reads this row when
CREATE_SNOW_INCIDENT fires and the incident_watcher reads it to auto-open
a ticket on every new incident if `auto_create_on_incident` is true.

ServiceNow Table API takes Basic auth: a service-account username + that
account's password. SNOW's MFA + password rotation is the operator's
responsibility on the SNOW side — Aegis only stores what was last pasted.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "m9n0o1p2q3r4"
down_revision = "l8m9n0o1p2q3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "servicenow_integrations",
        sa.Column("id",            sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",     sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",        sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_url",  sa.String(255), nullable=False),
        sa.Column("username",      sa.String(128), nullable=False),
        sa.Column("password",      sa.String(512), nullable=False),
        sa.Column("default_urgency", sa.Integer(), nullable=False,
                  server_default=sa.text("2")),
        sa.Column("default_impact",  sa.Integer(), nullable=False,
                  server_default=sa.text("2")),
        sa.Column("default_category",         sa.String(64), nullable=True),
        sa.Column("default_assignment_group", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("auto_create_on_incident", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_servicenow_integrations_tenant"),
        sa.CheckConstraint("org_id = tenant_id",
                           name="ck_servicenow_integrations_org_tenant_match"),
    )
    op.create_index(
        "ix_servicenow_integrations_tenant",
        "servicenow_integrations",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_servicenow_integrations_tenant",
                  table_name="servicenow_integrations")
    op.drop_table("servicenow_integrations")
