"""Sprint S4 — demo workspace lifecycle (is_demo, demo_expires_at).

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-06-19

Two columns on tenants. is_demo defaults to false so every existing
production tenant stays a real workspace. POST /demo/spawn-workspace
mints sandbox tenants with is_demo=true + demo_expires_at = now()+24h,
and POST /demo/cleanup-expired hard-deletes the rows past that mark.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "d1e2f3a4b5c6"
down_revision = "c1d2e3f4a5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "is_demo", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "demo_expires_at", sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tenants_demo_expires_at", "tenants",
        ["demo_expires_at"],
        postgresql_where=sa.text("is_demo = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_demo_expires_at", table_name="tenants")
    op.drop_column("tenants", "demo_expires_at")
    op.drop_column("tenants", "is_demo")
