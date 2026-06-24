"""Unit tests — Phase 2 demo lifecycle ordering.

The architect's critique (2026-06-24): demo tenants were being deleted while
their live-traffic worker was still emitting ``/execute`` calls. The audit
consumer would FK-fail on the missing tenant_id and drop the event in
``acp:audit_stream:dlq`` — visible on prod as ``audit_dlq=65``.

The fix enforces a strict per-tenant ordering in the cleanup-expired-demos
sweep:

    stop worker → confirm exit → drain audit stream → delete tenant

These tests assert that ordering with mocked subprocess + Redis. They are
the regression gate — any future refactor that breaks the order (back to
"delete first, worker eventually") fails these tests.

Phase 1 producer-side validation is covered in ``test_audit_emit_validation.py``.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

# Bootstrap env so sdk.common.config.ACPSettings() can instantiate at import
# time, mirroring the pattern in the other gateway tests.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "a" * 64)
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret")

import pytest  # noqa: E402,I001
from services.gateway.routers import demo as demo_router  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _OrderRecorder:
    """Records every phase of the lifecycle in order so the test can assert
    the cleanup runs ``stop → drain → delete`` and never the reverse."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def add(self, event: str) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Helpers under test — _terminate_demo_worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_demo_worker_no_pid_registered_returns_true(monkeypatch):
    """When no PID is stashed in Redis (worker never spawned, or already
    GC'd) the helper returns True so the caller continues into the drain +
    delete phases. Blocking forever on a missing PID would orphan tenants
    every cron run."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)

    ok = await demo_router._terminate_demo_worker("tenant-abc", redis)

    assert ok is True
    redis.get.assert_awaited_once_with("acp:demo_traffic:tenant-abc")


@pytest.mark.asyncio
async def test_terminate_demo_worker_sends_sigterm_then_succeeds(monkeypatch):
    """Happy path: PID is registered, SIGTERM lands, kernel reaps the process
    quickly so the existence-check (signal 0) raises ProcessLookupError on the
    first poll. Helper returns True + clears the PID stash."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value="4242")
    redis.delete = AsyncMock(return_value=1)

    signals_sent: list[tuple[int, int]] = []
    poll_count = {"n": 0}

    def fake_kill(pid: int, sig: int) -> None:
        signals_sent.append((pid, sig))
        if sig == 0:
            # First existence check after SIGTERM: process is gone.
            poll_count["n"] += 1
            if poll_count["n"] >= 1:
                raise ProcessLookupError()

    monkeypatch.setattr("os.kill", fake_kill)

    ok = await demo_router._terminate_demo_worker("tenant-abc", redis)

    assert ok is True
    # First call is SIGTERM, subsequent calls are existence checks (sig=0).
    assert signals_sent[0] == (4242, 15)  # SIGTERM == 15
    redis.delete.assert_awaited_once_with("acp:demo_traffic:tenant-abc")


