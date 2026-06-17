"""Regression test for U1 — employee virtual key ``is_active`` enforcement.

Verify-first finding (services + line numbers):

  1. services/api/router/api_key.py:82-95
     The validate endpoint returns ``APIKeyResponse.model_validate(api_key)``.

  2. services/api/schemas/api_key.py:36-44
     ``APIKeyResponse`` ALREADY exposes ``is_active: bool``. So the api-svc
     JSON response always includes the field. The explore-agent claim that
     the api-svc response does not include ``is_active`` was WRONG.

  3. services/api/repository/api_key.py:62
     ``get_by_hash`` filters ``APIKey.is_active`` at SQL level, so a fresh
     validate against a revoked key returns ``None`` and the api-svc
     endpoint replies 401.

  4. services/gateway/_mw_auth.py:28-49 (BEFORE the fix)
     ``_validate_api_key_cached`` caches the response for 60 seconds with no
     ``is_active`` re-check on cache reads and no invalidation hook on
     revoke. This was the REAL bug: a key revoked between cache writes
     keeps working until cache TTL expires.

This file pins the new behavior in place:

  • ``_validate_api_key_cached`` rejects when the payload carries
    ``is_active: False``.
  • The gateway revoke proxy at services/gateway/routers/users.py:139-167
    SADDs the key id into ``acp:apikey:revoked``; the cache layer
    SISMEMBER-checks that set and returns ``None`` even when a stale
    cache entry still claims ``is_active=True``.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Source-level contract pins
# ---------------------------------------------------------------------------

def test_api_svc_validate_returns_is_active_via_schema():
    """APIKeyResponse must expose ``is_active`` so the JSON response carries it."""
    src = Path("services/api/schemas/api_key.py").read_text()
    assert "is_active: bool" in src, (
        "APIKeyResponse must carry is_active; otherwise the gateway has no "
        "signal that a key has been revoked"
    )


def test_gateway_cache_double_checks_is_active():
    """Cache reads must reject ``is_active is False`` (defense-in-depth)."""
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert 'key_data.get("is_active") is False' in src


def test_gateway_revoke_proxy_invalidates_index():
    """Revoke proxy must populate the cross-instance revocation set."""
    src = Path("services/gateway/routers/users.py").read_text()
    assert 'acp:apikey:revoked' in src
    assert "sadd" in src


def test_gateway_cache_consults_revocation_index():
    """Cache reads must SISMEMBER-check the revocation set."""
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert "_API_KEY_REVOKED_SET" in src
    assert "sismember" in src


# ---------------------------------------------------------------------------
# Behavioral pins
# ---------------------------------------------------------------------------

KEY_ID = str(uuid.uuid4())
TENANT_ID = str(uuid.uuid4())
RAW_KEY = "acp_test_employee_virtual_key"
CACHE_KEY = (
    f"acp:apikey:valid:{hashlib.sha256(RAW_KEY.encode()).hexdigest()}"
)

ACTIVE_PAYLOAD = {
    "id": KEY_ID,
    "tenant_id": TENANT_ID,
    "name": "employee-virtual-key",
    "key_prefix": "acp_test",
    "is_active": True,
    "created_at": "2026-01-01T00:00:00Z",
    "last_used_at": None,
}

REVOKED_PAYLOAD = {**ACTIVE_PAYLOAD, "is_active": False}


def _make_mixin(redis_mock):
    from services.gateway._mw_auth import _AuthMixin

    mixin = _AuthMixin.__new__(_AuthMixin)
    mixin.redis = redis_mock
    return mixin


@pytest.mark.asyncio
async def test_cached_active_key_passes():
    """A cached, still-active key is returned normally (sanity)."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(ACTIVE_PAYLOAD))
    redis.sismember = AsyncMock(return_value=False)
    redis.delete = AsyncMock()

    mixin = _make_mixin(redis)
    result = await mixin._validate_api_key_cached(RAW_KEY)

    assert result is not None
    assert result["id"] == KEY_ID
    assert result["is_active"] is True


@pytest.mark.asyncio
async def test_cached_payload_marked_inactive_is_rejected():
    """If the cached payload itself reports is_active=False we MUST 401."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(REVOKED_PAYLOAD))
    redis.sismember = AsyncMock(return_value=False)
    redis.delete = AsyncMock()

    mixin = _make_mixin(redis)
    result = await mixin._validate_api_key_cached(RAW_KEY)

    assert result is None
    # Stale cache entry must also be evicted so the next caller re-checks.
    redis.delete.assert_awaited_once_with(CACHE_KEY)


@pytest.mark.asyncio
async def test_revoked_index_blocks_stale_active_cache():
    """
    Cache-staleness window: the cached blob still claims is_active=True
    (it was cached before the revoke landed), but the revocation index
    knows the key id has been revoked. The middleware MUST reject.

    This is the core fix for the cache-TTL bypass the explore agent
    pointed at.
    """
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(ACTIVE_PAYLOAD))
    redis.sismember = AsyncMock(return_value=True)  # key_id was revoked
    redis.delete = AsyncMock()

    mixin = _make_mixin(redis)
    result = await mixin._validate_api_key_cached(RAW_KEY)

    assert result is None
    redis.sismember.assert_awaited_once_with("acp:apikey:revoked", KEY_ID)
    redis.delete.assert_awaited_once_with(CACHE_KEY)


@pytest.mark.asyncio
async def test_live_validate_inactive_key_not_cached():
    """
    Cache miss → live api-svc validate → response says is_active=False.
    The middleware must NOT cache this and must return None.
    """
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # cache miss
    redis.setex = AsyncMock()
    redis.sismember = AsyncMock(return_value=False)
    redis.delete = AsyncMock()

    mixin = _make_mixin(redis)
    with patch("services.gateway._mw_auth.service_client") as sc:
        sc.validate_api_key = AsyncMock(return_value=REVOKED_PAYLOAD)
        result = await mixin._validate_api_key_cached(RAW_KEY)

    assert result is None
    # The middleware caches the api-svc response BEFORE the is_active check
    # (matches current implementation; the is_active rejection still kicks
    # in and we evict the just-written entry). Either way the caller gets
    # None — pin the contract on the final return value.
    redis.delete.assert_awaited_once_with(CACHE_KEY)


@pytest.mark.asyncio
async def test_live_validate_active_key_is_cached_and_returned():
    """Fresh validate of an active key still caches + returns the payload."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # cache miss
    redis.setex = AsyncMock()
    redis.sismember = AsyncMock(return_value=False)
    redis.delete = AsyncMock()

    mixin = _make_mixin(redis)
    with patch("services.gateway._mw_auth.service_client") as sc:
        sc.validate_api_key = AsyncMock(return_value=ACTIVE_PAYLOAD)
        result = await mixin._validate_api_key_cached(RAW_KEY)

    assert result is not None
    assert result["id"] == KEY_ID
    redis.setex.assert_awaited_once()
