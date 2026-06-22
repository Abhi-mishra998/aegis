"""Sprint EI-3 — Okta SCIM bearer token table.

Revision ID: l8m9n0o1p2q3
Revises: k7l8m9n0o1p2
Create Date: 2026-06-20

# SUPERSEDED 2026-06-22 — DO NOT APPLY THIS TO acp_identity IN PROD.
#
# Pentest finding P2-11 (see 22-testing-report.md): the SCIM mint endpoint
# and bearer validator both live in the gateway and use the gateway's
# DB session (→ acp_audit). The table physically lives in acp_audit,
# not acp_identity. Running this migration on acp_identity would create
# a parallel empty table that the gateway never reads from, while the
# real table in acp_audit would remain unmanaged by any migration chain.
#
# Replaced by:
#   services/audit/alembic/versions/i4j5k6l7m8n9_p2_11_scim_tokens_in_acp_audit.py
#
# That migration is idempotent and matches the emergency hotfix that
# created the table in acp_audit on 2026-06-22.
#
# If the long-term plan ever moves SCIM mint+validate to identity-svc
# (option A in 22-testing-report.md), revisit this header — at that
# point the identity-svc DOES own the table and this file becomes
# correct again. Until then this migration is a no-op placeholder kept
# only for the alembic chain so down_revision links don't break.

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
    # SUPERSEDED — see header. The SCIM table is now created by
    # services/audit/alembic/versions/i4j5k6l7m8n9_p2_11_scim_tokens_in_acp_audit.py
    # in the acp_audit database (where the gateway's request-scoped DB
    # session actually reads/writes). This revision stays in the
    # acp_identity alembic chain only to preserve the down_revision link
    # for any descendant migration. It does NOT create the table here.
    pass


def downgrade() -> None:
    # Mirror of upgrade — no-op. Downgrading this revision must not drop
    # a table that this revision did not create. See header.
    pass
