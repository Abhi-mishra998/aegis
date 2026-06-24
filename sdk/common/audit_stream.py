from __future__ import annotations

import json
import time
import traceback
import uuid
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

from sdk.utils import AUDIT_PRODUCER_DLQ_TOTAL, SLO_AUDIT_DURABILITY_TOTAL

logger = structlog.get_logger(__name__)


_STREAM_KEY = "acp:audit_stream"
_PRODUCER_DLQ_KEY = "acp:audit_stream:producer_dlq"

# Phase 1 (2026-06-24): producer-side rejection. The audit consumer used to
# swallow every malformed event into the consumer DLQ, hiding the offending
# caller. These fields MUST be present on every event — anything missing is
# the caller's bug and the producer fails loudly.
REQUIRED_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "request_id",
    "action",
    "decision",
    "ts",
)


class AuditValidationError(ValueError):
    """Raised when an audit event fails producer-side validation.

    Carries the reason label used for the producer DLQ counter so callers and
    test harnesses can match on it without re-parsing the message.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def _validate_audit_event(event: dict[str, Any]) -> None:
    """Reject events without the required fields. Raises AuditValidationError
    with a stable `reason` label so the producer-DLQ counter can attribute the
    bad caller without string-matching.
    """
    missing = [f for f in REQUIRED_FIELDS if not event.get(f)]
    if missing:
        raise AuditValidationError(
            "missing_field",
            f"required field(s) missing: {','.join(missing)}",
        )

    tenant_id_raw = event["tenant_id"]
    # Accept the literal "system" sentinel that the demo-cleanup audit row
    # uses — it has no tenant scope. Everything else MUST be a parseable UUID.
    if tenant_id_raw != "system":
        try:
            uuid.UUID(str(tenant_id_raw))
        except (ValueError, TypeError) as exc:
            raise AuditValidationError(
                "invalid_tenant_uuid",
                f"tenant_id={tenant_id_raw!r} is not a parseable UUID",
            ) from exc

    request_id = event["request_id"]
    if not isinstance(request_id, str) or not request_id.strip():
        raise AuditValidationError(
            "invalid_request_id",
            f"request_id must be a non-empty string, got {type(request_id).__name__}",
        )


async def _write_to_producer_dlq(
    redis: Redis | RedisCluster,
    event: dict[str, Any],
    reason: str,
    detail: str,
) -> None:
    """Best-effort drop the bad event into the producer DLQ. Mirrors the
    consumer DLQ envelope shape used by services/audit/main.py so dashboards
    that already alert on the consumer DLQ pick this up trivially.

    Failure here is logged but never raised — the caller already saw the
    AuditValidationError and the Prometheus counter incremented. Re-raising
    would mask the original validation error.
    """
    try:
        await redis.xadd(
            _PRODUCER_DLQ_KEY,
            {
                "identity":   str(event.get("request_id", "")),
                "payload":    json.dumps(
                    {k: str(v) for k, v in event.items()},
                    default=str,
                ),
                "error":      f"{reason}: {detail}",
                "reason":     reason,
                "ts":         str(time.time()),
                "stacktrace": "".join(traceback.format_stack(limit=12)),
            },
            maxlen=5_000,
            approximate=True,
        )
    except Exception as exc:
        # If even the DLQ write fails the operator at least gets a counter
        # bump + a log line — the original AuditValidationError still bubbles
        # to the caller.
        logger.error(
            "audit_producer_dlq_write_failed",
            reason=reason,
            error=str(exc),
        )
        AUDIT_PRODUCER_DLQ_TOTAL.labels(reason="producer_dlq_write_failed").inc()


async def emit_audit_event(
    redis: Redis | RedisCluster,
    event: dict[str, Any],
) -> None:
    """Centralized audit-stream producer.

    Every audit emit MUST go through this helper. Pre-xadd validation guards
    the consumer from FK-insert failures (tenant_id missing → audit DLQ) and
    from request-correlation gaps (request_id missing → unjoinable rows).

    On validation failure:
      - The event is dropped into ``acp:audit_stream:producer_dlq`` with a
        stacktrace so the bad call site is observable.
      - ``acp_audit_producer_dlq_total{reason="..."}`` increments.
      - ``AuditValidationError`` is raised so the caller can surface the bug.

    On valid events:
      - ``SLO_AUDIT_DURABILITY_TOTAL{stage="produced"}`` increments.
      - The event is xadd'd into ``acp:audit_stream`` with the same MAXLEN=10k
        approximate cap the historical producers used.
    """
    # Normalize: every consumer reads strings, so coerce non-string values
    # before validation so type quirks in callers don't masquerade as missing.
    normalized: dict[str, str] = {}
    for k, v in event.items():
        if v is None:
            normalized[k] = ""
        elif isinstance(v, str):
            normalized[k] = v
        elif isinstance(v, (dict, list)):
            normalized[k] = json.dumps(v, default=str)
        else:
            normalized[k] = str(v)

    # Auto-fill `ts` if the caller didn't — `ts` is in REQUIRED_FIELDS so the
    # validator would otherwise reject everything that didn't explicitly pass
    # it. The audit consumer uses DB now() for the row timestamp; this `ts`
    # is only for stream-debug + DLQ correlation.
    if not normalized.get("ts"):
        normalized["ts"] = str(time.time())

    try:
        _validate_audit_event(normalized)
    except AuditValidationError as exc:
        AUDIT_PRODUCER_DLQ_TOTAL.labels(reason=exc.reason).inc()
        SLO_AUDIT_DURABILITY_TOTAL.labels(stage="failed_at_producer").inc()
        await _write_to_producer_dlq(redis, normalized, exc.reason, str(exc))
        logger.error(
            "audit_producer_validation_failed",
            reason=exc.reason,
            detail=str(exc),
            tenant_id=normalized.get("tenant_id"),
            request_id=normalized.get("request_id"),
            action=normalized.get("action"),
        )
        raise

    try:
        SLO_AUDIT_DURABILITY_TOTAL.labels(stage="produced").inc()
        # maxlen=10_000 keeps the stream's steady-state below the
        # /system/health "Degraded Performance" threshold (12_000) so the
        # status badge reflects actual queue pressure rather than the
        # producer's own retention policy. With ~150 events/s peak the
        # consumer group catches up within ~60s; entries beyond that point
        # are already XACK'd and the stream is just a debug ring buffer.
        await redis.xadd(
            _STREAM_KEY,
            normalized,
            maxlen=10_000,
            approximate=True,
        )
    except Exception:
        SLO_AUDIT_DURABILITY_TOTAL.labels(stage="failed_at_producer").inc()
        # In production hardening, we might want to fail-close here depending
        # on criticality. For now, we log and continue to satisfy "cannot fail
        # silently" rule elsewhere.
        raise


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
    """Convenience wrapper around emit_audit_event. Kept so existing call
    sites (identity, registry, decision, gateway openai/messages routers,
    demo cleanup) don't need to be rewritten. New code should prefer
    emit_audit_event directly so the validator catches missing fields at
    construction time rather than after the kwargs are flattened.
    """
    event = {
        "tenant_id":     str(tenant_id),
        "agent_id":      str(agent_id) if agent_id else "",
        "action":        action,
        "tool":          tool or "",
        "decision":      decision,
        "reason":        reason or "",
        "metadata_json": json.dumps(metadata or {}),
        "request_id":    request_id or str(uuid.uuid4()),
    }
    await emit_audit_event(redis, event)
