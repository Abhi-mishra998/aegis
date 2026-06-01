"""Outbox + usage-events retention pruner.

Run on a cron (daily 03:00 UTC recommended). Deletes rows whose work is
done — both `pending_usage_events` rows whose status is "completed" and
who were processed more than RETENTION_DAYS ago.

The audit chain itself is NEVER touched here; `audit_logs` is the immutable
ledger and has its own retention story (partitioning under sprint-3.7).

Exit codes:
    0  pruning complete (may have pruned 0 rows)
    1  database connection failure or partial completion

Metrics (printed as Prometheus-style stdout lines for log scrapers):
    acp_outbox_pruned_rows{table="pending_usage_events"} <N>
    acp_outbox_table_size_bytes{table="pending_usage_events"} <bytes>
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# pending_usage_events kept for 14 days post-completion — long enough to
# investigate billing disputes, short enough to bound table growth.
RETENTION_DAYS = int(os.environ.get("OUTBOX_RETENTION_DAYS", "14"))

# Prune in batches so a 6-month backlog doesn't lock the table.
BATCH_SIZE = int(os.environ.get("OUTBOX_PRUNE_BATCH_SIZE", "10000"))


async def _prune_pending_usage_events(database_url: str) -> tuple[int, int]:
    """Return (deleted_rows, table_size_bytes). Raises on connect failure."""
    engine = create_async_engine(database_url, future=True)
    try:
        total_deleted = 0
        async with engine.begin() as conn:
            while True:
                # Use a CTE so the batch limit is enforced inside Postgres.
                result = await conn.execute(text("""
                    WITH stale AS (
                        SELECT id FROM pending_usage_events
                        WHERE status = 'completed'
                          AND processed_at < now() - (:days || ' days')::interval
                        LIMIT :batch
                    )
                    DELETE FROM pending_usage_events
                    USING stale
                    WHERE pending_usage_events.id = stale.id
                """), {"days": RETENTION_DAYS, "batch": BATCH_SIZE})
                deleted = result.rowcount or 0
                total_deleted += deleted
                if deleted < BATCH_SIZE:
                    break

            size_result = await conn.execute(
                text("SELECT pg_table_size('pending_usage_events')")
            )
            table_size = int(size_result.scalar_one() or 0)
        return total_deleted, table_size
    finally:
        await engine.dispose()


async def main() -> int:
    database_url = os.environ.get("AUDIT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("error: AUDIT_DATABASE_URL or DATABASE_URL must be set", file=sys.stderr)
        return 1
    try:
        deleted, size_bytes = await _prune_pending_usage_events(database_url)
    except Exception as exc:
        print(f"error: prune failed: {exc}", file=sys.stderr)
        return 1
    print(f"acp_outbox_pruned_rows{{table=\"pending_usage_events\"}} {deleted}")
    print(f"acp_outbox_table_size_bytes{{table=\"pending_usage_events\"}} {size_bytes}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
