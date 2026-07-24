"""Regression: revoke TTL must cover the token's remaining lifetime.

Prior code set revoke_key TTL to a hardcoded 86400 (24h). If
JWT_EXPIRY_MINUTES was ever set past 1440 (long-lived service tokens
for CI or agents), a revoked token would RESURRECT once the revoke key
expired — even though the token itself was still valid.

Fix: revoke() reads exp from the token payload and sets revoke_ttl to
`exp - now + 60`. revoke_all_for_agent() falls back to
`redis.ttl(active_key)` because it operates on hashes with no access to
the payload.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from jose import jwt

from services.identity.token_service import (
    REDIS_REVOKE_PREFIX,
    TOKEN_TYPE,
    TokenService,
)


def _mk_token(secret: str, *, algorithm: str, exp_seconds_from_now: int) -> str:
    """Mint a raw JWT with an exp N seconds in the future, signed with
    the caller-supplied secret+algorithm so it matches the TokenService
    instance under test (avoids fights with reload/monkeypatch)."""
    now = datetime.now(tz=UTC)
    payload = {
        "sub":       "agent_x",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "role":      "agent",
        "typ":       TOKEN_TYPE,
        "jti":       "test-jti",
        "iat":       int(now.timestamp()),
        "exp":       int(now.timestamp()) + exp_seconds_from_now,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


class _FakeRedis:
    """Redis stub that records setex calls so we can assert on TTL."""
    def __init__(self, active_ttl: int = 0):
        self.setex_calls: list[tuple[str, int, str]] = []
        self.deleted: list[str] = []
        self._active_ttl = active_ttl
        self.smembers = AsyncMock(return_value=set())
        self.publish = AsyncMock()

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))

    async def get(self, key):
        return None

    async def srem(self, key, val):
        return 1

    async def delete(self, key):
        self.deleted.append(key)

    async def ttl(self, key):
        return self._active_ttl


class TestRevokeTtlMatchesTokenExp:
    @pytest.mark.asyncio
    async def test_revoke_ttl_covers_48h_token(self):
        """A token that expires in 48h must get a revoke_key TTL of ~48h.
        With the old 86400 hardcode, the revoke would expire in 24h and
        the token would resurrect for its final 24h."""
        fake = _FakeRedis()
        svc = TokenService(fake)  # type: ignore[arg-type]
        # Use the ACTUAL settings that TokenService captured so we don't
        # fight with monkeypatch/reload interactions from other tests.
        token = _mk_token(svc._secret, algorithm=svc._algorithm,
                          exp_seconds_from_now=48 * 3600)

        ok = await svc.revoke(token)
        assert ok is True

        # Find the revoke setex call.
        revoke_calls = [c for c in fake.setex_calls if c[0].startswith(REDIS_REVOKE_PREFIX)]
        assert len(revoke_calls) == 1
        _, ttl, _ = revoke_calls[0]
        # Should be near 48h + 60s buffer. Give some slack for test wall-clock.
        assert 48 * 3600 <= ttl <= 48 * 3600 + 120, (
            f"revoke TTL {ttl}s does not cover 48h token; resurrection window still open"
        )

    @pytest.mark.asyncio
    async def test_revoke_ttl_floored_at_60s_for_expired(self):
        """Already-expired token must still get a non-trivial revoke
        window (60s floor). A zero/negative TTL would either fail the
        setex or immediately re-open the door."""
        fake = _FakeRedis()
        svc = TokenService(fake)  # type: ignore[arg-type]
        token = _mk_token(svc._secret, algorithm=svc._algorithm,
                          exp_seconds_from_now=-3600)  # expired 1h ago

        ok = await svc.revoke(token)
        assert ok is True

        revoke_calls = [c for c in fake.setex_calls if c[0].startswith(REDIS_REVOKE_PREFIX)]
        assert len(revoke_calls) == 1
        _, ttl, _ = revoke_calls[0]
        assert ttl == 60, f"expected 60s floor for expired token, got {ttl}"


class TestRevokeAllForAgentUsesActiveTtl:
    @pytest.mark.asyncio
    async def test_ttl_from_active_key(self):
        """revoke_all_for_agent operates on hashes with no payload.
        It must read the active-key TTL as a proxy for token lifetime."""
        import uuid as _uuid

        # active_ttl = 48h remaining
        fake = _FakeRedis(active_ttl=48 * 3600)
        fake.smembers = AsyncMock(return_value={b"h1", b"h2"})
        svc = TokenService(fake)  # type: ignore[arg-type]

        count = await svc.revoke_all_for_agent(_uuid.uuid4())
        assert count == 2

        revoke_calls = [c for c in fake.setex_calls if c[0].startswith(REDIS_REVOKE_PREFIX)]
        assert len(revoke_calls) == 2
        for _, ttl, _ in revoke_calls:
            assert ttl == 48 * 3600, f"expected 48h TTL from active_key, got {ttl}s"


class TestOldRevokeTtlWasBroken:
    """Canary: 86400 < 48h * 3600 → the OLD hardcoded 24h was strictly
    less than a 48h token's remaining lifetime. If someone reverts this
    fix, this test still passes but the OTHER tests fail loudly."""
    def test_24h_less_than_48h(self):
        assert 86400 < 48 * 3600
