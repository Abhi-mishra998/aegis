"""P2-11 — Formalize the scim_tokens table in acp_audit (gateway's DB).

Revision ID: i4j5k6l7m8n9
Revises:     h3i4j5k6l7m8
Create Date: 2026-06-22

# Why this migration lives in audit-svc's chain (not gateway-owned)

The SCIM mint endpoint (``services/gateway/routers/scim_tokens.py``) and
the SCIM-bearer validator (``services/gateway/_scim_auth.py``) both run
**in the gateway** and use the gateway's request-scoped DB session,
which is bound to ``acp_audit`` (the gateway's DATABASE_URL). The table
therefore physically lives in ``acp_audit``.

``acp_audit`` is alembically owned by the audit-svc (env.py declares
``version_table="alembic_version_audit"`` with audit-svc's owned_tables).
There is no gateway-owned alembic chain that targets ``acp_audit``.

We had two options (P2-11 in 22-testing-report.md):

  A. Refactor SCIM mint+validate to live in identity-svc; identity owns
     the table in ``acp_identity`` via the existing pre-existing
     migration ``l8m9n0o1p2q3``. Gateway proxies via /auth/scim/validate.
     Right long-term shape, ~3-4 hrs of work.

  B. Keep SCIM code in gateway and add the migration to audit-svc's
     chain since that is what owns ``acp_audit``. This is option B.

We picked B for the same-day client deadline. The trade-off is the
naming weirdness — a "scim_tokens" table inside the audit-svc alembic
chain — but the physical DB ownership is correct and the migration is
idempotent against the existing hotfix-created table.

The identity-targeted migration
``services/identity/alembic/versions/l8m9n0o1p2q3_sprint_ei3_scim_tokens.py``
must NOT be applied to ``acp_identity`` — see its header note. It is
superseded by this file.

# Idempotency

The table was created in-place via asyncpg by an emergency hotfix on
2026-06-22 (no migration). Applying this migration on top of the
existing prod state must be a no-op. We use ``IF NOT EXISTS`` semantics:
inspect the catalog and skip ``create_table`` if the table is already
present. Same for the two indexes. Downgrade drops everything cleanly.

# Acceptance check

After ``alembic upgrade head`` against ``acp_audit``::

    SELECT to_regclass('public.scim_tokens');  -- → scim_tokens (not NULL)
    SELECT version_num FROM alembic_version_audit;  -- → i4j5k6l7m8n9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "i4j5k6l7m8n9"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None


_TABLE = "scim_tokens"
_IX_TENANT = "ix_scim_tokens_tenant"
_IX_HASH = "ix_scim_tokens_token_hash"


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names(schema="public")


def _has_index(table: str, index: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(table):
        return False
    return any(ix.get("name") == index for ix in inspector.get_indexes(table, schema="public"))


def upgrade() -> None:
    if not _has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id",            sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id",     sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("org_id",        sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("label",         sa.String(128), nullable=False),
            sa.Column("token_hash",    sa.String(64),  nullable=False, unique=True),
            sa.Column("token_prefix",  sa.String(24),  nullable=False),
            sa.Column("last_used_at",  sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at",    sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint("org_id = tenant_id", name="ck_scim_tokens_org_tenant_match"),
        )
    if not _has_index(_TABLE, _IX_TENANT):
        op.create_index(_IX_TENANT, _TABLE, ["tenant_id"])
    if not _has_index(_TABLE, _IX_HASH):
        op.create_index(_IX_HASH, _TABLE, ["token_hash"])


def downgrade() -> None:
    if _has_index(_TABLE, _IX_HASH):
        op.drop_index(_IX_HASH, table_name=_TABLE)
    if _has_index(_TABLE, _IX_TENANT):
        op.drop_index(_IX_TENANT, table_name=_TABLE)
    if _has_table(_TABLE):
        op.drop_table(_TABLE)
