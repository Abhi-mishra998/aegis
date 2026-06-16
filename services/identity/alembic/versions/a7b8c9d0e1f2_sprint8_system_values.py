"""Sprint 8 — Per-tenant system_values JSONB for the Blast-Radius dollar formula.

Adds a single column:

  tenants.system_values  JSONB NOT NULL DEFAULT '{}'

Holds the workspace-configured dollar weight per resource kind, e.g.
``{"table": 50000, "api": 100000, "secret": 25000}``. The IAG blast-radius
endpoint reads this on every request (TenantMetadataCache, 10-min TTL) and
multiplies each untouched-resource kind's count by its weight to produce
the headline ``dollar_estimate`` the BlastRadiusCard renders.

NULL means "no weights configured" — the dollar_estimate then collapses to
0 and the BlastRadiusCard falls back to the criticality_score pill from
Sprint 5. Pre-Sprint-8 tenants get a `{}` default via the server_default
so the gateway never has to special-case None.

Revision ID: a7b8c9d0e1f2
Revises:    f1e2d3c4b5a6
Create Date: 2026-06-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7b8c9d0e1f2"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "system_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "system_values")
