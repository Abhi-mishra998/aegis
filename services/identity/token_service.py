from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.constants import (
    REDIS_AGENT_PREFIX,
    REDIS_REVOKE_PREFIX,
    REDIS_TOKEN_PREFIX,
)
from services.identity.exceptions import (
    AuthenticationError,
    TokenExpiredError,
    TokenRevokedError,
)

TOKEN_TYPE = "ACP_ACCESS"


class TokenService:
    """
    Handles JWT creation, validation, and Redis-backed revocation.

    Token payload includes:
      - jti, sub, tenant_id, org_id, role, typ, iat, exp
      - agent_id  (agent tokens)
      - user_id   (user tokens)
      - agent_status   (agent tokens — embedded at issuance, avoids registry lookup)
      - permissions    (agent tokens — [{tool_name, action}] list, avoids registry lookup)
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._secret = settings.JWT_SECRET_KEY
        self._algorithm = settings.JWT_ALGORITHM
        self._expiry_minutes = settings.JWT_EXPIRY_MINUTES

    async def issue(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        role: str = "agent",
        org_id: uuid.UUID | None = None,
        agent_status: str = "active",
        permissions: list[dict[str, str]] | None = None,
    ) -> tuple[str, int]:
        """
        Create a signed JWT and persist its hash in Redis.

        - org_id defaults to tenant_id when not supplied (backwards-compatible).
        - permissions and agent_status are embedded for agent tokens so the
          execution path never calls Registry at runtime.

        Returns:
            (token_string, expires_in_seconds)
        """
        now = datetime.now(tz=UTC)
        expiry_seconds = self._expiry_minutes * 60
        exp = now + timedelta(seconds=expiry_seconds)

        effective_org_id = org_id or tenant_id

        subject = str(agent_id or user_id)
        payload: dict[str, Any] = {
            "jti":       str(uuid.uuid4()),
            "sub":       subject,
            "tenant_id": str(tenant_id),
            "org_id":    str(effective_org_id),
            "role":      role,
            "typ":       TOKEN_TYPE,
            "iat":       int(now.timestamp()),
            "exp":       int(exp.timestamp()),
        }

        if agent_id:
            payload["agent_id"]     = str(agent_id)
            payload["agent_status"] = agent_status
            # Compact permission list — only what OPA needs
            payload["permissions"]  = [
                {"tool_name": p["tool_name"], "action": p.get("action", "ALLOW")}
                for p in (permissions or [])
            ]
        if user_id:
            payload["user_id"] = str(user_id)

        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        token_hash = self._hash(token)

        redis_key    = f"{REDIS_TOKEN_PREFIX}{token_hash}"
        subject_key  = f"{REDIS_AGENT_PREFIX}{subject}:tokens"

        await self._redis.setex(redis_key, expiry_seconds + 60, subject)  # type: ignore[misc]
        await self._redis.sadd(subject_key, token_hash)  # type: ignore[misc]
        await self._redis.expire(subject_key, expiry_seconds + 3600)  # type: ignore[misc]

        return token, expiry_seconds

    async def verify(self, token: str) -> dict[str, Any]:
        """
        Validate signature, expiry, Redis presence, and non-revocation.

        Returns decoded JWT payload dict.
        Raises TokenExpiredError, TokenRevokedError, AuthenticationError.
        """
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError() from None
        except JWTError:
            raise AuthenticationError("Invalid token") from None

        if payload.get("typ") != TOKEN_TYPE:
            raise AuthenticationError("Invalid token type")

        token_hash  = self._hash(token)
        revoke_key  = f"{REDIS_REVOKE_PREFIX}{token_hash}"
        active_key  = f"{REDIS_TOKEN_PREFIX}{token_hash}"

        if await self._redis.exists(revoke_key):
            raise TokenRevokedError()

        if not await self._redis.exists(active_key):
            raise AuthenticationError("Token not recognized")

        return dict(payload)

    # sprint-2.1 — gateway workers subscribe to this channel and drop the
    # matching entry from their in-process LRU on receipt. Keeps revocation
    # latency bounded to a single Redis hop instead of the 60-second LRU TTL.
    _REVOCATION_CHANNEL = "acp:token:revocations"

    async def _publish_revocation(self, token_hash: str) -> None:
        """Best-effort notify every gateway worker that this hash is dead."""
        try:
            await self._redis.publish(self._REVOCATION_CHANNEL, token_hash)
        except Exception:
            # Pub/Sub is best-effort — the Redis revocation key is still set,
            # so workers without the broadcast still reject the token (just
            # up to 60s slower on LRU hits). Don't fail the revoke call.
            pass

    async def revoke(self, token: str) -> bool:
        """Mark token as revoked. Returns True if token was active."""
        try:
            payload = jwt.decode(
                token, self._secret, algorithms=[self._algorithm],
                options={"verify_exp": False},
            )
        except JWTError:
            return False

        token_hash = self._hash(token)
        active_key = f"{REDIS_TOKEN_PREFIX}{token_hash}"
        revoke_key = f"{REDIS_REVOKE_PREFIX}{token_hash}"

        subject_id = await self._redis.get(active_key)
        if subject_id:
            subject_key = f"{REDIS_AGENT_PREFIX}{subject_id.decode()}:tokens"
            await self._redis.srem(subject_key, token_hash)  # type: ignore[misc]

        # Revoke TTL must cover the token's remaining lifetime. A hardcoded
        # 24h expiry would let a revoked token resurrect if
        # JWT_EXPIRY_MINUTES is ever set past 1440 (long-lived service
        # tokens). Floor at 60s so a clock skew or already-expired token
        # still gets a non-trivial deny window.
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            remaining = int(exp - datetime.now(tz=UTC).timestamp()) + 60
            revoke_ttl = max(remaining, 60)
        else:
            revoke_ttl = 86400  # fallback for tokens without exp
        await self._redis.setex(revoke_key, revoke_ttl, "1")  # type: ignore[misc]
        await self._redis.delete(active_key)
        await self._publish_revocation(token_hash)
        return True

    async def revoke_all_for_agent(self, agent_id: uuid.UUID) -> int:
        """Revoke all active tokens for an agent. O(M) where M = active token count."""
        return await self._revoke_all_for_subject(str(agent_id))

    async def revoke_all_for_subject(self, subject_id: str | uuid.UUID) -> int:
        """SEC-2026-07-31 (H11, H12): revoke every active token whose JWT
        ``sub`` claim matches ``subject_id``. Works for both agent and
        user tokens because :meth:`issue` writes them under the same
        ``REDIS_AGENT_PREFIX{subject}:tokens`` set. Callers:

          - password-reset confirm → nuke the user's live sessions so a
            stolen access token can't outlive the compromise.
          - refresh-token reuse detection → nuke the entire family when
            a rotated token is presented again.
        """
        return await self._revoke_all_for_subject(str(subject_id))

    async def _revoke_all_for_subject(self, subject: str) -> int:
        subject_key = f"{REDIS_AGENT_PREFIX}{subject}:tokens"
        token_hashes = await self._redis.smembers(subject_key)  # type: ignore[misc]

        if not token_hashes:
            return 0

        count = 0
        for h in token_hashes:
            token_hash = h.decode()
            active_key = f"{REDIS_TOKEN_PREFIX}{token_hash}"
            # Match revoke TTL to the token's remaining lifetime — see
            # `revoke()` for the resurrection-window rationale. The active
            # key's own TTL was set to `expiry_seconds + 60` at issuance,
            # so its remaining TTL is the token's remaining lifetime.
            remaining = await self._redis.ttl(active_key)  # type: ignore[misc]
            revoke_ttl = max(int(remaining) if remaining and remaining > 0 else 86400, 60)
            await self._redis.setex(f"{REDIS_REVOKE_PREFIX}{token_hash}", revoke_ttl, "1")
            await self._redis.delete(active_key)
            await self._publish_revocation(token_hash)
            count += 1

        await self._redis.delete(subject_key)
        return count

    def decode_unverified(self, token: str) -> dict[str, Any] | None:
        """Decode without Redis check — for introspection only."""
        try:
            return dict(
                jwt.decode(
                    token, self._secret, algorithms=[self._algorithm],
                    options={"verify_exp": False, "verify_signature": True},
                )
            )
        except JWTError:
            return None

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
