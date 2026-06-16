"""
ACP — Clerk JWKS Validator (shared between gateway + identity)
==============================================================
Validates Clerk-issued JWTs (RS256, rotating JWKS). The gateway uses this
alongside its HS256 self-issued validator (dispatched in
services/gateway/auth.py by the token's issuer claim). The identity
service uses this directly inside POST /auth/clerk/provision to
authenticate the freshly-signed-up user before persisting Aegis rows.

Trust model + payload shape unchanged from the original gateway-only
implementation — the body of this module is just re-located so both
services can import from one source of truth.

Trust model:
  - JWKS is fetched lazily from CLERK_JWKS_URL on first use, cached in
    Redis (and in-process) for CLERK_JWKS_CACHE_SECONDS.
  - On `kid` cache miss, the JWKS is refreshed once before failing. This
    closes the window during a Clerk key rotation where the new kid is
    served before our cached copy expires.
  - Signature verification uses python-jose's `jwt.decode` with the
    matched JWK. iss, exp and (optionally) audience claims are checked
    by python-jose itself.
  - Returned payload shape MATCHES the HS256 validator's so downstream
    middleware does NOT need to know which provider signed the token.

Claims contract for the `aegis` JWT template (configured in Clerk dashboard):
    {
      "sub":              <clerk user_id>,
      "iss":              <CLERK_ISSUER>,
      "aegis_tenant_id":  <our tenant uuid stored on org.public_metadata>,
      "aegis_org_id":     <our org  uuid stored on org.public_metadata>,
      "aegis_role":       <"org:owner" | "org:admin" | ...>,
      "email":            <primary email address>,
      "exp":              <unix epoch seconds>
    }

Output payload shape (matches what auth.py emits, plus `clerk_user_id`):
    {
      "sub":             <clerk user_id>,             # for verify_role + audit
      "tenant_id":       <aegis_tenant_id claim>,     # canonical aegis tenant
      "org_id":          <aegis_org_id claim>,        # canonical aegis org
      "role":            <normalized aegis role>,     # OWNER/ADMIN/SECURITY_ANALYST/DEVELOPER/READ_ONLY
      "email":           <claim>,
      "exp":             <claim>,
      "jti":             <CLERK_USER_JTI>,            # synthesized from sub+iat
      "clerk_user_id":   <claim sub>,
      "auth_provider":   "clerk",
    }
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import httpx
import structlog
from jose import ExpiredSignatureError, JWTError, jwt
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.exceptions import ACPAuthError

logger = structlog.get_logger(__name__)

REDIS_JWKS_CACHE_KEY = "acp:clerk:jwks"

# Mirror of the webhook receiver's key shape (services/identity/webhooks_clerk.py).
# Updated lookup-side here keeps the two in lockstep.
_ORG_TO_TENANT_KEY_PREFIX = "acp:clerk:org-tenant:"

# Clerk → Aegis role canonicalization. Clerk emits "org:owner" / "org:admin"
# style claims; we collapse them to the 5-tier UPPER_SNAKE_CASE vocabulary
# the rest of the codebase uses.
_CLERK_ROLE_MAP: dict[str, str] = {
    "org:owner": "OWNER",
    "org:admin": "ADMIN",
    "org:security_analyst": "SECURITY_ANALYST",
    "org:developer": "DEVELOPER",
    "org:read_only": "READ_ONLY",
}


def normalize_clerk_role(raw: str | None) -> str:
    """Collapse a Clerk org role (`org:foo`) into the canonical Aegis vocab."""
    if not raw:
        return "OWNER"
    mapped = _CLERK_ROLE_MAP.get(raw)
    if mapped:
        return mapped
    upper = raw.upper()
    if upper.startswith("ORG:"):
        upper = upper[4:]
    return upper or "OWNER"


class _JWKSCache:
    """In-process JWKS cache shared with a Redis-backed second tier.

    Fetches the JWKS document over HTTPS, parses it into a `{kid: jwk}`
    map for O(1) lookup, and serves it for `ttl_seconds`. On a `kid`
    miss, callers should invoke `force_refresh()` once before treating
    the signature as invalid — Clerk's key rotation may have served a
    new kid before our TTL expired.
    """

    def __init__(self, *, ttl_seconds: int, jwks_url: str) -> None:
        self._ttl = ttl_seconds
        self._url = jwks_url
        self._keys_by_kid: dict[str, dict[str, Any]] = {}
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return bool(self._keys_by_kid) and time.monotonic() < self._expires_at

    async def get_key(self, kid: str, redis: Redis | None = None) -> dict[str, Any] | None:
        if not self.loaded:
            await self._refresh(redis=redis)
        key = self._keys_by_kid.get(kid)
        if key is None:
            # Cache miss could be a stale cache during key rotation. Refresh once.
            await self._refresh(redis=redis, force=True)
            key = self._keys_by_kid.get(kid)
        return key

    async def force_refresh(self, redis: Redis | None = None) -> None:
        await self._refresh(redis=redis, force=True)

    async def _refresh(self, *, redis: Redis | None = None, force: bool = False) -> None:
        async with self._lock:
            if not force and self.loaded:
                return

            # Try Redis tier first — every worker shares it so we don't slam
            # Clerk's JWKS endpoint on cold start.
            if redis is not None and not force:
                try:
                    cached = await redis.get(REDIS_JWKS_CACHE_KEY)
                    if cached:
                        doc = json.loads(cached if isinstance(cached, str) else cached.decode("utf-8"))
                        self._ingest(doc)
                        return
                except Exception as exc:
                    logger.warning("clerk_jwks_redis_read_failed", error=str(exc))

            if not self._url:
                raise ACPAuthError("CLERK_JWKS_URL is not configured")

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(self._url)
                    resp.raise_for_status()
                    doc = resp.json()
            except httpx.HTTPError as exc:
                logger.error("clerk_jwks_fetch_failed", url=self._url, error=str(exc))
                raise ACPAuthError("Failed to fetch Clerk JWKS") from exc

            self._ingest(doc)

            if redis is not None:
                try:
                    await redis.setex(
                        REDIS_JWKS_CACHE_KEY,
                        max(60, self._ttl),
                        json.dumps(doc),
                    )
                except Exception as exc:
                    logger.warning("clerk_jwks_redis_write_failed", error=str(exc))

    def _ingest(self, doc: dict[str, Any]) -> None:
        keys = doc.get("keys", [])
        if not isinstance(keys, list):
            raise ACPAuthError("Malformed JWKS document — 'keys' is not a list")
        self._keys_by_kid = {
            jwk["kid"]: jwk
            for jwk in keys
            if isinstance(jwk, dict) and isinstance(jwk.get("kid"), str)
        }
        self._expires_at = time.monotonic() + self._ttl


_jwks_cache: _JWKSCache | None = None


def _get_jwks_cache() -> _JWKSCache:
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = _JWKSCache(
            ttl_seconds=settings.CLERK_JWKS_CACHE_SECONDS,
            jwks_url=settings.CLERK_JWKS_URL,
        )
    return _jwks_cache


class ClerkTokenValidator:
    """Validates a Clerk-issued JWT against the configured JWKS.

    Stateless apart from the JWKS cache. Designed to be picked up by the
    same SecurityMiddleware path that hosts LocalTokenValidator — the
    returned payload shape matches.
    """

    def __init__(self, redis_client: Redis | None = None) -> None:
        self._redis = redis_client
        self._issuer = settings.CLERK_ISSUER or settings.CLERK_FRONTEND_API
        if not self._issuer:
            raise ACPAuthError(
                "Clerk validator instantiated without CLERK_ISSUER or CLERK_FRONTEND_API",
            )

    async def validate(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise ACPAuthError(f"Invalid token header: {exc}") from exc

        kid = header.get("kid")
        if not kid:
            raise ACPAuthError("Clerk token missing 'kid' header")

        cache = _get_jwks_cache()
        jwk = await cache.get_key(kid, redis=self._redis)
        if jwk is None:
            raise ACPAuthError(f"No matching JWK for kid {kid!r}")

        try:
            payload = jwt.decode(
                token,
                jwk,
                algorithms=[jwk.get("alg") or "RS256"],
                issuer=self._issuer,
                options={"verify_aud": False, "require_iat": False},
            )
        except ExpiredSignatureError as exc:
            raise ACPAuthError("Clerk token has expired") from exc
        except JWTError as exc:
            raise ACPAuthError(f"Clerk token signature invalid: {exc}") from exc

        canonical = self._canonicalize(payload)

        # When the customer hasn't (yet) configured a custom `aegis` JWT
        # template, the canonical tenant_id claim is empty — fall back to
        # the org→tenant mapping the webhook receiver maintains in Redis.
        # This keeps the deployment workable from day 1 with only the
        # default Clerk JWT.
        if not canonical.get("tenant_id"):
            clerk_org_id = payload.get("org_id") or canonical.get("org_id")
            resolved = await self._resolve_tenant_id_from_redis(clerk_org_id)
            if resolved:
                canonical["tenant_id"] = resolved
                if not canonical.get("org_id"):
                    canonical["org_id"] = resolved

        # Fall back further to the native Clerk org_role claim when the
        # template-mapped aegis_role isn't set yet.
        if (not canonical.get("role") or canonical.get("role") == "OWNER") and payload.get("org_role"):
            canonical["role"] = normalize_clerk_role(payload.get("org_role"))

        return canonical

    async def _resolve_tenant_id_from_redis(self, clerk_org_id: str | None) -> str | None:
        """Look up the aegis tenant_id the webhook receiver cached for an org."""
        if not clerk_org_id or self._redis is None:
            return None
        try:
            raw = await self._redis.get(f"{_ORG_TO_TENANT_KEY_PREFIX}{clerk_org_id}")
        except Exception as exc:
            logger.warning("clerk_org_tenant_redis_read_failed", error=str(exc))
            return None
        if not raw:
            return None
        if isinstance(raw, bytes):
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return str(raw)

    def _canonicalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Reshape Clerk JWT claims into the payload contract auth.py emits."""
        clerk_user_id = payload.get("sub", "")
        tenant_id = payload.get("aegis_tenant_id") or ""
        # SaaS strict invariant (`ck_users_org_tenant_match`): user.org_id ==
        # user.tenant_id. The Clerk webhook used to write
        # `aegis_org_id = Organization.id` into the org's public_metadata —
        # a SEPARATE UUID from `aegis_tenant_id = Tenant.tenant_id`. Every
        # write request from those existing Clerk JWTs would then 403 with
        # "Org consistency violation during gateway write path" because the
        # gateway invariant check compares org_id to tenant_id directly.
        #
        # The fix is two-sided:
        #   1. webhooks_clerk.py now writes aegis_org_id = tenant.tenant_id
        #      (so freshly-minted tokens carry matching values).
        #   2. Here we coerce: for Clerk users, org_id IS tenant_id by
        #      definition (single-tenant-per-org Sprint-1 model). A stale
        #      aegis_org_id claim from a JWT minted before fix #1 must not
        #      break the user's session.
        org_id = tenant_id
        role = normalize_clerk_role(payload.get("aegis_role"))
        email = payload.get("email", "")
        exp = payload.get("exp", 0)
        iat = payload.get("iat", int(time.time()))

        # Synthesize a stable jti the downstream cache + revocation paths can
        # key on. SHA256(sub + iat) gives us per-token uniqueness without
        # forcing Clerk to mint a `jti` claim (which it doesn't by default).
        jti_seed = f"{clerk_user_id}:{iat}".encode("utf-8")
        jti = f"clerk-{hashlib.sha256(jti_seed).hexdigest()[:32]}"

        canonical: dict[str, Any] = {
            "sub": clerk_user_id,
            "tenant_id": tenant_id,
            "org_id": org_id,
            "role": role,
            "email": email,
            "exp": exp,
            "iat": iat,
            "jti": jti,
            "clerk_user_id": clerk_user_id,
            "auth_provider": "clerk",
        }
        return canonical


_clerk_validator: ClerkTokenValidator | None = None


def get_clerk_validator(redis_client: Redis | None = None) -> ClerkTokenValidator:
    global _clerk_validator
    if _clerk_validator is None:
        _clerk_validator = ClerkTokenValidator(redis_client=redis_client)
    return _clerk_validator


def looks_like_clerk_token(token: str) -> bool:
    """Cheap heuristic — does the unverified payload carry our Clerk issuer?

    Used by SmartTokenValidator to pick a path without a JWKS round trip
    when the token is obviously legacy.
    """
    if not token or not settings.CLERK_ISSUER:
        return False
    try:
        unverified = jwt.get_unverified_claims(token)
    except JWTError:
        return False
    iss = unverified.get("iss")
    return iss == settings.CLERK_ISSUER


__all__ = [
    "ClerkTokenValidator",
    "get_clerk_validator",
    "looks_like_clerk_token",
    "normalize_clerk_role",
]
