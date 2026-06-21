"""
Sprint 1.4 — per-service asymmetric mesh JWT (audit C12).

Pre-Sprint-1, every service signed and verified mesh tokens with the same
HS256 secret. Leak of one service's key forged every other service's tokens —
the exact scenario the audit's "no single shared secret" finding called out.

After this sprint each service owns an ES256 private key; verifiers hold the
public keys of every service they accept tokens from. These tests prove:

  * A token minted with service A's key VERIFIES.
  * A token minted with service A's key does NOT verify as if it came from
    service B (the headline C12 fix).
  * The legacy HS256 path still works when no asymmetric keys are configured.
  * The legacy ``X-Internal-Secret`` header is rejected once asymmetric keys
    are configured (otherwise the old single-secret blast radius survives).
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from sdk.common import auth as mesh


def _gen_ec_keypair() -> tuple[str, str]:
    """Return (private_pem_b64, public_pem_b64) for an ES256 keypair."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return (
        base64.b64encode(priv_pem).decode("ascii"),
        base64.b64encode(pub_pem).decode("ascii"),
    )


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch):
    """Each test gets fresh mesh-key caches and a clean env."""
    monkeypatch.delenv("ACP_MESH_SERVICE_NAME", raising=False)
    monkeypatch.delenv("ACP_MESH_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("ACP_MESH_TRUSTED_KEYS", raising=False)
    mesh._reset_mesh_caches_for_tests()
    yield
    mesh._reset_mesh_caches_for_tests()


# ---------------------------------------------------------------------------
# ES256 path
# ---------------------------------------------------------------------------


def test_es256_mint_and_verify_round_trip(monkeypatch):
    """Service A mints a token; the verifier accepts it as service A."""
    a_priv, a_pub = _gen_ec_keypair()
    trusted = {"gateway": a_pub}

    monkeypatch.setenv("ACP_MESH_SERVICE_NAME", "gateway")
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps(trusted))
    mesh._reset_mesh_caches_for_tests()

    token = mesh.mint_service_token("gateway")
    claims = mesh._verify_mesh_jwt(token)
    assert claims is not None and not claims.get("_expired")
    assert claims["iss"] == "gateway"
    assert claims["aud"] == "acp.mesh.internal"
    assert claims["scope"] == "internal"


def test_service_a_token_does_not_verify_as_service_b(monkeypatch):
    """The audit C12 ask: leaking service A's private key must not let an
    attacker mint a service-B token. The verifier rejects the cross-signed
    token because the kid lookup finds service-A's public key, but the
    attacker's forged token claims kid=audit."""
    a_priv, a_pub = _gen_ec_keypair()
    _, b_pub = _gen_ec_keypair()
    trusted = {"gateway": a_pub, "audit": b_pub}

    monkeypatch.setenv("ACP_MESH_SERVICE_NAME", "gateway")
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps(trusted))
    mesh._reset_mesh_caches_for_tests()

    # Attacker has gateway's private key and tries to issue a token claiming
    # to be 'audit' (the kid lookup must reject this).
    forged = mesh.mint_service_token("audit")  # signs with gateway's key, kid='audit'
    claims = mesh._verify_mesh_jwt(forged)
    # The verifier looks up kid='audit' → gets B's public key → signature fails.
    assert claims is None, (
        "forged token under another service's identity must NOT verify"
    )


def test_token_with_unknown_kid_is_rejected(monkeypatch):
    a_priv, a_pub = _gen_ec_keypair()
    trusted = {"gateway": a_pub}

    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps(trusted))
    mesh._reset_mesh_caches_for_tests()

    # An attacker generates their own keypair and signs a token claiming a
    # kid that exists in the trust list — the kid lookup finds gateway's key
    # but the signature is by an unrelated private key → fails.
    intruder_priv, _ = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", intruder_priv)
    mesh._reset_mesh_caches_for_tests()
    intruder_token = mesh.mint_service_token("gateway")

    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    mesh._reset_mesh_caches_for_tests()
    assert mesh._verify_mesh_jwt(intruder_token) is None


def test_token_with_kid_outside_trust_list_is_rejected(monkeypatch):
    a_priv, a_pub = _gen_ec_keypair()
    trusted = {"gateway": a_pub}  # 'unknown_service' is NOT in the trust list

    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps(trusted))
    mesh._reset_mesh_caches_for_tests()

    token = mesh.mint_service_token("unknown_service")
    assert mesh._verify_mesh_jwt(token) is None


# ---------------------------------------------------------------------------
# Back-compat HS256 path
# ---------------------------------------------------------------------------


