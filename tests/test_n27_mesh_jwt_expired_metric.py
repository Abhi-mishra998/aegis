"""N27 (2026-06-21) — mesh JWT clock-skew visibility.

Mesh tokens use a 5-minute TTL and jose.jwt.decode is called WITHOUT
``leeway``, so a host whose NTP drifts past the TTL boundary starts
turning every cross-service auth into ``_expired: True``. Before this
fix that mode was invisible: the failed call looked like any other
4xx in the request log and there was no per-issuer metric to alert on.

This test pins the new behavior:
  * the ``mesh_jwt_expired_total{issuer}`` counter exists and is a
    Counter (not a Gauge / Histogram)
  * expiry increments it with the correct issuer label
  * a non-expired token does NOT increment it (only expiry should)
  * the sentinel return value is unchanged so existing callers keep
    working
"""
from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt as jose_jwt

from sdk.common import auth as mesh


def _gen_ec_keypair() -> tuple[str, str]:
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


def _mint_expired_token(
    priv_pem_b64: str, *, iss: str, kid: str, expired_at_sec_ago: int = 60,
) -> str:
    """Mint a token whose ``exp`` is already in the past.

    We bypass mesh.mint_service_token because that helper enforces a
    minimum 5-second TTL — we need an immediately-expired token for the
    test.
    """
    now = int(time.time())
    payload = {
        "iss":   iss,
        "aud":   "acp.mesh.internal",
        "iat":   now - expired_at_sec_ago - 1,
        "exp":   now - expired_at_sec_ago,
        "scope": "internal",
    }
    priv_pem = base64.b64decode(priv_pem_b64)
    return jose_jwt.encode(
        payload, priv_pem.decode("ascii"),
        algorithm="ES256", headers={"kid": kid},
    )


def _counter_value(issuer: str) -> float:
    """Read the live MESH_JWT_EXPIRED_TOTAL value for one issuer."""
    return mesh.MESH_JWT_EXPIRED_TOTAL.labels(issuer=issuer)._value.get()


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch):
    monkeypatch.delenv("ACP_MESH_SERVICE_NAME", raising=False)
    monkeypatch.delenv("ACP_MESH_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("ACP_MESH_TRUSTED_KEYS", raising=False)
    mesh._reset_mesh_caches_for_tests()
    yield
    mesh._reset_mesh_caches_for_tests()


# --------------------------------------------------------------------------- #
# Counter shape                                                                #
# --------------------------------------------------------------------------- #


def test_counter_exists_and_is_a_prometheus_counter() -> None:
    """Sanity: a future refactor that swaps in a Gauge or Histogram
    silently breaks Prometheus's rate() function — pin the type."""
    from prometheus_client import Counter
    # MESH_JWT_EXPIRED_TOTAL is a Counter parent — labels() returns a Child.
    assert isinstance(mesh.MESH_JWT_EXPIRED_TOTAL, Counter)
    # And the label dimension is exactly "issuer".
    assert mesh.MESH_JWT_EXPIRED_TOTAL._labelnames == ("issuer",)


# --------------------------------------------------------------------------- #
# Behavior                                                                     #
# --------------------------------------------------------------------------- #


def test_expired_token_increments_counter_with_issuer_label(monkeypatch) -> None:
    """Verify the counter ticks with the issuer label on an expired token."""
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"gateway": a_pub}))
    mesh._reset_mesh_caches_for_tests()

    before = _counter_value("gateway")
    expired = _mint_expired_token(a_priv, iss="gateway", kid="gateway")
    claims = mesh._verify_mesh_jwt(expired)
    after = _counter_value("gateway")

    # Sentinel return preserved (callers depend on this contract).
    assert claims is not None
    assert claims.get("_expired") is True
    assert claims.get("iss") == "gateway"
    # And the counter ticked by exactly 1.
    assert after - before == pytest.approx(1.0)


def test_valid_token_does_not_increment_expired_counter(monkeypatch) -> None:
    """The counter must ONLY tick on expiry — not on every verification.
    Otherwise the rate() alert fires on healthy traffic."""
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_SERVICE_NAME", "audit")
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"audit": a_pub}))
    mesh._reset_mesh_caches_for_tests()

    before = _counter_value("audit")
    valid = mesh.mint_service_token("audit")  # 5-min TTL, not expired
    claims = mesh._verify_mesh_jwt(valid)
    after = _counter_value("audit")

    assert claims is not None
    assert not claims.get("_expired")
    assert after == before


def test_expired_token_with_unknown_iss_falls_back_to_kid(monkeypatch) -> None:
    """If the unverified claims somehow don't carry ``iss``, we still
    record the kid so the operator knows WHICH service was failing."""
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"behavior": a_pub}))
    mesh._reset_mesh_caches_for_tests()

    # Mint a payload with no `iss` field. We bypass _mint_expired_token
    # because it always sets iss; build it inline.
    now = int(time.time())
    payload = {
        # iss intentionally omitted
        "aud":   "acp.mesh.internal",
        "iat":   now - 600,
        "exp":   now - 60,
        "scope": "internal",
    }
    priv_pem = base64.b64decode(a_priv)
    token = jose_jwt.encode(
        payload, priv_pem.decode("ascii"),
        algorithm="ES256", headers={"kid": "behavior"},
    )

    before = _counter_value("behavior")
    claims = mesh._verify_mesh_jwt(token)
    after = _counter_value("behavior")

    assert claims and claims.get("_expired") is True
    # The label landed on "behavior" (the kid), not "unknown".
    assert after - before == pytest.approx(1.0)


def test_docstring_documents_skew_design() -> None:
    """The function's docstring must explain the strict-exp + counter
    design — if a future maintainer relaxes the TTL or adds leeway they
    should see this contract first."""
    doc = mesh._verify_mesh_jwt.__doc__ or ""
    assert "Clock-skew" in doc or "clock-skew" in doc.lower()
    assert "leeway" in doc.lower()
    assert "5-minute" in doc or "5 minute" in doc.lower() or "TTL" in doc
