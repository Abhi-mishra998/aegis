"""
N8 — Concurrent /auth/clerk/provision must not double-INSERT.

The pre-fix code in :func:`services.identity.webhooks_clerk._handle_membership_created_or_updated`
did a ``SELECT WHERE clerk_user_id = X`` followed by an INSERT when the
select returned None. Two concurrent provision requests for the same
Clerk subject could both pass the SELECT, both attempt INSERT, and the
second commit hit the UNIQUE constraint on ``clerk_user_id``. The
caller saw a 500.

The fix is in two parts:

  (1) A real, named UNIQUE constraint on ``users.clerk_user_id``
      (migration ``d1f2e3a4b5c6_user_clerk_user_id_unique.py``) so the
      ON CONFLICT inference target is identical in the prod schema and
      ``Base.metadata.create_all`` (the test fixture).

  (2) ``INSERT ... ON CONFLICT (clerk_user_id) DO UPDATE`` in the
      handler — atomic at the DB level so the race is impossible.

This test fires two concurrent ``_handle_membership_created_or_updated``
calls for the same ``clerk_user_id`` against the real identity Postgres
instance and asserts that:

  - both calls return successfully (no IntegrityError leaked to the
    caller as a 500),
  - both return the same ``user_id``,
  - the ``users`` table contains exactly ONE row for the
    ``clerk_user_id``.

Forcing the race
----------------
asyncio gather alone is not enough to reliably trigger the race because
the asyncio scheduler tends to run each task's SELECT and INSERT
together before yielding to the other task. We wrap ``AsyncSession.execute``
with a synchronisation barrier so that BOTH tasks complete their pre-INSERT
SELECT before EITHER reaches INSERT. That makes the race scenario
deterministic — without the fix every run would surface IntegrityError on
the losing task. The barrier touches only the test session, not
production code.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.identity.models import Organization, Tenant, TenantTier, User
from services.identity.tests.conftest import IDENTITY_TEST_DB_URL
from services.identity.webhooks_clerk import _handle_membership_created_or_updated

# Live Postgres required (asyncpg connection to acp_identity), same
# requirement as test_org_id_consistency.py. ``pytest -m 'not
# integration'`` (the project default) skips this file.
pytestmark = pytest.mark.integration


def _membership_payload(clerk_org_id: str, clerk_user_id: str, email: str) -> dict:
    """Minimal organizationMembership.created payload shape."""
    return {
        "id": f"orgmem_{uuid.uuid4().hex[:8]}",
        "role": "org:admin",
        "organization": {"id": clerk_org_id},
        "public_user_data": {
            "user_id": clerk_user_id,
            "identifier": email,
            "email_addresses": [
                {"id": "primary", "email_address": email},
            ],
            "primary_email_address_id": "primary",
        },
    }


async def _seed_org_and_tenant(db, clerk_org_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Pre-create the Organization+Tenant rows so the membership handler
    can find them without needing the Clerk Backend API or the
    write-back to org public_metadata."""
    tenant_uuid = uuid.uuid4()
    org = Organization(
        name="N8 race fixture",
        slug=f"n8-{uuid.uuid4().hex[:8]}",
        clerk_org_id=clerk_org_id,
    )
    db.add(org)
    await db.flush()
    tenant = Tenant(
        org_id=org.id,
        tenant_id=tenant_uuid,
        name="N8 race fixture",
        tier=TenantTier.BASIC,
        rpm_limit=0,
    )
    db.add(tenant)
    await db.commit()
    return org.id, tenant_uuid


