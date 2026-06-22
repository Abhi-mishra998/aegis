"""
Verify the new transactional /auth/clerk/provision is idempotent.

Runs in-process inside the identity container so we get a real DB + Redis
without smuggling the prod /provision JWT through a test harness. Exits 0
on success, 1 on any invariant failure.

Usage (run inside acp_identity):
    python3 /repo/scripts/ops/verify_provision_idempotent.py [N]

Default N=50. The same synthetic Clerk user id is used for every call so
the assertion is `org_count == tenant_count == user_count == 1`.

Cleanup: every Org / Tenant / User row written by this script is keyed on
the synthetic clerk_user_id prefix "test_idempotent_" and is removed
before the process exits, even on failure.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from redis.asyncio import from_url as redis_from_url

# Local imports — script must run inside the identity service container so
# the package is on sys.path.
sys.path.insert(0, "/repo")
from services.identity.clerk_provision import provision_aegis_identity  # noqa: E402
from services.identity.models import Organization, Tenant, User  # noqa: E402


N_CALLS = int(sys.argv[1]) if len(sys.argv) > 1 else 50
N_CONCURRENT = 20
SYN_CLERK_USER_ID = f"test_idempotent_{uuid.uuid4().hex[:8]}"


async def _cleanup(session_factory) -> None:
    async with session_factory() as db:
        # User cleanup
        await db.execute(
            delete(User).where(User.clerk_user_id == SYN_CLERK_USER_ID)
        )
        # Tenant + Org by clerk_org_id (synthetic prefix)
        clerk_org_id = f"personal_{SYN_CLERK_USER_ID}"
        org_rows = (await db.execute(
            select(Organization.id).where(Organization.clerk_org_id == clerk_org_id)
        )).all()
        for (oid,) in org_rows:
            await db.execute(delete(Tenant).where(Tenant.org_id == oid))
        await db.execute(
            delete(Organization).where(Organization.clerk_org_id == clerk_org_id)
        )
        await db.commit()


async def _count_invariant(session_factory) -> tuple[int, int, int]:
    async with session_factory() as db:
        clerk_org_id = f"personal_{SYN_CLERK_USER_ID}"
        org_count = (await db.execute(
            select(text("COUNT(*)")).select_from(Organization)
            .where(Organization.clerk_org_id == clerk_org_id)
        )).scalar_one()
        org_ids = (await db.execute(
            select(Organization.id).where(Organization.clerk_org_id == clerk_org_id)
        )).all()
        org_id_list = [r[0] for r in org_ids]
        tenant_count = 0
        if org_id_list:
            tenant_count = (await db.execute(
                select(text("COUNT(*)")).select_from(Tenant)
                .where(Tenant.org_id.in_(org_id_list))
            )).scalar_one()
        user_count = (await db.execute(
            select(text("COUNT(*)")).select_from(User)
            .where(User.clerk_user_id == SYN_CLERK_USER_ID)
        )).scalar_one()
        return int(org_count), int(tenant_count), int(user_count)


async def main() -> int:
    db_url = os.environ["DATABASE_URL"]
    redis_url = os.environ["REDIS_URL"]
    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_from_url(redis_url)

    rc = 0
    try:
        # Pre-flight: nothing matches our synthetic key.
        await _cleanup(session_factory)

        # ---- serial idempotency
        print(f"[serial] calling provision {N_CALLS}x for clerk_user_id={SYN_CLERK_USER_ID}")
        t0 = time.time()
        tenant_ids = set()
        for i in range(N_CALLS):
            async with session_factory() as db:
                result = await provision_aegis_identity(
                    db, redis,
                    clerk_user_id=SYN_CLERK_USER_ID,
                    raw_jwt_org_id=None,
                    org_role_claim="org:owner",
                    email=f"{SYN_CLERK_USER_ID}@idempotent.test",
                )
            tenant_ids.add(str(result.tenant_id))
        elapsed = time.time() - t0
        print(f"[serial] {N_CALLS} calls in {elapsed*1000:.0f}ms ({elapsed*1000/N_CALLS:.1f}ms/call)")

        org_count, tenant_count, user_count = await _count_invariant(session_factory)
        print(f"[serial] DB counts: org={org_count} tenant={tenant_count} user={user_count}")
        if not (org_count == tenant_count == user_count == 1):
            print(f"[FAIL] serial invariant violated: 1 expected each, got {org_count}/{tenant_count}/{user_count}")
            rc = 1
        if len(tenant_ids) != 1:
            print(f"[FAIL] serial returned multiple distinct tenant_ids: {tenant_ids}")
            rc = 1

        # ---- concurrent idempotency
        await _cleanup(session_factory)
        print(f"[concurrent] firing {N_CONCURRENT} provision calls in parallel")

        async def one_call():
            async with session_factory() as db:
                return await provision_aegis_identity(
                    db, redis,
                    clerk_user_id=SYN_CLERK_USER_ID,
                    raw_jwt_org_id=None,
                    org_role_claim="org:owner",
                    email=f"{SYN_CLERK_USER_ID}@idempotent.test",
                )

        results = await asyncio.gather(
            *[one_call() for _ in range(N_CONCURRENT)],
            return_exceptions=True,
        )
        ok = [r for r in results if not isinstance(r, Exception)]
        errs = [r for r in results if isinstance(r, Exception)]
        print(f"[concurrent] {len(ok)} ok / {len(errs)} errors")
        for e in errs[:3]:
            print(f"   error sample: {type(e).__name__}: {e}")
        if errs:
            rc = 1

        org_count, tenant_count, user_count = await _count_invariant(session_factory)
        print(f"[concurrent] DB counts: org={org_count} tenant={tenant_count} user={user_count}")
        if not (org_count == tenant_count == user_count == 1):
            print(f"[FAIL] concurrent invariant violated: 1 expected each, got {org_count}/{tenant_count}/{user_count}")
            rc = 1

        tenant_ids = {str(r.tenant_id) for r in ok}
        if len(tenant_ids) != 1:
            print(f"[FAIL] concurrent returned multiple distinct tenant_ids: {tenant_ids}")
            rc = 1

        if rc == 0:
            print("[PASS] /auth/clerk/provision is idempotent under both serial and concurrent load.")
    finally:
        await _cleanup(session_factory)
        await engine.dispose()
        await redis.aclose()

    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
