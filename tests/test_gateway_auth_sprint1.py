"""
Sprint 1.5 — fail-closed + agent-binding tests for the gateway auth middleware.

Covers:
  * Replay check returns HTTP 503 when Redis is unavailable (audit S4 — was
    previously a silent skip that allowed traffic through).
  * API-key with a bound ``agent_id`` rejects an ``X-Agent-ID`` mismatch with
    HTTP 403 (audit S5).
  * API-key without a binding (``agent_id`` NULL) preserves legacy behavior
    so existing tenant-scoped keys keep working.

These tests exercise the middleware via the ``_AuthMixin._authenticate`` entry
point with a hand-rolled stub stack — no live FastAPI app, no real Redis.
"""
from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.exceptions
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _StubRedis:
    """Just enough Redis for the auth path. Methods are AsyncMock by default."""

    def __init__(self) -> None:
        self.get = AsyncMock(return_value=None)
        self.set = AsyncMock(return_value=True)
        self.setex = AsyncMock(return_value=True)
        self.setnx = AsyncMock(return_value=True)
        self.expire = AsyncMock(return_value=True)
        self.incr = AsyncMock(return_value=1)


def _make_request(
    *,
    auth_header: str | None = None,
    x_agent: str | None = None,
    api_key_header: str | None = None,
    method: str = "POST",
    url_path: str = "/execute",
) -> Any:
    """Synthesize a Starlette-ish Request the middleware can consume."""
    headers: dict[str, str] = {}
    if auth_header:
        headers["Authorization"] = auth_header
    if x_agent:
        headers["X-Agent-ID"] = x_agent
    if api_key_header:
        headers["X-API-Key"] = api_key_header

    class _State:
        pass

    request = MagicMock()
    request.headers = headers
    request.cookies = {}
    request.client = MagicMock(host="127.0.0.1")
    request.state = _State()
    request.method = method
    request.url = MagicMock(path=url_path)
    return request


def _make_mixin(redis_stub: _StubRedis):
    from services.gateway._mw_auth import _AuthMixin
    inst = _AuthMixin.__new__(_AuthMixin)
    inst.redis = redis_stub
    return inst


# ---------------------------------------------------------------------------
# API-key agent binding (audit S5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_with_bound_agent_accepts_matching_header():
    """The happy path: key bound to agent X, X-Agent-ID is X → allowed."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    request = _make_request(
        auth_header="Bearer acp_realapikey",
        x_agent=agent_id,
    )
    inst._validate_api_key_cached = AsyncMock(return_value={  # type: ignore[attr-defined]
        "tenant_id": tenant_id,
        "agent_id":  agent_id,
        "key_prefix": "acp_real",
    })

    tid, aid, tid_s, aid_s, jti = await inst._authenticate(
        request, is_execute_path=True,
    )
    assert str(tid) == tenant_id
    assert str(aid) == agent_id
    assert jti is None


@pytest.mark.asyncio
async def test_api_key_with_bound_agent_rejects_mismatching_header():
    """The headline S5 fix: key bound to A, header claims B → 403."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    tenant_id = str(uuid.uuid4())
    bound_agent = str(uuid.uuid4())
    impostor_agent = str(uuid.uuid4())

    request = _make_request(
        auth_header="Bearer acp_realapikey",
        x_agent=impostor_agent,
    )
    inst._validate_api_key_cached = AsyncMock(return_value={  # type: ignore[attr-defined]
        "tenant_id": tenant_id,
        "agent_id":  bound_agent,
        "key_prefix": "acp_real",
    })

    with pytest.raises(HTTPException) as exc:
        await inst._authenticate(request, is_execute_path=True)
    assert exc.value.status_code == 403
    assert "X-Agent-ID does not match" in exc.value.detail


@pytest.mark.asyncio
async def test_api_key_with_bound_agent_requires_x_agent_header():
    """Per-agent keys must not be usable without an explicit X-Agent-ID."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    request = _make_request(
        auth_header="Bearer acp_realapikey",
        x_agent=None,
    )
    inst._validate_api_key_cached = AsyncMock(return_value={  # type: ignore[attr-defined]
        "tenant_id": str(uuid.uuid4()),
        "agent_id":  str(uuid.uuid4()),
        "key_prefix": "acp_real",
    })

    with pytest.raises(HTTPException) as exc:
        await inst._authenticate(request, is_execute_path=True)
    assert exc.value.status_code == 400
    assert "X-Agent-ID header is required" in exc.value.detail


@pytest.mark.asyncio
async def test_tenant_scoped_api_key_preserves_legacy_header_behavior():
    """Back-compat: keys without a binding (agent_id NULL) accept any
    X-Agent-ID, exactly as they did before Sprint 1.5."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    tenant_id = str(uuid.uuid4())
    any_agent = str(uuid.uuid4())

    request = _make_request(
        auth_header="Bearer acp_realapikey",
        x_agent=any_agent,
    )
    inst._validate_api_key_cached = AsyncMock(return_value={  # type: ignore[attr-defined]
        "tenant_id": tenant_id,
        "agent_id":  None,
        "key_prefix": "acp_real",
    })

    tid, aid, *_ = await inst._authenticate(request, is_execute_path=True)
    assert str(tid) == tenant_id
    assert str(aid) == any_agent


