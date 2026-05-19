"""Sprint 3.5 — oldest-age + queue-depth helpers.

The shape of these helpers matters more than the speed: each returns
``(depth, oldest_age_seconds)`` tuples so a refresh loop can update
two related gauges with one call. All functions are tolerant of
empty / missing structures — never raise on "queue not yet created".

Why oldest-age beats depth:

* A list of 2 entries that's been sitting for an hour is a stuck
  outbox; a list of 200 entries that's draining at 100 req/s is fine.
* Depth alone can't distinguish them. Age can.

Three queue topologies in ACP:

* Redis Stream  → XINFO STREAM gives `first-entry`, whose ID encodes
  millis-since-epoch. We parse the millis and diff against now().
* Redis List    → LRANGE 0 0 gives the head element. We require entry
  shape `{ts: int|float, ...}` and parse the ts field. ACP's
  billing-DLQ entries already include `ts` (best-effort).
* PG outbox     → `SELECT EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))
  FROM pending_usage_events WHERE status='pending'`.

A refresh loop in the gateway main lifespan ticks these every 30
seconds and writes the gauges declared in `sdk/utils.py`.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any


# Redis Stream IDs look like `<millis>-<seq>`; the millis are the
# server-epoch time at insert. We don't trust the ID format blindly —
# malformed IDs return 0 age (caller's choice not to alert on noise).
_STREAM_ID_PATTERN = re.compile(r"^(\d+)-\d+$")


async def stream_oldest_age_and_depth(
    redis: Any, key: str, *, now_epoch: float | None = None,
) -> tuple[int, int]:
    """Return ``(depth, oldest_age_seconds)`` for a Redis Stream.

    Returns ``(0, 0)`` when the stream doesn't exist or is empty —
    callers expose this as ``gauge.set(0)`` so /metrics never carries
    a stale value across a queue's lifetime.
    """
    try:
        info = await redis.xinfo_stream(key)
    except Exception:
        return 0, 0
    depth = int(_field(info, "length") or 0)
    if depth == 0:
        return 0, 0
    first = _field(info, "first-entry") or _field(info, b"first-entry")
    if not first:
        return depth, 0
    # first is `[entry_id, [k1, v1, k2, v2, ...]]` or `(entry_id, {...})`.
    entry_id = first[0] if isinstance(first, (list, tuple)) else first
    if isinstance(entry_id, (bytes, bytearray)):
        entry_id = entry_id.decode("ascii", errors="replace")
    m = _STREAM_ID_PATTERN.match(str(entry_id))
    if not m:
        return depth, 0
    inserted_at = int(m.group(1)) / 1000.0
    now = now_epoch if now_epoch is not None else time.time()
    return depth, max(0, int(now - inserted_at))


async def list_oldest_age_and_depth(
    redis: Any, key: str, *, now_epoch: float | None = None,
) -> tuple[int, int]:
    """Return ``(depth, oldest_age_seconds)`` for a Redis List.

    Assumes producers push JSON entries with a `ts` field (epoch seconds).
    A missing/garbled `ts` yields age=0 — the queue depth is still
    surfaced so the operator sees the buildup even when the age signal
    is unreliable.
    """
    try:
        depth = int(await redis.llen(key) or 0)
    except Exception:
        return 0, 0
    if depth == 0:
        return 0, 0
    # LRANGE -1 -1 = the LAST element. ACP producers RPUSH, so the
    # oldest entry is at index 0. We try LRANGE 0 0 first — if that
    # returns nothing (a producer that LPUSHes), we fall back to the
    # tail end.
    try:
        raw = await redis.lrange(key, 0, 0)
    except Exception:
        return depth, 0
    if not raw:
        return depth, 0
    head = raw[0]
    if isinstance(head, (bytes, bytearray)):
        head = head.decode("utf-8", errors="replace")
    try:
        entry = json.loads(head) if isinstance(head, str) else head
    except Exception:
        return depth, 0
    ts = None
    if isinstance(entry, dict):
        ts = entry.get("ts") or entry.get("timestamp") or entry.get("created_at")
    if not isinstance(ts, (int, float)):
        # Try to parse an ISO timestamp string.
        if isinstance(ts, str):
            from datetime import datetime, timezone
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = None
        if not isinstance(ts, (int, float)):
            return depth, 0
    now = now_epoch if now_epoch is not None else time.time()
    return depth, max(0, int(now - ts))


def _field(info: Any, key: Any) -> Any:
    """`xinfo_stream` returns either a dict (`decode_responses=True`)
    or a flat key/value list (binary mode). Handle both."""
    if isinstance(info, dict):
        return info.get(key) or info.get(key.encode() if isinstance(key, str) else key.decode())
    if isinstance(info, (list, tuple)):
        # Flat alternating: [k, v, k, v, ...]
        it = iter(info)
        for k in it:
            v = next(it, None)
            kk = k.decode() if isinstance(k, (bytes, bytearray)) else k
            target = key if not isinstance(key, (bytes, bytearray)) else key.decode()
            if kk == target:
                return v
    return None


async def outbox_pending_age_seconds(audit_conn, *, table: str = "pending_usage_events") -> int:
    """SELECT MIN(created_at) for status='pending' in the audit outbox.

    `audit_conn` is a psycopg2 / asyncpg-style sync connection — we use
    the synchronous cursor API so this helper composes with the
    existing scripts/* connection-by-DSN pattern.
    """
    try:
        with audit_conn.cursor() as cur:
            cur.execute(
                f"SELECT EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) "
                f"FROM {table} WHERE status='pending'"
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0
