"""Unit tests for the audit DLQ replay worker.

These exercise the classification + replay path without a live Redis. We
substitute a minimal in-memory fake that implements just the surface
``dlq_replay`` actually uses: ``xrange``, ``xadd``, ``xdel``.

The Prometheus counter is read directly off the global registry so we can
assert the right outcome label was incremented.
"""
from __future__ import annotations

import json
import uuid

import pytest

from sdk.utils import AUDIT_DLQ_REPLAY_TOTAL
from services.audit import dlq_replay


class _FakeRedis:
    """In-memory async fake exposing the stream methods replay uses."""

    def __init__(self) -> None:
        # streams keyed by name → list of (id, fields-dict)
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"{self._counter}-0"

    async def xrange(self, stream: str, min_id: str, max_id: str, count: int | None = None):
        entries = self.streams.get(stream, [])
        # min_id="-" / max_id="+" → all entries; this fake doesn't filter
        # by id range because every test only uses "-" / "+".
        out = []
        for entry_id, fields in entries:
            # Encode keys + values as bytes to mirror redis-py defaults.
            encoded = {
                k.encode() if isinstance(k, str) else k:
                v.encode() if isinstance(v, str) else v
                for k, v in fields.items()
            }
            out.append((entry_id.encode(), encoded))
            if count is not None and len(out) >= count:
                break
        return out

    async def xadd(self, stream: str, fields: dict):
        entry_id = self._next_id()
        normalized = {
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in fields.items()
        }
        self.streams.setdefault(stream, []).append((entry_id, normalized))
        return entry_id.encode()

    async def xdel(self, stream: str, *ids: str):
        target_ids = set(ids)
        entries = self.streams.get(stream, [])
        before = len(entries)
        self.streams[stream] = [
            (eid, fields) for eid, fields in entries if eid not in target_ids
        ]
        return before - len(self.streams[stream])


def _make_dlq_entry(*, tenant_id: str | None = None, error: str = "connection refused",
                    retry_count: int = 0, action: str = "execute_tool",
                    agent_id: str | None = None) -> dict[str, str]:
    """Build a DLQ entry mirroring the consumer's xadd shape."""
    if tenant_id is None:
        tenant_id = str(uuid.uuid4())
    payload = {
        "tenant_id": tenant_id,
        "agent_id":  agent_id or str(uuid.uuid4()),
        "action":    action,
        "tool":      "db.query",
        "decision":  "allow",
        "request_id": "req_" + uuid.uuid4().hex[:8],
    }
    if retry_count:
        payload["retry_count"] = retry_count
    return {
        "identity": "1234-0",
        "payload":  json.dumps(payload),
        "error":    error,
        "ts":       "0.0",
    }


def _counter_value(outcome: str) -> float:
    """Read the Prometheus counter for one outcome label."""
    return AUDIT_DLQ_REPLAY_TOTAL.labels(outcome=outcome)._value.get()  # noqa: SLF001


@pytest.mark.asyncio
async def test_connection_error_replays_back_onto_live_stream() -> None:
    redis = _FakeRedis()
    entry = _make_dlq_entry(error="connection refused", retry_count=2)
    await redis.xadd(dlq_replay._DLQ_KEY, entry)

    before_replayed = _counter_value("replayed")
    n = await dlq_replay._one_pass(redis)

    assert n == 1
    # Live stream got the replayed event.
    live = redis.streams.get(dlq_replay._LIVE_STREAM_KEY, [])
    assert len(live) == 1
    new_fields = live[0][1]
    # retry_count is incremented from 2 → 3 on the replay.
    assert new_fields.get("retry_count") == "3"
    # tenant_id + action + tool are preserved on the replayed event.
    original_payload = json.loads(entry["payload"])
    assert new_fields.get("tenant_id") == original_payload["tenant_id"]
    assert new_fields.get("action") == "execute_tool"
    assert new_fields.get("tool") == "db.query"
    # DLQ entry was removed.
    assert redis.streams.get(dlq_replay._DLQ_KEY, []) == []
    # Counter incremented for outcome="replayed".
    assert _counter_value("replayed") == before_replayed + 1


@pytest.mark.asyncio
async def test_foreign_key_violation_moves_to_permanently_failed() -> None:
    redis = _FakeRedis()
    entry = _make_dlq_entry(
        error="psycopg2.errors.ForeignKeyViolation: tenant not found",
        retry_count=0,
    )
    await redis.xadd(dlq_replay._DLQ_KEY, entry)

    before_pf = _counter_value("permanently_failed")
    n = await dlq_replay._one_pass(redis)

    assert n == 1
    # No xadd onto the live stream.
    assert redis.streams.get(dlq_replay._LIVE_STREAM_KEY, []) == []
    # Promoted to permanently_failed.
    pf = redis.streams.get(dlq_replay._PERMANENTLY_FAILED_KEY, [])
    assert len(pf) == 1
    pf_fields = pf[0][1]
    assert pf_fields.get("reason") == "non_recoverable_error_class"
    # DLQ entry was removed.
    assert redis.streams.get(dlq_replay._DLQ_KEY, []) == []
    # Counter incremented for outcome="permanently_failed".
    assert _counter_value("permanently_failed") == before_pf + 1


@pytest.mark.asyncio
async def test_retry_exceeded_moves_to_permanently_failed_regardless_of_error() -> None:
    redis = _FakeRedis()
    # Error is the normally-recoverable "connection refused" but retry_count is
    # at the cap, so the replay worker must give up regardless.
    entry = _make_dlq_entry(
        error="connection refused",
        retry_count=dlq_replay.MAX_RETRIES,
    )
    await redis.xadd(dlq_replay._DLQ_KEY, entry)

    before_pf = _counter_value("permanently_failed")
    n = await dlq_replay._one_pass(redis)

    assert n == 1
    # No replay attempt.
    assert redis.streams.get(dlq_replay._LIVE_STREAM_KEY, []) == []
    # Promoted to permanently_failed with the max-retries reason.
    pf = redis.streams.get(dlq_replay._PERMANENTLY_FAILED_KEY, [])
    assert len(pf) == 1
    pf_fields = pf[0][1]
    assert pf_fields.get("reason") == "max_retries_exceeded"
    assert pf_fields.get("retry_count") == str(dlq_replay.MAX_RETRIES)
    # DLQ entry was removed.
    assert redis.streams.get(dlq_replay._DLQ_KEY, []) == []
    # Counter incremented for outcome="permanently_failed".
    assert _counter_value("permanently_failed") == before_pf + 1


@pytest.mark.asyncio
async def test_empty_dlq_pass_returns_zero() -> None:
    redis = _FakeRedis()
    n = await dlq_replay._one_pass(redis)
    assert n == 0
    assert redis.streams == {}


@pytest.mark.asyncio
async def test_classify_error_dispatches_by_marker_or_count() -> None:
    # Substring markers (case-insensitive) → permanently_failed
    assert dlq_replay._classify_error("ForeignKeyViolation: tenant_id", 0) == "permanently_failed"
    assert dlq_replay._classify_error("tenant not found in db", 0) == "permanently_failed"
    # Recoverable network errors → replay (when retry budget remains)
    assert dlq_replay._classify_error("connection refused", 1) == "replay"
    assert dlq_replay._classify_error("read timeout after 5s", 2) == "replay"
    assert dlq_replay._classify_error("IntegrityError on stale write", 0) == "replay"
    # retry_count at the cap → permanently_failed regardless of error class
    assert dlq_replay._classify_error("connection refused", dlq_replay.MAX_RETRIES) == "permanently_failed"
