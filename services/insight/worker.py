"""
Insight Worker
==============
Async post-decision LLM enrichment — NOT in the hot request path.
Consumes acp:groq_queue Redis stream, calls Groq API, stores enriched
insights in:
  acp:groq:insight:{event_id}     (hash, 24-hour TTL)
  acp:groq:insights:timeline      (sorted set, score=unix_ts, for /insights?limit=N)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid

import structlog
from groq import AsyncGroq
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sdk.common.config import settings
from sdk.common.groq_helpers import (
    build_signals_block as _build_signals_block,
)
from sdk.common.groq_helpers import (
    pick_model as _pick_model,
)
from sdk.common.redis import get_redis_client

logging.basicConfig(level=logging.INFO)
logger = structlog.get_logger(__name__)

if not settings.GROQ_API_KEY or "change_me" in settings.GROQ_API_KEY:
    logger.error("GROQ_API_KEY is not configured or is still a placeholder. Insight worker cannot start.")
    sys.exit(1)

redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

_STREAM_KEY = "acp:groq_queue"
_DLQ_KEY = "acp:groq:dlq"
_CONSUMER_GROUP = "acp:insight:consumers"
_CONSUMER_NAME = "insight-worker-primary"
_TIMELINE_KEY = "acp:groq:insights:timeline"
_INSIGHT_TTL = 86400          # 24 hours — security insights need to outlast the shift
_TIMELINE_MAX = 500           # keep at most 500 entries in the sorted set

import contextlib

from sdk.common.groq_helpers import (  # noqa: E402
    THREAT_INTEL_SYSTEM_PROMPT as _SYSTEM_PROMPT,
)
from sdk.common.groq_helpers import THREAT_INTEL_USER_TEMPLATE as _USER_TEMPLATE


class InsightResponse(BaseModel):
    root_cause: str
    threat_classification: str = Field(
        description="OWASP-aligned or generic threat classification"
    )
    recommendation: str = Field(
        description="Must be one of: HIGHLIGHT, MONITOR, THROTTLE, ESCALATE, TERMINATE"
    )
    confidence: str = Field(description="Must be one of: HIGH, MEDIUM, LOW")
    narrative: str = Field(description="2-3 sentence executive summary")


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=15),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def generate_insight(event: dict) -> dict:
    risk_score = float(event.get("risk_score", 0.0))
    model = _pick_model(risk_score)

    user_msg = _USER_TEMPLATE.format(
        agent_id=event.get("agent_id", "unknown"),
        tool=event.get("tool", "unknown"),
        decision=event.get("decision", "unknown"),
        risk_score=risk_score,
        signals_block=_build_signals_block(event),
    )

    completion = await groq_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=400,
    )

    raw = completion.choices[0].message.content
    data = json.loads(raw)
    validated = InsightResponse(**data)
    result = validated.model_dump()
    result["groq_model"] = model
    return result


async def ensure_consumer_group() -> None:
    for attempt in range(3):
        try:
            await redis.xgroup_create(_STREAM_KEY, _CONSUMER_GROUP, id="0", mkstream=True)
            logger.info("consumer_group_ready", group=_CONSUMER_GROUP)
            return
        except Exception as e:
            if "BUSYGROUP" in str(e):
                return
            if attempt < 2:
                logger.warning("consumer_group_retry", error=str(e), attempt=attempt + 1)
                await asyncio.sleep(2 ** attempt)
            else:
                logger.warning(
                    "consumer_group_deferred",
                    error=str(e),
                    note="worker loop will retry when Redis recovers",
                )


async def store_insight(event_id: str, insight: dict) -> None:
    """Persist insight and add to the time-ordered sorted set for recent queries."""
    cache_key = f"acp:groq:insight:{event_id}"
    payload = json.dumps({**insight, "event_id": event_id})
    await redis.setex(cache_key, _INSIGHT_TTL, payload)

    # Maintain a sorted set so GET /insights returns genuinely recent items
    ts = time.time()
    await redis.zadd(_TIMELINE_KEY, {event_id: ts})
    # Trim to keep only the latest _TIMELINE_MAX entries
    await redis.zremrangebyrank(_TIMELINE_KEY, 0, -(_TIMELINE_MAX + 1))

    logger.info(
        "insight_stored",
        event_id=event_id,
        model=insight.get("groq_model"),
        classification=insight.get("threat_classification"),
        confidence=insight.get("confidence"),
    )


async def process_groq_queue() -> None:
    await ensure_consumer_group()
    logger.info("insight_worker_started", queue=_STREAM_KEY)

    while True:
        try:
            messages = await redis.xreadgroup(
                _CONSUMER_GROUP, _CONSUMER_NAME, {_STREAM_KEY: ">"}, count=10, block=2000
            )

            for _, msgs in messages:
                for msg_id, fields in msgs:
                    event = {
                        (k.decode() if isinstance(k, bytes) else k):
                        (v.decode() if isinstance(v, bytes) else v)
                        for k, v in fields.items()
                    }

                    # Events from middleware wrap payload under a "data" key
                    if "data" in event and isinstance(event["data"], str):
                        with contextlib.suppress(Exception):
                            event = {**event, **json.loads(event["data"])}

                    event_id = event.get("event_id") or str(uuid.uuid4())

                    # Validate tenant/agent UUIDs to reject malformed entries
                    try:
                        uuid.UUID(event.get("tenant_id", ""))
                        uuid.UUID(event.get("agent_id", ""))
                    except ValueError:
                        logger.warning("invalid_identity_fields", event_id=event_id)
                        await redis.xack(_STREAM_KEY, _CONSUMER_GROUP, msg_id)
                        continue

                    # Idempotency claim: atomic SET NX wins the right to call
                    # the paid Groq API. Releases on failure so a redelivery
                    # can retry; overwritten by store_insight on success.
                    cache_key = f"acp:groq:insight:{event_id}"
                    claimed = await redis.set(
                        cache_key, b"pending", nx=True, ex=_INSIGHT_TTL,
                    )
                    if not claimed:
                        logger.info("insight_skipped_idempotent", event_id=event_id)
                        await redis.xack(_STREAM_KEY, _CONSUMER_GROUP, msg_id)
                        continue

                    try:
                        insight = await generate_insight(event)
                        await store_insight(event_id, insight)

                        # Notify frontend via SSE pub/sub
                        tenant_id = event.get("tenant_id", "")
                        if tenant_id:
                            with contextlib.suppress(Exception):
                                await redis.publish(
                                    f"acp:events:{tenant_id}",
                                    json.dumps({
                                        "type": "insight_generated",
                                        "data": {
                                            "event_id": event_id,
                                            "agent_id": event.get("agent_id", ""),
                                            "tool": event.get("tool", ""),
                                            "threat_classification": insight.get("threat_classification"),
                                            "recommendation": insight.get("recommendation"),
                                            "confidence": insight.get("confidence"),
                                            "narrative": (insight.get("narrative") or "")[:300],
                                            "groq_model": insight.get("groq_model"),
                                            "ts": int(time.time()),
                                        },
                                    }),
                                )

                        await redis.xack(_STREAM_KEY, _CONSUMER_GROUP, msg_id)
                    except Exception as err:
                        # Release the idempotency claim so a redelivery can retry.
                        with contextlib.suppress(Exception):
                            await redis.delete(cache_key)
                        logger.error("insight_generation_failed", event_id=event_id, error=str(err))
                        await redis.xadd(
                            _DLQ_KEY,
                            {"event_data": json.dumps(event), "error": str(err)},
                            maxlen=1000,
                        )
                        await redis.xack(_STREAM_KEY, _CONSUMER_GROUP, msg_id)

            # Heartbeat — refresh the key the docker healthcheck reads.
            # A stuck loop never reaches here; healthcheck flips unhealthy
            # and docker compose restarts the worker.
            with contextlib.suppress(Exception):
                await redis.setex(b"acp:worker:heartbeat:insight_worker", 90, str(int(time.time())))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("worker_loop_error", error=str(e))
            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(process_groq_queue())
    except KeyboardInterrupt:
        logger.info("insight_worker_shutting_down")
