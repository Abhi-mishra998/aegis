"""Unit tests — N2 + N19 SSE security fixes.

N2: the SSE event-stream generator must verify the JSON payload's
``tenant_id`` field matches the authenticated client's tenant before
relaying a message. Any internal service with Redis access can
``PUBLISH acp:events:<otherTenant>`` and the old generator relayed it
blind — channel name was the only isolation primitive.

N19: when ``?agent_id=<uuid>`` is supplied the handler must verify the
agent record exists AND belongs to the authenticated tenant BEFORE
subscribing to the per-agent channel. Previously any caller could
subscribe to events for any agent UUID without an ownership check.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Bootstrap minimum env so sdk.common.config.ACPSettings() can instantiate
# at import time, mirroring the pattern in test_dashboard_router.py.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "a" * 64)
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret")

import pytest  # noqa: E402,I001
from services.gateway import main as gw_main  # noqa: E402,I001


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePubSub:
    """Minimal pubsub stand-in. ``queue`` is a list of (type, data, channel)
    tuples consumed in order by ``get_message``; once exhausted ``get_message``
    returns None so the SSE generator's heartbeat path runs."""

    def __init__(self, queue: list[dict]) -> None:
        self._queue = list(queue)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed.extend(channels)

    async def get_message(
        self, *, ignore_subscribe_messages: bool = True, timeout: float = 1.0,
    ):
        if self._queue:
            return self._queue.pop(0)
        return None

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        self.closed = True


class _FakeResp:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body


class _DisconnectAfter:
    """is_disconnected() returns False the first ``n`` times then True so the
    SSE generator gets one pass through the message-handling loop before
    exiting."""

    def __init__(self, false_calls: int = 1) -> None:
        self._remaining = false_calls

    async def __call__(self) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return False
        return True


def _make_request(
    *,
    token: str = "test-token",
    agent_id_query: str | None = None,
    registry_client: MagicMock | None = None,
    false_disconnect_calls: int = 2,
) -> SimpleNamespace:
    """Build a minimal Request stand-in matching the slots events_stream reads.

    ``request.app.state.client`` is the httpx client the SSE handler uses to
    call the registry service for the ownership check (N19).
    """
    headers = {"Authorization": f"Bearer {token}"}

    qp: dict[str, str] = {}
    if agent_id_query is not None:
        qp["agent_id"] = agent_id_query

    if registry_client is None:
        registry_client = MagicMock()
        registry_client.get = AsyncMock(return_value=_FakeResp(200, {}))

    return SimpleNamespace(
        cookies={},
        headers=SimpleNamespace(get=lambda k, d="": headers.get(k, d)),
        query_params=qp,
        app=SimpleNamespace(state=SimpleNamespace(client=registry_client)),
        is_disconnected=_DisconnectAfter(false_disconnect_calls),
    )


def _install_token_validator(monkeypatch, payload: dict) -> None:
    """Pin services.gateway.auth.token_validator to a stub whose .validate()
    returns the supplied JWT payload."""
    from services.gateway import auth as _auth_mod

    stub = MagicMock()
    stub.validate = AsyncMock(return_value=payload)
    monkeypatch.setattr(_auth_mod, "token_validator", stub)


async def _drain(generator) -> list[str]:
    """Drain an async generator into a list."""
    out: list[str] = []
    async for item in generator:
        out.append(item)
    return out


def _patch_local_redis(monkeypatch, pubsub_queue: list[dict]) -> _FakePubSub:
    """Patch sdk.common.redis.get_redis_client so the SSE generator's
    inline ``local_redis = get_redis_client(...)`` returns our fake."""
    from sdk.common import redis as _redis_mod

    pubsub = _FakePubSub(pubsub_queue)
    fake_redis = _FakeRedis(pubsub)

    def _factory(*_args, **_kwargs):
        return fake_redis

    monkeypatch.setattr(_redis_mod, "get_redis_client", _factory)
    return pubsub


