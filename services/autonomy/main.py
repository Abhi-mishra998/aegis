"""ACP Autonomy Service — Bounded autonomy contracts + human override timeline."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from sdk.common.db import engine
from sdk.utils import setup_app
from services.autonomy.router import router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("autonomy_service_started")
    yield
    await engine.dispose()


app = FastAPI(
    title="ACP Autonomy Service",
    description="Bounded autonomy contracts + human override timeline",
    version="1.0.0",
    lifespan=lifespan,
)
setup_app(app, "autonomy")
app.include_router(router)
