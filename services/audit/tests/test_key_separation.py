"""Receipt vs root key separation.

Two ed25519 keys: one for individual receipts, one for daily Merkle roots.
The roots key falls back to the receipts key when ROOT_SIGNING_KEY_PATH is
unset (back-compat). When set, the two keys are independent — rotating one
does not affect signatures produced by the other.
"""
import uuid
from datetime import UTC, date, datetime

import pytest

from sdk.acp_client import verify_receipt
from services.audit.signer import (
    get_root_signer,
    get_signer,
    reset_signer_for_tests,
)
from services.audit.transparency import _sign_root


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_PATH", str(tmp_path / "receipt.pem"))
    monkeypatch.delenv("RECEIPT_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("ROOT_SIGNING_KEY_PATH", raising=False)
    monkeypatch.delenv("ROOT_SIGNING_PRIVATE_KEY", raising=False)
    reset_signer_for_tests()
    yield
    reset_signer_for_tests()


def test_no_env_falls_back_to_receipt_key():
    r = get_signer()
    root = get_root_signer()
    assert root is r, "must reuse the receipt signer when no separate root key is configured"


def test_separate_root_key_has_different_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_SIGNING_KEY_PATH", str(tmp_path / "root.pem"))
    reset_signer_for_tests()

    r = get_signer()
    root = get_root_signer()
    assert r is not root
    assert r.public_key_pem() != root.public_key_pem()
    # Fingerprints must be distinct
    r_info = r.public_key_info()
    root_info = root.public_key_info()
    assert r_info["fingerprint"] != root_info["fingerprint"]


def test_signed_root_uses_root_key_when_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_SIGNING_KEY_PATH", str(tmp_path / "root.pem"))
    reset_signer_for_tests()

    root_signer = get_root_signer()
    payload = _sign_root(uuid.UUID(int=1), date(2026, 5, 14), "a" * 64, 5)

    # Fingerprint in the signed payload must be the ROOT key, not the receipt key
    assert payload["public_key_fingerprint"] == root_signer.public_key_info()["fingerprint"]
    assert payload["public_key_fingerprint"] != get_signer().public_key_info()["fingerprint"]


def test_signed_root_verifiable_with_root_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_SIGNING_KEY_PATH", str(tmp_path / "root.pem"))
    reset_signer_for_tests()

    payload = _sign_root(uuid.UUID(int=1), date(2026, 5, 14), "a" * 64, 5)
    root_pem = get_root_signer().public_key_pem()
    assert verify_receipt(payload, root_pem) is True


def test_signed_root_NOT_verifiable_with_receipt_key_when_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_SIGNING_KEY_PATH", str(tmp_path / "root.pem"))
    reset_signer_for_tests()

    payload = _sign_root(uuid.UUID(int=1), date(2026, 5, 14), "a" * 64, 5)
    receipt_pem = get_signer().public_key_pem()
    # Fingerprint mismatch → verify must return False (not raise)
    assert verify_receipt(payload, receipt_pem) is False


def test_back_compat_root_verifiable_with_same_key_when_no_separate_root(tmp_path):
    """When ROOT_SIGNING_KEY_PATH is unset, the existing root.payload still
    verifies against the receipt key — the back-compat path the previous
    sprint shipped."""
    payload = _sign_root(uuid.UUID(int=1), date(2026, 5, 14), "a" * 64, 5)
    pem = get_signer().public_key_pem()
    assert verify_receipt(payload, pem) is True
