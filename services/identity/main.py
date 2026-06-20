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
from services.identity.webhooks_clerk import router as clerk_webhooks_router

logger = structlog.get_logger(__name__)

_ORG_TO_TENANT_KEY_PREFIX = "acp:clerk:org-tenant:"
_DEMO_CLEANUP_INTERVAL_SECONDS = 3600  # hourly


async def _backfill_clerk_org_tenant_redis() -> None:
    """Self-heal: ensure Redis carries the clerk_org_id -> tenant_id mapping
    for every Clerk-linked org. The webhook normally writes this on org
    create; a fresh deploy (new Redis, existing DB) starts empty and every
    user session would hit `Workspace not found` until the mapping shows up.
    Idempotent: SET overwrites existing values with the same target.
    """
    try:
        redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 - identity must still boot
        logger.warning("clerk_org_tenant_backfill_no_redis", error=str(exc))
        return

    try:
        async with get_session_factory()() as db:
            rows = (
                await db.execute(
                    select(Organization.clerk_org_id, Tenant.tenant_id)
                    .join(Tenant, Tenant.org_id == Organization.id)
                    .where(Organization.clerk_org_id.is_not(None))
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 - skip on transient DB error
        logger.warning("clerk_org_tenant_backfill_db_failed", error=str(exc))
        return

    written = 0
    for clerk_org_id, tenant_id in rows:
        if not clerk_org_id or not tenant_id:
            continue
        try:
            await redis.set(f"{_ORG_TO_TENANT_KEY_PREFIX}{clerk_org_id}", str(tenant_id))
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("clerk_org_tenant_backfill_write_failed", error=str(exc))
    logger.info("clerk_org_tenant_backfill_done", written=written, total=len(rows))


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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with get_session_factory()() as db:
        await check_schema(db, "identity")
    await _backfill_clerk_org_tenant_redis()
    # EH-2: start the hourly demo-tenant reaper as a background task.
    cleanup_task = asyncio.create_task(_demo_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
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
