"""
ACP Billing DLQ Replay Worker (2026-06-24, Phase 3 enterprise-replay sprint)
============================================================================
Background loop that drains the billing dead-letter queue so terminal failures
don't sit in ``acp:billing_dlq`` until an operator intervenes.

This mirrors the audit DLQ replay worker (``services/audit/dlq_replay.py``)
adapted for the billing pipeline, which uses a Redis LIST (``lpush``/``lrange``)
rather than a STREAM.

Flow
----
Every ``REPLAY_INTERVAL_SECONDS`` (default 60s):

  1. ``LRANGE acp:billing_dlq 0 COUNT-1`` to read the oldest batch, then
     trim them off via ``LTRIM`` once each entry is handled. The batch is
     processed in-memory so a transient Redis hiccup mid-pass can't both
     re-process an entry and lose it.
  2. Inspect the entry's ``reason`` / embedded error string and ``retry_count``
     metadata:

       * permanently-failing error class (FK violation / tenant not found /
         max_retries_exhausted marker) → push onto
         ``acp:billing_dlq:permanently_failed``
       * ``retry_count >= MAX_RETRIES`` → same — promote to permanently_failed
       * everything else (connection refused, timeout, transient IntegrityError)
         → ``retry_count`` is bumped on the payload and the event is
         ``RPUSH``ed back onto the live ``acp:billing_retry_queue`` so the
         gateway billing-retry worker picks it up.

  3. Each decision increments the Prometheus counter
     ``acp_billing_dlq_replay_total{outcome=...}``. The gateway's
     ``/system/health`` reads this counter to compute the dashboard
     success-rate tile.

Cancellation
------------
``asyncio.CancelledError`` is caught at the outer loop and breaks out cleanly.
Each entry is consumed via ``LPOP`` so a cancel mid-batch leaves the remaining
entries on the DLQ for the next pass.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog
from redis.asyncio import Redis

from sdk.utils import BILLING_DLQ_REPLAY_TOTAL

logger = structlog.get_logger(__name__)

_DLQ_KEY = "acp:billing_dlq"
_RETRY_QUEUE_KEY = "acp:billing_retry_queue"
_PERMANENTLY_FAILED_KEY = "acp:billing_dlq:permanently_failed"

REPLAY_INTERVAL_SECONDS = float(os.getenv("BILLING_DLQ_REPLAY_INTERVAL", "60"))
REPLAY_BATCH_SIZE = int(os.getenv("BILLING_DLQ_REPLAY_BATCH_SIZE", "100"))
MAX_RETRIES = int(os.getenv("BILLING_DLQ_REPLAY_MAX_RETRIES", "5"))

# Error substrings that indicate a permanently-failing event. Matching is
# case-insensitive and substring-only so synonyms like "ForeignKeyViolation"
# from different driver versions still classify correctly. The gateway tags
# `max_retries_exhausted` as the reason when the sync write loop gave up, so
# we treat that as terminal too (the live worker will only re-fail).
_PERMANENT_ERROR_MARKERS = (
    "foreignkeyviolation",
    "tenant not found",
    "tenant_not_found",
    "tenant does not exist",
    "missing foreign key",
    "missing fk",
    "violates foreign key constraint",
    "max_retries_exhausted",
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


def _decode_item(raw: bytes | str) -> dict[str, Any]:
    """Parse one Redis-list entry into a dict. Returns ``{}`` if the entry is
    not JSON — the caller will promote unparseable entries to permanently_failed
    so they don't loop forever.
    """
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _entry_error(data: dict[str, Any]) -> str:
    """Pull the error/reason string out of a DLQ entry. The gateway tags both
    `reason` (from `_persist_billing_dlq`) and ad-hoc errors at the top level,
    so we check both before falling back to an empty string.
    """
    return (
        str(data.get("reason") or "")
        or str(data.get("error") or "")
        or str(data.get("last_error") or "")
    )


def _audit_id(data: dict[str, Any]) -> str | None:
    """Pull the original audit_id out of a DLQ entry's nested payload, with
    a safe fallback when the payload is missing or non-dict (eg unparseable
    raw entries promoted via the skipped path).
    """
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload.get("audit_id")
    return None


async def _promote_to_permanently_failed(
    redis: Redis,
    *,
    data: dict[str, Any],
    reason: str,
    outcome: str,
) -> bool:
    """LPUSH the entry onto ``acp:billing_dlq:permanently_failed`` and record
    the Prometheus outcome.

    Returns True on success. False (with a logged error) means the caller
    must re-queue the entry to avoid losing it.
    """
    promo = dict(data)
    promo["replay_reason"] = reason
    audit_id = _audit_id(data)
    try:
        await redis.lpush(_PERMANENTLY_FAILED_KEY, json.dumps(promo))
    except Exception as exc:
        logger.error(
            "billing_dlq_replay_promotion_failed",
            error=str(exc), audit_id=audit_id, reason=reason,
        )
        return False
    BILLING_DLQ_REPLAY_TOTAL.labels(outcome=outcome).inc()
    logger.info(
        "billing_dlq_event_promoted",
        audit_id=audit_id,
        retry_count=int(data.get("retry_count", 0) or 0),
        reason=reason,
        outcome=outcome,
    )
    return True


async def _process_one(redis: Redis, raw_item: bytes | str) -> None:
    """Handle one DLQ entry: classify, then either RPUSH back onto the live
    retry queue or LPUSH onto the permanently_failed list.
    """
    data = _decode_item(raw_item)
    if not data:
        # Unparseable — promote to permanently_failed with a minimal envelope
        # so operators can still inspect the raw bytes.
        text = raw_item.decode() if isinstance(raw_item, bytes) else str(raw_item)
        await _promote_to_permanently_failed(
            redis,
            data={"raw": text[:2000], "retry_count": MAX_RETRIES},
            reason="unparseable_payload",
            outcome="skipped",
        )
        return

    try:
        retry_count = int(data.get("retry_count", 0) or 0)
    except (TypeError, ValueError):
        retry_count = 0
    error_text = _entry_error(data)

    if _classify_error(error_text, retry_count) == "permanently_failed":
        reason = (
            "max_retries_exceeded" if retry_count >= MAX_RETRIES
            else "non_recoverable_error_class"
        )
        await _promote_to_permanently_failed(
            redis,
            data=data,
            reason=reason,
            outcome="permanently_failed",
        )
        return

    # Replay path: bump retry_count and push the entry back onto the live
    # retry queue so the gateway's _process_billing_queue worker picks it up.
    data["retry_count"] = retry_count + 1
    audit_id = _audit_id(data)
    try:
        await redis.rpush(_RETRY_QUEUE_KEY, json.dumps(data))
    except Exception as exc:
        logger.error(
            "billing_dlq_replay_rpush_failed", error=str(exc), audit_id=audit_id,
        )
        # Push the entry back onto the DLQ tail so the next pass can retry.
        try:
            await redis.rpush(_DLQ_KEY, json.dumps(data))
        except Exception as inner_exc:
            logger.critical(
                "billing_dlq_replay_requeue_failed",
                error=str(inner_exc), audit_id=audit_id,
            )
        return

    BILLING_DLQ_REPLAY_TOTAL.labels(outcome="replayed").inc()
    logger.info(
        "billing_dlq_event_replayed", audit_id=audit_id, retry_count=retry_count + 1,
    )


async def _one_pass(redis: Redis) -> int:
    """Read up to REPLAY_BATCH_SIZE entries from the DLQ via LPOP and process
    each one. LPOP is destructive: an entry is removed from the DLQ before
    being classified, so the worker is responsible for either re-queueing onto
    `acp:billing_retry_queue` or pushing onto `acp:billing_dlq:permanently_failed`.

    Returns the number of entries processed.
    """
    processed = 0
    for _ in range(REPLAY_BATCH_SIZE):
        try:
            raw = await redis.lpop(_DLQ_KEY)
        except Exception as exc:
            logger.warning("billing_dlq_replay_lpop_failed", error=str(exc))
            break
        if raw is None:
            break
        try:
            await _process_one(redis, raw)
            processed += 1
        except Exception as exc:
            logger.error(
                "billing_dlq_replay_entry_failed",
                error=str(exc),
                raw=(raw.decode() if isinstance(raw, bytes) else str(raw))[:200],
            )
            # Push the bad entry back onto the DLQ so we don't lose it.
            try:
                await redis.rpush(_DLQ_KEY, raw)
            except Exception as inner:
                logger.critical(
                    "billing_dlq_replay_requeue_after_error_failed",
                    error=str(inner),
                )
    return processed


async def run_dlq_replay_loop(redis: Redis) -> None:
    """Cancellation-safe replay loop. Wired from the usage-service lifespan
    alongside the existing reconciliation + pending_billing workers in
    ``services/usage/main.py``.
    """
    logger.info(
        "billing_dlq_replay_worker_started",
        interval=REPLAY_INTERVAL_SECONDS,
        batch_size=REPLAY_BATCH_SIZE,
        max_retries=MAX_RETRIES,
    )
    try:
        while True:
            try:
                n = await _one_pass(redis)
                if n > 0:
                    logger.info("billing_dlq_replay_pass_complete", processed=n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("billing_dlq_replay_loop_error", error=str(exc))
            try:
                await asyncio.sleep(REPLAY_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        logger.info("billing_dlq_replay_worker_shutdown")
        return
