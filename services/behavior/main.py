from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import Depends, FastAPI

from sdk.common.auth import verify_internal_secret
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.behavior.service import behavior_engine

logger = structlog.get_logger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # behavior_engine uses a default redis client if not provided
    yield

app = FastAPI(
    title="ACP Behavior Service",
    description="Real-time behavioral intelligence and sequence analysis",
    version="1.0.0",
    lifespan=lifespan,
)

setup_app(app, "behavior")

@app.post("/analyze")
async def analyze_behavior(payload: dict, _: str = Depends(verify_internal_secret)):
    """
    Standalone endpoint for behavior analysis.
    In distributed mode, Gateway calls this via HTTP.
    """
    try:
        tenant_id = uuid.UUID(payload.get("tenant_id") or "")
        agent_id = uuid.UUID(payload.get("agent_id") or "")
    except (ValueError, AttributeError) as exc:
        return {"success": False, "error": f"invalid uuid: {exc}"}
    tool = payload.get("tool")
    tokens = payload.get("tokens", 0)

    result = await behavior_engine.record_action(
        tenant_id=tenant_id,
        agent_id=agent_id,
        tool=tool,
        tokens=tokens
    )
    return {"success": True, "data": result}

@app.post("/check")
async def check_behavior(payload: dict, _: str = Depends(verify_internal_secret)):
    """
    Check behavioral sequence for anomalies without recording (pre-flight).
    """
    try:
        agent_uuid = uuid.UUID(payload.get("agent_id") or "")
        tenant_uuid = uuid.UUID(payload.get("tenant_id") or "")
    except (ValueError, AttributeError):
        agent_uuid = None
        tenant_uuid = None
    result = await behavior_engine.check_behavior(
        agent_id=agent_uuid,
        tool_name=payload.get("tool_name"),
        payload_hash=payload.get("payload_hash"),
        payload_text=payload.get("payload_text"),
        tenant_id=tenant_uuid
    )
    return {"success": True, "data": result}


# ── DEEP READINESS PROBE ───────────────────────────────────────────────────
# The default /health from setup_app() returns 200 unconditionally even when
# Redis is down or the engine cannot score. /system/health on the gateway
# therefore reported "behavior=healthy" while every decision-side call was
# falling back to fail-closed risk=0.5. /readiness probes the *actual scoring
# path's dependencies* (Redis ping with a tight budget) so the gateway can
# see truthful liveness.
@app.get("/readiness", tags=["ops"])
async def readiness() -> dict[str, object]:
    started = time.monotonic()
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    try:
        # 250ms cap — far shorter than any consumer's timeout, so this probe
        # never becomes the source of cascading false-negatives.
        import asyncio as _asyncio
        await _asyncio.wait_for(redis.ping(), timeout=0.25)
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "status": "ready",
            "service": "behavior",
            "redis": "ok",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "status": "not_ready",
            "service": "behavior",
            "redis": "fail",
            "error": type(exc).__name__,
            "detail": str(exc)[:120],
        }
    finally:
        with suppress(Exception):
            await redis.aclose()
