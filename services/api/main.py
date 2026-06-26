from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI

from sdk.common.config import settings
from sdk.common.db import engine, get_session_factory
from sdk.common.migrate import check_schema
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.api.are_worker import process_incident
from services.api.repository.incident import IncidentRepository
from services.api.router.api_key import router as api_key_router
from services.api.router.auto_response import router as are_router
from services.api.router.incident import router as incident_router
from services.api.schemas.incident import IncidentCreate

logger = structlog.get_logger(__name__)

# arch-26 W2.5 2026-06-26 — incident consumer liveness metric. The customer
# experienced "no incidents shown" with no internal alert firing, because
# we had ZERO observability on the incidents queue. _INCIDENT_LAST_PROCESSED_AT
# is updated every time _process_one succeeds; _INCIDENT_QUEUE_DEPTH polls
# XLEN once per loop. /health/incident-consumer surfaces both.
import time as _time_for_metrics
try:
    from prometheus_client import Gauge as _Gauge
    _INCIDENT_QUEUE_DEPTH = _Gauge(
        "acp_incidents_queue_depth",
        "Current XLEN of acp:incidents:queue (alert if > 100 for 5m)",
    )
    _INCIDENT_LAST_PROCESSED_TS = _Gauge(
        "acp_incident_consumer_last_processed_ts_seconds",
        "Unix timestamp of the last successfully-processed incident event "
        "(alert if delta from now > 300s = consumer dead)",
    )
except Exception:  # prometheus unavailable in some test contexts
    _INCIDENT_QUEUE_DEPTH = None
    _INCIDENT_LAST_PROCESSED_TS = None

# In-process counter the /health endpoint reads even if Prometheus is off.
_INCIDENT_LAST_PROCESSED: dict[str, float] = {"ts": 0.0, "depth": 0}

_INCIDENT_STREAM   = "acp:incidents:queue"
_INCIDENT_GROUP    = "api-incident-worker"
_INCIDENT_CONSUMER = "api-worker-1"

_ARE_GROUP    = "are-workers"
_ARE_CONSUMER = "are-worker-1"

_AUDIT_STREAM   = "acp:audit:events"
_AUDIT_ARE_GROUP    = "are-audit-workers"
_AUDIT_ARE_CONSUMER = "are-audit-worker-1"

# Backpressure threshold — pause ARE when backlog exceeds this
_BACKPRESSURE_THRESHOLD = 10_000


async def _incident_consumer(redis, session_factory) -> None:
    """
    Fix 1: Durable Redis Stream consumer for incident creation.
    At-least-once delivery with dedup + retry. Replaces fire-and-forget HTTP.
    """
    # Create consumer group if it doesn't exist
    try:
        await redis.xgroup_create(_INCIDENT_STREAM, _INCIDENT_GROUP, id="0", mkstream=True)
    except Exception:
        pass  # group already exists

    # On startup, re-process any pending (unacknowledged) messages from a prior crash
    await _drain_pending(redis, session_factory)

    while True:
        try:
            # arch-26 W2.5 — refresh queue-depth gauge on every loop tick
            # (free piggyback on the existing xread cycle; no extra RTT).
            try:
                depth = int(await redis.xlen(_INCIDENT_STREAM) or 0)
                _INCIDENT_LAST_PROCESSED["depth"] = depth
                if _INCIDENT_QUEUE_DEPTH is not None:
                    _INCIDENT_QUEUE_DEPTH.set(depth)
            except Exception:
                pass

            msgs = await redis.xreadgroup(
                _INCIDENT_GROUP,
                _INCIDENT_CONSUMER,
                {_INCIDENT_STREAM: ">"},
                count=10,
                block=2000,  # must be < socket_timeout(5s) to avoid spurious TimeoutError
            )
            for _stream, entries in (msgs or []):
                for msg_id, fields in entries:
                    await _process_one(redis, session_factory, msg_id, fields)
        except asyncio.CancelledError:
            break
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as exc:
            logger.warning("incident_consumer_redis_unavailable", error=str(exc))
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("incident_consumer_error", error=str(exc))
            await asyncio.sleep(2)


async def _drain_pending(redis, session_factory) -> None:
    """Re-process messages that were fetched but never acknowledged (crash recovery)."""
    try:
        pending = await redis.xpending_range(
            _INCIDENT_STREAM, _INCIDENT_GROUP, "-", "+", count=100
        )
        if not pending:
            return
        ids = [p["message_id"] for p in pending]
        msgs = await redis.xrange(_INCIDENT_STREAM, min=ids[0], max=ids[-1])
        for msg_id, fields in msgs:
            if any(str(p["message_id"]) == str(msg_id) for p in pending):
                await _process_one(redis, session_factory, msg_id, fields)
    except Exception as exc:
        logger.warning("incident_drain_pending_failed", error=str(exc))


