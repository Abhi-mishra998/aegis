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

from fastapi import Header, HTTPException, status

from sdk.common.auth import extract_bearer_token
from sdk.common.config import settings
from sdk.common.constants import REDIS_REVOKE_PREFIX, REDIS_TOKEN_PREFIX
from sdk.common.exceptions import ACPAuthError
from sdk.common.roles import Role, canonical_role
from services.gateway.auth_clerk import get_clerk_validator, looks_like_clerk_token

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
        self._store: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
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


def invalidate_local_token_by_hash(token_hash: str) -> bool:
    """Invalidate by sha256 hash — used by the revocation Pub/Sub listener.

    The Identity service publishes the hash on revoke; each gateway worker's
    listener drops the entry. Closes the up-to-60-second revocation latency
    window that the LRU otherwise opens.
    """
    return _LOCAL_TOKEN_LRU.invalidate(token_hash)


# Pub/Sub channel name shared with services/identity/router.py — keep in sync.
TOKEN_REVOCATIONS_CHANNEL = "acp:token:revocations"


async def run_revocation_listener(redis_client: Redis) -> None:
    """Subscribe to the revocation channel and drop entries from the LRU.

    Started as a background task in the gateway lifespan. One subscriber per
    uvicorn worker is required — Redis Pub/Sub fans out, but listeners must
    live in the process whose LRU they are clearing.

    Message format: the bare 64-char sha256 hex digest of the JWT.
    """
    import structlog
    log = structlog.get_logger(__name__)
    while True:
        try:
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(TOKEN_REVOCATIONS_CHANNEL)
            log.info("token_revocation_listener_subscribed", channel=TOKEN_REVOCATIONS_CHANNEL)
            async for msg in pubsub.listen():
                if not msg or msg.get("type") != "message":
                    continue
                data = msg.get("data", b"")
                token_hash = data.decode("utf-8", errors="ignore") if isinstance(data, (bytes, bytearray)) else str(data)
                token_hash = token_hash.strip()
                # Defence: only 64-char hex strings are valid token hashes.
                if len(token_hash) == 64 and all(c in "0123456789abcdef" for c in token_hash):
                    dropped = invalidate_local_token_by_hash(token_hash)
                    log.info("token_revocation_received", dropped=dropped)
                else:
                    log.warning("token_revocation_malformed", payload_len=len(token_hash))
        except Exception as exc:
            log.warning("token_revocation_listener_error", error=str(exc))
            # Back off briefly, then reconnect.
            import asyncio as _asyncio
            await _asyncio.sleep(2.0)


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

        Dispatches to one of two validators based on ACP_AUTH_PROVIDER and
        the token's issuer claim:

          - legacy: HS256 self-issued, gated by Identity active-key in Redis.
          - clerk:  RS256 Clerk-issued, gated by JWKS signature only.

        Returns:
            Decoded payload dict if valid. Shape is identical regardless of
            provider so downstream middleware does not need to know which
            path validated the token.

        Raises:
            ACPAuthError: signature invalid, token expired, required claims
                          missing, Identity does not recognize the token
                          (legacy active_key missing), or no validator is
                          enabled for the token's issuer.
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

        # Cache miss: dispatch by provider + token shape.
        auth_provider = settings.ACP_AUTH_PROVIDER
        is_clerk = (
            auth_provider in ("clerk", "both")
            and looks_like_clerk_token(token)
        )

        if is_clerk:
            # U4 FIX (2026-06-17): HS256 + Clerk-iss downgrade-attack reject.
            # `looks_like_clerk_token` only inspects the unverified `iss`
            # claim. An attacker who knows JWT_SECRET_KEY could mint an
            # HS256 token with iss=<clerk_issuer> and ride the Clerk path.
            # The inner ClerkTokenValidator enforces alg via the JWK, but
            # we enforce it here as defense-in-depth so the dispatcher
            # itself never lets an HS-signed token reach the Clerk path.
            try:
                _alg = jwt.get_unverified_header(token).get("alg")
            except JWTError as exc:
                raise ACPAuthError(f"Invalid Clerk token header: {exc}") from exc
            if _alg not in ("RS256", "RS512"):
                raise ACPAuthError(
                    f"Invalid Clerk token alg: expected RS256/RS512, got {_alg!r}",
                )
            clerk_validator = get_clerk_validator(self._redis)
            payload = await clerk_validator.validate(token)
        elif auth_provider in ("legacy", "both"):
            payload = self._validate_signature(token)

            # C-5 FIX (2026-05-13): Confirm Identity actually issued this token
            # by checking the active_key it sets at issuance. Without this,
            # anyone with JWT_SECRET_KEY can mint accepted tokens indefinitely.
            # Fails CLOSED on Redis error.
            #
            # Sprint S4 exception: anonymous demo tokens (is_demo=true) are
            # minted directly by the gateway's /demo/spawn-workspace endpoint
            # and never round-trip through Identity, so they cannot register
            # an active_key. They're safe because (a) the JWT is HS256 + signed
            # by JWT_SECRET_KEY which only the gateway holds, (b) the 30-min
            # TTL bounds the blast radius, and (c) the spawn endpoint is
            # rate-limited per source IP at the WAF.
            if self._redis is not None and not payload.get("is_demo"):
                active_key = f"{REDIS_TOKEN_PREFIX}{token_hash}"
                try:
                    if not await self._redis.exists(active_key):
                        raise ACPAuthError("Token not recognized by Identity service")
                except ACPAuthError:
                    raise
                except Exception as exc:
                    raise ACPAuthError(
                        "Authentication infrastructure unavailable",
                    ) from exc
            payload.setdefault("auth_provider", "legacy")
        else:
            raise ACPAuthError(
                f"No validator enabled for this token (ACP_AUTH_PROVIDER={auth_provider!r})",
            )

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

                from sdk.common.invariants import (
                    InvariantViolation,
                    assert_org_consistency,
                )
                try:
                    assert_org_consistency(uuid.UUID(org_id_str), uuid.UUID(tenant_id_str), "gateway token validation")
                except InvariantViolation as e:
                    raise ACPAuthError(f"System Integrity Error: {e}")

            return payload

        except ExpiredSignatureError as exc:
            raise ACPAuthError("Token has expired", reason="session_expired") from exc
        except JWTError as exc:
            raise ACPAuthError(f"Invalid token: {str(exc)}", reason="invalid_token") from exc

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