@pytest.mark.asyncio
async def test_terminate_demo_worker_already_dead_returns_true(monkeypatch):
    """If the worker already died (external reaper, crash) the SIGTERM raises
    ProcessLookupError. Helper treats that as success — exit is confirmed."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value="999")
    redis.delete = AsyncMock(return_value=1)

    def fake_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr("os.kill", fake_kill)

    ok = await demo_router._terminate_demo_worker("tenant-x", redis)
    assert ok is True
    redis.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# Helpers under test — _wait_for_audit_drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_audit_drain_returns_true_when_no_pending(monkeypatch):
    """Empty XPENDING is the common case (the sweep usually fires long after
    the per-tenant TTL handler already drained). Helper returns True without
    any further work."""
    redis = MagicMock()
    redis.xpending_range = AsyncMock(return_value=[])

    ok = await demo_router._wait_for_audit_drain(redis, "tenant-1", timeout=1.0)

    assert ok is True
    redis.xpending_range.assert_awaited()


@pytest.mark.asyncio
async def test_wait_for_audit_drain_skips_other_tenants(monkeypatch):
    """XPENDING returns messages for many tenants. Only the count for THIS
    tenant matters — unrelated pending events from other demos must not
    block this delete."""
    redis = MagicMock()
    redis.xpending_range = AsyncMock(return_value=[
        {"message_id": b"1-0"},
        {"message_id": b"2-0"},
    ])
    # XRANGE returns the actual fields. Both pending events belong to a
    # DIFFERENT tenant, so the count for "tenant-target" is 0 and the
    # helper exits immediately.
    redis.xrange = AsyncMock(return_value=[
        (b"1-0", {b"tenant_id": b"tenant-other"}),
        (b"2-0", {b"tenant_id": b"tenant-other"}),
    ])

    ok = await demo_router._wait_for_audit_drain(redis, "tenant-target", timeout=1.0)

    assert ok is True


@pytest.mark.asyncio
async def test_wait_for_audit_drain_returns_true_on_xpending_failure(monkeypatch):
    """If Redis itself is unhealthy the helper logs + returns True rather
    than blocking the cron sweep forever. The orphan-event risk is logged
    so operators can see it."""
    redis = MagicMock()
    redis.xpending_range = AsyncMock(side_effect=ConnectionError("redis down"))

    ok = await demo_router._wait_for_audit_drain(redis, "tenant-z", timeout=1.0)
    assert ok is True


# ---------------------------------------------------------------------------
# End-to-end: cleanup_expired_demos ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_expired_demos_enforces_stop_drain_delete_order(monkeypatch):
    """The cardinal regression test: per tenant the cleanup must run

        _terminate_demo_worker → _wait_for_audit_drain → DELETE FROM tenants

    and never any other order. We patch each of the three with a recorder
    so we can assert the exact sequence."""
    recorder = _OrderRecorder()

    async def fake_terminate(tenant_id, redis):
        recorder.add(f"stop:{tenant_id}")
        return True

    async def fake_drain(redis, tenant_id, timeout=30.0):
        recorder.add(f"drain:{tenant_id}")
        return True

    monkeypatch.setattr(demo_router, "_terminate_demo_worker", fake_terminate)
    monkeypatch.setattr(demo_router, "_wait_for_audit_drain", fake_drain)

    # Stub the Redis factory so we don't try to connect.
    fake_redis = MagicMock()
    monkeypatch.setattr(
        "sdk.common.redis.get_redis_client",
        lambda *a, **kw: fake_redis,
    )

    # Stub the audit emit so we don't try to validate a fake tenant_id.
    async def fake_push(*a, **kw):
        return None

    monkeypatch.setattr(
        "sdk.common.audit_stream.push_audit_event", fake_push,
    )

    # Mock the DB: SELECT returns two expired tenant rows; DELETE just
    # records the order. The SQLAlchemy delete stmt repr does NOT carry
    # the bound tenant_id, so we use a counter to attribute each DELETE
    # to its position in the expired list — the route deletes them in
    # iteration order.
    expired = ["tenant-A", "tenant-B"]

    select_result = MagicMock()
    select_result.all = MagicMock(return_value=[(tid,) for tid in expired])

    delete_counter = {"n": 0}

    db = MagicMock()
    async def fake_execute(stmt):
        # First call is the SELECT (only one in this route). Every
        # subsequent execute() is a per-tenant DELETE, in the same order
        # as the SELECT row list.
        stmt_str = str(stmt).lower()
        if stmt_str.startswith("select") or "select " in stmt_str[:20]:
            return select_result
        # Per-tenant DELETE — attribute by iteration order.
        tid = expired[delete_counter["n"]]
        delete_counter["n"] += 1
        recorder.add(f"delete:{tid}")
        return MagicMock()

    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    request = MagicMock()
    request.headers = {"X-Request-ID": "req-test"}

    result = await demo_router.cleanup_expired_demos(
        request=request, db=db, _auth="mesh-tester",
    )

    # Assert the exact ordering per tenant.
    expected = [
        "stop:tenant-A", "drain:tenant-A", "delete:tenant-A",
        "stop:tenant-B", "drain:tenant-B", "delete:tenant-B",
    ]
    assert recorder.events == expected, (
        f"lifecycle ran out of order:\n  expected: {expected}\n  actual:   {recorder.events}"
    )

    assert result["success"] is True
    assert sorted(result["data"]["swept_ids"]) == expired
    assert result["data"]["skipped"] == []


@pytest.mark.asyncio
async def test_cleanup_skips_tenant_when_worker_exit_unconfirmed(monkeypatch):
    """If we cannot confirm the worker exited, the tenant MUST NOT be
    deleted — the worker would keep emitting orphan events. The orphan
    tenant is preferable: visible on the next sweep, no DLQ pressure."""
    recorder = _OrderRecorder()

    async def fake_terminate(tenant_id, redis):
        recorder.add(f"stop:{tenant_id}")
        # Pretend we couldn't confirm the kill.
        return False

    async def fake_drain(redis, tenant_id, timeout=30.0):
        recorder.add(f"drain:{tenant_id}")
        return True

    monkeypatch.setattr(demo_router, "_terminate_demo_worker", fake_terminate)
    monkeypatch.setattr(demo_router, "_wait_for_audit_drain", fake_drain)
    fake_redis = MagicMock()
    monkeypatch.setattr(
        "sdk.common.redis.get_redis_client",
        lambda *a, **kw: fake_redis,
    )

    async def fake_push(*a, **kw):
        return None

    monkeypatch.setattr("sdk.common.audit_stream.push_audit_event", fake_push)

    select_result = MagicMock()
    select_result.all = MagicMock(return_value=[("tenant-zombie",)])

    db = MagicMock()
    async def fake_execute(stmt):
        stmt_str = str(stmt)
        if "select" in stmt_str.lower():
            return select_result
        recorder.add("delete:tenant-zombie")
        return MagicMock()

    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    request = MagicMock()
    request.headers = {}

    result = await demo_router.cleanup_expired_demos(
        request=request, db=db, _auth="mesh-tester",
    )

    # Only the stop phase ran — no drain, no delete. The orphan tenant lives
    # to fight another day (next cron will retry).
    assert "stop:tenant-zombie" in recorder.events
    assert "drain:tenant-zombie" not in recorder.events
    assert "delete:tenant-zombie" not in recorder.events
    assert result["data"]["swept_ids"] == []
    assert any(
        s.get("reason") == "worker_exit_unconfirmed"
        for s in result["data"]["skipped"]
    )


