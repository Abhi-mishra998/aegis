"""
ACP Audit DLQ Replay Worker (2026-06-24, Phase 3 enterprise-replay sprint)
==========================================================================
Background loop that drains the audit dead-letter stream so terminal failures
don't sit in `acp:audit_stream:dlq` until an operator intervenes.

Flow
----
Every ``REPLAY_INTERVAL_SECONDS`` (default 60s):

  1. ``XRANGE acp:audit_stream:dlq - + COUNT 100`` to read the oldest batch.
  2. Inspect the embedded ``error`` field and ``retry_count`` metadata for
     each entry:

       * permanently-failing error class (FK violation / tenant not found)
         → move to ``acp:audit_stream:permanently_failed`` and DELETE
       * ``retry_count >= MAX_RETRIES``
         → same — promote to permanently_failed
       * everything else (connection refused, timeout, transient IntegrityError)
         → ``retry_count + 1`` is stamped on the payload and the event is
         ``XADD``ed back onto the live ``acp:audit_stream`` so the regular
         consumer picks it up. The DLQ entry is then DELETEd.

  3. Each decision increments the Prometheus counter
     ``acp_audit_dlq_replay_total{outcome=...}``. The gateway's
     ``/system/health`` reads this counter (along with the
     ``acp_slo_audit_durability_total`` stages) to compute the dashboard
     success-rate tiles.

Cancellation
------------
``asyncio.CancelledError`` is caught at the outer loop and breaks out cleanly;
no partial replay state is left behind because every XADD-then-XDEL pair is
ordered (XADD first; on its success the DLQ entry is removed).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import structlog
from redis.asyncio import Redis

from sdk.utils import AUDIT_DLQ_REPLAY_TOTAL

logger = structlog.get_logger(__name__)

_DLQ_KEY = "acp:audit_stream:dlq"
_LIVE_STREAM_KEY = "acp:audit_stream"
_PERMANENTLY_FAILED_KEY = "acp:audit_stream:permanently_failed"

REPLAY_INTERVAL_SECONDS = float(os.getenv("AUDIT_DLQ_REPLAY_INTERVAL", "60"))
REPLAY_BATCH_SIZE = int(os.getenv("AUDIT_DLQ_REPLAY_BATCH_SIZE", "100"))
MAX_RETRIES = int(os.getenv("AUDIT_DLQ_REPLAY_MAX_RETRIES", "5"))

# Error substrings that indicate a permanently-failing event. Matching is
# case-insensitive and substring-only so synonyms like "ForeignKeyViolation"
# from different driver versions still classify correctly.
_PERMANENT_ERROR_MARKERS = (
    "foreignkeyviolation",
    "tenant not found",
    "tenant_not_found",
    "tenant does not exist",
    "missing foreign key",
    "missing fk",
    "violates foreign key constraint",
)


def _classify_error(error_text: str, retry_count: int) -> str:
    """Decide the replay outcome for one DLQ entry.

    Returns one of: ``"replay"``, ``"permanently_failed"``.
    """
    if retry_count >= MAX_RETRIES:
        return "permanently_failed"
    lower = (error_text or "").lower()
    if any(marker in lower for marker in _PERMANENT_ERROR_MARKERS):
        return "permanently_failed"
    return "replay"


def _decode_fields(fields: dict) -> dict[str, str]:
    """Coerce a redis-py XRANGE row's fields (bytes-keyed) to str-keyed strings."""
    return {
        (k.decode() if isinstance(k, bytes) else k):
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in fields.items()
    }


def _parse_payload(decoded: dict[str, str]) -> dict[str, Any]:
    """Extract the original event payload that the consumer JSON-encoded into
    the DLQ entry. Returns an empty dict on parse failure — the caller will
    still record the outcome (permanently_failed) so the entry doesn't loop.
    """
    raw = decoded.get("payload", "{}")
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    return {}