# ---------------------------------------------------------------------------
# Replay check fail-closed (audit S4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_check_returns_503_when_redis_unavailable():
    """The audit S4 ask: when Redis is down on the replay-check path, the
    middleware must deny (503) — not log and allow."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    jti = "test-jti-1"

    # Revocation check (the first redis.get) returns None (not revoked).
    # JTI revocation check (the second redis.get) returns None.
    # The replay setnx raises ConnectionError → fail-closed branch.
    redis_stub.get.return_value = None
    redis_stub.setnx.side_effect = redis.exceptions.ConnectionError(
        "redis unreachable",
    )

    request = _make_request(
        auth_header="Bearer eyJzdHViLnRva2VuLmlzbnQuYS5yZWFsLmp3dA",
        x_agent=agent_id,
    )

    # Stub the token validator so the JWT path proceeds to the replay check.
    fake_auth_data = {
        "tenant_id": tenant_id,
        "agent_id":  agent_id,
        "sub":       "test-user",
        "role":      "agent",
        "jti":       jti,
        "exp":       int(time.time()) + 600,
    }
    with patch("services.gateway.auth.token_validator", create=True) as tv:
        tv.validate = AsyncMock(return_value=fake_auth_data)

        with pytest.raises(HTTPException) as exc:
            await inst._authenticate(request, is_execute_path=True)
        assert exc.value.status_code == 503
        assert "replay check unavailable" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_revocation_check_fail_closed_remains_intact():
    """Sanity: the pre-existing revocation-check fail-closed path keeps
    returning 503 (we did not regress it while editing the replay branch)."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    redis_stub.get.side_effect = redis.exceptions.TimeoutError("redis timeout")

    request = _make_request(
        auth_header="Bearer some.jwt.token",
        x_agent=str(uuid.uuid4()),
    )
    with pytest.raises(HTTPException) as exc:
        await inst._authenticate(request, is_execute_path=True)
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# P3-1 + N17 — body unified to "Unauthorized" + WWW-Authenticate realm unified
# to "aegis". These tests pin the contract so a future merge cannot silently
# regress us back to "Invalid or expired token" / per-reason realm leak that
# the brutal review flagged as a probing oracle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p3_1_malformed_jwt_returns_unauthorized_with_aegis_realm():
    """The headline regression check: a syntactically-broken JWT must be
    rejected at the auth middleware with HTTPException(401, "Unauthorized",
    WWW-Authenticate: Bearer realm="aegis") — NOT fall through to the
    downstream decision-svc path that would yield a 403 fail-closed.
    """
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    # No revocation entry, so the validator gets called and decodes "xxx",
    # which jose treats as "Not enough segments" → ACPAuthError → 401.
    request = _make_request(auth_header="Bearer xxx", method="GET", url_path="/agents")

    with pytest.raises(HTTPException) as exc:
        await inst._authenticate(request, is_execute_path=False)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unauthorized"
    assert exc.value.headers is not None
    assert exc.value.headers.get("WWW-Authenticate") == 'Bearer realm="aegis"'


@pytest.mark.asyncio
async def test_p3_1_no_token_returns_unauthorized_with_aegis_realm():
    """A request with no Authorization header at all must surface the
    SAME body + realm as a malformed-token request, so the response is
    not an oracle for "did the attacker even send a token?".
    """
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    request = _make_request(auth_header=None, method="GET", url_path="/agents")

    with pytest.raises(HTTPException) as exc:
        await inst._authenticate(request, is_execute_path=False)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unauthorized"
    assert exc.value.headers is not None
    assert exc.value.headers.get("WWW-Authenticate") == 'Bearer realm="aegis"'


@pytest.mark.asyncio
async def test_p3_1_invalid_api_key_returns_unauthorized_with_aegis_realm():
    """API key (acp_…) rejections must also use the unified body + realm
    so the response does not leak which auth method an attacker probed.
    """
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)
    inst._validate_api_key_cached = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    request = _make_request(
        auth_header="Bearer acp_definitely_not_a_real_key",
        method="GET",
        url_path="/agents",
    )

    with pytest.raises(HTTPException) as exc:
        await inst._authenticate(request, is_execute_path=False)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Unauthorized"
    assert exc.value.headers is not None
    assert exc.value.headers.get("WWW-Authenticate") == 'Bearer realm="aegis"'


@pytest.mark.asyncio
async def test_p3_1_auth_failures_counter_keeps_per_reason_label():
    """The WWW-Authenticate realm is unified, but the internal counter
    AUTH_FAILURES_TOTAL must still receive the per-reason slug so our
    dashboards keep their diagnostic fidelity (invalid_token vs
    session_expired vs missing_token etc.).
    """
    from services.gateway.middleware import AUTH_FAILURES_TOTAL

    # Snapshot the metric for the invalid_token reason — _value._value is
    # the prometheus_client internal accessor that exposes the raw count.
    before = AUTH_FAILURES_TOTAL.labels(reason="invalid_token")._value.get()

    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)
    request = _make_request(auth_header="Bearer xxx", method="GET", url_path="/agents")

    with pytest.raises(HTTPException):
        await inst._authenticate(request, is_execute_path=False)

    after = AUTH_FAILURES_TOTAL.labels(reason="invalid_token")._value.get()
    assert after == before + 1, (
        f"AUTH_FAILURES_TOTAL{{reason=invalid_token}} did not tick "
        f"(before={before} after={after}) — the per-reason internal counter "
        f"is the only place the per-reason slug is still allowed to live."
    )
