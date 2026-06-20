"""Sprint EH-1 — explicit canonical Role on every api_keys row.

Revision ID: j5e6f7g8h9i0
Revises: i4d5e6f7g8h9
Create Date: 2026-06-21

Closes architect finding #1 (no authorization matrix): the gateway's
proxy handler used to set request.state.role from a JWT-derived default
when the inbound auth was an acp_emp_ / acp_ key, which meant every key
implicitly granted OWNER on the /v1/* surface. With a column on the row
we can mint a DEVELOPER-only Anthropic proxy key for an SDK without
giving its holder OWNER read of audit logs etc.

Legacy rows backfill to OWNER (server_default) — same behavior as before
this migration. New rows default to DEVELOPER in code.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'j5e6f7g8h9i0'
down_revision: str | Sequence[str] | None = 'i4d5e6f7g8h9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default="OWNER",
        ),
    )
    op.create_index(
        "ix_api_keys_role",
        "api_keys",
        ["role"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_role", table_name="api_keys")
    op.drop_column("api_keys", "role")
