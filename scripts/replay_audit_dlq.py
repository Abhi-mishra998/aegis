#!/usr/bin/env python3
"""
Replay the audit DLQ.

Reads every entry from `acp:audit_stream:dlq`, extracts the payload,
re-publishes it onto `acp:audit_stream` so the audit consumer can ingest it.
Deletes successfully-replayed entries from the DLQ.

Use after fixing the parser bug that caused the events to land in the DLQ.

Usage:
    docker exec -e REDIS_URL=redis://redis:6379/0 acp_audit \\
        python -m scripts.replay_audit_dlq

or, from the host:
    .venv/bin/python scripts/replay_audit_dlq.py
"""
from __future__ import annotations

import asyncio
import json
import os

from redis.asyncio import Redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DLQ_KEY    = "acp:audit_stream:dlq"
MAIN_KEY   = "acp:audit_stream"
BATCH_SIZE = 200


async def replay() -> None:
    redis = Redis.from_url(REDIS_URL, decode_responses=False)
    replayed = 0
    skipped = 0
    last_id = "-"
    while True:
        rows = await redis.xrange(DLQ_KEY, min=last_id, max="+", count=BATCH_SIZE)
        if not rows:
            break
        new_last = last_id
        for msg_id, fields in rows:
            new_last = msg_id
            decoded = {
                (k.decode() if isinstance(k, bytes) else k):
                (v.decode() if isinstance(v, bytes) else v)
                for k, v in fields.items()
            }
            try:
                payload = json.loads(decoded.get("payload", "{}"))
                if not payload.get("tenant_id"):
                    skipped += 1
                    continue
                await redis.xadd(MAIN_KEY, {
                    k: (json.dumps(v) if not isinstance(v, str) else v)
                    for k, v in payload.items()
                }, maxlen=50_000, approximate=True)
                await redis.xdel(DLQ_KEY, msg_id)
                replayed += 1
            except Exception as exc:
                print(f"  ⚠️  skipping {msg_id!r}: {exc}")
                skipped += 1
        # Increment so XRANGE doesn't re-return the same row
        last_id = (
            new_last.decode() if isinstance(new_last, bytes) else new_last
        )
        # XRANGE uses inclusive min; bump the suffix
        last_id = last_id + "-1" if "-" not in last_id else last_id

    print(f"\n✅ Replay complete: {replayed} re-published, {skipped} skipped.")
    print(f"   Remaining DLQ depth: {await redis.xlen(DLQ_KEY)}")
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(replay())
