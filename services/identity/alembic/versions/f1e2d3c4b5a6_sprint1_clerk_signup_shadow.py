"""Sprint 1 — Real-SaaS auth: shadow_mode_until, clerk_*_id, role enum extensions.

Adds the column + enum surface that the Sprint-1 self-serve signup flow
needs to land Clerk-issued users without breaking existing legacy rows.

Schema changes (additive only — no row backfill, no drops):

  tenants
    + shadow_mode_until           TIMESTAMPTZ NULL
      server_default = now() + interval '14 days'

  users
    + clerk_user_id               TEXT NULL UNIQUE INDEX

  organizations
    + clerk_org_id                TEXT NULL UNIQUE INDEX

  user_role_enum (Postgres enum type)
    + OWNER
    + SECURITY_ANALYST
    + DEVELOPER
    + READ_ONLY

The legacy enum values (ADMIN/SECURITY/AUDITOR/VIEWER/AGENT) stay on the
type for back-compat with rows written before this migration; the
gateway's verify_role middleware projects them onto the canonical Role
vocabulary via services.identity.models.canonical_role().

Downgrade drops the columns and re-creates user_role_enum without the
new values — destructive for any rows already using OWNER/etc, which is
the correct behaviour for a rollback.

Revision ID: f1e2d3c4b5a6
Revises:    d0e1f2a3b4c5
Create Date: 2026-06-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1e2d3c4b5a6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


# Enum values added on upgrade. Drop order on downgrade is the reverse so the
# rebuild preserves the original declaration sequence.
_NEW_ROLE_VALUES: tuple[str, ...] = (
    "OWNER",
    "SECURITY_ANALYST",
    "DEVELOPER",
    "READ_ONLY",
)


def upgrade() -> None:
    # --- 1. tenants.shadow_mode_until ----------------------------------------
    # NULL means "no shadow window" (legacy tenants are not retroactively
    # downgraded). The server_default applies on inserts going forward, which
    # is what /signup + the Clerk webhook receiver need.
    op.add_column(
        "tenants",
        sa.Column(
            "shadow_mode_until",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now() + interval '14 days'"),
        ),
    )

    # --- 2. users.clerk_user_id ---------------------------------------------
    # NULL on legacy rows. UNIQUE so two Clerk users can never collide on
    # our side; partial unique index (where NOT NULL) lets legacy rows
    # remain without competing for the NULL slot.
    op.add_column(
        "users",
        sa.Column("clerk_user_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_users_clerk_user_id",
        "users",
        ["clerk_user_id"],
        unique=True,
        postgresql_where=sa.text("clerk_user_id IS NOT NULL"),
    )

    # --- 3. organizations.clerk_org_id --------------------------------------
    op.add_column(
        "organizations",
        sa.Column("clerk_org_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_organizations_clerk_org_id",
        "organizations",
        ["clerk_org_id"],
        unique=True,
        postgresql_where=sa.text("clerk_org_id IS NOT NULL"),
    )

    # --- 4. user_role_enum + new values -------------------------------------
    # Postgres forbids enum mutation inside a transaction, so each ADD VALUE
    # runs in its own autocommit block. IF NOT EXISTS makes re-runs safe
    # (the migration re-applies cleanly on a partially-upgraded DB).
    with op.get_context().autocommit_block():
        for value in _NEW_ROLE_VALUES:
            op.execute(
                sa.text(f"ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS '{value}'"),
            )


def downgrade() -> None:
    # Drop the indexes first (Postgres won't drop a column with an index in
    # one go in older versions; explicit is safer either way).
    op.drop_index("ix_organizations_clerk_org_id", table_name="organizations")
    op.drop_column("organizations", "clerk_org_id")

    op.drop_index("ix_users_clerk_user_id", table_name="users")
    op.drop_column("users", "clerk_user_id")

    op.drop_column("tenants", "shadow_mode_until")

    # Rebuilding the enum type is the only safe way to remove values.
    # Any row whose `role` is one of the new values FAILS the conversion,
    # which is the correct behaviour for a rollback (forces an operator
    # to either decide what to do with those rows or block the downgrade).
    op.execute(sa.text("ALTER TYPE user_role_enum RENAME TO user_role_enum_old"))
    op.execute(
        sa.text(
            """
            CREATE TYPE user_role_enum AS ENUM (
                'ADMIN', 'SECURITY', 'AUDITOR', 'VIEWER', 'AGENT'
            )
            """,
        ),
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE users
                ALTER COLUMN role TYPE user_role_enum
                USING role::text::user_role_enum
            """,
        ),
    )
    op.execute(sa.text("DROP TYPE user_role_enum_old"))
