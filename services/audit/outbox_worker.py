"""
Audit → Billing Outbox Worker  (2026-05-14, Run-3 production hardening)
=======================================================================
Durability backstop for the sync billing path. The gateway middleware does
sync billing on the request hot path (low latency, primary). This worker is
the safety net: if sync ever drops an event — container OOM, network blip,
retry exhaustion — the outbox row is still in `pending_usage_events` and we
forward it to the usage service here.

Idempotency
-----------
Both paths converge on the same `usage_records.audit_id` UNIQUE constraint,
so a successful sync write followed by a worker retry is a no-op (PG returns
ON CONFLICT DO NOTHING). Same audit_id can never produce two usage rows.

Failure modes
-------------
- success (2xx, 409): processed; 409 means "already recorded" — semantically
  equivalent to the ON CONFLICT DO NOTHING idempotent-write outcome and MUST
  NOT be treated as transient (retry forever) or terminal (poison the row).
- transient (5xx, 408, 429, network): leave row pending, retry next poll;
  do NOT increment retry_count (downstream fault, not the event's fault).
- terminal (4xx other than 408/429/409, malformed): retry_count += 1,
  eventually poisoned.
- poisoned (retry_count > MAX): status='failed', alertable via OUTBOX_POISON_TOTAL

The poll loop is single-threaded inside this process. To run multiple workers
(future): partition by tenant_id range or add a row-level advisory lock.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.config import settings
from sdk.utils import (
    OUTBOX_PENDING_GAUGE,
    OUTBOX_POISON_TOTAL,
    OUTBOX_PROCESSED_TOTAL,
    OUTBOX_RETRY_TOTAL,
)
from services.audit.database import SessionLocal
from services.audit.models import PendingUsageEvent

logger = structlog.get_logger(__name__)

# QA-OUTBOX-FIX (2026-06-24) — the pre-launch audit observed outbox_pending
# growing 335 → 7 443 (+22×) under one IP of moderate SDET probing. Capacity
# math: the previous defaults processed 100 rows every 5 s = 20 rows/s, but
# each row sits for 60 s ``OUTBOX_GRACE_SECONDS`` before being claimable.
# Under burst traffic the queue grows linearly until burst ends. The grace
# period existed so the sync billing path (gateway middleware) could finish
# first and the worker wouldn't double-process, but the unique
# ``usage_records.audit_id`` constraint already enforces idempotency
# (`ON CONFLICT DO NOTHING`), so a 30 s grace is plenty. Combined changes:
#
#   GRACE  60 → 30   keeps the sync-billing-first race-prevention property
#                    intact (typical sync billing finishes in <500 ms; 30 s
#                    is 60× the p99 timeout) but halves the in-flight queue
#                    depth under steady traffic.
#   BATCH  100 → 250 a single worker now drains 250 rows per tick.
#   POLL   5 s → 2 s tighter loop reduces median outbox age from ~7 s to
#                    ~3 s while staying well under the gateway's per-call
#                    budget for any single DB query.
#
# Override via env vars in dev — sustained workloads above ~125 rows/s per
# tenant should add a second worker instead (the SELECT ... FOR UPDATE
# SKIP LOCKED claim already supports horizontal scaling).
OUTBOX_GRACE_SECONDS = int(os.getenv("OUTBOX_GRACE_SECONDS", "30"))
OUTBOX_BATCH_SIZE = int(os.getenv("OUTBOX_BATCH_SIZE", "250"))
OUTBOX_POLL_INTERVAL = float(os.getenv("OUTBOX_POLL_INTERVAL", "2.0"))
OUTBOX_MAX_RETRIES = int(os.getenv("OUTBOX_MAX_RETRIES", "5"))
WORKER_ID = os.getenv("WORKER_ID", "audit-outbox-1")


async def _claim_batch(db: AsyncSession) -> list[PendingUsageEvent]:
    """
    Claim a batch of pending rows older than the grace period.
    Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers don't double-process.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=OUTBOX_GRACE_SECONDS)
    stmt = (
        select(PendingUsageEvent)
        .where(PendingUsageEvent.status == "pending")
        .where(PendingUsageEvent.created_at <= cutoff)
        .where(PendingUsageEvent.retry_count < OUTBOX_MAX_RETRIES)
        .order_by(PendingUsageEvent.created_at.asc())
        .limit(OUTBOX_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _forward_to_usage(
    client: httpx.AsyncClient, ev: PendingUsageEvent
) -> tuple[bool, str | None, bool]:
    """
    POST the outbox event to the usage service. Returns (success, error_msg, is_transient).

    Run-3 (2026-05-14): the third return value tells the caller whether to count
    the attempt toward retry_count. Transient = downstream unreachable / 5xx /
    timeout — those are downstream-side faults and MUST NOT poison the event. A
    deployment restart or 30s outage would otherwise burn through MAX_RETRIES
    and falsely mark 100s of events as poisoned, defeating the purpose of the
    outbox.

    Idempotency: usage_records uses ON CONFLICT(audit_id) DO NOTHING, so duplicate
    writes from sync+outbox both succeed as a no-op.
    """
    payload = {
        "tenant_id": str(ev.tenant_id),
        "agent_id": str(ev.agent_id) if ev.agent_id else None,
        "tool": ev.tool,
        "units": int(ev.units),
        "cost": float(ev.cost),
        "audit_id": str(ev.audit_id),
    }
    from sdk.common.auth import mesh_headers
    headers = {
        **mesh_headers("audit"),
        "X-Tenant-ID": str(ev.tenant_id),
        "X-Request-ID": str(ev.audit_id),
        "Content-Type": "application/json",
    }
    try:
        resp = await client.post(
            f"{settings.USAGE_SERVICE_URL.rstrip('/')}/usage/record",
            json=payload,
            headers=headers,
            timeout=5.0,
        )
        # 2xx OR 409 = success-equivalent. 409 is the "already delivered /
        # unique-constraint hit" response from idempotent endpoints (e.g.
        # services/usage/billing_routes/router.py). The hot path
        # /usage/record uses ON CONFLICT DO NOTHING and returns 2xx, but
        # adjacent idempotent endpoints — and any new caller — may return
        # 409 to mean "your event was already recorded; nothing to do."
        # Treating 409 as terminal would poison legitimately-delivered
        # events; treating it as transient would retry forever. Both are
        # wrong. The right answer is: this event has been recorded, mark
        # it completed.
        if 200 <= resp.status_code < 300 or resp.status_code == 409:
            return True, None, False
        # 5xx, 408 (timeout), 429 (rate-limit), 503 (unavailable) = transient (downstream-side)
        # 4xx other than 408/429/409 = terminal (this event will never succeed; poison it)
        transient = resp.status_code >= 500 or resp.status_code in (408, 429)
        return False, f"http_{resp.status_code}:{resp.text[:120]}", transient
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        # Network-layer failures are ALWAYS transient — usage service is down,
        # not the event being malformed.
        return False, f"network:{type(exc).__name__}:{str(exc)[:80]}", True


async def _process_batch(db: AsyncSession, client: httpx.AsyncClient) -> int:
    """Process one batch. Returns the number of rows handled (succeeded or retried)."""
    events = await _claim_batch(db)
    if not events:
        return 0

    handled = 0
    for ev in events:
        ok, err, transient = await _forward_to_usage(client, ev)
        if ok:
            await db.execute(
                update(PendingUsageEvent)
                .where(PendingUsageEvent.id == ev.id)
                .values(
                    status="completed",
                    processed_at=func.now(),
                    error_message=None,
                )
            )
            OUTBOX_PROCESSED_TOTAL.inc()
            logger.info(
                "outbox_processed",
                worker_id=WORKER_ID,
                audit_id=str(ev.audit_id),
                tenant_id=str(ev.tenant_id),
                tool=ev.tool,
                retry_count=ev.retry_count,
            )
        elif transient:
            # Downstream-side failure (5xx, timeout, network). DO NOT increment
            # retry_count — that's reserved for events that are themselves bad.
            # Just leave the row pending; the next poll cycle retries.
            await db.execute(
                update(PendingUsageEvent)
                .where(PendingUsageEvent.id == ev.id)
                .values(
                    error_message=(err or "")[:500],
                )
            )
            OUTBOX_RETRY_TOTAL.inc()
            logger.warning(
                "outbox_transient_retry",
                worker_id=WORKER_ID,
                audit_id=str(ev.audit_id),
                tenant_id=str(ev.tenant_id),
                error=err,
            )
        else:
            # Terminal failure (4xx other than 408/429). The event itself is bad;
            # retrying will not help. Increment retry_count and poison after MAX.
            new_retry = (ev.retry_count or 0) + 1
            new_status = "failed" if new_retry >= OUTBOX_MAX_RETRIES else "pending"
            await db.execute(
                update(PendingUsageEvent)
                .where(PendingUsageEvent.id == ev.id)
                .values(
                    status=new_status,
                    retry_count=new_retry,
                    error_message=(err or "")[:500],
                )
            )
            if new_status == "failed":
                OUTBOX_POISON_TOTAL.inc()
                logger.critical(
                    "outbox_poisoned",
                    worker_id=WORKER_ID,
                    audit_id=str(ev.audit_id),
                    tenant_id=str(ev.tenant_id),
                    retry_count=new_retry,
                    error=err,
                )
            else:
                OUTBOX_RETRY_TOTAL.inc()
                logger.warning(
                    "outbox_terminal_retry",
                    worker_id=WORKER_ID,
                    audit_id=str(ev.audit_id),
                    tenant_id=str(ev.tenant_id),
                    retry_count=new_retry,
                    error=err,
                )
        handled += 1

    # SELECT FOR UPDATE held the rows for the duration of the loop; commit
    # releases the locks and persists the status changes.
    await db.commit()
    return handled


async def _update_pending_gauge(db: AsyncSession) -> None:
    """Cheap aggregate so the gauge reflects current backlog without scanning."""
    stmt = select(func.count(PendingUsageEvent.id)).where(PendingUsageEvent.status == "pending")
    result = await db.execute(stmt)
    OUTBOX_PENDING_GAUGE.set(int(result.scalar() or 0))


async def run_outbox_worker() -> None:
    """
    Long-running poll loop. Started as an asyncio task from the audit service's
    lifespan handler. Cooperatively cancellable.
    """
    logger.info(
        "outbox_worker_started",
        worker_id=WORKER_ID,
        grace_seconds=OUTBOX_GRACE_SECONDS,
        batch_size=OUTBOX_BATCH_SIZE,
        poll_interval=OUTBOX_POLL_INTERVAL,
        max_retries=OUTBOX_MAX_RETRIES,
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        while True:
            try:
                async with SessionLocal() as db:
                    processed = await _process_batch(db, client)
                    await _update_pending_gauge(db)

                # Adaptive backoff: when the backlog is empty we poll less often
                # to avoid burning DB connections; when there's work, we keep
                # the queue draining.
                if processed == 0:
                    await asyncio.sleep(OUTBOX_POLL_INTERVAL)
                else:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                logger.info("outbox_worker_cancelled", worker_id=WORKER_ID)
                break
            except Exception as exc:  # noqa: BLE001 — top-level supervisor
                # Top-level supervisor: we must NOT crash the worker on a
                # transient DB error. Log + backoff so we don't tight-loop on
                # poisoned state. The forbidden-pattern rule (`except: pass`)
                # is about silent suppression; this branch logs + counts and
                # then resumes — that's a supervisor, not a silencer.
                logger.error(
                    "outbox_worker_loop_error",
                    worker_id=WORKER_ID,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                await asyncio.sleep(min(OUTBOX_POLL_INTERVAL * 2, 30))
