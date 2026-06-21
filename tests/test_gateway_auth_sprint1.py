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
# N7 — Demo token must be rejected once demo_expires_at is in the past
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_token_rejected_after_demo_expires_at():
    """N7: when a JWT carries is_demo=True and demo_expires_at is in the
    past, the middleware must 401 BEFORE the DB lookup — even if the JWT
    signature + exp are still valid (cleanup background task hasn't yet
    deleted the tenant row)."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    demo_expired_at = time.time() - 3600  # 1 hour in the past

    redis_stub.get.return_value = None  # not revoked

    request = _make_request(
        auth_header="Bearer eyJzdHViLnRva2VuLmlzbnQuYS5yZWFsLmp3dA",
        x_agent=agent_id,
    )

    fake_auth_data = {
        "tenant_id":        tenant_id,
        "agent_id":         agent_id,
        "sub":              "demo-user",
        "role":             "agent",
        "jti":              "demo-jti",
        "exp":              int(time.time()) + 600,  # JWT exp still valid
        "is_demo":          True,
        "demo_expires_at":  demo_expired_at,
    }
    with patch("services.gateway.auth.token_validator", create=True) as tv:
        tv.validate = AsyncMock(return_value=fake_auth_data)

        with pytest.raises(HTTPException) as exc:
            await inst._authenticate(request, is_execute_path=True)
        assert exc.value.status_code == 401
        # P3-1 unified body: must be the literal "Unauthorized" string.
        assert exc.value.detail == "Unauthorized"
        # N17: realm must be the collapsed "aegis" literal.
        www = exc.value.headers["WWW-Authenticate"]
        assert 'realm="aegis"' in www


@pytest.mark.asyncio
async def test_demo_token_still_valid_passes_demo_guard():
    """N7 negative: a demo token whose demo_expires_at is in the future
    must NOT trip the demo guard. The token continues down the normal
    path (any rejection here would be from a later check, not the demo
    guard)."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    demo_future = time.time() + 3600  # 1 hour in the future

    redis_stub.get.return_value = None  # not revoked

    request = _make_request(
        auth_header="Bearer eyJzdHViLnRva2VuLmlzbnQuYS5yZWFsLmp3dA",
        x_agent=agent_id,
    )

    fake_auth_data = {
        "tenant_id":        tenant_id,
        "agent_id":         agent_id,
        "sub":              "demo-user",
        "role":             "agent",
        "jti":              "demo-jti-2",
        "exp":              int(time.time()) + 600,
        "is_demo":          True,
        "demo_expires_at":  demo_future,
    }
    with patch("services.gateway.auth.token_validator", create=True) as tv:
        tv.validate = AsyncMock(return_value=fake_auth_data)

        tid, aid, *_ = await inst._authenticate(request, is_execute_path=True)
        assert str(tid) == tenant_id
        assert str(aid) == agent_id


@pytest.mark.asyncio
async def test_non_demo_token_unaffected_by_demo_guard():
    """N7 sanity: a normal (non-demo) token has no is_demo claim and must
    flow through the demo guard untouched. Same shape as the
    test_replay_check_returns_503_when_redis_unavailable scaffolding minus
    the Redis blow-up, so we land cleanly past the demo branch."""
    redis_stub = _StubRedis()
    inst = _make_mixin(redis_stub)

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    redis_stub.get.return_value = None

    request = _make_request(
        auth_header="Bearer eyJzdHViLnRva2VuLmlzbnQuYS5yZWFsLmp3dA",
        x_agent=agent_id,
    )
    fake_auth_data = {
        "tenant_id": tenant_id,
        "agent_id":  agent_id,
        "sub":       "regular-user",
        "role":      "agent",
        "jti":       "regular-jti",
        "exp":       int(time.time()) + 600,
    }
    with patch("services.gateway.auth.token_validator", create=True) as tv:
        tv.validate = AsyncMock(return_value=fake_auth_data)

        tid, aid, *_ = await inst._authenticate(request, is_execute_path=True)
        assert str(tid) == tenant_id
        assert str(aid) == agent_id


# ---------------------------------------------------------------------------
# N1 — tenant + org compare must be constant-time
# ---------------------------------------------------------------------------


def test_tenant_compare_uses_secrets_compare_digest():
    """N1 byte-level: read the source back and confirm the tenant +
    org-id comparisons use secrets.compare_digest, not naive `!=`.
    A unit microbenchmark on Python `!=` would be flaky on CI; the
    static check is what the finding actually requires."""
    import inspect

    from services.gateway import _mw_auth

    src = inspect.getsource(_mw_auth._AuthMixin._authenticate)
    # Both compares must use the constant-time primitive.
    # x_tenant compare:
    assert "secrets.compare_digest(x_tenant, tenant_id_str)" in src, (
        "N1 regression: x_tenant comparison must use secrets.compare_digest"
    )
    # x_org_id compare:
    assert "secrets.compare_digest(str(x_org_id), str(token_org_id))" in src, (
        "N1 regression: x_org_id comparison must use secrets.compare_digest"
    )
    # Belt-and-braces: there must be no `x_tenant != tenant_id_str` left.
    assert "x_tenant != tenant_id_str" not in src, (
        "N1 regression: naive != tenant compare reintroduced"
    )
    assert "x_org_id != token_org_id" not in src, (
        "N1 regression: naive != org_id compare reintroduced"
    )


# ---------------------------------------------------------------------------
# N17 — every WWW-Authenticate realm slug is the single literal "aegis"
# ---------------------------------------------------------------------------


def test_no_branch_leaking_realm_slugs_remain():
    """N17: scan the two auth modules and assert every realm= value is
    `aegis`. Any remaining slug (`invalid_token` / `session_expired` /
    `insufficient_role` / `revoked_token`) re-opens the validator-branch
    oracle that the P3-1 body collapse already closed."""
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / "services" / "gateway" / "_mw_auth.py",
        repo_root / "services" / "gateway" / "auth.py",
    ]
    realm_re = re.compile(r'realm="([^"]+)"')
    offenders: list[tuple[str, int, str]] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in realm_re.finditer(line):
                slug = match.group(1)
                if slug != "aegis":
                    offenders.append((str(path), lineno, slug))
    assert not offenders, (
        f"N17 regression: non-`aegis` realm slugs leaked back in: {offenders}"
    )
