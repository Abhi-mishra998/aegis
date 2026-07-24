"""ATF v3.2 §14.5 ROTATE — cross-signature of the retiring key's final
batch by the new key.

Spec text (§14.5 ROTATE line):
    "old key's final batch cross-signed by new key so chain verification
    survives rotation"

Property proved end-to-end here (no DB — pure verifier test):
  1. `_cross_sign_payload` (from the rotate script) produces a signature
     the new key verifies against the OLD key's canonical
     signed_root_payload.
  2. `verify_rotation_cross_signature` returns True for a well-formed
     historical row with the transition_* fields populated.
  3. The verifier returns False (never raises) for legacy rows without
     the transition fields — so old rotations stay backwards-compatible.
  4. Verifier returns False on ANY tamper (wrong new key, mismatched
     root_hash, garbled signature).

The DB-side happy path (rotate script → INSERT → verifier at boot) is
covered by the existing 4-test rotation_survival suite via
fingerprint-dispatched historical keys; this file locks in the
cross-signature property that closes the "gap batch" that fingerprint
dispatch alone doesn't cover.
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from services.audit.signer import (
    canonical_json,
    fingerprint_public_key,
    verify_rotation_cross_signature,
)


def _keypair() -> tuple[ed25519.Ed25519PrivateKey, str, str]:
    priv = ed25519.Ed25519PrivateKey.generate()
    pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    fp = fingerprint_public_key(pem.encode("ascii"))
    return priv, pem, fp


def _cross_sign(new_priv, payload: dict) -> str:
    """Mirror the rotate script's `_cross_sign_payload` locally so this
    test doesn't reach into scripts/ (which imports SQLAlchemy models)."""
    sig = new_priv.sign(canonical_json(payload))
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def _row(**kw):
    """A historical-row stub — attribute access to match SQLAlchemy shape."""
    defaults = {
        "transition_root_hash": None,
        "transition_new_key_signature": None,
        "transition_new_key_fingerprint": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestCrossSignVerify:
    def test_happy_path_verifies(self):
        _old, _, old_fp = _keypair()
        new_priv, new_pem, new_fp = _keypair()

        # OLD key's final signed_root_payload — mirrors the shape the
        # signer builds when sealing a daily root.
        payload = {
            "root_hash":       "sha256:" + "aa" * 32,
            "tenant_id":       "acme",
            "root_date":       "2026-07-24",
            "leaf_count":      12_345,
            "prev_root_hash":  "sha256:" + "bb" * 32,
            "signing_key_fingerprint": old_fp,
        }
        sig_b64 = _cross_sign(new_priv, payload)
        row = _row(
            transition_root_hash=payload["root_hash"],
            transition_new_key_signature=sig_b64,
            transition_new_key_fingerprint=new_fp,
        )
        assert verify_rotation_cross_signature(row, new_pem, payload) is True

    def test_legacy_row_without_transition_fields_returns_false(self):
        """Rotation rows written before this sprint have all three fields
        NULL. Verifier returns False (not exception) so the caller can
        distinguish "not cross-signed" from "cross-signed but invalid"."""
        _, new_pem, _ = _keypair()
        row = _row()  # all None
        payload = {"root_hash": "sha256:" + "cc" * 32}
        assert verify_rotation_cross_signature(row, new_pem, payload) is False

    def test_wrong_new_key_rejected(self):
        _old, _, _ = _keypair()
        new_priv, _, new_fp = _keypair()
        other_priv, other_pem, _ = _keypair()  # noqa: F841 — used below

        payload = {"root_hash": "sha256:" + "aa" * 32, "leaf_count": 1}
        sig_b64 = _cross_sign(new_priv, payload)
        row = _row(
            transition_root_hash=payload["root_hash"],
            transition_new_key_signature=sig_b64,
            transition_new_key_fingerprint=new_fp,
        )
        # Present a DIFFERENT new key's PEM — fingerprint mismatch → False.
        assert verify_rotation_cross_signature(row, other_pem, payload) is False

    def test_mismatched_root_hash_rejected(self):
        """Row records root_hash A; caller passes payload for root_hash B.
        Even if the signature would verify against payload B, the
        boundary-hash check refuses — belt + suspenders."""
        new_priv, new_pem, new_fp = _keypair()
        real_payload = {"root_hash": "sha256:" + "aa" * 32}
        sig_b64 = _cross_sign(new_priv, real_payload)
        row = _row(
            transition_root_hash="sha256:" + "aa" * 32,
            transition_new_key_signature=sig_b64,
            transition_new_key_fingerprint=new_fp,
        )
        swapped_payload = {"root_hash": "sha256:" + "ff" * 32}
        assert verify_rotation_cross_signature(row, new_pem, swapped_payload) is False

    def test_tampered_signature_rejected(self):
        new_priv, new_pem, new_fp = _keypair()
        payload = {"root_hash": "sha256:" + "aa" * 32, "leaf_count": 1}
        _cross_sign(new_priv, payload)  # generate, then discard
        garbage = base64.urlsafe_b64encode(b"\x00" * 64).rstrip(b"=").decode("ascii")
        row = _row(
            transition_root_hash=payload["root_hash"],
            transition_new_key_signature=garbage,
            transition_new_key_fingerprint=new_fp,
        )
        assert verify_rotation_cross_signature(row, new_pem, payload) is False

    def test_partial_transition_fields_return_false(self):
        """Row with SOME (but not all) transition fields set is treated as
        "not cross-signed" — no half-verified states."""
        _, new_pem, new_fp = _keypair()
        payload = {"root_hash": "sha256:" + "aa" * 32}
        row_missing_sig = _row(
            transition_root_hash=payload["root_hash"],
            transition_new_key_fingerprint=new_fp,
            # transition_new_key_signature stays None
        )
        assert verify_rotation_cross_signature(row_missing_sig, new_pem, payload) is False
