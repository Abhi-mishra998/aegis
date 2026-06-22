from __future__ import annotations

import asyncio
import structlog
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import delete, select

from sdk.common.config import settings
from sdk.common.db import engine, get_session_factory
from sdk.common.migrate import check_schema
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.identity.models import Organization, Tenant, User
from services.identity.router import router
from services.identity.tenant_reconciler import run_forever as run_tenant_reconciler
from services.identity.webhooks_clerk import router as clerk_webhooks_router

logger = structlog.get_logger(__name__)

_ORG_TO_TENANT_KEY_PREFIX = "acp:clerk:org-tenant:"
_DEMO_CLEANUP_INTERVAL_SECONDS = 3600  # hourly


# NOTE (2026-06-22): the previous boot-time `_backfill_clerk_org_tenant_redis`
# walked Organization+Tenant naively. For a Clerk user with two Organization
# rows (the abhi986 incident), the loop wrote both Redis entries and the LAST
# one won — non-deterministic, and in the incident the orphan row won. The
# replacement is `services/identity/tenant_reconciler.py`:
#   - runs once at boot (catches drift the moment we restart)
#   - runs hourly thereafter (catches drift in flight)
#   - DB is source of truth; Redis is repaired to match.
# Postgres is what /auth/clerk/provision writes inside one transaction;
# Redis is downstream.


async def _cleanup_expired_demo_tenants() -> int:
    """EH-2: hard-delete every demo tenant past its expires_at deadline.

    Idempotent + safe to run hourly. Deletes the User row first (FK to
    tenant), then the Tenant. Returns the number of tenants reaped.
    Background task spawned by lifespan; survival depends on process
    restart but we re-arm every boot.
    """
    now = datetime.now(UTC)
    reaped = 0
    try:
        async with get_session_factory()() as db:
            rows = (
                await db.execute(
                    select(Tenant.tenant_id)
                    .where(Tenant.is_demo.is_(True))
                    .where(Tenant.demo_expires_at.is_not(None))
                    .where(Tenant.demo_expires_at < now)
                    .limit(500)  # process at most 500/hour to keep DB load bounded
                )
            ).all()
            for (tenant_id,) in rows:
                await db.execute(delete(User).where(User.tenant_id == tenant_id))
                await db.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
                reaped += 1
            if reaped:
                await db.commit()
    except Exception as exc:  # noqa: BLE001 — never crash identity over cleanup
        logger.warning("demo_cleanup_failed", error=str(exc))
    if reaped:
        logger.info("demo_tenants_reaped", count=reaped)
    return reaped


async def _demo_cleanup_loop() -> None:
    """Run _cleanup_expired_demo_tenants() forever every hour. Cancellable."""
    while True:
        await _cleanup_expired_demo_tenants()
        try:
            await asyncio.sleep(_DEMO_CLEANUP_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return


async def _get_redis_for_reconciler():
    return get_redis_client(settings.REDIS_URL, decode_responses=True)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with get_session_factory()() as db:
        await check_schema(db, "identity")
    # EH-2: hourly demo-tenant reaper.
    cleanup_task = asyncio.create_task(_demo_cleanup_loop())
    # 2026-06-22: tenant reconciler — Redis ↔ DB drift detector + Redis-side
    # auto-repair. Runs once at boot (replacing the old _backfill_*) then
    # hourly forever.
    reconciler_task = asyncio.create_task(
        run_tenant_reconciler(get_session_factory, _get_redis_for_reconciler),
    )
    try:
        yield
    finally:
        for task in (cleanup_task, reconciler_task):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await engine.dispose()


app = FastAPI(
    title="ACP Identity Service",
    description="JWT authentication + Redis-backed token lifecycle management",
    version="1.0.0",
    lifespan=lifespan,
)

# Consolidated SDK Setup
setup_app(app, "identity")

app.include_router(router)
app.include_router(clerk_webhooks_router)