# ---------------------------------------------------------------------------
# N2 — payload tenant_id verification
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_counter():
    """Make every test start at 0 on the SSE_CROSS_TENANT_DROP counter."""
    from services.gateway.middleware import SSE_CROSS_TENANT_DROP_TOTAL

    SSE_CROSS_TENANT_DROP_TOTAL._value.set(0)
    yield


def _drop_counter_value() -> float:
    from services.gateway.middleware import SSE_CROSS_TENANT_DROP_TOTAL

    return SSE_CROSS_TENANT_DROP_TOTAL._value.get()


def test_sse_drops_message_with_mismatched_payload_tenant_id(monkeypatch):
    """Tenant A subscribes; a publisher emits a payload whose ``tenant_id``
    is tenant B. The generator MUST drop the message + increment the
    counter and yield ONLY the ``connected`` envelope."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    tenant_b = "22222222-2222-2222-2222-222222222222"
    _install_token_validator(monkeypatch, {"tenant_id": tenant_a, "sub": "user-a"})

    cross_tenant_msg = {
        "type": "message",
        "channel": f"acp:events:{tenant_a}".encode(),
        "data": json.dumps({
            "tenant_id": tenant_b,
            "type": "policy_decision",
            "data": {"agent_id": "x", "allowed": True},
            "ts": 0,
        }).encode(),
    }
    _patch_local_redis(monkeypatch, [cross_tenant_msg])

    req = _make_request()
    resp = asyncio.run(gw_main.events_stream(req))

    # event_generator() is the body; drain it.
    out = asyncio.run(_drain(resp.body_iterator))
    payload_text = "".join(out)

    # The poisoned message must NOT appear in the yielded SSE bytes.
    assert tenant_b not in payload_text, payload_text
    assert "policy_decision" not in payload_text
    # The counter must have ticked exactly once.
    assert _drop_counter_value() == 1.0
    # We still get the initial `connected` envelope so the client sees the
    # stream open successfully (the bug is invisible to the victim).
    assert "event: connected" in payload_text
    assert tenant_a in payload_text


def test_sse_relays_message_with_matching_payload_tenant_id(monkeypatch):
    """Tenant A subscribes; a publisher emits a payload whose ``tenant_id``
    IS tenant A. The generator MUST yield it untouched and NOT tick the
    counter."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    _install_token_validator(monkeypatch, {"tenant_id": tenant_a, "sub": "user-a"})

    in_tenant_msg = {
        "type": "message",
        "channel": f"acp:events:{tenant_a}".encode(),
        "data": json.dumps({
            "tenant_id": tenant_a,
            "type": "policy_decision",
            "data": {"agent_id": "agent-1", "allowed": True},
            "ts": 0,
        }).encode(),
    }
    _patch_local_redis(monkeypatch, [in_tenant_msg])

    req = _make_request()
    resp = asyncio.run(gw_main.events_stream(req))
    out = asyncio.run(_drain(resp.body_iterator))
    payload_text = "".join(out)

    # The legitimate payload must appear and the counter must NOT tick.
    assert "policy_decision" in payload_text
    assert _drop_counter_value() == 0.0


