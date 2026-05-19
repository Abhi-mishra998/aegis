"""End-to-end roundtrip: the audit signer signs, the SDK verifier verifies."""
import uuid
from datetime import UTC, datetime

import pytest

from sdk.acp_client import verify_receipt
from services.audit.signer import get_signer, reset_signer_for_tests


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
        "chain_shard": 0,
    }


def test_sdk_verifies_server_signed_receipt():
    """The most important test in this sprint."""
    signer = get_signer()
    payload = signer.sign(_row())
    pub_pem = signer.public_key_pem()
    assert verify_receipt(payload, pub_pem) is True


def test_sdk_rejects_tampered_payload():
    signer = get_signer()
    payload = signer.sign(_row())
    payload["receipt"]["decision"] = "deny"
    assert verify_receipt(payload, signer.public_key_pem()) is False


def test_sdk_rejects_unknown_algorithm():
    signer = get_signer()
    payload = signer.sign(_row())
    payload["algorithm"] = "rsa"
    with pytest.raises(ValueError, match="unsupported algorithm"):
        verify_receipt(payload, signer.public_key_pem())


def test_sdk_rejects_fingerprint_mismatch():
    signer = get_signer()
    payload = signer.sign(_row())
    payload["public_key_fingerprint"] = "0" * 32
    assert verify_receipt(payload, signer.public_key_pem()) is False