async def _process_one(redis, session_factory, msg_id, fields: dict) -> None:
    """Process one stream message: dedup → create/bump → ack."""
    try:
        raw   = fields.get(b"data") or fields.get("data") or b"{}"
        data  = json.loads(raw if isinstance(raw, str) else raw.decode())

        # Fix 2: Deduplication — same agent + tool + trigger within 5-minute window
        time_bucket = int(time.time() // 300)
        dedup_raw   = f"{data.get('tenant_id')}:{data.get('agent_id')}:{data.get('tool', '')}:{data.get('trigger')}:{time_bucket}"
        dedup_hash  = hashlib.sha256(dedup_raw.encode()).hexdigest()[:16]
        dedup_key   = f"acp:incident:dedup:{dedup_hash}"

        existing_id_bytes = await redis.get(dedup_key)

        async with session_factory()() as db:
            repo = IncidentRepository(db)

            if existing_id_bytes:
                # Duplicate — bump violation counter on the existing open incident.
                # arch-26 W2.6 — pass tenant_id so the repo enforces the filter
                # defensively even though the dedup_key is tenant-scoped.
                existing_id = existing_id_bytes.decode() if isinstance(existing_id_bytes, bytes) else existing_id_bytes
                try:
                    _tenant_id_for_bump = uuid.UUID(data["tenant_id"]) if data.get("tenant_id") else None
                    await repo.bump_violation(uuid.UUID(existing_id), tenant_id=_tenant_id_for_bump)
                    logger.info("incident_dedup_bump", dedup_hash=dedup_hash, incident_id=existing_id)
                except Exception as exc:
                    logger.warning("incident_dedup_bump_failed", error=str(exc))
            else:
                # New incident
                try:
                    payload  = IncidentCreate(**data)
                    incident = await repo.create(payload, dedup_key=dedup_hash)
                    # Set dedup key with 5-min TTL so duplicates in the same window are caught
                    await redis.setex(dedup_key, 300, str(incident.id))
                    logger.info("incident_created_from_queue",
                        number=incident.incident_number,
                        severity=incident.severity,
                        agent_id=incident.agent_id,
                    )
                except Exception as exc:
                    logger.error("incident_create_failed", error=str(exc), data=data)
                    # Don't ack — let it be retried
                    return

        # Acknowledge only on success
        await redis.xack(_INCIDENT_STREAM, _INCIDENT_GROUP, msg_id)
        # arch-26 W2.5 — liveness signal
        _now = _time_for_metrics.time()
        _INCIDENT_LAST_PROCESSED["ts"] = _now
        if _INCIDENT_LAST_PROCESSED_TS is not None:
            _INCIDENT_LAST_PROCESSED_TS.set(_now)

    except Exception as exc:
        logger.error("incident_event_processing_failed", error=str(exc), msg_id=str(msg_id))
        # Don't ack — Redis will redeliver after PEL timeout


# ─── ARE consumer (separate group — evaluates every incident for auto-mitigation) ──

async def _are_consumer(redis, session_factory) -> None:
    """ARE evaluation worker — uses its own consumer group on the incidents stream."""
    try:
        await redis.xgroup_create(_INCIDENT_STREAM, _ARE_GROUP, id="0", mkstream=True)
    except Exception:
        pass  # group already exists

    while True:
        try:
            # Backpressure: pause if queue is too deep
            try:
                backlog = int(await redis.xlen(_INCIDENT_STREAM) or 0)
                if backlog > _BACKPRESSURE_THRESHOLD:
                    logger.warning("are_backpressure", backlog=backlog)
                    await asyncio.sleep(5)
                    continue
            except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError):
                await asyncio.sleep(1)
                continue

            msgs = await redis.xreadgroup(
                _ARE_GROUP, _ARE_CONSUMER,
                {_INCIDENT_STREAM: ">"},
                count=10, block=2000,  # must be < socket_timeout(5s)
            )
            for _stream, entries in (msgs or []):
                for msg_id, fields in entries:
                    await _are_process_one(redis, session_factory, msg_id, fields)
        except asyncio.CancelledError:
            break
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as exc:
            logger.warning("are_consumer_redis_unavailable", error=str(exc))
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("are_consumer_error", error=str(exc))
            await asyncio.sleep(2)


