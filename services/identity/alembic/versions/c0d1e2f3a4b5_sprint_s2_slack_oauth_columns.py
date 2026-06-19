"""Sprint S2 — Slack OAuth columns on tenants (bot_token, workspace_id, channel_id).

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-06-19

Three nullable columns. NULL on slack_bot_token means the OAuth flow
hasn't completed; the new /sso/slack/initiate router populates all
three at OAuth callback time. The legacy slack_webhook_url +
slack_approval_secret columns from sprint 21 stay — the OAuth callback
ALSO sets slack_webhook_url so the existing slack_approvals.py
post-card path fires unchanged.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("slack_bot_token", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("slack_workspace_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("slack_channel_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "slack_channel_id")
    op.drop_column("tenants", "slack_workspace_id")
    op.drop_column("tenants", "slack_bot_token")
