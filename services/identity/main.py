from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI
from sqlalchemy import delete, select

from sdk.common.db import engine, get_session_factory
from sdk.common.migrate import check_schema
from sdk.utils import setup_app
from services.identity.models import Tenant, User
from services.identity.router import router
from services.identity.scim_router import router as scim_router

logger = structlog.get_logger(__name__)

_DEMO_CLEANUP_INTERVAL_SECONDS = 3600  # hourly


async def _cleanup_expired_demo_tenants() -> int:
    """EH-2: hard-delete every demo tenant past its expires_at deadline.

    Idempotent + safe to run hourly. Deletes the User row first (FK to
    tenant), then the Tenant. Returns the number of tenants reaped.
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
                    .limit(500)
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

setup_app(app, "identity")

app.include_router(router)
app.include_router(scim_router)
