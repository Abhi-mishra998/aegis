"""composite (tenant_id, agent_id) uniqueness on behavior_profiles

Revision ID: s11h_composite_tenant_agent_2026_07_21
Revises: a1b2c3d4e5f6
Create Date: 2026-07-21

audit S11h (P1-14): the init migration set ``agent_id`` as a *global*
unique constraint. That happens to be safe today because agent_ids are
``uuid.uuid4()`` (collisions are astronomically unlikely), but the
constraint shape hides a defense-in-depth failure — a bulk import that
re-uses agent_ids across tenants, or a manual admin insert that copies
an existing agent_id under a new tenant, would silently fail with a
misleading error or (worse) surface as cross-tenant data leakage after
future schema changes.

This migration replaces the standalone uniqueness with the correct
composite ``(tenant_id, agent_id)`` shape. Combined with the repository
queries that now filter on both columns (see
``services/learning/repository.py``), the tenant isolation is enforced
at both the ORM and DB layers.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "s11h_composite_tenant_agent_2026_07_21"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_behavior_profiles_agent_id",
        "behavior_profiles",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_behavior_profiles_tenant_agent",
        "behavior_profiles",
        ["tenant_id", "agent_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_behavior_profiles_tenant_agent",
        "behavior_profiles",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_behavior_profiles_agent_id",
        "behavior_profiles",
        ["agent_id"],
    )
