#!/usr/bin/env python3
"""Backfill leaked Flight Recorder timelines.

The gateway is expected to close every `execution_timelines` row it opens via a
matching `emit_timeline_end` event. Historically a number of branches
(security blocks, autonomy denies, HTTPException paths) returned without
emitting the close, leaving rows wedged at:

    status         = 'in_progress'
    final_decision = NULL
    duration_ms    = NULL
    completed_at   = NULL

This script repairs the historical wedge by:

  1. Selecting timelines older than --grace-minutes (default 5) that are still
     `in_progress`.
  2. Inferring the tool from the earliest related step (steps carry the tool
     name in payload / step_type when the timeline row itself is null).
  3. Inferring final_decision from the latest step's `status` (deny/block/error
     → block; ok → allow; otherwise → error).
  4. Computing duration_ms from started_at → latest step.occurred_at (or now
     when the timeline has no steps).
  5. Setting status='recovered_backfill' so this row is visually + queryably
     distinguishable from live-closed timelines.

Idempotent: re-running on the same table is a no-op once everything is closed.

Run inside the acp_flight_recorder container (DATABASE_URL already points at
acp_flight_recorder), or pass --database-url explicitly:

    # in container
    python scripts/maintenance/backfill_flight_timelines.py --execute

    # locally
    DATABASE_URL=postgresql+asyncpg://flight_recorder_user:...@localhost:5432/acp_flight_recorder \
        python scripts/maintenance/backfill_flight_timelines.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Allow `python scripts/maintenance/backfill_flight_timelines.py` from repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.flight_recorder.models import (  # noqa: E402
    ExecutionStep,
    ExecutionTimeline,
)

logger = structlog.get_logger(__name__)

RECOVERED_STATUS = "recovered_backfill"


_DECISION_FROM_STATUS = {
    "ok":     "allow",
    "allow":  "allow",
    "deny":   "block",
    "block":  "block",
    "error":  "error",
    "pending": "escalate",
}


def _infer_decision(step_status: str | None, step_type: str | None) -> str:
    """Best-effort projection of step row → final_decision label.

    The step row's `status` already encodes the gateway's last classification
    (ok|deny|error|pending). We fall back to the step_type when status is null
    (some older rows pre-date the status column being non-null).
    """
    if step_status:
        mapped = _DECISION_FROM_STATUS.get(step_status.strip().lower())
        if mapped:
            return mapped
    if step_type and step_type.strip().lower() == "failure":
        return "error"
    # Conservative default: we genuinely don't know. Mark as error so the
    # recovered row is visually anomalous and gets attention.
    return "error"


def _infer_tool(first_step: ExecutionStep | None) -> str | None:
    """First step typically carries the tool either explicitly in payload, or
    implicitly via step_type. Return None if we can't tell; callers preserve
    the existing column value in that case."""
    if first_step is None:
        return None
    payload = first_step.payload or {}
    candidate = payload.get("tool") or payload.get("tool_name")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()[:255]
    # `step_type` is the gateway-phase label (policy, inference_proxy, etc.),
    # not the user-facing tool. Returning None is honest.
    return None


async def _process_timeline(
    db: AsyncSession,
    timeline: ExecutionTimeline,
    *,
    dry_run: bool,
) -> dict[str, object]:
    """Compute the recovery shape for one timeline. Returns a summary dict
    suitable for logging / aggregation."""
    steps_stmt = (
        select(ExecutionStep)
        .where(ExecutionStep.timeline_id == timeline.id)
        .order_by(ExecutionStep.step_index, ExecutionStep.occurred_at)
    )
    steps = (await db.execute(steps_stmt)).scalars().all()

    first_step = steps[0] if steps else None
    last_step = steps[-1] if steps else None

    inferred_tool = timeline.tool or _infer_tool(first_step)
    inferred_decision = _infer_decision(
        last_step.status if last_step else None,
        last_step.step_type if last_step else None,
    )

    completed_at = (
        last_step.occurred_at
        if last_step and last_step.occurred_at is not None
        else datetime.now(tz=UTC)
    )
    started = timeline.started_at or completed_at
    duration_ms = max(0, int((completed_at - started).total_seconds() * 1000))

    summary = {
        "timeline_id":     str(timeline.id),
        "request_id":      timeline.request_id,
        "tool_before":     timeline.tool,
        "tool_after":      inferred_tool,
        "final_decision":  inferred_decision,
        "duration_ms":     duration_ms,
        "step_count":      len(steps),
    }

    if dry_run:
        return summary

    # We update in a single statement so concurrent runs of the script don't
    # race (the WHERE status='in_progress' guard is the idempotency anchor).
    await db.execute(
        update(ExecutionTimeline)
        .where(
            ExecutionTimeline.id == timeline.id,
            ExecutionTimeline.status == "in_progress",
        )
        .values(
            tool=inferred_tool,
            final_decision=inferred_decision,
            duration_ms=duration_ms,
            completed_at=completed_at,
            status=RECOVERED_STATUS,
        )
    )
    return summary


async def backfill(
    *,
    database_url: str,
    grace_minutes: int,
    dry_run: bool,
    limit: int | None,
) -> dict[str, int]:
    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    cutoff = datetime.now(tz=UTC) - timedelta(minutes=grace_minutes)
    counts = {"scanned": 0, "would_update": 0, "updated": 0}

    async with session_factory() as db:
        stmt = (
            select(ExecutionTimeline)
            .where(
                ExecutionTimeline.status == "in_progress",
                ExecutionTimeline.started_at < cutoff,
            )
            .order_by(desc(ExecutionTimeline.started_at))
        )
        if limit:
            stmt = stmt.limit(limit)

        timelines = (await db.execute(stmt)).scalars().all()
        counts["scanned"] = len(timelines)
        logger.info(
            "flight_backfill_scan",
            count=counts["scanned"],
            cutoff=cutoff.isoformat(),
            dry_run=dry_run,
        )

        for tl in timelines:
            try:
                summary = await _process_timeline(db, tl, dry_run=dry_run)
                if dry_run:
                    counts["would_update"] += 1
                else:
                    counts["updated"] += 1
                logger.info("flight_backfill_row", **summary)
            except Exception as exc:
                logger.error(
                    "flight_backfill_row_failed",
                    timeline_id=str(tl.id),
                    error=str(exc),
                )

        if not dry_run:
            await db.commit()

    await engine.dispose()
    return counts


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) preview only, no DB writes")
    mode.add_argument("--execute", action="store_true", help="apply the backfill")
    p.add_argument("--grace-minutes", type=int, default=5,
                   help="ignore timelines younger than this (default: 5)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap the number of timelines to process in one run")
    p.add_argument("--database-url", default=None,
                   help="override DATABASE_URL (must point at acp_flight_recorder)")
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    dry_run = not args.execute  # default to safe mode

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set and --database-url not provided", file=sys.stderr)
        return 2

    counts = asyncio.run(backfill(
        database_url=database_url,
        grace_minutes=args.grace_minutes,
        dry_run=dry_run,
        limit=args.limit,
    ))
    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"[{mode}] scanned={counts['scanned']} would_update={counts['would_update']} updated={counts['updated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
