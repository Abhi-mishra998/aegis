"""2026-06-22 — clean up duplicate personal Org/Tenant rows from the abhi986 incident.

Revision ID: p2q3r4s5t6u7
Revises: d1f2e3a4b5c6
Create Date: 2026-06-22

Background
----------
Before the transactional /auth/clerk/provision rewrite (services/identity/
clerk_provision.py, 2026-06-22), the provision handler derived
`clerk_org_id` from the gateway-canonicalised claims dict. The
canonicaliser intentionally overwrites `claims["org_id"]` with the Aegis
tenant UUID (for an unrelated invariant check downstream). On the second
call to /auth/clerk/provision for the same Clerk user, the handler then
fed a UUID into Organization.clerk_org_id (a string slot) and created a
fresh Organization + Tenant row, leaving the user with two of each.

The code fix removes the bug at its source. This migration repairs any
existing rows that exhibit the smell:

    Organization.clerk_org_id LIKE '________-____-____-____-____________'
    (i.e. UUID-shaped clerk_org_id values)

For every such Organization, we:
    1. find the matching Tenant (Tenant.org_id == Organization.id)
    2. find the User pointed at that Tenant (users.tenant_id == Tenant.tenant_id)
    3. find the PRIOR canonical Organization with
       clerk_org_id = 'personal_<users.clerk_user_id>' (if one exists)
    4. if a canonical org exists: deactivate the duplicate Org + Tenant and
       UPDATE Organization.clerk_org_id of the wrongly-keyed row to a sentinel
       so it never UNIQUE-collides with a fresh `personal_<uid>` insert.
    5. if no canonical org exists: just normalise the wrong Org's
       clerk_org_id to `personal_<users.clerk_user_id>` so the row becomes
       semantically correct (UNIQUE still passes — the wrong UUID-keyed
       row is the only one for this user).

This migration is safe to re-run (idempotent: the LIKE filter no longer
matches after the first pass).
"""
from __future__ import annotations

import re
import structlog
import sqlalchemy as sa
from alembic import op


revision = "p2q3r4s5t6u7"
down_revision = "d1f2e3a4b5c6"
branch_labels = None
depends_on = None

logger = structlog.get_logger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def upgrade() -> None:
    conn = op.get_bind()

    # Pull every organization whose clerk_org_id has UUID shape — these are
    # the suspect rows.
    suspect = conn.execute(sa.text(
        "SELECT id::text AS org_id, clerk_org_id "
        "FROM organizations "
        "WHERE clerk_org_id ~ "
        "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
    )).fetchall()

    if not suspect:
        return

    repaired_canonicalised = 0
    repaired_orphan = 0

    for row in suspect:
        wrong_org_id = row[0]
        wrong_clerk_org_id = row[1]
        if not _UUID_RE.match(wrong_clerk_org_id or ""):
            continue  # defense in depth — shouldn't trigger

        # Find the tenant + the user attached to that wrong org.
        tenant_row = conn.execute(sa.text(
            "SELECT tenant_id::text FROM tenants WHERE org_id = :oid"
        ), {"oid": wrong_org_id}).first()
        if tenant_row is None:
            continue
        wrong_tenant_id = tenant_row[0]

        user_row = conn.execute(sa.text(
            "SELECT clerk_user_id FROM users "
            "WHERE tenant_id::text = :tid AND clerk_user_id IS NOT NULL "
            "ORDER BY created_at ASC LIMIT 1"
        ), {"tid": wrong_tenant_id}).first()

        if user_row is None or not user_row[0]:
            # No human user attached — relabel to a sentinel so the row no
            # longer looks like a Clerk-active row but is preserved for audit.
            sentinel = f"DUP_{wrong_clerk_org_id}"
            conn.execute(sa.text(
                "UPDATE organizations "
                "SET clerk_org_id = :sentinel, is_active = false "
                "WHERE id = :oid"
            ), {"sentinel": sentinel[:64], "oid": wrong_org_id})
            conn.execute(sa.text(
                "UPDATE tenants SET is_active = false "
                "WHERE org_id = :oid"
            ), {"oid": wrong_org_id})
            repaired_orphan += 1
            continue

        clerk_user_id = user_row[0]
        canonical_clerk_org_id = f"personal_{clerk_user_id}"

        # Check if a canonical Org row already exists for this user.
        canonical_row = conn.execute(sa.text(
            "SELECT id::text FROM organizations "
            "WHERE clerk_org_id = :cco"
        ), {"cco": canonical_clerk_org_id}).first()

        if canonical_row is None:
            # No prior canonical row — just rename the wrong-keyed Org so
            # future /provision calls land on the same row.
            conn.execute(sa.text(
                "UPDATE organizations SET clerk_org_id = :cco "
                "WHERE id = :oid"
            ), {"cco": canonical_clerk_org_id[:64], "oid": wrong_org_id})
            repaired_canonicalised += 1
        else:
            # Both rows exist. Keep the current User pointer (the wrong
            # tenant is the one the user has been writing to — the
            # canonical row's tenant is the orphan). Deactivate the
            # canonical-but-empty pair and relabel its clerk_org_id to a
            # sentinel so the wrong-keyed Org can be safely renamed to
            # `personal_<uid>`.
            canonical_org_id = canonical_row[0]
            sentinel = f"DUP_{canonical_clerk_org_id}"
            conn.execute(sa.text(
                "UPDATE organizations "
                "SET clerk_org_id = :sentinel, is_active = false "
                "WHERE id = :oid"
            ), {"sentinel": sentinel[:64], "oid": canonical_org_id})
            conn.execute(sa.text(
                "UPDATE tenants SET is_active = false "
                "WHERE org_id = :oid"
            ), {"oid": canonical_org_id})
            # Now safely take over the canonical key.
            conn.execute(sa.text(
                "UPDATE organizations SET clerk_org_id = :cco "
                "WHERE id = :oid"
            ), {"cco": canonical_clerk_org_id[:64], "oid": wrong_org_id})
            repaired_canonicalised += 1

    logger.info(
        "duplicate_personal_org_tenant_cleanup_done",
        scanned=len(suspect),
        canonicalised=repaired_canonicalised,
        orphan_deactivated=repaired_orphan,
    )


def downgrade() -> None:
    # Data repair migration — irreversible. A symmetric "re-insert duplicates"
    # operation is not useful and would be unsafe (would re-create the
    # exact data shape that caused the original abhi986 outage).
    pass
