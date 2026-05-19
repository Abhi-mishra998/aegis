"""Sprint 3.5 — daily inference-cost cap per tenant.

Adds:

  daily_inference_cost_cap_usd  NUMERIC(10,2) NULL  on tenants

NULL means "no cap" — the default for legacy tenants. Setting a numeric
value enables the cost-ceiling check in the gateway: at 80% of the cap
the system fires a billing alert; at 100% inference calls return 429
with limit_type="inference_cost" and an audit row of
action="inference_cost_cap_exceeded".

Per-agent caps live in Redis (`acp:agent_cost_cap:{agent_id}` = USD as
a string). Per-agent caps are operator-controlled hot config rather
than a new table — adding an agents table for one nullable column is
overkill given the agent-cred records already drive the rest of the
permission model.

Revision ID: d0e1f2a3b4c5
Revises:    c9d0e1f2a3b4
Create Date: 2026-05-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("daily_inference_cost_cap_usd", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "daily_inference_cost_cap_usd")
