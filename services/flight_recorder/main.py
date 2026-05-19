"""ACP Flight Recorder Service — replayable AI execution timelines."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI

from sdk.common.config import settings
from sdk.common.db import engine, get_session_factory
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.flight_recorder.router import router
from services.flight_recorder.worker import _consumer

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    session_factory = get_session_factory()
    consumer_task = asyncio.create_task(_consumer(redis, session_factory), name="flight_consumer")
    logger.info("flight_recorder_started")
    yield
    consumer_task.cancel()
    with suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(consumer_task, timeout=5.0)
    await redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="ACP Flight Recorder Service",
    description="Replayable runtime execution timelines for autonomous AI agents",
    version="1.0.0",
    lifespan=lifespan,
)
setup_app(app, "flight_recorder")
app.include_router(router)