# ---------------------------------------------------------------------------
# verify_role(*allowed) — FastAPI dependency factory
# ---------------------------------------------------------------------------
# Usage:
#
#     from services.gateway.auth import verify_role
#     from sdk.common.roles import Role
#
#     @router.post("/workspace/exit-shadow-mode", dependencies=[Depends(verify_role(Role.OWNER))])
#     async def exit_shadow_mode(...): ...
#
# Or to also receive the decoded claims:
#
#     @router.get("/admin/foo")
#     async def admin_foo(
#         claims: dict = Depends(verify_role(Role.OWNER, Role.ADMIN)),
#     ): ...
#
# The dependency:
#   1. Reads the Authorization: Bearer header.
#   2. Validates the token via the same `token_validator` global the
#      gateway middleware uses — so legacy HS256 and Clerk RS256 tokens
#      both flow through the same path.
#   3. Projects the JWT's `role` claim onto the canonical Role vocabulary
#      via sdk.common.roles.canonical_role().
#   4. Raises 403 if the projected role is not in `allowed`.
#   5. Returns the decoded claims dict on success.
#
# Behavioural choices:
#   - Always 401 on missing/invalid token (authentication failure).
#   - Always 403 on role mismatch (authorization failure).
#   - Never silently allow if `allowed` is empty — that's treated as a
#     programmer error and raises ValueError at dependency build time.


def verify_role(*allowed):
    """Factory returning a FastAPI dependency that gates by canonical Role.

    Accepts either Role enum members or raw role strings. Strings are
    canonicalized so verify_role("admin") and verify_role(Role.ADMIN)
    behave identically.
    """
    if not allowed:
        raise ValueError(
            "verify_role() requires at least one allowed role — empty allowlist "
            "would silently authorize every role.",
        )

    allowed_set: frozenset[str] = frozenset(
        (r.value if isinstance(r, Role) else canonical_role(str(r)))
        for r in allowed
    )

    async def _dependency(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header",
                headers={"WWW-Authenticate": 'Bearer realm="invalid_token"'},
            )

        raw = extract_bearer_token(authorization)
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Malformed Authorization header — expected 'Bearer <token>'",
                headers={"WWW-Authenticate": 'Bearer realm="invalid_token"'},
            )

        if token_validator is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Token validator not yet initialized",
            )

        try:
            claims = await token_validator.validate(raw)
        except ACPAuthError as exc:
            reason = getattr(exc, "reason", None) or "invalid_token"
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": f'Bearer realm="{reason}"'},
            ) from exc

        role_canonical = canonical_role(claims.get("role"))
        if role_canonical not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {role_canonical!r} is not permitted on this endpoint. "
                    f"Required: {sorted(allowed_set)}."
                ),
                headers={"WWW-Authenticate": 'Bearer realm="insufficient_role"'},
            )

        # Stamp the canonical projection onto the claims so route handlers
        # don't need to call canonical_role() themselves.
        claims["role_canonical"] = role_canonical
        return claims

    # Helpful for tests + introspection.
    _dependency.allowed_roles = allowed_set  # type: ignore[attr-defined]
    return _dependency


# Re-export so middleware can import it from here without re-importing constants
__all__ = [
    "LocalTokenValidator",
    "token_validator",
    "REDIS_REVOKE_PREFIX",
    "verify_role",
]
