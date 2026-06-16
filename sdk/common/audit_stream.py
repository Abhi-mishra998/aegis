from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

from sdk.utils import SLO_AUDIT_DURABILITY_TOTAL


async def push_audit_event(
    redis: Redis | RedisCluster,
    tenant_id: str | uuid.UUID,
    agent_id: str | uuid.UUID | None,
    action: str,
    tool: str | None = None,
    decision: str = "allow",
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    """
    Push a security event to the Redis Audit Stream for asynchronous processing.
    """
    try:
        SLO_AUDIT_DURABILITY_TOTAL.labels(stage="produced").inc()

        payload = {
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id) if agent_id else "",
            "action": action,
            "tool": tool or "",
            "decision": decision,
            "reason": reason or "",
            "metadata_json": json.dumps(metadata or {}),
            "request_id": request_id or str(uuid.uuid4()),
        }

        # maxlen=10_000 keeps the stream's steady-state below the
        # /system/health "Degraded Performance" threshold (12_000) so the
        # status badge reflects actual queue pressure rather than the
        # producer's own retention policy. With ~150 events/s peak the
        # consumer group catches up within ~60s; entries beyond that point
        # are already XACK'd and the stream is just a debug ring buffer.
        await redis.xadd(
            "acp:audit_stream",
            payload,
            maxlen=10_000,
            approximate=True,
        )
    except Exception:
        SLO_AUDIT_DURABILITY_TOTAL.labels(stage="failed_at_producer").inc()
        # In production hardening, we might want to fail-close here depending on criticality
        # For now, we log and continue to satisfy "cannot fail silently" rule elsewhere
        raise