def test_sse_drops_message_with_missing_tenant_id_in_payload(monkeypatch):
    """A legacy / pre-N2 publisher emits a payload without ``tenant_id``.
    The generator MUST drop it + count it — silently allowing an
    un-attributed message is a regression on the fix."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    _install_token_validator(monkeypatch, {"tenant_id": tenant_a, "sub": "user-a"})

    legacy_msg = {
        "type": "message",
        "channel": f"acp:events:{tenant_a}".encode(),
        "data": json.dumps({
            # No tenant_id field — legacy / forged.
            "type": "tool_executed",
            "data": {"tool": "noop"},
            "ts": 0,
        }).encode(),
    }
    _patch_local_redis(monkeypatch, [legacy_msg])

    req = _make_request()
    resp = asyncio.run(gw_main.events_stream(req))
    out = asyncio.run(_drain(resp.body_iterator))
    payload_text = "".join(out)

    assert "tool_executed" not in payload_text
    assert _drop_counter_value() == 1.0


# ---------------------------------------------------------------------------
# N19 — agent_id RBAC check before subscribing
# ---------------------------------------------------------------------------


def test_sse_returns_404_when_agent_belongs_to_other_tenant(monkeypatch):
    """Tenant A subscribes with ``?agent_id=<tenant B's agent>``. The
    registry returns 404 (because the SELECT is tenant-scoped).
    The handler MUST return 404 BEFORE subscribing — no Redis pubsub call,
    no stream started."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    other_tenant_agent = str(uuid.uuid4())
    _install_token_validator(monkeypatch, {"tenant_id": tenant_a, "sub": "user-a"})

    registry_client = MagicMock()
    registry_client.get = AsyncMock(return_value=_FakeResp(404, {"detail": "Agent not found"}))

    pubsub = _patch_local_redis(monkeypatch, [])

    req = _make_request(agent_id_query=other_tenant_agent, registry_client=registry_client)
    resp = asyncio.run(gw_main.events_stream(req))

    # 404 BEFORE any subscribe — Redis pubsub was never touched.
    assert resp.status_code == 404
    assert pubsub.subscribed == []
    # Registry was called with the JWT tenant (not whatever the client sent).
    call = registry_client.get.call_args
    sent_headers = call.kwargs.get("headers", {})
    assert sent_headers.get("X-Tenant-ID") == tenant_a
    assert f"/agents/{other_tenant_agent}" in call.args[0]


def test_sse_returns_404_when_agent_does_not_exist(monkeypatch):
    """Same code path as cross-tenant — the registry returns 404 because
    the agent ID does not exist anywhere in the database. The handler
    MUST NOT differentiate (leaking 'exists in other tenant' would let an
    attacker enumerate cross-tenant agent IDs)."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    nonexistent_agent = str(uuid.uuid4())
    _install_token_validator(monkeypatch, {"tenant_id": tenant_a, "sub": "user-a"})

    registry_client = MagicMock()
    registry_client.get = AsyncMock(return_value=_FakeResp(404, {}))

    _patch_local_redis(monkeypatch, [])

    req = _make_request(agent_id_query=nonexistent_agent, registry_client=registry_client)
    resp = asyncio.run(gw_main.events_stream(req))

    assert resp.status_code == 404


def test_sse_proceeds_when_agent_owned_by_authenticated_tenant(monkeypatch):
    """Tenant A's privileged user (OWNER) subscribes to their own agent.
    Registry returns 200 with the agent record. The handler MUST proceed
    to subscribe + stream."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    own_agent = str(uuid.uuid4())
    _install_token_validator(
        monkeypatch,
        {"tenant_id": tenant_a, "sub": "user-a", "role": "OWNER"},
    )

    agent_record = {
        "data": {
            "id": own_agent,
            "tenant_id": tenant_a,
            "owner_id": "user-b",  # OWNER role bypasses owner_id check
            "name": "my-agent",
        }
    }
    registry_client = MagicMock()
    registry_client.get = AsyncMock(return_value=_FakeResp(200, agent_record))

    pubsub = _patch_local_redis(monkeypatch, [])

    req = _make_request(agent_id_query=own_agent, registry_client=registry_client)
    resp = asyncio.run(gw_main.events_stream(req))

    # Stream started — body_iterator is the generator.
    out = asyncio.run(_drain(resp.body_iterator))
    payload_text = "".join(out)
    assert "event: connected" in payload_text
    # The agent channel was subscribed.
    assert f"acp:events:{tenant_a}:{own_agent}" in pubsub.subscribed
    assert f"acp:events:{tenant_a}" in pubsub.subscribed


