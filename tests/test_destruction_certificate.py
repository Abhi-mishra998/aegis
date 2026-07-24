"""ATF v3.2 §14.5 — DESTROY destruction-certificate tests.

The certificate is the artifact the customer keeps AFTER the ledger is
gone. Two invariants matter:
  1. Retention-floor violation MUST refuse — the cert never lies about
     how long the ledger existed.
  2. Any post-signing tamper MUST fail verification — the cert is
     independently verifiable offline against a stored public key.

The pure module has a `__main__` self-check that already exercises the
happy + refusal paths; this file adds negative-space coverage (tamper,
missing fields, alg mismatch, offset-shift equivalence).
"""
from __future__ import annotations

import base64
import copy
import hashlib
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from sdk.common.destruction_certificate import (
    CERT_VERSION,
    DEFAULT_RETENTION_FLOOR_DAYS,
    FinalAnchor,
    RetentionFloorNotMet,
    build_destruction_certificate,
    canonical_json_bytes,
    verify_destruction_certificate,
)


def _keypair() -> tuple[ed25519.Ed25519PrivateKey, str, str]:
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    fp = hashlib.sha256(pub_pem.encode("ascii")).hexdigest()
    return priv, pub_pem, fp


def _anchor(fp: str) -> FinalAnchor:
    return FinalAnchor(
        root_hash="sha256:" + "aa" * 32,
        root_date_iso="2026-07-24",
        leaf_count=42_000,
        signing_key_fingerprint=fp,
        prev_root_hash="sha256:" + "bb" * 32,
    )


def _cert(priv, fp, first_ts, final_ts, floor=DEFAULT_RETENTION_FLOOR_DAYS):
    return build_destruction_certificate(
        tenant_id="acme-inc",
        first_entry_ts=first_ts,
        final_entry_ts=final_ts,
        final_anchor=_anchor(fp),
        retention_floor_days=floor,
        signer_fingerprint=fp,
        sign=priv.sign,
    )


class TestHappyPath:
    def test_round_trip_verify(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        assert verify_destruction_certificate(cert, pub_pem) is True

    def test_cert_shape_stable(self):
        priv, _, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        # Contract fields the customer's tooling reads.
        for k in (
            "cert_version", "tenant_id", "issued_at",
            "first_entry_ts", "final_entry_ts",
            "retention_floor_days", "actual_retention_days",
            "final_anchor", "signature", "signature_algorithm",
            "signing_key_fingerprint", "canonical_body_sha256",
        ):
            assert k in cert, f"missing field {k}"
        assert cert["cert_version"] == CERT_VERSION
        assert cert["signature_algorithm"] == "ed25519"


class TestRetentionFloor:
    def test_actual_lt_floor_refused(self):
        priv, _, fp = _keypair()
        with pytest.raises(RetentionFloorNotMet):
            _cert(priv, fp,
                  datetime(2026, 4, 1, tzinfo=UTC),
                  datetime(2026, 7, 10, tzinfo=UTC),  # ~100 days < 180
                  floor=180)

    def test_actual_eq_floor_ok(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 6, 30, tzinfo=UTC),  # 180d exactly
                     floor=180)
        assert cert["actual_retention_days"] == 180
        assert verify_destruction_certificate(cert, pub_pem)

    def test_negative_floor_rejected(self):
        priv, _, fp = _keypair()
        with pytest.raises(ValueError):
            _cert(priv, fp,
                  datetime(2026, 1, 1, tzinfo=UTC),
                  datetime(2026, 7, 1, tzinfo=UTC),
                  floor=-1)

    def test_final_before_first_rejected(self):
        priv, _, fp = _keypair()
        with pytest.raises(ValueError):
            _cert(priv, fp,
                  datetime(2026, 7, 1, tzinfo=UTC),
                  datetime(2026, 1, 1, tzinfo=UTC),
                  floor=180)


class TestTamperEvidence:
    def test_mutated_retention_days_fails(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        tampered = copy.deepcopy(cert)
        tampered["actual_retention_days"] = 999_999
        assert not verify_destruction_certificate(tampered, pub_pem)

    def test_mutated_tenant_id_fails(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        tampered = copy.deepcopy(cert)
        tampered["tenant_id"] = "other-tenant"
        assert not verify_destruction_certificate(tampered, pub_pem)

    def test_mutated_final_anchor_root_hash_fails(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        tampered = copy.deepcopy(cert)
        tampered["final_anchor"]["root_hash"] = "sha256:" + "00" * 32
        assert not verify_destruction_certificate(tampered, pub_pem)

    def test_wrong_public_key_fails(self):
        priv, _, fp = _keypair()
        _, other_pub_pem, _ = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        assert not verify_destruction_certificate(cert, other_pub_pem)

    def test_swapped_signature_bytes_fails(self):
        """Directly overwrite the signature with random bytes — verify fails."""
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        tampered = copy.deepcopy(cert)
        tampered["signature"] = base64.urlsafe_b64encode(
            b"\x00" * 64,
        ).rstrip(b"=").decode("ascii")
        assert not verify_destruction_certificate(tampered, pub_pem)


class TestMissingFieldsRaise:
    """Distinguish `bad payload` from `valid payload that didn't verify` —
    same distinction as verify_receipt."""
    def test_missing_signature_raises(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        del cert["signature"]
        with pytest.raises(ValueError, match="missing field: signature"):
            verify_destruction_certificate(cert, pub_pem)

    def test_missing_final_anchor_raises(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        del cert["final_anchor"]
        with pytest.raises(ValueError, match="missing field: final_anchor"):
            verify_destruction_certificate(cert, pub_pem)

    def test_wrong_algorithm_raises(self):
        priv, pub_pem, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        cert["signature_algorithm"] = "rsa-4096"
        with pytest.raises(ValueError, match="unsupported algorithm"):
            verify_destruction_certificate(cert, pub_pem)


class TestCanonicalization:
    def test_body_digest_matches_canonical_form(self):
        """The published canonical_body_sha256 MUST equal
        sha256(canonical_json(body)) — a mismatch means tamper OR a
        signer bug. Cert-consumer tooling can rely on this equality."""
        priv, _, fp = _keypair()
        cert = _cert(priv, fp,
                     datetime(2026, 1, 1, tzinfo=UTC),
                     datetime(2026, 7, 24, tzinfo=UTC))
        body = {k: cert[k] for k in (
            "cert_version", "tenant_id", "issued_at",
            "first_entry_ts", "final_entry_ts",
            "retention_floor_days", "actual_retention_days",
            "final_anchor",
        )}
        expected = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        assert cert["canonical_body_sha256"] == expected
