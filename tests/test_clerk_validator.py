"""
Tests for services/gateway/auth_clerk.py — the Clerk JWKS validator.

Uses a freshly-generated RSA keypair: the public key is injected into the
in-process JWKS cache so the validator does not need to hit Clerk's real
JWKS endpoint, and the private key signs the test fixtures.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk
from jose import jwt

from sdk.common import clerk_auth as auth_clerk  # state lives in sdk.common.clerk_auth
from sdk.common.clerk_auth import (
    ClerkTokenValidator,
    looks_like_clerk_token,
    normalize_clerk_role,
)
from sdk.common.config import settings
from sdk.common.exceptions import ACPAuthError

_TEST_ISSUER = "https://test-clerk.example.com"
_TEST_KID = "test-kid-001"


@pytest.fixture(autouse=True)
def _isolate_validator_state(monkeypatch):
    """Each test gets a clean JWKS cache + clear singletons."""
    monkeypatch.setattr(settings, "CLERK_ISSUER", _TEST_ISSUER, raising=False)
    monkeypatch.setattr(settings, "CLERK_FRONTEND_API", _TEST_ISSUER, raising=False)
    monkeypatch.setattr(
        settings, "CLERK_JWKS_URL", f"{_TEST_ISSUER}/.well-known/jwks.json",
        raising=False,
    )
    monkeypatch.setattr(settings, "CLERK_JWKS_CACHE_SECONDS", 3600, raising=False)
    auth_clerk._jwks_cache = None
    auth_clerk._clerk_validator = None
    yield
    auth_clerk._jwks_cache = None
    auth_clerk._clerk_validator = None


@pytest.fixture
def rsa_keypair():
    """Generate an RSA keypair + the matching JWK for the validator's cache."""
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend(),
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    public_jwk = jose_jwk.construct(public_pem, algorithm="RS256").to_dict()
    public_jwk["kid"] = _TEST_KID
    public_jwk["alg"] = "RS256"
    return {
        "private_pem": private_pem,
        "public_jwk": public_jwk,
    }


def _seed_jwks(public_jwk: dict[str, Any]) -> None:
    """Bypass the HTTP fetch by hand-seeding the JWKS cache."""
    cache = auth_clerk._get_jwks_cache()
    cache._keys_by_kid = {public_jwk["kid"]: public_jwk}
    cache._expires_at = time.monotonic() + 3600


