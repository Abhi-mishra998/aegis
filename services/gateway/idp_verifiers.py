"""ATF v3.2 §4.2 — external IdP acceptance dispatcher.

Wires the three verifier libraries (`sdk/common/spiffe_auth`,
`entra_auth`, `okta_xaa`) into the gateway auth path.

Design invariants:

  * Each adapter is OFF unless its config is set (blank env var → skip).
  * Every failure path raises the same `ACPAuthError` with a NON-ORACLE
    message ("Unauthorized"); per-adapter reason is emitted only to the
    internal counter so the WWW-Authenticate body reveals nothing about
    which validator branch was tried.
  * JWKS is cached in Redis + in-process LRU; cache-fetch failures
    fail CLOSED (raise ACPAuthError), never fail open with an empty
    key set.
  * The returned payload mirrors the LocalTokenValidator shape so the
    downstream middleware doesn't need to know which adapter validated.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
import structlog
from jose import JWTError, jwt
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.entra_auth import EntraVerifyError
from sdk.common.entra_auth import verify as entra_verify
from sdk.common.exceptions import ACPAuthError
from sdk.common.okta_xaa import OktaVerifyError
from sdk.common.okta_xaa import verify as okta_verify
from sdk.common.spiffe_auth import SpiffeVerifyError
from sdk.common.spiffe_auth import verify as spiffe_verify

logger = structlog.get_logger(__name__)

# Small in-process JWKS cache keyed by (adapter, url). Redis is the
# cross-process source of truth; the LRU is a µs-fast layer for hot
# workers. Never larger than 8 entries — total number of live IdP
# JWKS across all supported tenants is small.
_JWKS_LRU: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_JWKS_LRU_MAX = 8


def _lru_get(adapter: str, url: str, ttl_seconds: int) -> dict[str, Any] | None:
    key = (adapter, url)
    hit = _JWKS_LRU.get(key)
    if hit is None:
        return None
    expires_at, jwks = hit
    if time.time() >= expires_at:
        _JWKS_LRU.pop(key, None)
        return None
    return jwks


def _lru_put(adapter: str, url: str, jwks: dict[str, Any], ttl_seconds: int) -> None:
    key = (adapter, url)
    if len(_JWKS_LRU) >= _JWKS_LRU_MAX and key not in _JWKS_LRU:
        # Drop the closest-to-expiry entry — bounded eviction.
        oldest = min(_JWKS_LRU.items(), key=lambda kv: kv[1][0])[0]
        _JWKS_LRU.pop(oldest, None)
    _JWKS_LRU[key] = (time.time() + ttl_seconds, jwks)


async def _fetch_jwks(url: str, adapter: str, ttl_seconds: int, redis: Redis | None) -> dict[str, Any]:
    """Fetch a JWKS with in-process + Redis caching. Fails CLOSED."""
    cached = _lru_get(adapter, url, ttl_seconds)
    if cached is not None:
        return cached

    redis_key = f"acp:jwks:{adapter}:{url}"
    if redis is not None:
        try:
            raw = await redis.get(redis_key)
        except Exception as exc:
            # Redis blip; fall through to HTTP fetch — the LRU catches the
            # next request.
            logger.warning("jwks_redis_read_failed", adapter=adapter, error=str(exc))
        else:
            if raw:
                try:
                    jwks = json.loads(raw)
                    _lru_put(adapter, url, jwks, ttl_seconds)
                    return jwks
                except json.JSONDecodeError:
                    logger.warning("jwks_redis_corrupt", adapter=adapter)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise ACPAuthError(
            f"IdP JWKS unreachable: {type(exc).__name__}",
        ) from exc

    if resp.status_code != 200:
        raise ACPAuthError(f"IdP JWKS returned HTTP {resp.status_code}")

    try:
        jwks = resp.json()
    except json.JSONDecodeError as exc:
        raise ACPAuthError("IdP JWKS is not JSON") from exc

    if not isinstance(jwks, dict) or not jwks.get("keys"):
        raise ACPAuthError("IdP JWKS has no keys")

    _lru_put(adapter, url, jwks, ttl_seconds)
    if redis is not None:
        try:
            await redis.setex(redis_key, ttl_seconds, json.dumps(jwks))
        except Exception as exc:
            logger.warning("jwks_redis_write_failed", adapter=adapter, error=str(exc))
    return jwks


# ─────────────────────────────────────────────────────────────
# Token-shape detectors — cheap, no crypto, header + unverified claims.
# ─────────────────────────────────────────────────────────────


def _peek_unverified_sub(token: str) -> str:
    try:
        return str(jwt.get_unverified_claims(token).get("sub") or "")
    except JWTError:
        return ""


def _peek_unverified_iss(token: str) -> str:
    try:
        return str(jwt.get_unverified_claims(token).get("iss") or "")
    except JWTError:
        return ""


def looks_like_spiffe(token: str) -> bool:
    if not settings.SPIFFE_TRUST_DOMAIN:
        return False
    return _peek_unverified_sub(token).startswith("spiffe://")


def looks_like_entra(token: str) -> bool:
    if not settings.ENTRA_TENANT_ID:
        return False
    iss = _peek_unverified_iss(token)
    return "login.microsoftonline.com" in iss and settings.ENTRA_TENANT_ID in iss


def looks_like_okta(token: str) -> bool:
    if not settings.OKTA_ISSUER:
        return False
    return _peek_unverified_iss(token) == settings.OKTA_ISSUER


# ─────────────────────────────────────────────────────────────
# Verifiers — each returns a payload mirroring LocalTokenValidator's
# shape (sub, tenant_id, role, exp, jti, auth_provider). Missing
# claims get safe defaults so RBAC still resolves.
# ─────────────────────────────────────────────────────────────


async def verify_spiffe_token(token: str, redis: Redis | None = None) -> dict[str, Any]:
    if not settings.SPIFFE_TRUST_DOMAIN:
        raise ACPAuthError("SPIFFE acceptance disabled")

    # Trust bundle is operator-supplied JSON — SPIFFE doesn't publish a
    # JWKS-URL like OIDC does. If missing → fail closed.
    if not settings.SPIFFE_TRUST_BUNDLE_JSON:
        raise ACPAuthError("SPIFFE trust bundle not configured")
    try:
        trust_bundle = json.loads(settings.SPIFFE_TRUST_BUNDLE_JSON)
    except json.JSONDecodeError as exc:
        raise ACPAuthError("SPIFFE trust bundle is not JSON") from exc

    try:
        ident = spiffe_verify(
            token,
            trust_bundle,
            expected_trust_domain=settings.SPIFFE_TRUST_DOMAIN,
            expected_audience=settings.SPIFFE_AUDIENCE or None,
        )
    except SpiffeVerifyError as exc:
        logger.info("spiffe_verify_failed", error=str(exc))
        raise ACPAuthError("Unauthorized") from exc

    return _to_payload(
        sub=ident.spiffe_id,
        tenant_id=ident.trust_domain,
        exp=ident.exp,
        role="agent",           # SPIFFE workloads = agent by default
        auth_provider="spiffe",
    )


async def verify_entra_token(token: str, redis: Redis | None = None) -> dict[str, Any]:
    if not settings.ENTRA_TENANT_ID:
        raise ACPAuthError("Entra acceptance disabled")
    if not settings.ENTRA_AUDIENCE:
        raise ACPAuthError("Entra audience not configured")

    ttl = int(settings.ENTRA_JWKS_CACHE_SECONDS)
    issuer_jwks_url = (
        f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}"
        "/discovery/v2.0/keys"
    )

    async def _loader(_iss: str) -> dict[str, Any]:
        return await _fetch_jwks(issuer_jwks_url, "entra", ttl, redis)

    try:
        ident = entra_verify(
            token,
            expected_tenant_id=settings.ENTRA_TENANT_ID,
            expected_audience=settings.ENTRA_AUDIENCE,
            jwks_loader=_loader,
        )
    except EntraVerifyError as exc:
        logger.info("entra_verify_failed", error=str(exc))
        raise ACPAuthError("Unauthorized") from exc

    role = _role_from_entra(ident.roles)
    return _to_payload(
        sub=ident.subject,
        tenant_id=ident.tenant_id,
        exp=ident.exp,
        role=role,
        auth_provider="entra",
    )


async def verify_okta_token(token: str, redis: Redis | None = None) -> dict[str, Any]:
    if not settings.OKTA_ISSUER:
        raise ACPAuthError("Okta acceptance disabled")
    if not settings.OKTA_AUDIENCE:
        raise ACPAuthError("Okta audience not configured")

    ttl = int(settings.OKTA_JWKS_CACHE_SECONDS)
    # Okta's JWKS lives at {issuer}/v1/keys.
    jwks_url = f"{settings.OKTA_ISSUER.rstrip('/')}/v1/keys"

    async def _loader(_iss: str) -> dict[str, Any]:
        return await _fetch_jwks(jwks_url, "okta", ttl, redis)

    try:
        ident = okta_verify(
            token,
            expected_issuer=settings.OKTA_ISSUER,
            expected_audience=settings.OKTA_AUDIENCE,
            jwks_loader=_loader,
        )
    except OktaVerifyError as exc:
        logger.info("okta_verify_failed", error=str(exc))
        raise ACPAuthError("Unauthorized") from exc

    role = _role_from_okta_scopes(ident.scopes)
    return _to_payload(
        sub=ident.subject,
        tenant_id="",       # tenant_id filled from X-Tenant-ID or Aegis Profile
        exp=ident.exp,
        role=role,
        auth_provider="okta",
    )


def _role_from_entra(roles: list[str]) -> str:
    """Map Entra app roles → Aegis RBAC. Falls back to READ_ONLY."""
    for r in roles:
        upper = r.upper()
        if upper in ("OWNER", "ADMIN", "SECURITY_ANALYST", "DEVELOPER", "READ_ONLY"):
            return upper
    return "READ_ONLY"


def _role_from_okta_scopes(scopes: list[str]) -> str:
    """Map Okta XAA scopes → Aegis RBAC. `agent.admin` → ADMIN; anything
    with `agent.execute` → agent; else READ_ONLY."""
    if any(s in ("agent.admin", "aegis.admin") for s in scopes):
        return "ADMIN"
    if any(s in ("agent.execute", "aegis.execute") for s in scopes):
        return "agent"
    return "READ_ONLY"


def _to_payload(
    *,
    sub: str,
    tenant_id: str,
    exp: int,
    role: str,
    auth_provider: str,
) -> dict[str, Any]:
    """Build a payload that mirrors LocalTokenValidator's shape so
    downstream RBAC / audit code doesn't need to know the IdP branch.
    `jti` is derived deterministically from (sub, exp) so per-token
    replay detection still works without the IdP minting one."""
    import hashlib
    jti = "idp:" + hashlib.sha256(f"{auth_provider}:{sub}:{exp}".encode()).hexdigest()[:16]
    return {
        "sub":           sub,
        "tenant_id":     tenant_id,
        "role":          role,
        "exp":           exp,
        "jti":           jti,
        "auth_provider": auth_provider,
    }