async def _are_process_one(redis, session_factory, msg_id, fields: dict) -> None:
    try:
        raw  = fields.get(b"data") or fields.get("data") or b"{}"
        data = json.loads(raw if isinstance(raw, str) else raw.decode())
        await process_incident(redis, session_factory, data)
        await redis.xack(_INCIDENT_STREAM, _ARE_GROUP, msg_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("are_process_failed", error=str(exc), msg_id=str(msg_id))


async def _audit_are_consumer(redis, session_factory) -> None:
    """
    Second ARE ingestion source: consume deny/high-risk events from the audit stream.
    Runs alongside the incidents stream consumer — same process_incident(), different source.
    """
    try:
        await redis.xgroup_create(_AUDIT_STREAM, _AUDIT_ARE_GROUP, id="$", mkstream=True)
    except Exception:
        pass  # group already exists

    while True:
        try:
            try:
                backlog = int(await redis.xlen(_AUDIT_STREAM) or 0)
                if backlog > _BACKPRESSURE_THRESHOLD:
                    await asyncio.sleep(5)
                    continue
            except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError):
                await asyncio.sleep(1)
                continue

            msgs = await redis.xreadgroup(
                _AUDIT_ARE_GROUP, _AUDIT_ARE_CONSUMER,
                {_AUDIT_STREAM: ">"},
                count=10, block=2000,  # must be < socket_timeout(5s)
            )
            for _stream, entries in (msgs or []):
                for msg_id, fields in entries:
                    try:
                        raw  = fields.get(b"data") or fields.get("data") or b"{}"
                        data = json.loads(raw if isinstance(raw, str) else raw.decode())
                        await process_incident(redis, session_factory, data)
                        await redis.xack(_AUDIT_STREAM, _AUDIT_ARE_GROUP, msg_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.error("audit_are_process_failed", error=str(exc))
        except asyncio.CancelledError:
            break
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as exc:
            logger.warning("audit_are_consumer_redis_unavailable", error=str(exc))
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("audit_are_consumer_error", error=str(exc))
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Schema migration check
    async with get_session_factory()() as db:
        await check_schema(db, "api")

    # Redis for stream consumers and action effects
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    _app.state.incident_redis = redis

    # Start durable incident consumer
    consumer_task = asyncio.create_task(
        _incident_consumer(redis, get_session_factory)
    )
    # Start ARE evaluation worker (separate consumer group on incidents stream)
    are_task = asyncio.create_task(
        _are_consumer(redis, get_session_factory)
    )
    # Start ARE audit-events consumer (event-driven: audit deny/high-risk → ARE)
    audit_are_task = asyncio.create_task(
        _audit_are_consumer(redis, get_session_factory)
    )

    yield

    consumer_task.cancel()
    are_task.cancel()
    audit_are_task.cancel()
    for t in (consumer_task, are_task, audit_are_task):
        with suppress(asyncio.CancelledError):
            await t
    await redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="ACP API Management Service",
    description="SaaS layer for API keys, security incident management, and durable event processing",
    version="3.0.0",
    lifespan=lifespan,
)

setup_app(app, "api-management")

app.include_router(api_key_router, prefix="/api-keys", tags=["API Keys"])
app.include_router(incident_router,                    tags=["Incidents"])
app.include_router(are_router,                         tags=["ARE"])


# arch-26 W2.5 2026-06-26 — incident consumer health probe. Ops + alertmanager
# can poll this every 30s and page if status != "ok". Customer reported zero
# incidents shown despite /execute deny events firing; root cause was a dead
# consumer with no observability. Now any deviation surfaces here.
@app.get("/health/incident-consumer", tags=["health"], include_in_schema=False)
async def health_incident_consumer() -> dict:
    now = _time_for_metrics.time()
    last = _INCIDENT_LAST_PROCESSED.get("ts", 0.0) or 0.0
    depth = int(_INCIDENT_LAST_PROCESSED.get("depth", 0) or 0)
    lag_s = round(now - last, 1) if last > 0 else None

    # Heuristic thresholds — keep aligned with alertmanager rule.
    if last == 0.0 and depth == 0:
        status = "warming"   # process just started, no events yet
    elif lag_s is not None and lag_s > 300 and depth > 0:
        status = "stuck"     # backlog + no recent processing → consumer dead
    elif depth > 1000:
        status = "backpressure"
    else:
        status = "ok"

    return {
        "status": status,
        "queue_depth": depth,
        "last_processed_at_ts": last,
        "lag_seconds": lag_s,
        "stream": _INCIDENT_STREAM,
        "group": _INCIDENT_GROUP,
    }


# ── Internal throttle endpoint — called by autonomy-service playbook executor ──
@app.post("/internal/throttle", tags=["internal"], include_in_schema=False)
async def internal_set_throttle(payload: dict) -> dict:
    """Write a per-agent throttle key to Redis.

    Called by the autonomy-service playbook THROTTLE action so it can apply
    rate limits without needing direct Redis access.

    Body: { agent_id, tenant_id, rate }  (rate = "<n>/<unit>", e.g. "5/m")
    """

    from sdk.common.config import settings as _cfg

    agent_id  = payload.get("agent_id", "")
    tenant_id = payload.get("tenant_id", "")
    rate      = payload.get("rate", "5/m")

    if not agent_id or not tenant_id:
        return {"status": "error", "reason": "agent_id and tenant_id are required"}

    try:
        from sdk.common.redis import get_redis_client
        r = get_redis_client(_cfg.REDIS_URL, decode_responses=True)
        await r.setex(f"acp:{tenant_id}:throttle:{agent_id}", 3600, rate)
        await r.aclose()
        logger.info("internal_throttle_set", agent=agent_id[:8], rate=rate)
        return {"status": "throttled", "agent_id": agent_id, "rate": rate}
    except Exception as exc:
        logger.error("internal_throttle_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}
