"""Tenant quota sprint 2026-05-15: rps/burst + daily/monthly request caps.

Adds four columns to `tenants` so per-tenant rate limiting can be
configured independently of the existing `rpm_limit`:

  - requests_per_second   INT   NOT NULL DEFAULT 50
  - burst                 INT   NOT NULL DEFAULT 100
  - daily_request_cap     INT   NOT NULL DEFAULT 1_000_000
  - monthly_request_cap   INT   NULL    (NULL = no monthly cap)

rpm_limit is kept for back-compat — the existing gateway middleware
still uses it as a 60-second rolling-window check; the new columns
drive the token-bucket (rps+burst) + daily/monthly counters that
replace the legacy enforcement under Sprint 3.2.

Revision ID: c9d0e1f2a3b4
Revises:    b8e9f0a1c2d3
Create Date: 2026-05-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8e9f0a1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("requests_per_second", sa.Integer(), nullable=False, server_default="50"),
    )
    op.add_column(
        "tenants",
        sa.Column("burst", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "daily_request_cap", sa.Integer(),
            nullable=False, server_default="1000000",
        ),
    )
    # monthly_request_cap nullable: NULL means "no monthly cap" (the
    # enterprise tier default). Tenants with a contractual ceiling get
    # an explicit integer value set via /auth/tenants.
    op.add_column(
        "tenants",
        sa.Column("monthly_request_cap", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "monthly_request_cap")
    op.drop_column("tenants", "daily_request_cap")
    op.drop_column("tenants", "burst")
    op.drop_column("tenants", "requests_per_second")
