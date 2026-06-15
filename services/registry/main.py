from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from sdk.common.config import settings
from sdk.common.db import engine, get_session_factory
from sdk.common.migrate import check_schema
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.registry.router import router
from services.registry.service import set_registry_redis
from services.registry.wizard import router as wizard_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    async with get_session_factory()() as db:
        await check_schema(db, "registry")

    redis = get_redis_client(settings.REDIS_URL)
    set_registry_redis(redis)

    _app.state.client = httpx.AsyncClient(timeout=10.0)
    yield
    await _app.state.client.aclose()
    await redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="ACP Registry Service",
    description="Source of truth for Agent metadata and tool permissions",
    version="1.0.0",
    lifespan=lifespan,
)

# Consolidated SDK Setup
setup_app(app, "registry")

# Wizard mounted BEFORE the agent CRUD router so /agents/wizard wins the
# match against /agents/{agent_id} (FastAPI matches in declaration order).
app.include_router(wizard_router)
app.include_router(router)
