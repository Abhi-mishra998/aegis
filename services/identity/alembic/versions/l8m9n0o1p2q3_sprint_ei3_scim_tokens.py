"""Sprint EI-3 — Okta SCIM bearer token table.

Revision ID: l8m9n0o1p2q3
Revises: k7l8m9n0o1p2
Create Date: 2026-06-20

One row per (tenant, SCIM connector). The Okta admin pastes the plaintext
into the Okta App → Provisioning → Authentication → API token field. We
store only the sha256 hash + a printable prefix so the operator can
identify the row in the UI without revealing the secret.

revoked_at IS NULL means active; setting it deactivates the token in O(1)
without deleting the row, so the audit trail of "this token did X on
2026-07-10" survives revocation.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "l8m9n0o1p2q3"
down_revision = "k7l8m9n0o1p2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scim_tokens",
        sa.Column("id",            sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id",     sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id",        sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label",         sa.String(128), nullable=False),
        sa.Column("token_hash",    sa.String(64),  nullable=False, unique=True),
        sa.Column("token_prefix",  sa.String(24),  nullable=False),
        sa.Column("last_used_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",    sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("org_id = tenant_id", name="ck_scim_tokens_org_tenant_match"),
    )
    op.create_index("ix_scim_tokens_tenant", "scim_tokens", ["tenant_id"])
    op.create_index("ix_scim_tokens_token_hash", "scim_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_index("ix_scim_tokens_token_hash", table_name="scim_tokens")
    op.drop_index("ix_scim_tokens_tenant", table_name="scim_tokens")
    op.drop_table("scim_tokens")