def _make_token(
    rsa_keypair: dict[str, Any],
    *,
    sub: str = "user_test_001",
    org_id: str = "org_test_001",
    org_role: str = "org:admin",
    aegis_tenant_id: str = "11111111-1111-1111-1111-111111111111",
    aegis_role: str | None = "org:admin",
    email: str = "alice@example.com",
    issuer: str = _TEST_ISSUER,
    exp_offset_seconds: int = 600,
    kid: str = _TEST_KID,
) -> str:
    """Sign a JWT with the test private key."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": issuer,
        "iat": now,
        "exp": now + exp_offset_seconds,
        "org_id": org_id,
        "org_role": org_role,
        "email": email,
    }
    if aegis_tenant_id:
        payload["aegis_tenant_id"] = aegis_tenant_id
        payload["aegis_org_id"] = aegis_tenant_id
    if aegis_role is not None:
        payload["aegis_role"] = aegis_role
    return jwt.encode(
        payload,
        rsa_keypair["private_pem"],
        algorithm="RS256",
        headers={"kid": kid},
    )


# ---------------------------------------------------------------------------
# normalize_clerk_role
# ---------------------------------------------------------------------------


def test_normalize_role_handles_canonical_clerk_values():
    assert normalize_clerk_role("org:owner") == "OWNER"
    assert normalize_clerk_role("org:admin") == "ADMIN"
    assert normalize_clerk_role("org:security_analyst") == "SECURITY_ANALYST"
    assert normalize_clerk_role("org:developer") == "DEVELOPER"
    assert normalize_clerk_role("org:read_only") == "READ_ONLY"


def test_normalize_role_falls_back_for_unknown_values():
    assert normalize_clerk_role("org:billing") == "BILLING"
    assert normalize_clerk_role(None) == "OWNER"
    assert normalize_clerk_role("") == "OWNER"


# ---------------------------------------------------------------------------
# looks_like_clerk_token
# ---------------------------------------------------------------------------


def test_looks_like_clerk_token_matches_issuer(rsa_keypair):
    token = _make_token(rsa_keypair)
    assert looks_like_clerk_token(token) is True


def test_looks_like_clerk_token_rejects_legacy_token(rsa_keypair):
    token = _make_token(rsa_keypair, issuer="aegis-internal")
    assert looks_like_clerk_token(token) is False


def test_looks_like_clerk_token_handles_garbage_input():
    assert looks_like_clerk_token("not-a-jwt") is False
    assert looks_like_clerk_token("") is False


# ---------------------------------------------------------------------------
# ClerkTokenValidator.validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_returns_canonical_payload(rsa_keypair):
    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(rsa_keypair)

    validator = ClerkTokenValidator(redis_client=None)
    payload = await validator.validate(token)

    assert payload["sub"] == "user_test_001"
    assert payload["clerk_user_id"] == "user_test_001"
    assert payload["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["role"] == "ADMIN"
    assert payload["email"] == "alice@example.com"
    assert payload["auth_provider"] == "clerk"
    assert payload["jti"].startswith("clerk-")


@pytest.mark.asyncio
async def test_validate_uses_native_org_role_when_template_role_absent(rsa_keypair):
    """Fallback path: no custom `aegis` template configured."""
    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(
        rsa_keypair,
        aegis_role=None,
        org_role="org:security_analyst",
    )

    validator = ClerkTokenValidator(redis_client=None)
    payload = await validator.validate(token)

    assert payload["role"] == "SECURITY_ANALYST"


@pytest.mark.asyncio
async def test_validate_rejects_expired_token(rsa_keypair):
    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(rsa_keypair, exp_offset_seconds=-30)

    validator = ClerkTokenValidator(redis_client=None)
    with pytest.raises(ACPAuthError, match="expired"):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_validate_rejects_wrong_issuer(rsa_keypair):
    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(rsa_keypair, issuer="https://attacker.example.com")

    validator = ClerkTokenValidator(redis_client=None)
    with pytest.raises(ACPAuthError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_validate_rejects_unknown_kid(rsa_keypair):
    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(rsa_keypair, kid="rogue-kid")

    # Without a refresh path that finds the rogue kid, the validator must
    # ultimately raise ACPAuthError ("No matching JWK").
    validator = ClerkTokenValidator(redis_client=None)
    with pytest.raises(ACPAuthError):
        # On miss the validator force-refreshes, which here will try to GET
        # the configured CLERK_JWKS_URL. We don't want a real network call;
        # the cache _refresh raises if URL is unreachable. So patch the
        # force_refresh to no-op.
        cache = auth_clerk._get_jwks_cache()

        async def _noop(redis=None):
            return None

        cache.force_refresh = _noop  # type: ignore[assignment]
        await validator.validate(token)


@pytest.mark.asyncio
async def test_validate_rejects_tampered_signature(rsa_keypair):
    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(rsa_keypair)
    tampered = token[:-2] + ("A" if not token.endswith("A") else "B")

    validator = ClerkTokenValidator(redis_client=None)
    with pytest.raises(ACPAuthError):
        await validator.validate(tampered)


@pytest.mark.asyncio
async def test_validate_falls_back_to_redis_org_mapping(rsa_keypair):
    """When the JWT carries no aegis_tenant_id, look up Redis by clerk_org_id."""

    class _FakeRedis:
        def __init__(self):
            self.store = {
                b"acp:clerk:org-tenant:org_test_001":
                    b"99999999-9999-9999-9999-999999999999",
            }

        async def get(self, key):
            return self.store.get(
                key.encode("utf-8") if isinstance(key, str) else key,
            )

    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(rsa_keypair, aegis_tenant_id="", aegis_role=None)

    validator = ClerkTokenValidator(redis_client=_FakeRedis())
    payload = await validator.validate(token)

    assert payload["tenant_id"] == "99999999-9999-9999-9999-999999999999"
    assert payload["role"] == "ADMIN"  # from org_role fallback


# ---------------------------------------------------------------------------
# U4 — HS256 + Clerk-iss downgrade attack
# ---------------------------------------------------------------------------
#
# `looks_like_clerk_token(token)` reads the UNVERIFIED `iss` claim to pick
# the validator. If an attacker who knows JWT_SECRET_KEY can mint an HS256
# token with iss=<clerk_issuer>, the dispatcher would route it to the Clerk
# path and (in a misconfigured world) verify it with HS256, bypassing JWKS.
#
# The fix lives in services/gateway/auth.py: the dispatcher REQUIRES the
# JWT alg header to be RS256/RS512 when the token claims to be Clerk.


@pytest.mark.asyncio
async def test_hs256_token_with_clerk_iss_is_rejected_at_dispatch(monkeypatch):
    """U4: an HS256 JWT with iss=<clerk_issuer> must be rejected — not
    routed to the Clerk path and not validated as legacy either."""
    from sdk.common.config import settings as gw_settings
    from services.gateway.auth import LocalTokenValidator, _LOCAL_TOKEN_LRU

    # Make sure cached state from earlier tests can't short-circuit us.
    _LOCAL_TOKEN_LRU.clear()

    # Force the gateway into the dual-provider mode where both legacy HS
    # and Clerk RS are accepted — this is the riskiest dispatch surface.
    monkeypatch.setattr(gw_settings, "ACP_AUTH_PROVIDER", "both", raising=False)
    # JWT_SECRET_KEY is the legacy HS256 signing key. An attacker who
    # learns it (or any insider) gets the ability to mint HS tokens.
    monkeypatch.setattr(
        gw_settings, "JWT_SECRET_KEY",
        "test-legacy-hs256-secret-for-u4-only",
        raising=False,
    )

    # Forge an HS256 token bearing the Clerk issuer claim. This is the
    # downgrade payload: same iss as a real Clerk token, but signed with
    # the legacy HS secret. There is NO `kid` header — and we don't need
    # one, because the dispatcher must reject this long before the Clerk
    # validator's JWKS lookup runs.
    now = int(time.time())
    forged_payload = {
        "iss": _TEST_ISSUER,
        "sub": "user_attacker",
        "aegis_tenant_id": "11111111-1111-1111-1111-111111111111",
        "aegis_org_id":   "11111111-1111-1111-1111-111111111111",
        "aegis_role":     "org:admin",
        "email": "attacker@example.com",
        "iat": now,
        "exp": now + 600,
        # Legacy required claims so it would otherwise pass _validate_signature.
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "role":      "OWNER",
        "jti":       "forged-jti-001",
    }
    forged_token = jwt.encode(
        forged_payload,
        "test-legacy-hs256-secret-for-u4-only",
        algorithm="HS256",
    )

    validator = LocalTokenValidator(redis_client=None)
    with pytest.raises(ACPAuthError, match="Invalid Clerk token alg"):
        await validator.validate(forged_token)


@pytest.mark.asyncio
async def test_rs256_clerk_token_still_accepted_after_alg_gate(monkeypatch, rsa_keypair):
    """U4 regression check: a legitimate RS256 Clerk token must STILL be
    accepted by the dispatcher after the alg gate is in place."""
    from sdk.common.config import settings as gw_settings
    from services.gateway.auth import LocalTokenValidator, _LOCAL_TOKEN_LRU

    _LOCAL_TOKEN_LRU.clear()
    monkeypatch.setattr(gw_settings, "ACP_AUTH_PROVIDER", "both", raising=False)

    _seed_jwks(rsa_keypair["public_jwk"])
    token = _make_token(rsa_keypair)

    validator = LocalTokenValidator(redis_client=None)
    payload = await validator.validate(token)

    assert payload["sub"] == "user_test_001"
    assert payload["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["auth_provider"] == "clerk"
