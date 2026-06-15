"""
Sprint 0 security tests for the OIDC SSO surface.

Covers:
  - id_token signature is verified against the IdP's JWKS (CRITICAL fix)
  - `alg: none` and HMAC algs are refused (alg-confusion attack)
  - Tampered claims fail verification
  - Wrong audience, wrong issuer, expired tokens are rejected
  - PKCE helpers produce RFC-7636-conformant pairs
  - /auth/sso/{provider} rejects requests with no/invalid tenant_id (no demo fallback)

These tests do NOT require the live stack — JWKS/discovery HTTP calls are patched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from jose import jwt as jose_jwt
from jose.backends import RSAKey
from jose.constants import ALGORITHMS

from services.identity import oidc

# ---------------------------------------------------------------------------
# Helpers — generate an RSA keypair, expose JWKS for the provider under test.
# ---------------------------------------------------------------------------

PROVIDER = "google"  # any enabled provider config in oidc.py works
KID = "test-kid-1"
ISSUER = "https://accounts.google.com"
AUDIENCE = "test-client-id"


def _make_rsa_jwk(kid: str = KID) -> tuple[dict, dict]:
    """Return (private_jwk, public_jwk) — public_jwk is what the JWKS endpoint serves."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_key = RSAKey(priv, ALGORITHMS.RS256).to_dict()
    pub_key = RSAKey(priv.public_key(), ALGORITHMS.RS256).to_dict()
    priv_key["kid"] = kid
    pub_key["kid"] = kid
    pub_key["use"] = "sig"
    pub_key["alg"] = "RS256"
    return priv_key, pub_key


def _sign(claims: dict, priv_jwk: dict, kid: str = KID, alg: str = "RS256") -> str:
    return jose_jwt.encode(claims, priv_jwk, algorithm=alg, headers={"kid": kid})


def _claims(**overrides) -> dict:
    base = {
        "iss":   ISSUER,
        "aud":   AUDIENCE,
        "sub":   "user-123",
        "email": "alice@example.com",
        "iat":   int(time.time()) - 5,
        "exp":   int(time.time()) + 600,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_caches_and_config():
    """Each test gets clean discovery/JWKS caches and a known client_id."""
    oidc._discovery_cache.clear()
    oidc._jwks_cache.clear()
    saved = dict(oidc._PROVIDER_CONFIG[PROVIDER])
    oidc._PROVIDER_CONFIG[PROVIDER]["client_id"] = AUDIENCE
    oidc._PROVIDER_CONFIG[PROVIDER]["expected_iss"] = ISSUER
    yield
    oidc._PROVIDER_CONFIG[PROVIDER].update(saved)
    oidc._discovery_cache.clear()
    oidc._jwks_cache.clear()


@pytest.fixture
def keypair():
    return _make_rsa_jwk()


@pytest.fixture
def patched_oidc(keypair):
    """Patch _get_discovery + _get_jwks so verify_id_token resolves locally."""
    _, pub = keypair
    discovery = {
        "issuer":                 ISSUER,
        "jwks_uri":               "https://example/jwks.json",
        "authorization_endpoint": "https://example/authorize",
        "token_endpoint":         "https://example/token",
        "userinfo_endpoint":      "https://example/userinfo",
    }
    jwks = {"keys": [pub]}
    with patch.object(oidc, "_get_discovery", new=AsyncMock(return_value=discovery)), \
         patch.object(oidc, "_get_jwks", new=AsyncMock(return_value=jwks)):
        yield


# ---------------------------------------------------------------------------
# Core security tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_id_token_is_accepted(keypair, patched_oidc):
    priv, _ = keypair
    token = _sign(_claims(), priv)
    claims = await oidc.verify_id_token(PROVIDER, token)
    assert claims["email"] == "alice@example.com"
    assert claims["sub"] == "user-123"


@pytest.mark.asyncio
async def test_tampered_id_token_payload_is_rejected(keypair, patched_oidc):
    """The original SEV-1: an attacker swaps the payload, signature is unchanged."""
    priv, _ = keypair
    token = _sign(_claims(email="alice@example.com"), priv)

    # Surgically replace the payload segment with an attacker-controlled one.
    header_b64, payload_b64, sig_b64 = token.split(".")
    forged_payload = base64.urlsafe_b64encode(
        json.dumps(_claims(email="attacker@evil.example.com")).encode()
    ).rstrip(b"=").decode()
    tampered = f"{header_b64}.{forged_payload}.{sig_b64}"

    with pytest.raises(ValueError, match="id_token verification failed"):
        await oidc.verify_id_token(PROVIDER, tampered)


@pytest.mark.asyncio
async def test_alg_none_is_rejected(keypair, patched_oidc):
    """The classic 'alg: none' downgrade — refused before signature lookup."""
    payload_b64 = base64.urlsafe_b64encode(json.dumps(_claims()).encode()).rstrip(b"=").decode()
    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT", "kid": KID}).encode()
    ).rstrip(b"=").decode()
    unsigned = f"{header_b64}.{payload_b64}."

    with pytest.raises(ValueError, match="disallowed alg"):
        await oidc.verify_id_token(PROVIDER, unsigned)


