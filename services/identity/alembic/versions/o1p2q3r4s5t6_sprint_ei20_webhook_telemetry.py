"""Sprint EI-20 — inbound-webhook deliverability telemetry.

Revision ID: o1p2q3r4s5t6
Revises: n0o1p2q3r4s5
Create Date: 2026-06-21

Adds last_webhook_received_at (DateTime) + last_webhook_status (String(32))
to both jira_integrations + servicenow_integrations. Written on EVERY
inbound /webhooks/{jira,servicenow} event — success or fail, before AND
after HMAC verify — so the operator's Settings UI can answer "did our
webhook actually reach Aegis?" without grepping gateway logs.

The status column carries the same word the webhook handler returns to
the upstream platform (closed / already_closed / ignored / no_config /
bad_signature / unknown_issue_key / patch_failed). The wire vocab is
locked at services/gateway/routers/itsm_webhooks.py:WEBHOOK_STATUS_VOCAB.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "o1p2q3r4s5t6"
down_revision = "n0o1p2q3r4s5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("jira_integrations", "servicenow_integrations"):
        op.add_column(
            table,
            sa.Column("last_webhook_received_at",
                      sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("last_webhook_status", sa.String(32), nullable=True),
        )


def downgrade() -> None:
    for table in ("servicenow_integrations", "jira_integrations"):
        op.drop_column(table, "last_webhook_status")
        op.drop_column(table, "last_webhook_received_at")
