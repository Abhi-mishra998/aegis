"""Background scheduler that commits daily Merkle roots.

A cron-style loop inside the audit service. Every hour it:

  1. Finds every (tenant_id, root_date) pair where:
     - root_date is yesterday (UTC) or older within a 7-day backfill window
     - that pair has at least one audit row
     - that pair is NOT yet persisted in transparency_roots
  2. Computes and persists the root for each missing pair.

Idempotent — calling compute_daily_root twice for the same (tenant, date)
just replaces the row. Restart-safe.

Exposes two Prometheus metrics:
  acp_transparency_roots_committed_total   counter
  acp_transparency_scheduler_last_success  gauge (unix timestamp)
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, date, datetime, timedelta

import structlog
from prometheus_client import Counter, Gauge
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.models import AuditLog, TransparencyRoot
from services.audit.signer import get_root_signer
from services.audit.transparency import (
    _leaf_for_row,
    _persist_root,
    _previous_root_hash,
    _rows_for_day,
    _sign_root,
    build_root,
    empty_epoch_root_hash,
)

log = structlog.get_logger(__name__)

ROOTS_COMMITTED = Counter(
    "acp_transparency_roots_committed_total",
    "Daily Merkle roots persisted to the transparency log.",
)
SCHEDULER_LAST_SUCCESS = Gauge(
    "acp_transparency_scheduler_last_success",
    "Unix timestamp of the most recent successful scheduler pass.",
)

# How often the loop runs. Tunable so a demo box can refresh today's running
# root every few minutes while production still defaults to an hourly cadence.
SCHEDULER_INTERVAL_SECONDS = int(
    os.environ.get("TRANSPARENCY_SCHEDULER_INTERVAL", str(60 * 60))
)
# How far back to look when backfilling missing days.
BACKFILL_WINDOW_DAYS = int(os.environ.get("TRANSPARENCY_BACKFILL_DAYS", "7"))


async def _missing_pairs(db: AsyncSession, window_days: int) -> list[tuple[str, date]]:
    """Find (tenant_id, day) pairs that have audit rows.

    Returns pairs for every day in the backfill window INCLUDING today —
    today's row is upserted on each pass so customers always see a current
    "running" root instead of waiting for the UTC day to roll over. Yesterday
    and earlier roots are immutable once their day closes (no new events) but
    we keep them in the candidate list so a re-deploy can heal a missed
    commit.
    """
    today = datetime.now(UTC).date()
    earliest = today - timedelta(days=window_days)
    # Inclusive of today: we want a live root while the day is open.
    horizon = today + timedelta(days=1)

    # 1. All (tenant, day) pairs with audit rows in the window.
    day_expr = func.date_trunc("day", AuditLog.timestamp).label("day")
    tenant_day_rows = (
        await db.execute(
            select(distinct(AuditLog.tenant_id), day_expr).where(
                AuditLog.timestamp >= datetime(earliest.year, earliest.month, earliest.day, tzinfo=UTC),
                AuditLog.timestamp < datetime(horizon.year, horizon.month, horizon.day, tzinfo=UTC),
            )
        )
    ).all()

    # 2. Already-persisted pairs in that window. Today is always considered
    #    missing so we recompute its running root on every pass.
    persisted = {
        (row.tenant_id, row.root_date)
        for row in (
            await db.execute(
                select(TransparencyRoot.tenant_id, TransparencyRoot.root_date).where(
                    TransparencyRoot.root_date >= earliest,
                    TransparencyRoot.root_date < today,
                )
            )
        ).all()
    }

    out: list[tuple[str, date]] = []
    seen: set[tuple] = set()
    for tenant_id, dt in tenant_day_rows:
        d = dt.date() if isinstance(dt, datetime) else dt
        if (tenant_id, d) not in persisted:
            out.append((tenant_id, d))
            seen.add((tenant_id, d))

    # 3. Empty-epoch fill-in. For each tenant that has at least one prior
    #    persisted root, ensure today gets a row too — even if no audit
    #    events landed today. This is what guarantees the chain has no
    #    silent gaps on quiet days (a customer auditing a 7-day window
    #    should see 7 sequential roots, not 4-with-2-holes).
    historical_tenants = {
        row.tenant_id
        for row in (
            await db.execute(
                select(distinct(TransparencyRoot.tenant_id))
            )
        ).all()
    }
    for tenant_id in historical_tenants:
        if (tenant_id, today) not in seen:
            out.append((tenant_id, today))
            seen.add((tenant_id, today))

    return out


async def _commit_one(db: AsyncSession, tenant_id, day: date) -> None:
    """Compute + persist the daily root for one (tenant, day) pair."""
    rows = await _rows_for_day(db, tenant_id, day)
    prev_hash = await _previous_root_hash(db, tenant_id, day)

    leaf_range_start_id = None
    leaf_range_end_id = None
    if not rows:
        # Empty-epoch path. Two cases:
        #   - This tenant has never sealed any root → skip silently (we'd be
        #     committing to a non-existent chain anchor).
        #   - This tenant has prior roots → seal a marker so the chain has no
        #     gaps. The marker's root_hash is `sha256(prev_root_hash || empty
        #     sentinel)`, deterministically reproducible by any customer.
        if prev_hash is None:
            log.info(
                "transparency_root_skipped_empty_genesis",
                tenant_id=str(tenant_id),
                root_date=day.isoformat(),
            )
            return
        leaves: list[str] = []
        root = empty_epoch_root_hash(prev_hash)
    else:
        leaves = [_leaf_for_row(r) for r in rows]
        root = build_root(leaves)
        leaf_range_start_id = rows[0].id
        leaf_range_end_id = rows[-1].id

    signer = get_root_signer()
    signed = _sign_root(
        tenant_id, day, root, len(leaves),
        prev_root_hash=prev_hash,
        leaf_range_start_id=leaf_range_start_id,
        leaf_range_end_id=leaf_range_end_id,
    )
    await _persist_root(
        db,
        tenant_id=tenant_id,
        root_date=day,
        root_hash=root,
        leaf_count=len(leaves),
        signed_payload=signed,
        prev_root_hash=prev_hash,
        leaf_range_start_id=leaf_range_start_id,
        leaf_range_end_id=leaf_range_end_id,
        signing_key_fingerprint=signer._fingerprint,  # noqa: SLF001
    )
    ROOTS_COMMITTED.inc()
    log.info(
        "transparency_root_committed",
        tenant_id=str(tenant_id),
        root_date=day.isoformat(),
        leaf_count=len(leaves),
        root_hash=root[:16] + "...",
        signing_key_fingerprint=signer._fingerprint,  # noqa: SLF001
        empty_epoch=len(leaves) == 0,
    )


async def _one_pass(session_factory) -> int:
    """One iteration. Returns the number of roots committed."""
    committed = 0
    async with session_factory() as db:
        pairs = await _missing_pairs(db, BACKFILL_WINDOW_DAYS)

    # Process each pair in its own transaction so a single failure doesn't
    # block the rest.
    for tenant_id, day in pairs:
        try:
            async with session_factory() as db:
                await _commit_one(db, tenant_id, day)
            committed += 1
        except Exception as exc:
            log.warning(
                "transparency_root_commit_failed",
                tenant_id=str(tenant_id),
                root_date=day.isoformat(),
                error=str(exc),
            )

    SCHEDULER_LAST_SUCCESS.set(time.time())
    return committed


async def run_transparency_scheduler(session_factory) -> None:
    """Run forever. Cancelled by the audit service's lifespan teardown."""
    log.info("transparency_scheduler_started", interval_seconds=SCHEDULER_INTERVAL_SECONDS)
    # Initial backfill on boot — the previous instance may have crashed
    # right before committing yesterday's root.
    try:
        committed = await _one_pass(session_factory)
        log.info("transparency_scheduler_initial_pass", committed=committed)
    except Exception as exc:
        log.warning("transparency_scheduler_initial_pass_failed", error=str(exc))

    while True:
        try:
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
            committed = await _one_pass(session_factory)
            if committed:
                log.info("transparency_scheduler_pass", committed=committed)
        except asyncio.CancelledError:
            log.info("transparency_scheduler_cancelled")
            return
        except Exception as exc:
            log.warning("transparency_scheduler_loop_error", error=str(exc))
            # Don't tight-loop on errors — sleep a bit before retrying.
            await asyncio.sleep(60)
