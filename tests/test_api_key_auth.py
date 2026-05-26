"""
Unit tests for API key authentication in SecurityMiddleware._AuthMixin.

Covers:
  - Bearer acp_... token detected as API key (not JWT)
  - Invalid API key returns 401
  - Valid API key sets tenant_id, permissions, role, actor
  - X-Agent-ID header is read when using API key auth
  - Redis cache is checked before calling service_client
  - X-API-Key header (legacy) also works
  - JWT path is unaffected (regression check via source inspection)
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Source-level contract tests (fast, no imports of heavy deps)
# ---------------------------------------------------------------------------

def test_api_key_bearer_prefix_detected():
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert 'token.startswith("acp_")' in src, "API key Bearer prefix detection missing"


def test_api_key_cache_method_exists():
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert "_validate_api_key_cached" in src, "_validate_api_key_cached helper missing"


def test_api_key_sets_role_agent():
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert 'request.state.role = "agent"' in src, "API key auth must set role=agent"


def test_api_key_sets_permissions():
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert '"execute_agent"' in src, "API key auth must grant execute_agent permission"


def test_api_key_reads_x_agent_id():
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert 'X-Agent-ID' in src, "API key auth must read X-Agent-ID header"


def test_api_key_cache_prefix_defined():
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert "_API_KEY_CACHE_PREFIX" in src, "Redis cache prefix constant missing"
    assert "_API_KEY_CACHE_TTL" in src, "Redis cache TTL constant missing"


def test_jwt_path_unchanged():
    """JWT Bearer path must still check REDIS_REVOKE_PREFIX (regression guard)."""
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert "REDIS_REVOKE_PREFIX" in src, "JWT revocation check removed — regression"


def test_legacy_x_api_key_header_handled():
    """X-API-Key header (legacy) must still be processed."""
    src = Path("services/gateway/_mw_auth.py").read_text()
    assert "api_key_header" in src, "Legacy X-API-Key header path removed"


# ---------------------------------------------------------------------------
# Logic tests using mocked deps
# ---------------------------------------------------------------------------

TENANT_ID = "00000000-0000-0000-0000-000000000001"
AGENT_ID  = "11111111-1111-1111-1111-111111111111"

KEY_DATA = {
    "id": str(uuid.uuid4()),
    "tenant_id": TENANT_ID,
    "name": "test-key",
    "key_prefix": "acp_xxxx",
    "is_active": True,
    "created_at": "2026-01-01T00:00:00Z",
    "last_used_at": None,
}


def _make_request(bearer: str, agent_id: str = AGENT_ID, x_api_key: str = "") -> MagicMock:
    req = MagicMock()
    req.method = "POST"
    req.client.host = "127.0.0.1"
    req.cookies = {}

    headers: dict[str, str] = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if x_api_key:
        headers["X-API-Key"] = x_api_key
    if agent_id:
        headers["X-Agent-ID"] = agent_id
    headers["X-Tenant-ID"] = TENANT_ID

    req.headers = headers
    req.state = MagicMock()
    req.state.permissions = None
    req.state.role = None
    req.state.actor = None
    req.state.jwt_claims = None
    return req


@pytest.mark.asyncio
async def test_valid_api_key_bearer_sets_tenant_and_role():
    """Bearer acp_... with a valid key must populate tenant_id + role=agent."""
    from services.gateway._mw_auth import _AuthMixin

    mixin = _AuthMixin.__new__(_AuthMixin)
    mixin.redis = AsyncMock()
    mixin.redis.get = AsyncMock(return_value=None)   # cache miss
    mixin.redis.setex = AsyncMock()

    request = _make_request("acp_validkey123")

    with patch.object(
        _AuthMixin, "_validate_api_key_cached", new=AsyncMock(return_value=KEY_DATA)
    ):
        tenant_id, agent_id, tid_str, aid_str, jti = await mixin._authenticate(request)

    assert str(tenant_id) == TENANT_ID
    assert request.state.role == "agent"
    assert "execute_agent" in request.state.permissions
    assert jti is None


@pytest.mark.asyncio
async def test_invalid_api_key_bearer_raises_401():
    """Bearer acp_... with invalid key must raise 401."""
    from fastapi import HTTPException

    from services.gateway._mw_auth import _AuthMixin

    mixin = _AuthMixin.__new__(_AuthMixin)
    mixin.redis = AsyncMock()
    mixin.redis.get = AsyncMock(return_value=None)
    mixin.redis.incr = AsyncMock()
    mixin.redis.expire = AsyncMock()

    request = _make_request("acp_badkey000")

    with patch.object(
        _AuthMixin, "_validate_api_key_cached", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await mixin._authenticate(request)

    assert exc_info.value.status_code == 401
    assert "API key" in exc_info.value.detail


@pytest.mark.asyncio
async def test_api_key_reads_agent_id_from_header():
    """X-Agent-ID header must be parsed into agent_id when using API key auth."""
    from services.gateway._mw_auth import _AuthMixin

    mixin = _AuthMixin.__new__(_AuthMixin)
    mixin.redis = AsyncMock()

    request = _make_request("acp_key", agent_id=AGENT_ID)

    with patch.object(
        _AuthMixin, "_validate_api_key_cached", new=AsyncMock(return_value=KEY_DATA)
    ):
        _, agent_id, _, _, _ = await mixin._authenticate(request)

    assert str(agent_id) == AGENT_ID


@pytest.mark.asyncio
async def test_api_key_cache_hit_skips_service_call():
    """Redis cache hit must return cached data without calling service_client."""
    import hashlib

    from services.gateway._mw_auth import _API_KEY_CACHE_PREFIX, _AuthMixin

    raw_key = "acp_cached_key"
    cache_key = f"{_API_KEY_CACHE_PREFIX}{hashlib.sha256(raw_key.encode()).hexdigest()}"

    mixin = _AuthMixin.__new__(_AuthMixin)
    mixin.redis = AsyncMock()
    mixin.redis.get = AsyncMock(side_effect=lambda k: json.dumps(KEY_DATA) if k == cache_key else None)

    with patch("services.gateway._mw_auth.service_client") as mock_sc:
        mock_sc.validate_api_key = AsyncMock()
        result = await mixin._validate_api_key_cached(raw_key)

    mock_sc.validate_api_key.assert_not_called()
    assert result is not None
    assert result["tenant_id"] == TENANT_ID
