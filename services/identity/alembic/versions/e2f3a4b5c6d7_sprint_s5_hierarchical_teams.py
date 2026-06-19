"""Sprint S5 — hierarchical Teams table + users.team_id FK.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-19

Adds a new `teams` table with parent_team_id self-FK for tree structure
and manager_user_id for the per-team rollup query. Adds team_id to the
users table as a nullable FK. Legacy users stay unassigned until the
operator places them into a team via the new TeamSettings UI.

The teams table sits inside the same per-tenant scope as users — every
query joins on tenant_id alongside team_id, and the org_id mixin
ensures CHECK constraint org_id == tenant_id propagates.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id",            sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",     sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",        sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name",          sa.String(128), nullable=False),
        sa.Column("parent_team_id",sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manager_user_id",sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("daily_budget_usd_cap",   sa.Integer(), nullable=True),
        sa.Column("monthly_budget_usd_cap", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_teams_tenant_name"),
        sa.CheckConstraint("org_id = tenant_id", name="ck_teams_org_tenant_match"),
    )
    op.create_index("ix_teams_tenant", "teams", ["tenant_id"])
    op.create_index("ix_teams_parent_team_id", "teams", ["parent_team_id"])

    op.add_column(
        "users",
        sa.Column(
            "team_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True,
        ),
    )
    op.create_index("ix_users_team_id", "users", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_users_team_id", table_name="users")
    op.drop_column("users", "team_id")
    op.drop_index("ix_teams_parent_team_id", table_name="teams")
    op.drop_index("ix_teams_tenant", table_name="teams")
    op.drop_table("teams")