@pytest.mark.asyncio
async def test_hs256_alg_confusion_is_rejected(keypair, patched_oidc):
    """HMAC algs let an attacker sign with the public key as if it were a secret."""
    _, pub = keypair
    # Sign with the public-key JWK material as if it were an HMAC secret.
    forged = jose_jwt.encode(_claims(), json.dumps(pub), algorithm="HS256", headers={"kid": KID})
    with pytest.raises(ValueError, match="disallowed alg"):
        await oidc.verify_id_token(PROVIDER, forged)


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected(keypair, patched_oidc):
    priv, _ = keypair
    token = _sign(_claims(aud="someone-elses-client-id"), priv)
    with pytest.raises(ValueError, match="id_token verification failed"):
        await oidc.verify_id_token(PROVIDER, token)


@pytest.mark.asyncio
async def test_wrong_issuer_is_rejected(keypair, patched_oidc):
    priv, _ = keypair
    token = _sign(_claims(iss="https://evil.example.com"), priv)
    with pytest.raises(ValueError, match="id_token verification failed"):
        await oidc.verify_id_token(PROVIDER, token)


@pytest.mark.asyncio
async def test_expired_id_token_is_rejected(keypair, patched_oidc):
    priv, _ = keypair
    token = _sign(_claims(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200), priv)
    with pytest.raises(ValueError, match="id_token verification failed"):
        await oidc.verify_id_token(PROVIDER, token)


@pytest.mark.asyncio
async def test_unknown_kid_triggers_jwks_refresh(keypair):
    """If the kid is missing from cache, a force_refresh JWKS fetch must be attempted."""
    priv, pub = keypair
    discovery = {
        "issuer":   ISSUER,
        "jwks_uri": "https://example/jwks.json",
    }
    # First call returns empty JWKS, second call returns the real key.
    jwks_calls = AsyncMock(side_effect=[{"keys": []}, {"keys": [pub]}])
    with patch.object(oidc, "_get_discovery", new=AsyncMock(return_value=discovery)), \
         patch.object(oidc, "_get_jwks", new=jwks_calls):
        token = _sign(_claims(), priv)
        claims = await oidc.verify_id_token(PROVIDER, token)
        assert claims["email"] == "alice@example.com"
        assert jwks_calls.call_count == 2  # cache miss, then force_refresh=True


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = oidc.build_pkce_challenge()
    # RFC 7636 §4.2: code_challenge = BASE64URL(SHA256(verifier))
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    assert challenge == expected
    # RFC 7636 §4.1: 43 <= len(verifier) <= 128
    assert 43 <= len(verifier) <= 128


def test_pkce_challenges_are_unique():
    pairs = {oidc.build_pkce_challenge() for _ in range(50)}
    assert len(pairs) == 50  # collision-resistant


# ---------------------------------------------------------------------------
# Router-level test — missing tenant_id must 400 (no demo fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_login_rejects_missing_tenant_id():
    """No demo-tenant fallback: tenant_id is required on /auth/sso/{provider}."""
    from fastapi import HTTPException

    from services.identity.router import sso_login

    fake_redis = AsyncMock()
    # Use a provider that the test-env may not have configured; the provider check
    # runs after the tenant check, so we substitute the enabled_providers list.
    with patch.object(oidc, "enabled_providers", return_value=[PROVIDER]):
        with pytest.raises(HTTPException) as exc:
            await sso_login(provider=PROVIDER, request=None, redis=fake_redis, tenant_id=None)
        assert exc.value.status_code == 400
        assert "tenant_id" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_sso_login_rejects_malformed_tenant_id():
    from fastapi import HTTPException

    from services.identity.router import sso_login

    fake_redis = AsyncMock()
    with patch.object(oidc, "enabled_providers", return_value=[PROVIDER]):
        with pytest.raises(HTTPException) as exc:
            await sso_login(
                provider=PROVIDER, request=None, redis=fake_redis, tenant_id="not-a-uuid",
            )
        assert exc.value.status_code == 400
        assert "uuid" in exc.value.detail.lower()
