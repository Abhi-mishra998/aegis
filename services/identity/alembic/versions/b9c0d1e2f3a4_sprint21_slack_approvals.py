"""Sprint 21 — Slack approvals: webhook URL + HMAC signing secret on tenants.

Revision ID: b9c0d1e2f3a4
Revises: a7b8c9d0e1f2
Create Date: 2026-06-17

Two nullable columns. NULL on both disables the feature for the tenant;
the gateway's /v1/messages escalation path silently skips the webhook
post when either is empty, so this migration is safe to apply ahead of
the gateway rollout.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "b9c0d1e2f3a4"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("slack_webhook_url", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("slack_approval_secret", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "slack_approval_secret")
    op.drop_column("tenants", "slack_webhook_url")
