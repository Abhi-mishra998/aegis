"""Unit tests — Phase 1 producer-side audit event validation.

Architect's critique (2026-06-24): the audit consumer used to absorb every
malformed event into ``acp:audit_stream:dlq``, hiding the bad caller behind
a generic "consumer DLQ depth" alert. The fix moves validation to the
producer:

  - Missing required field → emit_audit_event raises AuditValidationError
    AND lands the bad event in acp:audit_stream:producer_dlq with a
    stacktrace AND increments acp_audit_producer_dlq_total{reason="..."}.

These tests pin that behavior so it can never regress to "silently absorb at
the consumer".
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "a" * 64)
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret")

import pytest  # noqa: E402,I001
from sdk.common import audit_stream  # noqa: E402


@pytest.mark.asyncio
async def test_emit_audit_event_rejects_missing_tenant_id():
    """The most common bug: a caller forgets to thread tenant_id through.
    Pre-fix this landed in the consumer DLQ as a FK insert failure. Now it
    raises at the call site so the bad caller is debuggable from the
    stacktrace."""
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value=b"1-0")

    bad_event = {
        # tenant_id intentionally omitted
        "request_id": "req-123",
        "action":     "execute_tool",
        "decision":   "allow",
    }

    with pytest.raises(audit_stream.AuditValidationError) as exc_info:
        await audit_stream.emit_audit_event(redis, bad_event)

    assert exc_info.value.reason == "missing_field"
    # The producer DLQ must have been written so the bad event is
    # observable in operator dashboards.
    producer_dlq_call = [
        c for c in redis.xadd.await_args_list
        if c.args and c.args[0] == "acp:audit_stream:producer_dlq"
    ]
    assert producer_dlq_call, "bad event must land in producer DLQ"
    # The stream itself must NOT have received the bad event.
    main_stream_call = [
        c for c in redis.xadd.await_args_list
        if c.args and c.args[0] == "acp:audit_stream"
    ]
    assert not main_stream_call, "bad event must not reach the consumer stream"


@pytest.mark.asyncio
async def test_emit_audit_event_rejects_missing_request_id():
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value=b"1-0")

    bad_event = {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "action":    "execute_tool",
        "decision":  "allow",
        # request_id intentionally omitted
    }
    with pytest.raises(audit_stream.AuditValidationError) as exc_info:
        await audit_stream.emit_audit_event(redis, bad_event)
    assert exc_info.value.reason == "missing_field"


@pytest.mark.asyncio
async def test_emit_audit_event_rejects_non_uuid_tenant_id():
    """tenant_id="abc" looks like a real string but isn't a UUID. The audit
    consumer would crash on uuid.UUID("abc") parse. Reject at the producer."""
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value=b"1-0")

    bad_event = {
        "tenant_id":  "not-a-uuid",
        "request_id": "req-789",
        "action":     "execute_tool",
        "decision":   "allow",
    }
    with pytest.raises(audit_stream.AuditValidationError) as exc_info:
        await audit_stream.emit_audit_event(redis, bad_event)
    assert exc_info.value.reason == "invalid_tenant_uuid"


@pytest.mark.asyncio
async def test_emit_audit_event_accepts_system_sentinel():
    """``tenant_id="system"`` is the documented sentinel for tenant-less
    operator events (demo cleanup, internal heartbeats). It must NOT be
    rejected as a non-UUID — that would silently break the existing
    demo_cleanup_swept audit row."""
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value=b"1-0")

    ok_event = {
        "tenant_id":  "system",
        "request_id": "req-system-1",
        "action":     "demo_cleanup_swept",
        "decision":   "allow",
    }
    # No exception means accepted.
    await audit_stream.emit_audit_event(redis, ok_event)

    # The event should land on the main stream (NOT the producer DLQ).
    main_stream_call = [
        c for c in redis.xadd.await_args_list
        if c.args and c.args[0] == "acp:audit_stream"
    ]
    assert main_stream_call, "system events must reach the main stream"


@pytest.mark.asyncio
async def test_emit_audit_event_happy_path():
    """A well-formed event should flow through to the main stream with no
    drama — the producer-side validation is opt-OUT only for callers that
    construct bad events."""
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value=b"1-0")

    good_event = {
        "tenant_id":  "00000000-0000-0000-0000-000000000123",
        "request_id": "req-good",
        "action":     "execute_tool",
        "tool":       "tool.read_file",
        "decision":   "allow",
    }
    await audit_stream.emit_audit_event(redis, good_event)

    redis.xadd.assert_awaited()
    call_args = redis.xadd.await_args_list[0]
    assert call_args.args[0] == "acp:audit_stream"
    # The helper auto-fills ts so the validator passes.
    payload = call_args.args[1]
    assert payload["tenant_id"] == "00000000-0000-0000-0000-000000000123"
    assert payload["request_id"] == "req-good"
    assert "ts" in payload


@pytest.mark.asyncio
async def test_push_audit_event_wrapper_still_works():
    """Backwards compat: every existing call site uses push_audit_event(...).
    The kwargs-based wrapper must continue to land valid events on the
    main stream."""
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value=b"1-0")

    await audit_stream.push_audit_event(
        redis=redis,
        tenant_id="00000000-0000-0000-0000-000000000456",
        agent_id=None,
        action="user_login",
        decision="allow",
        request_id="req-wrapper",
    )

    main_stream_call = [
        c for c in redis.xadd.await_args_list
        if c.args and c.args[0] == "acp:audit_stream"
    ]
    assert main_stream_call, "wrapper must route through the main stream"
