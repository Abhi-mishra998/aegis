"""
Tenant Reconciliation Worker
============================

Hourly background job that verifies the four sources of tenant ownership
agree with each other:

    Postgres: users.tenant_id  ⇄  tenants.tenant_id  ⇄  organizations.id
    Redis   : acp:clerk:org-tenant:<clerk_org_id> → tenants.tenant_id

Postgres is the source of truth. When Redis disagrees with Postgres, the
reconciler refreshes Redis (the cheap, reversible side). Postgres-side
inconsistencies (orphan Tenant rows, dangling users.tenant_id, etc.) are
LOGGED + AUDITED but NOT auto-repaired — those require operator review.

Why this exists:
    The /auth/clerk/provision path now writes Redis on every successful
    call, so the happy path is self-healing. The reconciler covers the
    failure modes:
      - Redis was lost (maintenance, eviction, manual flush)
      - A bad provision predates the transactional rewrite (abhi986)
      - Two webhooks raced and the loser wrote stale data

Trigger:
    Spawned by services/identity/main.py:lifespan() as a background task.
    Runs once at startup (to catch boot-time drift), then every
    ``RECONCILER_INTERVAL_SECONDS`` (default 3600).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.identity.models import Organization, Tenant, User

logger = structlog.get_logger(__name__)

_ORG_TO_TENANT_KEY_PREFIX = "acp:clerk:org-tenant:"
_ORG_TO_TENANT_TTL_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_INTERVAL_SECONDS = 3600

# Optional cap so the first pass on a giant org list does not run forever.
_MAX_ROWS_PER_PASS = int(os.environ.get("TENANT_RECONCILER_MAX_ROWS", "20000"))


def _interval_seconds() -> int:
    raw = os.environ.get("TENANT_RECONCILER_INTERVAL_SECONDS")
    if not raw:
        return _DEFAULT_INTERVAL_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return _DEFAULT_INTERVAL_SECONDS


async def _reconcile_one_org(
    redis: Redis, *,
    clerk_org_id: str,
    canonical_tenant_id: str,
) -> dict[str, Any]:
    """Compare Redis to DB for a single org. Repair Redis on mismatch."""
    key = f"{_ORG_TO_TENANT_KEY_PREFIX}{clerk_org_id}"
    try:
        raw = await redis.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reconcile_redis_read_failed",
            clerk_org_id=clerk_org_id, error=str(exc),
        )
        return {"clerk_org_id": clerk_org_id, "outcome": "redis_read_error"}

    cached = raw.decode("utf-8") if isinstance(raw, bytes) else (raw or "")
    cached = cached.strip()

    if cached == canonical_tenant_id:
        return {"clerk_org_id": clerk_org_id, "outcome": "ok"}

    # Drift detected. Repair Redis (cache loses to DB).
    try:
        await redis.setex(key, _ORG_TO_TENANT_TTL_SECONDS, canonical_tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "reconcile_redis_repair_failed",
            clerk_org_id=clerk_org_id, error=str(exc),
        )
        return {"clerk_org_id": clerk_org_id, "outcome": "repair_failed"}

    logger.warning(
        "reconcile_redis_drift_repaired",
        clerk_org_id=clerk_org_id,
        redis_was=cached or None,
        db_truth=canonical_tenant_id,
    )
    return {
        "clerk_org_id": clerk_org_id,
        "outcome": "repaired" if cached else "filled_missing",
        "redis_was": cached or None,
        "db_truth": canonical_tenant_id,
    }


async def reconcile_all(
    db: AsyncSession, redis: Redis,
) -> dict[str, Any]:
    """One full reconciliation pass. Returns a summary dict."""
    started_at = datetime.now(tz=timezone.utc)

    # Pull (clerk_org_id, tenant_id) tuples from the canonical join.
    rows = (await db.execute(
        select(Organization.clerk_org_id, Tenant.tenant_id)
        .join(Tenant, Tenant.org_id == Organization.id)
        .where(Organization.clerk_org_id.is_not(None))
        .where(Organization.is_active.is_(True))
        .where(Tenant.is_active.is_(True))
        .limit(_MAX_ROWS_PER_PASS)
    )).all()

    counts = {"ok": 0, "repaired": 0, "filled_missing": 0,
              "repair_failed": 0, "redis_read_error": 0}
    drift_examples: list[dict[str, Any]] = []

    for clerk_org_id, tenant_id in rows:
        if not clerk_org_id or not tenant_id:
            continue
        outcome = await _reconcile_one_org(
            redis,
            clerk_org_id=str(clerk_org_id),
            canonical_tenant_id=str(tenant_id),
        )
        counts[outcome["outcome"]] = counts.get(outcome["outcome"], 0) + 1
        if outcome["outcome"] in ("repaired", "repair_failed") and len(drift_examples) < 10:
            drift_examples.append(outcome)

    # Cross-check: every User.tenant_id should match SOME Tenant.tenant_id.
    orphan_users = (await db.execute(
        select(User.id, User.clerk_user_id, User.tenant_id)
        .outerjoin(Tenant, User.tenant_id == Tenant.tenant_id)
        .where(Tenant.tenant_id.is_(None))
        .where(User.is_active.is_(True))
        .limit(50)
    )).all()
    if orphan_users:
        logger.error(
            "reconcile_user_tenant_orphan",
            count=len(orphan_users),
            sample=[{"user_id": str(u[0]),
                     "clerk_user_id": u[1],
                     "tenant_id": str(u[2])} for u in orphan_users[:5]],
        )

    finished_at = datetime.now(tz=timezone.utc)
    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "rows_checked": len(rows),
        "counts": counts,
        "drift_examples": drift_examples,
        "orphan_users_count": len(orphan_users),
    }
    logger.info("tenant_reconciler_pass_done", **summary)
    return summary


async def run_forever(get_db_factory, get_redis):
    """Loop that runs `reconcile_all` once on boot, then on a schedule.

    Args:
        get_db_factory: zero-arg callable returning an AsyncSession factory.
        get_redis: zero-arg coroutine that returns a configured Redis client.
    """
    interval = _interval_seconds()
    logger.info("tenant_reconciler_started", interval_seconds=interval)
    # First pass runs immediately so boot-time drift gets fixed even if the
    # process dies before the first scheduled tick.
    while True:
        try:
            redis = await get_redis()
            async with get_db_factory()() as db:
                await reconcile_all(db, redis)
        except asyncio.CancelledError:
            logger.info("tenant_reconciler_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("tenant_reconciler_pass_failed", error=str(exc))
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