def _build_replay_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Re-encode the original event payload into the stream-field shape the
    audit consumer expects. `_parse_stream_event` in `services/audit/main.py`
    reads each field as a string, so we coerce here.

    Returns an empty dict when the payload is missing the required (tenant_id,
    action) pair, since `AuditLogCreate` would reject the replay immediately.
    """
    if not payload.get("tenant_id") or not payload.get("action"):
        return {}
    fields: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        fields[str(key)] = (
            value if isinstance(value, str) else
            json.dumps(value) if isinstance(value, (dict, list)) else
            str(value)
        )
    return fields


async def _promote_to_permanently_failed(
    redis: Redis,
    *,
    entry_id: str,
    payload_json: str,
    error: str,
    retry_count: int,
    reason: str,
    outcome: str,
) -> bool:
    """XADD the entry onto `acp:audit_stream:permanently_failed`, XDEL it from
    the DLQ, and record the Prometheus outcome.

    Returns True on success. False (with a logged error) leaves the DLQ entry
    in place so the next pass can retry.
    """
    promo_fields = {
        "original_id": entry_id,
        "payload":     payload_json,
        "error":       error,
        "retry_count": str(retry_count),
        "ts":          str(time.time()),
        "reason":      reason,
    }
    try:
        await redis.xadd(_PERMANENTLY_FAILED_KEY, promo_fields)
    except Exception as exc:
        logger.error(
            "audit_dlq_replay_promotion_failed",
            error=str(exc), entry_id=entry_id, reason=reason,
        )
        return False
    await redis.xdel(_DLQ_KEY, entry_id)
    AUDIT_DLQ_REPLAY_TOTAL.labels(outcome=outcome).inc()
    logger.info(
        "audit_dlq_event_promoted",
        entry_id=entry_id, retry_count=retry_count, reason=reason, outcome=outcome,
    )
    return True


async def _process_one(redis: Redis, entry_id: bytes | str, fields: dict) -> None:
    """Handle one DLQ entry: classify, replay or promote, then delete from DLQ."""
    entry_id_str = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
    decoded = _decode_fields(fields)
    error_text = decoded.get("error", "")
    payload = _parse_payload(decoded)

    # retry_count lives inside the payload (this worker stamps it on each
    # replay so the consumer's stream-write contract is unchanged).
    try:
        retry_count = int(payload.get("retry_count", 0))
    except (TypeError, ValueError):
        retry_count = 0

    if _classify_error(error_text, retry_count) == "permanently_failed":
        reason = (
            "max_retries_exceeded" if retry_count >= MAX_RETRIES
            else "non_recoverable_error_class"
        )
        await _promote_to_permanently_failed(
            redis,
            entry_id=entry_id_str,
            payload_json=json.dumps(payload),
            error=error_text,
            retry_count=retry_count,
            reason=reason,
            outcome="permanently_failed",
        )
        return

    # Replay path: bump retry_count + rebuild the original stream fields and
    # XADD back onto acp:audit_stream so the regular consumer picks it up.
    payload["retry_count"] = retry_count + 1
    new_fields = _build_replay_fields(payload)
    if not new_fields:
        await _promote_to_permanently_failed(
            redis,
            entry_id=entry_id_str,
            payload_json=decoded.get("payload", "{}"),
            error="replay_rebuild_failed: missing tenant_id or action",
            retry_count=retry_count,
            reason="unparseable_payload",
            outcome="skipped",
        )
        return

    try:
        await redis.xadd(_LIVE_STREAM_KEY, new_fields)
    except Exception as exc:
        # XADD failed — leave the DLQ entry in place for the next pass.
        logger.error(
            "audit_dlq_replay_xadd_failed", error=str(exc), entry_id=entry_id_str,
        )
        return
    # Delete from DLQ only after the live-stream XADD succeeded.
    await redis.xdel(_DLQ_KEY, entry_id_str)
    AUDIT_DLQ_REPLAY_TOTAL.labels(outcome="replayed").inc()
    logger.info(
        "audit_dlq_event_replayed",
        entry_id=entry_id_str, retry_count=retry_count + 1,
    )


async def _one_pass(redis: Redis) -> int:
    """Read up to REPLAY_BATCH_SIZE entries from the DLQ and process them.

    Returns the number of entries processed (independent of outcome) so the
    caller can log + decide whether to sleep before the next pass.
    """
    try:
        entries = await redis.xrange(_DLQ_KEY, "-", "+", count=REPLAY_BATCH_SIZE)
    except Exception as exc:
        logger.warning("audit_dlq_replay_xrange_failed", error=str(exc))
        return 0
    if not entries:
        return 0
    processed = 0
    for entry_id, fields in entries:
        try:
            await _process_one(redis, entry_id, fields)
            processed += 1
        except Exception as exc:
            logger.error(
                "audit_dlq_replay_entry_failed",
                error=str(exc), entry_id=str(entry_id),
            )
    return processed


async def run_dlq_replay_loop(redis: Redis) -> None:
    """Cancellation-safe replay loop. Wired from the audit-service lifespan
    alongside the regular stream consumer in `services/audit/main.py`.
    """
    logger.info(
        "audit_dlq_replay_worker_started",
        interval=REPLAY_INTERVAL_SECONDS,
        batch_size=REPLAY_BATCH_SIZE,
        max_retries=MAX_RETRIES,
    )
    try:
        while True:
            try:
                n = await _one_pass(redis)
                if n > 0:
                    logger.info("audit_dlq_replay_pass_complete", processed=n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("audit_dlq_replay_loop_error", error=str(exc))
            try:
                await asyncio.sleep(REPLAY_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        logger.info("audit_dlq_replay_worker_shutdown")
        return
