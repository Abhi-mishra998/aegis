"""ACP Autonomy Service — Bounded autonomy contracts + human override timeline."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI

from sdk.common.db import engine, get_session_factory
from sdk.utils import setup_app
from services.autonomy.incident_watcher import run_incident_watcher
from services.autonomy.metrics import gauge_refresh_loop
from services.autonomy.router import router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("autonomy_service_started")
    watcher_task = asyncio.create_task(
        run_incident_watcher(get_session_factory())
    )
    # Feeds acp_autonomy_pending_approvals gauge — consumed by Grafana
    # customer-slo dashboard panel 3 (infra/grafana-dashboards/customer-slo.json).
    gauge_task = asyncio.create_task(
        gauge_refresh_loop(get_session_factory())
    )
    yield
    watcher_task.cancel()
    gauge_task.cancel()
    with suppress(asyncio.CancelledError):
        await watcher_task
    with suppress(asyncio.CancelledError):
        await gauge_task
    await engine.dispose()


app = FastAPI(
    title="ACP Autonomy Service",
    description="Bounded autonomy contracts + human override timeline",
    version="1.0.0",
    lifespan=lifespan,
)
setup_app(app, "autonomy")
app.include_router(router)