def test_sse_role_gate_blocks_read_only_from_other_users_agent(monkeypatch):
    """A READ_ONLY user subscribes to an agent owned by a different user
    (within the same tenant). The role-gate MUST return 403 — only
    OWNER/ADMIN/SECURITY_ANALYST can subscribe to other users' agents."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    other_user_agent = str(uuid.uuid4())
    _install_token_validator(
        monkeypatch,
        {"tenant_id": tenant_a, "sub": "user-a", "role": "READ_ONLY"},
    )

    agent_record = {
        "data": {
            "id": other_user_agent,
            "tenant_id": tenant_a,
            "owner_id": "user-b",  # owned by a different user
            "name": "someone-elses-agent",
        }
    }
    registry_client = MagicMock()
    registry_client.get = AsyncMock(return_value=_FakeResp(200, agent_record))

    pubsub = _patch_local_redis(monkeypatch, [])

    req = _make_request(agent_id_query=other_user_agent, registry_client=registry_client)
    resp = asyncio.run(gw_main.events_stream(req))

    assert resp.status_code == 403
    # Subscribe was never called — generator never ran.
    assert pubsub.subscribed == []


def test_sse_role_gate_allows_developer_for_their_own_agent(monkeypatch):
    """A DEVELOPER user subscribes to an agent they own — owner_id ==
    sub claim. The role-gate MUST allow the subscription."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    own_agent = str(uuid.uuid4())
    _install_token_validator(
        monkeypatch,
        {"tenant_id": tenant_a, "sub": "user-a", "role": "DEVELOPER"},
    )

    agent_record = {
        "data": {
            "id": own_agent,
            "tenant_id": tenant_a,
            "owner_id": "user-a",  # same as JWT sub
            "name": "my-own-agent",
        }
    }
    registry_client = MagicMock()
    registry_client.get = AsyncMock(return_value=_FakeResp(200, agent_record))

    pubsub = _patch_local_redis(monkeypatch, [])

    req = _make_request(agent_id_query=own_agent, registry_client=registry_client)
    resp = asyncio.run(gw_main.events_stream(req))

    out = asyncio.run(_drain(resp.body_iterator))
    payload_text = "".join(out)
    assert "event: connected" in payload_text
    assert f"acp:events:{tenant_a}:{own_agent}" in pubsub.subscribed


def test_sse_returns_503_when_registry_unreachable(monkeypatch):
    """If the registry call raises (connection refused, timeout, etc.) the
    handler MUST fail closed — we cannot verify ownership so we cannot
    open the stream."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    agent_id = str(uuid.uuid4())
    _install_token_validator(monkeypatch, {"tenant_id": tenant_a, "sub": "user-a"})

    registry_client = MagicMock()
    registry_client.get = AsyncMock(side_effect=ConnectionError("registry down"))

    pubsub = _patch_local_redis(monkeypatch, [])

    req = _make_request(agent_id_query=agent_id, registry_client=registry_client)
    resp = asyncio.run(gw_main.events_stream(req))

    assert resp.status_code == 503
    assert pubsub.subscribed == []


def test_sse_no_agent_filter_skips_registry_lookup(monkeypatch):
    """Without ``?agent_id=`` the handler subscribes only to the tenant
    channel and MUST NOT call the registry."""
    tenant_a = "11111111-1111-1111-1111-111111111111"
    _install_token_validator(monkeypatch, {"tenant_id": tenant_a, "sub": "user-a"})

    registry_client = MagicMock()
    registry_client.get = AsyncMock()

    pubsub = _patch_local_redis(monkeypatch, [])

    req = _make_request(registry_client=registry_client)
    resp = asyncio.run(gw_main.events_stream(req))

    asyncio.run(_drain(resp.body_iterator))
    registry_client.get.assert_not_awaited()
    assert pubsub.subscribed == [f"acp:events:{tenant_a}"]
