"""ATF §7.4 (roadmap: anchor cross-signing) — verifies that chain
verification SURVIVES a transparency-key rotation via the historical-
key registry, without any need for the transition batch to be
literally cross-signed.

Design note (kept in this test file so a future contributor doesn't
"add cross-signing" without knowing the existing design):

The Aegis rotation model is NOT single-artifact-two-signatures. It is:

    * Every root / receipt carries `public_key_fingerprint` — the
      fingerprint of the ONE key that signed it.
    * `scripts/maintenance/rotate_transparency_key.py` INSERTs the
      OLD key's public PEM + fingerprint into
      `transparency_historical_keys` BEFORE the new private key
      becomes active on disk.
    * `verify_receipt_against_known_keys()` tries the ACTIVE key first,
      then falls back to fingerprint-lookup in the historical registry.

Property proved by this test:

    A receipt signed BEFORE rotation still verifies AFTER rotation,
    because the verifier finds the old key in the historical registry
    by fingerprint match. Chain verification survives rotation without
    cross-signing.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from services.audit import signer as sg


def _pem(pk: ed25519.Ed25519PrivateKey) -> str:
    return pk.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _sign_receipt(pk: ed25519.Ed25519PrivateKey, row: dict) -> dict:
    """Mirror `ReceiptSigner.sign` but with a caller-supplied key so we
    can simulate pre-rotation vs post-rotation keys."""
    pub_pem_bytes = pk.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fp = sg.fingerprint_public_key(pub_pem_bytes)
    receipt = {
        "version":      "1.0",
        "execution_id": row["id"],
        "tenant_id":    row["tenant_id"],
        "agent_id":     row["agent_id"],
        "tool":         "delete_record",
        "action":       "execute_tool",
        "decision":     "allow",
        "reason":       None,
        "request_id":   row["request_id"],
        "timestamp":    row["timestamp"],
        "event_hash":   row["event_hash"],
        "prev_hash":    row["prev_hash"],
        "chain_shard":  0,
    }
    sig = pk.sign(sg.canonical_json(receipt))
    return {
        "receipt":                receipt,
        "signature":              sg._b64(sig),
        "algorithm":              "ed25519",
        "public_key_fingerprint": fp,
    }


def _fake_row() -> dict:
    return {
        "id":          "el_01",
        "tenant_id":   "acme",
        "agent_id":    "ag_1",
        "request_id":  "req_1",
        "timestamp":   "2026-07-22T14:00:00Z",
        "event_hash":  "sha256:aa",
        "prev_hash":   "sha256:bb",
    }


class TestRotationSurvival:
    def test_pre_rotation_receipt_verifies_against_its_own_key(self):
        """Sanity: a fresh receipt verifies against the key that signed
        it — no rotation involved."""
        key_a = ed25519.Ed25519PrivateKey.generate()
        payload = _sign_receipt(key_a, _fake_row())
        assert sg.verify_receipt(payload, _pem(key_a)) is True

    def test_pre_rotation_receipt_fails_against_wrong_key(self):
        """Sanity: swap in a DIFFERENT key's PEM — verification fails.
        This is the check that catches an operator forgetting to archive
        the old key before rotation."""
        key_a = ed25519.Ed25519PrivateKey.generate()
        key_b = ed25519.Ed25519PrivateKey.generate()
        payload = _sign_receipt(key_a, _fake_row())
        assert sg.verify_receipt(payload, _pem(key_b)) is False

    @pytest.mark.asyncio
    async def test_verify_against_known_keys_walks_historical_registry(
        self, monkeypatch,
    ):
        """The core property: after rotation, a pre-rotation receipt
        still verifies via `verify_receipt_against_known_keys` because
        the historical registry contains the retired key's PEM +
        fingerprint. The verifier fingerprint-matches into history."""
        # 1. Mint pre-rotation key + sign a receipt with it.
        old_key = ed25519.Ed25519PrivateKey.generate()
        pre_rotation_receipt = _sign_receipt(old_key, _fake_row())
        old_fp = pre_rotation_receipt["public_key_fingerprint"]
        old_pem = _pem(old_key)

        # 2. Simulate rotation: mint a NEW key, make it the active signer.
        new_key = ed25519.Ed25519PrivateKey.generate()
        sg.reset_signer_for_tests()

        class _StubSigner:
            def __init__(self, k):
                self._priv = k
                self._pub_pem = k.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                self._fingerprint = sg.fingerprint_public_key(self._pub_pem)
            def public_key_pem(self):
                return self._pub_pem.decode("ascii")

        monkeypatch.setattr(sg, "get_signer",
                            lambda: _StubSigner(new_key))

        # 3. Simulate the historical registry containing the archived
        # OLD key. This is what `_record_historical_key` writes during
        # rotation. Post-rotation, `load_historical_public_keys` returns
        # this row and the verifier picks it up by fingerprint.
        async def _fake_load_historical(db):
            return [{
                "fingerprint":    old_fp,
                "public_key_pem": old_pem,
                "algorithm":      "ed25519",
                "rotated_at":     "2026-07-22T14:00:00Z",
            }]
        monkeypatch.setattr(sg, "load_historical_public_keys", _fake_load_historical)

        # 4. Verify the pre-rotation receipt POST rotation. The active
        # key is now `new_key` (fingerprint mismatch); the verifier
        # falls back to the historical registry, finds the OLD key by
        # fingerprint, and completes the verification.
        valid, used_fp = await sg.verify_receipt_against_known_keys(
            db=None, payload=pre_rotation_receipt,
        )
        assert valid is True, "pre-rotation receipt must verify post-rotation"
        assert used_fp == old_fp, (
            f"verifier should have used the historical key (fp={old_fp}), "
            f"got fp={used_fp}"
        )

    @pytest.mark.asyncio
    async def test_verify_fails_when_historical_registry_missing_key(
        self, monkeypatch,
    ):
        """If rotation happens WITHOUT archiving the old key (operator
        error), pre-rotation receipts stop verifying. This test proves
        the failure is loud — the SILENT case (fingerprint-based
        dispatch succeeds against wrong key) is what would defeat the
        whole model."""
        old_key = ed25519.Ed25519PrivateKey.generate()
        pre_rotation_receipt = _sign_receipt(old_key, _fake_row())

        new_key = ed25519.Ed25519PrivateKey.generate()
        sg.reset_signer_for_tests()

        class _StubSigner:
            def __init__(self, k):
                self._priv = k
                self._pub_pem = k.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                self._fingerprint = sg.fingerprint_public_key(self._pub_pem)
            def public_key_pem(self):
                return self._pub_pem.decode("ascii")

        monkeypatch.setattr(sg, "get_signer",
                            lambda: _StubSigner(new_key))

        # Empty historical registry — operator forgot to archive.
        async def _empty_history(db):
            return []
        monkeypatch.setattr(sg, "load_historical_public_keys", _empty_history)

        valid, used_fp = await sg.verify_receipt_against_known_keys(
            db=None, payload=pre_rotation_receipt,
        )
        assert valid is False
        assert used_fp is None