def test_legacy_hs256_round_trip_when_no_mesh_keys(monkeypatch):
    """No ES256 keys → mint/verify still works via the legacy shared HS256."""
    monkeypatch.setattr(mesh.settings, "MESH_JWT_SECRET", "legacy-secret", raising=False)
    monkeypatch.setattr(mesh.settings, "INTERNAL_SECRET", "legacy-internal", raising=False)
    token = mesh.mint_service_token("gateway")
    claims = mesh._verify_mesh_jwt(token)
    assert claims and claims["iss"] == "gateway"


def test_verify_internal_secret_accepts_legacy_header_when_no_mesh_keys(monkeypatch):
    monkeypatch.setattr(mesh.settings, "INTERNAL_SECRET", "the-secret", raising=False)
    # No ACP_MESH_* configured → legacy path is active.
    result = mesh.verify_internal_secret(secret="the-secret", mesh_token=None)
    assert result == "the-secret"


def test_verify_internal_secret_rejects_legacy_header_once_mesh_keys_configured(monkeypatch):
    """Audit C12: once asymmetric mesh keys are configured the single-shared-secret
    door must be closed. Otherwise leaking the old INTERNAL_SECRET would still
    let an attacker reach internal services through the front door."""
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"gateway": a_pub}))
    mesh._reset_mesh_caches_for_tests()
    monkeypatch.setattr(mesh.settings, "INTERNAL_SECRET", "the-secret", raising=False)

    with pytest.raises(HTTPException) as exc:
        mesh.verify_internal_secret(secret="the-secret", mesh_token=None)
    assert exc.value.status_code == 403
    assert "legacy X-Internal-Secret is disabled" in exc.value.detail


def test_verify_internal_secret_accepts_valid_es256_mesh_token(monkeypatch):
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_SERVICE_NAME", "gateway")
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"gateway": a_pub}))
    mesh._reset_mesh_caches_for_tests()

    token = mesh.mint_service_token("gateway")
    result = mesh.verify_internal_secret(secret=None, mesh_token=token)
    assert result.startswith("mesh:gateway")


# ---------------------------------------------------------------------------
# N6 — mesh_headers() resilience on mint failure
# ---------------------------------------------------------------------------


def test_mesh_headers_returns_empty_when_private_key_missing(monkeypatch):
    """N6 fix: when ACP_MESH_PRIVATE_KEY_PEM is unset (fresh ASG boot before
    SSM populates env, misconfigured launch template, key rotation gap),
    mint_service_token raises RuntimeError. Pre-N6 that RuntimeError
    propagated out and FastAPI returned an opaque HTTP 500.

    Post-N6: mesh_headers traps the failure, increments
    mesh_headers_mint_failures_total{service=...}, and returns {} so the
    caller can still build a request — the receiver naturally 403s on the
    missing X-Mesh-Token (correct degraded behaviour, no silent bypass)."""
    # Env vars are already cleared by the autouse fixture and caches reset,
    # so _mesh_private_key_pem() will return None on first call — exactly
    # the cold-boot condition we want to exercise.
    assert mesh._mesh_private_key_pem() is None, (
        "test prerequisite — ACP_MESH_PRIVATE_KEY_PEM must be empty"
    )

    # Snapshot the counter before (use a unique service label so this test
    # is independent of other tests' counter state).
    counter = mesh.MESH_HEADERS_MINT_FAILURES.labels(service="n6-test-service")
    before = counter._value.get()

    result = mesh.mesh_headers("n6-test-service")

    # Empty dict instead of raising RuntimeError
    assert result == {}, (
        "mesh_headers must swallow RuntimeError from mint_service_token "
        "and return an empty dict — otherwise FastAPI surfaces a generic 500"
    )

    # Counter incremented exactly once
    after = counter._value.get()
    assert after == before + 1, (
        f"MESH_HEADERS_MINT_FAILURES counter for service=n6-test-service should "
        f"increment by 1 (before={before}, after={after})"
    )


def test_mesh_headers_succeeds_when_private_key_present(monkeypatch):
    """Happy-path sanity check: when keys are configured mesh_headers returns
    the X-Mesh-Token header and does NOT increment the failure counter."""
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"gateway": a_pub}))
    mesh._reset_mesh_caches_for_tests()

    counter = mesh.MESH_HEADERS_MINT_FAILURES.labels(service="gateway")
    before = counter._value.get()

    headers = mesh.mesh_headers("gateway")
    assert "X-Mesh-Token" in headers
    assert headers["X-Mesh-Token"]  # non-empty

    after = counter._value.get()
    assert after == before, (
        "MESH_HEADERS_MINT_FAILURES must NOT increment on the happy path"
    )
