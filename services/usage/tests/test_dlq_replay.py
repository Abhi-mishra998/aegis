"""Unit tests for the billing DLQ replay worker.

These exercise the classification + replay path without a live Redis. We
substitute a minimal in-memory fake that implements just the surface
``dlq_replay`` actually uses: ``lpop``, ``rpush``, ``lpush``.

The Prometheus counter is read directly off the global registry so we can
assert the right outcome label was incremented.

Mirrors services/audit/tests/test_dlq_replay.py — same 5-case shape adapted
for billing's list-based DLQ.
"""
from __future__ import annotations

import json
import uuid

import pytest

from sdk.utils import BILLING_DLQ_REPLAY_TOTAL
from services.usage import dlq_replay


class _FakeRedis:
    """In-memory async fake exposing the list methods replay uses."""

    def __init__(self) -> None:
        # lists keyed by name → list of bytes (one per entry, head at index 0)
        self.lists: dict[str, list[bytes]] = {}

    async def lpop(self, key: str):
        entries = self.lists.get(key, [])
        if not entries:
            return None
        return entries.pop(0)

    async def rpush(self, key: str, *values: bytes | str) -> int:
        bucket = self.lists.setdefault(key, [])
        for v in values:
            bucket.append(v.encode() if isinstance(v, str) else v)
        return len(bucket)

    async def lpush(self, key: str, *values: bytes | str) -> int:
        bucket = self.lists.setdefault(key, [])
        for v in values:
            bucket.insert(0, v.encode() if isinstance(v, str) else v)
        return len(bucket)


def _make_dlq_entry(
    *,
    tenant_id: str | None = None,
    error: str = "connection refused",
    retry_count: int = 0,
    action: str = "allow",
    agent_id: str | None = None,
) -> str:
    """Build a DLQ entry mirroring the gateway's `_persist_billing_dlq` shape."""
    if tenant_id is None:
        tenant_id = str(uuid.uuid4())
    payload = {
        "tenant_id":       tenant_id,
        "agent_id":        agent_id or str(uuid.uuid4()),
        "tool":            "db.query",
        "units":           1,
        "cost":            0.001,
        "audit_id":        "aud_" + uuid.uuid4().hex[:8],
        "idempotency_key": "idem_" + uuid.uuid4().hex[:8],
    }
    return json.dumps({
        "payload":     payload,
        "action":      action,
        "retry_count": retry_count,
        "reason":      error,
    })


def _counter_value(outcome: str) -> float:
    """Read the Prometheus counter for one outcome label."""
    return BILLING_DLQ_REPLAY_TOTAL.labels(outcome=outcome)._value.get()  # noqa: SLF001


@pytest.mark.asyncio
async def test_connection_error_replays_back_onto_retry_queue() -> None:
    redis = _FakeRedis()
    entry = _make_dlq_entry(error="connection refused", retry_count=2)
    await redis.rpush(dlq_replay._DLQ_KEY, entry)

    before_replayed = _counter_value("replayed")
    n = await dlq_replay._one_pass(redis)

    assert n == 1
    # Retry queue got the replayed event.
    retry_q = redis.lists.get(dlq_replay._RETRY_QUEUE_KEY, [])
    assert len(retry_q) == 1
    replayed_data = json.loads(retry_q[0].decode())
    # retry_count is incremented from 2 → 3 on the replay.
    assert replayed_data["retry_count"] == 3
    # Original payload is preserved on the replayed event.
    original = json.loads(entry)
    assert replayed_data["payload"]["tenant_id"] == original["payload"]["tenant_id"]
    assert replayed_data["payload"]["audit_id"] == original["payload"]["audit_id"]
    assert replayed_data["action"] == "allow"
    # DLQ entry was consumed (lpop is destructive).
    assert redis.lists.get(dlq_replay._DLQ_KEY, []) == []
    # Counter incremented for outcome="replayed".
    assert _counter_value("replayed") == before_replayed + 1


@pytest.mark.asyncio
async def test_foreign_key_violation_moves_to_permanently_failed() -> None:
    redis = _FakeRedis()
    entry = _make_dlq_entry(
        error="psycopg2.errors.ForeignKeyViolation: tenant not found",
        retry_count=0,
    )
    await redis.rpush(dlq_replay._DLQ_KEY, entry)

    before_pf = _counter_value("permanently_failed")
    n = await dlq_replay._one_pass(redis)

    assert n == 1
    # No re-push onto the retry queue.
    assert redis.lists.get(dlq_replay._RETRY_QUEUE_KEY, []) == []
    # Promoted to permanently_failed.
    pf = redis.lists.get(dlq_replay._PERMANENTLY_FAILED_KEY, [])
    assert len(pf) == 1
    pf_data = json.loads(pf[0].decode())
    assert pf_data["replay_reason"] == "non_recoverable_error_class"
    # DLQ entry was consumed.
    assert redis.lists.get(dlq_replay._DLQ_KEY, []) == []
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
    await redis.rpush(dlq_replay._DLQ_KEY, entry)

    before_pf = _counter_value("permanently_failed")
    n = await dlq_replay._one_pass(redis)

    assert n == 1
    # No replay attempt.
    assert redis.lists.get(dlq_replay._RETRY_QUEUE_KEY, []) == []
    # Promoted to permanently_failed with the max-retries reason.
    pf = redis.lists.get(dlq_replay._PERMANENTLY_FAILED_KEY, [])
    assert len(pf) == 1
    pf_data = json.loads(pf[0].decode())
    assert pf_data["replay_reason"] == "max_retries_exceeded"
    assert pf_data["retry_count"] == dlq_replay.MAX_RETRIES
    # DLQ entry was consumed.
    assert redis.lists.get(dlq_replay._DLQ_KEY, []) == []
    # Counter incremented for outcome="permanently_failed".
    assert _counter_value("permanently_failed") == before_pf + 1


@pytest.mark.asyncio
async def test_empty_dlq_pass_returns_zero() -> None:
    redis = _FakeRedis()
    n = await dlq_replay._one_pass(redis)
    assert n == 0
    assert redis.lists == {}


@pytest.mark.asyncio
async def test_classify_error_dispatches_by_marker_or_count() -> None:
    # Substring markers (case-insensitive) → permanently_failed
    assert dlq_replay._classify_error("ForeignKeyViolation: tenant_id", 0) == "permanently_failed"
    assert dlq_replay._classify_error("tenant not found in db", 0) == "permanently_failed"
    # The gateway tags retry-exhausted DLQ entries with this reason so we treat
    # the marker as terminal even when retry_count looks low (the live worker
    # already gave up).
    assert dlq_replay._classify_error("max_retries_exhausted", 0) == "permanently_failed"
    # Recoverable network errors → replay (when retry budget remains)
    assert dlq_replay._classify_error("connection refused", 1) == "replay"
    assert dlq_replay._classify_error("read timeout after 5s", 2) == "replay"
    assert dlq_replay._classify_error("IntegrityError on stale write", 0) == "replay"
    # retry_count at the cap → permanently_failed regardless of error class
    assert dlq_replay._classify_error("connection refused", dlq_replay.MAX_RETRIES) == "permanently_failed"
