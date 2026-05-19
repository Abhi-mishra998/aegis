"""
ACP Gateway — Local JWT Validator with Caching
================================================
Validates tokens locally with Redis caching for high-concurrency environments.

Token Cache Strategy:
  - Cache validated payloads in Redis with TTL matching token expiry
  - Reduces concurrent jwt.decode() calls (python-jose can have race conditions under extreme load)
  - Graceful fallback to local validation if cache miss
  - Revocation checks still SEPARATE in SecurityMiddleware (before this validator)
  - C-5 FIX (2026-05-13): Confirms Identity active_key presence in Redis so a
    stolen JWT_SECRET_KEY does NOT grant indefinite token minting.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.constants import REDIS_REVOKE_PREFIX, REDIS_TOKEN_PREFIX
from sdk.common.exceptions import ACPAuthError


REDIS_TOKEN_VALIDATION_PREFIX = "acp:token_validation:"

# In-process LRU cache for validated JWT payloads. Sits in front of the
# Redis cache so /execute's hot path can answer "is this token valid?"
# without a network round-trip. Defaults: 60s TTL, 10_000 entries (covers
# the cardinality of unique tokens we expect to see in a 1-minute window
# at 200 concurrent users with reasonable token reuse).
_LRU_DEFAULT_MAX = 10_000
_LRU_DEFAULT_TTL_SECONDS = 60.0


class _LocalTokenLRU:
    """Tiny thread-safe LRU with TTL eviction.

    Not using `functools.lru_cache` because (a) we need TTL semantics — a
    revoked token's cached entry must time out even without an explicit
    invalidate() call — and (b) we need targeted invalidation when
    logout/rotation events arrive."""

    def __init__(self, *, max_entries: int, ttl_seconds: float) -> None:
        self._max = max_entries
        self._ttl = ttl_seconds
        self._store: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()
        self._lock = threading.Lock()
        # Hit/miss counters — surfaced via /metrics through metric_snapshot()
        # so dashboards can prove the cache is actually hot.
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, payload = entry
            if expires_at <= now:
                # Expired — drop it.
                self._store.pop(key, None)
                self.misses += 1
                return None
            # Move to MRU end.
            self._store.move_to_end(key)
            self.hits += 1
            # Return a copy so a caller mutation can't poison the cache.
            return dict(payload)

    def set(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, dict(payload))
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def metric_snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._store), "hits": self.hits, "misses": self.misses}


_LOCAL_TOKEN_LRU = _LocalTokenLRU(
    max_entries=_LRU_DEFAULT_MAX,
    ttl_seconds=_LRU_DEFAULT_TTL_SECONDS,
)


def get_local_token_cache() -> _LocalTokenLRU:
    """Module-level accessor — tests + ops endpoints reach in here."""
    return _LOCAL_TOKEN_LRU


def invalidate_local_token(token: str) -> bool:
    """Drop a token's cached payload — call this on logout / revoke /
    key rotation so the next request re-validates from Redis."""
    return _LOCAL_TOKEN_LRU.invalidate(LocalTokenValidator._token_hash(token))


class LocalTokenValidator:
    """
    Handles local JWT validation with Redis-backed caching.

    Responsibilities:
      - Verify JWT signature with shared secret (local, no I/O)
      - Verify token expiry
      - Verify required claims (agent_id/user_id, tenant_id)
      - Cache validated payloads in Redis (reduces concurrent jwt.decode calls)

    Revocation checks happen separately in SecurityMiddleware.
    """

    def __init__(self, redis_client: Redis | None = None) -> None:
        self._secret = settings.JWT_SECRET_KEY
        self._algorithm = settings.JWT_ALGORITHM
        self._redis = redis_client

    async def validate(self, token: str) -> dict[str, Any]:
        """
        Validate token signature, expiry, AND Identity-issued status.

        Returns:
            Decoded payload dict if valid.

        Raises:
            ACPAuthError: If signature is invalid, token is expired, required
                          claims are missing, or Identity does not recognize
                          this token (active_key missing in Redis).
        """
        token_hash = self._token_hash(token)

        # Layer 1: in-process LRU (60s TTL). Returns in <1µs on hit and
        # skips both the Redis cache lookup AND the Identity active-key
        # confirmation below — the cached entry is itself proof that the
        # token was Identity-recognised within the TTL window. Sprint 2:
        # this removes 2 Redis round-trips from the /execute hot path.
        cached_local = _LOCAL_TOKEN_LRU.get(token_hash)
        if cached_local is not None:
            return cached_local

        # Layer 2: shared Redis cache (cross-process). Catches the
        # in-process miss when the request lands on a different uvicorn
        # worker than the one that originally validated the token.
        if self._redis:
            cached = await self._get_cached_payload(token)
            if cached is not None:
                _LOCAL_TOKEN_LRU.set(token_hash, cached)
                return cached

        # Cache miss: validate locally
        payload = self._validate_signature(token)

        # C-5 FIX (2026-05-13): Confirm Identity actually issued this token by
        # checking the active_key it sets at issuance. Without this, anyone with
        # JWT_SECRET_KEY can mint accepted tokens indefinitely. Fails CLOSED on
        # Redis error: a Redis outage should not be an authentication bypass.
        if self._redis is not None:
            active_key = f"{REDIS_TOKEN_PREFIX}{self._token_hash(token)}"
            try:
                if not await self._redis.exists(active_key):
                    raise ACPAuthError("Token not recognized by Identity service")
            except ACPAuthError:
                raise
            except Exception as exc:
                # Fail closed: do not let a Redis hiccup become an auth bypass.
                raise ACPAuthError("Authentication infrastructure unavailable") from exc

        # Store in cache for next request (with short TTL to match token expiry)
        if self._redis and "exp" in payload:
            await self._cache_payload(token, payload)

        # Always populate the in-process LRU on a successful validate so
        # subsequent calls on this worker skip both layers above.
        _LOCAL_TOKEN_LRU.set(token_hash, payload)
        return payload

    def _validate_signature(self, token: str) -> dict[str, Any]:
        """Validate JWT signature and claims (no I/O)."""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
            )

            required = ["sub", "tenant_id", "role", "exp", "jti"]
            for field in required:
                if field not in payload:
                    raise ACPAuthError(f"Invalid token: missing {field}")

            org_id_str = payload.get("org_id")
            tenant_id_str = payload.get("tenant_id")
            if org_id_str and tenant_id_str:
                import uuid
                from sdk.common.invariants import assert_org_consistency, InvariantViolation
                try:
                    assert_org_consistency(uuid.UUID(org_id_str), uuid.UUID(tenant_id_str), "gateway token validation")
                except InvariantViolation as e:
                    raise ACPAuthError(f"System Integrity Error: {e}")

            return payload

        except ExpiredSignatureError as exc:
            raise ACPAuthError("Token has expired") from exc
        except JWTError as exc:
            raise ACPAuthError(f"Invalid token: {str(exc)}") from exc

    async def _get_cached_payload(self, token: str) -> dict[str, Any] | None:
        """Retrieve cached payload from Redis (if exists and valid)."""
        assert self._redis is not None
        try:
            cache_key = f"{REDIS_TOKEN_VALIDATION_PREFIX}{self._token_hash(token)}"
            cached_json = await self._redis.get(cache_key)
            if cached_json:
                return json.loads(cached_json)
        except Exception:
            pass
        return None

    async def _cache_payload(self, token: str, payload: dict[str, Any]) -> None:
        """Cache validated payload with TTL matching token expiry."""
        assert self._redis is not None
        try:
            import time
            cache_key = f"{REDIS_TOKEN_VALIDATION_PREFIX}{self._token_hash(token)}"
            exp = payload.get("exp", 0)
            ttl = max(1, int(exp - time.time()))
            await self._redis.setex(cache_key, ttl, json.dumps(payload))
        except Exception:
            pass

    @staticmethod
    def _token_hash(token: str) -> str:
        """Hash token for cache key (same as identity service)."""
        return hashlib.sha256(token.encode()).hexdigest()


token_validator: LocalTokenValidator | None = None


def init_token_validator(redis_client: Redis | None = None) -> LocalTokenValidator:
    """Initialize token validator with optional Redis caching."""
    global token_validator
    token_validator = LocalTokenValidator(redis_client=redis_client)
    return token_validator


# Re-export so middleware can import it from here without re-importing constants
__all__ = ["LocalTokenValidator", "token_validator", "REDIS_REVOKE_PREFIX"]
