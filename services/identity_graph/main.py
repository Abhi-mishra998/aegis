"""ACP Identity Graph Service — FastAPI app + background workers."""
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
from services.identity_graph.router import router
from services.identity_graph.worker import (
    _drift_loop,
    _graph_event_consumer,
    _trust_score_loop,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    session_factory = get_session_factory()

    workers = [
        asyncio.create_task(_graph_event_consumer(redis, session_factory), name="graph_consumer"),
        asyncio.create_task(_trust_score_loop(session_factory),            name="trust_scorer"),
        asyncio.create_task(_drift_loop(session_factory),                  name="drift_detector"),
    ]
    logger.info("identity_graph_started", workers=len(workers))
    yield

    for w in workers:
        w.cancel()
    for w in workers:
        with suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(w, timeout=5.0)
    await redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="ACP Identity Graph Service",
    description="Runtime agent identity graph, trust scoring, drift detection, compromise simulation",
    version="1.0.0",
    lifespan=lifespan,
)

setup_app(app, "identity_graph")
app.include_router(router)
