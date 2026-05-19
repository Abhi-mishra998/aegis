import uuid
from datetime import UTC, datetime

import pytest

from services.audit.signer import (
    ReceiptSigner,
    canonical_json,
    fingerprint_public_key,
    get_signer,
    reset_signer_for_tests,
    verify_receipt,
)


@pytest.fixture(autouse=True)
def _isolate_signer(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_PATH", str(tmp_path / "test-key.pem"))
    monkeypatch.delenv("RECEIPT_SIGNING_PRIVATE_KEY", raising=False)
    reset_signer_for_tests()
    yield
    reset_signer_for_tests()


def _row():
    return {
        "id":          uuid.uuid4(),
        "tenant_id":   uuid.uuid4(),
        "agent_id":    uuid.uuid4(),
        "tool":        "db.query",
        "action":      "execute",
        "decision":    "allow",
        "reason":      None,
        "request_id":  "req_abc",
        "timestamp":   datetime.now(UTC),
        "event_hash":  "a" * 64,
        "prev_hash":   "b" * 64,
        "chain_shard": 3,
    }


def test_sign_then_verify_roundtrip():
    signer = get_signer()
    payload = signer.sign(_row())
    assert verify_receipt(payload, signer.public_key_pem()) is True


def test_verify_rejects_tampered_receipt():
    signer = get_signer()
    payload = signer.sign(_row())
    payload["receipt"]["decision"] = "deny"
    assert verify_receipt(payload, signer.public_key_pem()) is False


def test_verify_rejects_wrong_public_key():
    signer = get_signer()
    payload = signer.sign(_row())

    # Build a second signer with a fresh key
    reset_signer_for_tests()
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        os.environ["RECEIPT_SIGNING_KEY_PATH"] = os.path.join(td, "other.pem")
        other = get_signer()

    # The fingerprint in payload belongs to the first signer, so verify against
    # the *other* signer's PEM must fail — without raising.
    assert verify_receipt(payload, other.public_key_pem()) is False


def test_canonical_json_is_stable():
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b
    assert a == b'{"a":2,"b":1}'


def test_fingerprint_length_and_hex():
    signer = get_signer()
    fp = fingerprint_public_key(signer.public_key_pem().encode("ascii"))
    assert len(fp) == 32
    int(fp, 16)  # must be valid hex


def test_singleton_returns_same_instance():
    a = get_signer()
    b = get_signer()
    assert a is b


def test_verify_raises_on_missing_field():
    signer = get_signer()
    with pytest.raises(ValueError, match="missing field"):
        verify_receipt({"receipt": {}, "signature": "x"}, signer.public_key_pem())


def test_verify_raises_on_wrong_algorithm():
    signer = get_signer()
    payload = signer.sign(_row())
    payload["algorithm"] = "rsa"
    with pytest.raises(ValueError, match="unsupported algorithm"):
        verify_receipt(payload, signer.public_key_pem())
