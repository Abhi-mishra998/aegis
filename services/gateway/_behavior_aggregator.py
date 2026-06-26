"""
L3 Behavior Aggregator — sliding-window detectors for slow-exfiltration
and runaway-loop attack patterns that L2 (per-call action semantics) misses.

L2 catches: SELECT 50000 patients in ONE call.
L3 catches: SELECT 100 patients × 500 calls in 1h. Each call looks safe
            individually; the cumulative pattern is the attack.

Per-minute bucket design — chosen so the hot path is O(1) write + O(60)
read, and individual buckets expire naturally rather than scanning a
sorted set on every call.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis as _Redis

# Window sizes — tune via env later. The threshold-vs-risk check stays
# in local_action_semantics.py + action_semantics_deny.rego so the deny
# semantics line up with the per-call rule.
ROW_WINDOW_MINUTES   = 60
FAILURE_WINDOW_MINUTES = 5
RUNAWAY_FAILURE_THRESHOLD = 50
# GAP-4 2026-06-15 — tighter quarantine specifically for bulk-PII denies.
# Generic shell-error retries can churn 50× / 5min on a flaky tool; bulk-PII
# escalations cannot. The agent is mass-attempting a single class of data
# exfil and has lost the trust budget.
#
# matrix-26 P1-3 (2026-06-26) — bumped from 3 → 15. Real evidence: the live
# matrix-26 Phase 6 SQLi probes legitimately tripped this at 3 and then the
# Phase 1 benign analytics queries on the same agent came back 403. A real
# batch-analytics workload (e.g., a finance reconciliation agent running 5
# unbounded SELECTs in normal operation) was hitting it too. 15 in 5min is
# still a strong attack signal but no longer false-quarantines normal
# analytical workloads. If a true mass-exfil pattern emerges, the cumulative
# risk pipeline + per-finding behavior baseline will catch it earlier than
# this counter does anyway.
BULK_PII_QUARANTINE_THRESHOLD = 15

_TABLE_RE = re.compile(r"\bfrom\s+([a-z_][a-z0-9_]*)\b", re.IGNORECASE)


def extract_table_norm(query_norm: str) -> str:
    """Pull the first `FROM <table>` target from a normalized SQL string."""
    if not query_norm:
        return ""
    m = _TABLE_RE.search(query_norm)
    return m.group(1).lower() if m else ""


def _now_minute() -> int:
    return int(time.time()) // 60


async def record_and_sum_rows(
    redis: "_Redis",
    tenant_id: uuid.UUID | str,
    agent_id: uuid.UUID | str,
    table: str,
    row_count: int,
    window_minutes: int = ROW_WINDOW_MINUTES,
) -> int:
    """
    Add row_count to the current minute bucket for (tenant, agent, table) and
    return the sum across the last `window_minutes` buckets. The sum INCLUDES
    the current row_count so the caller can compare directly against the
    PII threshold.
    """
    if not table or row_count <= 0:
        return 0
    minute = _now_minute()
    cur_key = f"acp:rows:{tenant_id}:{agent_id}:{table}:{minute}"

    # Record current minute
    pipe = redis.pipeline()
    pipe.incrby(cur_key, row_count)
    pipe.expire(cur_key, window_minutes * 60 + 120)
    await pipe.execute()

    # Sum the last `window_minutes` buckets including the current one.
    bucket_keys = [
        f"acp:rows:{tenant_id}:{agent_id}:{table}:{minute - i}"
        for i in range(window_minutes)
    ]
    values = await redis.mget(*bucket_keys)
    return sum(int(v or 0) for v in values)


async def record_bulk_pii_attempt(
    redis: "_Redis",
    tenant_id: uuid.UUID | str,
    agent_id: uuid.UUID | str,
) -> int:
    """GAP-4 2026-06-15 — separate counter for bulk-PII escalates/denies so
    the quarantine threshold can be different from the generic shell-loop
    threshold. Returns the count in the last FAILURE_WINDOW_MINUTES.
    """
    minute = _now_minute()
    cur_key = f"acp:bulkpii:{tenant_id}:{agent_id}:{minute}"
    pipe = redis.pipeline()
    pipe.incr(cur_key)
    pipe.expire(cur_key, FAILURE_WINDOW_MINUTES * 60 + 60)
    await pipe.execute()
    bucket_keys = [
        f"acp:bulkpii:{tenant_id}:{agent_id}:{minute - i}"
        for i in range(FAILURE_WINDOW_MINUTES)
    ]
    values = await redis.mget(*bucket_keys)
    return sum(int(v or 0) for v in values)


async def record_failure(
    redis: "_Redis",
    tenant_id: uuid.UUID | str,
    agent_id: uuid.UUID | str,
    tool: str,
) -> int:
    """
    Record one failure for (agent, tool) and return the cumulative failure
    count in the last FAILURE_WINDOW_MINUTES. The caller decides whether the
    threshold has been breached (kept in this module so the threshold is one
    knob, not duplicated at every call site).
    """
    if not tool:
        return 0
    minute = _now_minute()
    cur_key = f"acp:fail:{tenant_id}:{agent_id}:{tool}:{minute}"

    pipe = redis.pipeline()
    pipe.incr(cur_key)
    pipe.expire(cur_key, FAILURE_WINDOW_MINUTES * 60 + 60)
    await pipe.execute()

    bucket_keys = [
        f"acp:fail:{tenant_id}:{agent_id}:{tool}:{minute - i}"
        for i in range(FAILURE_WINDOW_MINUTES)
    ]
    values = await redis.mget(*bucket_keys)
    return sum(int(v or 0) for v in values)


async def is_quarantined(
    redis: "_Redis",
    tenant_id: uuid.UUID | str,
    agent_id: uuid.UUID | str,
) -> tuple[bool, str]:
    """Return (quarantined, reason). Reason is the trigger string, '' if not set."""
    key = f"acp:quarantine:{tenant_id}:{agent_id}"
    raw = await redis.get(key)
    if not raw:
        return False, ""
    return True, raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)


async def quarantine_agent(
    redis: "_Redis",
    tenant_id: uuid.UUID | str,
    agent_id: uuid.UUID | str,
    reason: str,
    ttl_seconds: int = 86_400,
) -> None:
    """Mark agent quarantined; default 24h auto-clear so operator can
    re-enable without DB write."""
    key = f"acp:quarantine:{tenant_id}:{agent_id}"
    await redis.setex(key, ttl_seconds, reason)


async def release_quarantine(
    redis: "_Redis",
    tenant_id: uuid.UUID | str,
    agent_id: uuid.UUID | str,
) -> None:
    key = f"acp:quarantine:{tenant_id}:{agent_id}"
    await redis.delete(key)