@pytest.mark.asyncio
async def test_concurrent_provision_for_same_clerk_user_yields_one_row() -> None:
    """
    Two concurrent ``_handle_membership_created_or_updated`` calls for
    the same clerk_user_id must both return success and leave exactly
    one ``users`` row behind.
    """
    # Each concurrent task needs its own AsyncSession bound to the same
    # underlying engine so the two writers race at the Postgres level
    # (sharing a session would just queue them up on the asyncio loop).
    engine = create_async_engine(IDENTITY_TEST_DB_URL)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    clerk_org_id = f"org_n8_{uuid.uuid4().hex[:8]}"
    clerk_user_id = f"user_n8_{uuid.uuid4().hex[:12]}"
    email = f"n8-{uuid.uuid4().hex[:8]}@example.com"

    # Seed organization + tenant up-front so the handler skips its
    # late-arriving-org branch (that branch calls _handle_organization_created
    # which makes an outbound Clerk metadata write we don't want to mock here).
    async with session_factory() as setup_session:
        _, tenant_uuid = await _seed_org_and_tenant(setup_session, clerk_org_id)

    payload = _membership_payload(clerk_org_id, clerk_user_id, email)
    redis_mock = AsyncMock()  # handler does not touch redis on this path

    # Barrier forces both tasks to complete every pre-write SELECT before
    # either task is allowed to INSERT/UPDATE. The pre-fix code would then
    # deterministically fail one task with IntegrityError; the post-fix
    # ON CONFLICT path resolves both atomically.
    barrier = asyncio.Barrier(2)
    original_execute = AsyncSession.execute

    async def barrier_execute(self, statement, *args, **kwargs):
        result = await original_execute(self, statement, *args, **kwargs)
        # Only block at the SELECT phase — the handler runs two SELECTs
        # (legacy-email lookup + the clerk_user_id resolution) before any
        # write. We synchronise on the LAST SELECT in the handler, which
        # is the clerk_user_id one in the pre-fix code (the
        # legacy-email-link SELECT is gated by ``if email:`` and queries
        # User by ``email``). Easiest robust check: wait once per task
        # right before the INSERT.
        text = str(statement)
        if text.strip().upper().startswith("SELECT") and "users" in text.lower():
            try:
                # ``wait()`` releases once both tasks have arrived; a
                # third arrival would just no-op past it via the
                # ``_done`` flag we keep on the barrier.
                if not getattr(barrier, "_n8_released", False):
                    await barrier.wait()
                    barrier._n8_released = True
            except asyncio.BrokenBarrierError:
                pass
        return result

    async def _race(session_factory):
        async with session_factory() as s:
            return await _handle_membership_created_or_updated(s, redis_mock, payload)

    try:
        with patch.object(AsyncSession, "execute", barrier_execute):
            results = await asyncio.gather(
                _race(session_factory),
                _race(session_factory),
                return_exceptions=True,
            )

        # Neither call may surface a 500 / IntegrityError to the caller.
        for idx, result in enumerate(results):
            assert not isinstance(result, BaseException), (
                f"Concurrent call #{idx} raised {type(result).__name__}: {result}"
            )

        user_ids = [r["user_id"] for r in results]
        assert user_ids[0] == user_ids[1], (
            f"Concurrent provision returned two different user_ids "
            f"({user_ids[0]} vs {user_ids[1]}) — the table now has duplicates."
        )

        # End-state assertion: exactly one row in ``users`` for this
        # clerk_user_id.
        async with session_factory() as verify_session:
            count_q = await verify_session.execute(
                select(func.count())
                .select_from(User)
                .where(User.clerk_user_id == clerk_user_id),
            )
            row_count = count_q.scalar_one()
            assert row_count == 1, (
                f"Expected exactly 1 User row for clerk_user_id={clerk_user_id!r}, "
                f"found {row_count} — the race produced a duplicate."
            )

            # Tenant binding must be the seeded tenant.
            user_q = await verify_session.execute(
                select(User).where(User.clerk_user_id == clerk_user_id),
            )
            user_row = user_q.scalar_one()
            assert user_row.tenant_id == tenant_uuid
            assert user_row.org_id == tenant_uuid
            assert user_row.is_active is True
    finally:
        # Cleanup so re-runs of the test do not accumulate rows.
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                User.__table__.delete().where(User.clerk_user_id == clerk_user_id),
            )
            await cleanup_session.execute(
                Tenant.__table__.delete().where(Tenant.tenant_id == tenant_uuid),
            )
            await cleanup_session.execute(
                Organization.__table__.delete().where(
                    Organization.clerk_org_id == clerk_org_id,
                ),
            )
            await cleanup_session.commit()
        await engine.dispose()
