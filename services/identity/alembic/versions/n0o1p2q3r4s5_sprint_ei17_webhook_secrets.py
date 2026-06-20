"""Sprint EI-17 — inbound-webhook HMAC secrets on the two ITSM integrations.

Revision ID: n0o1p2q3r4s5
Revises: m9n0o1p2q3r4
Create Date: 2026-06-21

Adds `webhook_secret` (nullable String(64)) to both jira_integrations and
servicenow_integrations. Populated server-side when the operator clicks
"Generate webhook secret" in the Settings tab; returned to the operator
ONCE so they can paste it into the upstream ITSM platform's outbound-
webhook config; never returned again over the CRUD surface.

Inbound /webhooks/{jira,servicenow} endpoints use HMAC-SHA256(body) of
the per-tenant secret as the only auth gate (no JWT — the upstream ITSM
platform doesn't carry one).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "n0o1p2q3r4s5"
down_revision = "m9n0o1p2q3r4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jira_integrations",
        sa.Column("webhook_secret", sa.String(64), nullable=True),
    )
    op.add_column(
        "servicenow_integrations",
        sa.Column("webhook_secret", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servicenow_integrations", "webhook_secret")
    op.drop_column("jira_integrations", "webhook_secret")
