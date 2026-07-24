"""Real crypto tests for the multi-IdP acceptance layer.

Generates RSA + EC keypairs, mints tokens, verifies them through the
dispatcher. No mocks of the crypto path — the whole stack from
token-shape detection to signature verify runs.
"""
from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from jose.constants import ALGORITHMS

from sdk.common.exceptions import ACPAuthError


def _rsa_keypair() -> tuple[rsa.RSAPrivateKey, dict]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    jwks_entry = jwk.construct(pub_pem, algorithm=ALGORITHMS.RS256).to_dict()
    jwks_entry["kid"] = "test-kid-1"
    jwks_entry["alg"] = "RS256"
    jwks_entry["use"] = "sig"
    return key, jwks_entry, pem  # type: ignore[return-value]


def _sign(claims: dict, private_pem: bytes, kid: str = "test-kid-1", alg: str = "RS256") -> str:
    return jwt.encode(claims, private_pem, algorithm=alg, headers={"kid": kid})


@pytest.fixture
def spiffe_env(monkeypatch):
    """Turn on SPIFFE acceptance with a fresh trust bundle."""
    key, jwks_entry, pem = _rsa_keypair()
    bundle = {"keys": [jwks_entry]}
    monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "acme.example")
    monkeypatch.setenv("SPIFFE_TRUST_BUNDLE_JSON", json.dumps(bundle))
    monkeypatch.setenv("SPIFFE_AUDIENCE", "")
    # Force settings re-read on the imported module.
    from importlib import reload as _reload

    from sdk.common import config as _cfg
    _reload(_cfg)
    from services.gateway import idp_verifiers as _iv
    _reload(_iv)
    return _iv, pem


@pytest.fixture
def entra_env(monkeypatch):
    key, jwks_entry, pem = _rsa_keypair()
    monkeypatch.setenv("ENTRA_TENANT_ID", "a1b2c3d4")
    monkeypatch.setenv("ENTRA_AUDIENCE", "api://aegis")
    from importlib import reload as _reload

    from sdk.common import config as _cfg
    _reload(_cfg)
    from services.gateway import idp_verifiers as _iv
    _reload(_iv)
    return _iv, pem, jwks_entry


@pytest.fixture
def okta_env(monkeypatch):
    key, jwks_entry, pem = _rsa_keypair()
    monkeypatch.setenv("OKTA_ISSUER", "https://acme.okta.com/oauth2/default")
    monkeypatch.setenv("OKTA_AUDIENCE", "api://aegis")
    from importlib import reload as _reload

    from sdk.common import config as _cfg
    _reload(_cfg)
    from services.gateway import idp_verifiers as _iv
    _reload(_iv)
    return _iv, pem, jwks_entry


class TestShapeDetection:
    def test_spiffe_disabled_when_no_trust_domain(self, monkeypatch):
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "")
        from importlib import reload as _reload

        from sdk.common import config as _cfg
        _reload(_cfg)
        from services.gateway import idp_verifiers as _iv
        _reload(_iv)
        assert not _iv.looks_like_spiffe("anything")

    def test_spiffe_detects_spiffe_sub(self, spiffe_env):
        iv, pem = spiffe_env
        tok = _sign(
            {"sub": "spiffe://acme.example/agent/1", "exp": int(time.time()) + 300},
            pem,
        )
        assert iv.looks_like_spiffe(tok)

    def test_spiffe_ignores_non_spiffe_sub(self, spiffe_env):
        iv, pem = spiffe_env
        tok = _sign({"sub": "user_123", "exp": int(time.time()) + 300}, pem)
        assert not iv.looks_like_spiffe(tok)

    def test_entra_matches_configured_tid(self, entra_env):
        iv, pem, _ = entra_env
        tok = _sign(
            {
                "iss": "https://login.microsoftonline.com/a1b2c3d4/v2.0",
                "exp": int(time.time()) + 300,
            },
            pem,
        )
        assert iv.looks_like_entra(tok)

    def test_entra_ignores_other_tenant(self, entra_env):
        iv, pem, _ = entra_env
        tok = _sign(
            {
                "iss": "https://login.microsoftonline.com/OTHER/v2.0",
                "exp": int(time.time()) + 300,
            },
            pem,
        )
        assert not iv.looks_like_entra(tok)

    def test_okta_exact_issuer_match(self, okta_env):
        iv, pem, _ = okta_env
        tok = _sign(
            {"iss": "https://acme.okta.com/oauth2/default", "exp": int(time.time()) + 300},
            pem,
        )
        assert iv.looks_like_okta(tok)


class TestSpiffeVerify:
    @pytest.mark.asyncio
    async def test_valid_svid_returns_payload_with_agent_role(self, spiffe_env):
        iv, pem = spiffe_env
        tok = _sign(
            {
                "sub": "spiffe://acme.example/agent/finance-001",
                "exp": int(time.time()) + 300,
            },
            pem,
        )
        payload = await iv.verify_spiffe_token(tok, redis=None)
        assert payload["sub"] == "spiffe://acme.example/agent/finance-001"
        assert payload["tenant_id"] == "acme.example"
        assert payload["role"] == "agent"
        assert payload["auth_provider"] == "spiffe"
        assert payload["jti"].startswith("idp:")

    @pytest.mark.asyncio
    async def test_wrong_trust_domain_rejected(self, spiffe_env):
        iv, pem = spiffe_env
        tok = _sign(
            {
                "sub": "spiffe://hostile.example/agent/x",
                "exp": int(time.time()) + 300,
            },
            pem,
        )
        with pytest.raises(ACPAuthError):
            await iv.verify_spiffe_token(tok, redis=None)

    @pytest.mark.asyncio
    async def test_expired_svid_rejected(self, spiffe_env):
        iv, pem = spiffe_env
        tok = _sign(
            {
                "sub": "spiffe://acme.example/agent/1",
                "exp": int(time.time()) - 60,  # expired
            },
            pem,
        )
        with pytest.raises(ACPAuthError):
            await iv.verify_spiffe_token(tok, redis=None)

    @pytest.mark.asyncio
    async def test_disabled_when_no_config(self, monkeypatch):
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "")
        from importlib import reload as _reload

        from sdk.common import config as _cfg
        _reload(_cfg)
        from services.gateway import idp_verifiers as _iv
        _reload(_iv)
        with pytest.raises(ACPAuthError):
            await _iv.verify_spiffe_token("any.token.here", redis=None)


class TestErrorUniformity:
    """Every verifier failure must raise ACPAuthError with message
    'Unauthorized' — never a per-adapter oracle."""

    @pytest.mark.asyncio
    async def test_spiffe_forged_token_uniform_error(self, spiffe_env):
        iv, _pem = spiffe_env
        _other_key, _, other_pem = _rsa_keypair()
        forged = _sign(
            {
                "sub": "spiffe://acme.example/agent/1",
                "exp": int(time.time()) + 300,
            },
            other_pem,
            kid="test-kid-1",
        )
        try:
            await iv.verify_spiffe_token(forged, redis=None)
            raise AssertionError("expected ACPAuthError")
        except ACPAuthError as exc:
            assert str(exc) == "Unauthorized"
